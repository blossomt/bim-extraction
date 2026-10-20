"""Folium 2D map (Leaflet CRS.Simple, IFC world XY metres, no basemap) and optional Plotly 3D view."""

from __future__ import annotations

import html
from pathlib import Path

import folium
import numpy as np
import plotly.graph_objects as go
import shapely

SYNTHETIC_NOTE = (
    "Synthetic local coordinates: IFC world X/Y in metres displayed on a flat canvas. "
    "NOT geographic &mdash; no basemap, no real-world location implied."
)

# layer name -> (frame key, style function)
WALL_COLOURS = {"internal": "#555555", "external": "#000000", "unknown": "#d6008f"}
OPENING_COLOURS = {"door": "#2ca02c", "window": "#1f77b4", "unfilled": "#999999", "other": "#9467bd"}


def _style(fill, line=None, weight=1, opacity=0.5, dash=None):
    return lambda _f: {"fillColor": fill, "color": line or fill, "weight": weight, "fillOpacity": opacity, "dashArray": dash}


def _wall_style(feature):
    cls = feature["properties"]["info"].split(";")[0].split("=")[-1]
    colour = WALL_COLOURS.get(cls, WALL_COLOURS["unknown"])
    return {"fillColor": colour, "color": colour, "weight": 1, "fillOpacity": 0.8}


def _opening_style(feature):
    kind = feature["properties"]["info"].split(";")[0].split("=")[-1]
    colour = OPENING_COLOURS.get(kind, "#9467bd")
    return {"fillColor": colour, "color": colour, "weight": 1, "fillOpacity": 0.7}


def _lift_style(feature):
    info = feature["properties"]["info"]
    if "access_candidate=True" in info:
        misplaced = "placement_outside_storey=True" in info
        return {"fillColor": "#ff7f0e", "color": "#ff7f0e", "weight": 2, "fillOpacity": 0.3 if misplaced else 0.9,
                "dashArray": "4 4" if misplaced else None}
    return {"fillColor": "#bbbbbb", "color": "#888888", "weight": 1, "fillOpacity": 0.4}


LAYERS = [
    ("Floor footprint (derived from wall envelope)", "footprint", _style("#dddddd", "#666666", opacity=0.4)),
    ("Slabs", "slabs", _style("#cccccc", opacity=0.3)),
    ("IfcSpace polygons", "spaces", _style("#fff3b0", "#e0a800", opacity=0.5)),
    ("Walkable — naive (solid walls)", "walkable_naive", _style("#ff9896", "#d62728", opacity=0.4)),
    ("Walkable — opening-aware (door cuts)", "walkable_opening_aware", _style("#98df8a", "#2ca02c", opacity=0.5)),
    ("Walls (internal grey / external black / unknown magenta)", "walls", _wall_style),
    ("Obstacles (columns, curtain walls)", "obstacles", _style("#8c564b", opacity=0.7)),
    ("Openings (door green / window blue / unfilled grey)", "openings", _opening_style),
    ("Doors", "doors", _style("#d62728", opacity=0.6)),
    ("Door cuts applied to host walls", "door_cuts", _style("#00e5ff", "#008b9c", opacity=0.6)),
    ("Stairs", "stairs", _style("#9467bd", opacity=0.4)),
    ("Lift parts (label-only; orange = landing-door access candidate, dashed = misplaced)", "lifts", _lift_style),
    ("Network regions (rooms yellow / corridors blue)", "net_regions",
     lambda f: {"fillColor": "#ffe08a" if f["properties"]["kind"] == "room" else "#9ecae1", "color": "#666666",
                "weight": 1, "fillOpacity": 0.45}),
    ("Corridor centreline (pygeoops)", "net_centrelines", lambda _f: {"color": "#0055ff", "weight": 2, "opacity": 0.9}),
    ("Connectors (orange = room spoke, green = corridor snap, red dashed = too long / issue)", "net_connectors",
     lambda f: _connector_style(f)),
]


def _connector_style(feature):
    p = feature["properties"]
    if p["status"] != "ok" or (p.get("crosses_non_walkable_m") or 0) > 0.01:
        return {"color": "#d62728", "weight": 3, "opacity": 0.9, "dashArray": "5 4"}
    colour = "#e6550d" if p["kind"] == "room_spoke" else "#2ca02c"
    if p["source"] == "lift_landing_door":
        colour = "#9467bd"
    return {"color": colour, "weight": 2, "opacity": 0.9}

# Point layers: (label, frame key, colour, radius, row filter)
POINT_LAYERS = [
    ("Room centre nodes", "net_room_nodes", "#e6550d", 5, lambda g: g[g["doors"] > 0]),
    ("Room-like regions without doors (isolated)", "net_room_nodes", "#999999", 3, lambda g: g[g["doors"] == 0]),
    ("Doorway nodes (connected)", "net_door_access", "#2ca02c", 3,
     lambda g: g[g["connection_status"].str.startswith("connected")]),
    ("Doorway nodes (blocked / unconnected / partial)", "net_door_access", "#d62728", 5,
     lambda g: g[~g["connection_status"].str.startswith("connected")]),
    ("Lift access points (label-only landing doors, connected)", "net_lift_access", "#9467bd", 6,
     lambda g: g[g["connection_status"] == "connected"]),
    ("Lift landing doors excluded / unconnected", "net_lift_access", "#bbbbbb", 4,
     lambda g: g[g["connection_status"] != "connected"]),
]

TOOLTIP_FIELDS = ["category", "name", "global_id", "entity", "storey", "status", "info"]


def _tooltip_fields(gdf) -> list[str]:
    if "category" in gdf.columns:  # IFC element frames
        return [f for f in TOOLTIP_FIELDS if f in gdf.columns]
    return [c for c in gdf.columns if c != "geometry"]


def build_floor_map(frames: dict, storey_name: str, bounds_xy: list[float] | None, summary_lines: list[str], out_path: Path) -> Path:
    m = folium.Map(location=[0, 0], zoom_start=2, crs="Simple", tiles=None, control_scale=False)

    for label, key, style in LAYERS:
        gdf = frames.get(key)
        if gdf is None or len(gdf) == 0:
            continue
        gdf = gdf[~gdf.geometry.is_empty]
        if len(gdf) == 0:
            continue
        fields = _tooltip_fields(gdf)
        folium.GeoJson(
            gdf[fields + ["geometry"]].to_json(),
            name=f"{label} ({len(gdf)})",
            style_function=style,
            tooltip=folium.GeoJsonTooltip(fields=fields),
        ).add_to(m)

    points = frames.get("door_points")
    if points is not None and len(points):
        layer = folium.FeatureGroup(name=f"Door representative points ({len(points)})", show=False)
        for _, row in points.iterrows():
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x], radius=3, color="#d62728", fill=True,
                tooltip=html.escape(f"{row['name']} | {row['global_id']}"),
            ).add_to(layer)
        layer.add_to(m)
    lines = frames.get("door_lines")
    if lines is not None and len(lines):
        folium.GeoJson(lines.to_json(), name=f"Door axes ({len(lines)})", show=False,
                       style_function=lambda _f: {"color": "#d62728", "weight": 3}).add_to(m)

    for label, key, colour, radius, select in POINT_LAYERS:
        gdf = frames.get(key)
        if gdf is None or len(gdf) == 0:
            continue
        gdf = select(gdf)
        if len(gdf) == 0:
            continue
        fields = [c for c in gdf.columns if c != "geometry"]
        folium.GeoJson(
            gdf.to_json(), name=f"{label} ({len(gdf)})",
            marker=folium.CircleMarker(radius=radius, color=colour, fill=True, fill_opacity=1),
            tooltip=folium.GeoJsonTooltip(fields=fields),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    if bounds_xy:
        minx, miny, maxx, maxy = bounds_xy
        m.fit_bounds([[miny, minx], [maxy, maxx]])

    lines_html = "".join(f"<li>{html.escape(s)}</li>" for s in summary_lines)
    panel = (
        '<div style="position:fixed;bottom:20px;left:10px;z-index:9999;background:white;padding:8px 12px;'
        'border:1px solid #888;max-width:420px;max-height:40vh;overflow:auto;font:12px sans-serif">'
        f"<b>Storey: {html.escape(storey_name)}</b><br><i>{SYNTHETIC_NOTE}</i><ul style='margin:4px 0;padding-left:16px'>{lines_html}</ul></div>"
    )
    m.get_root().html.add_child(folium.Element(panel))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    return out_path


# --- Optional Plotly 3D view -----------------------------------------------------------------

MESH_STYLE = {  # group -> (colour, opacity)
    "walls": ("#b0b0b0", 1.0),
    "IfcColumn": ("#8c564b", 1.0),
    "IfcCurtainWall": ("#9ecae1", 0.35),
    "doors": ("#d62728", 1.0),
    "stairs": ("#9467bd", 1.0),
    "lifts": ("#ff7f0e", 1.0),
    "spaces": ("#ffe08a", 0.25),
}


def _mesh_trace(group: str, items: list[dict], visible) -> go.Mesh3d:
    verts, faces, text, offset = [], [], [], 0
    for it in items:
        v, f = it["verts"], it["faces"]
        verts.append(v)
        faces.append(f + offset)
        label = f"{it['entity']}: {(it['name'] or '')[:50]}<br>{it['global_id']}"
        if it["parent_global_id"]:
            label += f"<br>part of {it['parent_global_id']}"
        text.extend([label] * len(v))
        offset += len(v)
    v, f = np.vstack(verts), np.vstack(faces)
    colour, opacity = MESH_STYLE.get(group, ("#7f7f7f", 1.0))
    return go.Mesh3d(x=v[:, 0], y=v[:, 1], z=v[:, 2], i=f[:, 0], j=f[:, 1], k=f[:, 2], color=colour, opacity=opacity,
                     flatshading=True, name=f"{group} ({len(items)})", hovertext=text, hoverinfo="text",
                     showlegend=True, visible=visible)


def _polygon_trace(polys: list, z: float, colour: str, name: str, labels: list[str]) -> go.Mesh3d | None:
    xs, ys, ii, jj, kk, text, offset = [], [], [], [], [], [], 0
    for poly, label in zip(polys, labels):
        for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(poly)):
            c = np.asarray(tri.exterior.coords)[:3]
            xs.extend(c[:, 0]); ys.extend(c[:, 1])
            ii.append(offset); jj.append(offset + 1); kk.append(offset + 2)
            text.extend([label] * 3)
            offset += 3
    if not xs:
        return None
    return go.Mesh3d(x=xs, y=ys, z=[z] * len(xs), i=ii, j=jj, k=kk, color=colour, opacity=0.55, name=name,
                     hovertext=text, hoverinfo="text", showlegend=True)


def _line_trace(geoms, z: float, colour: str, name: str, width: int = 4, dash=None) -> go.Scatter3d | None:
    xs, ys = [], []
    for g in geoms:
        for part in shapely.get_parts(g):
            c = np.asarray(part.coords)
            xs.extend(list(c[:, 0]) + [None]); ys.extend(list(c[:, 1]) + [None])
    if not xs:
        return None
    return go.Scatter3d(x=xs, y=ys, z=[z if x is not None else None for x in xs], mode="lines", name=name,
                        line={"color": colour, "width": width, "dash": dash}, hoverinfo="skip")


def _point_trace(gdf, z: float, colour: str, name: str, label_cols: list[str], size: int = 4) -> go.Scatter3d | None:
    if gdf is None or len(gdf) == 0:
        return None
    text = ["<br>".join(f"{c}: {row[c]}" for c in label_cols) for _, row in gdf.iterrows()]
    return go.Scatter3d(x=gdf.geometry.x, y=gdf.geometry.y, z=[z] * len(gdf), mode="markers", name=f"{name} ({len(gdf)})",
                        marker={"size": size, "color": colour}, hovertext=text, hoverinfo="text")


def build_3d_view(meshes: dict, network: dict, connectors: dict, storey_name: str, elevation_m: float,
                  hidden: list[str], out_path: Path) -> Path:
    traces = [_mesh_trace(group, items, "legendonly" if group in hidden else True)
              for group, items in meshes.items() if items]

    z_floor, z_net = elevation_m + 0.02, elevation_m + 0.1
    regions = network["regions"]
    for kind, colour in (("room", "#ffe08a"), ("corridor", "#9ecae1")):
        sel = regions[regions["kind"] == kind] if len(regions) else regions
        labels = [f"{r.region_id} ({kind}) {r.area_m2} m2, doors {r.doors}" for r in sel.itertuples()]
        traces.append(_polygon_trace(list(sel.geometry), z_floor, colour, f"walkable {kind} regions ({len(sel)})", labels))
    traces.append(_line_trace(network["centrelines"].geometry, z_net, "#0055ff", "corridor centreline"))
    conn = connectors["connectors"]
    if len(conn):
        traces.append(_line_trace(conn[conn["kind"] == "room_spoke"].geometry, z_net, "#e6550d", "room spokes", 3))
        traces.append(_line_trace(conn[conn["kind"] == "corridor_snap"].geometry, z_net, "#2ca02c", "corridor snaps", 3))
    traces.append(_point_trace(network["room_nodes"], z_net, "#e6550d", "room nodes", ["node_id", "doors"]))
    traces.append(_point_trace(connectors["door_access"], z_net, "#d62728", "doorway nodes",
                               ["name", "door_global_id", "connection_status"], 3))
    traces.append(_point_trace(connectors["lift_access"], z_net, "#9467bd", "lift access (label-only)",
                               ["name", "lift_global_id", "connection_status"], 6))

    fig = go.Figure([t for t in traces if t is not None])
    fig.update_layout(
        title=f"Storey {storey_name} — 3D view",
        annotations=[{"text": "IFC world coordinates in metres (synthetic local frame, not geographic). "
                              "Click legend entries to show/hide; drag to rotate.",
                      "xref": "paper", "yref": "paper", "x": 0, "y": 1.0, "showarrow": False, "font": {"size": 11}}],
        scene={"aspectmode": "data", "xaxis_title": "X (m)", "yaxis_title": "Y (m)", "zaxis_title": "Z (m)"},
        legend={"itemsizing": "constant"},
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs="cdn")
    return out_path
