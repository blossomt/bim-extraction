import geopandas as gpd
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


def test_fill_outer_rings_fills_rooms():
    ring_wall = shapely.box(0, 0, 10, 10).difference(shapely.box(0.2, 0.2, 9.8, 9.8))
    assert gp.fill_outer_rings(ring_wall).area == pytest.approx(100)


def test_clean_polygons_drops_slivers():
    geom = shapely.union_all([shapely.box(0, 0, 5, 5), shapely.box(10, 10, 10.1, 10.1)])
    cleaned, log = gp.clean_polygons(geom, 0.5)
    assert cleaned.area == pytest.approx(25) and log["slivers_removed"] == 1


def _frames(walls, openings=(), doors=()):
    def frame(rows):
        rows = [{"global_id": gid, "entity": ent, "covers_walking_height": True, "geometry": g} for gid, ent, g in rows]
        return gpd.GeoDataFrame(rows or {"geometry": []}, geometry="geometry", crs=None)
    return {
        "walls": frame(walls), "obstacles": frame([]), "openings": frame(openings), "doors": frame(doors),
        "slabs": frame([]), "spaces": frame([]),
    }


def test_floor_plan_only_door_openings_open_walls():
    # Square room with one door opening and one window opening in the wall's west side.
    room = shapely.box(0, 0, 10, 10).difference(shapely.box(0.2, 0.2, 9.8, 9.8))
    corridor_wall = shapely.box(5, 0.2, 5.2, 9.8)  # splits the room in two
    door_op = shapely.box(4.9, 2, 5.3, 3)
    window_op = shapely.box(4.9, 6, 5.3, 7)
    frames = _frames(
        walls=[("W1", "IfcWall", room), ("W2", "IfcWall", corridor_wall)],
        openings=[("O1", "IfcOpeningElement", door_op), ("O2", "IfcOpeningElement", window_op)],
        doors=[("D1", "IfcDoor", door_op)],
    )
    extracted = {"elements": {
        "slabs": [],
        "openings": [{"global_id": "O1", "filling_kind": "door"}, {"global_id": "O2", "filling_kind": "window"}],
        "doors": [{"global_id": "D1", "name": "d", "opening": {"global_id": "O1"}, "host_element": {"global_id": "W2"},
                   "aggregated_in": None, "host_relationship_status": "opening_and_host"}],
    }}
    plan = gp.build_floor_plan(frames, extracted, config)
    s = plan["summary"]
    assert s["footprint_source"] == "derived_from_wall_envelope"
    assert s["walkable"]["naive"]["components"] == 2
    assert s["walkable"]["opening_aware"]["components"] == 1
    assert s["door_cuts"] == {"door_filled_opening": 1}
    assert s["doorways_still_blocked"] == []


def test_floor_plan_unavailable_without_walls_or_slabs():
    frames = _frames(walls=[])
    plan = gp.build_floor_plan(frames, {"elements": {"slabs": [], "openings": [], "doors": []}}, config)
    assert plan["summary"]["footprint_source"] == "unavailable"
    assert plan["summary"]["footprint_unavailable_reason"]


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

    plan = gp.build_floor_plan(geom["frames"], extracted, config)["summary"]
    assert plan["footprint_source"] == "derived_from_wall_envelope"
    assert plan["footprint"]["components"] == 1 and plan["footprint"]["area_m2"] > 6000
    assert plan["door_cuts"] == {"door_filled_opening": 114, "door_in_curtain_wall": 6}
    assert plan["walkable"]["opening_aware"]["components"] < plan["walkable"]["naive"]["components"]
