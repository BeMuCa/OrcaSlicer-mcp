import json
import os
import struct
from pathlib import Path

import pytest

from orcaslicer_mcp import orca


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_resolve_profile_merges_inheritance_child_wins(tmp_path):
    _write(tmp_path / "OrcaFilamentLibrary/filament/base.json",
           {"name": "base", "filament_type": ["PLA"], "filament_density": ["1.24"]})
    _write(tmp_path / "V/filament/mid.json",
           {"name": "mid", "inherits": "base", "filament_type": ["PETG"]})
    _write(tmp_path / "V/filament/leaf.json",
           {"name": "leaf", "inherits": "mid", "instantiation": "true"})
    p = orca.resolve_profile(tmp_path, "V", "filament", "leaf")
    assert p["name"] == "leaf"
    assert p["filament_type"] == ["PETG"]
    assert p["filament_density"] == ["1.24"]
    assert "inherits" not in p


def test_resolve_profile_missing_parent_raises(tmp_path):
    _write(tmp_path / "V/process/a.json", {"name": "a", "inherits": "nope"})
    with pytest.raises(orca.OrcaError, match="nope"):
        orca.resolve_profile(tmp_path, "V", "process", "a")


def test_list_profiles_only_selectable(tmp_path):
    _write(tmp_path / "V/process/a.json", {"name": "0.20mm A", "instantiation": "true"})
    _write(tmp_path / "V/process/b.json", {"name": "common", "instantiation": "false"})
    assert orca.list_profiles(tmp_path, "V", "process") == ["0.20mm A"]


def test_gcode_stats(tmp_path):
    g = tmp_path / "x.gcode"
    g.write_text("; total layer number: 100\n; max_z_height: 20.00\n"
                 "; filament used [mm] = 1800.70\n; total filament used [g] = 5.41\n"
                 "; estimated printing time (normal mode) = 25m 3s\n; filament_type = PETG\n")
    s = orca.gcode_stats(g)
    assert s == {"layers": "100", "max_z_mm": "20.00", "filament_mm": "1800.70",
                 "filament_g": "5.41", "estimated_time": "25m 3s", "filament_type": "PETG"}


def _cube(path: Path, a: float = 20) -> None:
    v = [(x, y, z) for x in (0, a) for y in (0, a) for z in (0, a)]
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
         (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    with open(path, "wb") as o:
        o.write(b"\0" * 80 + struct.pack("<I", len(f)))
        for t in f:
            o.write(struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v[i]) for i in t) + b"\0\0")


@pytest.mark.skipif(not os.environ.get("ORCASLICER_PATH"), reason="needs a real OrcaSlicer")
def test_slice_real_cube_elegoo_centauri_carbon(tmp_path):
    _cube(tmp_path / "cube.stl")
    r = orca.slice_model(tmp_path / "cube.stl", "Elegoo", "Elegoo Centauri Carbon 0.4 nozzle",
                         "0.20mm Standard @Elegoo CC 0.4 nozzle", "Elegoo PETG @ECC",
                         tmp_path / "out", {"sparse_infill_density": "40%"})
    assert r["filament_type"] == "PETG"
    assert float(r["filament_g"]) > 0
    assert r["layers"] == "100"
    assert "; sparse_infill_density = 40%" in Path(r["gcode"]).read_text()


def test_server_registers_tools():
    import asyncio

    from orcaslicer_mcp import server
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"slicer_info", "list_profiles", "slice_model", "gcode_stats",
                     "printer_info", "upload_to_printer"}


def test_tool_errors_reach_the_client(monkeypatch):
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError

    from orcaslicer_mcp import server
    monkeypatch.delenv("ELEGOO_HOST", raising=False)
    with pytest.raises(ToolError, match="ELEGOO_HOST"):
        asyncio.run(server.mcp.call_tool("printer_info", {}))
