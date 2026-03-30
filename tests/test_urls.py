"""Tests for URL building and channel label helpers."""

from flash_tool import build_urls, _channel_label, FIRMWARE_CDN, MSC_FW_PATH


# ── _channel_label ───────────────────────────────────


class TestChannelLabel:
    def test_stable(self):
        assert _channel_label("stable") == "Stable Channel"

    def test_staging(self):
        assert _channel_label("staging") == "Beta Channel (Staging)"

    def test_feature_branch(self):
        label = _channel_label("feature-test-midi-sync")
        assert label == "Beta Channel (Feature: midi-sync)"

    def test_unknown_channel_titlecased(self):
        assert _channel_label("nightly") == "Nightly"


# ── build_urls ───────────────────────────────────────


def _version(unified="stable/v1.0/p4.bin", pico="stable/v1.0/pico.uf2",
             sdcard="stable/v1.0/sd.zip", hash_file="stable/v1.0/sd.hash",
             tag="v1.0"):
    """Helper to build a version dict matching releases.json shape."""
    files = {}
    if unified:
        files["unified"] = unified
    if pico:
        files["pico"] = pico
    if sdcard:
        files["sdcard"] = sdcard
    if hash_file:
        files["hash"] = hash_file
    return {"tag": tag, "files": files}


class TestBuildUrls:
    def test_all_urls_present(self):
        urls = build_urls(_version())
        assert urls["tag"] == "v1.0"
        assert urls["p4_url"] == f"{FIRMWARE_CDN}/stable/v1.0/p4.bin"
        assert urls["pico_url"] == f"{FIRMWARE_CDN}/stable/v1.0/pico.uf2"
        assert urls["sd_url"] == f"{FIRMWARE_CDN}/stable/v1.0/sd.zip"
        assert urls["hash_url"] == f"{FIRMWARE_CDN}/stable/v1.0/sd.hash"
        assert urls["msc_url"] == f"{FIRMWARE_CDN}/{MSC_FW_PATH}"

    def test_missing_pico_is_none(self):
        urls = build_urls(_version(pico=None))
        assert urls["pico_url"] is None

    def test_missing_sdcard_is_none(self):
        urls = build_urls(_version(sdcard=None))
        assert urls["sd_url"] is None

    def test_missing_hash_is_none(self):
        urls = build_urls(_version(hash_file=None))
        assert urls["hash_url"] is None

    def test_msc_url_is_always_set(self):
        urls = build_urls(_version(unified=None, pico=None, sdcard=None, hash_file=None))
        assert urls["msc_url"] == f"{FIRMWARE_CDN}/{MSC_FW_PATH}"

    def test_tag_preserved(self):
        urls = build_urls(_version(tag="v10.3.5-rc1"))
        assert urls["tag"] == "v10.3.5-rc1"

    def test_empty_files_dict(self):
        v = {"tag": "v0.0.1", "files": {}}
        urls = build_urls(v)
        assert urls["p4_url"] is None
        assert urls["pico_url"] is None
        assert urls["sd_url"] is None
        assert urls["hash_url"] is None
