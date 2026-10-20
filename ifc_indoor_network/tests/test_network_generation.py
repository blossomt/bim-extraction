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


def test_rectangle_metrics():
    rect, aspect = ng.rectangle_metrics(shapely.box(0, 0, 4, 2))
    assert rect == pytest.approx(1) and aspect == pytest.approx(2)


def test_room_gets_centre_node_and_corridor_gets_centreline():
    walkable, door = _plan()
    result = ng.build_network(_components(walkable), _cuts(("D1", door)), config)
    s = result["summary"]
    assert s["regions"] == {"room": 1, "corridor": 1}
    nodes = result["room_nodes"]
    assert nodes.geometry.iloc[0].equals_exact(shapely.Point(2, 2), 1e-9)
    assert len(result["centrelines"]) == 1 and s["corridor_status"] == {"ok": 1}
    assert s["doors_by_status"] == {"open": 1} and s["doors_by_adjacent_region_count"] == {2: 1}
    spoke = result["spokes"].iloc[0]
    assert spoke["door_global_id"] == "D1" and spoke["leaves_walkable_m"] == 0
    assert s["regions_without_doors"] == []


def test_blocked_door_gets_no_spoke():
    room = shapely.box(0, 0, 4, 4)
    cut_outside = shapely.box(10, 10, 11, 10.2)
    result = ng.build_network(_components(room), _cuts(("D1", cut_outside)), config)
    assert result["summary"]["doors_by_status"] == {"blocked": 1}
    assert len(result["spokes"]) == 0
    assert result["summary"]["regions_without_doors"] == ["R000"]


def test_corridor_failure_is_reported(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("bad geometry")
    monkeypatch.setattr(ng.pygeoops, "centerline", boom)
    walkable, door = _plan()
    result = ng.build_network(_components(walkable), _cuts(("D1", door)), config)
    failed = result["summary"]["corridors_failed_or_empty"]
    assert failed[0]["status"] == "failed" and "bad geometry" in failed[0]["error"]
    assert len(result["centrelines"]) == 0
