# CLAUDE.md — project instructions

IFC → indoor walking-network prototype. Requirements: [SPEC.md](SPEC.md). Phases: [PLAN.md](PLAN.md). Status: [PROGRESS.md](PROGRESS.md) — update it at the end of every phase.

## Environment
- Python venv at `.venv` (3.11). Install: `.venv/bin/pip install -r ifc_indoor_network/requirements.txt`.
- Run from `ifc_indoor_network/`: `../.venv/bin/python main.py --storey "Level 1"` (`--list-storeys`, `--storey-id`, `--ifc`, `--output-dir`, `--no-3d`, `--export-images`).
- README images: `--export-images`, then copy `output/floor_plan_{2d,3d}.png` to `docs/images/`.
- Tests: `cd ifc_indoor_network && ../.venv/bin/python -m pytest -q`.
- Sample model: `data/20210219Architecture.ifc` (IFC2X3, Revit, length unit FOOT, ~4 s to load).

## Rules
- Work phase by phase; stop at each phase checkpoint for review. Do not implement later phases early.
- Commit to git at the end of each phase (`Phase N: ...`). `data/*.ifc` and `output/` are git-ignored.
- Keep modules separate: `ifc_reader` (semantics only) → `geometry_processing` → `network_generation` → `visualisation` → `diagnostics`.
- Storey membership only via explicit IFC relationships; record the path. Never assign by geometry, never guess.
- Never invent IFC labels, relationships, openings, lift entrances or geographic coordinates. Report gaps instead.
- Label-based matches (e.g. "Elevator" proxies) must stay flagged as label-only candidates. Only `Elevator_LandingDoor*` proxies may serve as lift access points.
- IFC attributes are in project units (feet here); IfcOpenShell geometry is in metres. Convert explicitly.
- Verify installed library APIs (`inspect.signature`) before use; pygeoops is 0.6.0.
- Only door-filled openings may make walls passable — never window openings. Walls are meshed solid; doorways are cut with door-opening footprints (no horizontal sections: window sills are below walking height).
- Folium: local coordinates must be shown as clearly labelled synthetic coordinates.
- Record assumptions, data-quality issues and failures in PROGRESS.md and diagnostics output.
- No unrelated refactoring; keep code simple.
