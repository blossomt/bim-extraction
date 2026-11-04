"""Tuned constants. Validated on the sample and several other models; not intended to be edited per model (see config.py)."""

import os

# --- Corridor centreline (pygeoops.centerline + post-processing), metres ---
CORRIDOR_DENSIFY_M = 0.25
CORRIDOR_MIN_BRANCH_M = 2.0
CORRIDOR_SIMPLIFY_M = 0.1
# Leaf branches shorter than this x the local half-width (junction to corridor boundary) are corner/end forks: pruned.
CORRIDOR_SPUR_HALF_WIDTH_FACTOR = 1.5
# Only junctions in corridors up to this half-width are pruned; wide halls keep their branches to reach side doors.
CORRIDOR_SPUR_MAX_HALF_WIDTH_M = 4.0
# Centreline loops enclosing less than this area (tight loops around columns/posts) collapse to one path.
CORRIDOR_LOOP_MAX_AREA_M2 = 20.0
# Leaf branches running alongside another branch within this distance (fans in open spaces) are removed; 0 disables.
CORRIDOR_PARALLEL_OFFSET_M = 1.5

# --- 2D geometry ---
GEOMETRY_GRID_SIZE_M = 0.001  # snapping grid for unions; small enough not to close doorways
GEOMETRY_THREADS = os.cpu_count() or 1
MIN_COMPONENT_MEAN_WIDTH_M = 0.3  # walkable components with mean width (2 * area / perimeter) below this are removed
DOORWAY_BLOCKED_FRACTION = 0.5  # doorway reported as blocked if more than this fraction of its cut remains covered
DOOR_ADJACENCY_TOL_M = 0.05  # a region is adjacent to a door if within this distance of the door cut
DOOR_WIDTH_CHECK_TOLERANCE_M = 0.15  # door axis minus OverallWidth: deviations from the median above this are reported
CUT_CURTAIN_WALL_DOORS = True  # doors aggregated in a curtain wall (no opening element) cut their own footprint
# Floor footprint envelope = walls plus these entities spanning walking height (facade walls often stop at columns).
ENVELOPE_OBSTACLE_ENTITIES = ["IfcCurtainWall", "IfcColumn"]

# --- Connectors ---
# Corridor snap limit = max(MAX_SNAP_DISTANCE_M, region max inscribed radius + SNAP_WIDTH_MARGIN_M); longer is flagged.
MAX_SNAP_DISTANCE_M = 3.0
SNAP_WIDTH_MARGIN_M = 1.0
LIFT_ADJACENCY_TOL_M = 0.5  # lift landing-door footprints link to walkable regions within this distance

# --- IFC reading and reporting (Revit conventions) ---
PLACEMENT_Z_TOLERANCE_M = 0.5  # placement Z outside [elevation - tol, next storey elevation - tol) is flagged (report only)
INVENTORY_CATEGORIES = [  # counted in the inventory; subtypes included (IfcWall counts IfcWallStandardCase)
    "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcSpace", "IfcDoor", "IfcStair", "IfcStairFlight",
    "IfcTransportElement", "IfcOpeningElement",
]
LABEL_SUMMARY_TOP_N = 15  # distinct labels (ObjectType, type names) listed per category in the inventory
LIFT_ROLE_PATTERNS = {"landing_door": r"landing_?door", "car_door": r"car_?door", "cabin": r"cabin"}
# Standard Pset_* sets are always kept; these Revit sets are kept in addition, per category.
EXTRA_PSETS = {
    "IfcWall": ["Constraints"],
    "IfcDoor": ["Dimensions", "Constraints"],
    "IfcSpace": ["Dimensions", "Identity Data"],
    "IfcStair": ["Constraints"],
}

# --- 3D view ---
PLOT3D_HIDDEN = ["spaces"]  # start hidden in the legend
PLOT3D_SKIP_PART_ENTITIES = ["IfcRailing"]  # decomposition parts not drawn (stair railings were ~60% of all vertices)
