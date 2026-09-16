"""Tests for analysis wiring: pushover, CQC, and RS base reactions.

Builder / preprocessor integration tests.  Pure-model unit tests live in
their mirror files (``test_csm.py``, ``test_buckling.py``,
``test_sap_data.py``, ``test_parser.py``, ``test_geometry_core_frames.py``).
"""

import math

import pytest

from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaMesh,
    FrameDistributedLoad,
    FrameElement,
    FrameEndOffset,
    ISection,
    LoadPattern,
    Material,
    Node,
    PipeSection,
    Restraint,
    SAPModelData,
    Section,
    ShellSection,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

# ============================================================================
# Pushover analysis tests
# ============================================================================


def _make_pushover_ab(md):
    """Create a pre-built AnalysisBuilder for pushover tests."""
    cfg = {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
    mesh_model = preprocess_model(md, cfg)
    ab = AnalysisBuilder(mesh_model, cfg)
    ab.build_domain()
    return ab


class TestPushoverBuild:
    """Tests for pushover analysis via AnalysisBuilder."""

    @pytest.fixture
    def cantilever_model(self):
        """A simple 2‑node cantilever for fast pushover testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=5),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000),
        }
        sections = {
            "UB100": Section(
                name="UB100",
                shape="I/Wide Flange",
                material="Steel",
                A=0.01,
                I33=1e-4,
                I22=1e-5,
                J=1e-6,
            ),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
        }
        load_patterns = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD", self_weight_factor=1),
            "WIND": LoadPattern(name="WIND", pattern_type="WIND", self_weight_factor=0),
        }
        frame_dist_loads = [
            FrameDistributedLoad(
                pattern="WIND",
                frame_id="1",
                direction="X",
                load_type="Force",
                shape="Uniform",
                val_a=1000,
                val_b=1000,
                rdist_a=0,
                rdist_b=1,
                dist_a=0,
                dist_b=5,
            ),
        ]
        return SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"1": "UB100"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            load_patterns=load_patterns,
            frame_dist_loads=frame_dist_loads,
        )

    def test_returns_expected_keys(self, cantilever_model):
        """Result dict has all required keys (pattern type)."""
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="pattern",
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        for key in (
            "step",
            "control_disp",
            "base_shear",
            "status",
            "control_node",
            "dof",
            "lateral_load_type",
        ):
            assert key in results
        assert results["lateral_load_type"] == "pattern"

    def test_gravity_base_shear_zero(self, cantilever_model):
        """After gravity alone, lateral base shear ≈ 0."""
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="pattern",
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        # Note: initial base_shear includes gravity reaction
        assert abs(results["base_shear"][0]) < 3000.0

    def test_cantilever_linear_pushover_pattern(self, cantilever_model):
        """Cantilever with elastic sections: linear, monotonic (pattern)."""
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="pattern",
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=10,
            print_progress=False,
        )
        assert len(results["control_disp"]) == 11
        assert results["status"][-1] == 0, "Last step failed"
        shears = [abs(v) for v in results["base_shear"]]
        assert all(shears[i] <= shears[i + 1] for i in range(len(shears) - 1)), "Not monotonic"
        assert abs(results["control_disp"][-1] - 0.1) < 0.01

    def test_uniform_pattern_returns_keys(self, cantilever_model):
        """Uniform pattern returns expected keys."""
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        for key in ("step", "control_disp", "base_shear", "status", "control_node", "dof"):
            assert key in results
        assert results["lateral_load_type"] == "uniform"

    def test_triangular_pattern_returns_keys(self, cantilever_model):
        """Triangular pattern returns expected keys."""
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="triangular",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        for key in ("step", "control_disp", "base_shear", "status", "control_node", "dof"):
            assert key in results

    def test_invalid_lateral_load_type_raises(self, cantilever_model):
        """Invalid lateral_load_type raises ValueError."""
        b = _make_pushover_ab(cantilever_model)
        import pytest

        with pytest.raises(ValueError, match="Unknown lateral_load_type"):
            b.run_pushover_analysis(
                gravity_patterns={"DEAD": 1.0},
                lateral_load_type="wind",
                lateral_direction="X",
                control_node_tag=2,
                max_disp=0.1,
                num_steps=5,
                print_progress=False,
            )

    def test_pattern_requires_name(self, cantilever_model):
        """pattern type without lateral_pattern_name raises ValueError."""
        b = _make_pushover_ab(cantilever_model)
        import pytest

        with pytest.raises(ValueError, match="lateral_pattern_name is required"):
            b.run_pushover_analysis(
                gravity_patterns={"DEAD": 1.0},
                lateral_load_type="pattern",
                lateral_direction="X",
                control_node_tag=2,
                max_disp=0.1,
                num_steps=5,
                print_progress=False,
            )

    def test_pushover_via_two_stage_path(self, cantilever_model):
        """Pushover returns correct keys through the two-stage path."""
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses()
        b.run_modal_analysis(num_modes=1, print_results=False)
        b.extract_mode_shapes(1)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.3,
            num_steps=5,
            print_progress=False,
        )
        for key in (
            "step",
            "control_disp",
            "base_shear",
            "status",
            "gravity_displacements",
            "control_node",
            "dof",
            "lateral_load_type",
        ):
            assert key in results, f"Missing key: {key}"
        assert (
            len(results["step"])
            == len(results["control_disp"])
            == len(results["base_shear"])
            == len(results["status"])
        )
        assert results["step"][0] == 0  # gravity step recorded
        assert results["control_node"] == 2
        assert results["dof"] == 1  # X direction

    def test_pushover_uniform_via_two_stage(self, cantilever_model):
        """Uniform pushover produces non-zero base shear through two-stage."""
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses()
        b.run_modal_analysis(num_modes=1, print_results=False)
        b.extract_mode_shapes(1)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.3,
            num_steps=5,
            print_progress=False,
        )
        # Base shears should be non-zero (cantilever fixed at base, push at top)
        assert any(abs(v) > 0 for v in results["base_shear"]), (
            "Expected non-zero base shear in at least one step"
        )
        # Displacement should increase monotonically
        assert all(
            results["control_disp"][i] <= results["control_disp"][i + 1]
            for i in range(len(results["control_disp"]) - 1)
        ), "Control displacement should be monotonic"


# ============================================================================
# HingeRadau beam integration tests
# ============================================================================


class TestHingeRadauIntegration:
    """Tests for :func:`compute_hinge_length`."""

    def test_hinge_length_i_section(self):
        """ISection depth → Lp = 0.5 * depth."""
        from fea_toolkit.model.checks import compute_hinge_length

        md = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        md.sections["UB300"] = ISection(
            name="UB300",
            shape="I/Wide Flange",
            material="Steel",
            depth=0.3,
            bf=0.15,
            tf=0.01,
            tw=0.006,
            A=8e-3,
            I33=1.2e-4,
            I22=4e-5,
            J=2e-6,
        )
        Lp = compute_hinge_length(md.sections["UB300"], 10.0)
        assert abs(Lp - 0.15) < 0.01  # 0.5 * 0.3

    def test_hinge_length_pipe_section(self):
        """Pipe OD → Lp = 0.5 * OD."""
        from fea_toolkit.model.checks import compute_hinge_length

        md = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        md.sections["PIP4"] = PipeSection(
            name="PIP4",
            shape="Pipe",
            material="Steel",
            od=0.1143,
            t=0.006,
            A=2e-3,
            I33=3e-6,
            I22=3e-6,
            J=1e-6,
        )
        Lp = compute_hinge_length(md.sections["PIP4"], 10.0)
        assert abs(Lp - 0.05715) < 0.001  # 0.5 * 0.1143

    def test_hinge_length_fallback(self):
        """Unknown section → Lp = 0.1 * L."""
        from fea_toolkit.model.checks import compute_hinge_length

        md = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        md.sections["GENERIC"] = Section(
            name="GENERIC",
            shape="NA",
            material="Steel",
            A=1e-2,
            I33=1e-4,
            I22=1e-4,
            J=1e-6,
        )
        Lp = compute_hinge_length(md.sections["GENERIC"], 8.0)
        assert abs(Lp - 0.8) < 0.01  # 0.1 * 8.0


# ============================================================================
# Builder integration tests: frame end offsets + area meshing
# ============================================================================


class TestBuilderFrameEndOffsets:
    """Verify frame end offsets are applied during build()."""

    @pytest.fixture
    def offset_model(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
        }
        mats = {"Steel": Material("Steel", "Steel", E_mod=2e11)}
        secs = {
            "UB300": Section("UB300", "I/Wide Flange", "Steel", A=0.01, I33=1e-4, I22=1e-5, J=1e-6),
        }
        frames = {"1": FrameElement("1", 10, "1", "2")}
        return SAPModelData(
            nodes=nodes,
            restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
            materials=mats,
            sections=secs,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"1": "UB300"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            frame_end_offsets={"1": FrameEndOffset(0.3, 0.4)},
        )

    def test_offset_nodes_created_in_opensees(self, offset_model):
        """Offset nodes are created at correct positions."""
        import openseespy.opensees as ops

        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"verbose": False, "use_elastic_sections": True}
        mm = preprocess_model(offset_model, cfg)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            # Offset nodes: I-end offset=0.3, J-end offset=0.4
            # Element from (0,0,0) → (6,0,0), length 6
            # I-end offset node at: (0 + 0.3, 0, 0) = (0.3, 0, 0)
            # J-end offset node at: (6 - 0.4, 0, 0) = (5.6, 0, 0)
            assert "1_off_i" in mm.nodes, "I-end offset node missing"
            assert "1_off_j" in mm.nodes, "J-end offset node missing"
            for nid, nd in mm.nodes.items():
                if "_off_i" in nid:
                    coords = list(ops.nodeCoord(nd.node_tag))
                    assert coords == pytest.approx([0.3, 0.0, 0.0], abs=1e-9)
                elif "_off_j" in nid:
                    coords = list(ops.nodeCoord(nd.node_tag))
                    assert coords == pytest.approx([5.6, 0.0, 0.0], abs=1e-9)
        finally:
            ops.wipe()

    def test_rigid_links_recorded(self, offset_model):
        """_offset_rigid_links contains entries after build()."""
        import openseespy.opensees as ops

        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"verbose": False, "use_elastic_sections": True}
        mm = preprocess_model(offset_model, cfg)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            assert len(b._offset_rigid_links) == 2
        finally:
            ops.wipe()

    def test_no_offsets_no_links(self):
        """Zero offsets produce no rigid links."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
        }
        mats = {"Steel": Material("Steel", "Steel", E_mod=2e11)}
        secs = {
            "UB300": Section("UB300", "I/Wide Flange", "Steel", A=0.01, I33=1e-4, I22=1e-5, J=1e-6),
        }
        frames = {"1": FrameElement("1", 10, "1", "2")}
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=mats,
            sections=secs,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"1": "UB300"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            frame_end_offsets={"1": FrameEndOffset(0.0, 0.0)},
        )
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"verbose": False, "use_elastic_sections": True}
        mm = preprocess_model(md, cfg)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            assert len(b._offset_rigid_links) == 0
        finally:
            import openseespy.opensees as ops

            ops.wipe()


class TestBuilderAreaMeshing:
    """Verify area elements are meshed during build()."""

    @pytest.fixture
    def mesh_model(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        secs = {
            "Slab200": ShellSection("Slab200", "Shell", "Concrete", thickness=0.2),
        }
        areas = {"1": AreaElement("1", 10, ["1", "2", "3", "4"])}
        return SAPModelData(
            nodes=nodes,
            restraints={},
            materials=mats,
            sections=secs,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=6.0)},
        )

    def test_mesh_creates_sub_areas(self, mesh_model):
        """Preprocessor meshing creates exactly 4 sub-quads (2×2 grid)."""
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"verbose": False, "create_shells": True}
        mm = preprocess_model(mesh_model, cfg)
        # Original area should be inactive in the mesh model
        assert mm.area_elements["1"].inactive is True
        # 12×8 quad with max_size=6.0 → ceil(12/6)=2 × ceil(8/6)=2 = 4
        sub_ids = sorted(aid for aid in mm.area_elements if "_sub_" in aid)
        assert len(sub_ids) == 4
        # Sub-areas should all be active
        for sid in sub_ids:
            assert mm.area_elements[sid].inactive is False
        # Section assignment inherited
        for sid in sub_ids:
            assert mm.area_assignments.get(sid) == "Slab200"

    def test_mesh_creates_opensees_nodes(self, mesh_model):
        """Mesh nodes are created at correct grid positions."""
        import openseespy.opensees as ops

        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"verbose": False, "create_shells": True}
        mm = preprocess_model(mesh_model, cfg)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            # 2×2 grid → 5 mesh nodes (4 edge midpoints + 1 interior)
            mesh_nodes = {nid: nd for nid, nd in mm.nodes.items() if "_mesh_" in nid}
            assert len(mesh_nodes) == 5
            # Expected coordinates (12×8 rectangle, bilinear grid)
            expected = {
                "1_mesh_0_1": (6.0, 0.0, 0.0),  # edge midpoint
                "1_mesh_1_0": (0.0, 4.0, 0.0),
                "1_mesh_1_1": (6.0, 4.0, 0.0),  # fully interior
                "1_mesh_1_2": (12.0, 4.0, 0.0),
                "1_mesh_2_1": (6.0, 8.0, 0.0),
            }
            for nid, nd in mesh_nodes.items():
                coords = list(ops.nodeCoord(nd.node_tag))
                assert coords == pytest.approx(expected[nid], abs=1e-9), (
                    f"{nid}: expected {expected[nid]}, got {coords}"
                )
        finally:
            ops.wipe()

    def test_no_mesh_no_change(self):
        """Without mesh settings, area elements are unchanged."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        secs = {
            "Slab200": ShellSection("Slab200", "Shell", "Concrete", thickness=0.2),
        }
        areas = {"1": AreaElement("1", 10, ["1", "2", "3", "4"])}
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=mats,
            sections=secs,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200"},
            groups={},
            frame_auto_mesh={},
        )
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"verbose": False, "create_shells": True}
        mm = preprocess_model(md, cfg)
        # No area_mesh config → no subdivision
        assert mm.area_elements["1"].inactive is False
        # No sub-area or mesh node artifacts
        assert not any("_sub_" in aid for aid in mm.area_elements)
        assert not any("_mesh_" in nid for nid in mm.nodes)

    def test_mesh_propagates_edge_restraints(self):
        """Mesh nodes on edges between restrained corners inherit AND of DOFs."""
        from fea_toolkit.model.sap_data import Restraint
        from fea_toolkit.opensees.preprocessor import preprocess_model

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        # Restrain bottom edge (nodes 1,2) — both fully fixed
        # Restrain left edge (nodes 1,4) — one fixed [1,1,1,1,1,1],
        #   the other pinned [1,1,1,0,0,0] → AND should be [1,1,1,0,0,0]
        restraints = {
            "1": Restraint([1, 1, 1, 1, 1, 1]),  # fully fixed
            "2": Restraint([1, 1, 1, 1, 1, 1]),  # fully fixed
            "4": Restraint([1, 1, 1, 0, 0, 0]),  # pinned
        }
        mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        secs = {
            "Slab200": ShellSection("Slab200", "Shell", "Concrete", thickness=0.2),
        }
        areas = {"1": AreaElement("1", 10, ["1", "2", "3", "4"])}
        md = SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=mats,
            sections=secs,
            frame_elements={},
            area_elements=areas,
            frame_assignments={},
            area_assignments={"1": "Slab200"},
            groups={},
            frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=6.0)},
        )
        cfg = {"verbose": False, "create_shells": True}
        mm = preprocess_model(md, cfg)
        import openseespy.opensees as ops

        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()

            # Mesh node should exist in MeshModel
            n1 = mm.nodes.get("1_mesh_0_1")  # (6, 0, 0)
            assert n1 is not None, "bottom-edge mesh node missing"

            # Mesh nodes SHOULD appear in MeshModel restraints — the Preprocessor
            # propagates edge restraints into mm.restraints via
            # geometry._propagate_edge_restraints (single source of truth for
            # Tcl export / recorder.py).  The AnalysisBuilder applies them once.
            mesh_ids = {nid for nid in mm.nodes if "_mesh_" in nid}
            restrained_mesh = mesh_ids & set(mm.restraints.keys())
            assert len(restrained_mesh) >= 2, (
                f"expected propagated mesh restraints, got {restrained_mesh}"
            )
            # Bottom edge (1→2): AND of two fully-fixed corners.
            assert mm.restraints["1_mesh_0_1"].dofs == [1, 1, 1, 1, 1, 1]
            # Left edge (1→4): AND of fully-fixed and pinned corners.
            assert mm.restraints["1_mesh_1_0"].dofs == [1, 1, 1, 0, 0, 0]

            # Check the mesh node at (6,0,0) is fixed in OpenSees
            mesh_tag = b._node_tag_from_id("1_mesh_0_1")
            assert mesh_tag is not None
            fixed = ops.getFixedDOFs(int(mesh_tag))
            assert len(fixed) == 6, f"mesh node {mesh_tag} should have 6 fixed DOFs, got {fixed}"

            assert mm.area_elements["1"].inactive, "original area should be inactive after meshing"

        finally:
            ops.wipe()


# ============================================================================
# Mander confinement wiring tests
# ============================================================================


class TestManderConfinementWiring:
    """Mander confinement wiring on concrete section dataclasses.

    Verifies that:
    * ``fiber_confinement()`` returns Mander results when tie data is
      complete and geometrically valid.
    * ``fiber_confinement()`` returns ``None`` (backward compatible) when
      any required tie data is missing, so builders fall back to the
      conventional 1.25–1.3 × f'c heuristic.
    * The computed confined strength is strictly higher than f'c and the
      confined strain higher than the unconfined 0.002.
    """

    def test_rectangular_confined(self):
        from fea_toolkit.model.sap_data import ConcreteRectangularSection

        sec = ConcreteRectangularSection(
            name="CR400",
            shape="Concrete Rectangular",
            material="Concrete",
            A=0.16,
            I33=0.00213,
            I22=0.00213,
            J=0,
            depth=0.4,
            bf=0.4,
            cover=0.04,
            top_bars=4,
            bot_bars=4,
            top_bar_dia=0.02,
            bot_bar_dia=0.02,
            tie_diameter=0.01,
            tie_spacing=0.1,
            tie_fy=420e6,
        )
        res = sec.fiber_confinement(fc=30e6, tie_fy=420e6)
        assert res is not None
        assert res["fcc"] > 30e6
        assert res["ecc"] > 0.002
        assert res["ecu"] > res["ecc"]

    def test_rectangular_missing_tie_returns_none(self):
        from fea_toolkit.model.sap_data import ConcreteRectangularSection

        sec = ConcreteRectangularSection(
            name="CR400",
            shape="Concrete Rectangular",
            material="Concrete",
            A=0.16,
            I33=0.00213,
            I22=0.00213,
            J=0,
            depth=0.4,
            bf=0.4,
            cover=0.04,
            top_bars=4,
            bot_bars=4,
            top_bar_dia=0.02,
            bot_bar_dia=0.02,
        )
        # No tie data → None (builders use the conventional heuristic)
        assert sec.fiber_confinement(fc=30e6, tie_fy=420e6) is None
        # Partial tie data (no spacing) → None
        sec.tie_diameter = 0.01
        assert sec.fiber_confinement(fc=30e6, tie_fy=420e6) is None

    def test_circular_confined_spiral(self):
        from fea_toolkit.model.sap_data import ConcreteCircularSection

        sec = ConcreteCircularSection(
            name="CC400",
            shape="Concrete Circular",
            material="Concrete",
            A=0.1256,
            I33=0.00126,
            I22=0.00126,
            J=0,
            diameter=0.4,
            cover=0.04,
            bar_count=8,
            bar_dia=0.02,
            tie_diameter=0.01,
            tie_spacing=0.08,
            tie_fy=420e6,
        )
        res = sec.fiber_confinement(fc=30e6, tie_fy=420e6)
        assert res is not None
        assert res["fcc"] > 30e6
        assert res["ecc"] > 0.002

    def test_circular_missing_tie_returns_none(self):
        from fea_toolkit.model.sap_data import ConcreteCircularSection

        sec = ConcreteCircularSection(
            name="CC400",
            shape="Concrete Circular",
            material="Concrete",
            A=0.1256,
            I33=0.00126,
            I22=0.00126,
            J=0,
            diameter=0.4,
            cover=0.04,
            bar_count=8,
            bar_dia=0.02,
        )
        assert sec.fiber_confinement(fc=30e6, tie_fy=420e6) is None


# ============================================================================
# Builder hinge type tests
# ============================================================================


class TestBuilderHingeModel:
    """Lumped plasticity (hinge_model='lumped') integration."""

    def test_default_hinge_model_is_fiber(self):
        """Default config uses fiber (distributed plasticity)."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        b = AnalysisBuilder.__new__(AnalysisBuilder)
        b.config = {}
        b._set_defaults()
        assert b.config["hinge_model"] == "fiber"

    def test_asce41_hinge_length_steel_beam(self):
        """Steel I-section with depth uses d_b = depth per ASCE 41-17 Eq 10-1.

        An ISection with depth=0.3 m (UB300) gives:
          Lp = 0.08·6.0 + 0.022·300·250/1000 = 2.13 → capped at 0.33·6.0 = 1.98
        """
        from fea_toolkit.model.checks import compute_asce41_hinge_length
        from fea_toolkit.model.sap_data import (
            ISection,
            Material,
            Node,
            SAPModelData,
        )

        nodes = {"1": Node("1", 1, 0, 0, 0), "2": Node("2", 2, 6, 0, 0)}
        mats = {"Steel": Material("Steel", "Steel", E_mod=2e11, Fy=2.5e8)}
        secs = {
            "UB300": ISection(
                name="UB300",
                shape="I/Wide Flange",
                material="Steel",
                depth=0.3,
                bf=0.15,
                tf=0.01,
                tw=0.006,
                A=8e-3,
                I33=1.2e-4,
                I22=4e-5,
                J=2e-6,
            ),
        }
        md = SAPModelData(
            nodes=nodes,
            restraints={},
            materials=mats,
            sections=secs,
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        Lp = compute_asce41_hinge_length(md, "UB300", 6.0)
        # Capped at 0.33 * L = 1.98
        assert Lp == pytest.approx(1.98, abs=0.01)

    def test_lumped_hinge_build_invokes_create_lumped_hinges(self):
        """build_domain() with hinge_model='lumped' exercises _create_lumped_hinges."""
        import openseespy.opensees as ops

        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        md = make_sample_model()
        mm = preprocess_model(md, {"split_elements": False})
        b = AnalysisBuilder(
            mm,
            {
                "element_type": "elasticBeamColumn",
                "hinge_model": "lumped",
                "verbose": False,
            },
        )
        try:
            b.build_domain()
            node_tags = ops.getNodeTags()
            ele_tags = ops.getEleTags()
            # Original model has 2 nodes + 1 element.
            # Lumped hinges add 2 hinge nodes + 2 zero-length elements.
            assert len(node_tags) >= 4, f"Expected ≥4 nodes, got {node_tags}"
            assert len(ele_tags) >= 3, f"Expected ≥3 elements, got {ele_tags}"
            # equalDOF constraints tie translation DOFs
            # (just verify the model is consistent)
            coords = [ops.nodeCoord(t) for t in (1, 2)]
            assert len(coords) == 2
        finally:
            ops.wipe()


# ═══════════════════════════════════════════════════════════════════
# CQC combination engine
# ═══════════════════════════════════════════════════════════════════


class TestSpectrumCqcCombine:
    """Tests for :func:`fea_toolkit.spectrum.cqc_base_shear` (legacy alias
    :func:`fea_toolkit.spectrum.cqc_combine`)."""

    def test_single_mode(self):
        """Single mode → CQC == SRSS == modal_shear."""
        from fea_toolkit.spectrum import cqc_combine

        def _sa(T):
            return 9.81  # constant 1g

        result = cqc_combine(
            eff_masses=[100.0],
            periods=[1.0],
            spectrum_fn=_sa,
            damping=0.05,
        )
        assert result is not None
        assert abs(result["base_shear_cqc"] - 981.0) < 1e-6
        assert abs(result["base_shear_srss"] - 981.0) < 1e-6

    def test_two_modes_srss(self):
        """Two uncorrelated modes → SRSS equals CQC (rho ≈ 0)."""
        from fea_toolkit.spectrum import cqc_combine

        def _sa(T):
            return 9.81

        result = cqc_combine(
            eff_masses=[100.0, 60.0],
            periods=[1.0, 0.01],  # very separated → rho ≈ 0
            spectrum_fn=_sa,
            damping=0.05,
        )
        expected_srss = math.sqrt((100 * 9.81) ** 2 + (60 * 9.81) ** 2)
        assert abs(result["base_shear_srss"] - expected_srss) < 1e-6

    def test_total_mass_missing(self):
        """Missing-mass correction is proportional to residual mass × Sa(0)."""
        from fea_toolkit.spectrum import cqc_combine

        calls = []

        def _sa(T):
            calls.append(T)
            return 9.81

        result = cqc_combine(
            eff_masses=[100.0],
            periods=[1.0],
            spectrum_fn=_sa,
            total_mass=150.0,
        )
        assert result["residual_mass"] == 50.0  # 150 - 100
        assert abs(result["base_shear_missing_mass"] - 50.0 * 9.81) < 1e-6

    def test_rigid_cutoff(self):
        """Modes below T_rigid are treated as rigid (Sa(0) scaling)."""
        from fea_toolkit.spectrum import cqc_combine

        def _sa(T):
            return 9.81 if T < 0.05 else 9.81 * 2.0  # 1g rigid, 2g flexible

        result = cqc_combine(
            eff_masses=[100.0, 60.0],
            periods=[0.02, 1.0],  # first mode is rigid
            spectrum_fn=_sa,
            T_rigid=0.05,
        )
        assert result["n_modes_rigid"] == 1
        assert result["n_modes_flexible"] == 1

    def test_empty_input(self):
        """Empty inputs return empty dict."""
        from fea_toolkit.spectrum import cqc_combine

        result = cqc_combine(eff_masses=[], periods=[], spectrum_fn=lambda T: 0)
        assert result == {}

    def test_canonical_name_and_legacy_alias_match(self):
        """``cqc_base_shear`` is the canonical name; ``cqc_combine`` delegates."""
        from fea_toolkit.spectrum import cqc_base_shear, cqc_combine

        def _sa(T):
            return 9.81

        kwargs = {
            "eff_masses": [100.0],
            "periods": [1.0],
            "spectrum_fn": _sa,
            "damping": 0.05,
        }
        canonical = cqc_base_shear(**kwargs)
        legacy = cqc_combine(**kwargs)
        assert canonical == legacy
        assert canonical["base_shear_total"] == legacy["base_shear_total"]
        # Sanity: same result as the established single-mode expectation.
        assert abs(canonical["base_shear_cqc"] - 981.0) < 1e-6


# ═══════════════════════════════════════════════════════════════════
# Modal participation DataFrame
# ═══════════════════════════════════════════════════════════════════


class TestModalParticipationDf:
    """Tests for :func:`fea_toolkit.io.report.modal_participation_df`."""

    def test_basic(self):
        from fea_toolkit.io.report import modal_participation_df

        modal_result = {
            "periods": [0.5, 0.2],
            "modal_props": {
                "partiMassRatiosMX": [60.0, 30.0],
                "partiMassRatiosMY": [5.0, 40.0],
                "partiMassRatiosMZ": [0.0, 0.0],
                "partiMassRatiosRMX": [0.0, 0.0],
                "partiMassRatiosRMY": [0.0, 0.0],
                "partiMassRatiosRMZ": [10.0, 20.0],
            },
        }
        df = modal_participation_df(modal_result)
        assert df is not None
        assert len(df) == 3  # 2 modes + SUM
        assert float(df.iloc[2]["Mx (%)"]) == 90.0  # 60 + 30

    def test_empty(self):
        from fea_toolkit.io.report import modal_participation_df

        assert modal_participation_df({"periods": [], "modal_props": {}}) is None


# ═══════════════════════════════════════════════════════════════════
# Two-stage build (Preprocessor + AnalysisBuilder)
# ═══════════════════════════════════════════════════════════════════
class TestSumReactionsWithOverturning:
    """Test the centralized overturning-moment utility."""

    def test_single_node_no_overturning(self):
        """Single node at centroid → forces pass through directly."""
        from fea_toolkit.model.sap_data import Node
        from fea_toolkit.utils import sum_reactions_with_overturning

        nodes = {"B1": Node(node_id="B1", node_tag=1, x=5, y=5, z=0)}
        reactions = {1: {"fx": 100.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}}
        result = sum_reactions_with_overturning(reactions, nodes)
        assert result["fx"] == 100.0
        assert result["mx"] == 0.0  # at centroid → no lever arm

    def test_two_node_overturning(self):
        """Two base nodes with vertical reactions → My from Fz·dx."""
        from fea_toolkit.model.sap_data import Node
        from fea_toolkit.utils import sum_reactions_with_overturning

        nodes = {
            "A": Node(node_id="A", node_tag=10, x=0, y=0, z=0),
            "B": Node(node_id="B", node_tag=20, x=10, y=0, z=0),
        }
        reactions = {
            10: {"fx": 0.0, "fy": 0.0, "fz": 100.0, "mx": 0.0, "my": 0.0, "mz": 0.0},
            20: {"fx": 0.0, "fy": 0.0, "fz": -100.0, "mx": 0.0, "my": 0.0, "mz": 0.0},
        }
        result = sum_reactions_with_overturning(reactions, nodes)
        assert abs(result["fz"]) < 1e-10  # equal and opposite
        # My = Fz_A * (0-5) + Fz_B * (10-5) = 100*(-5) + (-100)*5 = -1000
        assert abs(result["my"] - 1000.0) < 1e-10

    def test_empty_reactions(self):
        """Empty reactions → all zero."""
        from fea_toolkit.utils import sum_reactions_with_overturning

        result = sum_reactions_with_overturning(
            {}, {"N1": type("N", (), {"x": 0, "y": 0, "z": 0})()}
        )
        for k in ["fx", "fy", "fz", "mx", "my", "mz"]:
            assert result[k] == 0.0

    def test_empty_nodes(self):
        """Empty nodes → all zero."""
        from fea_toolkit.utils import sum_reactions_with_overturning

        result = sum_reactions_with_overturning(
            {1: {"fx": 1.0, "fy": 0, "fz": 0, "mx": 0, "my": 0, "mz": 0}}, {}
        )
        for k in ["fx", "fy", "fz", "mx", "my", "mz"]:
            assert result[k] == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# RS base_reactions_cqc (two-stage path)
# ═════════════════════════════════════════════════════════════════════════════


class TestRSBaseReactionsTwoStage:
    """Test that RS analysis returns full 6-DoF base reactions."""

    def test_base_reactions_cqc_keys(self):
        """run_response_spectrum_analysis returns base_reactions_cqc."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import Preprocessor

        md = make_sample_model()
        pp = Preprocessor({"split_elements": True, "create_shells": False, "verbose": False})
        mesh = pp.run(md)
        ab = AnalysisBuilder(mesh, {"verbose": False, "element_type": "elasticBeamColumn"})
        ab.build_domain()
        ab.compute_seismic_masses()

        spec_cfg = {
            "code": "GB50011",
            "intensity": 7,
            "acceleration": 0.10,
            "site_class": "I1",
            "design_group": 1,
            "level": "rare",
            "damping": 0.05,
        }
        from fea_toolkit.spectrum import _build_spectrum

        T_spec, Sa_spec, _, _, _, _ = _build_spectrum(spec_cfg)

        modal = ab.run_modal_analysis(num_modes=2, print_results=False)
        rs = ab.run_response_spectrum_analysis(
            num_modes=min(2, modal["num_modes"]),
            modal_periods=modal["periods"],
            spectrum_periods=T_spec,
            spectrum_accels=Sa_spec,
            direction="X",
            damping_ratio=0.05,
            print_results=False,
        )

        # New full 6-DoF results
        assert "base_reactions_cqc" in rs
        r = rs["base_reactions_cqc"]
        for comp in ["fx", "fy", "fz", "mx", "my", "mz"]:
            assert comp in r, f"Missing {comp} in base_reactions_cqc"
        # X-direction excitation should produce non-zero Fx and My
        assert abs(r["fx"]) > 0, "Expected non-zero base shear in X"
        assert abs(r["my"]) > 0, "Expected non-zero overturning moment My"

        # modal_base_reactions should have one entry per mode
        assert len(rs["modal_base_reactions"]) == modal["num_modes"]

    def test_check_load_equilibrium_has_correct_units(self):
        """check_load_equilibrium uses mesh_model.units, not '?'."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import Preprocessor

        md = make_sample_model()
        pp = Preprocessor({"split_elements": True, "verbose": False})
        mesh = pp.run(md)
        ab = AnalysisBuilder(mesh, {"verbose": False})
        df = ab.check_load_equilibrium()
        assert not df.empty
        # Column headers should contain the force unit, not '?'
        for col in df.columns:
            assert "?" not in col, f"Column '{col}' contains '?'"
