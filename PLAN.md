# PLAN — IFC indoor walking-network prototype

Source of truth for requirements: [SPEC.md](SPEC.md). This file orders the work; [PROGRESS.md](PROGRESS.md) tracks status.

## Findings that shape the plan (from Phase 1 inspection)

| Topic | Finding in `data/20210219Architecture.ifc` | Consequence |
|---|---|---|
| Schema / source | IFC2X3, Revit 2019 export (CoordinationView 2.0), 113 MB, loads in ~4 s | `IfcWallStandardCase` exists; no `IfcMapConversion` in schema |
| Units | Length unit = FOOT (×0.3048). IFC attributes (e.g. `Elevation`) are in feet; IfcOpenShell geometry output is in metres | Convert attributes explicitly; verify geometry units in Phase 3 |
| Storeys | 8: Level 1 (0 ft) … Level 7A (100 ft), Level 7 (111 ft). Name order ≠ elevation order | Sort by elevation; exact/case-insensitive match only (no substring: "Level 7" ≠ "Level 7A") |
| Containment | 0 unassigned elements. Most elements contained directly in storeys; 415 proxies contained in `IfcSpace`; curtain-wall parts, sub-stairs and roof slabs via aggregation; openings via host | Membership resolved transitively through explicit relationships; path recorded |
| Slabs | All 24 `IfcSlab` are `PredefinedType=ROOF`, aggregated in `IfcRoof` (Levels 2, 3, 7). **No floor slabs** | Slabs cannot be the floor footprint. Footprint depends on `IfcSpace` |
| Spaces | 187: Level 1 = 95, Level 2 = 91, Level 6 = 1, none on Levels 3–5, 7, 7A | Floor polygon only feasible for Levels 1–2; others must report "footprint unavailable" |
| Walls | 1578 (1392 standard case). `Pset_WallCommon.IsExternal` present on all: 262 true / 1316 false. Revit "Top Constraint" shows walls spanning storeys | Classification available. Multi-storey walls only belong to their base storey — report, do not reassign |
| Doors | 435 `IfcDoor`; 427 fill openings (opening → host wall via `IfcRelVoidsElement`); 8 without `FillsVoids` (curtain-wall doors, 6 aggregated in `IfcCurtainWall`) | Door→opening→wall chain available for most doors |
| Openings | 766: 754 in `IfcWallStandardCase`, 2 in `IfcWall`, 10 in slabs; filled by 427 doors + 131 windows | Opening-aware walls must only use **door-filled** openings (never windows) |
| Lifts | **0 `IfcTransportElement`.** Elevator families exported as `IfcBuildingElementProxy` (`LandingDoor2L/2R`, `CarDoor`, `CabinFront`) + 4 `IfcColumn` "ElevatorCoordinateSystem" (false positives) | Only label-based lift candidates exist — must be flagged as such |
| Lift data quality | 7 landing doors contained in Level 1 are physically at Z ≈ 19.7 ft (Level 2) | Report containment/elevation mismatch; do not reassign |
| Stairs | 60 `IfcStair` (30 are sub-stairs aggregated in a parent stair); 0 `IfcStairFlight` | Display parent stairs; avoid double-counting |
| Other obstacles | 178 `IfcCurtainWall`, 349 `IfcColumn`, 89 `IfcRailing`, 201 furnishings, 1462 proxies | Decide which count as "clearly identified fixed obstacles" (configurable) |
| Georeferencing | `IfcSite` "Default" with RefLat/Long 42°24′N, 71°15′W (Revit default location — Boston); no map conversion | Treat as **not reliable**. Use a clearly labelled synthetic local coordinate display |
| Relationships absent | 0 `IfcRelSpaceBoundary`, 0 `IfcRelReferencedInSpatialStructure`, 0 `IfcZone` | No space↔door/wall adjacency from IFC; no explicit multi-storey references |
| Performance | First `create_shape` call ~5 s (initialisation); later shapes ms | Use `ifcopenshell.geom.iterator` with an element filter and multiple cores |

Default target storey: **Level 1** (95 spaces, 120 doors, 38 lift label candidates).

## Decisions (reviewed after Phase 1)

1. **Lift access**: use name-based `Elevator_LandingDoor*` proxies as lift access points, always flagged `identification = label_only`. Other elevator-labelled objects (car doors, cabins, columns) are context only, never access points.
2. **Extra obstacles**: subtract `IfcCurtainWall` and `IfcColumn` (configurable, separate layers). Railings/furnishings/proxies are not obstacles.
3. **Wall 2D method** (revised in Phase 3 on evidence): a horizontal section at walking height would open 58 of 66 Level 1 window openings (sills at 0.71 m) and 48 unfilled openings, violating the "never window openings" rule. Instead: walls are meshed **solid** (`disable-opening-subtractions`); naive version = full solid footprints; opening-aware version = solid footprints minus footprints of **door-filled openings that span walking height** (storey elevation + 1.0 m).
4. **Version control**: git repository; commit at the end of each phase. Large IFC data and generated outputs are git-ignored.

## Decision (reviewed after Phase 2)

5. **Misplaced landing doors**: 7 of Level 1's 16 `Elevator_LandingDoor*` proxies are placed at Z ≈ 6.0 m (Level 2 height) although contained in Level 1. Decision: report and flag them, but do **not** connect them to the Level 1 network (status `excluded_placement_inconsistent`). This excludes; it never reassigns.

## Decision (reviewed after Phase 3)

6. **Floor footprint source = wall envelope (option B)**, because `IfcSpace` is not reliable enough (Level 1 main circulation is not modelled as a space: spaces ≈ 2,730 m² vs envelope ≈ 6,600 m²). Footprint = filled outer rings of the union of wall + curtain-wall footprints that span walking height, flagged `footprint_source = derived_from_wall_envelope`. A 0.02 m morphological closing seals sub-centimetre envelope gaps (without it Level 1 loses the corridor/lobby, ~2,000 m²); it is applied only to the envelope, never to the walkable polygon. Fallback: FLOOR/BASESLAB slabs, then `unavailable`. `IfcSpace` stays a separate layer and is compared in diagnostics.

## Phases

Each phase ends with runnable code, tests passing, and an inspected output. Do not start a phase until its dependencies are validated.

### Phase 1 — Environment, IFC inspection, storey inventory ✅
- **Deliverables**: `.venv`, `ifc_indoor_network/{config.py, ifc_reader.py, main.py, requirements.txt, tests/}`, `output/inventory.json`.
- **Validation**: unit tests (synthetic IFC4 model) + integration test on the sample; CLI error paths (no match, ambiguous, bad path).
- **Done when**: schema, units, buildings, storeys (name/GlobalId/elevation), category counts per storey, membership paths, unassigned elements, label/pset summaries and lift candidates are printed and written to JSON; storey selection works by exact / case-insensitive name or GlobalId with helpful errors.

### Phase 2 — Semantic element extraction for the selected storey ✅
- **Depends on**: Phase 1.
- **Deliverables**: `ifc_reader.py` extraction functions returning plain records (no geometry) for walls (IsExternal → internal/external/unknown), slabs (with predefined type), spaces, doors (opening + host wall via `IfcRelFillsElement`/`IfcRelVoidsElement`), lifts (explicit + label candidates, flagged), stairs (parent/child), candidate obstacles; relevant psets; membership path per element. Written to `output/elements.json`.
- **Validation**: counts match Phase 1 per-storey counts; doors without relationships listed; tests on synthetic model.
- **Done when**: every record has GlobalId, name, entity, predefined/object type, storey, membership path and extraction status; nothing is assigned to the storey without an explicit relationship.

### Phase 3 — Geometry extraction → 2D Shapely (+ minimal viewer) ✅
- **Depends on**: Phase 2.
- **Deliverables**: `geometry_processing.py`: IfcOpenShell iterator (world coords) for selected GlobalIds; per element: 3D bounds/z-range, 2D footprint (XY projection) and, for walls, a horizontal section at walking height; door representative point/line; failures recorded per element. GeoDataFrames per category in a local CRS (metres). Minimal `visualisation.py` Folium map in a synthetic `CRS.Simple` frame to inspect outputs.
- **Validation**: unit sanity (door widths ≈ 0.8–1.8 m, storey Z matches), valid geometry counts, visual check that walls/spaces align; compare section-vs-projection for walls with door openings.
- **Done when**: Level 1 walls, spaces, doors, stairs, lift candidates render in correct relative positions with failure counts reported.

### Phase 4 — Floor footprint and walkable polygons ✅
- **Depends on**: Phase 3.
- **Deliverables**: footprint from wall envelope (decision 6) → FLOOR/BASESLAB slabs → unavailable; naive walkable = footprint − solid wall footprints − configured obstacles; opening-aware walkable = footprint − (each host wall/curtain wall minus its own door cuts: door-filled openings spanning walking height, or doors aggregated in curtain walls). Repair (`make_valid`), sliver removal with configurable tolerance, area-loss logging.
- **Validation**: areas before/after, component counts, doorways visibly open in opening-aware version, no aggressive buffering.
- **Done when**: both polygons generated for Level 1, layered output inspected, and a non-space storey (e.g. Level 4) reports "footprint unavailable" cleanly.

### Phase 5 — Centreline with pygeoops
- **Depends on**: Phase 4.
- **Deliverables**: `network_generation.py` using `pygeoops.centerline(geometry, densify_distance, min_branch_length, simplify_tolerance, extend)` (signature verified in 0.6.0) per polygon component; stable component IDs; failures/empties reported with diagnostics.
- **Validation**: runtime acceptable, components count, visual overlay.
- **Done when**: centreline layer produced for each walkable component or a recorded reason why not.

### Phase 6 — Door and lift connectors
- **Depends on**: Phase 5.
- **Deliverables**: access point per door, nearest centreline point (`shapely.ops.nearest_points`), connector line, distance, status vs configurable max snap distance; flag points outside walkable polygon. Lift connection only from explicit (or, if approved, flagged label-based) access geometry — never shaft centres.
- **Done when**: every door has a connector or a recorded failure reason.

### Phase 7 — Full interactive 2D visualisation
- **Depends on**: Phases 3–6.
- **Deliverables**: Folium HTML with all layers listed in SPEC §7, popups (GlobalId, name, entity, storey, properties, status), legend, summary panel, "synthetic local coordinates — not geographic" label.
- **Done when**: `output/floor_plan.html` opens and every layer toggles and inspects correctly.

### Phase 8 — Diagnostics report
- **Depends on**: Phases 2–6.
- **Deliverables**: `diagnostics.py` console + `output/diagnostics.json` covering SPEC §9 (incl. unconnected doors, centreline components without doors, disconnected walkable areas).
- **Done when**: every SPEC §9 item is present or explicitly marked unavailable with reason.

### Phase 9 — Optional Plotly 3D view
- **Depends on**: Phase 3 (+ 5 for centreline). Only if effort is reasonable.
- **Deliverables**: `output/floor_plan_3d.html` with filterable element types.

### Phase 10 — README and acceptance review
- **Deliverables**: README (setup, config, run, outputs, interpreting missing relationships/failures); walk SPEC §13 acceptance criteria; final summary + next three improvements.

## Risks / failure points
- Geometry units or placement mismatch (feet attributes vs metre geometry).
- Wall projections closing doorways; window openings wrongly treated as passable.
- Space polygons may not cover corridors/circulation fully (unbounded areas) → disconnected walkable graph.
- pygeoops centreline on complex polygons with many holes: runtime and spurious branches.
- Folium is geographic; local coordinates must use `CRS.Simple` or a clearly synthetic transform.
- Label-only lift identification may be wrong; must stay flagged.
