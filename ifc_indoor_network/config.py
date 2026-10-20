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
# Doors aggregated in a curtain wall (no IfcOpeningElement) cut their own footprint from that curtain wall.
CUT_CURTAIN_WALL_DOORS = True
# Doorway reported as still blocked if more than this fraction of its cut area remains covered by obstacles.
DOORWAY_BLOCKED_FRACTION = 0.5
