# PROGRESS

**Current phase:** Phase 3 complete — awaiting review before Phase 4 (open question 6 in PLAN.md).

## Completed

### Phase 3 — Geometry extraction → 2D (2026-10-21)
- [ifc_indoor_network/geometry_processing.py](ifc_indoor_network/geometry_processing.py): `ifcopenshell.geom.iterator` (world coords, metres, opening subtraction disabled, multi-threaded) for all Phase 2 records; XY footprint = union of projected non-degenerate triangles (1 mm grid); z-range and `covers_walking_height`; curtain-wall footprints from aggregated parts; door representative point (footprint centroid) and axis (long side of min. rotated rectangle); status per element (`ok`, `ok_from_parts`, `no_representation`, `no_geometry_output`, `empty_footprint`); `make_valid` repairs logged.
- [ifc_indoor_network/visualisation.py](ifc_indoor_network/visualisation.py): minimal Folium map in Leaflet `CRS.Simple` (no tiles), labelled as synthetic local coordinates; toggleable layers with tooltips; summary panel.
- `main.py` writes `output/geometry_summary.json` and `output/floor_plan.html`. Total run ~9 s (geometry ~2.5 s).
- Level 1: walls 278 ok, spaces 95 ok, doors 120 ok, openings 233 ok, obstacles 93 ok + 12 curtain walls from parts, stairs 7 ok + 7 parent stairs without representation (parts extracted separately), lifts 38 ok; 0 repairs. Visual check: geometry aligned and plausible.
- Units verified: door axis − `OverallWidth` = +0.15 m for 112 doors (Revit trim 2 × 0.25 ft), 0.00 for 6 curtain-wall doors, +0.61 m for 2 doors (outliers listed). Level 4 walking height 16.85 m = 52 ft × 0.3048 + 1 m.

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
- `cd ifc_indoor_network && ../.venv/bin/python -m pytest -q` → **24 passed** (~16 s; includes sample-file integration tests, skipped if file absent).
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
- **Main circulation is not modelled as `IfcSpace`** on Level 1: spaces ≈ 2,730 m² vs ≈ 4,650 m² enclosed by walls/curtain walls.
- Horizontal wall sections at 1 m would open window openings (sills 0.71 m) → wall method revised (PLAN decision 3).
- Level 1: 13 openings, 4 obstacles and 11 lift parts do not span walking height; Level 4: 11 walls do not.

## Decisions
- Reviewed after Phase 1: see "Decisions" in [PLAN.md](PLAN.md) (landing-door proxies as flagged lift access; curtain walls + columns as obstacles; wall section at walking height; git commit per phase).

## Next steps
1. Decide open question 6 in [PLAN.md](PLAN.md) (floor footprint source).
2. Phase 4: floor footprint and naive / opening-aware walkable polygons.
