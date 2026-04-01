"""Tests for partition table parsing and OTA data construction."""

import struct
import zlib

from flash_tool import (
    build_ota_data,
    parse_partition_table,
    PT_MAGIC,
    PT_MD5_MAGIC,
    PT_ENTRY_SIZE,
    OTA_DATA_SIZE,
)


# ── helpers ──────────────────────────────────────────


def _make_entry(type_id, subtype, offset, size, name):
    """Build one 32-byte ESP-IDF partition table entry."""
    magic = PT_MAGIC  # 0xAA50
    name_bytes = name.encode("ascii")[:16].ljust(16, b"\x00")
    flags = struct.pack("<I", 0)
    return (
        magic
        + bytes([type_id, subtype])
        + struct.pack("<II", offset, size)
        + name_bytes
        + flags
    )


def _md5_marker():
    """Build a 32-byte MD5 end-of-table marker."""
    return PT_MD5_MAGIC + b"\xff" * (PT_ENTRY_SIZE - 2)


# ── parse_partition_table ────────────────────────────


class TestParsePartitionTable:
    def test_single_entry(self):
        data = _make_entry(0, 0x10, 0x10000, 0x500000, "ota_0")
        entries = parse_partition_table(data)
        assert len(entries) == 1
        e = entries[0]
        assert e["type"] == 0
        assert e["subtype"] == 0x10
        assert e["offset"] == 0x10000
        assert e["size"] == 0x500000
        assert e["name"] == "ota_0"

    def test_two_ota_partitions(self):
        data = (
            _make_entry(0, 0x10, 0x10000, 0x500000, "ota_0")
            + _make_entry(0, 0x11, 0x510000, 0x100000, "ota_1")
        )
        entries = parse_partition_table(data)
        assert len(entries) == 2
        assert entries[0]["name"] == "ota_0"
        assert entries[1]["name"] == "ota_1"
        assert entries[1]["offset"] == 0x510000
        assert entries[1]["size"] == 0x100000

    def test_md5_marker_stops_parsing(self):
        data = (
            _make_entry(0, 0x10, 0x10000, 0x500000, "ota_0")
            + _md5_marker()
            + _make_entry(0, 0x11, 0x510000, 0x100000, "ota_1")
        )
        entries = parse_partition_table(data)
        assert len(entries) == 1

    def test_bad_magic_stops_parsing(self):
        good = _make_entry(0, 0x10, 0x10000, 0x500000, "ota_0")
        bad = b"\x00\x00" + b"\xff" * 30
        data = good + bad + _make_entry(0, 0x11, 0x510000, 0x100000, "ota_1")
        entries = parse_partition_table(data)
        assert len(entries) == 1

    def test_empty_data(self):
        assert parse_partition_table(b"") == []

    def test_truncated_entry(self):
        data = _make_entry(0, 0x10, 0x10000, 0x500000, "ota_0")[:20]
        assert parse_partition_table(data) == []

    def test_data_partition_type(self):
        data = _make_entry(1, 0x00, 0xD000, 0x2000, "otadata")
        entries = parse_partition_table(data)
        assert entries[0]["type"] == 1
        assert entries[0]["subtype"] == 0x00

    def test_mixed_app_and_data_partitions(self):
        data = (
            _make_entry(1, 0x00, 0xD000, 0x2000, "otadata")
            + _make_entry(0, 0x10, 0x10000, 0x500000, "ota_0")
            + _make_entry(0, 0x11, 0x510000, 0x100000, "ota_1")
            + _make_entry(1, 0x82, 0x610000, 0x1F0000, "spiffs")
            + _md5_marker()
        )
        entries = parse_partition_table(data)
        assert len(entries) == 4
        names = [e["name"] for e in entries]
        assert names == ["otadata", "ota_0", "ota_1", "spiffs"]


# ── build_ota_data ───────────────────────────────────


class TestBuildOtaData:
    def test_none_returns_erased_state(self):
        data = build_ota_data(None)
        assert len(data) == OTA_DATA_SIZE
        assert data == b"\xff" * OTA_DATA_SIZE

    def test_slot0_seq_is_1(self):
        data = build_ota_data(0)
        seq = struct.unpack_from("<I", data, 0)[0]
        assert seq == 1

    def test_slot1_seq_is_2(self):
        data = build_ota_data(1)
        seq = struct.unpack_from("<I", data, 0)[0]
        assert seq == 2

    def test_crc_matches_esp_idf(self):
        """CRC must be crc32 over the 4-byte ota_seq, seeded with 0xFFFFFFFF."""
        for slot in (0, 1):
            data = build_ota_data(slot)
            seq_bytes = data[0:4]
            stored_crc = struct.unpack_from("<I", data, 28)[0]
            expected_crc = zlib.crc32(seq_bytes, 0xFFFFFFFF) & 0xFFFFFFFF
            assert stored_crc == expected_crc

    def test_second_sector_is_erased(self):
        data = build_ota_data(1)
        second_sector = data[0x1000:]
        assert second_sector == b"\xff" * 0x1000

    def test_output_size_is_always_8kb(self):
        for slot in (None, 0, 1):
            assert len(build_ota_data(slot)) == OTA_DATA_SIZE

    def test_seq_label_region_is_0xff(self):
        """Bytes 4..23 (seq_label) must stay 0xFF."""
        data = build_ota_data(1)
        assert data[4:24] == b"\xff" * 20

    def test_ota_state_is_0xff(self):
        """Bytes 24..27 (ota_state) must stay 0xFF (ESP_OTA_IMG_NEW)."""
        data = build_ota_data(0)
        assert data[24:28] == b"\xff" * 4
