import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def sample_ifc_path():
    path = Path(__file__).resolve().parents[1] / "data" / "20210219Architecture.ifc"
    if not path.is_file():
        pytest.skip(f"Sample IFC not present: {path}")
    return path
