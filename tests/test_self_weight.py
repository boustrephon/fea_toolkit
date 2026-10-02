"""Unit tests for the selection self-weight helper (``model/self_weight.py``)."""

from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
    ShellSection,
)
from fea_toolkit.model.selection import Selection
from fea_toolkit.model.self_weight import SelectionWeight, selection_weight


def _model() -> SAPModelData:
    """A small model: one 2 m beam and one 1 m × 1 m slab, both weighable.

    Units are SI (N, m); the beam weighs ``A·ρ·L = 0.01 × 7850 × 2 = 157 N`` and
    the slab ``area·t·ρ = 1 × 0.2 × 2500 = 500 N``, total 657 N.
    """
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=2.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=0.0, y=1.0, z=0.0),
        "4": Node(node_id="4", node_tag=4, x=1.0, y=1.0, z=0.0),
        "5": Node(node_id="5", node_tag=5, x=1.0, y=0.0, z=0.0),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        materials={
            "Steel": Material(name="Steel", type="Steel", unit_weight=7850.0),
            "Concrete": Material(name="Concrete", type="Concrete", unit_weight=2500.0),
        },
        sections={
            "BEAM": Section(name="BEAM", shape="I/Wide Flange", material="Steel", A=0.01),
            "SLAB": ShellSection(name="SLAB", shape="Shell", material="Concrete", thickness=0.2),
        },
        frame_elements={
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
        },
        area_elements={
            "10": AreaElement(area_id="10", area_tag=10, node_ids=["1", "3", "4", "5"]),
        },
        frame_assignments={"1": "BEAM"},
        area_assignments={"10": "SLAB"},
        groups={},
        frame_auto_mesh={},
    )


def test_whole_model_weighs_frames_and_areas():
    weight = selection_weight(_model())
    assert weight.frame_count == 1
    assert weight.area_count == 1
    assert weight.frame_weight == 157.0
    assert weight.area_weight == 500.0
    assert weight.total == 657.0
    assert weight.unit == "N"


def test_selection_narrows_to_frames_only():
    weight = selection_weight(_model(), Selection(element_types=["Frame"]))
    assert weight.frame_count == 1
    assert weight.area_count == 0
    assert weight.frame_weight == 157.0
    assert weight.total == 157.0


def test_selection_narrows_to_areas_only():
    weight = selection_weight(_model(), Selection(element_types=["Area"]))
    assert weight.frame_count == 0
    assert weight.area_count == 1
    assert weight.area_weight == 500.0


def test_node_only_selection_weighs_nothing():
    weight = selection_weight(_model(), Selection(element_types=["Node"], node_ids=["1"]))
    assert weight.count == 0
    assert weight.total == 0.0


def test_inactive_elements_are_skipped():
    model = _model()
    model.frame_elements["2"] = FrameElement(
        elem_id="2", elem_tag=2, node_i="2", node_j="1", inactive=True
    )
    model.frame_assignments["2"] = "BEAM"
    weight = selection_weight(model)
    assert weight.frame_count == 1  # only the active beam
    assert weight.frame_weight == 157.0


def test_missing_unit_weight_weighs_nothing():
    model = _model()
    model.materials["Steel"] = Material(name="Steel", type="Steel", unit_weight=0.0)
    weight = selection_weight(model)
    assert weight.frame_count == 0
    assert weight.frame_weight == 0.0
    assert weight.area_weight == 500.0


def test_result_dataclass_defaults():
    weight = SelectionWeight()
    assert weight.total == 0.0
    assert weight.count == 0
