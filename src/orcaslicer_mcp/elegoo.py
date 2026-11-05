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


# --- MQTT control (port 1883, user "elegoo", password = access code) ---------------------
# Verified on a Centauri 2, firmware 01.03.02.36: register, 1002 status, 1044 file list.
# Method numbers and the 1020 start-print params come from Elegoo's LAN SDK as documented in
# github.com/ChestnutLabs/printer-protocol-orchard (protocols/elegoo.md).

STATUS_NAMES = {0: "initializing", 1: "idle", 2: "printing", 3: "loading filament",
                4: "unloading filament", 5: "auto leveling", 6: "PID calibrating",
                7: "resonance testing", 8: "self checking", 9: "updating", 10: "homing",
                11: "file transferring", 12: "creating timelapse", 13: "extruder operating",
                14: "emergency stop", 15: "power loss recovery"}


def mqtt_call(host: str, token: str, method: int, params: dict, timeout: float = 10) -> dict:
    import secrets
    import threading

    import paho.mqtt.client as mqtt

    sn = system_info(host, token)["system_info"]["sn"]
    cid, rid = secrets.token_hex(5), secrets.token_hex(13)
    registered, answered = threading.Event(), threading.Event()
    box: dict = {}

    def on_message(client, userdata, msg):
        data = json.loads(msg.payload)
        if msg.topic.endswith("/register_response"):
            box["register"] = data
            registered.set()
        elif msg.topic.endswith("/api_response") and data.get("id") == 1:
            box["response"] = data
            answered.set()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, protocol=mqtt.MQTTv311)
    c.username_pw_set("elegoo", token)
    c.on_message = on_message
    try:
        c.connect(host, 1883, 60)
    except OSError as e:
        raise PrinterError(f"MQTT connect to {host}:1883 failed: {e}")
    c.loop_start()
    try:
        c.subscribe(f"elegoo/{sn}/{rid}/register_response", 1)
        c.subscribe(f"elegoo/{sn}/{cid}/api_response", 1)
        c.publish(f"elegoo/{sn}/api_register", json.dumps({"client_id": cid, "request_id": rid}), 1)
        if not registered.wait(timeout) or box["register"].get("error") != "ok":
            raise PrinterError(f"printer refused MQTT registration: {box.get('register')} "
                               "(max 2 clients at once - close the Elegoo app or OrcaSlicer device tab?)")
        c.publish(f"elegoo/{sn}/{cid}/api_request",
                  json.dumps({"id": 1, "method": method, "params": params}), 1)
        if not answered.wait(timeout):
            raise PrinterError(f"no answer to method {method} within {timeout}s")
    finally:
        c.loop_stop()
        c.disconnect()
    result = box["response"].get("result", {})
    if result.get("error_code", 0) != 0:
        raise PrinterError(f"printer error {result.get('error_code')} for method {method}: {result}")
    return result


def status(host: str, token: str) -> dict:
    r = mqtt_call(host, token, 1002, {})
    ms, ps = r.get("machine_status", {}), r.get("print_status", {})
    return {"state": STATUS_NAMES.get(ms.get("status"), ms.get("status")),
            "progress": ms.get("progress"), "file": ps.get("filename"),
            "layer": ps.get("current_layer"), "remaining_s": ps.get("remaining_time_sec"),
            "nozzle_c": r.get("extruder", {}).get("temperature"),
            "bed_c": r.get("heater_bed", {}).get("temperature"),
            "filament_detected": bool(r.get("extruder", {}).get("filament_detected")),
            "raw": r}


def list_files(host: str, token: str) -> list[dict]:
    r = mqtt_call(host, token, 1044, {"storage_media": "local", "dir": "", "offset": 0, "limit": 200})
    return [{"filename": f["filename"], "size": f.get("size"), "print_time_s": f.get("print_time")}
            for f in r.get("file_list", [])]


def start_print(host: str, token: str, filename: str, bed_leveling: bool = False) -> dict:
    st = status(host, token)
    if st["state"] != "idle":
        raise PrinterError(f"printer is {st['state']}, not idle - refusing to start")
    if filename not in {f["filename"] for f in list_files(host, token)}:
        raise PrinterError(f"{filename} is not on the printer; upload it first")
    return mqtt_call(host, token, 1020, {
        "storage_media": "local", "filename": filename,
        "config": {"delay_video": False, "printer_check": True, "print_layout": "A",
                   "bedlevel_force": bed_leveling, "slot_map": []}})
