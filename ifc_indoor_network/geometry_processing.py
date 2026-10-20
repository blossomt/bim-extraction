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


def mesh_settings(subtract_openings: bool = False) -> ifcopenshell.geom.settings:
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    s.set("disable-opening-subtractions", not subtract_openings)
    return s


def iterate_meshes(model: ifcopenshell.file, elements: list, num_threads: int,
                   subtract_openings: bool = False) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{GlobalId: (verts (n,3) metres, faces (m,3))}. Elements without output are simply absent."""
    meshes: dict = {}
    if not elements:
        return meshes
    it = ifcopenshell.geom.iterator(mesh_settings(subtract_openings), model, num_threads, include=elements)
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
            # Parts above/below walking height (e.g. transoms over doors) are not walking obstacles.
            walking_parts = [(v, f) for v, f in part_meshes if v[:, 2].min() <= walking_z <= v[:, 2].max()]
            own = meshes.get(gid)
            sources = ([own] if own is not None else []) + part_meshes
            if sources:
                footprint = polygonal_part(shapely.union_all(
                    [mesh_footprint(v, f, cfg.GEOMETRY_GRID_SIZE_M) for v, f in ([own] if own is not None else []) + walking_parts],
                    grid_size=cfg.GEOMETRY_GRID_SIZE_M,
                ))
                z = np.concatenate([v[:, 2] for v, _ in sources])
                z_min, z_max = float(z.min()), float(z.max())
                status = "ok" if own is not None else "ok_from_parts"
                if footprint.is_empty:
                    status = "no_parts_at_walking_height" if part_meshes and not walking_parts and own is None else "empty_footprint"
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
                    f"; parts_meshed={len(part_meshes)}/{len(parts_of[gid])}; parts_at_walking_height={len(walking_parts)}"
                    if gid in parts_of else ""),
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


def collect_display_meshes(model: ifcopenshell.file, extracted: dict, categories: list[str], cfg) -> dict:
    """3D display meshes (openings subtracted) per category; elements without own geometry use their parts."""
    items = []  # (category, record, element)
    for category in categories:
        for rec in extracted["elements"].get(category, []):
            items.append((category, rec, model.by_guid(rec["global_id"])))
    parts_of = {rec["global_id"]: [p for rel in e.IsDecomposedBy or () for p in rel.RelatedObjects] for _, rec, e in items}
    to_mesh = [e for _, _, e in items] + [p for parts in parts_of.values() for p in parts]
    meshes = iterate_meshes(model, to_mesh, cfg.GEOMETRY_THREADS, subtract_openings=True)

    out: dict[str, list] = {}
    drawn: set[str] = set()
    for category, rec, e in items:
        gid = rec["global_id"]
        group = rec["entity"] if category == "obstacles" else category
        sources = [(gid, e)] if gid in meshes else [(p.GlobalId, p) for p in parts_of[gid] if p.GlobalId in meshes]
        for sgid, src in sources:
            if sgid in drawn:
                continue
            drawn.add(sgid)
            verts, faces = meshes[sgid]
            out.setdefault(group, []).append({
                "global_id": sgid, "name": getattr(src, "Name", None), "entity": src.is_a(),
                "parent_global_id": gid if sgid != gid else None, "verts": verts, "faces": faces,
            })
    return out


# --- Phase 4: floor footprint and walkable polygons ----------------------------------------


def _active(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rows with geometry that span walking height."""
    if len(gdf) == 0 or "covers_walking_height" not in gdf:
        return gdf.iloc[0:0]
    return gdf[(gdf["covers_walking_height"] == True) & ~gdf.geometry.is_empty]  # noqa: E712


def fill_outer_rings(geom) -> Polygon | MultiPolygon:
    return polygonal_part(shapely.union_all([Polygon(p.exterior) for p in shapely.get_parts(polygonal_part(geom))]))


def clean_polygons(geom, min_area: float, min_mean_width: float = 0.0) -> tuple[Polygon | MultiPolygon, dict]:
    """make_valid if needed, keep polygonal parts, drop tiny or very thin components. Returns geometry + log."""
    repaired = not geom.is_valid
    if repaired:
        geom = shapely.make_valid(geom)
    parts = list(shapely.get_parts(polygonal_part(geom)))
    small = [p for p in parts if p.area < min_area]
    thin = [p for p in parts if p.area >= min_area and 2 * p.area / p.length < min_mean_width]
    keep = [p for p in parts if p.area >= min_area and 2 * p.area / p.length >= min_mean_width]
    log = {
        "repaired": repaired,
        "slivers_removed": len(small),
        "sliver_area_m2": round(sum(p.area for p in small), 4),
        "thin_removed": len(thin),
        "thin_area_m2": round(sum(p.area for p in thin), 4),
    }
    return (shapely.union_all(keep) if keep else Polygon()), log


def _components_frame(geom, variant: str) -> gpd.GeoDataFrame:
    parts = sorted(shapely.get_parts(geom), key=lambda p: -p.area) if not geom.is_empty else []
    rows = [{
        "component_id": f"{variant[0].upper()}{i:03d}",  # ordered by descending area
        "variant": variant,
        "area_m2": round(p.area, 3),
        "holes": len(p.interiors),
        "geometry": p,
    } for i, p in enumerate(parts)]
    return gpd.GeoDataFrame(rows or {"geometry": []}, geometry="geometry", crs=None)


def _polygon_stats(geom) -> dict:
    parts = list(shapely.get_parts(geom)) if not geom.is_empty else []
    return {"area_m2": round(geom.area, 3), "components": len(parts), "holes": sum(len(p.interiors) for p in parts)}


def build_floor_plan(frames: dict, extracted: dict, cfg) -> dict:
    """Footprint from the wall envelope (fallback: floor slabs), then naive and opening-aware walkable polygons."""
    grid = cfg.GEOMETRY_GRID_SIZE_M
    walls = _active(frames["walls"])
    obstacles = _active(frames["obstacles"])
    curtain = obstacles[obstacles["entity"] == "IfcCurtainWall"] if len(obstacles) else obstacles

    # Footprint
    envelope = shapely.union_all(list(walls.geometry) + list(curtain.geometry), grid_size=grid)
    c = cfg.ENVELOPE_CLOSING_M
    closed = envelope.buffer(c, join_style="mitre").buffer(-c, join_style="mitre") if c > 0 and not envelope.is_empty else envelope
    footprint = fill_outer_rings(closed)
    source, reason = "derived_from_wall_envelope", None
    if footprint.is_empty:
        floor_ids = {s["global_id"] for s in extracted["elements"]["slabs"] if s["is_floor_candidate"]}
        slabs = frames["slabs"]
        slab_geoms = [g for gid, g in zip(slabs.get("global_id", []), slabs.geometry) if gid in floor_ids and not g.is_empty]
        footprint = polygonal_part(shapely.union_all(slab_geoms)) if slab_geoms else Polygon()
        source = "floor_slabs" if not footprint.is_empty else "unavailable"
        reason = None if slab_geoms else "No wall/curtain-wall envelope spanning walking height and no FLOOR/BASESLAB slabs."

    # Door cuts: only explicit door-filled openings (host via IfcRelVoidsElement) and doors aggregated in curtain walls.
    opening_recs = {o["global_id"]: o for o in extracted["elements"]["openings"]}
    active_openings = _active(frames["openings"])
    opening_geom = dict(zip(active_openings["global_id"], active_openings.geometry)) if len(active_openings) else {}
    door_frame = frames["doors"]
    door_geom = dict(zip(door_frame["global_id"], door_frame.geometry)) if len(door_frame) else {}
    cuts_by_host: dict[str, list] = {}
    cut_rows, uncut = [], []
    for door in extracted["elements"]["doors"]:
        gid, cut, host, kind = door["global_id"], None, None, None
        if door["opening"] and door["host_element"]:
            op = opening_recs.get(door["opening"]["global_id"])
            if op and op["filling_kind"] == "door" and op["global_id"] in opening_geom:
                cut, host, kind = opening_geom[op["global_id"]], door["host_element"]["global_id"], "door_filled_opening"
            else:
                uncut.append({"global_id": gid, "reason": "opening has no geometry spanning walking height"})
        elif (cfg.CUT_CURTAIN_WALL_DOORS and door["aggregated_in"] and door["aggregated_in"]["entity"] == "IfcCurtainWall"
              and gid in door_geom and not door_geom[gid].is_empty):
            cut, host, kind = door_geom[gid], door["aggregated_in"]["global_id"], "door_in_curtain_wall"
        else:
            uncut.append({"global_id": gid, "reason": door["host_relationship_status"]})
        if cut is not None:
            cuts_by_host.setdefault(host, []).append(cut)
            cut_rows.append({"door_global_id": gid, "door_name": door["name"], "host_global_id": host, "cut_kind": kind, "geometry": cut})
    door_cuts = gpd.GeoDataFrame(cut_rows or {"geometry": []}, geometry="geometry", crs=None)

    # Obstacles: naive = solid; opening-aware = each host minus its own door cuts.
    obstacle_rows = _id_geoms(walls, obstacles)
    naive_obstacles = shapely.union_all([g for _, g in obstacle_rows], grid_size=grid)
    aware_parts = [
        g.difference(shapely.union_all(cuts_by_host[gid])) if gid in cuts_by_host else g for gid, g in obstacle_rows
    ]
    aware_obstacles = shapely.union_all(aware_parts, grid_size=grid)

    walkable, logs = {}, {}
    for variant, obst in (("naive", naive_obstacles), ("opening_aware", aware_obstacles)):
        raw = footprint.difference(obst, grid_size=grid) if not footprint.is_empty else Polygon()
        walkable[variant], logs[variant] = clean_polygons(raw, cfg.MIN_COMPONENT_AREA_M2, cfg.MIN_COMPONENT_MEAN_WIDTH_M)

    # Doorways that stay blocked in the opening-aware result (e.g. another, overlapping element not voided by the opening).
    blocked = []
    for row in cut_rows:
        inside = row["geometry"].intersection(footprint)
        if inside.area <= 0:
            blocked.append({"door_global_id": row["door_global_id"], "reason": "cut outside footprint"})
            continue
        frac = inside.intersection(aware_obstacles).area / inside.area
        if frac > cfg.DOORWAY_BLOCKED_FRACTION:
            blockers = [gid for gid, g in obstacle_rows if gid != row["host_global_id"] and g.intersection(inside).area > 1e-4]
            blocked.append({"door_global_id": row["door_global_id"], "covered_fraction": round(frac, 3), "blocking_elements": blockers})

    spaces = frames["spaces"]
    spaces_union = shapely.union_all([g for g in spaces.geometry if not g.is_empty]) if len(spaces) else Polygon()
    not_active = {
        name: int((~frames[name].index.isin(_active(frames[name]).index) & ~frames[name].geometry.is_empty).sum())
        for name in ("walls", "obstacles", "openings") if len(frames[name])
    }
    fa = footprint.area
    summary = {
        "footprint_source": source,
        "footprint_unavailable_reason": reason,
        "envelope_closing_m": c,
        "footprint": _polygon_stats(footprint),
        "obstacles_used": {"walls": len(walls), "obstacles": len(obstacles)},
        "excluded_not_spanning_walking_height": not_active,
        "door_cuts": dict(Counter(r["cut_kind"] for r in cut_rows)),
        "doors_without_cut": uncut,
        "doorways_still_blocked": blocked,
        "walkable": {
            v: {**_polygon_stats(walkable[v]), **logs[v],
                "area_lost_vs_footprint_m2": round(fa - walkable[v].area, 3),
                "area_lost_pct": round(100 * (fa - walkable[v].area) / fa, 2) if fa else None}
            for v in walkable
        },
        "spaces_comparison": {
            "spaces_area_m2": round(spaces_union.area, 3),
            "spaces_area_outside_footprint_m2": round(spaces_union.difference(footprint).area, 3) if not footprint.is_empty else None,
            "footprint_area_not_in_spaces_m2": round(footprint.difference(spaces_union).area, 3) if not footprint.is_empty else None,
        },
    }
    out_frames = {
        "footprint": gpd.GeoDataFrame([{"source": source, "area_m2": round(fa, 3), "geometry": footprint}], geometry="geometry", crs=None),
        "door_cuts": door_cuts,
        "walkable_naive": _components_frame(walkable["naive"], "naive"),
        "walkable_opening_aware": _components_frame(walkable["opening_aware"], "opening_aware"),
    }
    return {"frames": out_frames, "geometries": {"footprint": footprint, **{f"walkable_{k}": v for k, v in walkable.items()}}, "summary": summary}


def _id_geoms(*frames) -> list[tuple[str, object]]:
    return [(gid, g) for f in frames if len(f) for gid, g in zip(f["global_id"], f.geometry)]

