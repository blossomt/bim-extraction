"""IFC indoor walking network prototype - command-line entry point.

Phase 1: model inspection, storey inventory and storey selection.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import config
import ifc_reader


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    t0 = time.time()
    try:
        model = ifc_reader.open_model(args.ifc)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"Loaded {args.ifc} in {time.time() - t0:.1f}s")

    inv, _membership = ifc_reader.build_inventory(
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
            print("\nPhase 1 only: geometry extraction and network generation are not implemented yet.")

    out_path.write_text(json.dumps(inv, indent=2, default=str))
    print(f"\nInventory written to {out_path}  ({time.time() - t0:.1f}s total)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
