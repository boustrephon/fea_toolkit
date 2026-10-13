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
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model


def _l_frame() -> SAPModelData:
    """Minimal one-bay L-frame: a column (along Z) + a beam (along X)."""
    nodes = {
        "1": Node("1", 1, 0.0, 0.0, 0.0),
        "2": Node("2", 2, 0.0, 0.0, 2.0),
        "3": Node("3", 3, 3.5, 0.0, 2.0),
    }
    # Planar X-Z model in the 3D engine: the out-of-plane DOFs (UY, RX,
    # RZ) at the in-plane nodes are restrained.  Sections carry explicit
    # A/I/J (400 x 300) so the elastic builder produces non-degenerate
    # stiffness (bare Section subclasses default A/I to 0).
    restraints = {
        "1": Restraint([1, 1, 1, 1, 1, 1]),
        "2": Restraint([0, 1, 0, 0, 0, 1]),
        "3": Restraint([0, 1, 0, 0, 0, 1]),
    }
    materials = {"M": Material(name="M", type="Steel", E_mod=200e6)}
    sections = {
        "COL": RectangularSection(
            name="COL",
            shape="Rectangular",
            material="M",
            depth=0.4,
            bf=0.3,
            A=0.12,
            I33=1.6e-3,
            I22=9.0e-4,
            J=2.0e-3,
        ),
        "BEAM": RectangularSection(
            name="BEAM",
            shape="Rectangular",
            material="M",
            depth=0.4,
            bf=0.3,
            A=0.12,
            I33=1.6e-3,
            I22=9.0e-4,
            J=2.0e-3,
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
        """``rigid_end_zones=True`` (beam fallback) -> one rigid link per member end."""
        mesh = preprocess_model(
            _l_frame(), {"rigid_end_zones": True, "rigid_offset_method": "beam"}
        )
        assert len(mesh.offset_rigid_links) == 2
        link_ids = {link[0] for link in mesh.offset_rigid_links}
        assert "1_rigid_j" in link_ids
        assert "2_rigid_i" in link_ids

    def test_explicit_offsets_win(self):
        """Explicit ``frame_end_offsets`` replace auto-derived values."""
        md = _l_frame()
        md.frame_end_offsets = {"1": FrameEndOffset(end_j=0.3)}
        mesh = preprocess_model(md, {"rigid_end_zones": True, "rigid_offset_method": "beam"})
        link_ids = {link[0] for link in mesh.offset_rigid_links}
        assert "1_rigid_j" in link_ids
        assert "2_rigid_i" in link_ids
        # Column top offset node at z = 2.0 - 0.3 = 1.7 (explicit end_j wins
        # over the auto-derived 0.2).
        assert mesh.nodes["1_off_j"].z == pytest.approx(1.7)

    def test_orphan_joint_node_retained(self):
        """The joint node survives the orphan-node step (singularity fix).

        After ``apply_frame_end_offsets`` rewires the members to offset
        nodes, the original joint node ("2") is referenced only by the
        rigid-link bookkeeping.  Dropping it would leave the links
        unconnected -> singular stiffness matrix (previously observed as
        ``BandGenLinLapackSolver ... matrix singular U(i,i)=0``).
        """
        mesh = preprocess_model(
            _l_frame(), {"rigid_end_zones": True, "rigid_offset_method": "beam"}
        )
        assert "2" in mesh.nodes
        assert len(mesh.offset_rigid_links) == 2

    def test_absolute_config(self):
        """``rigid_offset_absolute`` overrides the derived ``factor x D``."""
        mesh = preprocess_model(
            _l_frame(),
            {
                "rigid_end_zones": True,
                "rigid_offset_absolute": 0.25,
                "rigid_offset_method": "beam",
            },
        )
        # Column top offset node at z = 2.0 - 0.25 = 1.75.
        assert mesh.nodes["1_off_j"].z == pytest.approx(1.75)
        # Beam left offset node at x = 0.0 + 0.25 = 0.25.
        assert mesh.nodes["2_off_i"].x == pytest.approx(0.25)

    def test_joint_extents_config(self):
        """``joint_extents`` consumes the derived offset -> no rigid links."""
        mesh = preprocess_model(
            _l_frame(),
            {
                "rigid_end_zones": True,
                "joint_extents": {"2": 0.3},
                "rigid_offset_method": "beam",
            },
        )
        assert mesh.offset_rigid_links == []

    def test_offset_nodes_excluded_from_storey_diaphragms(self):
        """Offset nodes are not swept into storey-detected diaphragm groups.

        ``rigid_diaphragms: True`` forces storey-based detection (Path B),
        which clusters nodes by Z.  The column's offset node (z = 2 − 0.2 =
        1.8) and the beam's offset node (z = 2.0) sit inside the top storey's
        clustering band and would otherwise be swept into the diaphragm —
        double-constraining rigid-link slaves.  They must be excluded.
        """
        mesh = preprocess_model(
            _l_frame(),
            {
                "rigid_end_zones": True,
                "rigid_offset_method": "beam",
                "rigid_diaphragms": True,
            },
        )
        all_ids = [nid for _z, ids in mesh.diaphragm_components for nid in ids]
        # The real joints remain in the diaphragm groups…
        assert "2" in all_ids
        assert "3" in all_ids
        # …but no internal offset node is swept in.
        assert not any(nid.endswith(("_off_i", "_off_j")) for nid in all_ids)


class TestBuilderRigidEndZones:
    """Analysis-level behaviour of the frame member rigid end zones."""

    def test_mpc_links_build_and_run_static(self):
        """``rigid_link_mpc=True`` emits MPC rigid links; a static step runs.

        Regression for the two failure modes the MPC path fixed: (a) the
        orphan-node step dropped the joint nodes that only the rigid links
        reference (singular matrix), and (b) very stiff elastic links
        ill-condition the system under the solver.
        """
        import openseespy.opensees as ops
        from openseespy.opensees import wipe

        cfg = {"rigid_end_zones": True, "rigid_link_mpc": True}
        mesh = preprocess_model(_l_frame(), cfg)
        assert len(mesh.offset_rigid_links) == 2
        builder = AnalysisBuilder(mesh, cfg)
        try:
            builder.build_domain()
            # The joint node the orphan-node fix protects is in the domain.
            assert mesh.nodes["2"].node_tag in ops.getNodeTags()
            tip = mesh.nodes["3"].node_tag
            base = mesh.nodes["1"].node_tag
            ops.timeSeries("Linear", 1)
            ops.pattern("Plain", 1, 1)
            ops.load(tip, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            ops.constraints("Transformation")
            ops.numberer("RCM")
            ops.system("BandGeneral")
            ops.test("NormDispIncr", 1e-8, 30)
            ops.algorithm("Newton")
            ops.integrator("LoadControl", 1.0)
            ops.analysis("Static")
            assert ops.analyze(1) == 0
            # Load path through the MPCs: tip load equilibrated at the base.
            ops.reactions()
            assert abs(ops.nodeReaction(base, 1) + 100.0) < 1e-4
        finally:
            wipe()

    def test_beam_fallback_creates_elastic_links(self):
        """``rigid_offset_method="beam"`` emits stiff elastic rigid links."""
        import openseespy.opensees as ops
        from openseespy.opensees import wipe

        cfg = {"rigid_end_zones": True, "rigid_offset_method": "beam"}
        mesh = preprocess_model(_l_frame(), cfg)
        builder = AnalysisBuilder(mesh, cfg)
        try:
            builder.build_domain()
            # 2 frame elements + 2 elastic rigid links.
            assert len(ops.getEleTags()) == 4
            assert builder._rigid_section_tag is not None
        finally:
            wipe()

    def test_mpc_method_key_emits_mpc(self):
        """``rigid_offset_method="mpc"`` emits MPCs, not elastic rigid links.

        Pins the single-spelling fix: before it, the builder read the legacy
        ``rigid_link_mpc`` boolean directly, so the new ``"mpc"`` key silently
        degraded to stiff elastic links (2 frame + 2 link elements = 4).
        MPCs add no elements, so the domain holds only the 2 frame elements.
        """
        import openseespy.opensees as ops
        from openseespy.opensees import wipe

        cfg = {"rigid_end_zones": True, "rigid_offset_method": "mpc"}
        mesh = preprocess_model(_l_frame(), cfg)
        assert len(mesh.offset_rigid_links) == 2
        builder = AnalysisBuilder(mesh, cfg)
        try:
            builder.build_domain()
            assert len(ops.getEleTags()) == 2
        finally:
            wipe()

    def test_mpc_with_diaphragms_warns(self):
        """MPC end offsets coexisting with diaphragms emit a conflict warning."""
        import warnings

        with pytest.warns(UserWarning, match="coexists with diaphragm/BODY"):
            preprocess_model(
                _l_frame(),
                {
                    "rigid_end_zones": True,
                    "rigid_link_mpc": True,
                    "rigid_diaphragms": True,
                },
            )
        # Without diaphragms the same MPC offsets are legitimate — no warning.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            preprocess_model(_l_frame(), {"rigid_end_zones": True, "rigid_link_mpc": True})

    def test_rigid_zones_stiffen_lateral_response(self):
        """Rigid end zones shorten the members -> smaller lateral drift."""
        import openseespy.opensees as ops
        from openseespy.opensees import wipe

        def drift(rigid):
            cfg = {"rigid_end_zones": rigid, "rigid_link_mpc": rigid}
            mesh = preprocess_model(_l_frame(), cfg)
            builder = AnalysisBuilder(mesh, cfg)
            try:
                builder.build_domain()
                tip = mesh.nodes["3"].node_tag
                ops.timeSeries("Linear", 1)
                ops.pattern("Plain", 1, 1)
                ops.load(tip, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                ops.constraints("Transformation")
                ops.numberer("RCM")
                ops.system("BandGeneral")
                ops.test("NormDispIncr", 1e-8, 30)
                ops.algorithm("Newton")
                ops.integrator("LoadControl", 1.0)
                ops.analysis("Static")
                assert ops.analyze(1) == 0
                return float(abs(ops.nodeDisp(tip, 1)))
            finally:
                wipe()

        assert drift(True) < drift(False)
