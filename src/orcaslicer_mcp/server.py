"""MCP server exposing the official OrcaSlicer CLI."""

from __future__ import annotations

import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import elegoo, orca

mcp = MCPServer("orcaslicer")


@mcp.tool()
def slicer_info() -> dict:
    """Where OrcaSlicer and its bundled profiles were found."""
    exe = orca.find_exe()
    root = orca.profiles_root(exe)
    return {"exe": str(exe), "profiles": str(root),
            "vendors": sorted(p.name for p in root.iterdir() if p.is_dir())}


@mcp.tool()
def list_profiles(vendor: str, kind: str, query: str = "") -> list[str]:
    """Selectable profile names of one vendor. kind: machine | process | filament.
    query filters by substring, e.g. vendor="Elegoo", kind="process", query="CC 0.4"."""
    return orca.list_profiles(orca.profiles_root(orca.find_exe()), vendor, kind, query)


@mcp.tool()
def slice_model(model_path: str, vendor: str, machine: str, process: str, filament: str,
                overrides: dict[str, str] | None = None, output_dir: str = "",
                filament_overrides: dict[str, str] | None = None,
                bed_type: str = "Textured PEI Plate") -> dict:
    """Slice an STL/3MF and return G-code path plus time/material estimates.
    overrides are process settings by OrcaSlicer key, e.g. {"sparse_infill_density": "40%",
    "wall_loops": "4", "enable_support": "1"}; filament_overrides likewise for the filament,
    e.g. {"nozzle_temperature": "215"}. Use get_profile to see every key and its current value.
    bed_type picks the plate temperatures: "Textured PEI Plate", "Hot Plate" (smooth PEI),
    "Cool Plate", "Engineering Plate". output_dir defaults to <model dir>/sliced/<model>-<timestamp>."""
    model = Path(model_path).expanduser()
    out = Path(output_dir).expanduser() if output_dir else (
        model.parent / "sliced" / f"{model.stem}-{time.strftime('%Y%m%d-%H%M%S')}")
    return orca.slice_model(model, vendor, machine, process, filament, out, overrides,
                            filament_overrides=filament_overrides, bed_type=bed_type)


@mcp.tool()
def get_profile(vendor: str, kind: str, name: str, keys: list[str] | None = None,
                query: str = "") -> dict:
    """All resolved settings of one profile (kind: machine | process | filament), inheritance
    applied. keys picks exact settings; query filters keys by substring (e.g. "speed",
    "support", "temperature"). Use it before overriding settings in slice_model."""
    p = orca.resolve_profile(orca.profiles_root(orca.find_exe()), vendor, kind, name)
    if keys:
        return {k: p.get(k, "<not in profile>") for k in keys}
    return {k: v for k, v in p.items() if query.lower() in k.lower()}


@mcp.tool()
def gcode_stats(gcode_path: str) -> dict:
    """Time, filament and layer estimates from an OrcaSlicer G-code file."""
    return orca.gcode_stats(Path(gcode_path).expanduser())


@mcp.tool()
def printer_info() -> dict:
    """Elegoo Centauri 2 / Carbon 2 system info (LAN-only mode). Needs ELEGOO_HOST and
    ELEGOO_TOKEN or ~/.config/elegoo_token."""
    return elegoo.system_info(*elegoo.config())


@mcp.tool()
def upload_to_printer(gcode_path: str) -> dict:
    """Upload a G-code file to the Elegoo printer; start it with start_print."""
    return elegoo.upload(*elegoo.config(), Path(gcode_path).expanduser())


@mcp.tool()
def printer_status() -> dict:
    """Live state of the Elegoo printer: idle/printing/..., progress, temperatures, file."""
    st = elegoo.status(*elegoo.config())
    st.pop("raw")
    return st


@mcp.tool()
def printer_files() -> list[dict]:
    """G-code files stored on the Elegoo printer."""
    return elegoo.list_files(*elegoo.config())


@mcp.tool()
def start_print(filename: str, bed_leveling: bool = False) -> dict:
    """Start printing a file already on the printer (see printer_files). Refuses unless the
    printer is idle. Only call after the user confirmed the bed is clear and filament loaded."""
    return elegoo.start_print(*elegoo.config(), filename, bed_leveling)


def main() -> None:
    mcp.run()
