# PROGRESS

**Current phase:** Phases 1–7 and 9 complete; Phase 8 (consolidated diagnostics report) and Phase 10 (acceptance review) remain.

## Completed

### Centreline constraints: 1 m clear width, no parallel lines (2026-10-22)
- `_corridor_centreline` now: (1) opens each corridor polygon by `CORRIDOR_MIN_CLEAR_WIDTH_M = 1.0` (shrink and regrow by 0.5 m), so no centreline passes a gap under 1 m and corridors under 1 m wide get none (status `empty`, doors there get `no_centreline` connectors); (2) runs `pygeoops.centerline` per remaining part; (3) `collapse_obstacle_loops` keeps one side of loops enclosing < `CORRIDOR_LOOP_MAX_AREA_M2 = 20` m² (the side nearer the corridor boundary is dropped); (4) `drop_parallel_leaves` removes leaf branches with ≥ 70 % of their length within `CORRIDOR_PARALLEL_OFFSET_M = 1.5` m of another branch; (5) the existing corner-spur pruning. Parameters are recorded in `summary.json` `corridor_parameters`.
- Root causes on a split-level validation storey: the medial axis passed through narrow gaps (508.7 m of centreline where the clear width was < 1 m), looped around 200+ tiny holes, and fanned out towards a saw-tooth boundary made of ~2 cm segments in a wide hall, where spur pruning is deliberately off.
- Split-level validation storey (cached geometry): centreline 1533 → 717 m, pieces 72 → 34, leaf ends 224 → 91, length in gaps < 1 m 508.7 → 22 m (the remainder is leaf ends near walls). Corridor status 30 ok / 7 fragmented → 23 ok / 3 fragmented / 11 empty; all empty corridors are < 1 m wide. The columned walkway in R000 is cut at each column because its gaps are 0.65 m and 0.9 m. Full run: largest network component 27 of 41 connected doors, no too-long connectors.
- A loop-obstacle-area criterion was tried first; it dropped the door side of a loop in Level 1's main hall (one 10.7 m connector over its limit), so the criterion is the total loop area.
- Level 1: 3 corridors under 1 m wide are now `empty`; no too-long connectors. Committed `output/` was not regenerated (only the split-level validation storey was regenerated locally). Tests added for the narrow gap, column loop and parallel leaf; the Level 1 sample test now allows `empty` corridors narrower than 1 m.

### CLI follow-up — Explicit IFC input required (2026-10-22)
- Made `--ifc` mandatory, including inventory-only runs; removed `IFC_PATH` and its unused root constant from configuration. No input model is selected implicitly.
- Updated [README.md](README.md) commands and relative-path guidance. Sample integration tests use a test-only fixture pointing at `ifc_indoor_network/data/20210219Architecture.ifc`, independent of application configuration.
- Validation: 33 passed, 2 skipped (sample absent at the integration-fixture location); missing-input cases exit with argparse's required-argument error, and explicit paths are preserved. No IDE errors in changed Python files. No outputs regenerated.

### All walls block at any height (2026-10-22)
- Root cause (centrelines appearing to cross walls): a split-level validation storey with a ramp; 48 of 49 crossed walls lie wholly below the floor (e.g. 2.5–3.85 m vs walking height 5.0 m) or above it (from +3.1 m), so the single walking-height filter excluded them although they separate levels.
- New `config.WALLS_BLOCK_AT_ANY_HEIGHT = True`: every storey wall with geometry is an obstacle and envelope element, and door-filled openings cut at any height. Columns/curtain walls keep the walking-height filter. `summary.json` `floor_plan.walls_block_at_any_height` records the setting.
- Accepted trade-off, measured with the cached geometry: Level 1 and another validation storey unchanged; the split-level validation storey's centreline inside walls 38.2 → 0.2 m, but largest network component 44/47 → 28/47 doors; a further validation storey 75/75 → 55/78 (bulkhead and below-floor downstand walls now block corridors). A proper fix would use a separate walking height per area (from IfcSpace/slab elevations).
- Test added (`test_walls_not_spanning_walking_height_block_when_configured`).

### Lift access skips the shaft region (2026-10-22)
- `build_connectors` now connects each eligible lift landing door to the nearest adjacent region that is not a lift region (a region with ≥ 50 % of its area covered by lift parts), so lifts join the main network instead of an isolated shaft region. Level 1: still 9 lifts connected / 7 excluded; network components 14 → 11, largest component still holds 118 of 118 connected doors; connectors crossing non-walkable area 5 → 11 (lift connectors crossing the shaft wall, flagged in `summary.json`). Test added.

### README flow diagram simplification (2026-10-22)
- Mermaid extraction flow reduced from 12 to 7 nodes in plain, active wording (read model and pick storey; identify floor boundary; identify doors in walls; identify rooms and corridor-like regions; create centre nodes and centrelines; connect doors and lifts; output 2D/3D map). Display-mesh, inventory and PNG nodes removed.
- Folium layer-control font reduced to 11 px; layer order left unchanged (draw order matters: region fills must sit under walls/doors/connectors).

### Map header, popups, elements.json removal (2026-10-22)
- Folium header is now: title "Indoor walking network (prototype)", then `Source file`, `Storey`, `Run time` (seconds up to the map build; the 3D view is built afterwards) and the synthetic-coordinates note. The key-parameters subheader and `main.map_parameters` were dropped. `build_floor_map(frames, ifc_name, storey_name, run_seconds, bounds_xy, out_path)`.
- `output/elements.json` is no longer written (nothing read it). The extraction summary moved into `summary.json` under `extraction`; the per-element storey-membership paths are no longer exported (they remain in the in-memory extraction result).
- Popups: per-layer field lists in `visualisation.DERIVED_POPUP_FIELDS` (regions: `ifc_space`, `region_id`, `kind`, `area_m2`, `doors`; no rectangularity/aspect/holes/status). GeoJSON carries only popup and style columns.
- New `network_generation.label_regions`: region → IfcSpace label (LongName, else Name) by largest overlap, requiring ≥ 50 % of the region inside the space. Display-only; unmatched regions show "—". The label can be inexact where the model's spaces and the geometric regions disagree.

### Map header and PNG removal (2026-10-22)
- Folium map: removed the "Processing summary" panel and `map_summary_lines`; the header now shows the source IFC file name, with a subheader of key parameters (storey, walking height, room rule, spur pruning, snap limit) from `main.map_parameters`. `build_floor_map(frames, ifc_name, parameters, bounds_xy, out_path)`.
- Removed `--export-images`, `build_2d_figure`, `export_png` and the `kaleido` requirement; `output/floor_plan_{2d,3d}.png` deleted. `docs/images/*.png` remain as static README screenshots (not regenerated).

### Output and centreline simplification (2026-10-22)
- Outputs: the four per-stage summaries are now one `summary.json` (`geometry`, `floor_plan`, `network`, `connectors`). `inventory.json` and `elements.json` are unchanged; PNGs only with `--export-images`.
- Folium: removed footprint, slabs, IfcSpace, openings, stairs, door-centre/axis, doorless-room and excluded-lift layers (still available in `summary.json`/`elements.json`); popups show name, GlobalId, entity and class only; legend drops stairs. 2D PNG drops stairs. 3D view unchanged.
- Corridor centrelines: new `prune_corner_spurs` in `network_generation.py` removes leaf forks shorter than `CORRIDOR_SPUR_HALF_WIDTH_FACTOR` (1.5) × local half-width, only where half-width ≤ `CORRIDOR_SPUR_MAX_HALF_WIDTH_M` (4.0). Tuned on Level 1: pygeoops `min_branch_length` alone left junctions at 8 m; uncapped pruning (factor 1.5–3) cut centrelines to ~830–900 m but stranded wide-hall doors (5–18 too-long connectors, 3 components with doors). Chosen settings: centreline 1336 → 1025 m, 0 too-long connectors, 118 of 118 connected doors in one component.
- Known trade-off: branches in wide halls (> 4 m half-width) are not pruned, so some branching remains there.
- Validation: 51 passed (sample integration tests now run against `ifc_indoor_network/data/`). Level 1 outputs and `docs/images` regenerated; old four summary files removed.

### Phases 4–7 follow-up — One walkable area; simpler documentation (2026-10-22)
- Revised [README.md](README.md) to explain walkable area upfront in plain language: the Z check filters elements spanning walking height; it is not a horizontal section. Added venv activation commands and placed detailed method rules behind an expandable section. Configuration details remain in `config.py`.
- Removed the solid-wall comparison, `CENTRELINE_VARIANT`, variant metadata and duplicate 2D layer. Geometry, CLI, map summaries and network generation now use one `walkable` result; `floor_plan_summary.json` contains flat walkable statistics. Updated the related requirement and plan.
- Door eligibility, missing-cut reporting, blocked-doorway analysis and connector behavior remain unchanged, as confirmed with the user. Window openings remain impassable.
- Validation: focused geometry/network/visualisation suite passes (23 passed, 1 skipped: original sample IFC absent); regression checks cover exact walkable area, door/window passage behavior, single-result shape, network connections, console/map summaries and the exported layer. No IDE errors in changed Python files.
- Existing user-generated outputs and historical sample exports were not overwritten. They retain their previous layers/summary structure until regenerated. No later phase implemented; Phase 8 and Phase 10 remain pending.

### Phase 7 documentation follow-up — Extraction method and configuration (2026-10-22)
- Expanded [README.md](README.md) with a Mermaid extraction-flow diagram, method/terminology and assumption/limitation tables, and a grouped configuration reference checked against the implementation.
- Distinguished explicit IFC relationships from derived geometric adjacency, geometric room/corridor classes from space-use classifications, and analysis meshes from 3D display meshes.
- Documented current limitations: storey-datum fallbacks, unfiltered slab elevations, envelope void filling, report-only connector crossings, and differing floor-plan/network blockage criteria. Historical sample results are explicitly qualified; no model outputs were regenerated.
- Validation: documentation cross-checked against the pipeline, semantic extraction, geometry, network, visualisation and configuration sources; no runtime behavior changed. Phase 8 and Phase 10 remain pending; this is not an acceptance review.

### Phase 9 follow-up — Offline 3D view (2026-10-21)
- `build_3d_view()` writes with `include_plotlyjs=True`, so plotly.js (from the installed `plotly` 7.1.0 package, plotly.js 4.1.1) is embedded and `floor_plan_3d.html` needs no network access. Cost: about +4.6 MB per file.
- The embedded bundle still contains Plotly's default topojson URL (`cdn.plot.ly/un/`). Only geographic map traces use it; the 3D view has none.
- Validation: 42 passed, 2 skipped. The test checks there are no external `<script src>` tags. The existing `output/floor_plan_3d.html` was patched in place by swapping its CDN tag for the same plotly.js 4.1.1 bundle, and rendered with all non-file requests blocked (0 requests).

### Phase 4 revision — World storey Z; slab union in footprint (2026-10-21)
- Real-model test on an open validation storey: of 184 columns, 91 span walking height and 83 are freestanding. There are 34 FLOOR slabs, and no doors or spaces.
- **Storey datum bug (fixed):**
  - `IfcBuildingStorey.Elevation` is relative to the building, and this model places its `IfcBuilding` at z = 5.5 m. The validation storey has Elevation 20.5 m, but its world placement is z = 26.0 m.
  - Meshes are in world coordinates, so the walking height of 21.5 m missed every element: all walls, columns and openings were `covers_walking_height=False`.
  - New `StoreyInfo.world_z_m` uses placement Z, falling back to Elevation. It is now used for walking height, the placement-Z window and the 3D floor/clip.
- **Footprint = wall envelope ∪ FLOOR/BASESLAB slabs (user decision):**
  - With the correct datum, the wall envelope covers only 94 m² in 117 parts; this is an open level. The FLOOR slabs cover 1,479 m².
  - `footprint_source = wall_envelope_and_floor_slabs`. The summary `footprint_parts_m2` reports the envelope, slab, slabs-only and envelope-only areas.
  - Trade-off: slab overhangs and balconies are included. On another validation storey the footprint grows 1,713 → 1,752 m² (+38.5 m² from slabs only), and all 33 doors stay in one network component.
  - Slabs are not filtered by height. Some storey-contained slabs sit at other elevations.
- **Centreline around columns (tried, then reverted at the user's request):**
  - pygeoops' medial axis loops around every hole. On the open validation storey there are 44 loops, 35 of them around freestanding columns (0.3–1.4 m² each).
  - A variant filled holes ≤ 2 m² and detoured the line around them (44 → 2 loops, 862 → 649 m). The user prefers loops around columns, because walking either side is a valid route, so the variant was removed. The centreline is unchanged from Phase 5.
- **Validation:** 42 passed, 2 skipped (the reverted detour tests were removed). The open validation storey runs end to end including 3D (exit 0). The Level 1 sample (absent) was **not** re-run, and the committed outputs were not regenerated.
### Phase 4 revision — Columns close the wall envelope (2026-10-21)
- Real-model finding on a validation storey: facade walls stop at structural columns, so the wall + curtain-wall envelope was open around several units. Footprint 842 m² in 5 parts; 870 of 1,699 m² of `IfcSpace` area lay outside it, so those units had no regions, nodes or centrelines.
- `build_floor_plan()` now adds `IfcColumn` footprints spanning walking height (`config.ENVELOPE_OBSTACLE_ENTITIES`) to the envelope. Columns are still subtracted as obstacles, so walkable geometry inside the envelope is unchanged. The summary reports `envelope_elements`.
- `5TH STOREY` result: footprint 1,713 m² in 1 part; `IfcSpace` area outside footprint 0 m²; walkable 1,625 m²; 26 rooms / 7 corridors; 33 doors open, 30 connected on both sides; one network component holds all 33 doors (previously 6 door-bearing components, 1 blocked door).
- Windows were not added to the envelope. 136 of 148 windows on that storey fill wall openings, and walls are meshed solid, so they would add nothing. Tested: adding windows changed the footprint by 0 m².
- Still present (expected): L-shaped units 05-01/05-02 are classified as corridors (rectangularity 0.73–0.79), and rooms 02-107/02-108 are doorless because their only openings have no door filling.
- Validation: 40 passed, 2 skipped (the sample-IFC tests; `data/20210219Architecture.ifc` was not present). The committed Level 1 sample outputs were **not** regenerated and the effect of this change on Level 1 is unverified.

### Phase 9 follow-up — Empty-storey 3D rendering (2026-10-21)
- Reproduced the `layout.scene.aspectratio.x = NaN` crash using an empty network, both with and without valid IFC display meshes. Root cause: empty region `total_bounds` is all NaN and was always included in the 3D XY bounds.
- `build_3d_view()` now includes region bounds only when non-empty geometry exists. Empty results have an explicit no-displayable-geometry notice and hidden axes; the CLI also prints a warning. Zero horizontal extent uses Plotly data aspect mode instead of dividing by zero. Non-empty views retain their true-scale, clipped aspect ratios.
- Validation: 6 visualisation tests pass; no IDE diagnostics in the changed Python files. Regression coverage includes empty results with/without next-storey clipping, valid meshes without walkable regions, zero horizontal extent and existing non-empty/offline exports.
- The reported `Storey -1` run extracted zero elements in every supported category; its footprint was unavailable. No IFC membership was inferred or reassigned. User-generated model outputs were preserved. A full-model validation attempt was blocked by organization content policy on the diagnostic-log path; no access workaround was attempted.

### Phase 7 follow-up — Offline 2D map (2026-10-21)
- `floor_plan.html` is now a standalone file: Leaflet 1.9.3, jQuery 3.7.1, Bootstrap 5.2.2 CSS and Leaflet control images are embedded. Pinned source assets and their licenses are kept in `ifc_indoor_network/vendor/`; generation needs no network access. Unused Bootstrap JS, AwesomeMarkers and icon-font CDN dependencies are omitted.
- `visualisation.build_floor_map()` embeds the same assets for all future exports. The committed Level 1 HTML was updated without changing its map body or geometry; the sample IFC is not present in this checkout, so the IFC pipeline was not re-run.
- Validation: 2 visualisation tests pass, including a standalone-resource regression test from a different working directory. Opened the committed HTML via `file://` with browser networking disabled and HTTP(S) requests blocked (cache bypassed): zero external requests or JavaScript errors; map rendering, embedded layer-control image, door-layer toggling, zoom and IFC property popup verified.
- Scope: the optional Plotly 3D HTML still used its CDN at this point (made offline in the "Offline 3D view" follow-up).

### Phase 7 — Presentation-ready visualisation + README (2026-10-21)
- 2D map: layers grouped as `IFC ·`, `Analysis ·`, `Network ·`; analysis/intermediate layers hidden by default; click popups with name, GlobalId, entity, storey, status, info and flattened standard `Pset_*` properties (new `properties` column); short hover tooltips; legend panel; title bar with the synthetic-coordinates note; collapsible processing summary with presentation-level facts (`map_summary_lines` in `main.py`); collapsed layer control; neutral canvas background.
- 3D view: legend groups (IFC elements / walking network), translucent walls and curtain walls, raised default camera, Z axis clipped at the next storey elevation (noted on the figure), stair railings skipped (`PLOT3D_SKIP_PART_ENTITIES`), float32 coordinates, shorter hover labels → 4.8 MB (was 13.6 MB), ~2.5 s.
- `--export-images`: static PNGs `floor_plan_2d.png` (Plotly figure from the same geometries) and `floor_plan_3d.png` via kaleido 1.5.0 (uses local Chrome). Added to requirements.
- Root [README.md](README.md) with method, setup, run commands, outputs, interpretation and images in `docs/images/`.
- Console print functions no longer feed the map panel; summary line for geometry now distinguishes failures (0) from elements drawn via parts (9).

### Phase 9 — Optional Plotly 3D view (2026-10-21)
- `geometry_processing.collect_display_meshes()`: re-meshes the selected categories with **opening subtraction enabled** (walls show door/window holes); elements without own geometry (curtain walls, parent stairs) are drawn from their decomposition parts, de-duplicated by GlobalId.
- `visualisation.build_3d_view()`: one Plotly `Mesh3d` per category (walls, columns, curtain walls, doors, stairs, lift parts, spaces hidden by default) with per-element hover (entity, name, GlobalId, parent); walkable room/corridor regions triangulated with `shapely.constrained_delaunay_triangles` at floor level; corridor centreline, room spokes, corridor snaps, room/doorway/lift nodes as `Scatter3d`. Equal-axis scaling (`aspectmode=data`), labelled as synthetic local frame. Legend toggles = element-type filter; `config.PLOT3D_CATEGORIES` / `PLOT3D_HIDDEN`; `--no-3d` skips it.
- Level 1: `output/floor_plan_3d.html`, ~8 s, 13.6 MB (Plotly JS from CDN). Meshes: 278 walls, 675 curtain-wall parts, 93 columns, 114 doors (6 curtain-wall doors drawn as curtain-wall parts), 21 stair parts, 36 lift parts, 95 spaces.
- Visible in 3D: a multi-storey glazed curtain wall contained in Level 1 (shown as modelled).

### Phase 6 — Door and lift connectors (2026-10-21)
- `network_generation.build_connectors()`: for each open doorway, one edge per adjacent region — `room_spoke` (doorway → room centre node) or `corridor_snap` (doorway → nearest point on that corridor's centreline, `shapely.ops.nearest_points`). Per connector: distance, snap limit, status (`ok` / `too_long` / `no_centreline`), length crossing non-walkable area. Per door: access point (doorway centroid), adjustment from the door footprint centre, in-walkable flag, overall status (`connected`, `connected_one_side`, `partial`, `unconnected`, `blocked_doorway`, `no_adjacent_region`).
- Snap limit per corridor region = max(3.0 m, max inscribed radius + 1.0 m): a fixed 3 m rejected 5 valid doors on the ~7 m-wide-from-centre atrium and split the network. Accepted snaps above 3 m are counted.
- Lifts: only `Elevator_LandingDoor*` proxies (label-only) not flagged by placement; footprint centroid → nearest region within 0.5 m → room node / corridor centreline. Misplaced ones: `excluded_placement_inconsistent`.
- Network connectivity: centrelines + accepted connectors grouped into components; doors per component.
- Level 1: 104 doors connected + 14 connected one side (external/single-region) + 2 blocked; 231 connectors (141 corridor snaps, mean 1.56 m, max 7.10 m; 90 room spokes), 0 too long, 5 crossing ≤ 0.34 m of non-walkable; lifts 9 connected, 7 excluded; **one network component holds all 118 connected doors** (13 other components are doorless corridor fragments). Level 2: 15 components with doors; Level 4: 47 (incomplete envelope).
- Map: connectors (spoke/snap/issue styles), doorway nodes split by status, lift access points (connected vs excluded). Output `connectors_summary.json`.

### Phase 5 revision — room nodes + corridor centrelines (2026-10-21)
- Requested: a centreline for the central corridor (not an `IfcSpace`) and no diagonal "X" skeletons in rectangular rooms.
- `network_generation.build_network()` (replaces `generate_centrelines`): walkable − door cuts → regions `R###` (by area); rooms (rectangularity ≥ 0.85, aspect ≤ 3) → centre node `R###.N` + spokes to open doorways; corridors → pygeoops centreline `R###.C#` (`densify 0.25`, `min_branch 2.0`, `simplify 0.1`). Doorway nodes record adjacent regions/kinds; doors without walkable doorway are `blocked`.
- Level 1: 139 regions (107 room-like, 32 corridors); central corridor = largest corridor region (~1,900 m²) with its own centreline; corridor centreline 1,339 m in 32 components (was 3,263 m of noisy skeleton); 68 room nodes with doors, 81 spokes (3 cross outside walkable — non-convex rooms, flagged); doors: 118 open (104 between two regions, 14 with one region e.g. external), 2 blocked; 48 regions without doors (39 room-like, 9 corridor-like: stall cubicles whose doors are proxies, façade gaps, shafts) shown as a separate grey layer. Runtime ~0.5 s.
- Map layers: network regions (rooms yellow / corridors blue), corridor centreline, room door spokes, room centre nodes, isolated room-like regions, doorway nodes. Output `network_summary.json` (replaces `centreline_summary.json`).

### Phase 5 — Centreline with pygeoops (2026-10-21)
- [ifc_indoor_network/network_generation.py](ifc_indoor_network/network_generation.py): one `pygeoops.centerline` call per opening-aware walkable component; connected line groups get IDs `<component>.C<k>`; per-component status `ok` / `fragmented` / `empty` / `failed` (exceptions reported, no substitute algorithm); doorway crossing check (centreline must extend ≥ 0.5 m beyond the door cut); length outside walkable polygon.
- Parameters fixed after benchmarking on Level 1: `densify_distance=0.25`, `min_branch_length=0.5`, `simplifytolerance=0.05`. pygeoops' automatic (width-scaled) defaults pruned doorway branches (72/120 crossed).
- Fixes found while validating: (1) curtain-wall footprints now use only parts spanning walking height — transoms above sliding doors had sealed the vestibule (status `no_parts_at_walking_height` for 2 curtain walls entirely above); (2) walkable components with mean width < 0.3 m removed and logged (`thin_removed`) — wall-gap strips; (3) doorway-crossing check ignores centrelines confined to the cut.
- Level 1: 51 components → 52 centreline components, 3,263 m, ~2 s, 0 outside walkable; 118/120 doorways crossed (the 2 not crossed are the known blocked external doors); 1 fragmented component (O000: a 0.38 m wide dead-end strip, 0.23 m from the main network). Level 2: 56/57 crossed; Level 4: 78/86 crossed (incomplete envelope).
- Map layer `Centreline (pygeoops)`; `output/centreline_summary.json`.

### Phase 4 — Floor footprint and walkable polygons (2026-10-21)
- `geometry_processing.build_floor_plan()`: envelope footprint (decision 6), naive and opening-aware walkable polygons, door cuts applied only to each door's explicit host (opening host via `IfcRelVoidsElement`, or parent curtain wall via `IfcRelAggregates`), only elements spanning walking height used as obstacles, `make_valid` + sliver removal (< 0.5 m²) logged, area-loss %, walkable components with IDs (`O000`… opening-aware, `N000`… naive, by descending area), doorways still blocked after cutting (with blocking element IDs), IfcSpace comparison.
- Map layers added: footprint, walkable naive, walkable opening-aware, door cuts. Summary panel moved to bottom-left (was covering layer control).
- `main.py` writes `output/floor_plan_summary.json`.
- Level 1: footprint 6,601 m² (1 component); naive walkable 6,233 m² / 144 components; opening-aware 6,255 m² / 60 components (largest 5,500 m² = 88%); door cuts 114 openings + 6 curtain-wall doors; 0 doors without cut; 2 doorways still blocked; spaces cover only 2,729 m² of the footprint. No geometry repairs needed.
- Level 2: 4,491 m² footprint (3 comp.), opening-aware 69 components. Level 4: footprint 1,801 m² in 9 components (incomplete envelope, see issues). Level 7A: 106 m².

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
- `main.py` prints the summary, cross-checks counts against the Phase 1 inventory and records the summary under `extraction` in `output/summary.json`.
- Level 1 result: 278 walls (272 internal, 6 external), 0 slabs, 95 spaces, 120 doors (114 with opening+host, 6 curtain-wall doors without), 233 openings (114 door, 66 window, 53 unfilled), 14 stairs (7 top-level), 105 obstacles (93 columns, 12 curtain walls), 38 lift label candidates (16 landing doors → 9 at floor level, 7 flagged at Z ≈ 6.0 m). Cross-check OK (also Levels 4 and 7).

### Phase 1 — Environment, IFC inspection, storey inventory (2026-10-21)
- Created project venv `.venv` (Python 3.11.9). Pinned in [ifc_indoor_network/requirements.txt](ifc_indoor_network/requirements.txt): ifcopenshell 0.9.0.post1, shapely 2.2.0, geopandas 1.2.0, pygeoops 0.6.0, folium 0.20.0, plotly 7.1.0, numpy 2.4.6, pytest 9.1.1.
- [ifc_indoor_network/config.py](ifc_indoor_network/config.py): IFC path, target storey, categories, lift keywords.
- [ifc_indoor_network/ifc_reader.py](ifc_indoor_network/ifc_reader.py): open model, units, buildings, storeys (sorted by elevation, metres + raw), entity counts (schema-aware), transitive storey membership via explicit relationships (contained_in / aggregated_by / nested_in / voids / fills) with recorded path, per-storey direct/indirect/unassigned counts, label/pset summaries, wall IsExternal summary, lift candidates (explicit vs label-only), georeferencing info, storey selection.
- [ifc_indoor_network/main.py](ifc_indoor_network/main.py): CLI `--ifc --storey --storey-id --list-storeys --output-dir`; writes `output/inventory.json`. Exit code 2 on bad path / failed selection.

## Tests
- `cd ifc_indoor_network && ../.venv/bin/python -m pytest -q` → **36 passed** (~19 s; includes sample-file integration tests, skipped if file absent).
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
- Envelope has sub-2 cm gaps between walls on Level 1 → 0.02 m closing needed (otherwise corridor/lobby lost).
- 2 Level 1 external doors (`1IjDNnD1b6tenKMZR0qFDY`, `1IjDNnD1b6tenKMZR0q8OV`) remain blocked: their opening voids one wall but an overlapping exterior brick wall (`...187264/187265`) is not voided. Reported, not fixed.
- Upper storeys (e.g. Level 4) have incomplete envelopes because multi-storey exterior walls are contained only in their base storey → fragmented footprint. Level 1 (and 2) are the reliable targets.
- Remaining opening-aware components on Level 1 (59 besides the main one) are rooms/shafts/closets without a door cut reaching the main area — to be examined with centreline/connectors.
- Centreline is a raw medial axis: diagonal "X" branches in rectangular rooms and saw-tooth spurs along façade mullions/columns. Resolved for rectangular rooms by the room-node revision; corridor centrelines still show some loops around islands (e.g. low partitions in the atrium). Loops around freestanding columns are kept by design.
- Room/corridor classification is geometric (rectangularity/aspect), not from IFC labels. Non-rectangular rooms (L-shapes) are treated as corridors and get a centreline.
- WC stall doors are `IfcBuildingElementProxy` (`10_Stall Door`), not `IfcDoor`, so stall cubicles are doorless regions.
- Level 2 network splits into 15 door-bearing components and Level 4 into 47 (incomplete envelopes / missing door relationships); Level 1 is fully connected.

## Decisions
- Reviewed after Phase 1: see "Decisions" in [PLAN.md](PLAN.md) (landing-door proxies as flagged lift access; curtain walls + columns as obstacles; wall section at walking height; git commit per phase).

## Next steps
1. Phase 8: consolidated `diagnostics.json` + console report covering SPEC §9. Phase 10: acceptance review against SPEC §13 and final summary.
