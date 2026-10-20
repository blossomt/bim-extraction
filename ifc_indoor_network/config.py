"""User-editable defaults. All values can be overridden from the command line."""

from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IFC_PATH = PROJECT_ROOT / "data" / "20210219Architecture.ifc"
TARGET_STOREY = "Level 1"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# Categories counted in the inventory. Subtypes are included (IfcWall counts IfcWallStandardCase).
INVENTORY_CATEGORIES = [
    "IfcWall",
    "IfcWallStandardCase",
    "IfcSlab",
    "IfcSpace",
    "IfcDoor",
    "IfcStair",
    "IfcStairFlight",
    "IfcTransportElement",
    "IfcOpeningElement",
]

# Name/ObjectType/type-name keywords used to *report* possible lift objects that are not
# modelled as IfcTransportElement. These are label-based candidates, never confirmed lifts.
LIFT_LABEL_KEYWORDS = ["elevator", "lift"]

# Number of distinct labels (ObjectType, type names) listed per category in the inventory.
LABEL_SUMMARY_TOP_N = 15

# Fixed obstacles subtracted from the walkable area in addition to walls (reviewed decision).
OBSTACLE_CATEGORIES = ["IfcCurtainWall", "IfcColumn"]

# Label-only lift parts. Only names matching LIFT_ACCESS_NAME_PATTERN may become lift access points.
LIFT_ACCESS_NAME_PATTERN = r"^Elevator_LandingDoor"
LIFT_ROLE_PATTERNS = {"landing_door": r"landing_?door", "car_door": r"car_?door", "cabin": r"cabin"}

# Standard Pset_* sets are always kept; these vendor (Revit) sets are kept in addition, per category.
EXTRA_PSETS = {
    "IfcWall": ["Constraints"],
    "IfcDoor": ["Dimensions", "Constraints"],
    "IfcSpace": ["Dimensions", "Identity Data"],
    "IfcStair": ["Constraints"],
}

# Elements whose placement Z lies outside [elevation - tol, next storey elevation - tol) are flagged (report only).
PLACEMENT_Z_TOLERANCE_M = 0.5

# Height above the storey elevation used to decide whether walls/openings matter for walking.
WALKING_HEIGHT_M = 1.0
# Snapping grid for 2D unions (metres). Small enough not to close doorways.
GEOMETRY_GRID_SIZE_M = 0.001
GEOMETRY_THREADS = os.cpu_count() or 1
# Door footprint axis minus IFC OverallWidth: deviations from the median offset above this are reported.
DOOR_WIDTH_CHECK_TOLERANCE_M = 0.15

# Floor footprint = filled outer rings of wall + curtain-wall footprints (reviewed decision: rooms are unreliable).
# Morphological closing used ONLY to seal sub-centimetre envelope gaps; 0.02 m is the smallest value sealing Level 1.
ENVELOPE_CLOSING_M = 0.02
# Walkable components smaller than this are removed as slivers (logged).
MIN_COMPONENT_AREA_M2 = 0.5
# Walkable components whose mean width (2 * area / perimeter) is below this are removed (wall-gap strips; logged).
MIN_COMPONENT_MEAN_WIDTH_M = 0.3
# Doors aggregated in a curtain wall (no IfcOpeningElement) cut their own footprint from that curtain wall.
CUT_CURTAIN_WALL_DOORS = True
# Doorway reported as still blocked if more than this fraction of its cut area remains covered by obstacles.
DOORWAY_BLOCKED_FRACTION = 0.5

# Network: the walkable polygon is split at door cuts into regions. Compact near-rectangular regions are
# "rooms" (one centre node + straight spokes to their doors); all other regions are "corridors" (pygeoops centreline).
CENTRELINE_VARIANT = "opening_aware"
ROOM_MIN_RECTANGULARITY = 0.85  # region area / minimum rotated rectangle area
ROOM_MAX_ASPECT = 3.0  # long / short side of the minimum rotated rectangle
# pygeoops.centerline parameters for corridor regions (metres). Doorways are not part of corridor regions,
# so branches can be pruned harder than before without disconnecting doors.
CORRIDOR_DENSIFY_M = 0.25
CORRIDOR_MIN_BRANCH_M = 2.0
CORRIDOR_SIMPLIFY_M = 0.1
# A region is adjacent to a door if it lies within this distance of the door cut.
DOOR_ADJACENCY_TOL_M = 0.05

# Connectors: doorway/lift access point -> nearest corridor centreline point. Longer connectors are flagged, not accepted.
# Limit per corridor region = max(MAX_SNAP_DISTANCE_M, region's max inscribed radius + SNAP_WIDTH_MARGIN_M):
# in wide lobbies the centreline is legitimately far from the walls.
MAX_SNAP_DISTANCE_M = 3.0
SNAP_WIDTH_MARGIN_M = 1.0
# Lift landing-door footprints are linked to walkable regions within this distance.
LIFT_ADJACENCY_TOL_M = 0.5

# Optional Plotly 3D view: Phase 2 categories meshed (openings subtracted). Spaces start hidden in the legend.
PLOT3D_CATEGORIES = ["walls", "obstacles", "doors", "stairs", "lifts", "spaces"]
PLOT3D_HIDDEN = ["spaces"]
# Decomposition parts not drawn in 3D (stair railings were ~60% of all vertices on Level 1).
PLOT3D_SKIP_PART_ENTITIES = ["IfcRailing"]
