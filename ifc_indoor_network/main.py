"""IFC indoor walking network prototype - command-line entry point.

Phase 1: model inspection, storey inventory and storey selection.
Phase 2: semantic element extraction for the selected storey.
Phase 3: 2D geometry extraction and a minimal local-coordinate map.
Phase 4: floor footprint (wall envelope) and naive / opening-aware walkable polygons.
Phase 5: pygeoops centreline per walkable component.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import config
import geometry_processing
import ifc_reader
import network_generation
import visualisation


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ifc", type=Path, default=config.IFC_PATH, help="Path to the IFC file")
    p.add_argument("--storey", default=config.TARGET_STOREY, help="IfcBuildingStorey.Name (exact, then case-insensitive)")
    p.add_argument("--storey-id", default=None, help="IfcBuildingStorey GlobalId; overrides --storey")
    p.add_argument("--list-storeys", action="store_true", help="Print the inventory and storeys, then exit")
    p.add_argument("--output-dir", type=Path, default=config.OUTPUT_DIR)
    return p.parse_args(argv)


def _fmt(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def print_inventory(inv: dict) -> None:
    unit = inv["length_unit"]
    print(f"Schema: {inv['schema']}   Length unit: {unit['name']} (x{unit['scale_to_metre']} -> m)")
    for b in inv["buildings"]:
        print(f"Building: {b['name']!r} {b['global_id']}")

    print("\nStoreys (sorted by elevation):")
    cats = [c for c in inv["entity_counts"] if inv["entity_counts"][c] is not None]
    short = {c: c.replace("Ifc", "") for c in cats}
    header = f"  {'Name':<12} {'GlobalId':<24} {'Elev(raw)':>9} {'Elev(m)':>8}  " + " ".join(f"{short[c]:>8.8}" for c in cats)
    print(header)
    for s in inv["storeys"]:
        counts = s["element_counts"]
        cells = " ".join(f"{sum(counts.get(c, {}).values()):>8}" for c in cats)
        print(f"  {str(s['name']):<12} {s['global_id']:<24} {_fmt(s['elevation_raw']):>9} {_fmt(s['elevation_m']):>8}  {cells}")
    unassigned = inv["unassigned_element_counts"]
    cells = " ".join(f"{sum(unassigned.get(c, {}).values()):>8}" for c in cats)
    print(f"  {'UNASSIGNED':<12} {'':<24} {'':>9} {'':>8}  {cells}")
    totals = " ".join(f"{inv['entity_counts'][c]:>8}" for c in cats)
    print(f"  {'TOTAL':<12} {'':<24} {'':>9} {'':>8}  {totals}")
    missing = [c for c, v in inv["entity_counts"].items() if v is None]
    if missing:
        print(f"  Not in schema {inv['schema']}: {', '.join(missing)}")
    print("  (IfcWall includes IfcWallStandardCase.)")

    print("\nStorey membership paths (explicit relationships followed):")
    for path, n in inv["membership_paths"].items():
        print(f"  {n:>6}  {path}")
    ua = inv["unassigned"]
    print(f"Elements without a resolvable storey: {ua['count']}  {ua['by_entity'] or ''}")

    print(f"\nWall Pset_WallCommon.IsExternal: {inv['wall_is_external']}")
    slab = inv["labels"].get("IfcSlab")
    if slab:
        print(f"Slab predefined types: {slab['predefined_types']}")
    lifts = inv["lifts"]
    print(
        f"Lifts: {len(lifts['explicit_transport_elements'])} IfcTransportElement; "
        f"{len(lifts['label_candidates'])} label-only candidates by ObjectType {lifts['label_candidates_by_object_type']}"
    )
    geo = inv["georeferencing"]
    print(f"Georeferencing: IfcMapConversion={geo['ifc_map_conversion_count']}; sites={geo['sites']}")


def print_selected(storey: ifc_reader.StoreyInfo, inv: dict) -> None:
    counts = next(s["element_counts"] for s in inv["storeys"] if s["global_id"] == storey.global_id)
    print(f"\nSelected storey: {storey.name!r}  GlobalId {storey.global_id}")
    print(f"  Elevation: {_fmt(storey.elevation_raw)} {inv['length_unit']['name']} = {_fmt(storey.elevation_m)} m; "
          f"placement Z {_fmt(storey.placement_z_m)} m")
    print("  Element counts (direct = one explicit relationship to the storey; indirect = via space/aggregate/opening/filling):")
    for cat in inv["entity_counts"]:
        c = counts.get(cat, {})
        print(f"    {cat:<22} direct {c.get('direct', 0):>5}   indirect {c.get('indirect', 0):>5}")
    lift_cands = [c for c in inv["lifts"]["label_candidates"] if c["storey"] == storey.name]
    print(f"  Lift label candidates on this storey: {len(lift_cands)}")


INVENTORY_CATEGORY_FOR = {
    "walls": "IfcWall",
    "slabs": "IfcSlab",
    "spaces": "IfcSpace",
    "doors": "IfcDoor",
    "openings": "IfcOpeningElement",
    "stairs": "IfcStair",
    "stair_flights": "IfcStairFlight",
}


def print_extraction(extracted: dict, inv: dict) -> list[str]:
    """Print the extraction summary; return count mismatches against the Phase 1 inventory."""
    s = extracted["summary"]
    print("\nExtracted records (semantics only, no geometry):")
    for name, n in s["counts"].items():
        print(f"  {name:<14} {n:>5}")
    print(f"  Walls by classification: {s['walls_by_classification']}")
    print(f"  Doors by host relationship: {s['doors_by_host_status']}")
    if s["doors_host_on_other_storey"]:
        print(f"  Doors whose host wall is on another storey: {len(s['doors_host_on_other_storey'])}")
    print(f"  Openings by filling: {s['openings_by_filling']}")
    print(f"  Slab floor candidates: {s['slab_floor_candidates']} of {s['counts']['slabs']}")
    print(f"  Stairs: {s['stairs_top_level']} top-level of {s['counts']['stairs']}")
    print(f"  Lifts: {s['lifts_explicit']} explicit IfcTransportElement; label-only by role {s['lifts_label_only_by_role']}; "
          f"{s['lift_access_point_candidates']} access-point candidates (label-only)")
    lo, hi = s["placement_z_window_m"]
    print(f"  Placement Z outside storey window [{_fmt(lo)}, {_fmt(hi)}) m (report only, no reassignment):")
    for cat, items in s["placement_z_outside_storey"].items():
        print(f"    {cat:<14} {len(items):>4}  e.g. {items[0]['name']!r} at Z {items[0]['placement_z_m']} m")

    storey_counts = next(st["element_counts"] for st in inv["storeys"] if st["global_id"] == extracted["storey"]["global_id"])
    mismatches = []
    for name, ifc_cat in INVENTORY_CATEGORY_FOR.items():
        expected = sum(storey_counts.get(ifc_cat, {}).values())
        if expected != s["counts"][name]:
            mismatches.append(f"{name}: extracted {s['counts'][name]} vs inventory {expected}")
    print("  Cross-check vs inventory: " + ("OK" if not mismatches else "MISMATCH " + "; ".join(mismatches)))
    return mismatches


def print_geometry(summary: dict) -> list[str]:
    """Print the geometry summary; return short lines for the map panel."""
    print(f"\nGeometry ({summary['coordinate_frame']}); walking height Z = {_fmt(summary['walking_height_z_m'])} m")
    lines = []
    for cat, counts in summary["status_by_category"].items():
        if counts:
            print(f"  {cat:<14} {counts}")
            lines.append(f"{cat}: {counts}")
    print(f"  Not covering walking height: {summary['not_covering_walking_height']}")
    print(f"  Geometry repairs (make_valid): {len(summary['geometry_repairs'])}")
    dw = summary["door_width_check"]
    print(f"  Door axis - OverallWidth: {dw['compared']} compared, median {_fmt(dw['median_axis_minus_overall_width_m'], 3)} m, "
          f"offsets {dw['offset_counts_m']}, {len(dw['outliers'])} outliers vs median")
    print(f"  XY bounds (m): {[round(b, 2) for b in summary['bounds_xy_m'] or []]}")
    return lines


def print_floor_plan(summary: dict) -> list[str]:
    fp = summary["footprint"]
    print(f"\nFloor plan: footprint source = {summary['footprint_source']} (envelope closing {summary['envelope_closing_m']} m)")
    if summary["footprint_unavailable_reason"]:
        print(f"  UNAVAILABLE: {summary['footprint_unavailable_reason']}")
    print(f"  Footprint: {fp['area_m2']} m2, {fp['components']} components")
    print(f"  Obstacles used: {summary['obstacles_used']}; excluded (not spanning walking height): {summary['excluded_not_spanning_walking_height']}")
    print(f"  Door cuts: {summary['door_cuts']}; doors without cut: {len(summary['doors_without_cut'])}")
    lines = [f"Footprint ({summary['footprint_source']}): {fp['area_m2']} m2"]
    for v, s in summary["walkable"].items():
        print(f"  Walkable {v:<14} {s['area_m2']:>9} m2, {s['components']:>4} components, {s['holes']:>3} holes, "
              f"lost {s['area_lost_pct']}%, slivers removed {s['slivers_removed']} ({s['sliver_area_m2']} m2), "
              f"thin removed {s['thin_removed']} ({s['thin_area_m2']} m2), repaired={s['repaired']}")
        lines.append(f"Walkable {v}: {s['area_m2']} m2, {s['components']} components")
    print(f"  Doorways still blocked (opening-aware): {len(summary['doorways_still_blocked'])}")
    sc = summary["spaces_comparison"]
    print(f"  IfcSpace area {sc['spaces_area_m2']} m2; outside footprint {sc['spaces_area_outside_footprint_m2']} m2; "
          f"footprint not covered by spaces {sc['footprint_area_not_in_spaces_m2']} m2")
    return lines


def print_centrelines(summary: dict) -> list[str]:
    print(f"\nCentreline (pygeoops {summary['pygeoops_version']}, {summary['variant']} walkable, "
          f"params {summary['parameters']}) in {summary['runtime_s']}s")
    print(f"  Walkable components {summary['walkable_components']}: {summary['component_status']}")
    print(f"  Centreline components {summary['centreline_components']}, total length {summary['total_length_m']} m, "
          f"outside walkable {summary['length_outside_walkable_m']} m")
    print(f"  Doorways crossed by centreline: {summary['doorways_crossed']}; not crossed: {len(summary['doorways_not_crossed'])}")
    for c in summary["failed_or_empty"]:
        print(f"  {c['status'].upper()}: {c['component_id']} area {c['area_m2']} m2 {c.get('error', '')}")
    for c in summary["fragmented"]:
        print(f"  Fragmented: {c['component_id']} area {c['area_m2']} m2 -> {c['centreline_groups']} groups")
    return [f"Centreline: {summary['centreline_components']} components, {summary['total_length_m']} m; "
            f"doorways crossed {summary['doorways_crossed']}"]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    t0 = time.time()
    try:
        model = ifc_reader.open_model(args.ifc)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"Loaded {args.ifc} in {time.time() - t0:.1f}s")

    inv, membership = ifc_reader.build_inventory(
        model, config.INVENTORY_CATEGORIES, config.LIFT_LABEL_KEYWORDS, config.LABEL_SUMMARY_TOP_N
    )
    inv["ifc_path"] = str(args.ifc)
    print_inventory(inv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "inventory.json"

    exit_code = 0
    if not args.list_storeys:
        storeys = ifc_reader.list_storeys(model)
        try:
            selected = ifc_reader.select_storey(storeys, args.storey, args.storey_id)
        except ifc_reader.StoreySelectionError as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            inv["selection"] = {"requested_name": args.storey, "requested_id": args.storey_id, "error": str(e)}
            exit_code = 2
        else:
            print_selected(selected, inv)
            inv["selection"] = {"requested_name": args.storey, "requested_id": args.storey_id, "selected_global_id": selected.global_id}
            extracted = ifc_reader.extract_storey_elements(model, membership, selected, storeys, config)
            extracted["summary"]["inventory_mismatches"] = print_extraction(extracted, inv)
            elements_path = args.output_dir / "elements.json"
            elements_path.write_text(json.dumps(extracted, indent=2, default=str))
            print(f"Elements written to {elements_path}")

            t_geom = time.time()
            geom = geometry_processing.extract_geometry(model, extracted, selected.elevation_m or 0.0, config)
            print(f"\nGeometry extracted in {time.time() - t_geom:.1f}s")
            panel_lines = print_geometry(geom["summary"])
            (args.output_dir / "geometry_summary.json").write_text(json.dumps(geom["summary"], indent=2, default=str))
            plan = geometry_processing.build_floor_plan(geom["frames"], extracted, config)
            panel_lines = print_floor_plan(plan["summary"]) + panel_lines
            (args.output_dir / "floor_plan_summary.json").write_text(json.dumps(plan["summary"], indent=2, default=str))
            network = network_generation.generate_centrelines(
                plan["frames"][f"walkable_{config.CENTRELINE_VARIANT}"], plan["frames"]["door_cuts"], config
            )
            panel_lines = print_centrelines(network["summary"]) + panel_lines
            (args.output_dir / "centreline_summary.json").write_text(
                json.dumps({**network["summary"], "per_component": network["per_component"]}, indent=2, default=str)
            )
            map_path = visualisation.build_floor_map(
                {**geom["frames"], **plan["frames"], "centrelines": network["centrelines"]},
                selected.name, geom["summary"]["bounds_xy_m"], panel_lines,
                args.output_dir / "floor_plan.html",
            )
            print(f"Map written to {map_path}")
            print("\nPhases 1-5 only: door/lift connectors are not implemented yet.")

    out_path.write_text(json.dumps(inv, indent=2, default=str))
    print(f"\nInventory written to {out_path}  ({time.time() - t0:.1f}s total)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
