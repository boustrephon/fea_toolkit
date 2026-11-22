"""Base isolation — replacing fixed supports with grounded isolator links."""

import pytest

from fea_toolkit.model.sap_data import (
    GapParams,
    LinkProperty,
    Node,
    Restraint,
    SAPModelData,
    TriplePendulumParams,
    WenParams,
)
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


def test_isolate_base_accepts_triple_friction_pendulum():
    """A Triple Friction Pendulum property is a valid grounded isolator."""
    md = _model()
    md.link_properties["TPB"] = LinkProperty(
        name="TPB",
        link_type="Triple Friction Pendulum",
        ke=[1.0e6, 1000.0, 1000.0, 0.0, 0.0, 0.0],
        nonlinear_params=TriplePendulumParams(
            radius=[0.4, 3.8, 3.8],
            ubar=[0.05, 0.5, 0.5],
            mu=[0.02, 0.08, 0.08],
            W=1000.0,
        ),
    )
    count, ids = isolate_base(md, "TPB")
    assert (count, ids) == (1, ["1"])
    assert md.link_assignments["ISO_1"] == "TPB"


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
        statuses = {row[0]: row[1] for row in table.rows}
        assert statuses.get("WEN[0]") == "passed"
        assert "(none)" not in statuses
    finally:
        import openseespy.opensees as ops

        ops.wipe()


def test_check_hysteretic_backbone_gap_opens_and_closes():
    """The D8 verb cyclically checks a one-sided Gap (closes → opens → re-closes)."""
    from fea_toolkit.workflow.steps import Step, StepContext
    from fea_toolkit.workflow.verbs.check_hysteretic import run_check_hysteretic_backbone

    md = _model()
    md.link_properties["GAP"] = LinkProperty(
        name="GAP",
        link_type="Gap",
        ke=[1000.0] * 6,
        nonlinear_params=GapParams(open=[0.005] * 6),
    )
    try:
        ctx = StepContext(model_data=md)
        results = run_check_hysteretic_backbone(ctx, Step(verb="check_hysteretic_backbone"))
        table = results[0].payload
        statuses = {row[0]: row[1] for row in table.rows}
        assert statuses.get("GAP[0]") == "passed"
        assert "(none)" not in statuses
    finally:
        import openseespy.opensees as ops

        ops.wipe()
