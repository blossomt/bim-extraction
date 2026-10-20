"""Folium 2D map (Leaflet CRS.Simple, IFC world XY metres, no basemap) and optional Plotly 3D view."""

from __future__ import annotations

import html
from pathlib import Path

import folium
import numpy as np
import plotly.graph_objects as go
import shapely

SYNTHETIC_NOTE = (
    "Synthetic local coordinates (IFC world X/Y, metres) on a blank canvas — not geographic."
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


REGION_COLOURS = {"room": "#ffe08a", "corridor": "#9ecae1"}

# (layer name, frame key, style function, shown by default). Prefixes group layers in the layer control.
LAYERS = [
    ("Analysis · Floor footprint (wall envelope)", "footprint", _style("#dddddd", "#666666", opacity=0.4), False),
    ("IFC · Slabs", "slabs", _style("#cccccc", opacity=0.3), False),
    ("IFC · IfcSpace polygons", "spaces", _style("#fff3b0", "#e0a800", opacity=0.5), False),
    ("Analysis · Walkable, naive (solid walls)", "walkable_naive", _style("#ff9896", "#d62728", opacity=0.4), False),
    ("Analysis · Walkable, opening-aware", "walkable_opening_aware", _style("#98df8a", "#2ca02c", opacity=0.5), False),
    ("Network · Regions (rooms / corridors)", "net_regions",
     lambda f: {"fillColor": REGION_COLOURS.get(f["properties"]["kind"], "#cccccc"), "color": "#999999",
                "weight": 0.5, "fillOpacity": 0.55}, True),
    ("IFC · Walls", "walls", _wall_style, True),
    ("IFC · Columns & curtain walls", "obstacles", _style("#8c564b", opacity=0.7), True),
    ("IFC · Openings (door / window / unfilled)", "openings", _opening_style, False),
    ("IFC · Doors", "doors", _style("#d62728", opacity=0.6), True),
    ("Analysis · Door cuts in host walls", "door_cuts", _style("#00e5ff", "#008b9c", opacity=0.6), False),
    ("IFC · Stairs", "stairs", _style("#9467bd", opacity=0.4), True),
    ("IFC · Lift parts (label-only)", "lifts", _lift_style, True),
    ("Network · Corridor centreline (pygeoops)", "net_centrelines", lambda _f: {"color": "#0055ff", "weight": 2.5, "opacity": 0.9}, True),
    ("Network · Connectors", "net_connectors", lambda f: _connector_style(f), True),
]


def _connector_style(feature):
    p = feature["properties"]
    if p["status"] != "ok" or (p.get("crosses_non_walkable_m") or 0) > 0.01:
        return {"color": "#d62728", "weight": 3, "opacity": 0.9, "dashArray": "5 4"}
    colour = "#e6550d" if p["kind"] == "room_spoke" else "#2ca02c"
    if p["source"] == "lift_landing_door":
        colour = "#9467bd"
    return {"color": colour, "weight": 2, "opacity": 0.9}


# (layer name, frame key, colour, radius, row filter, shown by default)
POINT_LAYERS = [
    ("Network · Room nodes", "net_room_nodes", "#e6550d", 4, lambda g: g[g["doors"] > 0], True),
    ("Network · Doorless room-like regions", "net_room_nodes", "#999999", 3, lambda g: g[g["doors"] == 0], False),
    ("Network · Doorway nodes", "net_door_access", "#2ca02c", 3,
     lambda g: g[g["connection_status"].str.startswith("connected")], True),
    ("Network · Blocked / unconnected doorways", "net_door_access", "#d62728", 6,
     lambda g: g[~g["connection_status"].str.startswith("connected")], True),
    ("Network · Lift access (label-only landing doors)", "net_lift_access", "#9467bd", 6,
     lambda g: g[g["connection_status"] == "connected"], True),
    ("Network · Lift landing doors excluded (misplaced)", "net_lift_access", "#bbbbbb", 4,
     lambda g: g[g["connection_status"] != "connected"], False),
]

ELEMENT_POPUP_FIELDS = ["name", "global_id", "entity", "storey", "status", "info", "properties"]

LEGEND = [  # (swatch kind, colour, label)
    ("box", REGION_COLOURS["room"], "Room region (one centre node)"),
    ("box", REGION_COLOURS["corridor"], "Corridor region (centreline)"),
    ("box", WALL_COLOURS["internal"], "Internal wall"),
    ("box", WALL_COLOURS["external"], "External wall"),
    ("box", "#8c564b", "Column / curtain wall"),
    ("box", "#d62728", "Door (IfcDoor)"),
    ("box", "#9467bd", "Stair"),
    ("box", "#ff7f0e", "Lift landing door (label-only)"),
    ("line", "#0055ff", "Corridor centreline (pygeoops)"),
    ("line", "#e6550d", "Door \u2192 room node"),
    ("line", "#2ca02c", "Door \u2192 corridor centreline"),
    ("line", "#9467bd", "Lift \u2192 network"),
    ("dash", "#d62728", "Connector issue (too long / crosses obstacle)"),
    ("dot", "#e6550d", "Room node"),
    ("dot", "#2ca02c", "Doorway node"),
    ("dot", "#d62728", "Blocked doorway"),
]


def _legend_html() -> str:
    rows = []
    for kind, colour, label in LEGEND:
        if kind == "box":
            sw = f"<span style='display:inline-block;width:14px;height:10px;background:{colour};border:1px solid #777'></span>"
        elif kind == "dot":
            sw = f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;background:{colour};margin:0 2px'></span>"
        else:
            style = "dashed" if kind == "dash" else "solid"
            sw = f"<span style='display:inline-block;width:14px;border-top:3px {style} {colour};vertical-align:middle'></span>"
        rows.append(f"<div style='margin:1px 0'>{sw}&nbsp;{html.escape(label)}</div>")
    return "".join(rows)


def _panel(css: str, body: str) -> folium.Element:
    return folium.Element(
        f"<div style='position:fixed;z-index:9999;background:rgba(255,255,255,0.92);padding:6px 10px;"
        f"border:1px solid #bbb;border-radius:4px;font:11px/1.35 sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.15);{css}'>"
        f"{body}</div>"
    )


def build_floor_map(frames: dict, storey_name: str, bounds_xy: list[float] | None, summary_lines: list[str], out_path: Path) -> Path:
    m = folium.Map(location=[0, 0], zoom_start=2, crs="Simple", tiles=None, control_scale=False, prefer_canvas=True)
    m.get_root().header.add_child(folium.Element("<style>.leaflet-container{background:#f4f4f2}</style>"))

    for label, key, style, show in LAYERS:
        gdf = frames.get(key)
        if gdf is None or len(gdf) == 0:
            continue
        gdf = gdf[~gdf.geometry.is_empty]
        if len(gdf) == 0:
            continue
        is_element = "category" in gdf.columns
        fields = [f for f in ELEMENT_POPUP_FIELDS if f in gdf.columns] if is_element else [c for c in gdf.columns if c != "geometry"]
        folium.GeoJson(
            gdf[fields + ["geometry"]].to_json(), name=f"{label} ({len(gdf)})", show=show, style_function=style,
            tooltip=folium.GeoJsonTooltip(fields=["name"] if is_element else fields[:2], labels=not is_element),
            popup=folium.GeoJsonPopup(fields=fields, max_width=420),
        ).add_to(m)

    points = frames.get("door_points")
    if points is not None and len(points):
        folium.GeoJson(points.to_json(), name=f"Analysis · Door footprint centres ({len(points)})", show=False,
                       marker=folium.CircleMarker(radius=2, color="#d62728", fill=True)).add_to(m)
    lines = frames.get("door_lines")
    if lines is not None and len(lines):
        folium.GeoJson(lines.to_json(), name=f"Analysis · Door axes ({len(lines)})", show=False,
                       style_function=lambda _f: {"color": "#d62728", "weight": 3}).add_to(m)

    for label, key, colour, radius, select, show in POINT_LAYERS:
        gdf = frames.get(key)
        if gdf is None or len(gdf) == 0:
            continue
        gdf = select(gdf)
        if len(gdf) == 0:
            continue
        fields = [c for c in gdf.columns if c != "geometry"]
        folium.GeoJson(
            gdf.to_json(), name=f"{label} ({len(gdf)})", show=show,
            marker=folium.CircleMarker(radius=radius, color="white", weight=1, fill=True, fill_color=colour, fill_opacity=1),
            tooltip=folium.GeoJsonTooltip(fields=fields[:2]),
            popup=folium.GeoJsonPopup(fields=fields, max_width=420),
        ).add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    if bounds_xy:
        minx, miny, maxx, maxy = bounds_xy
        m.fit_bounds([[miny, minx], [maxy, maxx]])

    root = m.get_root().html
    root.add_child(_panel(
        "top:10px;left:60px;max-width:46vw",
        f"<b style='font-size:13px'>{html.escape(storey_name)} — indoor walking network (prototype)</b><br>"
        f"<i style='color:#555'>{SYNTHETIC_NOTE}</i>",
    ))
    root.add_child(_panel("bottom:20px;right:10px;max-height:55vh;overflow:auto",
                          f"<details open><summary><b>Legend</b></summary>{_legend_html()}</details>"))
    lines_html = "".join(f"<li>{html.escape(s)}</li>" for s in summary_lines)
    root.add_child(_panel(
        "bottom:20px;left:10px;max-width:340px;max-height:45vh;overflow:auto",
        f"<details open><summary><b>Processing summary</b></summary>"
        f"<ul style='margin:4px 0;padding-left:16px'>{lines_html}</ul>"
        f"<div style='color:#666'>Click any element for IFC GlobalId, properties and status. "
        f"Layers (top right): IFC = model elements, Analysis = intermediate geometry, Network = derived walking graph.</div>"
        f"</details>",
    ))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    return out_path


# --- Optional Plotly 3D view -----------------------------------------------------------------

MESH_STYLE = {  # group -> (colour, opacity); translucent walls keep the floor-level network visible
    "walls": ("#9a9a9a", 0.3),
    "IfcColumn": ("#8c564b", 0.8),
    "IfcCurtainWall": ("#9ecae1", 0.25),
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
        label = f"{it['entity']}: {(it['name'] or '')[:40]}<br>{it['global_id']}"
        text.extend([label] * len(v))
        offset += len(v)
    v, f = np.vstack(verts).astype(np.float32), np.vstack(faces).astype(np.int32)
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
                  hidden: list[str], out_path: Path, z_clip_m: float | None = None) -> Path:
    traces = [_mesh_trace(group, items, "legendonly" if group in hidden else True)
              for group, items in meshes.items() if items]
    for t in traces:
        t.update(legendgroup="ifc", legendgrouptitle_text="IFC elements")

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
    rooms = network["room_nodes"]
    traces.append(_point_trace(rooms[rooms["doors"] > 0] if len(rooms) else rooms, z_net, "#e6550d", "room nodes",
                               ["node_id", "doors"]))
    traces.append(_point_trace(connectors["door_access"], z_net, "#d62728", "doorway nodes",
                               ["name", "door_global_id", "connection_status"], 3))
    traces.append(_point_trace(connectors["lift_access"], z_net, "#9467bd", "lift access (label-only)",
                               ["name", "lift_global_id", "connection_status"], 6))
    for t in traces[len([g for g, items in meshes.items() if items]):]:
        if t is not None:
            t.update(legendgroup="network", legendgrouptitle_text="Walking network")

    note = "IFC world coordinates in metres (synthetic local frame, not geographic). Legend toggles layers; drag to rotate."
    zaxis = {"title": "Z (m)"}
    if z_clip_m is not None:
        zaxis["range"] = [elevation_m - 0.5, z_clip_m]
        note += f" View clipped at Z = {z_clip_m:.1f} m (next storey); some elements extend higher."
    axis_style = {"backgroundcolor": "#fafafa", "gridcolor": "#e5e5e5", "showspikes": False}

    fig = go.Figure([t for t in traces if t is not None])
    fig.update_layout(
        title=f"{storey_name} — IFC elements and derived walking network (3D)",
        annotations=[{"text": note, "xref": "paper", "yref": "paper", "x": 0, "y": 1.0, "showarrow": False,
                      "font": {"size": 11, "color": "#555"}}],
        scene={
            "aspectmode": "data",
            "xaxis": {"title": "X (m)", **axis_style},
            "yaxis": {"title": "Y (m)", **axis_style},
            "zaxis": {**zaxis, **axis_style},
            "camera": {"eye": {"x": 0.55, "y": -1.35, "z": 1.6}},
        },
        legend={"itemsizing": "constant", "groupclick": "toggleitem", "bgcolor": "rgba(255,255,255,0.8)"},
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
        paper_bgcolor="white",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs="cdn")
    return out_path, fig


# --- Static 2D figure (for reports / README; needs kaleido + Chrome for PNG export) ---------------


def _fill_trace(geoms, colour: str, name: str, line_colour: str | None = None, opacity: float = 1.0) -> go.Scatter | None:
    xs, ys = [], []
    for g in geoms:
        for poly in shapely.get_parts(g):
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            c = np.asarray(poly.exterior.coords)
            xs.extend(list(c[:, 0]) + [None]); ys.extend(list(c[:, 1]) + [None])
    if not xs:
        return None
    return go.Scatter(x=xs, y=ys, fill="toself", fillcolor=colour, mode="lines", opacity=opacity, name=name,
                      line={"color": line_colour or colour, "width": 0.5}, hoverinfo="skip")


def build_2d_figure(frames: dict, storey_name: str) -> go.Figure:
    regions = frames["net_regions"]
    walls = frames["walls"]
    conn = frames["net_connectors"]
    doors = frames["net_door_access"]
    traces = [
        _fill_trace(regions[regions["kind"] == "room"].geometry, REGION_COLOURS["room"], "room region"),
        _fill_trace(regions[regions["kind"] == "corridor"].geometry, REGION_COLOURS["corridor"], "corridor region"),
        _fill_trace(walls.geometry, WALL_COLOURS["internal"], "walls"),
        _fill_trace(frames["obstacles"].geometry, "#8c564b", "columns / curtain walls"),
        _fill_trace(frames["stairs"].geometry, "#9467bd", "stairs", opacity=0.6),
    ]
    for kind, colour, label in (("room_spoke", "#e6550d", "door \u2192 room node"),
                                ("corridor_snap", "#2ca02c", "door \u2192 corridor centreline")):
        t = _line_trace(conn[conn["kind"] == kind].geometry, 0, colour, label, 2) if len(conn) else None
        if t is not None:
            traces.append(go.Scatter(x=t.x, y=t.y, mode="lines", name=label, line={"color": colour, "width": 1.5}))
    cl = _line_trace(frames["net_centrelines"].geometry, 0, "#0055ff", "corridor centreline")
    if cl is not None:
        traces.append(go.Scatter(x=cl.x, y=cl.y, mode="lines", name="corridor centreline (pygeoops)",
                                 line={"color": "#0055ff", "width": 2}))
    nodes = frames["net_room_nodes"]
    nodes = nodes[nodes["doors"] > 0]
    traces.append(go.Scatter(x=nodes.geometry.x, y=nodes.geometry.y, mode="markers", name="room node",
                             marker={"color": "#e6550d", "size": 6, "line": {"color": "white", "width": 1}}))
    ok = doors[doors["connection_status"].str.startswith("connected")]
    bad = doors[~doors["connection_status"].str.startswith("connected")]
    traces.append(go.Scatter(x=ok.geometry.x, y=ok.geometry.y, mode="markers", name="doorway node",
                             marker={"color": "#2ca02c", "size": 4}))
    if len(bad):
        traces.append(go.Scatter(x=bad.geometry.x, y=bad.geometry.y, mode="markers", name="blocked doorway",
                                 marker={"color": "#d62728", "size": 9, "symbol": "x"}))
    lifts = frames["net_lift_access"]
    lifts = lifts[lifts["connection_status"] == "connected"]
    if len(lifts):
        traces.append(go.Scatter(x=lifts.geometry.x, y=lifts.geometry.y, mode="markers", name="lift access (label-only)",
                                 marker={"color": "#9467bd", "size": 9, "symbol": "square"}))
    fig = go.Figure([t for t in traces if t is not None])
    fig.update_layout(
        title=f"{storey_name} \u2014 derived walking network (synthetic local coordinates, metres)",
        xaxis={"title": "X (m)", "scaleanchor": "y", "showgrid": False, "zeroline": False},
        yaxis={"title": "Y (m)", "showgrid": False, "zeroline": False},
        plot_bgcolor="white", paper_bgcolor="white", legend={"bgcolor": "rgba(255,255,255,0.85)"},
        margin={"l": 50, "r": 10, "t": 50, "b": 40},
    )
    return fig


def export_png(fig: go.Figure, path: Path, width: int, height: int, scale: float = 1.5) -> Path:
    fig.write_image(str(path), width=width, height=height, scale=scale)
    return path
