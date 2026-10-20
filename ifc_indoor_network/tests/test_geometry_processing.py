import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

import config
import geometry_processing as gp
import ifc_reader


def _box_mesh(x0, y0, z0, x1, y1, z1):
    v = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], dtype=float)
    faces = np.array([
        [0, 1, 3], [0, 3, 2], [4, 5, 7], [4, 7, 6],  # bottom, top
        [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6],  # vertical faces project to zero area
        [0, 2, 6], [0, 6, 4], [1, 3, 7], [1, 7, 5],
    ])
    return v, faces


def test_mesh_footprint_box():
    v, f = _box_mesh(0, 0, 0, 4, 0.2, 3)
    fp = gp.mesh_footprint(v, f, 0.001)
    assert fp.is_valid and fp.area == pytest.approx(0.8)


def test_mesh_footprint_empty():
    assert gp.mesh_footprint(np.zeros((0, 3)), np.zeros((0, 3), dtype=int), 0.001).is_empty


def test_polygonal_part_drops_lines():
    geom = GeometryCollection([Polygon([(0, 0), (1, 0), (1, 1)]), LineString([(0, 0), (5, 5)]), Point(3, 3)])
    assert gp.polygonal_part(geom).area == pytest.approx(0.5)


def test_door_axis_runs_along_long_side():
    axis = gp.door_axis(shapely.box(10, 0, 10.9, 0.2))
    assert axis.length == pytest.approx(0.9)
    assert axis.centroid.y == pytest.approx(0.1)


@pytest.mark.skipif(not config.IFC_PATH.is_file(), reason="sample IFC not present")
def test_sample_level1_geometry():
    model = ifc_reader.open_model(config.IFC_PATH)
    membership = ifc_reader.build_membership(model)
    storeys = ifc_reader.list_storeys(model)
    extracted = ifc_reader.extract_storey_elements(model, membership, storeys[0], storeys, config)
    geom = gp.extract_geometry(model, extracted, storeys[0].elevation_m, config)
    s = geom["summary"]
    assert s["status_by_category"]["walls"] == {"ok": 278}
    assert s["status_by_category"]["doors"] == {"ok": 120}
    assert s["status_by_category"]["spaces"] == {"ok": 95}
    # Door axes include ~0.15 m frame/trim; a feet/metre mix-up would be far off.
    assert s["door_width_check"]["median_axis_minus_overall_width_m"] == pytest.approx(0.152, abs=0.01)
    minx, miny, maxx, maxy = s["bounds_xy_m"]
    assert 50 < maxx - minx < 150 and 50 < maxy - miny < 150
    walls = geom["frames"]["walls"]
    assert walls.geometry.is_valid.all() and (walls["area_m2"] > 0).all()
