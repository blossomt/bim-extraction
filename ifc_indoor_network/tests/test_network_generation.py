import geopandas as gpd
import pytest
import shapely
from shapely.geometry import LineString

import config
import network_generation as ng


def _components(*polys):
    rows = [{"component_id": f"O{i:03d}", "geometry": p} for i, p in enumerate(polys)]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=None)


def _cuts(*rows):
    return gpd.GeoDataFrame([{"door_global_id": d, "geometry": g} for d, g in rows] or {"geometry": []}, geometry="geometry", crs=None)


def test_connected_groups():
    lines = [LineString([(0, 0), (1, 0)]), LineString([(1, 0), (2, 0)]), LineString([(5, 5), (6, 5)])]
    groups = ng.connected_groups(lines)
    assert len(groups) == 2 and groups[0].length == pytest.approx(2)


def _plan():
    # Room (4x4) -- doorway (1 m wide, 0.2 m deep) -- L-shaped corridor.
    room = shapely.box(0, 0, 4, 4)
    door = shapely.box(4, 1.5, 4.2, 2.5)
    corridor = shapely.union_all([shapely.box(4.2, 0, 6.2, 12), shapely.box(4.2, 10, 20, 12)])
    return shapely.union_all([room, door, corridor]), door


def test_label_regions_uses_overlapping_ifc_space_label():
    regions = gpd.GeoDataFrame([
        {"region_id": "R000", "geometry": shapely.box(0, 0, 4, 4)},
        {"region_id": "R001", "geometry": shapely.box(10, 0, 14, 4)},  # outside every IfcSpace
        {"region_id": "R002", "geometry": shapely.box(20, 0, 30, 4)},  # < 50 % inside the space
    ], geometry="geometry", crs=None)
    spaces = gpd.GeoDataFrame([
        {"global_id": "S1", "geometry": shapely.box(-0.1, -0.1, 4.1, 4.1)},
        {"global_id": "S2", "geometry": shapely.box(20, 0, 22, 4)},
    ], geometry="geometry", crs=None)
    records = [{"global_id": "S1", "name": "1", "long_name": "OFFICE"}, {"global_id": "S2", "name": "2", "long_name": None}]
    assert ng.label_regions(regions, spaces, records) == {"R000": "OFFICE"}


def test_rectangle_metrics():
    rect, aspect = ng.rectangle_metrics(shapely.box(0, 0, 4, 2))
    assert rect == pytest.approx(1) and aspect == pytest.approx(2)


def test_corner_spurs_pruned_to_single_axis():
    # Hand-built "T" of centrelines: a long axis with two short corner forks at one end.
    corridor = shapely.box(0, 0, 30, 3)
    axis = LineString([(1.5, 1.5), (28.5, 1.5)])
    forks = [LineString([(28.5, 1.5), (30, 0)]), LineString([(28.5, 1.5), (30, 3)])]
    pruned = ng.prune_corner_spurs(shapely.MultiLineString([axis, *forks]), corridor, 1.5)
    assert pruned.geom_type == "LineString" and pruned.length == pytest.approx(27)


def test_wide_hall_spurs_are_kept():
    hall = shapely.box(0, 0, 30, 12)
    lines = shapely.MultiLineString([LineString([(6, 6), (24, 6)]), LineString([(24, 6), (27, 2)])])
    assert ng.prune_corner_spurs(lines, hall, 1.5, max_half_width=4.0).length == pytest.approx(18 + 5)


def test_long_branch_is_not_pruned():
    corridor = shapely.union_all([shapely.box(0, 0, 30, 3), shapely.box(14, 3, 17, 20)])
    lines = shapely.MultiLineString([LineString([(1.5, 1.5), (28.5, 1.5)]), LineString([(15.5, 1.5), (15.5, 19)])])
    pruned = ng.prune_corner_spurs(lines, corridor, 1.5)
    assert pruned.length == pytest.approx(27 + 17.5)


def test_room_gets_centre_node_and_corridor_gets_centreline():
    walkable, door = _plan()
    result = ng.build_network(_components(walkable), _cuts(("D1", door)), config)
    s = result["summary"]
    assert s["regions"] == {"room": 1, "corridor": 1}
    nodes = result["room_nodes"]
    assert nodes.geometry.iloc[0].equals_exact(shapely.Point(2, 2), 1e-9)
    assert len(result["centrelines"]) == 1 and s["corridor_status"] == {"ok": 1}
    assert s["doors_by_status"] == {"open": 1} and s["doors_by_adjacent_region_count"] == {2: 1}
    assert s["regions_without_doors"] == []

    conn = ng.build_connectors(result, _cuts(), _cuts(), [], config)
    kinds = sorted(conn["connectors"]["kind"])
    assert kinds == ["corridor_snap", "room_spoke"]
    assert (conn["connectors"]["status"] == "ok").all()
    assert conn["summary"]["doors_by_connection_status"] == {"connected": 1}
    assert conn["summary"]["network_components"] == 1


def test_blocked_door_gets_no_spoke():
    room = shapely.box(0, 0, 4, 4)
    cut_outside = shapely.box(10, 10, 11, 10.2)
    result = ng.build_network(_components(room), _cuts(("D1", cut_outside)), config)
    assert result["summary"]["doors_by_status"] == {"blocked": 1}
    assert result["summary"]["regions_without_doors"] == ["R000"]
    conn = ng.build_connectors(result, _cuts(), _cuts(), [], config)
    assert len(conn["connectors"]) == 0
    assert conn["summary"]["doors_by_connection_status"] == {"blocked_doorway": 1}


def _lift(gid, misplaced, access=True):
    return {"global_id": gid, "name": "Elevator_LandingDoor2L:x", "identification": "label_only",
            "is_access_point_candidate": access, "placement_z_outside_storey": misplaced, "access_rule": "r"}


def test_lift_landing_doors_connected_or_excluded():
    walkable, door = _plan()
    net = ng.build_network(_components(walkable), _cuts(), config)
    landing = shapely.box(10, 11.8, 11, 12.0)
    lifts = gpd.GeoDataFrame([{"global_id": g, "geometry": landing} for g in ("L1", "L2", "L3")], geometry="geometry", crs=None)
    conn = ng.build_connectors(net, _cuts(), lifts, [_lift("L1", False), _lift("L2", True), _lift("L3", False, access=False)], config)
    assert conn["summary"]["lifts"]["by_connection_status"] == {"connected": 1, "excluded_placement_inconsistent": 1}
    lift_conn = conn["connectors"][conn["connectors"]["source"] == "lift_landing_door"]
    assert lift_conn["source_global_id"].tolist() == ["L1"] and lift_conn["identification"].iloc[0] == "label_only"


def test_long_corridor_snap_is_flagged(monkeypatch):
    monkeypatch.setattr(config, "MAX_SNAP_DISTANCE_M", 0.1)
    monkeypatch.setattr(config, "SNAP_WIDTH_MARGIN_M", -10.0)
    walkable, door = _plan()
    net = ng.build_network(_components(walkable), _cuts(("D1", door)), config)
    conn = ng.build_connectors(net, _cuts(), _cuts(), [], config)
    assert conn["summary"]["connectors_too_long"]
    assert conn["summary"]["doors_by_connection_status"] == {"partial": 1}


def test_corridor_failure_is_reported(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("bad geometry")
    monkeypatch.setattr(ng.pygeoops, "centerline", boom)
    walkable, door = _plan()
    result = ng.build_network(_components(walkable), _cuts(("D1", door)), config)
    failed = result["summary"]["corridors_failed_or_empty"]
    assert failed[0]["status"] == "failed" and "bad geometry" in failed[0]["error"]
    assert len(result["centrelines"]) == 0
