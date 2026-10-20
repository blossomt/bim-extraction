"""User-editable defaults. All values can be overridden from the command line."""

from pathlib import Path

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
