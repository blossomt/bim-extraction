"""Centreline generation (pygeoops) for walkable polygon components."""

from __future__ import annotations

import time
from collections import Counter

import geopandas as gpd
import pygeoops
import shapely
from shapely.geometry import LineString, MultiLineString

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


def _mean_width(poly) -> float:
    return 2 * poly.area / poly.length if poly.length else 0.0


def generate_centrelines(components: gpd.GeoDataFrame, door_cuts: gpd.GeoDataFrame, cfg) -> dict:
    """One pygeoops call per walkable component; connected centreline groups get IDs '<component>.C<k>'."""
    t0 = time.time()
    rows, per_component = [], []
    for cid, poly in zip(components.get("component_id", []), components.geometry):
        record = {"component_id": cid, "area_m2": round(poly.area, 3), "mean_width_m": round(_mean_width(poly), 3)}
        try:
            cl = pygeoops.centerline(
                poly,
                densify_distance=cfg.CENTRELINE_DENSIFY_M,
                min_branch_length=cfg.CENTRELINE_MIN_BRANCH_M,
                simplifytolerance=cfg.CENTRELINE_SIMPLIFY_M,
            )
        except Exception as e:  # report and continue; never substitute another algorithm
            per_component.append({**record, "status": "failed", "error": f"{type(e).__name__}: {e}"})
            continue
        groups = connected_groups(_line_parts(cl))
        if not groups:
            per_component.append({**record, "status": "empty"})
            continue
        status = "ok" if len(groups) == 1 else "fragmented"
        per_component.append({**record, "status": status, "centreline_groups": len(groups),
                              "length_m": round(sum(g.length for g in groups), 3)})
        for k, g in enumerate(groups):
            rows.append({
                "centreline_id": f"{cid}.C{k}",
                "component_id": cid,
                "length_m": round(g.length, 3),
                "segments": len(g.geoms),
                "component_status": status,
                "geometry": g,
            })
    centrelines = gpd.GeoDataFrame(rows or {"geometry": []}, geometry="geometry", crs=None)

    all_lines = shapely.union_all(centrelines.geometry.values) if len(centrelines) else None
    crossed, not_crossed = [], []
    for door_id, cut in zip(door_cuts.get("door_global_id", []), door_cuts.geometry):
        hits = centrelines[centrelines.intersects(cut)] if len(centrelines) else centrelines
        # Ignore centrelines confined to the door cut itself (isolated doorway strips).
        ok = any(g.difference(cut).length >= cfg.DOORWAY_CROSSING_MIN_EXTENT_M for g in hits.geometry)
        (crossed if ok else not_crossed).append(door_id)
    walkable = shapely.union_all(components.geometry.values) if len(components) else None
    outside = shapely.difference(all_lines, walkable.buffer(CONNECT_TOL_M)).length if all_lines is not None else 0.0

    status_counts = Counter(c["status"] for c in per_component)
    summary = {
        "variant": cfg.CENTRELINE_VARIANT,
        "parameters": {
            "densify_distance": cfg.CENTRELINE_DENSIFY_M,
            "min_branch_length": cfg.CENTRELINE_MIN_BRANCH_M,
            "simplifytolerance": cfg.CENTRELINE_SIMPLIFY_M,
        },
        "pygeoops_version": pygeoops.__version__,
        "runtime_s": round(time.time() - t0, 2),
        "walkable_components": len(per_component),
        "component_status": dict(status_counts),
        "centreline_components": len(centrelines),
        "total_length_m": round(float(centrelines["length_m"].sum()), 3) if len(centrelines) else 0.0,
        "length_outside_walkable_m": round(outside, 4),
        "doorways_crossed": len(crossed),
        "doorways_not_crossed": not_crossed,
        "failed_or_empty": [c for c in per_component if c["status"] in ("failed", "empty")],
        "fragmented": [c for c in per_component if c["status"] == "fragmented"],
    }
    return {"centrelines": centrelines, "per_component": per_component, "summary": summary}
