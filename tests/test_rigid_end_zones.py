"""Tests for preprocessor-level rigid end zones (Level 1 joint modelling).

Covers :func:`fea_toolkit.model.geometry.derive_rigid_end_offsets` and its
wiring through :func:`fea_toolkit.opensees.preprocessor.preprocess_model`
via the ``rigid_end_zones`` / ``rigid_offset_factor`` /
``rigid_offset_absolute`` / ``joint_extents`` config options.
"""

import pytest

from fea_toolkit.model.geometry import derive_rigid_end_offsets
from fea_toolkit.model.sap_data import (
    FrameElement,
    FrameEndOffset,
    Material,
    Node,
    RectangularSection,
    Restraint,
    SAPModelData,
)
from fea_toolkit.opensees.preprocessor import preprocess_model


def _l_frame() -> SAPModelData:
    """Minimal one-bay L-frame: a column (along Z) + a beam (along X)."""
    nodes = {
        "1": Node("1", 1, 0.0, 0.0, 0.0),
        "2": Node("2", 2, 0.0, 0.0, 2.0),
        "3": Node("3", 3, 3.5, 0.0, 2.0),
    }
    restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
    materials = {"M": Material(name="M", type="Steel", E_mod=200e6)}
    sections = {
        "COL": RectangularSection(name="COL", shape="Rectangular", material="M", depth=0.4, bf=0.3),
        "BEAM": RectangularSection(
            name="BEAM", shape="Rectangular", material="M", depth=0.4, bf=0.3
        ),
    }
    frame_elements = {
        "1": FrameElement("1", 1, "1", "2"),  # column, node 1 -> 2 (Z)
        "2": FrameElement("2", 2, "2", "3"),  # beam, node 2 -> 3 (X)
    }
    frame_assignments = {"1": "COL", "2": "BEAM"}
    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments=frame_assignments,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )


class TestDeriveRigidEndOffsets:
    def test_orthogonal_beam_column_half_depth(self):
        """Each member end at the joint gets 0.5 x the intersecting depth."""
        md = _l_frame()
        offs = derive_rigid_end_offsets(
            md.frame_elements, md.frame_assignments, md.nodes, md.sections
        )
        # Column end_j at node 2 -> 0.5 x beam depth 0.4 = 0.2; base -> 0.
        assert offs["1"].end_i == 0.0
        assert offs["1"].end_j == pytest.approx(0.2)
        # Beam end_i at node 2 -> 0.2; free end_j -> 0.
        assert offs["2"].end_i == pytest.approx(0.2)
        assert offs["2"].end_j == 0.0

    def test_collinear_connector_ignored(self):
        """Two collinear beams meeting at a node produce no offset."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 3.0, 0.0, 0.0),
            "3": Node("3", 3, 6.0, 0.0, 0.0),
        }
        sections = {
            "B": RectangularSection(name="B", shape="Rectangular", material="M", depth=0.4, bf=0.3)
        }
        elements = {
            "1": FrameElement("1", 1, "1", "2"),
            "2": FrameElement("2", 2, "2", "3"),
        }
        assignments = {"1": "B", "2": "B"}
        assert derive_rigid_end_offsets(elements, assignments, nodes, sections) == {}

    def test_absolute_override(self):
        md = _l_frame()
        offs = derive_rigid_end_offsets(
            md.frame_elements,
            md.frame_assignments,
            md.nodes,
            md.sections,
            absolute=0.25,
        )
        assert offs["1"].end_j == pytest.approx(0.25)
        assert offs["2"].end_i == pytest.approx(0.25)

    def test_factor_scaling(self):
        md = _l_frame()
        offs = derive_rigid_end_offsets(
            md.frame_elements,
            md.frame_assignments,
            md.nodes,
            md.sections,
            factor=1.0,
        )
        assert offs["1"].end_j == pytest.approx(0.4)  # full intersecting depth

    def test_joint_extents_subtracted_and_clamped(self):
        md = _l_frame()
        # Joint panel larger than the derived offset -> fully cancelled.
        offs = derive_rigid_end_offsets(
            md.frame_elements,
            md.frame_assignments,
            md.nodes,
            md.sections,
            joint_extents={"2": 0.3},
        )
        assert offs == {}
        # Smaller joint panel -> partial offset remains.
        offs2 = derive_rigid_end_offsets(
            md.frame_elements,
            md.frame_assignments,
            md.nodes,
            md.sections,
            joint_extents={"2": 0.1},
        )
        assert offs2["1"].end_j == pytest.approx(0.1)
        assert offs2["2"].end_i == pytest.approx(0.1)


class TestPreprocessorRigidEndZones:
    def test_default_off_no_links(self):
        """``rigid_end_zones`` defaults to off -> no rigid links emitted."""
        mesh = preprocess_model(_l_frame(), {})
        assert mesh.offset_rigid_links == []

    def test_enabled_creates_links(self):
        """``rigid_end_zones=True`` -> one rigid link per member end at the joint."""
        mesh = preprocess_model(_l_frame(), {"rigid_end_zones": True})
        assert len(mesh.offset_rigid_links) == 2
        link_ids = {link[0] for link in mesh.offset_rigid_links}
        assert "1_rigid_j" in link_ids
        assert "2_rigid_i" in link_ids

    def test_explicit_offsets_win(self):
        """Explicit ``frame_end_offsets`` replace auto-derived values."""
        md = _l_frame()
        md.frame_end_offsets = {"1": FrameEndOffset(end_j=0.3)}
        mesh = preprocess_model(md, {"rigid_end_zones": True})
        link_ids = {link[0] for link in mesh.offset_rigid_links}
        assert "1_rigid_j" in link_ids
        assert "2_rigid_i" in link_ids
