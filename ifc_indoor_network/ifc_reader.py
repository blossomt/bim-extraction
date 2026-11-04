"""IFC inspection, storey inventory, floor membership and storey selection.

This module only reads IFC semantics (entities, attributes, properties and explicit
relationships). It does not process geometry.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
import ifcopenshell.util.unit as uu
import heuristics

MAX_RELATIONSHIP_DEPTH = 10


class StoreySelectionError(ValueError):
    """Raised when a storey label matches nothing or more than one storey."""


@dataclass
class StoreyInfo:
    global_id: str
    name: str | None
    long_name: str | None
    elevation_raw: float | None  # IfcBuildingStorey.Elevation in project length units
    elevation_m: float | None
    placement_z_m: float | None  # World Z of the storey placement, in metres
    building: str | None

    @property
    def world_z_m(self) -> float:
        """Storey floor in world Z (same datum as meshes and element placements). Elevation is relative to the
        building, which may itself be placed above zero; fall back to it only without a storey placement."""
        if self.placement_z_m is not None:
            return self.placement_z_m
        return self.elevation_m or 0.0


@dataclass
class Membership:
    """How an element was linked to a storey through explicit IFC relationships."""

    global_id: str
    entity: str
    name: str | None
    storey_global_id: str | None
    storey_name: str | None
    path: list[str] = field(default_factory=list)  # e.g. ["contained_in:IfcSpace:25", "aggregated_by:IfcBuildingStorey:Level 2"]
    stopped_at: str | None = None  # Non-storey structure where resolution ended (e.g. IfcBuilding)
    referenced_storeys: list[str] = field(default_factory=list)

    @property
    def is_direct(self) -> bool:
        return self.storey_global_id is not None and len(self.path) == 1


def open_model(path: str | Path) -> ifcopenshell.file:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"IFC file not found: {path}")
    return ifcopenshell.open(str(path))


def get_length_unit(model: ifcopenshell.file) -> dict:
    unit = uu.get_project_unit(model, "LENGTHUNIT")
    name = None
    if unit is not None:
        prefix = getattr(unit, "Prefix", None)
        name = f"{prefix or ''}{unit.Name}" if unit.is_a("IfcSIUnit") else unit.Name
    return {
        "name": name,
        "entity": unit.is_a() if unit is not None else None,
        "scale_to_metre": uu.calculate_unit_scale(model, "LENGTHUNIT"),
    }


def _storey_building(storey) -> str | None:
    parent = ue.get_aggregate(storey)
    while parent is not None and not parent.is_a("IfcBuilding"):
        parent = ue.get_aggregate(parent)
    if parent is None:
        return None
    return parent.Name or parent.GlobalId


def list_storeys(model: ifcopenshell.file) -> list[StoreyInfo]:
    scale = uu.calculate_unit_scale(model, "LENGTHUNIT")
    storeys = []
    for st in model.by_type("IfcBuildingStorey"):
        placement_z = None
        if st.ObjectPlacement is not None:
            placement_z = float(up.get_local_placement(st.ObjectPlacement)[2][3]) * scale
        elevation = float(st.Elevation) if st.Elevation is not None else None
        storeys.append(
            StoreyInfo(
                global_id=st.GlobalId,
                name=st.Name,
                long_name=st.LongName,
                elevation_raw=elevation,
                elevation_m=elevation * scale if elevation is not None else None,
                placement_z_m=placement_z,
                building=_storey_building(st),
            )
        )
    return sorted(storeys, key=lambda s: (s.elevation_m is None, s.elevation_m or 0.0, s.name or ""))


def count_entities(model: ifcopenshell.file, entity_names: list[str]) -> dict[str, int | None]:
    """Counts including subtypes. None means the entity does not exist in the file's schema."""
    counts = {}
    for name in entity_names:
        try:
            counts[name] = len(model.by_type(name))
        except RuntimeError:
            counts[name] = None
    return counts


def _label(entity) -> str:
    return f"{entity.is_a()}:{getattr(entity, 'Name', None) or entity.GlobalId}"


def _next_structural_link(entity):
    """Return (relationship_label, parent) for the next explicit upward relationship, or None."""
    for rel in getattr(entity, "ContainedInStructure", None) or ():
        return "contained_in", rel.RelatingStructure
    for rel in getattr(entity, "Decomposes", None) or ():
        if rel.is_a("IfcRelAggregates"):
            return "aggregated_by", rel.RelatingObject
    for rel in getattr(entity, "Nests", None) or ():
        return "nested_in", rel.RelatingObject
    for rel in getattr(entity, "VoidsElements", None) or ():
        return "voids", rel.RelatingBuildingElement
    for rel in getattr(entity, "FillsVoids", None) or ():
        return "fills", rel.RelatingOpeningElement
    return None


def resolve_membership(element) -> Membership:
    """Follow explicit containment/aggregation/void/fill relationships up to an IfcBuildingStorey."""
    record = Membership(
        global_id=element.GlobalId,
        entity=element.is_a(),
        name=getattr(element, "Name", None),
        storey_global_id=None,
        storey_name=None,
    )
    for rel in getattr(element, "ReferencedInStructures", None) or ():
        if rel.RelatingStructure.is_a("IfcBuildingStorey"):
            record.referenced_storeys.append(rel.RelatingStructure.GlobalId)

    current = element
    for _ in range(MAX_RELATIONSHIP_DEPTH):
        link = _next_structural_link(current)
        if link is None:
            return record
        rel_name, parent = link
        record.path.append(f"{rel_name}:{_label(parent)}")
        if parent.is_a("IfcBuildingStorey"):
            record.storey_global_id = parent.GlobalId
            record.storey_name = parent.Name
            return record
        if parent.is_a("IfcBuilding") or parent.is_a("IfcSite") or parent.is_a("IfcProject"):
            record.stopped_at = _label(parent)
            return record
        current = parent
    record.stopped_at = "max_depth_exceeded"
    return record


def build_membership(model: ifcopenshell.file) -> dict[str, Membership]:
    """Membership for every IfcElement and IfcSpace in the model, keyed by GlobalId."""
    elements = list(model.by_type("IfcElement")) + list(model.by_type("IfcSpace"))
    return {e.GlobalId: resolve_membership(e) for e in elements}


def storey_category_counts(
    model: ifcopenshell.file, membership: dict[str, Membership], categories: list[str]
) -> dict[str | None, dict[str, dict[str, int]]]:
    """{storey_gid: {category: {"direct": n, "indirect": n}}, None: {category: {"unassigned": n}}}."""
    result: dict = defaultdict(lambda: defaultdict(Counter))
    for category in categories:
        try:
            elements = model.by_type(category)
        except RuntimeError:
            continue
        for e in elements:
            m = membership.get(e.GlobalId)
            if m is None:
                continue
            if m.storey_global_id is None:
                key = "unassigned"
            else:
                key = "direct" if m.is_direct else "indirect"
            result[m.storey_global_id][category][key] += 1
    return {k: {c: dict(n) for c, n in v.items()} for k, v in result.items()}


def _predefined(element) -> tuple[str, object] | None:
    """(attribute name, value) of the first of PredefinedType / OperationType / ShapeType the entity has."""
    return next(((a, getattr(element, a)) for a in ("PredefinedType", "OperationType", "ShapeType") if hasattr(element, a)), None)


def summarise_labels(model: ifcopenshell.file, category: str, top_n: int) -> dict | None:
    try:
        elements = model.by_type(category)
    except RuntimeError:
        return None
    object_types: Counter = Counter()
    predefined: Counter = Counter()
    type_names: Counter = Counter()
    psets: Counter = Counter()
    for e in elements:
        object_types[getattr(e, "ObjectType", None)] += 1
        found = _predefined(e)
        predefined[f"{found[0]}={found[1]}" if found else None] += 1
        t = ue.get_type(e)
        type_names[f"{t.is_a()}:{t.Name}" if t is not None else None] += 1
        psets.update(ue.get_psets(e).keys())
    return {
        "count": len(elements),
        "predefined_types": {str(k): v for k, v in predefined.most_common()},
        "object_types_top": {str(k): v for k, v in object_types.most_common(top_n)},
        "distinct_object_types": len(object_types),
        "type_objects_top": {str(k): v for k, v in type_names.most_common(top_n)},
        "property_sets": dict(psets.most_common()),
    }


def _transport_kind(element) -> str | None:
    value = getattr(element, "PredefinedType", None) or getattr(element, "OperationType", None)
    t = ue.get_type(element)
    if value is None and t is not None:
        value = getattr(t, "PredefinedType", None)
    return None if value is None and t is None else str(value)


def _has_lift_label(element, keywords: list[str]) -> bool:
    """True if the name, ObjectType or type name starts a word with one of the keywords (label-only evidence)."""
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")", re.IGNORECASE)
    t = ue.get_type(element)
    labels = [element.Name, getattr(element, "ObjectType", None), t.Name if t is not None else None]
    return any(lbl and pattern.search(lbl) for lbl in labels)


def find_lift_candidates(model: ifcopenshell.file, membership: dict[str, Membership], keywords: list[str]) -> dict:
    """Explicit lifts (IfcTransportElement) and separately, label-only candidates."""
    try:
        transport = model.by_type("IfcTransportElement")
    except RuntimeError:
        transport = ()
    explicit = [{"global_id": e.GlobalId, "name": e.Name, "kind": _transport_kind(e),
                 "storey": membership[e.GlobalId].storey_name if e.GlobalId in membership else None} for e in transport]
    transport_ids = {e.GlobalId for e in transport}
    label_candidates = []
    for e in model.by_type("IfcElement"):
        if e.GlobalId not in transport_ids and _has_lift_label(e, keywords):
            t, m = ue.get_type(e), membership.get(e.GlobalId)
            label_candidates.append({
                "global_id": e.GlobalId, "entity": e.is_a(), "name": e.Name, "object_type": getattr(e, "ObjectType", None),
                "type_name": t.Name if t is not None else None, "storey": m.storey_name if m else None,
                "membership_path": m.path if m else [],
            })
    return {
        "explicit_transport_elements": explicit,
        "label_candidates": label_candidates,
        "label_candidates_by_object_type": dict(Counter(c["object_type"] for c in label_candidates).most_common()),
        "note": "Label candidates are matched on name text only and are not confirmed lifts.",
    }


def select_storey(storeys: list[StoreyInfo], name: str | None, global_id: str | None = None) -> StoreyInfo:
    """Select by GlobalId, else exact name, else case-insensitive name. Never partial matches."""
    available = ", ".join(repr(s.name) for s in storeys)
    if global_id:
        matches = [s for s in storeys if s.global_id == global_id]
        if not matches:
            raise StoreySelectionError(f"No storey with GlobalId {global_id!r}. Available storeys: {available}")
        return matches[0]
    if not name:
        raise StoreySelectionError(f"No storey name given. Available storeys: {available}")

    matches = [s for s in storeys if s.name == name]
    if not matches:
        wanted = name.strip().casefold()
        matches = [s for s in storeys if (s.name or "").strip().casefold() == wanted]
    if not matches:
        raise StoreySelectionError(f"No storey named {name!r}. Available storeys: {available}")
    if len(matches) > 1:
        candidates = "; ".join(f"{s.name!r} (GlobalId {s.global_id}, elevation {s.elevation_m} m)" for s in matches)
        raise StoreySelectionError(
            f"Storey name {name!r} is ambiguous: {candidates}. Re-run with --storey-id <GlobalId>."
        )
    return matches[0]


def build_inventory(model: ifcopenshell.file, categories: list[str], lift_keywords: list[str], top_n: int) -> dict:
    """Model-wide inventory as a JSON-serialisable dict, plus the membership map used to build it."""
    membership = build_membership(model)
    per_storey = storey_category_counts(model, membership, categories)
    containment: dict = defaultdict(lambda: {"relations": 0, "elements": 0})
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        containment[rel.RelatingStructure.is_a()]["relations"] += 1
        containment[rel.RelatingStructure.is_a()]["elements"] += len(rel.RelatedElements)
    paths: Counter = Counter()
    for m in membership.values():
        key = ">".join(f"{step.split(':')[0]}>{step.split(':')[1]}" for step in m.path) or "none"
        paths[f"UNASSIGNED[{key}]" if m.storey_global_id is None else key] += 1
    unassigned = [m for m in membership.values() if m.storey_global_id is None]
    return {
        "schema": model.schema,
        "length_unit": get_length_unit(model),
        "georeferencing": {
            "sites": [{"global_id": s.GlobalId, "name": s.Name, "ref_latitude_dms": list(s.RefLatitude) if s.RefLatitude else None,
                       "ref_longitude_dms": list(s.RefLongitude) if s.RefLongitude else None, "ref_elevation": s.RefElevation}
                      for s in model.by_type("IfcSite")],
            "ifc_map_conversion_count": count_entities(model, ["IfcMapConversion"])["IfcMapConversion"],  # None: not in schema
        },
        "buildings": [{"global_id": b.GlobalId, "name": b.Name, "long_name": b.LongName} for b in model.by_type("IfcBuilding")],
        "storeys": [{**asdict(s), "element_counts": per_storey.get(s.global_id, {})} for s in list_storeys(model)],
        "entity_counts": count_entities(model, categories),
        "unassigned_element_counts": per_storey.get(None, {}),
        "relationship_counts": count_entities(model, [
            "IfcRelContainedInSpatialStructure", "IfcRelReferencedInSpatialStructure", "IfcRelAggregates", "IfcRelVoidsElement",
            "IfcRelFillsElement", "IfcRelSpaceBoundary", "IfcRelConnectsPathElements"]),
        "containment_by_structure_type": dict(containment),
        "membership_paths": dict(paths.most_common()),
        "unassigned": {
            "count": len(unassigned),
            "by_entity": dict(Counter(m.entity for m in unassigned).most_common()),
            "by_stop_reason": dict(Counter(m.stopped_at or "no_relationship" for m in unassigned).most_common()),
            "examples": [asdict(m) for m in unassigned[:50]],
        },
        "wall_is_external": dict(Counter(str(ue.get_pset(w, "Pset_WallCommon", "IsExternal")) for w in model.by_type("IfcWall"))),
        "labels": {c: summarise_labels(model, c, top_n) for c in categories},
        "lifts": find_lift_candidates(model, membership, lift_keywords),
    }, membership


# --- Phase 2: semantic extraction for one storey -------------------------------------------


def _predefined_value(found) -> str | None:
    return str(found[1]) if found and found[1] is not None else None


def _selected_psets(element, extra: list[str]) -> dict:
    return {
        name: {k: v for k, v in props.items() if k != "id"}
        for name, props in ue.get_psets(element).items()
        if name.startswith("Pset_") or name in extra
    }


def _ref(entity) -> dict | None:
    if entity is None:
        return None
    return {"global_id": entity.GlobalId, "entity": entity.is_a(), "name": getattr(entity, "Name", None)}


def _base_record(element, m: Membership, scale: float, extra_psets: list[str]) -> dict:
    t = ue.get_type(element)
    parent = ue.get_aggregate(element)
    return {
        "global_id": element.GlobalId,
        "entity": element.is_a(),
        "name": element.Name,
        "object_type": getattr(element, "ObjectType", None),
        "predefined_type": _predefined_value(_predefined(element)),
        "type_object": _ref(t),
        "storey_global_id": m.storey_global_id,
        "storey_name": m.storey_name,
        "membership_path": m.path,
        "referenced_storeys": m.referenced_storeys,
        "aggregated_in": _ref(parent) if parent is not None and not parent.is_a("IfcSpatialStructureElement") else None,
        "placement_z_m": float(up.get_local_placement(element.ObjectPlacement)[2][3]) * scale
        if getattr(element, "ObjectPlacement", None) is not None else None,
        "psets": _selected_psets(element, extra_psets),
    }


def _wall_record(wall, base: dict) -> dict:
    is_external = ue.get_pset(wall, "Pset_WallCommon", "IsExternal")
    classification = {True: "external", False: "internal"}.get(is_external, "unknown")
    openings = []
    for rel in wall.HasOpenings or ():
        opening = rel.RelatedOpeningElement
        openings.append(
            {
                "opening_global_id": opening.GlobalId,
                "filled_by": [_ref(r.RelatedBuildingElement) for r in opening.HasFillings or ()],
            }
        )
    constraints = base["psets"].get("Constraints", {})
    return {
        **base,
        "classification": classification,
        "classification_source": "Pset_WallCommon.IsExternal" if is_external is not None else None,
        "openings": openings,
        "vendor_span_hint": {  # Revit properties; informational only, never used for membership
            "base_constraint": constraints.get("Base Constraint"),
            "top_constraint": constraints.get("Top Constraint"),
        },
    }


def _filling_kind(opening) -> str:
    fillings = [r.RelatedBuildingElement for r in opening.HasFillings or ()]
    if not fillings:
        return "unfilled"
    if any(f.is_a("IfcDoor") for f in fillings):
        return "door"
    if any(f.is_a("IfcWindow") for f in fillings):
        return "window"
    return "other"


def _opening_record(opening, base: dict) -> dict:
    hosts = [r.RelatingBuildingElement for r in opening.VoidsElements or ()]
    return {
        **base,
        "host": _ref(hosts[0]) if hosts else None,
        "filling_kind": _filling_kind(opening),
        "filled_by": [_ref(r.RelatedBuildingElement) for r in opening.HasFillings or ()],
    }


def _door_record(door, base: dict, scale: float, membership: dict[str, Membership]) -> dict:
    opening = host = None
    for rel in door.FillsVoids or ():
        opening = rel.RelatingOpeningElement
        hosts = [r.RelatingBuildingElement for r in opening.VoidsElements or ()]
        host = hosts[0] if hosts else None
    if opening is None:
        status = "no_fills_relationship"
    elif host is None:
        status = "opening_without_host"
    else:
        status = "opening_and_host"
    host_m = membership.get(host.GlobalId) if host is not None else None
    width, height = getattr(door, "OverallWidth", None), getattr(door, "OverallHeight", None)
    return {
        **base,
        "overall_width_m": width * scale if width is not None else None,
        "overall_height_m": height * scale if height is not None else None,
        "opening": _ref(opening),
        "host_element": _ref(host),
        "host_storey_name": host_m.storey_name if host_m else None,
        "host_relationship_status": status,
    }


def _stair_record(stair, base: dict) -> dict:
    parent = ue.get_aggregate(stair)
    return {
        **base,
        "parent_stair": _ref(parent) if parent is not None and parent.is_a("IfcStair") else None,
        "sub_elements": [_ref(c) for rel in stair.IsDecomposedBy or () for c in rel.RelatedObjects],
    }


def _lift_record(element, base: dict, role_patterns: dict[str, str], access_pattern: str) -> dict:
    explicit = element.is_a("IfcTransportElement")
    labels = " ".join(filter(None, [element.Name, getattr(element, "ObjectType", None)]))
    role = next((r for r, p in role_patterns.items() if re.search(p, labels, re.IGNORECASE)), "other")
    is_access = bool(element.Name and re.search(access_pattern, element.Name, re.IGNORECASE))
    return {
        **base,
        "identification": "explicit_ifc" if explicit else "label_only",
        "transport_kind": _transport_kind(element) if explicit else None,
        "role": role,
        "is_access_point_candidate": is_access,
        "access_rule": f"Name matches {access_pattern!r}" if is_access else None,
    }


def _flag_placement(records: list[dict], z_min: float, z_max: float | None) -> list[dict]:
    flagged = []
    for r in records:
        z = r["placement_z_m"]
        outside = z is not None and (z < z_min or (z_max is not None and z >= z_max))
        r["placement_z_outside_storey"] = outside
        if outside:
            flagged.append({"global_id": r["global_id"], "entity": r["entity"], "name": r["name"], "placement_z_m": round(z, 3)})
    return flagged


def extract_storey_elements(
    model: ifcopenshell.file,
    membership: dict[str, Membership],
    storey: StoreyInfo,
    storeys: list[StoreyInfo],
    cfg,
) -> dict:
    """Semantic records (no geometry) for elements whose storey membership is the selected storey."""
    scale = uu.calculate_unit_scale(model, "LENGTHUNIT")
    on_storey = {gid for gid, m in membership.items() if m.storey_global_id == storey.global_id}

    def elements(entity: str):
        try:
            found = model.by_type(entity)
        except RuntimeError:
            return []
        return [e for e in found if e.GlobalId in on_storey]

    def base(e, category):
        return _base_record(e, membership[e.GlobalId], scale, heuristics.EXTRA_PSETS.get(category, []))

    cats: dict[str, list[dict]] = {
        "walls": [_wall_record(w, base(w, "IfcWall")) for w in elements("IfcWall")],
        "slabs": [{**r, "is_floor_candidate": r["predefined_type"] in ("FLOOR", "BASESLAB")}
                  for r in (base(s, "IfcSlab") for s in elements("IfcSlab"))],
        "spaces": [{**base(s, "IfcSpace"), "long_name": s.LongName,
                    "interior_or_exterior": str(getattr(s, "InteriorOrExteriorSpace", None))} for s in elements("IfcSpace")],
        "doors": [_door_record(d, base(d, "IfcDoor"), scale, membership) for d in elements("IfcDoor")],
        "openings": [_opening_record(o, base(o, "IfcOpeningElement")) for o in elements("IfcOpeningElement")],
        "stairs": [_stair_record(s, base(s, "IfcStair")) for s in elements("IfcStair")],
        "stair_flights": [base(s, "IfcStairFlight") for s in elements("IfcStairFlight")],
        "obstacles": [
            {**base(e, c), "obstacle_category": c} for c in cfg.OBSTACLE_CATEGORIES for e in elements(c)
        ],
    }

    lift_elements = {e.GlobalId: e for e in elements("IfcTransportElement")}
    for e in elements("IfcElement"):
        if _has_lift_label(e, cfg.LIFT_LABEL_KEYWORDS):
            lift_elements.setdefault(e.GlobalId, e)
    cats["lifts"] = [
        _lift_record(e, base(e, "lift"), heuristics.LIFT_ROLE_PATTERNS, cfg.LIFT_ACCESS_NAME_PATTERN)
        for e in lift_elements.values()
    ]

    idx = storeys.index(storey)
    next_storey = storeys[idx + 1] if idx + 1 < len(storeys) else None
    z_min = storey.world_z_m - heuristics.PLACEMENT_Z_TOLERANCE_M
    z_max = next_storey.world_z_m - heuristics.PLACEMENT_Z_TOLERANCE_M if next_storey else None
    placement_flags = {name: _flag_placement(recs, z_min, z_max) for name, recs in cats.items()}

    doors, walls, lifts = cats["doors"], cats["walls"], cats["lifts"]
    summary = {
        "counts": {name: len(recs) for name, recs in cats.items()},
        "walls_by_classification": dict(Counter(w["classification"] for w in walls)),
        "doors_by_host_status": dict(Counter(d["host_relationship_status"] for d in doors)),
        "doors_host_on_other_storey": [
            d["global_id"] for d in doors if d["host_storey_name"] not in (None, storey.name)
        ],
        "openings_by_filling": dict(Counter(o["filling_kind"] for o in cats["openings"])),
        "slab_floor_candidates": sum(s["is_floor_candidate"] for s in cats["slabs"]),
        "stairs_top_level": sum(1 for s in cats["stairs"] if s["parent_stair"] is None),
        "lifts_explicit": sum(1 for r in lifts if r["identification"] == "explicit_ifc"),
        "lifts_label_only_by_role": dict(Counter(r["role"] for r in lifts if r["identification"] == "label_only")),
        "lift_access_point_candidates": sum(r["is_access_point_candidate"] for r in lifts),
        "placement_z_window_m": [z_min, z_max],
        "placement_z_outside_storey": {k: v for k, v in placement_flags.items() if v},
    }
    return {"storey": asdict(storey), "summary": summary, "elements": cats}
