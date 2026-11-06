"""Locate the official OrcaSlicer, resolve its bundled profiles, run its CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

KINDS = ("machine", "process", "filament")

_CANDIDATES = [
    "/mnt/c/Program Files/OrcaSlicer/orca-slicer.exe",  # Windows install seen from WSL
    "C:/Program Files/OrcaSlicer/orca-slicer.exe",
    "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
    "/usr/bin/orca-slicer",
]


class OrcaError(ToolError):  # ToolError: the MCP client sees the message
    pass


def find_exe() -> Path:
    override = os.environ.get("ORCASLICER_PATH")
    if override:
        p = Path(override)
        if not p.is_file():
            raise OrcaError(f"ORCASLICER_PATH={override} does not exist")
        return p
    for name in ("orca-slicer", "orca-slicer.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for c in _CANDIDATES:
        if Path(c).is_file():
            return Path(c)
    raise OrcaError("OrcaSlicer not found; set ORCASLICER_PATH to orca-slicer(.exe)")


def profiles_root(exe: Path) -> Path:
    for root in (exe.parent / "resources" / "profiles",
                 exe.parent.parent / "Resources" / "profiles",  # macOS bundle
                 Path("/usr/share/OrcaSlicer/profiles")):
        if root.is_dir():
            return root
    raise OrcaError(f"no profiles directory next to {exe}")


def native_path(p: Path, exe: Path) -> str:
    """A Windows .exe launched from WSL needs Windows paths."""
    if exe.suffix.lower() == ".exe" and os.name != "nt":
        return subprocess.run(["wslpath", "-w", str(p.resolve())], capture_output=True,
                              text=True, check=True).stdout.strip()
    return str(p.resolve())


def _index(root: Path, vendor: str, kind: str) -> dict[str, Path]:
    """name -> file for one vendor; OrcaFilamentLibrary fills gaps for inherited bases."""
    idx: dict[str, Path] = {}
    for v in ("OrcaFilamentLibrary", vendor):
        d = root / v / kind
        if not d.is_dir():
            continue
        for f in d.rglob("*.json"):
            try:
                name = json.loads(f.read_text(encoding="utf-8")).get("name")
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if name:
                idx[name] = f
    return idx


def list_profiles(root: Path, vendor: str, kind: str, query: str = "") -> list[str]:
    if kind not in KINDS:
        raise OrcaError(f"kind must be one of {KINDS}")
    out = []
    for f in sorted((root / vendor / kind).rglob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("instantiation") == "true" and query.lower() in data.get("name", "").lower():
            out.append(data["name"])
    return out


def resolve_profile(root: Path, vendor: str, kind: str, name: str) -> dict:
    """Flatten the `inherits` chain into one dict the CLI can load on its own."""
    idx = _index(root, vendor, kind)
    chain, seen = [], set()
    while name:
        if name in seen:
            raise OrcaError(f"inheritance loop at {name}")
        if name not in idx:
            raise OrcaError(f"{kind} profile '{name}' not found for vendor {vendor}")
        seen.add(name)
        data = json.loads(idx[name].read_text(encoding="utf-8"))
        chain.append(data)
        name = data.get("inherits", "")
    merged: dict = {}
    for data in reversed(chain):
        merged.update(data)
    merged.pop("inherits", None)
    merged["name"] = chain[0]["name"]
    merged["from"] = "User"
    return merged


_STATS = {
    "estimated_time": r"; estimated printing time \(normal mode\) = (.+)",
    "filament_mm": r"; filament used \[mm\] = ([\d.]+)",
    "filament_cm3": r"; filament used \[cm3\] = ([\d.]+)",
    "filament_g": r"; total filament used \[g\] = ([\d.]+)",
    "layers": r"; total layer number: (\d+)",
    "max_z_mm": r"; max_z_height: ([\d.]+)",
    "printer_model": r";\s*printer_model\s*[:=]\s*(.+)",
    "filament_type": r"; filament_type = (.+)",
    "layer_height": r"; layer_height = ([\d.]+)",
}


def gcode_stats(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    stats = {}
    for key, pat in _STATS.items():
        m = re.search(pat, text)
        if m:
            stats[key] = m.group(1).strip()
    return stats


def apply_overrides(profile: dict, overrides: dict | None, kind: str) -> None:
    """Set values in the profile's own shape: filament values are lists, process values strings.
    Keys the vendor profile leaves out (e.g. fuzzy_skin) are set too; slice_model checks
    afterwards that the G-code carries every override, so a typo cannot silently do nothing."""
    for k, v in (overrides or {}).items():
        cur = profile.get(k, [] if kind == "filament" else "")
        profile[k] = [str(v)] * max(1, len(cur)) if isinstance(cur, list) else str(v)


def _check_applied(gcode: Path, overrides: dict) -> None:
    text = gcode.read_text(encoding="utf-8", errors="replace")
    missing = [k for k, v in overrides.items()
               if not re.search(rf"^; {re.escape(k)} = {re.escape(str(v))}(,|$)", text, re.M)]
    if missing:
        raise OrcaError(f"setting(s) {missing} not applied - unknown key or invalid value; "
                        "use get_profile to see valid keys")


def slice_model(model: Path, vendor: str, machine: str, process: str, filament: str,
                outdir: Path, overrides: dict | None = None, timeout: int = 600,
                filament_overrides: dict | None = None,
                bed_type: str = "Textured PEI Plate") -> dict:
    exe = find_exe()
    root = profiles_root(exe)
    if not model.is_file():
        raise OrcaError(f"model not found: {model}")
    outdir.mkdir(parents=True, exist_ok=True)
    proc = resolve_profile(root, vendor, "process", process)
    apply_overrides(proc, overrides, "process")
    # Without this the CLI assumes "Cool Plate" and heats PLA beds to 35 C only (spaghetti on PEI)
    proc["curr_bed_type"] = bed_type
    fil = resolve_profile(root, vendor, "filament", filament)
    apply_overrides(fil, filament_overrides, "filament")
    # The CLI resolves inheritance for system machine files itself and refuses a
    # flattened machine (exit 239); for filaments it does not resolve inheritance
    # (slices as PLA, 0 g), so those are flattened. Process is flattened to carry overrides.
    machine_file = _index(root, vendor, "machine").get(machine)
    if machine_file is None:
        raise OrcaError(f"machine profile '{machine}' not found for vendor {vendor}")
    files = {"machine": machine_file}
    for kind, data in (("process", proc),
                       ("filament", fil)):
        files[kind] = outdir / f"{kind}.json"
        files[kind].write_text(json.dumps(data, indent=1), encoding="utf-8")
    for old in outdir.glob("*.gcode"):
        old.unlink()
    n = lambda p: native_path(p, exe)  # noqa: E731
    argv = [str(exe),
            "--load-settings", f"{n(files['machine'])};{n(files['process'])}",
            "--load-filaments", n(files["filament"]),
            "--slice", "1", "--outputdir", n(outdir), n(model)]
    # cwd=outdir: on failure the CLI writes its reason to a numbered .log in the cwd
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=outdir)
    gcodes = sorted(outdir.glob("*.gcode"))
    if res.returncode != 0 or not gcodes:
        logs = " ".join(p.read_text(errors="replace").strip() for p in outdir.glob("*.log"))
        raise OrcaError(f"slice failed (exit {res.returncode}): {logs or (res.stderr or res.stdout)[-1500:]}")
    _check_applied(gcodes[0], {**(overrides or {}), **(filament_overrides or {})})
    return {"gcode": str(gcodes[0]), **gcode_stats(gcodes[0])}
