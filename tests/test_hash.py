"""Tests for SHA-256 hashing, sidecar files, and download integrity."""

import hashlib
import os

from flash_tool import sha256_file, _write_hash_sidecar, is_valid_cached_file


def _sha256(data):
    """Helper: compute SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


class TestSha256File:
    def test_known_hash(self, tmp_path):
        f = tmp_path / "test.bin"
        content = b"hello world"
        f.write_bytes(content)
        assert sha256_file(str(f)) == _sha256(content)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert sha256_file(str(f)) == _sha256(b"")

    def test_large_file(self, tmp_path):
        f = tmp_path / "large.bin"
        content = b"x" * 200_000
        f.write_bytes(content)
        assert sha256_file(str(f)) == _sha256(content)


class TestWriteHashSidecar:
    def test_creates_sidecar(self, tmp_path):
        f = tmp_path / "fw.bin"
        content = b"firmware content"
        f.write_bytes(content)
        _write_hash_sidecar(str(f))

        sidecar = tmp_path / "fw.bin.sha256"
        assert sidecar.exists()
        assert sidecar.read_text().strip() == _sha256(content)

    def test_sidecar_is_single_line(self, tmp_path):
        f = tmp_path / "fw.bin"
        f.write_bytes(b"data")
        _write_hash_sidecar(str(f))
        lines = (tmp_path / "fw.bin.sha256").read_text().splitlines()
        assert len(lines) == 1


class TestCacheValidationWithHash:
    """Tests that is_valid_cached_file checks the sidecar hash."""

    def _make_cached(self, tmp_path, name, content):
        """Create a cached file with its sidecar hash."""
        f = tmp_path / name
        f.write_bytes(content)
        sidecar = tmp_path / (name + ".sha256")
        sidecar.write_text(_sha256(content) + "\n")
        return str(f)

    def test_valid_file_with_matching_hash(self, tmp_path):
        path = self._make_cached(tmp_path, "fw.bin", b"x" * 2000)
        assert is_valid_cached_file(path) is True

    def test_corrupted_file_rejected(self, tmp_path):
        path = self._make_cached(tmp_path, "fw.bin", b"x" * 2000)
        # Corrupt the file after writing the sidecar
        with open(path, "wb") as f:
            f.write(b"y" * 2000)
        assert is_valid_cached_file(path) is False

    def test_corrupted_file_gets_deleted(self, tmp_path):
        path = self._make_cached(tmp_path, "fw.bin", b"x" * 2000)
        with open(path, "wb") as f:
            f.write(b"y" * 2000)
        is_valid_cached_file(path)
        assert not os.path.exists(path)
        assert not os.path.exists(path + ".sha256")

    def test_missing_sidecar_still_valid(self, tmp_path):
        """Files without a sidecar (e.g. old cached files) are accepted."""
        f = tmp_path / "old.bin"
        f.write_bytes(b"x" * 2000)
        assert is_valid_cached_file(str(f)) is True

    def test_too_small_with_valid_hash_still_rejected(self, tmp_path):
        """Size check comes before hash check."""
        content = b"tiny"
        path = self._make_cached(tmp_path, "tiny.bin", content)
        assert is_valid_cached_file(path) is False
