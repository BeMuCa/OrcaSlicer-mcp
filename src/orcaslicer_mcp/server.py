"""MCP server exposing the official OrcaSlicer CLI."""

from __future__ import annotations

import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import orca

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
                overrides: dict[str, str] | None = None, output_dir: str = "") -> dict:
    """Slice an STL/3MF and return G-code path plus time/material estimates.
    overrides are process settings by OrcaSlicer key, e.g. {"sparse_infill_density": "40%",
    "wall_loops": "4"}. output_dir defaults to <model dir>/sliced/<model>-<timestamp>."""
    model = Path(model_path).expanduser()
    out = Path(output_dir).expanduser() if output_dir else (
        model.parent / "sliced" / f"{model.stem}-{time.strftime('%Y%m%d-%H%M%S')}")
    return orca.slice_model(model, vendor, machine, process, filament, out, overrides)


@mcp.tool()
def gcode_stats(gcode_path: str) -> dict:
    """Time, filament and layer estimates from an OrcaSlicer G-code file."""
    return orca.gcode_stats(Path(gcode_path).expanduser())


def main() -> None:
    mcp.run()
