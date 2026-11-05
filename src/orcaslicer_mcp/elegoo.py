"""LAN access to Elegoo Centauri 2 / Centauri Carbon 2 printers (LAN-only mode).

Protocol as implemented by OrcaSlicer 2.4.2 (src/slic3r/Utils/ElegooLink.cpp):
GET /system/info?X-Token=<code> and chunked PUT /upload, both with an X-Token
header carrying the printer's access code. Responses are JSON with error_code 0
on success. OrcaSlicer itself cannot start a print on these printers.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

CHUNK = 1024 * 1024


class PrinterError(ToolError):  # ToolError: the MCP client sees the message
    pass


def config() -> tuple[str, str]:
    host = os.environ.get("ELEGOO_HOST", "")
    token = os.environ.get("ELEGOO_TOKEN", "")
    token_file = Path(os.environ.get("ELEGOO_TOKEN_FILE", "~/.config/elegoo_token")).expanduser()
    if not token and token_file.is_file():
        token = token_file.read_text().strip()
    if not host:
        raise PrinterError("set ELEGOO_HOST to the printer's IP")
    if not token:
        raise PrinterError(f"set ELEGOO_TOKEN or put the access code in {token_file}")
    return host, token


def _request(req: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PrinterError(f"HTTP {e.code}: invalid access code or printer not in LAN-only mode")
        raise PrinterError(f"HTTP {e.code}: {e.read()[:300]!r}")
    except urllib.error.URLError as e:
        raise PrinterError(f"printer unreachable: {e.reason}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise PrinterError(f"unexpected response: {body[:300]}")
    if data.get("error_code", -1) != 0:
        raise PrinterError(f"printer error {data.get('error_code')}: {data.get('message', body[:300])}")
    return data


def system_info(host: str, token: str, timeout: int = 10) -> dict:
    url = f"http://{host}/system/info?X-Token={urllib.parse.quote(token)}"
    req = urllib.request.Request(url, headers={"X-Token": token, "Accept": "application/json"})
    return _request(req, timeout)


def upload(host: str, token: str, path: Path, name: str = "", timeout: int = 180) -> dict:
    data = path.read_bytes()
    if not data:
        raise PrinterError(f"{path} is empty")
    name = name or path.name
    md5 = hashlib.md5(data).hexdigest()
    for offset in range(0, len(data), CHUNK):
        chunk = data[offset:offset + CHUNK]
        req = urllib.request.Request(f"http://{host}/upload", data=chunk, method="PUT", headers={
            "Accept": "application/json",
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{len(data)}",
            "X-File-Name": name,
            "X-File-MD5": md5,
            "X-Token": token,
        })
        _request(req, timeout)
    return {"uploaded": name, "bytes": len(data), "md5": md5}
