"""Tests for SD card safety checks (_is_safe_to_erase)."""

import os
import platform

import pytest

from flash_tool import _is_safe_to_erase, PROTECTED_PATHS


# ── protected paths ──────────────────────────────────


class TestProtectedPaths:
    """Layer 1: hard-coded protected paths must always be refused."""

    @pytest.mark.parametrize("path", [
        "/",
        "/System",
        "/Users",
        "/Applications",
        "/Library",
        "/home",
        "/root",
        "/usr",
        "/var",
        "/etc",
        "/opt",
        "/bin",
        "/sbin",
    ])
    def test_unix_protected_paths_refused(self, path):
        safe, reason = _is_safe_to_erase(path)
        assert not safe, f"{path} should be refused, got: {reason}"

    @pytest.mark.parametrize("path", [
        "C:\\",
        "C:\\Windows",
        "C:\\Users",
        "C:\\Program Files",
    ])
    def test_windows_protected_paths_refused(self, path):
        safe, reason = _is_safe_to_erase(path)
        assert not safe


class TestRootAndSystemPaths:
    """Layer 2: root and direct children of root must be refused."""

    def test_root_refused(self):
        safe, _ = _is_safe_to_erase("/")
        assert not safe

    def test_slash_tmp_refused(self):
        """Direct children of / (like /tmp) are refused by layer 2."""
        safe, _ = _is_safe_to_erase("/tmp")
        assert not safe


class TestMacOSVolumeChecks:
    """Layer 3: macOS-specific volume path checks."""

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_non_volumes_path_refused(self):
        safe, reason = _is_safe_to_erase("/Users/someone/Desktop")
        assert not safe
        assert "Not a mounted volume" in reason

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_macintosh_hd_refused(self):
        # On macOS, /Volumes/Macintosh HD is a symlink to /
        # so it may be caught by the root or protected-path layer instead
        safe, reason = _is_safe_to_erase("/Volumes/Macintosh HD")
        assert not safe, f"Macintosh HD should be refused, got: {reason}"

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_macintosh_hd_data_refused(self):
        safe, reason = _is_safe_to_erase("/Volumes/Macintosh HD - Data")
        assert not safe
        assert "System volume" in reason


class TestNonExistentPaths:
    """Layer 5: path must be a real directory."""

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS volume check")
    def test_nonexistent_volume_refused(self):
        safe, reason = _is_safe_to_erase("/Volumes/DOES_NOT_EXIST_12345")
        assert not safe

    def test_file_instead_of_dir_refused(self):
        """A regular file should not pass as an erasable volume."""
        safe, _ = _is_safe_to_erase(__file__)
        assert not safe


class TestProtectedPathsCompleteness:
    """Verify the PROTECTED_PATHS constant covers critical system locations."""

    def test_root_in_protected(self):
        assert "/" in PROTECTED_PATHS

    def test_contains_unix_essentials(self):
        for p in ("/usr", "/bin", "/sbin", "/etc", "/var"):
            assert p in PROTECTED_PATHS, f"{p} missing from PROTECTED_PATHS"

    def test_contains_windows_essentials(self):
        for p in ("C:\\", "C:\\Windows", "C:\\Users"):
            assert p in PROTECTED_PATHS, f"{p} missing from PROTECTED_PATHS"
