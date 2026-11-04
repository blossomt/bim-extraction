"""IFC indoor walking network prototype - command-line entry point.

Phase 1: model inspection, storey inventory and storey selection.
Phase 2: semantic element extraction for the selected storey.
Phase 3: 2D geometry extraction and a minimal local-coordinate map.
Phase 4: floor footprint (wall envelope and floor slabs) and opening-aware walkable area.
Phase 5: walking network — walkable area split at door cuts; room centre nodes; pygeoops corridor centrelines.
Phase 6: door and lift connectors.
Phase 9: optional Plotly 3D view.
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
    p.add_argument("--ifc", type=Path, required=True, help="Path to the IFC file (required)")
    p.add_argument("--storey", default=config.TARGET_STOREY, help="IfcBuildingStorey.Name (exact, then case-insensitive)")
    p.add_argument("--storey-id", default=None, help="IfcBuildingStorey GlobalId; overrides --storey")
    p.add_argument("--list-storeys", action="store_true", help="Print the inventory and storeys, then exit")
    p.add_argument("--output-dir", type=Path, default=config.OUTPUT_DIR)
    p.add_argument("--no-3d", action="store_true", help="Skip the optional Plotly 3D view")
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
    print(f"\nSelected storey: {storey.name!r}  GlobalId {storey.global_id}")
    print(f"  Elevation {_fmt(storey.elevation_m)} m; placement Z {_fmt(storey.placement_z_m)} m "
          f"(world Z used for walking height: {_fmt(storey.world_z_m)} m)")


INVENTORY_CATEGORY_FOR = {
    "walls": "IfcWall", "slabs": "IfcSlab", "spaces": "IfcSpace", "doors": "IfcDoor",
    "openings": "IfcOpeningElement", "stairs": "IfcStair", "stair_flights": "IfcStairFlight",
}


def cross_check_inventory(extracted: dict, inv: dict) -> list[str]:
    """Count mismatches between extracted records and the Phase 1 inventory for the selected storey."""
    counts = extracted["summary"]["counts"]
    storey_counts = next(st["element_counts"] for st in inv["storeys"] if st["global_id"] == extracted["storey"]["global_id"])
    return [f"{name}: extracted {counts[name]} vs inventory {sum(storey_counts.get(cat, {}).values())}"
            for name, cat in INVENTORY_CATEGORY_FOR.items() if sum(storey_counts.get(cat, {}).values()) != counts[name]]


def run_storey_pipeline(model, membership, selected: ifc_reader.StoreyInfo, storeys: list, inv: dict,
                        args: argparse.Namespace, t0: float) -> None:
    """Phases 2-7 and 9 for one storey; writes summary.json, floor_plan.html and (unless --no-3d) floor_plan_3d.html."""
    print_selected(selected, inv)
    extracted = ifc_reader.extract_storey_elements(model, membership, selected, storeys, config)
    mismatches = extracted["summary"]["inventory_mismatches"] = cross_check_inventory(extracted, inv)
    print(f"Extracted {extracted['summary']['counts']}; cross-check vs inventory: {'OK' if not mismatches else mismatches}")

    t_geom = time.time()
    geom = geometry_processing.extract_geometry(model, extracted, selected.world_z_m, config)
    print(f"\nGeometry extracted in {time.time() - t_geom:.1f}s")
    plan = geometry_processing.build_floor_plan(geom["frames"], extracted, config)
    fp, walk = plan["summary"]["footprint"], plan["summary"]["walkable"]
    print(f"Floor plan: footprint {fp['area_m2']} m2 ({plan['summary']['footprint_source']}), walkable {walk['area_m2']} m2, "
          f"{plan['summary']['door_cuts']} door cuts")
    network = network_generation.build_network(plan["frames"]["walkable"], plan["frames"]["door_cuts"], config)
    ns = network["summary"]
    print(f"Network in {ns['runtime_s']}s: regions {ns['regions']}, {ns['room_nodes']} room nodes, "
          f"{ns['centreline_components']} centreline components ({ns['centreline_length_m']} m)")
    labels = network_generation.label_regions(network["regions"], geom["frames"]["spaces"], extracted["elements"]["spaces"])
    for key in ("regions", "room_nodes"):
        if len(network[key]):
            network[key]["ifc_space"] = network[key]["region_id"].map(labels).fillna("—")
    conn = network_generation.build_connectors(
        network, geom["frames"]["door_points"], geom["frames"]["lifts"], extracted["elements"]["lifts"], config
    )
    cs = conn["summary"]
    print(f"Connectors: doors {cs['doors_by_connection_status']}; lifts {cs['lifts']['by_connection_status']}; "
          f"{cs['network_components']} network components, largest holds {cs['largest_component_doors']} of "
          f"{cs['connected_doors']} connected doors; {len(cs['connectors_too_long'])} too long")
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps({
        "extraction": extracted["summary"], "geometry": geom["summary"], "floor_plan": plan["summary"],
        "network": network["summary"], "connectors": conn["summary"],
    }, indent=2, default=str))
    print(f"Stage summaries written to {summary_path}")
    net_frames = {f"net_{k}": v for k, v in {**network, **conn}.items() if k not in ("summary", "walkable")}
    map_path = visualisation.build_floor_map(
        {**geom["frames"], **plan["frames"], **net_frames}, args.ifc.name, selected.name, time.time() - t0,
        geom["summary"]["bounds_xy_m"], args.output_dir / "floor_plan.html",
    )
    print(f"Map written to {map_path}")
    if not args.no_3d:
        write_3d_view(model, extracted, network, conn, selected, storeys, args.output_dir)
    print("\nPhases 1-7 and 9 done; consolidated diagnostics report (Phase 8) not implemented yet.")


def write_3d_view(model, extracted: dict, network: dict, conn: dict, selected: ifc_reader.StoreyInfo, storeys: list,
                  output_dir: Path) -> None:
    t3 = time.time()
    meshes = geometry_processing.collect_display_meshes(model, extracted, config.PLOT3D_CATEGORIES, config)
    idx = storeys.index(selected)
    z_clip = storeys[idx + 1].world_z_m if idx + 1 < len(storeys) else None
    path_3d, fig_3d = visualisation.build_3d_view(
        meshes, network, conn, selected.name, selected.world_z_m, config.PLOT3D_HIDDEN,
        output_dir / "floor_plan_3d.html", z_clip_m=z_clip,
    )
    if not fig_3d.data:
        print("WARNING: No displayable geometry or walkable regions for the selected storey; "
              "the 3D view contains an empty-result notice.")
    counts = {k: len(v) for k, v in meshes.items()}
    print(f"3D view written to {path_3d} ({time.time() - t3:.1f}s; meshes {counts}; {path_3d.stat().st_size / 1e6:.1f} MB)")


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
    exit_code = 0
    if not args.list_storeys:
        storeys = ifc_reader.list_storeys(model)
        request = {"requested_name": args.storey, "requested_id": args.storey_id}
        try:
            selected = ifc_reader.select_storey(storeys, args.storey, args.storey_id)
        except ifc_reader.StoreySelectionError as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            inv["selection"] = {**request, "error": str(e)}
            exit_code = 2
        else:
            inv["selection"] = {**request, "selected_global_id": selected.global_id}
            run_storey_pipeline(model, membership, selected, storeys, inv, args, t0)

    out_path = args.output_dir / "inventory.json"
    out_path.write_text(json.dumps(inv, indent=2, default=str))
    print(f"\nInventory written to {out_path}  ({time.time() - t0:.1f}s total)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
