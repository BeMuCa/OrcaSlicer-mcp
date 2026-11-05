import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from orcaslicer_mcp import elegoo

TOKEN = "abc123"


class FakePrinter(BaseHTTPRequestHandler):
    received: dict = {}

    def _reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.headers.get("X-Token") != TOKEN:
            return self._reply(401, {})
        self._reply(200, {"error_code": 0, "system_info": {"sn": "SN1"}})

    def do_PUT(self):
        if self.headers.get("X-Token") != TOKEN:
            return self._reply(401, {})
        body = self.rfile.read(int(self.headers["Content-Length"]))
        r = FakePrinter.received.setdefault(self.headers["X-File-Name"], {"data": b"", "ranges": []})
        r["data"] += body
        r["ranges"].append(self.headers["Content-Range"])
        r["md5"] = self.headers["X-File-MD5"]
        self._reply(200, {"error_code": 0})

    def log_message(self, *a):
        pass


@pytest.fixture
def printer():
    FakePrinter.received = {}
    srv = HTTPServer(("127.0.0.1", 0), FakePrinter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_system_info(printer):
    assert elegoo.system_info(printer, TOKEN)["system_info"]["sn"] == "SN1"


def test_wrong_token_is_explained(printer):
    with pytest.raises(elegoo.PrinterError, match="401.*access code"):
        elegoo.system_info(printer, "wrong")


def test_upload_chunks_and_md5(printer, tmp_path, monkeypatch):
    monkeypatch.setattr(elegoo, "CHUNK", 10)
    f = tmp_path / "part.gcode"
    f.write_bytes(b"G1 X1\n" * 5)  # 30 bytes -> 3 chunks
    r = elegoo.upload(printer, TOKEN, f)
    got = FakePrinter.received["part.gcode"]
    assert got["data"] == f.read_bytes()
    assert got["ranges"] == ["bytes 0-9/30", "bytes 10-19/30", "bytes 20-29/30"]
    assert got["md5"] == r["md5"]
