"""Tests for download_file — uses a local HTTP server, no real network."""

import http.server
import os
import tempfile
import threading

import pytest

from flash_tool import download_file


@pytest.fixture(scope="module")
def http_server():
    """Spin up a throwaway HTTP server serving known content."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok.bin":
                body = b"\xDE\xAD" * 512  # 1 KB
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/no-length.bin":
                body = b"\xCA\xFE" * 64
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/empty.bin":
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/404":
                self.send_error(404, "Not Found")
            elif self.path == "/500":
                self.send_error(500, "Internal Server Error")
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # silence server logs during tests

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestDownloadFile:
    def test_downloads_file_correctly(self, http_server, tmp_path):
        dest = str(tmp_path / "ok.bin")
        result = download_file(f"{http_server}/ok.bin", dest)
        assert result is True
        assert os.path.getsize(dest) == 1024
        with open(dest, "rb") as f:
            assert f.read(2) == b"\xDE\xAD"

    def test_creates_parent_directories(self, http_server, tmp_path):
        dest = str(tmp_path / "sub" / "dir" / "firmware.bin")
        result = download_file(f"{http_server}/ok.bin", dest)
        assert result is True
        assert os.path.isfile(dest)

    def test_works_without_content_length(self, http_server, tmp_path):
        dest = str(tmp_path / "no-length.bin")
        result = download_file(f"{http_server}/no-length.bin", dest)
        assert result is True
        assert os.path.getsize(dest) == 128

    def test_handles_empty_response(self, http_server, tmp_path):
        dest = str(tmp_path / "empty.bin")
        result = download_file(f"{http_server}/empty.bin", dest)
        assert result is True
        assert os.path.getsize(dest) == 0

    def test_http_404_returns_false(self, http_server, tmp_path):
        dest = str(tmp_path / "missing.bin")
        result = download_file(f"{http_server}/404", dest)
        assert result is False

    def test_http_500_returns_false(self, http_server, tmp_path):
        dest = str(tmp_path / "error.bin")
        result = download_file(f"{http_server}/500", dest)
        assert result is False

    def test_bad_host_returns_false(self, tmp_path):
        dest = str(tmp_path / "unreachable.bin")
        result = download_file("http://192.0.2.1:1/nope", dest, timeout=1)
        assert result is False

    def test_label_does_not_affect_result(self, http_server, tmp_path):
        dest = str(tmp_path / "labeled.bin")
        result = download_file(f"{http_server}/ok.bin", dest, label="test fw")
        assert result is True
