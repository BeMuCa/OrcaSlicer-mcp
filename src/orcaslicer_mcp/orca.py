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
    Unknown keys are refused so a typo cannot silently do nothing."""
    unknown = [k for k in (overrides or {}) if k not in profile]
    if unknown:
        raise OrcaError(f"unknown {kind} setting(s) {unknown}; read the profile to see valid keys")
    for k, v in (overrides or {}).items():
        profile[k] = [str(v)] * max(1, len(profile[k])) if isinstance(profile[k], list) else str(v)


def slice_model(model: Path, vendor: str, machine: str, process: str, filament: str,
                outdir: Path, overrides: dict | None = None, timeout: int = 600,
                filament_overrides: dict | None = None) -> dict:
    exe = find_exe()
    root = profiles_root(exe)
    if not model.is_file():
        raise OrcaError(f"model not found: {model}")
    outdir.mkdir(parents=True, exist_ok=True)
    proc = resolve_profile(root, vendor, "process", process)
    apply_overrides(proc, overrides, "process")
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
    return {"gcode": str(gcodes[0]), **gcode_stats(gcodes[0])}
