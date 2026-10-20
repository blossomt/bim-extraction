"""Walking-network generation: door-split regions, room centre nodes and pygeoops corridor centrelines."""

from __future__ import annotations

import time
from collections import Counter

import geopandas as gpd
import numpy as np
import pygeoops
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon

import geometry_processing as gp

CONNECT_TOL_M = 1e-6


def _gdf(rows: list[dict]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows or {"geometry": []}, geometry="geometry", crs=None)


def _line_parts(geom) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    merged = shapely.line_merge(shapely.union_all(geom)) if not isinstance(geom, LineString) else geom
    return [p for p in shapely.get_parts(merged) if isinstance(p, LineString) and p.length > 0]


def connected_groups(lines: list[LineString]) -> list[MultiLineString]:
    """Group line parts that touch each other; groups ordered by descending total length."""
    if not lines:
        return []
    blobs = list(shapely.get_parts(shapely.union_all([ln.buffer(CONNECT_TOL_M) for ln in lines])))
    tree = shapely.STRtree(blobs)
    members: dict[int, list] = {}
    for ln in lines:
        hits = tree.query(ln.interpolate(0.5, normalized=True), predicate="intersects")
        members.setdefault(int(hits[0]) if len(hits) else -1, []).append(ln)
    groups = [MultiLineString(v) for v in members.values()]
    return sorted(groups, key=lambda g: -g.length)


def rectangle_metrics(poly: Polygon) -> tuple[float, float]:
    """(rectangularity = area / min. rotated rectangle area, aspect = long side / short side)."""
    rect = poly.minimum_rotated_rectangle
    if rect.area == 0:
        return 0.0, float("inf")
    c = shapely.get_coordinates(rect)[:3]
    a, b = sorted([np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[1])])
    return poly.area / rect.area, b / a if a else float("inf")


def classify_region(poly: Polygon, cfg) -> str:
    rectangularity, aspect = rectangle_metrics(poly)
    return "room" if rectangularity >= cfg.ROOM_MIN_RECTANGULARITY and aspect <= cfg.ROOM_MAX_ASPECT else "corridor"


def room_point(poly: Polygon) -> tuple[Point, str]:
    c = poly.centroid
    if poly.contains(c):
        return c, "centroid"
    return poly.point_on_surface(), "point_on_surface"  # centroid falls in a hole (e.g. column)


def split_regions(walkable, door_cuts: gpd.GeoDataFrame, cfg) -> tuple[list[Polygon], dict]:
    """Walkable polygon minus door cuts: rooms and corridors become separate regions joined only via doorways."""
    cuts = shapely.union_all(door_cuts.geometry.values) if len(door_cuts) else Polygon()
    regions, log = gp.clean_polygons(walkable.difference(cuts), cfg.MIN_COMPONENT_AREA_M2, cfg.MIN_COMPONENT_MEAN_WIDTH_M)
    parts = sorted(shapely.get_parts(regions), key=lambda p: -p.area) if not regions.is_empty else []
    return list(parts), log


def _corridor_centreline(poly: Polygon, cfg):
    return pygeoops.centerline(
        poly,
        densify_distance=cfg.CORRIDOR_DENSIFY_M,
        min_branch_length=cfg.CORRIDOR_MIN_BRANCH_M,
        simplifytolerance=cfg.CORRIDOR_SIMPLIFY_M,
    )


def build_network(components: gpd.GeoDataFrame, door_cuts: gpd.GeoDataFrame, cfg) -> dict:
    t0 = time.time()
    walkable = shapely.union_all(components.geometry.values) if len(components) else Polygon()
    parts, split_log = split_regions(walkable, door_cuts, cfg)
    tree = shapely.STRtree(parts)

    region_rows, room_rows, centreline_rows = [], [], []
    for i, poly in enumerate(parts):
        rid = f"R{i:03d}"  # ordered by descending area
        rectangularity, aspect = rectangle_metrics(poly)
        kind = classify_region(poly, cfg)
        row = {"region_id": rid, "kind": kind, "area_m2": round(poly.area, 3), "rectangularity": round(rectangularity, 3),
               "aspect": round(aspect, 2), "holes": len(poly.interiors), "doors": 0, "status": "ok", "geometry": poly}
        if kind == "room":
            pt, method = room_point(poly)
            room_rows.append({"region_id": rid, "node_id": f"{rid}.N", "method": method, "geometry": pt})
        else:
            try:
                groups = connected_groups(_line_parts(_corridor_centreline(poly, cfg)))
            except Exception as e:  # report and continue; never substitute another algorithm
                groups, row["status"], row["error"] = [], "failed", f"{type(e).__name__}: {e}"
            if not groups and row["status"] == "ok":
                row["status"] = "empty"
            elif len(groups) > 1:
                row["status"] = "fragmented"
            for k, g in enumerate(groups):
                centreline_rows.append({"centreline_id": f"{rid}.C{k}", "region_id": rid, "length_m": round(g.length, 3),
                                        "segments": len(g.geoms), "geometry": g})
        region_rows.append(row)

    # Doors: doorway = walkable part of the door cut; adjacency = regions touching the cut.
    door_rows, spoke_rows = [], []
    rooms_by_index = {int(r["region_id"][1:]): r for r in room_rows}
    walk_tol = walkable.buffer(1e-3) if not walkable.is_empty else walkable
    names = door_cuts["door_name"] if "door_name" in door_cuts else [None] * len(door_cuts)
    for door_id, door_name, cut in zip(door_cuts.get("door_global_id", []), names, door_cuts.geometry):
        doorway = cut.intersection(walkable)
        adjacent = sorted(int(j) for j in tree.query(cut.buffer(cfg.DOOR_ADJACENCY_TOL_M), predicate="intersects")) if parts else []
        status = "open" if doorway.area > 0 else "blocked"
        point = doorway.centroid if status == "open" else cut.centroid
        door_rows.append({
            "door_global_id": door_id, "name": door_name, "status": status,
            "adjacent_regions": ",".join(region_rows[j]["region_id"] for j in adjacent),
            "adjacent_kinds": ",".join(region_rows[j]["kind"] for j in adjacent),
            "geometry": point,
        })
        for j in adjacent:
            region_rows[j]["doors"] += 1
        if status != "open":
            continue
        for j in adjacent:
            if j in rooms_by_index:
                room = rooms_by_index[j]
                spoke = LineString([point, room["geometry"]])
                spoke_rows.append({"door_global_id": door_id, "region_id": room["region_id"], "length_m": round(spoke.length, 3),
                                   "leaves_walkable_m": round(spoke.difference(walk_tol).length, 3), "geometry": spoke})

    regions = _gdf(region_rows)
    for room in room_rows:
        room["doors"] = region_rows[int(room["region_id"][1:])]["doors"]
    centrelines = _gdf(centreline_rows)
    kinds = Counter(r["kind"] for r in region_rows)
    summary = {
        "variant": cfg.CENTRELINE_VARIANT,
        "pygeoops_version": pygeoops.__version__,
        "room_rule": {"min_rectangularity": cfg.ROOM_MIN_RECTANGULARITY, "max_aspect": cfg.ROOM_MAX_ASPECT},
        "corridor_parameters": {"densify_distance": cfg.CORRIDOR_DENSIFY_M, "min_branch_length": cfg.CORRIDOR_MIN_BRANCH_M,
                                "simplifytolerance": cfg.CORRIDOR_SIMPLIFY_M},
        "runtime_s": round(time.time() - t0, 2),
        "region_split": split_log,
        "regions": dict(kinds),
        "room_area_m2": round(sum(r["area_m2"] for r in region_rows if r["kind"] == "room"), 3),
        "corridor_area_m2": round(sum(r["area_m2"] for r in region_rows if r["kind"] == "corridor"), 3),
        "corridor_status": dict(Counter(r["status"] for r in region_rows if r["kind"] == "corridor")),
        "centreline_components": len(centrelines),
        "centreline_length_m": round(float(centrelines["length_m"].sum()), 3) if len(centrelines) else 0.0,
        "room_nodes": len(room_rows),
        "room_spokes": len(spoke_rows),
        "spokes_leaving_walkable": [r["door_global_id"] for r in spoke_rows if r["leaves_walkable_m"] > 0.01],
        "doors_by_status": dict(Counter(r["status"] for r in door_rows)),
        "doors_by_adjacent_region_count": dict(Counter(
            len(r["adjacent_regions"].split(",")) if r["adjacent_regions"] else 0 for r in door_rows)),
        "regions_without_doors": [r["region_id"] for r in region_rows if r["doors"] == 0],
        "regions_without_doors_by_kind": dict(Counter(r["kind"] for r in region_rows if r["doors"] == 0)),
        "corridors_failed_or_empty": [
            {k: r.get(k) for k in ("region_id", "area_m2", "status", "error")}
            for r in region_rows if r["kind"] == "corridor" and r["status"] in ("failed", "empty")
        ],
    }
    return {"regions": regions, "room_nodes": _gdf(room_rows), "centrelines": centrelines, "doors": _gdf(door_rows),
            "spokes": _gdf(spoke_rows), "summary": summary}
