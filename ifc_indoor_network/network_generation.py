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
import heuristics

CONNECT_TOL_M = 1e-6


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
    regions, log = gp.clean_polygons(walkable.difference(cuts), cfg.MIN_COMPONENT_AREA_M2, heuristics.MIN_COMPONENT_MEAN_WIDTH_M)
    parts = sorted(shapely.get_parts(regions), key=lambda p: -p.area) if not regions.is_empty else []
    return list(parts), log


def _edges(geom) -> list[LineString]:
    """Line network split at every junction into junction-to-junction/leaf edges (coordinates rounded to snap ends)."""
    merged = shapely.line_merge(shapely.node(shapely.union_all(geom)))
    return [LineString([(round(x, 6), round(y, 6)) for x, y in g.coords])
            for g in shapely.get_parts(merged) if isinstance(g, LineString) and g.length > 0]


def _merge(edges: list[LineString]):
    return shapely.line_merge(MultiLineString(edges)) if edges else None


def open_polygon(poly: Polygon, min_clear_width: float):
    """Morphological opening: removes every part of the region narrower than `min_clear_width` (gaps and teeth)."""
    r = min_clear_width / 2
    return poly.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")


def collapse_obstacle_loops(geom, poly: Polygon, max_loop_m2: float):
    """Medial axes loop around every obstacle (column, post). Loops enclosing less than `max_loop_m2` in total are
    duplicate paths: drop the side closest to the corridor boundary, leaving one path past the obstacle."""
    edges = _edges(geom)
    boundary = poly.boundary
    while edges:
        mids = [e.interpolate(0.5, normalized=True) for e in edges]
        drop, used = set(), set()
        for face in shapely.get_parts(shapely.polygonize(edges)):
            if face.area >= max_loop_m2:
                continue
            sides = [i for i, m in enumerate(mids) if face.exterior.distance(m) < CONNECT_TOL_M]
            if sides and not used.intersection(sides):
                used.update(sides)
                drop.add(min(sides, key=lambda i: boundary.distance(mids[i])))
        if not drop:
            break
        edges = _edges([e for i, e in enumerate(edges) if i not in drop])
    return _merge(edges)


def drop_parallel_leaves(geom, offset_m: float, cover: float = 0.7):
    """Remove leaf branches that run alongside another branch (>= `cover` of their length within `offset_m`), as
    produced in open spaces by jagged boundaries. Shortest first, one at a time, until stable."""
    edges = _edges(geom)
    while True:
        degree = Counter(pt for e in edges for pt in (e.coords[0], e.coords[-1]))
        leaves = sorted((i for i, e in enumerate(edges) if 1 in (degree[e.coords[0]], degree[e.coords[-1]])),
                        key=lambda i: edges[i].length)
        drop = None
        for i in leaves:
            others = [e for j, e in enumerate(edges) if j != i]
            if others and edges[i].intersection(shapely.union_all(others).buffer(offset_m)).length >= cover * edges[i].length:
                drop = i
                break
        if drop is None:
            return _merge(edges)
        edges = _edges([e for j, e in enumerate(edges) if j != drop])


def prune_corner_spurs(geom, poly: Polygon, factor: float, max_half_width: float = float("inf")):
    """Remove short leaf branches (medial-axis forks into corridor corners and ends).

    A leaf branch is a spur if it is shorter than `factor` x the local half-width (distance from its junction to the
    corridor boundary). Only junctions with half-width <= `max_half_width` are pruned: in wide halls the branches
    are real access paths. At each junction at least one branch is kept. Repeated until stable.
    """
    edges = _edges(geom)
    boundary = poly.boundary
    while True:
        degree = Counter(pt for e in edges for pt in (e.coords[0], e.coords[-1]))
        spurs: dict[tuple, list[int]] = {}
        for i, e in enumerate(edges):
            for leaf, junction in ((e.coords[0], e.coords[-1]), (e.coords[-1], e.coords[0])):
                if degree[leaf] == 1 and degree[junction] >= 3:
                    half_width = boundary.distance(Point(junction))
                    if half_width <= max_half_width and e.length < factor * half_width:
                        spurs.setdefault(junction, []).append(i)
                    break
        drop = set()
        for junction, idx in spurs.items():
            idx.sort(key=lambda i: edges[i].length)
            drop.update(idx[: degree[junction] - 1])
        if not drop:
            return _merge(edges)
        edges = [e for i, e in enumerate(edges) if i not in drop]


def _corridor_centreline(poly: Polygon, cfg):
    """Centreline of the region's parts at least CORRIDOR_MIN_CLEAR_WIDTH_M wide, one path per corridor axis."""
    pieces = [pygeoops.centerline(part, densify_distance=heuristics.CORRIDOR_DENSIFY_M, min_branch_length=heuristics.CORRIDOR_MIN_BRANCH_M,
                                  simplifytolerance=heuristics.CORRIDOR_SIMPLIFY_M)
              for part in shapely.get_parts(open_polygon(poly, cfg.CORRIDOR_MIN_CLEAR_WIDTH_M))]
    pieces = [p for p in pieces if p is not None and not p.is_empty]
    lines = collapse_obstacle_loops(pieces, poly, heuristics.CORRIDOR_LOOP_MAX_AREA_M2) if pieces else None
    if lines is not None and heuristics.CORRIDOR_PARALLEL_OFFSET_M > 0:
        lines = drop_parallel_leaves(lines, heuristics.CORRIDOR_PARALLEL_OFFSET_M)
    if lines is None:
        return None
    return prune_corner_spurs(lines, poly, heuristics.CORRIDOR_SPUR_HALF_WIDTH_FACTOR, heuristics.CORRIDOR_SPUR_MAX_HALF_WIDTH_M)


def _build_regions(parts: list[Polygon], cfg) -> tuple[list[dict], list[dict], list[dict]]:
    """Region rows, room centre nodes and corridor centrelines (regions ordered by descending area)."""
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
    return region_rows, room_rows, centreline_rows


def _build_doors(door_cuts: gpd.GeoDataFrame, walkable, parts: list[Polygon], region_rows: list[dict], cfg) -> list[dict]:
    """Doorway = walkable part of the door cut; adjacency = regions touching the cut (updates region door counts)."""
    tree = shapely.STRtree(parts)
    door_rows = []
    names = door_cuts["door_name"] if "door_name" in door_cuts else [None] * len(door_cuts)
    for door_id, door_name, cut in zip(door_cuts.get("door_global_id", []), names, door_cuts.geometry):
        doorway = cut.intersection(walkable)
        adjacent = sorted(int(j) for j in tree.query(cut.buffer(heuristics.DOOR_ADJACENCY_TOL_M), predicate="intersects")) if parts else []
        status = "open" if doorway.area > 0 else "blocked"
        door_rows.append({
            "door_global_id": door_id, "name": door_name, "status": status,
            "adjacent_regions": ",".join(region_rows[j]["region_id"] for j in adjacent),
            "adjacent_kinds": ",".join(region_rows[j]["kind"] for j in adjacent),
            "geometry": doorway.centroid if status == "open" else cut.centroid,
        })
        for j in adjacent:
            region_rows[j]["doors"] += 1
    return door_rows


def _network_summary(region_rows, room_rows, centrelines, door_rows, split_log, runtime_s, cfg) -> dict:
    kinds = Counter(r["kind"] for r in region_rows)
    return {
        "pygeoops_version": pygeoops.__version__,
        "room_rule": {"min_rectangularity": cfg.ROOM_MIN_RECTANGULARITY, "max_aspect": cfg.ROOM_MAX_ASPECT},
        "corridor_parameters": {"densify_distance": heuristics.CORRIDOR_DENSIFY_M, "min_branch_length": heuristics.CORRIDOR_MIN_BRANCH_M,
                                "simplifytolerance": heuristics.CORRIDOR_SIMPLIFY_M,
                                "spur_half_width_factor": heuristics.CORRIDOR_SPUR_HALF_WIDTH_FACTOR,
                                "spur_max_half_width": heuristics.CORRIDOR_SPUR_MAX_HALF_WIDTH_M,
                                "min_clear_width": cfg.CORRIDOR_MIN_CLEAR_WIDTH_M,
                                "loop_max_area_m2": heuristics.CORRIDOR_LOOP_MAX_AREA_M2,
                                "parallel_offset": heuristics.CORRIDOR_PARALLEL_OFFSET_M},
        "runtime_s": runtime_s,
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


def build_network(components: gpd.GeoDataFrame, door_cuts: gpd.GeoDataFrame, cfg) -> dict:
    t0 = time.time()
    walkable = shapely.union_all(components.geometry.values) if len(components) else Polygon()
    parts, split_log = split_regions(walkable, door_cuts, cfg)
    region_rows, room_rows, centreline_rows = _build_regions(parts, cfg)
    door_rows = _build_doors(door_cuts, walkable, parts, region_rows, cfg)
    for room in room_rows:
        room["doors"] = region_rows[int(room["region_id"][1:])]["doors"]
    centrelines = gp.gdf(centreline_rows)
    summary = _network_summary(region_rows, room_rows, centrelines, door_rows, split_log, round(time.time() - t0, 2), cfg)
    return {"regions": gp.gdf(region_rows), "room_nodes": gp.gdf(room_rows), "centrelines": centrelines,
            "doors": gp.gdf(door_rows), "walkable": walkable, "summary": summary}


def label_regions(regions: gpd.GeoDataFrame, spaces: gpd.GeoDataFrame, space_records: list[dict],
                  min_overlap: float = 0.5) -> dict[str, str]:
    """Display-only IFC room purpose per region: the IfcSpace (LongName, else Name) covering most of the region.

    Matched by geometric overlap, so it is a hint for map readers only and never used for storey membership or
    network logic. Regions with < `min_overlap` of their area inside one IfcSpace stay unlabelled.
    """
    if not len(regions) or not len(spaces):
        return {}
    labels = {rec["global_id"]: rec.get("long_name") or rec.get("name") for rec in space_records}
    tree = shapely.STRtree(spaces.geometry.values)
    out = {}
    for rid, poly in zip(regions["region_id"], regions.geometry):
        best, best_area = None, 0.0
        for j in tree.query(poly, predicate="intersects"):
            area = poly.intersection(spaces.geometry.iloc[j]).area
            if area > best_area:
                best, best_area = spaces["global_id"].iloc[j], area
        label = labels.get(best)
        if label and best_area >= min_overlap * poly.area:
            out[rid] = str(label)
    return out


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
        limit = max(heuristics.MAX_SNAP_DISTANCE_M, (region.get("max_half_width_m") or 0) + heuristics.SNAP_WIDTH_MARGIN_M)
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


def _door_connectors(network: dict, original: dict, region_by_id: dict, room_nodes: dict, centrelines: dict,
                     walk_tol, cfg) -> tuple[list[dict], list[dict]]:
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
    return connector_rows, door_rows


def _lift_connectors(regions: gpd.GeoDataFrame, lift_frame: gpd.GeoDataFrame, lift_records: list[dict],
                     region_by_id: dict, room_nodes: dict, centrelines: dict, walk_tol, cfg) -> tuple[list[dict], list[dict]]:
    lift_geom = dict(zip(lift_frame.get("global_id", []), lift_frame.geometry))
    tree = shapely.STRtree(regions.geometry.values) if len(regions) else None
    lift_regions = set()
    if tree is not None and len(lift_frame):
        lift_union = shapely.union_all(list(lift_frame.geometry))
        lift_regions = {k for k in tree.query(lift_union, predicate="intersects")
                        if regions.geometry.iloc[k].intersection(lift_union).area >= 0.5 * regions.geometry.iloc[k].area}
    connector_rows, lift_rows = [], []
    for rec in lift_records:
        if not rec["is_access_point_candidate"]:
            continue
        gid = rec["global_id"]
        base = {"lift_global_id": gid, "name": rec["name"], "identification": rec["identification"],
                "access_rule": rec["access_rule"]}
        fp = lift_geom.get(gid)
        has_geometry = fp is not None and not fp.is_empty
        point = fp.centroid if has_geometry else None
        # A region mostly occupied by lift parts (the shaft) is not part of the main network: connect outward.
        near = [k for k in tree.query(fp.buffer(heuristics.LIFT_ADJACENCY_TOL_M), predicate="intersects")
                if k not in lift_regions] if has_geometry and tree is not None else []
        status = ("excluded_placement_inconsistent" if rec["placement_z_outside_storey"]  # report, never connect
                  else "no_geometry" if not has_geometry else "no_adjacent_region" if not near else None)
        if status:
            lift_rows.append({**base, "connection_status": status, "region_id": None, "distance_m": None, "geometry": point})
            continue
        j = min(near, key=lambda k: regions.geometry.iloc[k].distance(point))
        c = _connect(point, region_by_id[regions["region_id"].iloc[j]], room_nodes, centrelines, walk_tol, cfg)
        connector_rows.append({"source": "lift_landing_door", "source_global_id": gid, "source_name": rec["name"],
                               "identification": rec["identification"], **c})
        lift_rows.append({**base, "connection_status": "connected" if c["status"] == "ok" else c["status"],
                          "region_id": c["region_id"], "distance_m": c["distance_m"], "geometry": point})
    return connector_rows, lift_rows


def _connectivity_summary(network: dict, connector_rows, door_rows, lift_rows, centrelines: dict, cfg) -> dict:
    # Whole-network connectivity: centrelines + accepted connectors (touching lines form one component).
    edges = list(network["centrelines"].geometry) + [r["geometry"] for r in connector_rows
                                                     if r["geometry"] is not None and r["status"] == "ok" and r["geometry"].length > 0]
    groups = connected_groups([ln for g in edges for ln in shapely.get_parts(g)])
    door_pts = [r["geometry"] for r in door_rows if r["connection_status"].startswith("connected")]
    doors_per_group = [sum(1 for p in door_pts if g.distance(p) < 1e-6) for g in groups]
    corridor_ids_with_connectors = {r["region_id"] for r in connector_rows if r["kind"] == "corridor_snap" and r["status"] == "ok"}
    snaps = [r for r in connector_rows if r["kind"] == "corridor_snap" and r["distance_m"] is not None]
    return {
        "max_snap_distance_m": heuristics.MAX_SNAP_DISTANCE_M,
        "snap_width_margin_m": heuristics.SNAP_WIDTH_MARGIN_M,
        "connectors_over_base_limit_accepted": sum(
            1 for r in snaps if r["status"] == "ok" and r["distance_m"] > heuristics.MAX_SNAP_DISTANCE_M),
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

    connector_rows, door_rows = _door_connectors(network, original, region_by_id, room_nodes, centrelines, walk_tol, cfg)
    lift_connector_rows, lift_rows = _lift_connectors(regions, lift_frame, lift_records, region_by_id, room_nodes,
                                                      centrelines, walk_tol, cfg)
    connector_rows += lift_connector_rows
    summary = _connectivity_summary(network, connector_rows, door_rows, lift_rows, centrelines, cfg)
    return {"connectors": gp.gdf([r for r in connector_rows if r["geometry"] is not None]), "door_access": gp.gdf(door_rows),
            "lift_access": gp.gdf([r for r in lift_rows if r["geometry"] is not None]), "summary": summary}
