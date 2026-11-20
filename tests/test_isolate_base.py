"""Base isolation — replacing fixed supports with grounded isolator links."""

import pytest

from fea_toolkit.model.sap_data import LinkProperty, Node, Restraint, SAPModelData, WenParams
from fea_toolkit.model.supports import isolate_base


def _model():
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=1.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=3.0),
        },
        restraints={
            "1": Restraint([1, 1, 1, 1, 1, 1]),  # fully fixed → isolated
            "2": Restraint([1, 1, 1, 0, 0, 0]),  # pinned → NOT isolated by default
        },
        materials={},
        sections={},
        frame_elements={},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        link_properties={
            "ISO1": LinkProperty(name="ISO1", link_type="Rubber Isolator", ke=[0.0] * 6),
        },
    )


def test_isolate_base_replaces_fixed_supports():
    """A fully-restrained base node becomes a grounded isolator link."""
    md = _model()
    count, ids = isolate_base(md, "ISO1")
    assert (count, ids) == (1, ["1"])
    assert "1" not in md.restraints
    assert "2" in md.restraints  # pinned node untouched
    link = md.link_connectivity[0]
    assert (link.node_i, link.node_j, link.coord_dir1) == ("1", "", "+Z")
    assert md.link_assignments["ISO_1"] == "ISO1"


def test_isolate_base_rejects_non_isolator_property():
    md = _model()
    md.link_properties["LIN"] = LinkProperty(name="LIN", link_type="Linear", ke=[1000.0] * 6)
    with pytest.raises(ValueError, match="not an isolator"):
        isolate_base(md, "LIN")


def test_isolate_base_respects_selection():
    from fea_toolkit.model.selection import Selection

    md = _model()
    md.restraints["3"] = Restraint([1, 1, 1, 1, 1, 1])
    _count, ids = isolate_base(md, "ISO1", selection=Selection(node_ids=["3"]))
    assert ids == ["3"]
    assert "1" in md.restraints  # outside the selection → untouched


def test_check_hysteretic_backbone_verb_passes():
    """The D8 verb re-runs the cyclic check and reports a passed table."""
    from fea_toolkit.workflow.steps import Step, StepContext
    from fea_toolkit.workflow.verbs.check_hysteretic import run_check_hysteretic_backbone

    md = _model()
    md.link_properties["WEN"] = LinkProperty(
        name="WEN",
        link_type="Plastic (Wen)",
        ke=[1000.0] * 6,
        nonlinear_params=WenParams(fy=[50.0] * 6, ratio=0.1, n=2.0),
    )
    try:
        ctx = StepContext(model_data=md)
        results = run_check_hysteretic_backbone(ctx, Step(verb="check_hysteretic_backbone"))
        table = results[0].payload
        assert results[0].kind == "table"
        assert all(row[1] == "passed" for row in table.rows)
    finally:
        import openseespy.opensees as ops

        ops.wipe()
