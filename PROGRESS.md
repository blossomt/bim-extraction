# PROGRESS

**Current phase:** Phase 1 complete — awaiting review before Phase 2.

## Completed

### Phase 1 — Environment, IFC inspection, storey inventory (2026-10-21)
- Created project venv `.venv` (Python 3.11.9). Pinned in [ifc_indoor_network/requirements.txt](ifc_indoor_network/requirements.txt): ifcopenshell 0.9.0.post1, shapely 2.2.0, geopandas 1.2.0, pygeoops 0.6.0, folium 0.20.0, plotly 7.1.0, numpy 2.4.6, pytest 9.1.1.
- [ifc_indoor_network/config.py](ifc_indoor_network/config.py): IFC path, target storey, categories, lift keywords.
- [ifc_indoor_network/ifc_reader.py](ifc_indoor_network/ifc_reader.py): open model, units, buildings, storeys (sorted by elevation, metres + raw), entity counts (schema-aware), transitive storey membership via explicit relationships (contained_in / aggregated_by / nested_in / voids / fills) with recorded path, per-storey direct/indirect/unassigned counts, label/pset summaries, wall IsExternal summary, lift candidates (explicit vs label-only), georeferencing info, storey selection.
- [ifc_indoor_network/main.py](ifc_indoor_network/main.py): CLI `--ifc --storey --storey-id --list-storeys --output-dir`; writes `output/inventory.json`. Exit code 2 on bad path / failed selection.

## Tests
- `cd ifc_indoor_network && ../.venv/bin/python -m pytest -q` → **14 passed** (~7 s; includes sample-file integration test, skipped if file absent).
- Manual: `main.py` (Level 1) exit 0; `--storey "Level 9"` exit 2 listing storeys; `--storey "level 7a"` selects Level 7A (not Level 7); bad `--ifc` path exit 2.

## Known issues / data-quality findings
- No floor slabs (all 24 `IfcSlab` are ROOF). Spaces exist only on Levels 1, 2 (and 1 on Level 6).
- No `IfcTransportElement`; lifts only identifiable by proxy names (`Elevator_*`). 4 `IfcColumn` "ElevatorCoordinateSystem" are label false positives.
- 7 elevator landing-door proxies contained in Level 1 lie at Z ≈ 19.7 ft (Level 2 height).
- 8 `IfcDoor` without `FillsVoids` (curtain-wall doors).
- 30 of 60 `IfcStair` are sub-stairs aggregated into parent stairs; no `IfcStairFlight`.
- Walls span storeys (Revit "Top Constraint") but are contained only in their base storey.
- Site RefLat/Long is Revit's default location; no `IfcMapConversion` → treat as not georeferenced.

## Decisions
- Reviewed after Phase 1: see "Decisions" in [PLAN.md](PLAN.md) (landing-door proxies as flagged lift access; curtain walls + columns as obstacles; wall section at walking height; git commit per phase).
