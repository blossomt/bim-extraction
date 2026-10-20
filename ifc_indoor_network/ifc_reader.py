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


def list_buildings(model: ifcopenshell.file) -> list[dict]:
    return [{"global_id": b.GlobalId, "name": b.Name, "long_name": b.LongName} for b in model.by_type("IfcBuilding")]


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


def relationship_path_summary(membership: dict[str, Membership]) -> dict[str, int]:
    """How storey membership was established, e.g. 'contained_in>IfcBuildingStorey'."""
    counts: Counter = Counter()
    for m in membership.values():
        if not m.path:
            key = "none"
        else:
            key = ">".join(f"{step.split(':')[0]}>{step.split(':')[1]}" for step in m.path)
        if m.storey_global_id is None:
            key = f"UNASSIGNED[{key}]"
        counts[key] += 1
    return dict(counts.most_common())


def summarise_unassigned(membership: dict[str, Membership], limit: int = 50) -> dict:
    unassigned = [m for m in membership.values() if m.storey_global_id is None]
    return {
        "count": len(unassigned),
        "by_entity": dict(Counter(m.entity for m in unassigned).most_common()),
        "by_stop_reason": dict(Counter(m.stopped_at or "no_relationship" for m in unassigned).most_common()),
        "examples": [asdict(m) for m in unassigned[:limit]],
    }


def _predefined_type(element):
    for attr in ("PredefinedType", "OperationType", "ShapeType"):
        if hasattr(element, attr):
            return f"{attr}={getattr(element, attr)}"
    return None


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
        predefined[_predefined_type(e)] += 1
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


def wall_classification_summary(model: ifcopenshell.file) -> dict[str, int]:
    """Pset_WallCommon.IsExternal values across all walls (None = property missing)."""
    counts: Counter = Counter()
    for w in model.by_type("IfcWall"):
        counts[str(ue.get_pset(w, "Pset_WallCommon", "IsExternal"))] += 1
    return dict(counts)


def containment_relationship_summary(model: ifcopenshell.file) -> dict[str, dict[str, int]]:
    """Number of IfcRelContainedInSpatialStructure relations and elements per container entity type."""
    out: dict = defaultdict(lambda: {"relations": 0, "elements": 0})
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        out[rel.RelatingStructure.is_a()]["relations"] += 1
        out[rel.RelatingStructure.is_a()]["elements"] += len(rel.RelatedElements)
    return dict(out)


def relationship_counts(model: ifcopenshell.file) -> dict[str, int | None]:
    return count_entities(
        model,
        [
            "IfcRelContainedInSpatialStructure",
            "IfcRelReferencedInSpatialStructure",
            "IfcRelAggregates",
            "IfcRelVoidsElement",
            "IfcRelFillsElement",
            "IfcRelSpaceBoundary",
            "IfcRelConnectsPathElements",
        ],
    )


def _transport_kind(element) -> str | None:
    for attr in ("PredefinedType", "OperationType"):
        value = getattr(element, attr, None)
        if value is not None:
            return str(value)
    t = ue.get_type(element)
    if t is not None:
        return str(getattr(t, "PredefinedType", None))
    return None


def find_lift_candidates(model: ifcopenshell.file, membership: dict[str, Membership], keywords: list[str]) -> dict:
    """Explicit lifts (IfcTransportElement ELEVATOR) and separately, label-only candidates."""
    explicit = []
    try:
        transport = model.by_type("IfcTransportElement")
    except RuntimeError:
        transport = ()
    for e in transport:
        m = membership.get(e.GlobalId)
        explicit.append(
            {
                "global_id": e.GlobalId,
                "name": e.Name,
                "kind": _transport_kind(e),
                "storey": m.storey_name if m else None,
            }
        )

    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")", re.IGNORECASE)
    label_candidates = []
    transport_ids = {e["global_id"] for e in explicit}
    for e in model.by_type("IfcElement"):
        if e.GlobalId in transport_ids:
            continue
        t = ue.get_type(e)
        labels = [e.Name, getattr(e, "ObjectType", None), t.Name if t is not None else None]
        if any(lbl and pattern.search(lbl) for lbl in labels):
            m = membership.get(e.GlobalId)
            label_candidates.append(
                {
                    "global_id": e.GlobalId,
                    "entity": e.is_a(),
                    "name": e.Name,
                    "object_type": getattr(e, "ObjectType", None),
                    "type_name": t.Name if t is not None else None,
                    "storey": m.storey_name if m else None,
                    "membership_path": m.path if m else [],
                }
            )
    return {
        "explicit_transport_elements": explicit,
        "label_candidates": label_candidates,
        "label_candidates_by_object_type": dict(Counter(c["object_type"] for c in label_candidates).most_common()),
        "note": "Label candidates are matched on name text only and are not confirmed lifts.",
    }


def georeferencing_info(model: ifcopenshell.file) -> dict:
    sites = []
    for s in model.by_type("IfcSite"):
        sites.append(
            {
                "global_id": s.GlobalId,
                "name": s.Name,
                "ref_latitude_dms": list(s.RefLatitude) if s.RefLatitude else None,
                "ref_longitude_dms": list(s.RefLongitude) if s.RefLongitude else None,
                "ref_elevation": s.RefElevation,
            }
        )
    map_conversions = count_entities(model, ["IfcMapConversion"])["IfcMapConversion"]
    return {
        "sites": sites,
        "ifc_map_conversion_count": map_conversions,  # None: entity not in schema (e.g. IFC2X3)
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
    storeys = list_storeys(model)
    per_storey = storey_category_counts(model, membership, categories)
    return {
        "schema": model.schema,
        "length_unit": get_length_unit(model),
        "georeferencing": georeferencing_info(model),
        "buildings": list_buildings(model),
        "storeys": [
            {**asdict(s), "element_counts": per_storey.get(s.global_id, {})} for s in storeys
        ],
        "entity_counts": count_entities(model, categories),
        "unassigned_element_counts": per_storey.get(None, {}),
        "relationship_counts": relationship_counts(model),
        "containment_by_structure_type": containment_relationship_summary(model),
        "membership_paths": relationship_path_summary(membership),
        "unassigned": summarise_unassigned(membership),
        "wall_is_external": wall_classification_summary(model),
        "labels": {c: summarise_labels(model, c, top_n) for c in categories},
        "lifts": find_lift_candidates(model, membership, lift_keywords),
    }, membership
