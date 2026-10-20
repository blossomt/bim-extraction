import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.guid
import pytest

import config
import ifc_reader
from ifc_reader import StoreyInfo, StoreySelectionError, select_storey


def _storey(name, gid, elev=0.0):
    return StoreyInfo(gid, name, None, elev, elev, elev, "B")


STOREYS = [_storey("Level 1", "A"), _storey("Level 7", "B", 30), _storey("Level 7A", "C", 27)]


class TestSelectStorey:
    def test_exact(self):
        assert select_storey(STOREYS, "Level 7").global_id == "B"

    def test_case_insensitive_without_partial_match(self):
        assert select_storey(STOREYS, "level 7").global_id == "B"

    def test_no_match_lists_available(self):
        with pytest.raises(StoreySelectionError, match="Level 7A"):
            select_storey(STOREYS, "Level 9")

    def test_case_insensitive_ambiguity(self):
        storeys = [_storey("Ground", "G1"), _storey("GROUND", "G2")]
        with pytest.raises(StoreySelectionError, match="ambiguous"):
            select_storey(storeys, "ground")

    def test_duplicate_exact_names_require_id(self):
        storeys = [_storey("L1", "X"), _storey("L1", "Y")]
        with pytest.raises(StoreySelectionError, match="--storey-id"):
            select_storey(storeys, "L1")
        assert select_storey(storeys, "L1", global_id="Y").global_id == "Y"

    def test_unknown_id(self):
        with pytest.raises(StoreySelectionError):
            select_storey(STOREYS, None, global_id="nope")


@pytest.fixture
def model():
    f = ifcopenshell.file(schema="IFC4")
    new = lambda cls, name=None: ifcopenshell.api.root.create_entity(f, ifc_class=cls, name=name)
    project = new("IfcProject", "P")
    site, building = new("IfcSite"), new("IfcBuilding", "B")
    storey = new("IfcBuildingStorey", "Level 1")
    space = new("IfcSpace", "101")
    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(f, products=[building], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=building)
    ifcopenshell.api.aggregate.assign_object(f, products=[space], relating_object=storey)

    wall = new("IfcWall", "wall")
    proxy_in_space = new("IfcBuildingElementProxy", "in space")
    in_building = new("IfcBuildingElementProxy", "in building")
    ifcopenshell.api.spatial.assign_container(f, products=[wall], relating_structure=storey)
    ifcopenshell.api.spatial.assign_container(f, products=[proxy_in_space], relating_structure=space)
    ifcopenshell.api.spatial.assign_container(f, products=[in_building], relating_structure=building)
    new("IfcColumn", "orphan")

    opening, door = new("IfcOpeningElement", "op"), new("IfcDoor", "door")
    f.create_entity("IfcRelVoidsElement", GlobalId=ifcopenshell.guid.new(), RelatingBuildingElement=wall, RelatedOpeningElement=opening)
    f.create_entity("IfcRelFillsElement", GlobalId=ifcopenshell.guid.new(), RelatingOpeningElement=opening, RelatedBuildingElement=door)
    return f


def _by_name(membership, name):
    return next(m for m in membership.values() if m.name == name)


class TestMembership:
    def test_direct_containment(self, model):
        m = _by_name(ifc_reader.build_membership(model), "wall")
        assert m.storey_name == "Level 1" and m.is_direct

    def test_via_space(self, model):
        m = _by_name(ifc_reader.build_membership(model), "in space")
        assert m.storey_name == "Level 1" and not m.is_direct
        assert m.path[0].startswith("contained_in:IfcSpace")

    def test_door_via_opening_and_host_wall(self, model):
        m = _by_name(ifc_reader.build_membership(model), "door")
        assert m.storey_name == "Level 1"
        assert [p.split(":")[0] for p in m.path] == ["fills", "voids", "contained_in"]

    def test_unassigned_is_not_guessed(self, model):
        membership = ifc_reader.build_membership(model)
        orphan = _by_name(membership, "orphan")
        assert orphan.storey_global_id is None and orphan.path == []
        above = _by_name(membership, "in building")
        assert above.storey_global_id is None and above.stopped_at.startswith("IfcBuilding")

    def test_space_is_aggregated(self, model):
        m = _by_name(ifc_reader.build_membership(model), "101")
        assert m.storey_name == "Level 1"

    def test_storey_counts(self, model):
        membership = ifc_reader.build_membership(model)
        counts = ifc_reader.storey_category_counts(model, membership, ["IfcWall", "IfcDoor", "IfcColumn"])
        storey_gid = model.by_type("IfcBuildingStorey")[0].GlobalId
        assert counts[storey_gid]["IfcWall"] == {"direct": 1}
        assert counts[storey_gid]["IfcDoor"] == {"indirect": 1}
        assert counts[None]["IfcColumn"] == {"unassigned": 1}


def test_count_entities_reports_schema_gaps():
    f = ifcopenshell.file(schema="IFC2X3")
    counts = ifc_reader.count_entities(f, ["IfcWall", "IfcMapConversion"])
    assert counts == {"IfcWall": 0, "IfcMapConversion": None}


@pytest.mark.skipif(not config.IFC_PATH.is_file(), reason="sample IFC not present")
def test_sample_model_inventory():
    model = ifc_reader.open_model(config.IFC_PATH)
    inv, membership = ifc_reader.build_inventory(model, config.INVENTORY_CATEGORIES, config.LIFT_LABEL_KEYWORDS, 5)
    assert inv["schema"] == "IFC2X3"
    assert inv["length_unit"]["name"] == "FOOT"
    assert [s["name"] for s in inv["storeys"]][:2] == ["Level 1", "Level 2"]
    assert len(inv["storeys"]) == 8
    assert inv["entity_counts"]["IfcWall"] == 1578
    assert inv["entity_counts"]["IfcStairFlight"] == 0
    assert inv["unassigned"]["count"] == 0
    assert inv["lifts"]["explicit_transport_elements"] == []
    selected = select_storey(ifc_reader.list_storeys(model), "level 1")
    assert selected.elevation_m == 0.0
