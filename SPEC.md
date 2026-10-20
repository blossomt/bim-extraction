# Task: Prototype IFC-to-Indoor-Walking-Network Extraction for a Selected Floor

## Objective

Build a Python prototype that reads an IFC BIM file, selects a particular building floor using its `IfcBuildingStorey` label, extracts the floor layout including internal walls, generates a simplified walking-network centreline using `pygeoops`, and identifies/connects doors and lifts associated with that floor.

The goal is to evaluate how well a reasonably well-authored IFC model supports approximate indoor routing and travel-time estimation using **IFC labels, properties and explicit relationships only**.

This is an exploratory prototype, not a production-ready indoor routing engine. Prioritise transparency, debuggability, and visual inspection over perfect geometry or topology.

## 1. Development requirements

- Use Python in a VS Code environment.
- Inspect the existing project structure before making changes. Reuse existing conventions and dependencies where sensible.
- If an IFC file is already present, use it. Otherwise, make the IFC input path configurable.
- Use `ifcopenshell` for IFC parsing and geometry extraction.
- Use `shapely` and `geopandas` for 2D geometry operations.
- Use `pygeoops` for centreline generation.
- Use `folium` for an interactive 2D visualisation. Investigate a lightweight 3D alternative, such as Plotly, if the extracted geometry supports it and implementation effort is reasonable.
- Keep the code modular, with separate functions for IFC inspection, element extraction, geometry conversion, floor-plan processing, network generation and visualisation.
- Do not assume specific IFC entities, property sets or relationships are present. Inspect the model and report what is available.
- Avoid unnecessary frameworks or infrastructure. This should be runnable locally from VS Code.

First inspect the available files and environment, then implement the prototype. Do not stop after providing a design or code snippets; create runnable code and a short README with execution instructions.

## 2. IFC inspection and floor selection

Read the IFC file and produce an inventory of:

- IFC schema version.
- Buildings and `IfcBuildingStorey` objects.
- Storey names, GlobalIds and elevations where available.
- Counts of `IfcWall`, `IfcWallStandardCase` where supported, `IfcSlab`, `IfcSpace`, `IfcDoor`, `IfcStair`, `IfcStairFlight`, `IfcTransportElement` and `IfcOpeningElement`.
- Available element names, types, predefined types, property sets and spatial containment relationships.

Allow the user to select a floor by an exact or case-insensitive match against `IfcBuildingStorey.Name`. Make the target storey name configurable at the top of the script or through command-line arguments.

Display the matching storey and its associated element counts before processing. If multiple storeys match, show the candidates and require an unambiguous selection. If no storey matches, list the available storey names and exit with a helpful error.

For this first version, use explicit IFC storey containment and available relationships as the source of floor membership. Do not implement geometric floor reassignment or complex cross-level inference yet.

For elements whose spatial containment is missing, report them as unassigned rather than silently assigning them to the selected floor. Handle elements that reference or span multiple storeys transparently.

## 3. Geometry extraction

Use IfcOpenShell's geometry processing to extract the selected elements' shapes, respecting object placements, nested placements, model units and coordinate transformations.

Convert relevant 3D geometry into 2D Shapely geometries for the selected floor.

Extract the following categories separately:

### A. Walls

Extract wall footprints or projected wall geometry for the selected storey.

- Preserve individual wall IDs and names.
- Where practical, distinguish internal and external walls using available IFC labels or properties.
- Do not assume every wall is internal.
- If wall classification is missing, retain it as an unknown category rather than guessing.
- Preserve enough information to inspect individual wall footprints and their relationship to the overall floor layout.

### B. Floor slabs

Extract `IfcSlab` objects associated with the selected floor.

Use these as a visual reference for the floor footprint and potential walkable area. Inspect available predefined types, since not all slabs are floor surfaces.

Do not automatically assume the union of all slab geometry is the exact walkable area. If the model has multiple slabs, openings or irregular geometry, retain and visualise them separately.

### C. Doors

Extract `IfcDoor` objects associated with the selected floor.

For each door, record:
- GlobalId and name.
- Object type and predefined type, if available.
- Geometry and representative 2D point or line.
- Storey association.
- Available properties and relevant relationships.

Where available, inspect `IfcRelFillsElement` and `IfcRelVoidsElement` relationships to identify the opening and host wall. Preserve these relationships in the output.

Do not infer missing door-to-wall relationships in this first version.

### D. Lifts

Inspect `IfcTransportElement` and related type objects for elevator/lift classifications.

Extract the lift's geometry, GlobalId, name, available properties, spatial containment and relevant aggregation relationships.

Also extract any `IfcDoor` objects explicitly associated with the selected floor. If the IFC model contains no explicit lift-floor or lift-door relationship, report that limitation rather than inferring a lift entrance from geometry.

### E. Stairs (optional, but useful)

Extract `IfcStair` and `IfcStairFlight` objects associated with the selected storey. Display them for context, but do not implement complex stair-landing or cross-floor connections yet.

## 4. Generate the floor-plan polygon

Create a simplified polygon representing the selected floor layout while accounting for internal walls.

Use the following preferred approach:

1. If usable `IfcSpace` geometries exist, extract and visualise them as individual room/space polygons.
2. Otherwise, use the extracted floor slab geometry as the initial floor footprint.
3. If neither provides a usable footprint, report that the floor polygon cannot be generated reliably from the available model.

For the initial implementation, derive an approximate walkable polygon by subtracting wall footprints and other clearly identified fixed obstacles from the floor footprint.

Use Shapely operations such as `unary_union`, `difference`, `make_valid` or other appropriate geometry repair functions as necessary.

Handle polygon holes, multipolygons, invalid geometries and small sliver polygons carefully. Log geometry repairs and any area lost during processing.

Use a configurable tolerance for geometric operations. Avoid aggressive buffering or simplification that could eliminate doors, narrow corridors or other valid passages.

**Important:** subtracting full wall footprints can close doorways or openings. If the IFC model explicitly relates openings to host walls, try to preserve those openings in the obstacle geometry. If this is not reliable, make the limitation visible and provide an option to generate both:
- A naive walkable polygon with full wall footprints subtracted.
- A version that accounts for explicitly modelled wall openings where feasible.

Do not silently invent openings for the first version.

Visualise the original floor footprint, wall footprints and resulting walkable polygon as separate layers.

## 5. Generate a centreline using pygeoops

Use `pygeoops` to generate centreline geometry from the resulting walkable polygon or polygons.

Consult the installed version's documentation and function signatures rather than guessing the API. Check how it handles polygon holes, multipolygons and narrow corridors.

Requirements:

- Support multiple disconnected walkable polygons.
- Preserve separate centreline components.
- Allow relevant simplification or tolerance parameters to be configured.
- Report failures and empty centreline outputs.
- Visualise the generated centreline over the walkable polygon.
- Preserve a stable identifier for each resulting line component.

Do not assume that the centreline is automatically routable or that it passes through all doors. Treat it as an approximate geometric skeleton that will need connectivity checks.

If `pygeoops` cannot produce a useful result for the selected geometry, report why and provide a small diagnostic output. Do not silently replace it with a different algorithm.

## 6. Connect doors and lifts to the centreline

For each explicitly extracted door:

1. Derive a representative access point from the door geometry, preferably using the centre of the opening or door geometry.
2. Find the nearest point on the generated centreline.
3. Calculate the connector distance.
4. Create a connector line between the door access point and the nearest centreline point.
5. Record whether the connection is within a configurable maximum snap distance.

Store:
- Door GlobalId and name.
- Original access point.
- Snapped centreline point.
- Connector geometry.
- Connector distance.
- Connection status.

Use Shapely's nearest-point functionality or an equivalent method.

Do not automatically accept arbitrarily long connectors. Flag them for inspection.

For lifts, attempt to connect the lift's floor-level access point to the centreline only if an explicit lift door or other usable access geometry is available. Do not simply snap the centre of the lift shaft to the centreline and treat it as a valid entrance.

If a door is on a wall but its representative point does not lie within the walkable polygon, show the original point and connector separately. Do not move the original geometry without recording the adjustment.

For this first version, connectivity can be approximate. The objective is to assess whether the extracted model produces a plausible network, not to solve all graph-topology issues.

## 7. Interactive 2D visualisation with Folium

Create an interactive HTML map that can be opened in a browser from VS Code.

The visualisation should include toggleable layers for:

1. Original floor slab or footprint.
2. `IfcSpace` polygons, if available.
3. Internal and external wall footprints, using distinct styles where classification is known.
4. Walkable polygon after obstacle subtraction.
5. Centreline geometry.
6. Doors and their labels.
7. Door-to-centreline connectors.
8. Lift objects and identified lift access points.
9. Stairs and stair flights, if extracted.
10. Elements with missing floor assignments or failed geometry extraction, where they can be displayed.

Clicking an element should show its IFC GlobalId, name, entity type, storey, relevant properties, and extraction or connection status.

Display a legend, layer controls, the selected storey name and a brief summary of the processing results.

IFC models may use local Cartesian coordinates rather than longitude/latitude. Do not pass arbitrary local XY coordinates to Folium as if they were geographic coordinates.

For the Folium visualisation, choose one of these approaches:

- If a reliable georeferencing transformation exists, transform the data to a suitable geographic coordinate reference system.
- Otherwise, create a local-coordinate visualisation with an appropriate transformation to a synthetic display coordinate system, or use a local-coordinate viewer instead.

Clearly label any synthetic coordinates and do not imply that they represent real-world locations.

If Folium's geographic map framework makes local-coordinate display awkward, prioritise correct geometry display over forcing the model onto an actual basemap.

## 8. Optional 3D visualisation

If feasible without significantly complicating the prototype, create an interactive Plotly 3D view.

Display:
- Wall meshes or extruded wall footprints.
- Floor slabs or walkable polygons at their respective elevations.
- Doors as markers or simplified geometry.
- Lift shafts and stairs where available.
- The generated centreline positioned at the relevant floor elevation.

Allow rotation, zooming and hover inspection of object IDs and labels.

Use consistent units and world-space placements. Avoid rendering every IFC object if this makes the visualisation excessively slow; allow filtering to selected element types.

The 3D view is supplementary. The 2D floor-plan output and extracted centreline are the primary deliverables.

## 9. Diagnostics and quality checks

Produce a concise report, both in the console and as a JSON file, covering:

- Selected storey name, GlobalId and elevation.
- IFC schema version and units.
- Number of elements found, extracted and skipped by category.
- Number of walls with missing classification.
- Number of elements without explicit storey containment.
- Availability of `IfcSpace` geometry.
- Floor footprint source: spaces, slabs or unavailable.
- Walkable polygon area before and after obstacle subtraction.
- Number of polygon components and centreline components.
- Number of doors extracted and successfully connected.
- Number of connectors exceeding the configured maximum distance.
- Number of lifts identified and number with explicit floor-level access geometry.
- Geometry failures, invalid geometries and warnings.

Include a few useful diagnostics, such as doors that cannot be connected, centreline components that have no connected doors, and walkable areas that become disconnected after wall subtraction.

Do not treat an extraction failure as an empty feature class without explaining why.

## 10. Suggested project structure

Use an organisation similar to:

```text
ifc_indoor_network/
├── main.py
├── config.py
├── ifc_reader.py
├── geometry_processing.py
├── network_generation.py
├── visualisation.py
├── diagnostics.py
├── requirements.txt
├── README.md
└── output/
    ├── diagnostics.json
    ├── floor_plan.html
    └── floor_plan_3d.html
```

Adapt this structure to the existing repository if one already exists.

Avoid duplicating extracted geometry across modules unnecessarily. Keep a clear separation between IFC objects, spatial features and derived network features.

## 11. Dependencies and execution

Use compatible versions of:
- `ifcopenshell`
- `shapely`
- `geopandas`
- `pygeoops`
- `folium`
- `plotly` (optional)

Pin dependencies where appropriate after checking compatibility with the current Python environment.

Provide a README that explains:
- How to configure the IFC path and target storey label.
- How to create the environment and install dependencies.
- How to run the script.
- Where the generated HTML and diagnostics are saved.
- How to interpret missing relationships and geometry failures.

The script should be runnable with a straightforward command, for example:

`python main.py --ifc /path/to/building.ifc --storey "Level 1"`

Adjust the actual command-line interface as needed.

## 12. Scope boundaries

For this initial prototype, do not implement:
- Geometric floor assignment for elements with missing containment.
- Automatic reconstruction of rooms when `IfcSpace` is absent.
- Complex inference of stair landings or lift-served floors.
- Full 3D pathfinding.
- Accessibility compliance checking.
- Travel-time modelling.
- Automatic correction of arbitrary BIM modelling errors.
- A production routing service or database backend.

Keep the code extensible so these can be added later.

## 13. Acceptance criteria

The prototype is complete when:

1. It can inspect an IFC file and list available storeys.
2. It can select one storey using its label.
3. It extracts and displays walls, slabs/spaces, doors, and available lift/stair objects associated with that storey.
4. It generates an approximate walkable polygon while accounting for wall geometry.
5. It uses `pygeoops` to generate a centreline from that polygon.
6. It creates approximate connectors between doors and the centreline, reporting snap distances and failures.
7. It produces a usable interactive HTML visualisation and machine-readable diagnostics.
8. It does not fabricate geographic coordinates or silently invent missing relationships.
9. It handles common geometry and missing-data problems gracefully.
10. The README contains working setup and execution instructions.

At the end, summarise what was implemented, the assumptions made, any failures encountered, and the next three improvements that would most increase the quality of the extracted walking network.

Start by inspecting the available IFC file and Python environment. Then implement and run the prototype against the available model, fixing errors until the acceptance criteria are met where the input data permits.