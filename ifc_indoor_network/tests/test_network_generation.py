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


def test_two_rooms_through_doorway():
    # Two 4x4 rooms joined by a 1 m wide, 0.2 m deep doorway.
    rooms = shapely.union_all([shapely.box(0, 0, 4, 4), shapely.box(4.2, 0, 8.2, 4), shapely.box(4, 1.5, 4.2, 2.5)])
    door = shapely.box(4, 1.5, 4.2, 2.5)
    result = ng.generate_centrelines(_components(rooms), _cuts(("D1", door)), config)
    s = result["summary"]
    assert s["component_status"] == {"ok": 1}
    assert s["doorways_crossed"] == 1 and s["doorways_not_crossed"] == []
    assert s["length_outside_walkable_m"] == pytest.approx(0, abs=1e-6)
    assert result["centrelines"]["centreline_id"].tolist() == ["O000.C0"]


def test_isolated_doorway_strip_is_not_crossed():
    strip = shapely.box(0, 0, 1, 0.2)  # centreline confined to the door cut itself
    result = ng.generate_centrelines(_components(shapely.box(5, 5, 9, 9)), _cuts(("D1", strip)), config)
    assert result["summary"]["doorways_not_crossed"] == ["D1"]


def test_failure_is_reported(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("bad geometry")
    monkeypatch.setattr(ng.pygeoops, "centerline", boom)
    result = ng.generate_centrelines(_components(shapely.box(0, 0, 4, 4)), _cuts(), config)
    failed = result["summary"]["failed_or_empty"]
    assert failed[0]["status"] == "failed" and "bad geometry" in failed[0]["error"]
    assert len(result["centrelines"]) == 0
