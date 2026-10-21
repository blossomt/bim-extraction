from html.parser import HTMLParser
import re

import geopandas as gpd
import numpy as np
import pytest
import shapely

import config
import network_generation as ng
import visualisation


class _Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "img"} and "src" in attrs:
            self.urls.append(attrs["src"])
        if tag == "link" and "href" in attrs:
            self.urls.append(attrs["href"])


def test_build_floor_map_is_standalone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doors = gpd.GeoDataFrame([{
        "name": "Test door", "global_id": "D1", "category": "doors",
        "entity": "IfcDoor", "storey": "Test", "status": "ok", "info": "",
        "properties": "FireRating=60", "geometry": shapely.box(0, 0, 1, 0.2),
    }], geometry="geometry", crs=None)
    nodes = gpd.GeoDataFrame([{
        "region_id": "R1", "doors": 1, "geometry": shapely.Point(1, 1),
    }], geometry="geometry", crs=None)
    out = visualisation.build_floor_map(
        {"doors": doors, "net_room_nodes": nodes}, "Test", [0, 0, 4, 4],
        ["Offline test"], tmp_path / "nested" / "floor_plan.html",
    )
    text = out.read_text(encoding="utf-8")
    resources = _Resources()
    resources.feed(text)
    assert not resources.urls
    assert all(url.strip("'\"").startswith(("data:", "#")) for url in re.findall(r"url\(([^)]+)\)", text))
    assert "Leaflet 1.9.3" in text
    assert "jQuery v3.7.1" in text
    assert re.search(r"Bootstrap\s+v5\.2\.2", text)
    assert "data:image/png;base64," in text
    assert "D1" in text and "FireRating=60" in text
    assert "L.control.layers(" in text and "new L.CircleMarker(" in text
    assert "Offline test" in text and "not geographic" in text


def test_build_3d_view(tmp_path):
    walkable = shapely.union_all([shapely.box(0, 0, 4, 4), shapely.box(4, 1.5, 4.2, 2.5), shapely.box(4.2, 0, 6.2, 12)])
    cuts = gpd.GeoDataFrame([{"door_global_id": "D1", "geometry": shapely.box(4, 1.5, 4.2, 2.5)}], geometry="geometry", crs=None)
    comps = gpd.GeoDataFrame([{"component_id": "O000", "geometry": walkable}], geometry="geometry", crs=None)
    net = ng.build_network(comps, cuts, config)
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=None)
    conn = ng.build_connectors(net, empty, empty, [], config)
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 3]], dtype=float)
    faces = np.array([[0, 1, 2], [0, 1, 3]])
    meshes = {"walls": [{"global_id": "W1", "name": "wall", "entity": "IfcWall", "parent_global_id": None,
                         "verts": verts, "faces": faces}]}
    out, _fig = visualisation.build_3d_view(meshes, net, conn, "Test", 0.0, [], tmp_path / "v.html")
    text = out.read_text(encoding="utf-8")
    assert "walls (1)" in text and "corridor centreline" in text and "not geographic" in text
    assert not re.search(r"<script[^>]*\bsrc=", text) and "Plotly.newPlot" in text  # plotly.js embedded: offline


@pytest.mark.parametrize("z_clip", [None, 10.6])
def test_build_3d_view_without_storey_geometry(tmp_path, z_clip):
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=None)
    net = ng.build_network(empty, empty, config)
    conn = ng.build_connectors(net, empty, empty, [], config)
    out, fig = visualisation.build_3d_view(
        {}, net, conn, "Storey -1", 7.6, [], tmp_path / "empty.html", z_clip_m=z_clip,
    )
    assert out.is_file()
    assert len(fig.data) == 0
    assert "No displayable geometry" in out.read_text(encoding="utf-8")
    assert fig.layout.scene.aspectratio.x is None
    assert fig.layout.scene.xaxis.visible is False


@pytest.mark.parametrize("horizontal_extent", [0, 4])
def test_build_3d_view_with_meshes_but_no_walkable_regions(tmp_path, horizontal_extent):
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=None)
    net = ng.build_network(empty, empty, config)
    conn = ng.build_connectors(net, empty, empty, [], config)
    meshes = {"walls": [{
        "global_id": "W1", "name": "wall", "entity": "IfcWall", "parent_global_id": None,
        "verts": np.array([
            [10, 20, 0], [10 + horizontal_extent, 20, 0], [10, 20 + horizontal_extent / 2, 3],
        ], dtype=float),
        "faces": np.array([[0, 1, 2]]),
    }]}
    out, fig = visualisation.build_3d_view(meshes, net, conn, "Test", 0.0, [], tmp_path / "meshes.html")
    assert out.is_file() and len(fig.data) == 1
    if horizontal_extent == 0:
        assert fig.layout.scene.aspectmode == "data"
        assert fig.layout.scene.aspectratio.x is None
        return
    assert tuple(fig.layout.scene.xaxis.range) == (10, 14)
    assert tuple(fig.layout.scene.yaxis.range) == (20, 22)
    assert fig.layout.scene.aspectratio.x == 1
    assert fig.layout.scene.aspectratio.y == 0.5
    assert fig.layout.scene.aspectratio.z == 0.875
