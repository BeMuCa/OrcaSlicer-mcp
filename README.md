# OrcaSlicer-mcp

MCP server that lets Claude (or any MCP client) slice models with the **official, unmodified
[OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer)** through its command line. No fork and
no patched build: just updates OrcaSlicer itself.

Tested with OrcaSlicer 2.4.2 on Windows, driven from WSL2, Elegoo Centauri Carbon profiles.

## Tools

| Tool | What it does |
|---|---|
| `slicer_info` | Where OrcaSlicer and its bundled profiles are, list of vendors |
| `list_profiles(vendor, kind, query)` | Selectable machine / process / filament profiles |
| `slice_model(model_path, vendor, machine, process, filament, overrides)` | Slice an STL/3MF, return G-code path + time, filament (mm, cm³, g), layers |
| `gcode_stats(gcode_path)` | Estimates from an existing OrcaSlicer G-code |

`overrides` are process settings by OrcaSlicer key, e.g. `{"sparse_infill_density": "40%"}`.

## How profiles are handled

Bundled profiles inherit from each other. The CLI resolves that for machine files, but not for
filament files (they silently slice as PLA with 0 g). So the server flattens the filament and
process chains itself and passes the original machine file.

## Setup

1. Install OrcaSlicer and [uv](https://docs.astral.sh/uv/).
2. Clone this repo.
3. Add to your `.mcp.json` (Claude Code):

```json
{
  "mcpServers": {
    "orcaslicer": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/OrcaSlicer-mcp", "orcaslicer-mcp"],
      "env": { "ORCASLICER_PATH": "/mnt/c/Program Files/OrcaSlicer/orca-slicer.exe" }
    }
  }
}
```

`ORCASLICER_PATH` is only needed if OrcaSlicer is not on `PATH` or in its default install
location. From WSL, a Windows `orca-slicer.exe` works; paths are converted with `wslpath`.

## Tests

```bash
uv run pytest                                      # unit tests
ORCASLICER_PATH=/path/to/orca-slicer uv run pytest # plus a real slice of a 20 mm cube
```

## License

MIT
