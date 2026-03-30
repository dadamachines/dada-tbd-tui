"""Tests for security fixes: port validation, drive letter validation, download integrity."""

import hashlib
import http.server
import os
import threading

import pytest

from flash_tool import _is_valid_port_path, _safe_drive_letter, verify_download, sha256_file


# ── SEC-03: Port path validation ─────────────────────


class TestPortPathValidation:
    """Ensure _is_valid_port_path rejects dangerous inputs."""

    # Valid paths
    def test_unix_usbmodem(self):
        assert _is_valid_port_path("/dev/cu.usbmodem1101") is True

    def test_unix_ttyUSB(self):
        assert _is_valid_port_path("/dev/ttyUSB0") is True

    def test_unix_ttyACM(self):
        assert _is_valid_port_path("/dev/ttyACM0") is True

    def test_windows_com(self):
        assert _is_valid_port_path("COM3") is True

    def test_windows_com_lowercase(self):
        assert _is_valid_port_path("com3") is True

    # Invalid / malicious paths
    def test_path_traversal(self):
        assert _is_valid_port_path("/dev/../../etc/passwd") is False

    def test_absolute_path_not_dev(self):
        assert _is_valid_port_path("/etc/passwd") is False

    def test_home_dir(self):
        assert _is_valid_port_path("/home/user/fake") is False

    def test_ansi_escape_injection(self):
        assert _is_valid_port_path("/dev/\033[31mhacked") is False

    def test_space_injection(self):
        assert _is_valid_port_path("/dev/tty; rm -rf /") is False

    def test_empty_string(self):
        assert _is_valid_port_path("") is False

    def test_slash_dev_only(self):
        assert _is_valid_port_path("/dev/") is False

    def test_windows_fake_com(self):
        assert _is_valid_port_path("COM") is False

    def test_windows_path_traversal(self):
        assert _is_valid_port_path("COM3; calc.exe") is False

    def test_nested_dev_path(self):
        assert _is_valid_port_path("/dev/sub/dir") is False


# ── SEC-04/05: Drive letter validation (PowerShell injection) ──


class TestSafeDriveLetter:
    """Ensure _safe_drive_letter rejects dangerous inputs."""

    def test_valid_drive(self):
        assert _safe_drive_letter("D:\\") == "D"

    def test_valid_drive_lowercase(self):
        assert _safe_drive_letter("e:\\data") == "E"

    def test_c_drive(self):
        assert _safe_drive_letter("C:\\Users") == "C"

    def test_empty_string(self):
        assert _safe_drive_letter("") is None

    def test_unix_path(self):
        assert _safe_drive_letter("/Volumes/NO NAME") is None

    def test_powershell_injection_single_quote(self):
        assert _safe_drive_letter("'; Remove-Item -Recurse C:\\Users; ':") is None

    def test_powershell_injection_semicolon(self):
        assert _safe_drive_letter("';calc;':") is None

    def test_no_colon(self):
        assert _safe_drive_letter("D") is None

    def test_number_not_letter(self):
        assert _safe_drive_letter("1:\\") is None

    def test_too_long_drive(self):
        assert _safe_drive_letter("DD:\\") is None


# ── SEC-02: Server-side hash verification ────────────


@pytest.fixture(scope="module")
def hash_server():
    """HTTP server that serves files and their .sha256 companions."""

    FIRMWARE = b"\xDE\xAD\xBE\xEF" * 1024  # 4 KB
    FIRMWARE_HASH = hashlib.sha256(FIRMWARE).hexdigest()
    BAD_HASH = "0" * 64

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/firmware.bin":
                self.send_response(200)
                self.send_header("Content-Length", str(len(FIRMWARE)))
                self.end_headers()
                self.wfile.write(FIRMWARE)
            elif self.path == "/firmware.bin.sha256":
                body = (FIRMWARE_HASH + "\n").encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/tampered.bin":
                # Serve different content than what the hash says
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"\x00" * 4096)
            elif self.path == "/tampered.bin.sha256":
                body = (FIRMWARE_HASH + "\n").encode()  # hash of FIRMWARE, not the zeros
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/nohash.bin":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(FIRMWARE)
            elif self.path == "/nohash.bin.sha256":
                self.send_error(404)
            elif self.path == "/hashwithname.bin.sha256":
                # Format: "hash  filename"
                body = (FIRMWARE_HASH + "  hashwithname.bin\n").encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/hashwithname.bin":
                self.send_response(200)
                self.send_header("Content-Length", str(len(FIRMWARE)))
                self.end_headers()
                self.wfile.write(FIRMWARE)
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestVerifyDownload:
    def test_valid_file_passes(self, hash_server, tmp_path):
        from flash_tool import download_file
        dest = str(tmp_path / "firmware.bin")
        download_file(f"{hash_server}/firmware.bin", dest, verify=False)
        assert verify_download(dest, f"{hash_server}/firmware.bin") is True

    def test_tampered_file_fails(self, hash_server, tmp_path):
        from flash_tool import download_file
        dest = str(tmp_path / "tampered.bin")
        download_file(f"{hash_server}/tampered.bin", dest, verify=False)
        result = verify_download(dest, f"{hash_server}/tampered.bin")
        assert result is False
        # File should be deleted
        assert not os.path.exists(dest)

    def test_no_server_hash_passes(self, hash_server, tmp_path):
        from flash_tool import download_file
        dest = str(tmp_path / "nohash.bin")
        download_file(f"{hash_server}/nohash.bin", dest, verify=False)
        assert verify_download(dest, f"{hash_server}/nohash.bin") is True

    def test_hash_with_filename_format(self, hash_server, tmp_path):
        """Server hash file in 'hash  filename' format should work."""
        from flash_tool import download_file
        dest = str(tmp_path / "hashwithname.bin")
        download_file(f"{hash_server}/hashwithname.bin", dest, verify=False)
        assert verify_download(dest, f"{hash_server}/hashwithname.bin") is True

    def test_download_with_verify_flag(self, hash_server, tmp_path):
        """download_file(verify=True) should call verify_download."""
        from flash_tool import download_file
        dest = str(tmp_path / "verified.bin")
        result = download_file(f"{hash_server}/firmware.bin", dest, verify=True)
        assert result is True

    def test_download_with_verify_rejects_tampered(self, hash_server, tmp_path):
        from flash_tool import download_file
        dest = str(tmp_path / "bad.bin")
        result = download_file(f"{hash_server}/tampered.bin", dest, verify=True)
        assert result is False
