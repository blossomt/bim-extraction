"""Folium map in a synthetic local frame (Leaflet CRS.Simple): IFC world XY metres, no basemap."""

from __future__ import annotations

import html
from pathlib import Path

import folium

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
]

TOOLTIP_FIELDS = ["category", "name", "global_id", "entity", "storey", "status", "info"]


def _tooltip_fields(gdf) -> list[str]:
    fields = [f for f in TOOLTIP_FIELDS if f in gdf.columns]
    return fields or [c for c in gdf.columns if c != "geometry"]


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
        layer = folium.FeatureGroup(name=f"Door representative points ({len(points)})")
        for _, row in points.iterrows():
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x], radius=3, color="#d62728", fill=True,
                tooltip=html.escape(f"{row['name']} | {row['global_id']}"),
            ).add_to(layer)
        layer.add_to(m)
    lines = frames.get("door_lines")
    if lines is not None and len(lines):
        folium.GeoJson(lines.to_json(), name=f"Door axes ({len(lines)})",
                       style_function=lambda _f: {"color": "#d62728", "weight": 3}).add_to(m)

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
