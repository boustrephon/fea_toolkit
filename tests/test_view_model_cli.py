"""Tests for the ``examples/view_model.py`` CLI.

``view_model`` is the packaged viewer's command line: model-file dispatch,
result-mode policy, ``--select`` parsing/warnings, ``--highlight-constraint``
resolution and the 1-based ``--mode`` index.  Plotting itself is covered by
``tests/test_viz_model.py``; model-file dispatch by ``tests/test_model_loader.py``.
"""

import pytest

# ============================================================================
# Local scenario builders
# ============================================================================


def _selection_model():
    """Small ``SAPModelData`` with sections, a group, constraints and 4 nodes.

    Kept local to this file (see ``tests/README.md``): it is this module's
    specification of the CLI scenario, not shared infrastructure.
    """
    from fea_toolkit.model.sap_data import (
        AreaElement,
        Constraint,
        FrameElement,
        Group,
        ISection,
        Node,
        SAPModelData,
    )

    def _isection(name):
        return ISection(
            name=name,
            shape="W16x31",
            material="Steel",
            A=0.4,
            I33=0.02,
            I22=0.02,
            J=0.0,
            depth=0.4,
            bf=0.2,
            tf=0.01,
            tw=0.01,
        )

    return SAPModelData(
        nodes={
            "N1": Node("N1", 1, 0.0, 0.0, 0.0),
            "N2": Node("N2", 2, 4.0, 0.0, 0.0),
            "N3": Node("N3", 3, 4.0, 0.0, 3.0),
            "N4": Node("N4", 4, 0.0, 4.0, 0.0),
        },
        restraints={},
        materials={},
        sections={"COL": _isection("COL"), "BEAM": _isection("BEAM")},
        frame_elements={
            "F1": FrameElement("F1", 10, "N1", "N3"),
            "F2": FrameElement("F2", 20, "N2", "N3"),
        },
        area_elements={"A1": AreaElement("A1", 30, ["N1", "N2", "N3", "N4"])},
        frame_assignments={"F1": "COL", "F2": "BEAM"},
        area_assignments={"A1": "SLAB"},
        groups={"Cols": Group(name="Cols", color="", objects=["Frame:F1", "Joint:N1"])},
        frame_auto_mesh={},
        constraints={"Fix": Constraint(name="Fix", constraint_type="BODY")},
        constraint_assignments={"N1": "Fix", "N2": "Fix", "N4": "Fix"},
    )


# ============================================================================
# Mode index (1-based --mode)
# ============================================================================


class TestViewModelModeIndex:
    """``examples/view_model.py`` presents modes 1-based to the user."""

    @staticmethod
    def _args(mode):
        from argparse import Namespace

        return Namespace(mode=mode)

    def test_first_mode_is_one(self):
        from examples.view_model import mode_index

        assert mode_index(self._args(1)) == 0
        assert mode_index(self._args(3)) == 2

    def test_zero_or_negative_mode_exits(self):
        from examples.view_model import mode_index

        for bad in (0, -1):
            with pytest.raises(SystemExit):
                mode_index(self._args(bad))


# ============================================================================
# Model-viewer constraint highlighting
# ============================================================================


class TestViewModelConstraintHighlight:
    """``--highlight-constraint`` resolves SAP2000 constraint groups to joints.

    The resolution is type-agnostic (it reads the assignment table, not the
    definition type), so ``BODY`` rigid bodies, ``DIAPHRAGM`` groups and the
    other constraint types all work.
    """

    @staticmethod
    def _args(names):
        from argparse import Namespace

        return Namespace(highlight_constraint=names)

    @staticmethod
    def _md():
        from types import SimpleNamespace

        from fea_toolkit.model.sap_data import Constraint, Node

        return SimpleNamespace(
            constraints={
                "Fix": Constraint("Fix", "BODY"),
                "D1": Constraint("D1", "DIAPHRAGM"),
            },
            constraint_assignments={"1": "Fix", "2": "Fix", "10": "D1"},
            nodes={
                "1": Node("1", 1, 0.0, 0.0, 0.0),
                "2": Node("2", 2, 1.0, 0.0, 0.0),
                "10": Node("10", 10, 0.0, 0.0, 3.0),
            },
        )

    def test_unused_option_returns_empty(self):
        from examples.view_model import constraint_node_colors

        assert constraint_node_colors(self._args(None), self._md()) == {}
        assert constraint_node_colors(self._args([]), self._md()) == {}

    def test_resolves_body_group(self, capsys):
        from examples.view_model import constraint_node_colors

        colors = constraint_node_colors(self._args(["Fix"]), self._md())

        assert set(colors) == {"1", "2"}
        assert set(colors.values()) == {"#ff2d2d"}
        assert "Highlighting constraint 'Fix' (BODY): 2 of 2" in capsys.readouterr().out

    def test_multiple_groups_are_unioned(self):
        from examples.view_model import constraint_node_colors

        colors = constraint_node_colors(self._args(["Fix", "D1"]), self._md())

        assert set(colors) == {"1", "2", "10"}

    def test_red_and_yellow_paths_agree(self):
        """The red set is resolved through Selection.constraints, so it
        equals what --select would highlight."""
        from examples.view_model import constraint_node_colors
        from fea_toolkit.model.selection import Selection

        md = self._md()
        colors = constraint_node_colors(self._args(["Fix", "D1"]), md)

        assert set(colors) == set(Selection(constraints=["Fix", "D1"]).get_node_ids(md))

    def test_unknown_name_is_reported_and_ignored(self, capsys):
        from examples.view_model import constraint_node_colors

        assert constraint_node_colors(self._args(["Nope"]), self._md()) == {}
        assert "not defined in the model" in capsys.readouterr().out

    def test_joints_missing_from_the_model_are_skipped(self, capsys):
        """A joint removed by the importer must not colour a different node."""
        from examples.view_model import constraint_node_colors

        md = self._md()
        md.constraint_assignments = {**md.constraint_assignments, "99": "Fix"}

        colors = constraint_node_colors(self._args(["Fix"]), md)

        assert set(colors) == {"1", "2"}
        assert "2 of 3 assigned joint(s) present" in capsys.readouterr().out


# ============================================================================
# Model-viewer selection expressions (--select)
# ============================================================================


class TestViewModelSelectionExpression:
    """CLI behaviour of ``--select``: parsing delegates to
    :meth:`Selection.from_string`, the CLI adds warnings and exits."""

    @staticmethod
    def _args(exprs):
        from argparse import Namespace

        return Namespace(select=exprs)

    def test_unused_option_returns_none(self):
        from examples.view_model import selection_highlights

        assert selection_highlights(self._args(None)) is None
        assert selection_highlights(self._args([])) is None

    def test_multiple_expressions_become_multiple_selections(self):
        from examples.view_model import selection_highlights

        sels = selection_highlights(self._args(["type=Frame; section=2xR3", "id=1,2"]))

        assert [s.sections for s in sels] == [["2xR3"], None]
        assert [s.element_ids for s in sels] == [None, ["1", "2"]]

    def test_bad_expression_exits_with_message(self):
        from examples.view_model import selection_highlights

        with pytest.raises(SystemExit) as exc:
            selection_highlights(self._args(["bogus=1"]))

        assert "unknown selection key" in str(exc.value)

    def test_node_only_selection_warns_about_ignored_criteria(self, capsys):
        """Nodes are matched by type / id / group — say so for section / z."""
        from examples.view_model import selection_highlights

        sels = selection_highlights(self._args(["type=Node; z=0:3.4"]))

        assert len(sels) == 1
        assert "Node-only Selection" in capsys.readouterr().out

    def test_frames_with_elevation_do_not_warn(self, capsys):
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["type=Frame; z=0:3.4"]))

        assert capsys.readouterr().out == ""

    def test_constraint_only_does_not_warn(self, capsys):
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["constraint=Fix"]))

        assert capsys.readouterr().out == ""

    def test_constraint_with_element_type_warns(self, capsys):
        """constraint= selects joints, so type=Frame cannot match anything."""
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["constraint=Fix; type=Frame"]))

        assert "matches nothing" in capsys.readouterr().out

    def test_constraint_with_section_warns_about_ignored_criteria(self, capsys):
        from examples.view_model import selection_highlights

        selection_highlights(self._args(["constraint=Fix; section=2xR3"]))

        assert "Node-only Selection" in capsys.readouterr().out


# ============================================================================
# View-model input formats (.s2k / raw-table JSON / model-codec JSON)
# ============================================================================


class TestViewModelInputPolicy:
    """CLI policy for what a loaded model can be asked to do."""

    @staticmethod
    def _mesh_snapshot():
        """A post-preprocessing MeshModel — codec-serialisable, not analysable."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import FrameElement, Node

        return MeshModel(
            nodes={"1": Node("1", 1, 0.0, 0.0, 0.0)},
            frame_elements={"1": FrameElement("1", 1, "1", "1")},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
        )

    def test_mesh_snapshot_only_supports_the_mesh_view(self):
        """An already-meshed snapshot has no input for an analysis."""
        from examples.view_model import check_result_supported

        check_result_supported(self._mesh_snapshot(), "mesh")  # no raise

        with pytest.raises(SystemExit) as exc:
            check_result_supported(self._mesh_snapshot(), "static")

        assert "mesh only" in str(exc.value)

    def test_sap_model_snapshot_supports_every_result(self):
        from examples.view_model import check_result_supported

        check_result_supported(_selection_model(), "static")  # no raise

    def test_newer_schema_version_is_rejected(self, tmp_path):
        """A snapshot from a newer build is refused, not mis-decoded."""
        import json as _json

        from examples.view_model import load_model
        from fea_toolkit.io.model_codec import SCHEMA_KEY, model_to_dict

        payload = model_to_dict(_selection_model())
        payload[SCHEMA_KEY] = 999
        path = tmp_path / "future.json"
        path.write_text(_json.dumps(payload), encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            load_model(path)

        assert "upgrade fea_toolkit" in str(exc.value)
