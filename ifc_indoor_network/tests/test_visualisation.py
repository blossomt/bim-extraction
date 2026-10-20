import geopandas as gpd
import numpy as np
import shapely

import config
import network_generation as ng
import visualisation


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
    out = visualisation.build_3d_view(meshes, net, conn, "Test", 0.0, [], tmp_path / "v.html")
    text = out.read_text()
    assert "walls (1)" in text and "corridor centreline" in text and "not geographic" in text
