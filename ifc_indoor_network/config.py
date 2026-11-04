"""Settings a building owner may change for a different IFC model. Tuned internals live in heuristics.py.

--storey and --output-dir override the first two; --ifc is always required.
"""

from pathlib import Path

# --- Run ---
TARGET_STOREY = "Level 1"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# --- Model conventions: change if the model names or classifies things differently ---
# Fixed obstacles subtracted from the walkable area in addition to walls.
OBSTACLE_CATEGORIES = ["IfcCurtainWall", "IfcColumn"]
# Lifts: only elements whose Name matches LIFT_ACCESS_NAME_PATTERN become lift access points. Name/ObjectType/type-name
# keywords only *report* other possible lift objects (label-based candidates, never confirmed lifts).
LIFT_ACCESS_NAME_PATTERN = r"^Elevator_LandingDoor"
LIFT_LABEL_KEYWORDS = ["elevator", "lift"]

# --- What counts as walkable ---
# Height above the storey elevation used to decide whether columns, curtain walls and openings matter for walking.
WALKING_HEIGHT_M = 1.0
# True: every wall on the storey blocks walking whatever its height range (safe for split-level storeys, but bulkheads
# above / downstands below the floor also block). False: only walls spanning WALKING_HEIGHT_M block.
WALLS_BLOCK_AT_ANY_HEIGHT = True
# Corridor centrelines only run where the corridor is at least this wide (e.g. accessibility clear width); narrower
# corridors get no centreline (status "empty").
CORRIDOR_MIN_CLEAR_WIDTH_M = 1.0

# --- Model data quality ---
# Closes gaps between wall footprints when deriving the floor footprint; 0.02 m is the smallest value sealing the sample.
ENVELOPE_CLOSING_M = 0.02
MIN_COMPONENT_AREA_M2 = 0.5  # walkable components smaller than this are removed as slivers (logged)

# --- Room vs corridor: compact near-rectangular regions are rooms (one centre node), the rest are corridors ---
ROOM_MIN_RECTANGULARITY = 0.85  # region area / minimum rotated rectangle area
ROOM_MAX_ASPECT = 3.0  # long / short side of the minimum rotated rectangle

# --- Optional Plotly 3D view: categories meshed (openings subtracted) ---
PLOT3D_CATEGORIES = ["walls", "obstacles", "doors", "stairs", "lifts", "spaces"]
