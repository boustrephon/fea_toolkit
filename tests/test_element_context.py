"""``element_context`` — the Inspector's section / material / groups lookup."""

from fea_toolkit.model.element_context import element_rows, groups_of
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    Group,
    Material,
    Node,
    SAPModelData,
    Section,
)


def _model():
    """A small model with frames, an area, sections, materials and groups."""
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=5, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=5, z=0),
            "4": Node(node_id="4", node_tag=4, x=0, y=5, z=0),
        },
        restraints={},
        materials={
            "Steel": Material(name="Steel", type="Steel"),
            "Concrete": Material(name="Concrete", type="Concrete"),
        },
        sections={
            "UB300": Section(name="UB300", shape="I/Wide Flange", material="Steel"),
            "Slab200": Section(name="Slab200", shape="Shell", material="Concrete"),
        },
        frame_elements={
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
            "1a": FrameElement(elem_id="1a", elem_tag=2, node_i="1", node_j="2", parent_id="1"),
            "2": FrameElement(elem_id="2", elem_tag=3, node_i="2", node_j="3"),
        },
        area_elements={
            "A1": AreaElement(area_id="A1", area_tag=1, node_ids=["1", "2", "3", "4"]),
        },
        frame_assignments={"1": "UB300"},
        area_assignments={"A1": "Slab200"},
        groups={
            "Cols": Group(name="Cols", objects=["Frame:1", "Joint:1"]),
            "Slabs": Group(name="Slabs", objects=["Area:A1"]),
            "All": Group(name="All", objects=["Frame:1", "Area:A1", "Joint:2"]),
        },
        frame_auto_mesh={},
    )


def test_a_frame_reports_section_material_and_groups():
    model = _model()
    assert element_rows(model, model.frame_elements["1"]) == [
        ("Section", "UB300 (I/Wide Flange)"),
        ("Material", "Steel"),
        ("Groups", "Cols, All"),
    ]


def test_an_area_reports_its_section_and_material():
    model = _model()
    assert element_rows(model, model.area_elements["A1"]) == [
        ("Section", "Slab200 (Shell)"),
        ("Material", "Concrete"),
        ("Groups", "Slabs, All"),
    ]


def test_a_node_reports_groups_but_no_section_or_material():
    model = _model()
    assert element_rows(model, model.nodes["1"]) == [("Groups", "Cols")]


def test_a_split_child_resolves_through_its_parent():
    """The child's own id is absent; its ``parent_id`` names the assignment."""
    model = _model()
    rows = element_rows(model, model.frame_elements["1a"])
    assert ("Section", "UB300 (I/Wide Flange)") in rows
    assert ("Material", "Steel") in rows


def test_an_unassigned_element_has_no_section_or_material():
    model = _model()
    rows = element_rows(model, model.frame_elements["2"])
    assert ("Section", "UB300 (I/Wide Flange)") not in rows
    assert ("Material", "Steel") not in rows


def test_a_groupless_entity_has_no_groups_row():
    model = _model()
    assert "Groups" not in dict(element_rows(model, model.frame_elements["2"]))


def test_none_model_adds_nothing():
    model = _model()
    assert element_rows(None, model.frame_elements["1"]) == []


def test_a_non_entity_adds_nothing():
    assert element_rows(_model(), "not an entity") == []


def test_groups_of_uses_the_reference_space():
    model = _model()
    assert groups_of(model, "Frame", "1") == ["Cols", "All"]
    assert groups_of(model, "Joint", "1") == ["Cols"]
    assert groups_of(model, "Frame", "2") == []


def test_material_label_omits_a_redundant_type():
    """``Steel (Steel)`` would be noise — the type is dropped when it matches."""
    model = _model()
    # ``Steel`` material has type ``Steel``, so the row is just the name.
    rows = element_rows(model, model.frame_elements["1"])
    assert ("Material", "Steel") in rows
