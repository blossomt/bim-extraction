"""Walking-network generation: door-split regions, room centre nodes and pygeoops corridor centrelines."""

from __future__ import annotations

import time
from collections import Counter

import geopandas as gpd
import numpy as np
import pygeoops
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import nearest_points

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
            row["max_half_width_m"] = round(shapely.maximum_inscribed_circle(poly, 0.05).length, 3)
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
    door_rows = []
    names = door_cuts["door_name"] if "door_name" in door_cuts else [None] * len(door_cuts)
    for door_id, door_name, cut in zip(door_cuts.get("door_global_id", []), names, door_cuts.geometry):
        doorway = cut.intersection(walkable)
        adjacent = sorted(int(j) for j in tree.query(cut.buffer(cfg.DOOR_ADJACENCY_TOL_M), predicate="intersects")) if parts else []
        status = "open" if doorway.area > 0 else "blocked"
        door_rows.append({
            "door_global_id": door_id, "name": door_name, "status": status,
            "adjacent_regions": ",".join(region_rows[j]["region_id"] for j in adjacent),
            "adjacent_kinds": ",".join(region_rows[j]["kind"] for j in adjacent),
            "geometry": doorway.centroid if status == "open" else cut.centroid,
        })
        for j in adjacent:
            region_rows[j]["doors"] += 1

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
            "walkable": walkable, "summary": summary}


# --- Phase 6: connectors ---------------------------------------------------------------------


def _connect(point: Point, region: dict, room_nodes: dict, centrelines: dict, walk_tol, cfg) -> dict:
    """Edge from an access point into one region: room centre node, or nearest corridor centreline point."""
    rid = region["region_id"]
    if region["kind"] == "room":
        target, kind = room_nodes[rid], "room_spoke"
    elif rid in centrelines:
        target, kind = nearest_points(point, centrelines[rid])[1], "corridor_snap"
    else:
        return {"region_id": rid, "kind": "corridor_snap", "status": "no_centreline", "distance_m": None,
                "snap_limit_m": None, "crosses_non_walkable_m": None, "geometry": None}
    line = LineString([point, target])
    dist = point.distance(target)
    # Room spokes are internal room edges; only snaps to a corridor centreline are limited by the snap distance.
    limit = None
    if kind == "corridor_snap":
        limit = max(cfg.MAX_SNAP_DISTANCE_M, (region.get("max_half_width_m") or 0) + cfg.SNAP_WIDTH_MARGIN_M)
    status = "too_long" if limit is not None and dist > limit else "ok"
    return {"region_id": rid, "kind": kind, "status": status, "distance_m": round(dist, 3),
            "snap_limit_m": round(limit, 3) if limit is not None else None,
            "crosses_non_walkable_m": round(line.difference(walk_tol).length, 3), "geometry": line}


def _overall_status(connectors: list[dict], n_regions: int) -> str:
    ok = sum(c["status"] == "ok" for c in connectors)
    if n_regions == 0:
        return "no_adjacent_region"
    if ok == 0:
        return "unconnected"
    if ok < len(connectors):
        return "partial"
    return "connected" if n_regions >= 2 else "connected_one_side"


def build_connectors(network: dict, door_points: gpd.GeoDataFrame, lift_frame: gpd.GeoDataFrame,
                     lift_records: list[dict], cfg) -> dict:
    """Connect doorways and label-only lift landing doors to room nodes / corridor centrelines."""
    regions = network["regions"]
    region_by_id = {r["region_id"]: r for r in regions.to_dict("records")} if len(regions) else {}
    room_nodes = dict(zip(network["room_nodes"].get("region_id", []), network["room_nodes"].geometry))
    centrelines = {}
    for rid, g in zip(network["centrelines"].get("region_id", []), network["centrelines"].geometry):
        centrelines[rid] = shapely.union_all([centrelines[rid], g]) if rid in centrelines else g
    walkable = network["walkable"]
    walk_tol = walkable.buffer(1e-3) if not walkable.is_empty else walkable
    original = dict(zip(door_points.get("global_id", []), door_points.geometry))

    connector_rows, door_rows = [], []
    for door in network["doors"].to_dict("records"):
        gid, point = door["door_global_id"], door["geometry"]
        orig = original.get(gid)
        base = {"door_global_id": gid, "name": door["name"],
                "access_point_adjustment_m": round(orig.distance(point), 3) if orig is not None else None,
                "access_point_in_walkable": bool(walk_tol.contains(point))}
        if door["status"] != "open":
            door_rows.append({**base, "connection_status": "blocked_doorway", "connectors": 0, "geometry": point})
            continue
        rids = [r for r in door["adjacent_regions"].split(",") if r]
        conns = [_connect(point, region_by_id[r], room_nodes, centrelines, walk_tol, cfg) for r in rids]
        for c in conns:
            connector_rows.append({"source": "door", "source_global_id": gid, "source_name": door["name"],
                                   "identification": "explicit_ifc", **c})
        door_rows.append({**base, "connection_status": _overall_status(conns, len(rids)), "connectors": len(conns),
                          "geometry": point})

    lift_geom = dict(zip(lift_frame.get("global_id", []), lift_frame.geometry))
    tree = shapely.STRtree(regions.geometry.values) if len(regions) else None
    lift_rows = []
    for rec in lift_records:
        if not rec["is_access_point_candidate"]:
            continue
        gid = rec["global_id"]
        base = {"lift_global_id": gid, "name": rec["name"], "identification": rec["identification"],
                "access_rule": rec["access_rule"]}
        fp = lift_geom.get(gid)
        if rec["placement_z_outside_storey"]:
            status = "excluded_placement_inconsistent"  # decision 5: report, never connect
        elif fp is None or fp.is_empty:
            status = "no_geometry"
        else:
            status = None
        if status:
            lift_rows.append({**base, "connection_status": status, "region_id": None, "distance_m": None,
                              "geometry": fp.centroid if fp is not None and not fp.is_empty else None})
            continue
        point = fp.centroid
        near = tree.query(fp.buffer(cfg.LIFT_ADJACENCY_TOL_M), predicate="intersects") if tree is not None else []
        if len(near) == 0:
            lift_rows.append({**base, "connection_status": "no_adjacent_region", "region_id": None, "distance_m": None,
                              "geometry": point})
            continue
        j = min(near, key=lambda k: regions.geometry.iloc[k].distance(point))
        c = _connect(point, region_by_id[regions["region_id"].iloc[j]], room_nodes, centrelines, walk_tol, cfg)
        connector_rows.append({"source": "lift_landing_door", "source_global_id": gid, "source_name": rec["name"],
                               "identification": rec["identification"], **c})
        lift_rows.append({**base, "connection_status": "connected" if c["status"] == "ok" else c["status"],
                          "region_id": c["region_id"], "distance_m": c["distance_m"], "geometry": point})

    connectors = _gdf([r for r in connector_rows if r["geometry"] is not None])
    # Whole-network connectivity: centrelines + accepted connectors (touching lines form one component).
    edges = list(network["centrelines"].geometry) + [r["geometry"] for r in connector_rows
                                                     if r["geometry"] is not None and r["status"] == "ok" and r["geometry"].length > 0]
    groups = connected_groups([ln for g in edges for ln in shapely.get_parts(g)])
    door_pts = [r["geometry"] for r in door_rows if r["connection_status"].startswith("connected")]
    doors_per_group = [sum(1 for p in door_pts if g.distance(p) < 1e-6) for g in groups]
    corridor_ids_with_connectors = {r["region_id"] for r in connector_rows if r["kind"] == "corridor_snap" and r["status"] == "ok"}
    snaps = [r for r in connector_rows if r["kind"] == "corridor_snap" and r["distance_m"] is not None]

    summary = {
        "max_snap_distance_m": cfg.MAX_SNAP_DISTANCE_M,
        "snap_width_margin_m": cfg.SNAP_WIDTH_MARGIN_M,
        "connectors_over_base_limit_accepted": sum(
            1 for r in snaps if r["status"] == "ok" and r["distance_m"] > cfg.MAX_SNAP_DISTANCE_M),
        "doors_by_connection_status": dict(Counter(r["connection_status"] for r in door_rows)),
        "connectors_by_kind_status": dict(Counter(f"{r['kind']}:{r['status']}" for r in connector_rows)),
        "corridor_snap_distance_m": {
            "mean": round(float(np.mean([r["distance_m"] for r in snaps])), 3) if snaps else None,
            "max": max((r["distance_m"] for r in snaps), default=None),
        },
        "connectors_too_long": [{"source_global_id": r["source_global_id"], "distance_m": r["distance_m"],
                                 "snap_limit_m": r["snap_limit_m"]}
                                for r in connector_rows if r["status"] == "too_long"],
        "connectors_crossing_non_walkable": [
            {"source_global_id": r["source_global_id"], "kind": r["kind"], "length_m": r["crosses_non_walkable_m"]}
            for r in connector_rows if (r["crosses_non_walkable_m"] or 0) > 0.01],
        "door_access_points_outside_walkable": [r["door_global_id"] for r in door_rows if not r["access_point_in_walkable"]],
        "door_access_point_adjustment_max_m": max((r["access_point_adjustment_m"] or 0 for r in door_rows), default=None),
        "unconnected_doors": [{"door_global_id": r["door_global_id"], "status": r["connection_status"]}
                              for r in door_rows if r["connection_status"] not in ("connected", "connected_one_side")],
        "lifts": {
            "access_point_candidates": len(lift_rows),
            "identification": dict(Counter(r["identification"] for r in lift_rows)),
            "by_connection_status": dict(Counter(r["connection_status"] for r in lift_rows)),
        },
        "corridor_regions_without_connectors": sorted(set(centrelines) - corridor_ids_with_connectors),
        "network_components": len(groups),
        "network_components_with_doors": sum(1 for n in doors_per_group if n),
        "largest_component_doors": max(doors_per_group, default=0),
        "connected_doors": len(door_pts),
    }
    return {"connectors": connectors, "door_access": _gdf(door_rows),
            "lift_access": _gdf([r for r in lift_rows if r["geometry"] is not None]), "summary": summary}
