"""Folium 2D map (Leaflet CRS.Simple, IFC world XY metres, no basemap) and optional Plotly 3D view."""

from __future__ import annotations

import base64
import html
from pathlib import Path

import folium
import numpy as np
import plotly.graph_objects as go
import shapely

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


def _offline_map_header() -> str:
    parts = []
    for library in ("leaflet", "jquery", "bootstrap"):
        license_text = (VENDOR_DIR / f"LICENSE-{library}").read_text(encoding="utf-8")
        parts.append(f"<!--\n{license_text}\n-->")
    for filename in ("leaflet.css", "bootstrap.min.css"):
        css = (VENDOR_DIR / filename).read_text(encoding="utf-8")
        if filename == "leaflet.css":
            for image in ("layers.png", "layers-2x.png", "marker-icon.png"):
                data = base64.b64encode((VENDOR_DIR / "images" / image).read_bytes()).decode("ascii")
                css = css.replace(f"images/{image}", f"data:image/png;base64,{data}")
        css = "\n".join(line for line in css.splitlines() if "sourceMappingURL=" not in line)
        parts.append(f"<style>{css}</style>")
    for filename in ("leaflet.js", "jquery.min.js"):
        js = (VENDOR_DIR / filename).read_text(encoding="utf-8")
        js = "\n".join(line for line in js.splitlines() if "sourceMappingURL=" not in line)
        parts.append(f"<script>{js}</script>")
    return "\n".join(parts)

# layer name -> (frame key, style function)
WALL_COLOURS = {"internal": "#555555", "external": "#000000", "unknown": "#d6008f"}


def _style(fill, line=None, weight=1, opacity=0.5, dash=None):
    return lambda _f: {"fillColor": fill, "color": line or fill, "weight": weight, "fillOpacity": opacity, "dashArray": dash}


def _wall_style(feature):
    cls = feature["properties"]["info"].split(";")[0].split("=")[-1]
    colour = WALL_COLOURS.get(cls, WALL_COLOURS["unknown"])
    return {"fillColor": colour, "color": colour, "weight": 1, "fillOpacity": 0.8}


def _lift_style(feature):
    info = feature["properties"]["info"]
    if "access_candidate=True" in info:
        misplaced = "placement_outside_storey=True" in info
        return {"fillColor": "#ff7f0e", "color": "#ff7f0e", "weight": 2, "fillOpacity": 0.3 if misplaced else 0.9,
                "dashArray": "4 4" if misplaced else None}
    return {"fillColor": "#bbbbbb", "color": "#888888", "weight": 1, "fillOpacity": 0.4}


REGION_COLOURS = {"room": "#ffe08a", "corridor": "#9ecae1"}


def _connector_style(feature):
    p = feature["properties"]
    if p["status"] != "ok" or (p.get("crosses_non_walkable_m") or 0) > 0.01:
        return {"color": "#d62728", "weight": 3, "opacity": 0.9, "dashArray": "5 4"}
    colour = "#e6550d" if p["kind"] == "room_spoke" else "#2ca02c"
    if p["source"] == "lift_landing_door":
        colour = "#9467bd"
    return {"color": colour, "weight": 2, "opacity": 0.9}


# (layer name, frame key, style function, shown by default). Prefixes group layers in the layer control.
LAYERS = [
    ("Analysis · Walkable area", "walkable", _style("#98df8a", "#2ca02c", opacity=0.5), False),
    ("Network · Regions (rooms / corridors)", "net_regions",
     lambda f: {"fillColor": REGION_COLOURS.get(f["properties"]["kind"], "#cccccc"), "color": "#999999",
                "weight": 0.5, "fillOpacity": 0.55}, True),
    ("IFC · Walls", "walls", _wall_style, True),
    ("IFC · Columns & curtain walls", "obstacles", _style("#8c564b", opacity=0.7), True),
    ("IFC · Doors", "doors", _style("#d62728", opacity=0.6), True),
    ("Analysis · Door cuts in host walls", "door_cuts", _style("#00e5ff", "#008b9c", opacity=0.6), False),
    ("IFC · Lift parts (label-only)", "lifts", _lift_style, True),
    ("Network · Corridor centreline (pygeoops)", "net_centrelines", lambda _f: {"color": "#0055ff", "weight": 2.5, "opacity": 0.9}, True),
    ("Network · Connectors", "net_connectors", _connector_style, True),
]


# (layer name, frame key, colour, radius, row filter, shown by default)
POINT_LAYERS = [
    ("Network · Room nodes", "net_room_nodes", "#e6550d", 4, lambda g: g[g["doors"] > 0], True),
    ("Network · Doorway nodes", "net_door_access", "#2ca02c", 3,
     lambda g: g[g["connection_status"].str.startswith("connected")], True),
    ("Network · Blocked / unconnected doorways", "net_door_access", "#d62728", 6,
     lambda g: g[~g["connection_status"].str.startswith("connected")], True),
    ("Network · Lift access (label-only landing doors)", "net_lift_access", "#9467bd", 6,
     lambda g: g[g["connection_status"] == "connected"], True),
]

ELEMENT_POPUP_FIELDS = ["name", "global_id", "entity", "info"]
# Derived (non-IFC-element) layers: identifiers and meaningful quantities only; the first two also form the tooltip.
DERIVED_POPUP_FIELDS = {
    "net_regions": ["ifc_space", "region_id", "kind", "area_m2", "doors"],
    "net_room_nodes": ["ifc_space", "region_id", "doors"],
    "net_connectors": ["source_name", "kind", "status", "distance_m"],
    "net_door_access": ["name", "door_global_id", "connection_status"],
    "net_lift_access": ["name", "lift_global_id", "connection_status"],
    "door_cuts": ["door_name", "door_global_id", "cut_kind"],
    "walkable": ["component_id", "area_m2"],
}

LEGEND = [  # (swatch kind, colour, label)
    ("box", REGION_COLOURS["room"], "Room region (one centre node)"),
    ("box", REGION_COLOURS["corridor"], "Corridor region (centreline)"),
    ("box", WALL_COLOURS["internal"], "Internal wall"),
    ("box", WALL_COLOURS["external"], "External wall"),
    ("box", "#8c564b", "Column / curtain wall"),
    ("box", "#d62728", "Door (IfcDoor)"),
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


STYLE_COLUMNS = {"category", "entity", "kind", "status", "crosses_non_walkable_m", "cut_kind", "source",
                 "connection_status", "method", "identification"}


def _keep(gdf, fields):
    """Popup fields plus the columns style functions read; everything else stays out of the GeoJSON."""
    return list(dict.fromkeys(fields + [c for c in gdf.columns if c in STYLE_COLUMNS] + ["geometry"]))


def build_floor_map(frames: dict, ifc_name: str, storey_name: str, run_seconds: float, bounds_xy: list[float] | None,
                    out_path: Path) -> Path:
    m = folium.Map(location=[0, 0], zoom_start=2, crs="Simple", tiles=None, control_scale=False, prefer_canvas=True)
    # This map uses GeoJSON/CircleMarker, not Bootstrap JS or AwesomeMarkers.
    m.default_js = []
    m.default_css = []
    m.get_root().header.add_child(folium.Element(_offline_map_header()), name="offline_assets")
    m.get_root().header.add_child(folium.Element(
        "<style>.leaflet-container{background:#f4f4f2}"
        ".leaflet-control-layers{font-size:11px}.leaflet-control-layers label{margin-bottom:1px}</style>"))

    for label, key, style, show in LAYERS:
        gdf = frames.get(key)
        if gdf is None or len(gdf) == 0:
            continue
        gdf = gdf[~gdf.geometry.is_empty]
        if len(gdf) == 0:
            continue
        is_element = "category" in gdf.columns
        if is_element:
            fields = [f for f in ELEMENT_POPUP_FIELDS if f in gdf.columns]
        else:
            fields = [f for f in DERIVED_POPUP_FIELDS.get(key, []) if f in gdf.columns] or [c for c in gdf.columns if c != "geometry"]
        folium.GeoJson(
            gdf[_keep(gdf, fields)].to_json(), name=f"{label} ({len(gdf)})", show=show, style_function=style,
            tooltip=folium.GeoJsonTooltip(fields=["name"] if is_element else fields[:2], labels=not is_element),
            popup=folium.GeoJsonPopup(fields=fields, max_width=420),
        ).add_to(m)

    for label, key, colour, radius, select, show in POINT_LAYERS:
        gdf = frames.get(key)
        if gdf is None or len(gdf) == 0:
            continue
        gdf = select(gdf)
        if len(gdf) == 0:
            continue
        fields = [f for f in DERIVED_POPUP_FIELDS.get(key, []) if f in gdf.columns] or [c for c in gdf.columns if c != "geometry"]
        folium.GeoJson(
            gdf[_keep(gdf, fields)].to_json(), name=f"{label} ({len(gdf)})", show=show,
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
        f"<b style='font-size:13px'>Indoor walking network (prototype)</b><br>"
        f"Source file: <code>{html.escape(ifc_name)}</code><br>"
        f"Storey: <code>{html.escape(storey_name)}</code><br>"
        f"Run time: {run_seconds:.0f} seconds<br>"
        f"<i>Synthetic local coordinates (IFC world X/Y, metres) on a blank canvas — not geographic.</i>"
    ))
    root.add_child(_panel("bottom:20px;right:10px;max-height:55vh;overflow:auto",
                          f"<details open><summary><b>Legend</b></summary>{_legend_html()}</details>"))
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


def _network_traces(network: dict, connectors: dict, elevation_m: float) -> list:
    z_floor, z_net = elevation_m + 0.02, elevation_m + 0.1
    traces = []
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
    traces = [t for t in traces if t is not None]
    for t in traces:
        t.update(legendgroup="network", legendgrouptitle_text="Walking network")
    return traces


def _scene_layout(meshes: dict, regions, elevation_m: float, z_clip_m: float | None) -> tuple[dict, str]:
    """3D scene (axes, true-scale aspect from the visible ranges) and the explanatory note."""
    note = "IFC world coordinates in metres (synthetic local frame, not geographic). Legend toggles layers; drag to rotate."
    all_verts = [it["verts"] for items in meshes.values() for it in items]
    xy_sources = [v[:, :2] for v in all_verts if len(v)]
    if len(regions) and not regions.geometry.is_empty.all():
        xy_sources.append(np.asarray(regions.total_bounds).reshape(2, 2))
    z0 = elevation_m - 0.5
    z1 = z_clip_m if z_clip_m is not None else max((float(v[:, 2].max()) for v in all_verts), default=elevation_m + 3)
    if z_clip_m is not None:
        note += f" View clipped at Z = {z_clip_m:.1f} m (next storey); some elements extend higher."
    axis_style = {"backgroundcolor": "#fafafa", "gridcolor": "#e5e5e5", "showspikes": False}
    scene = {
        "aspectmode": "data",
        "xaxis": {"title": "X (m)", **axis_style},
        "yaxis": {"title": "Y (m)", **axis_style},
        "zaxis": {"title": "Z (m)", "nticks": 3, **axis_style},
        "camera": {"eye": {"x": 0.45, "y": -1.1, "z": 0.75}},
    }
    if xy_sources:
        xy = np.vstack(xy_sources)
        if not np.isfinite(xy).all() or not np.isfinite([z0, z1]).all():
            raise ValueError("Cannot build 3D view: geometry bounds or storey elevations are not finite.")
        (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
        span = max(x1 - x0, y1 - y0)
        if span > 0:
            # Use visible ranges, not the full, unclipped mesh Z extent.
            scene.update(
                aspectmode="manual",
                aspectratio={"x": (x1 - x0) / span, "y": (y1 - y0) / span, "z": (z1 - z0) / span},
            )
            scene["xaxis"]["range"] = [x0, x1]
            scene["yaxis"]["range"] = [y0, y1]
        scene["zaxis"]["range"] = [z0, z1]
    else:
        note += " No displayable geometry or walkable regions for the selected storey."
        for axis in ("xaxis", "yaxis", "zaxis"):
            scene[axis]["visible"] = False
    return scene, note


def build_3d_view(meshes: dict, network: dict, connectors: dict, storey_name: str, elevation_m: float,
                  hidden: list[str], out_path: Path, z_clip_m: float | None = None) -> tuple[Path, go.Figure]:
    traces = [_mesh_trace(group, items, "legendonly" if group in hidden else True)
              for group, items in meshes.items() if items]
    for t in traces:
        t.update(legendgroup="ifc", legendgrouptitle_text="IFC elements")
    traces += _network_traces(network, connectors, elevation_m)
    scene, note = _scene_layout(meshes, network["regions"], elevation_m, z_clip_m)

    fig = go.Figure(traces)
    fig.update_layout(
        title=f"{storey_name} — IFC elements and derived walking network (3D)",
        annotations=[{"text": note, "xref": "paper", "yref": "paper", "x": 0, "y": 1.0, "showarrow": False,
                      "font": {"size": 11, "color": "#555"}}],
        scene=scene,
        legend={"itemsizing": "constant", "groupclick": "toggleitem", "bgcolor": "rgba(255,255,255,0.8)"},
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
        paper_bgcolor="white",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs=True)  # embed plotly.js so the file works offline
    return out_path, fig

