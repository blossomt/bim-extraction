# PROGRESS

**Current phase:** Phase 2 complete — awaiting review before Phase 3.

## Completed

### Phase 2 — Semantic element extraction (2026-10-21)
- `ifc_reader.extract_storey_elements()` returns records (no geometry) for walls, slabs, spaces, doors, openings, stairs, stair flights, obstacles (`IfcCurtainWall`, `IfcColumn`) and lifts. Every record has GlobalId, entity, name, object/predefined type, type object, storey, membership path, aggregate parent, placement Z (m) and selected psets (`Pset_*` + configured Revit sets).
- Walls: classification internal/external/unknown from `Pset_WallCommon.IsExternal`; openings with fillings; Revit Base/Top Constraint kept as an informational span hint.
- Doors: opening + host wall via `IfcRelFillsElement`/`IfcRelVoidsElement`, status `opening_and_host` / `opening_without_host` / `no_fills_relationship`; overall width/height in metres.
- Openings: `filling_kind` door / window / other / unfilled (only door openings may open walls later).
- Lifts: `identification` explicit_ifc / label_only, `role` landing_door / car_door / cabin / other, `is_access_point_candidate` only for names matching `^Elevator_LandingDoor`.
- Placement-Z check (report only): flags elements placed outside `[elevation − 0.5 m, next elevation − 0.5 m)`.
- `main.py` prints the summary, cross-checks counts against the Phase 1 inventory and writes `output/elements.json`.
- Level 1 result: 278 walls (272 internal, 6 external), 0 slabs, 95 spaces, 120 doors (114 with opening+host, 6 curtain-wall doors without), 233 openings (114 door, 66 window, 53 unfilled), 14 stairs (7 top-level), 105 obstacles (93 columns, 12 curtain walls), 38 lift label candidates (16 landing doors → 9 at floor level, 7 flagged at Z ≈ 6.0 m). Cross-check OK (also Levels 4 and 7).

### Phase 1 — Environment, IFC inspection, storey inventory (2026-10-21)
- Created project venv `.venv` (Python 3.11.9). Pinned in [ifc_indoor_network/requirements.txt](ifc_indoor_network/requirements.txt): ifcopenshell 0.9.0.post1, shapely 2.2.0, geopandas 1.2.0, pygeoops 0.6.0, folium 0.20.0, plotly 7.1.0, numpy 2.4.6, pytest 9.1.1.
- [ifc_indoor_network/config.py](ifc_indoor_network/config.py): IFC path, target storey, categories, lift keywords.
- [ifc_indoor_network/ifc_reader.py](ifc_indoor_network/ifc_reader.py): open model, units, buildings, storeys (sorted by elevation, metres + raw), entity counts (schema-aware), transitive storey membership via explicit relationships (contained_in / aggregated_by / nested_in / voids / fills) with recorded path, per-storey direct/indirect/unassigned counts, label/pset summaries, wall IsExternal summary, lift candidates (explicit vs label-only), georeferencing info, storey selection.
- [ifc_indoor_network/main.py](ifc_indoor_network/main.py): CLI `--ifc --storey --storey-id --list-storeys --output-dir`; writes `output/inventory.json`. Exit code 2 on bad path / failed selection.

## Tests
- `cd ifc_indoor_network && ../.venv/bin/python -m pytest -q` → **19 passed** (~7 s; includes sample-file integration test, skipped if file absent).
- Manual: `main.py` (Level 1) exit 0; `--storey "Level 9"` exit 2 listing storeys; `--storey "level 7a"` selects Level 7A (not Level 7); bad `--ifc` path exit 2.

## Known issues / data-quality findings
- No floor slabs (all 24 `IfcSlab` are ROOF). Spaces exist only on Levels 1, 2 (and 1 on Level 6).
- No `IfcTransportElement`; lifts only identifiable by proxy names (`Elevator_*`). 4 `IfcColumn` "ElevatorCoordinateSystem" are label false positives.
- 7 elevator landing-door proxies contained in Level 1 lie at Z ≈ 19.7 ft (Level 2 height).
- 8 `IfcDoor` without `FillsVoids` (curtain-wall doors).
- 30 of 60 `IfcStair` are sub-stairs aggregated into parent stairs; no `IfcStairFlight`.
- Walls span storeys (Revit "Top Constraint") but are contained only in their base storey.
- Site RefLat/Long is Revit's default location; no `IfcMapConversion` → treat as not georeferenced.
- Level 1 has 53 unfilled wall openings (not door openings; will not be made passable).
- `IfcCurtainWall` geometry lives in aggregated `IfcPlate` parts — Phase 3 must handle parts.
- 2 "ElevatorCoordinateSystem" `IfcColumn`s on Level 1 appear both as lift label candidates (role `other`) and obstacles.

## Decisions
- Reviewed after Phase 1: see "Decisions" in [PLAN.md](PLAN.md) (landing-door proxies as flagged lift access; curtain walls + columns as obstacles; wall section at walking height; git commit per phase).

## Next steps
1. Review open question 5 in [PLAN.md](PLAN.md) (misplaced landing doors).
2. Phase 3: geometry extraction to 2D Shapely + minimal Folium viewer in synthetic local coordinates.
