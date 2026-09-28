"""Reading a node's support conditions: restraints and joint constraints.

Qt-free and renderer-free.  These labels are what the Inspector lists and what
the glyph renderer draws a picture of, so a change here moves both — which is the
reason the lookup lives in one module instead of beside each caller.
"""

from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import Constraint, Restraint
from fea_toolkit.model.supports import (
    constraint_label,
    fixed_dofs,
    restraint_dofs,
    restraint_label,
    support_rows,
)


def _model(*, restraints=None, assignments=None, constraints=None):
    """A real ``MeshModel`` carrying only what support lookup reads.

    The real class rather than a stand-in: ``MeshModel`` is what a processed model
    always is, and what a processed view's Inspector is given, so this exercises
    the actual attribute contract.  ``constraint_assignments`` / ``constraints``
    live on ``SAPModelData`` — a ``MeshModel`` never carries them — so they are
    attached here only to keep both sources testable in one place.
    """
    model = MeshModel(
        nodes={},
        frame_elements={},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        restraints=restraints if restraints is not None else {},
    )
    model.constraint_assignments = assignments if assignments is not None else {}
    model.constraints = constraints if constraints is not None else {}
    return model


class TestRestraintLabels:
    """The six flags, and the names the conventional sets carry."""

    def test_a_fixed_node_reads_as_fixed(self):
        model = _model(restraints={"1": Restraint([1, 1, 1, 1, 1, 1])})
        assert restraint_label(model, "1") == "Fixed (U1 U2 U3 R1 R2 R3)"

    def test_a_pinned_node_names_its_pattern(self):
        """Translations held, rotations free — the common pin."""
        model = _model(restraints={"1": Restraint([1, 1, 1, 0, 0, 0])})
        assert restraint_label(model, "1") == "Pinned (U1 U2 U3)"

    def test_an_unconventional_set_reports_its_dofs(self):
        """No invented name: a roller along X is just its restrained DOFs."""
        model = _model(restraints={"1": Restraint([0, 1, 1, 0, 0, 0])})
        assert restraint_label(model, "1") == "U2 U3"

    def test_a_node_with_no_definition_reports_nothing(self):
        """``""`` rather than "Free", so the Inspector omits the row entirely."""
        assert restraint_label(_model(), "2") == ""
        assert restraint_dofs(_model(), "2") == ()

    def test_an_all_zero_restraint_reports_nothing(self):
        model = _model(restraints={"1": Restraint([0, 0, 0, 0, 0, 0])})
        assert restraint_label(model, "1") == ""

    def test_fixed_dofs_keeps_the_axis_order(self):
        model = _model(restraints={"1": Restraint([0, 1, 0, 0, 0, 1])})
        assert fixed_dofs(model, "1") == ("U2", "R3")

    def test_a_model_with_no_restraints_at_all_is_handled(self):
        """An archive has none, and neither has a bare object."""
        assert restraint_dofs(object(), "1") == ()
        assert restraint_label(None, "1") == ""


class TestConstraintLabels:
    """A joint constraint is an assignment plus a definition."""

    def test_an_assignment_reads_with_its_type(self):
        model = _model(
            assignments={"1": "D1"},
            constraints={"D1": Constraint(name="D1", constraint_type="DIAPHRAGM")},
        )
        assert constraint_label(model, "1") == "D1 (DIAPHRAGM)"

    def test_an_assignment_without_a_definition_still_reports_the_name(self):
        """The definition may live in a table this model did not carry."""
        model = _model(assignments={"1": "D1"})
        assert constraint_label(model, "1") == "D1"

    def test_an_unassigned_node_reports_nothing(self):
        assert constraint_label(_model(), "1") == ""


class TestSupportRows:
    """What the Inspector appends to a node's own fields."""

    def test_a_restrained_and_assigned_node_reports_both(self):
        model = _model(
            restraints={"1": Restraint([1, 1, 1, 0, 0, 0])},
            assignments={"1": "BODY1"},
            constraints={"BODY1": Constraint(name="BODY1", constraint_type="BODY")},
        )
        assert support_rows(model, "1") == [
            ("Restraints", "Pinned (U1 U2 U3)"),
            ("Constraint", "BODY1 (BODY)"),
        ]

    def test_a_bare_node_gains_no_rows(self):
        """Only rows with something to say — no clutter on ordinary nodes."""
        assert support_rows(_model(), "2") == []

    def test_a_restraint_without_a_constraint_reports_one_row(self):
        model = _model(restraints={"1": Restraint([1, 0, 0, 0, 0, 0])})
        assert support_rows(model, "1") == [("Restraints", "U1")]
