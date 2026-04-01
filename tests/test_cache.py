"""Tests for cache validation and download cleanup."""

import os

from flash_tool import is_valid_cached_file, MIN_FIRMWARE_SIZE


class TestIsValidCachedFile:
    def test_nonexistent_file(self, tmp_path):
        assert is_valid_cached_file(str(tmp_path / "nope.bin")) is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert is_valid_cached_file(str(f)) is False

    def test_tiny_file_below_threshold(self, tmp_path):
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"x" * 100)
        assert is_valid_cached_file(str(f)) is False

    def test_file_at_threshold(self, tmp_path):
        f = tmp_path / "threshold.bin"
        f.write_bytes(b"x" * MIN_FIRMWARE_SIZE)
        assert is_valid_cached_file(str(f)) is True

    def test_large_file(self, tmp_path):
        f = tmp_path / "firmware.bin"
        f.write_bytes(b"x" * 50000)
        assert is_valid_cached_file(str(f)) is True

    def test_custom_min_size(self, tmp_path):
        f = tmp_path / "small.bin"
        f.write_bytes(b"x" * 10)
        assert is_valid_cached_file(str(f), min_size=5) is True
        assert is_valid_cached_file(str(f), min_size=100) is False

    def test_directory_is_not_valid(self, tmp_path):
        assert is_valid_cached_file(str(tmp_path)) is False

    def test_html_error_page_rejected(self, tmp_path):
        """A small HTML error page saved to disk should be rejected."""
        f = tmp_path / "error.bin"
        f.write_bytes(b"<html><body>404 Not Found</body></html>")
        assert is_valid_cached_file(str(f)) is False
