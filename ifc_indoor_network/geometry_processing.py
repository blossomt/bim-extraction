"""IFC geometry -> 2D Shapely geometry for the selected storey.

Coordinates are IfcOpenShell world coordinates in metres (local project frame, not geographic).
Walls are meshed with opening subtraction disabled (solid); door openings are kept as separate
footprints so later phases can decide explicitly which openings make a wall passable.
"""

from __future__ import annotations

from collections import Counter

import geopandas as gpd
import ifcopenshell
import ifcopenshell.geom
import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

# Categories from Phase 2 records that get a 2D footprint.
FOOTPRINT_CATEGORIES = ["slabs", "spaces", "walls", "obstacles", "openings", "doors", "stairs", "stair_flights", "lifts"]


def mesh_settings() -> ifcopenshell.geom.settings:
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    s.set("disable-opening-subtractions", True)
    return s


def iterate_meshes(model: ifcopenshell.file, elements: list, num_threads: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{GlobalId: (verts (n,3) metres, faces (m,3))}. Elements without output are simply absent."""
    meshes: dict = {}
    if not elements:
        return meshes
    it = ifcopenshell.geom.iterator(mesh_settings(), model, num_threads, include=elements)
    if not it.initialize():
        return meshes
    while True:
        shape = it.get()
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.asarray(shape.geometry.faces, dtype=int).reshape(-1, 3)
        meshes[shape.guid] = (verts, faces)
        if not it.next():
            break
    return meshes


def polygonal_part(geom) -> Polygon | MultiPolygon:
    """Keep only polygonal parts (make_valid/union can return mixed collections)."""
    if geom is None or geom.is_empty:
        return Polygon()
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    return shapely.union_all(parts) if parts else Polygon()


def mesh_footprint(verts: np.ndarray, faces: np.ndarray, grid_size: float) -> Polygon | MultiPolygon:
    """XY projection of a triangle mesh: union of non-degenerate projected triangles."""
    if len(faces) == 0:
        return Polygon()
    tri = verts[faces][:, :, :2]
    area = 0.5 * np.abs(
        (tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1])
        - (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1])
    )
    tri = tri[area > 1e-10]
    if len(tri) == 0:
        return Polygon()
    polys = shapely.polygons(np.concatenate([tri, tri[:, :1]], axis=1))
    return polygonal_part(shapely.union_all(polys, grid_size=grid_size))


def door_axis(footprint) -> LineString | None:
    """Line along the long side of the door footprint's minimum rotated rectangle, through its centre."""
    if footprint.is_empty:
        return None
    rect = footprint.minimum_rotated_rectangle
    if not isinstance(rect, Polygon):
        return None
    c = np.asarray(rect.exterior.coords)[:4]
    edges = [(c[i], c[(i + 1) % 4]) for i in range(4)]
    lengths = [np.linalg.norm(b - a) for a, b in edges]
    i = int(np.argmin(lengths))
    short_a, short_b = edges[i], edges[(i + 2) % 4]
    return LineString([(short_a[0] + short_a[1]) / 2, (short_b[0] + short_b[1]) / 2])


def _info(category: str, rec: dict) -> str:
    """Short category-specific description used in map tooltips."""
    if category == "walls":
        return f"classification={rec['classification']}; openings={len(rec['openings'])}"
    if category == "openings":
        return f"filling={rec['filling_kind']}; host={(rec['host'] or {}).get('name')}"
    if category == "doors":
        return f"host_status={rec['host_relationship_status']}; width_m={rec['overall_width_m']}"
    if category == "spaces":
        return f"long_name={rec['long_name']}"
    if category == "obstacles":
        return f"obstacle={rec['obstacle_category']}"
    if category == "lifts":
        return (f"{rec['identification']}; role={rec['role']}; access_candidate={rec['is_access_point_candidate']}; "
                f"placement_outside_storey={rec['placement_z_outside_storey']}")
    if category == "slabs":
        return f"predefined={rec['predefined_type']}; floor_candidate={rec['is_floor_candidate']}"
    if category == "stairs":
        return f"parent_stair={(rec['parent_stair'] or {}).get('name')}"
    return ""


def _status_for_missing(element) -> str:
    if getattr(element, "Representation", None) is None:
        return "no_representation"
    return "no_geometry_output"


def extract_geometry(model: ifcopenshell.file, extracted: dict, storey_elevation_m: float, cfg) -> dict:
    """Build per-category GeoDataFrames (footprints) plus door points/lines and a summary."""
    walking_z = storey_elevation_m + cfg.WALKING_HEIGHT_M
    records = {c: extracted["elements"].get(c, []) for c in FOOTPRINT_CATEGORIES}

    entities, parts_of = {}, {}
    for recs in records.values():
        for rec in recs:
            e = model.by_guid(rec["global_id"])
            entities[rec["global_id"]] = e
            if rec["entity"] == "IfcCurtainWall":  # Revit curtain-wall geometry lives in aggregated plates/members
                parts_of[rec["global_id"]] = [p for rel in e.IsDecomposedBy or () for p in rel.RelatedObjects]
    to_mesh = list(entities.values()) + [p for parts in parts_of.values() for p in parts]
    meshes = iterate_meshes(model, to_mesh, cfg.GEOMETRY_THREADS)

    frames, status_counts, repairs = {}, {}, []
    door_points, door_lines, width_checks = [], [], []
    for category, recs in records.items():
        rows = []
        for rec in recs:
            gid = rec["global_id"]
            part_meshes = [meshes[p.GlobalId] for p in parts_of.get(gid, []) if p.GlobalId in meshes]
            own = meshes.get(gid)
            sources = ([own] if own is not None else []) + part_meshes
            if sources:
                footprint = polygonal_part(shapely.union_all(
                    [mesh_footprint(v, f, cfg.GEOMETRY_GRID_SIZE_M) for v, f in sources], grid_size=cfg.GEOMETRY_GRID_SIZE_M
                ))
                z = np.concatenate([v[:, 2] for v, _ in sources])
                z_min, z_max = float(z.min()), float(z.max())
                status = "ok" if own is not None else "ok_from_parts"
                if footprint.is_empty:
                    status = "empty_footprint"
            else:
                footprint, z_min, z_max = Polygon(), None, None
                status = _status_for_missing(entities[gid])
            if not footprint.is_empty and not footprint.is_valid:
                before = footprint.area
                footprint = polygonal_part(shapely.make_valid(footprint))
                repairs.append({"global_id": gid, "category": category, "area_before": before, "area_after": footprint.area})
            rows.append({
                "category": category,
                "global_id": gid,
                "entity": rec["entity"],
                "name": rec["name"],
                "storey": rec["storey_name"],
                "status": status,
                "z_min": z_min,
                "z_max": z_max,
                "covers_walking_height": None if z_min is None else bool(z_min <= walking_z <= z_max),
                "area_m2": round(footprint.area, 4),
                "info": _info(category, rec) + (
                    f"; parts_meshed={len(part_meshes)}/{len(parts_of[gid])}" if gid in parts_of else ""),
                "geometry": footprint,
            })
            if category == "doors" and not footprint.is_empty:
                axis = door_axis(footprint)
                door_points.append({"global_id": gid, "name": rec["name"], "geometry": footprint.centroid})
                if axis is not None:
                    door_lines.append({"global_id": gid, "name": rec["name"], "length_m": axis.length, "geometry": axis})
                    if rec["overall_width_m"]:
                        width_checks.append((gid, axis.length - rec["overall_width_m"]))
        frames[category] = gpd.GeoDataFrame(rows, geometry="geometry", crs=None) if rows else gpd.GeoDataFrame(
            {"geometry": []}, geometry="geometry", crs=None)
        status_counts[category] = dict(Counter(r["status"] for r in rows))

    frames["door_points"] = gpd.GeoDataFrame(door_points or {"geometry": []}, geometry="geometry", crs=None)
    frames["door_lines"] = gpd.GeoDataFrame(door_lines or {"geometry": []}, geometry="geometry", crs=None)

    non_empty = [f for f in frames.values() if len(f) and not f.geometry.is_empty.all()]
    bounds = shapely.union_all([shapely.box(*f.total_bounds) for f in non_empty]).bounds if non_empty else None
    diffs = np.asarray([d for _, d in width_checks])
    median = float(np.median(diffs)) if len(diffs) else None
    summary = {
        "coordinate_frame": "IfcOpenShell world XY, metres (local project coordinates; not geographic)",
        "walking_height_z_m": walking_z,
        "status_by_category": status_counts,
        "not_covering_walking_height": {
            c: int((f["covers_walking_height"] == False).sum()) for c, f in frames.items() if "covers_walking_height" in f  # noqa: E712
        },
        "geometry_repairs": repairs,
        "door_width_check": {  # footprint axis includes frame/trim, so compare against the typical offset
            "compared": int(len(diffs)),
            "median_axis_minus_overall_width_m": median,
            "offset_counts_m": dict(Counter(round(float(d), 2) for d in diffs).most_common()),
            "outliers": [
                {"global_id": gid, "diff_m": round(float(d), 3)}
                for gid, d in width_checks if abs(d - median) > cfg.DOOR_WIDTH_CHECK_TOLERANCE_M
            ],
        },
        "bounds_xy_m": list(bounds) if bounds else None,
    }
    return {"frames": frames, "summary": summary}
