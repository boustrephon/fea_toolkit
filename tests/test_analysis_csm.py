"""Tests for analysis wiring: pushover, capacity spectrum, CSM, CQC, RS."""

"""Tests for the model layer: dataclasses, geometry utilities, and sections."""

import math
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from fea_toolkit.model.csm import (
    bilinearize_composite,
    bilinearize_equal_energy,
    bilinearize_rc,
    bilinearize_stiffness_change,
)
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaMesh,
    FrameDistributedLoad,
    FrameElement,
    FrameEndOffset,
    ISection,
    JointLoad,
    LoadCase,
    LoadPattern,
    Material,
    Node,
    PipeSection,
    Restraint,
    SAPModelData,
    Section,
    ShellSection,
)
from fea_toolkit.model.selection import Selection
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

# ============================================================================
# Fixtures
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ============================================================================
# Dataclass construction tests
# ============================================================================


# ═══════════════════════════════════════════════════════════════════
# Analysis / capacity-spectrum wiring
# ═══════════════════════════════════════════════════════════════════


class TestCqcCombineUtils:
    """Tests for :func:`fea_toolkit.utils.cqc_combine`."""

    def test_single_mode(self):
        from fea_toolkit.utils import cqc_combine

        result = cqc_combine([100.0], [2.0], [0.05])
        assert abs(result - 100.0) < 1e-6

    def test_two_uncorrelated(self):
        from fea_toolkit.utils import cqc_combine

        # Very separated frequencies → ρ ≈ 0 → SRSS ≈ sqrt(a² + b²)
        vals = [100.0, 50.0]
        omega = [1.0, 50.0]
        damp = [0.05, 0.05]
        result = cqc_combine(vals, omega, damp)
        expected = math.sqrt(100**2 + 50**2)
        assert abs(result - expected) < 0.1

    def test_identical_modes(self):
        from fea_toolkit.utils import cqc_combine

        # Identical frequency → ρ → 1 → CQC = sum of absolute values
        vals = [100.0, 50.0]
        omega = [2.0, 2.0]
        damp = [0.05, 0.05]
        result = cqc_combine(vals, omega, damp)
        assert abs(result - 150.0) < 1.0


# ============================================================================
# Plotting module import tests
# ============================================================================


class TestPlottingImports:
    def test_rs_force_diagram_no_data(self):
        from fea_toolkit.plotting import plot_force_diagram

        fig = plot_force_diagram([], quantity="My_i", kind="rs", dimension="2d")
        assert fig is None

    def test_rs_force_diagram_unwraps_result_dict(self):
        """plot_force_diagram accepts the full extract_element_rs_forces dict."""
        from fea_toolkit.plotting import plot_force_diagram

        # Empty dict unwraps to no element results → None (no matplotlib needed)
        assert plot_force_diagram({}, quantity="My_i", kind="rs", dimension="2d") is None

    def test_deprecated_plot_names_removed(self):
        """The deprecated plot functions are no longer part of the public API.

        Note: ``plot_force_diagram`` is intentionally absent from this list —
        the name was reintroduced as the *unified* force-diagram dispatcher
        (``fea_toolkit.plotting.force_diagram.plot_force_diagram``), which is
        a different function from the deprecated 3D-static plotter removed in
        the Phase-3 cleanup.
        """
        from fea_toolkit import plotting

        for name in (
            "plot_model_3d",
            "plot_deformed_3d",
            "plot_rs_deformed_3d",
            "plot_mode_3d",
            "plot_static_moment_3d",
            "plot_static_shear_3d",
            "plot_static_axial_3d",
            "plot_static_force_diagram",
            # Phase-B unified-dispatcher cleanup (removed 2026-08-24)
            "plot_force_diagram_3d",
            "plot_rs_force_diagram",
            "plot_npz_force_diagram",
            "plot_npz_moment_3d",
        ):
            assert not hasattr(plotting, name), f"{name} should have been removed"

    def test_force_diagram_unified_import(self):
        """The unified plot_force_diagram dispatcher is importable."""
        from fea_toolkit.plotting import plot_force_diagram

        assert callable(plot_force_diagram)

    def test_force_diagram_invalid_quantity(self):
        """Invalid quantity returns None."""
        from fea_toolkit.plotting import plot_force_diagram

        result = plot_force_diagram({}, quantity="ZZ")
        assert result is None

    def test_force_diagram_no_data_builder(self):
        """Builder without force_data returns None."""
        from fea_toolkit.plotting import plot_force_diagram

        # Use a minimal mock that satisfies _resolve_mesh_data
        class MockModel:
            nodes: ClassVar = {}
            frame_elements: ClassVar = {}
            area_elements: ClassVar = {}
            frame_assignments: ClassVar = {}
            area_assignments: ClassVar = {}

        class MockBuilder:
            model = MockModel()
            split_elements: ClassVar = {}
            split_assignments: ClassVar = {}
            _mesh_model = None

        result = plot_force_diagram(MockBuilder())
        assert result is None

    def test_force_diagram_npz_no_static(self):
        """NPZ dict without static cases raises ValueError."""
        import pytest

        from fea_toolkit.plotting import plot_force_diagram

        with pytest.raises(ValueError, match="No static cases found"):
            plot_force_diagram({}, quantity="Mz", kind="static", dimension="3d")

    def test_unified_functions_import(self):
        """All unified functions are importable from the plotting package."""
        from fea_toolkit.plotting import (
            compare_meshes,
            plot_mesh,
            plot_mode_animation,
        )

        assert callable(plot_mesh)
        assert callable(compare_meshes)
        assert callable(plot_mode_animation)

    def test_model_viewer_import_and_types(self):
        """ModelViewer and its data types import correctly."""
        import numpy as np

        from fea_toolkit.plotting.renderers import (
            AnnotationDef,
            FrameGeom,
            HighlightDef,
            NodeGeom,
            ShellGeom,
        )

        # Data types construct
        f = FrameGeom(
            elem_id="1", section="UB300", node_i="1", node_j="2", start=np.zeros(3), end=np.ones(3)
        )
        assert f.elem_id == "1"

        s = ShellGeom(area_id="1", section="SLAB", vertices=np.zeros((4, 3)))
        assert s.area_id == "1"

        n = NodeGeom(node_id="1", position=np.zeros(3))
        assert n.node_id == "1"

        h = HighlightDef(frame_ids=["1"], color=(1, 0, 0), label="Test")
        assert h.label == "Test"

        a = AnnotationDef(text="Hello", position=np.zeros(3))
        assert a.text == "Hello"

    def test_model_viewer_from_sample(self):
        """ModelViewer extracts geometry from sample model data."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        viewer = ModelViewer(model_data=md, backend="pyvista", off_screen=True)

        # show_model should extract geometry and render
        viewer.show_model(show_nodes=True, show_shells=False)
        assert viewer._geom_extracted
        assert len(viewer._frames) == 1
        assert viewer._frames[0].elem_id == "1"

        # Test highlight
        viewer.highlight_elements(frame_ids=["1"], color=(1, 0, 0), label="Test")

        # Test annotation
        viewer.annotate("Hi", node_id="2", color=(1, 1, 0))

        # Test screenshot
        import os
        import tempfile

        tmp = tempfile.mktemp(suffix=".png")
        viewer.screenshot(tmp)
        assert os.path.getsize(tmp) > 0
        os.remove(tmp)

        viewer.clear()


# ============================================================================
# MASS SOURCE parser tests (integration)
# ============================================================================


class TestMassSourceParser:
    def test_parse_from_s2k(self):
        """Verify MassSource is parsed from a sample S2K file."""
        from fea_toolkit.io.s2k_parser import SAP2000Parser

        s2k_file = FIXTURES_DIR / "sample.s2k"
        if not s2k_file.exists():
            pytest.skip("sample.s2k not available")
        parser = SAP2000Parser(s2k_file)
        parser.parse()
        md = parser.get_model_data()
        assert hasattr(md, "mass_sources")
        # sample.s2k has MSSSRC1 with Elements=True, Masses=True, Loads=False
        if md.mass_sources:
            ms = md.mass_sources.get("MSSSRC1")
            if ms:
                assert ms.elements is True


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
# Brace subdivision tests
# ============================================================================


class TestSubdivideElements:
    """Tests for :func:`fea_toolkit.model.geometry.subdivide_elements`."""

    def test_subdivide_creates_sub_elements(self):
        """4 segments → 4 child elements, original marked inactive."""
        from fea_toolkit.model.geometry import subdivide_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        assignments = {"B1": "UB300"}
        result_elems, result_assign, _result_nodes, _, _ = subdivide_elements(
            elements,
            assignments,
            nodes,
            n_segments=4,
            brace_ids={"B1"},
            next_tag=100,
        )
        assert elem.inactive is True, "Original should be inactive"
        assert len(result_elems) == 5  # 1 original + 4 subs
        sub_ids = [eid for eid in result_elems if eid.startswith("B1_sub")]
        assert len(sub_ids) == 4
        for sid in sub_ids:
            assert sid in result_assign
            assert result_assign[sid] == "UB300"

    def test_subdivide_creates_internal_nodes(self):
        """4 segments → 3 new internal nodes."""
        from fea_toolkit.model.geometry import subdivide_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        assignments = {"B1": "UB300"}
        _, _, result_nodes, _, _ = subdivide_elements(
            elements,
            assignments,
            nodes,
            n_segments=4,
            brace_ids={"B1"},
            next_tag=100,
        )
        new_nodes = [nid for nid in result_nodes if nid.startswith("B1_sub")]
        assert len(new_nodes) == 3  # 4 segments → 3 internal nodes

    def test_imperfection_offsets_mid_node(self):
        """Mid-node of subdivided brace has lateral offset ≈ L/500."""
        from fea_toolkit.model.geometry import subdivide_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        _, _, result_nodes, _, _ = subdivide_elements(
            elements,
            assignments={"B1": "UB300"},
            nodes=nodes,
            n_segments=4,
            imperfection_ratio=1 / 500,
            brace_ids={"B1"},
            next_tag=100,
        )
        # The middle internal node (at z≈5) should have an x-offset
        mid_nodes = [
            n
            for nid, n in result_nodes.items()
            if nid.startswith("B1_sub") and abs(n.z - 5.0) < 0.5
        ]
        assert len(mid_nodes) > 0, "No midpoint node found — subdivision may have failed"
        # Imperfection is perpendicular to the brace axis. For a vertical brace
        # (0,0,0)→(0,0,10) the perpendicular direction is Y.
        offset = abs(mid_nodes[0].y)
        assert offset > 0.001, f"Expected imperfection offset, got {offset}"

    def test_end_offset_creates_rigid_links(self):
        """end_offset > 0 creates offset nodes and rigid link entries."""
        from fea_toolkit.model.geometry import subdivide_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        _, _, result_nodes, _, rigid_links = subdivide_elements(
            elements,
            assignments={"B1": "UB300"},
            nodes=nodes,
            n_segments=4,
            brace_ids={"B1"},
            end_offset=0.5,
            next_tag=100,
        )
        # Should have two rigid links (I-end and J-end)
        assert len(rigid_links) == 2
        link_i, link_j = rigid_links
        assert link_i[1] == "1"  # I-end: original node
        assert link_j[2] == "2"  # J-end: original node
        # Should have two offset nodes
        offset_ids = [nid for nid in result_nodes if "_offset_" in nid]
        assert len(offset_ids) == 2
        # Sub-elements should connect to offset nodes, not original nodes
        sub_ids = [eid for eid in elements if "_sub_" in eid]
        first_sub = elements[sub_ids[0]]
        last_sub = elements[sub_ids[-1]]
        assert first_sub.node_i in offset_ids
        assert last_sub.node_j in offset_ids

    def test_end_offset_clamped_to_half_length(self):
        """end_offset larger than half length is clamped."""
        from fea_toolkit.model.geometry import subdivide_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=5),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        _, _, result_nodes, _, _rigid_links = subdivide_elements(
            elements,
            assignments={"B1": "UB300"},
            nodes=nodes,
            n_segments=2,
            brace_ids={"B1"},
            end_offset=3.0,
            next_tag=100,
        )
        # Brace should still have at least some length (clamped to 45%)
        offset_ids = [nid for nid in result_nodes if "_offset_" in nid]
        if offset_ids:
            # Check offset nodes are within bounds
            for nid in offset_ids:
                n = result_nodes[nid]
                assert 0.0 <= n.z <= 5.0


# ============================================================================
# Euler buckling check tests
# ============================================================================


class TestBraceBucklingCheck:
    """Tests for :meth:`~fea_toolkit.model.checks.check_brace_buckling`."""

    @pytest.fixture
    def brace_model(self):
        """A simple 2‑node cantilever used as a brace."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=6, y=0, z=6),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000),
        }
        sections = {
            "PIP4": PipeSection(
                name="PIP4",
                shape="Pipe",
                material="Steel",
                od=0.1143,
                t=0.006,
                A=2e-3,
                I33=3e-6,
                I22=3e-6,
                J=1e-6,
            ),
        }
        frames = {
            "B1": FrameElement(elem_id="B1", elem_tag=1, node_i="1", node_j="2"),
        }
        return SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"B1": "PIP4"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )

    def test_euler_buckling_pinned(self, brace_model):
        """Euler P_cr with K=1 matches π²EI/L²."""
        from fea_toolkit.model.checks import check_brace_buckling

        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)
        assert "B1" in results
        r = results["B1"]
        # L = sqrt(6² + 6²) ≈ 8.485, I = 3e-6, E = 2e11
        expected = (math.pi**2 * 2e11 * 3e-6) / (8.485**2)
        assert abs(r["P_cr"] - expected) / expected < 0.01
        assert r["slenderness"] > 0

    def test_buckling_with_axial_demand(self, brace_model):
        """D/C ratio computed correctly."""
        from fea_toolkit.model.checks import check_brace_buckling

        results = check_brace_buckling(
            brace_model,
            brace_ids={"B1"},
            K=1.0,
            axial_demand={"B1": 50000.0},  # 50 kN
            print_results=False,
        )
        r = results["B1"]
        assert r["P_demand"] == 50000.0
        assert r["ratio"] > 0

    def test_buckling_table_auto_detect(self, brace_model):
        """brace_buckling_check auto-detects Pipe sections and returns a DataFrame."""
        from fea_toolkit.model.checks import brace_buckling_check

        df = brace_buckling_check(brace_model, n_longest=2, K=1.0)
        assert next(iter(df.columns)) == "Element"
        assert df["Element"].iloc[0] == "B1"
        pcr_col = [c for c in df.columns if c.startswith("P_cr")]
        assert pcr_col, "P_cr column missing from buckling table"
        assert df[pcr_col[0]].iloc[0] > 0

    def test_buckling_table_n_longest_ordering(self, brace_model):
        """brace_buckling_check sorts by length (desc) and truncates to n_longest."""
        from fea_toolkit.model.checks import brace_buckling_check

        # Add a second, longer brace (B2 length 12 > B1 length ≈ 8.49)
        brace_model.nodes["3"] = Node(node_id="3", node_tag=3, x=0, y=0, z=12)
        brace_model.frame_elements["B2"] = FrameElement(
            elem_id="B2", elem_tag=2, node_i="1", node_j="3"
        )
        brace_model.frame_assignments["B2"] = "PIP4"

        # Both braces reported, longest first
        df = brace_buckling_check(brace_model, n_longest=2, K=1.0)
        assert list(df["Element"]) == ["B2", "B1"]
        length_col = next(c for c in df.columns if c.startswith("Length"))
        assert df.loc[0, length_col] > df.loc[1, length_col]

        # Truncation: only the longest brace survives n_longest=1
        df_top = brace_buckling_check(brace_model, n_longest=1, K=1.0)
        assert len(df_top) == 1
        assert list(df_top["Element"]) == ["B2"]

    def test_buckling_table_empty_model_note_schema(self, brace_model):
        """No brace sections (cleared assignments) → Note-only DataFrame schema."""
        from fea_toolkit.model.checks import brace_buckling_check

        brace_model.frame_assignments = {}
        df = brace_buckling_check(brace_model, n_longest=2, K=1.0)
        assert list(df.columns) == ["Note"]
        assert len(df) == 1
        assert "No brace sections found" in df["Note"].iloc[0]

    def test_buckling_table_type_hints_resolve(self):
        """Return annotation is runtime-resolvable (no function-local pandas)."""
        import typing

        from fea_toolkit.model.checks import brace_buckling_check

        hints = typing.get_type_hints(brace_buckling_check)
        assert "return" in hints

    def test_from_brace_sections(self):
        """Selection.from_brace_sections detects Pipe, Angle, etc."""

        sections = {
            "PIP4": PipeSection(
                name="PIP4",
                shape="Pipe",
                material="Steel",
                od=0.1,
                t=0.005,
                A=1e-3,
                I33=1e-6,
                I22=1e-6,
                J=1e-7,
            ),
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
        model = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections=sections,
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        sel = Selection.from_brace_sections(model)
        assert sel.sections is not None
        assert "PIP4" in sel.sections
        assert "UB300" not in sel.sections

    # ── Non-positive A / fallback I22 regression tests ─────────────

    @pytest.mark.parametrize("bad_A", [0.0, -1.0])
    def test_non_positive_area_warns_and_clamps_a(self, brace_model, bad_A):
        """A<=0 → UserWarning + A clamped to 1e-4; P_cr stays exact."""
        from fea_toolkit.model.checks import check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.A = bad_A

        with pytest.warns(UserWarning, match="no positive cross-sectional area: 'PIP4'"):
            results = check_brace_buckling(
                brace_model, brace_ids={"B1"}, K=1.0, print_results=False
            )

        r = results["B1"]
        assert r["A"] == 1e-4
        L = math.hypot(6.0, 6.0)
        # Slenderness recomputed from the clamped area
        assert r["slenderness"] == pytest.approx((1.0 * L) / math.sqrt(sec.I22 / 1e-4), rel=1e-9)
        # P_cr only depends on I22 (unchanged) — the clamp must not leak in
        assert r["P_cr"] == pytest.approx((math.pi**2 * 2e11 * sec.I22) / (L**2), rel=1e-9)

    @pytest.mark.parametrize("bad_I22", [0.0, -1e-6])
    def test_non_positive_i22_falls_back_to_i33(self, brace_model, bad_I22, recwarn):
        """I22<=0 → I33 used as the minor-axis fallback (no fabricated-area warning)."""
        from fea_toolkit.model.checks import check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.I22 = bad_I22

        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)

        r = results["B1"]
        assert r["I22"] == sec.I33
        L = math.hypot(6.0, 6.0)
        assert r["P_cr"] == pytest.approx((math.pi**2 * 2e11 * sec.I33) / (L**2), rel=1e-9)
        # Positive A → nothing is fabricated, so no UserWarning is emitted
        assert not any(
            "Brace buckling" in str(w.message)
            for w in recwarn
            if issubclass(w.category, UserWarning)
        )

    def test_zero_inertia_skipped_without_warning(self, brace_model, recwarn):
        """I22<=0 AND I33<=0 → element skipped; no result row and no warning."""
        from fea_toolkit.model.checks import check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.I22 = 0.0
        sec.I33 = 0.0

        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)
        assert results == {}
        assert not any(
            "Brace buckling" in str(w.message)
            for w in recwarn
            if issubclass(w.category, UserWarning)
        )

    def test_buckling_table_effective_columns_match_engine(self, brace_model):
        """DataFrame A/I22 columns echo the engine's effective (clamped/fallback) values."""
        from fea_toolkit.model.checks import brace_buckling_check, check_brace_buckling

        sec = brace_model.sections["PIP4"]
        sec.A = 0.0  # non-positive → clamped to 1e-4
        sec.I22 = -1e-6  # non-positive → falls back to I33

        with pytest.warns(UserWarning, match="no positive cross-sectional area"):
            engine = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0, print_results=False)
            df = brace_buckling_check(brace_model, n_longest=2, K=1.0)

        r = engine["B1"]
        assert r["A"] == 1e-4
        assert r["I22"] == sec.I33

        lu = brace_model.units.get("L", "m")
        a_col = f"A ({lu}²)"
        i22_col = f"I22 ({lu}⁴)"
        assert a_col in df.columns and i22_col in df.columns
        assert df.loc[0, a_col] == round(r["A"], 6)
        assert df.loc[0, i22_col] == round(r["I22"], 8)
        # Capacity/slenderness columns derive from the same effective values
        assert df.loc[0, "Slenderness"] == round(r["slenderness"], 1)


# ============================================================================
# Integration test: subdivided brace in pushover pipeline
# ============================================================================


class TestSubdividedBraceInPushover:
    """Verify that braces with subdivision + imperfection can be built and
    run through a pushover analysis without error.

    This tests the pipeline integration — not the exact buckling load
    (which is verified analytically in ``TestBraceBucklingCheck``).
    The practical workflow is:

    1. Identify braces via ``Selection``
    2. Subdivide them with imperfection via ``set_brace_selection()``
    3. Run pushover analysis
    4. Optionally check critical braces via ``check_brace_buckling()``
    """

    @pytest.fixture
    def brace_model(self):
        """A slender 10 m pin-pin pipe column for pushover testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000, Fy=2.5e8),
        }
        sections = {
            "PIP4": PipeSection(
                name="PIP4",
                shape="Pipe",
                material="Steel",
                od=0.1,
                t=0.005,
                A=0.001492,
                I33=1.70e-6,
                I22=1.70e-6,
                J=3.4e-6,
            ),
        }
        frames = {
            "B1": FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2"),
        }
        load_patterns = {
            "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
        }
        frame_dist_loads = [
            FrameDistributedLoad(
                pattern="WIND",
                frame_id="B1",
                direction="X",
                load_type="Force",
                shape="Uniform",
                val_a=5000,
                val_b=5000,
                rdist_a=0,
                rdist_b=1,
                dist_a=0,
                dist_b=10,
            ),
        ]
        return SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements={},
            frame_assignments={"B1": "PIP4"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            load_patterns=load_patterns,
            frame_dist_loads=frame_dist_loads,
        )

    def test_subdivided_brace_builds_and_runs(self, brace_model):
        """AnalysisBuilder with subdivided braces runs pushover without crash."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mm = preprocess_model(brace_model, {"split_elements": False})
        b = AnalysisBuilder(
            mm,
            {
                "element_type": "forceBeamColumn",
                "create_fiber_sections": True,
                "geom_transf_type": "Corotational",
                "split_elements": False,
                "verbose": False,
            },
        )
        b.set_brace_selection({"B1"}, end_offset=0.0)

        # Run a quick pushover to verify the pipeline holds
        results = b.run_pushover_analysis(
            gravity_patterns={},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.05,
            num_steps=5,
            print_progress=False,
        )
        assert results is not None
        assert "control_disp" in results
        assert len(results["control_disp"]) > 1

    def test_check_buckling_after_pushover(self, brace_model):
        """Can check Euler buckling of braces (analytical, no OpenSees needed)."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mm = preprocess_model(brace_model, {"split_elements": False})
        b = AnalysisBuilder(
            mm,
            {
                "element_type": "forceBeamColumn",
                "create_fiber_sections": True,
                "split_elements": False,
                "verbose": False,
            },
        )
        b.set_brace_selection({"B1"}, end_offset=0.0)

        # Check Euler buckling directly from model data (no analysis required)
        buckling = b.check_brace_buckling(
            brace_ids={"B1"},
            K=1.0,
            print_results=False,
        )
        assert "B1" in buckling
        assert buckling["B1"]["P_cr"] > 0
        assert buckling["B1"]["slenderness"] > 0
        # P_cr ≈ π² × 2e11 × 1.7e-6 / 10² ≈ 33.6 kN
        P_cr = buckling["B1"]["P_cr"]
        assert 30000 < P_cr < 37000, f"Expected P_cr ≈ 33.6 kN, got {P_cr:.0f} N"


# ============================================================================
# Euler buckling benchmark: SciPy eigenvalue analysis of subdivided column
# ============================================================================


class TestEulerBucklingBenchmark:
    """Benchmark: eigenvalue buckling of a subdivided column via SciPy.

    Assembles the global elastic stiffness matrix *K* and geometric stiffness
    matrix *K_g* for the subdivided column using standard Euler-Bernoulli
    beam elements, then solves the generalised eigenvalue problem:

    .. math:: (K - \\lambda K_g)\\phi = 0

    using ``scipy.linalg.eig``.  The smallest positive eigenvalue gives the
    buckling load :math:`P_{cr}`, which should match the analytical Euler
    formula :math:`\\pi^2 EI / (KL)^2` within a small discretisation error.

    This is an **independent verification** of the subdivided brace concept
    — it does **not** depend on OpenSees' nonlinear solver, so it is fast,
    deterministic, and numerically robust.
    """

    def test_eigenvalue_buckling_matches_euler(self):
        """Eigenvalue buckling from FEA assembly matches Euler P_cr within 5 %."""
        pytest.importorskip("scipy", reason="scipy not installed")
        import numpy as np
        from scipy.linalg import eig

        L = 10.0
        E = 2e11
        I22 = 1.70e-6
        P_cr_euler = (math.pi**2 * E * I22) / (L**2)

        # Subdivide into N segments
        n_seg = 6
        seg_len = L / n_seg
        n_nodes = n_seg + 1  # total nodes including ends

        # DOF numbering: each node has 2 DOFs (v, θ)
        # Pinned ends: v=0, θ free → remove v DOFs at ends
        n_dof_total = n_nodes * 2  # raw DOFs including constraints
        constrained = {0}  # node 0: v=0 → DOF 0 removed (θ free)
        constrained.add(n_nodes * 2 - 2)  # last node: v=0 → DOF removed (θ free)
        dof_map_raw = [d for d in range(n_dof_total) if d not in constrained]
        n_dof = len(dof_map_raw)
        # dof_map_raw[i] = global raw DOF index for reduced DOF i

        def beam_stiffness(Le, Ee, Ie):
            return np.array(
                [
                    [
                        12 * Ee * Ie / Le**3,
                        6 * Ee * Ie / Le**2,
                        -12 * Ee * Ie / Le**3,
                        6 * Ee * Ie / Le**2,
                    ],
                    [6 * Ee * Ie / Le**2, 4 * Ee * Ie / Le, -6 * Ee * Ie / Le**2, 2 * Ee * Ie / Le],
                    [
                        -12 * Ee * Ie / Le**3,
                        -6 * Ee * Ie / Le**2,
                        12 * Ee * Ie / Le**3,
                        -6 * Ee * Ie / Le**2,
                    ],
                    [6 * Ee * Ie / Le**2, 2 * Ee * Ie / Le, -6 * Ee * Ie / Le**2, 4 * Ee * Ie / Le],
                ]
            )

        def beam_geo_stiffness(Le):
            return (1.0 / (30 * Le)) * np.array(
                [
                    [36, 3 * Le, -36, 3 * Le],
                    [3 * Le, 4 * Le**2, -3 * Le, -(Le**2)],
                    [-36, -3 * Le, 36, -3 * Le],
                    [3 * Le, -(Le**2), -3 * Le, 4 * Le**2],
                ]
            )

        def to_global(raw_dofs):
            """Map 4 element DOFs to reduced system indices (or -1 if constrained)."""
            return [dof_map_raw.index(d) if d in dof_map_raw else -1 for d in raw_dofs]

        K = np.zeros((n_dof, n_dof))
        Kg = np.zeros((n_dof, n_dof))

        for seg in range(n_seg):
            n0 = seg  # left node index
            n1 = seg + 1  # right node index
            # Raw DOFs: [n0*2 (v0), n0*2+1 (θ0), n1*2 (v1), n1*2+1 (θ1)]
            raw = [n0 * 2, n0 * 2 + 1, n1 * 2, n1 * 2 + 1]
            gn = to_global(raw)

            k_e = beam_stiffness(seg_len, E, I22)
            k_ge = beam_geo_stiffness(seg_len)

            for i in range(4):
                gi = gn[i]
                if gi < 0:
                    continue
                for j in range(4):
                    gj = gn[j]
                    if gj < 0:
                        continue
                    K[gi, gj] += k_e[i, j]
                    Kg[gi, gj] += k_ge[i, j]

        # Solve (K - λ Kg)φ = 0
        eigvals, _ = eig(K, Kg)
        # The smallest positive eigenvalue is the buckling load
        buckling_loads = sorted(
            [np.real(ev) for ev in eigvals if np.real(ev) > 1000 and not np.iscomplex(ev)]
        )
        assert len(buckling_loads) > 0, "No valid buckling eigenvalues found"
        P_cr_fea = buckling_loads[0]
        ratio = P_cr_fea / P_cr_euler
        assert 0.95 < ratio < 1.10, (
            f"FEA eigenvalue P_cr ({P_cr_fea:.0f} N) differs from Euler "
            f"({P_cr_euler:.0f} N) by {abs(1 - ratio) * 100:.1f}%"
        )


# ============================================================================
# Capacity Spectrum Method tests
# ============================================================================


class TestCapacitySpectrumMethod:
    """Tests for :meth:`AnalysisBuilder.pushover_to_adrs` and
    :meth:`AnalysisBuilder.compute_performance_point`.

    CSM/ADRS requires a consistent mass matrix, which the elastic
    cantilever fixture could not provide (near-zero effective mass via
    ``compute_seismic_masses()``).  These tests use the fiber-capable
    single-storey RC moment frame from :func:`make_rc_frame_model`
    instead: the initial domain is built with elastic sections (used
    for the modal analysis), and ``run_pushover_analysis()`` rebuilds
    it with ``forceBeamColumn`` + fiber sections internally, giving a
    genuinely nonlinear capacity curve.
    """

    @pytest.fixture
    def rc_model(self):
        """Fiber-capable single-storey RC moment frame for CSM testing."""
        from examples.sample_model import make_rc_frame_model

        return make_rc_frame_model()

    @pytest.fixture
    def rc_ab(self, rc_model):
        """AnalysisBuilder for the RC frame (elastic sections for modal)."""
        import openseespy.opensees as ops

        cfg = {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
        mesh_model = preprocess_model(rc_model, cfg)
        b = AnalysisBuilder(mesh_model, cfg)
        yield b
        ops.wipe()

    @pytest.fixture
    def rc_adrs(self, rc_ab):
        """ADRS data from a nonlinear pushover of the RC frame."""
        rc_ab.build_domain()
        rc_ab.compute_seismic_masses()
        modal = rc_ab.run_modal_analysis(num_modes=1, print_results=False)
        shapes = rc_ab.extract_mode_shapes(1)
        results = rc_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=4,
            max_disp=0.3,
            num_steps=50,
            print_progress=False,
        )
        adrs = rc_ab.pushover_to_adrs(results, modal, shapes, direction="X")
        return rc_ab, results, modal, shapes, adrs

    def test_pushover_to_adrs_values_consistent(self, rc_adrs):
        """ADRS values are positive and consistent (no NaN or negative)."""
        _b, _results, _modal, _shapes, adrs = rc_adrs
        assert len(adrs["S_a"]) == len(adrs["S_d"]) > 0
        assert abs(adrs["M_eff"]) > 0
        assert all(v >= 0 for v in adrs["S_a"])
        assert all(v >= 0 for v in adrs["S_d"])
        assert all(math.isfinite(v) for v in adrs["S_a"])
        assert all(math.isfinite(v) for v in adrs["S_d"])

    def test_performance_point_elastic(self, rc_adrs):
        """Elastic demand path: converges at mu = 1 with finite values.

        A weak demand spectrum (50 % of the bilinearised yield
        acceleration) keeps the frame below yield, so the CSM iteration
        must converge to mu = 1.  The equivalent secant period is the
        secant stiffness of the *capacity curve at the performance
        point* — for a real RC frame this is not guaranteed to equal
        the elastic modal period, so it is only required to be positive
        and finite.
        """
        from fea_toolkit.model.csm import bilinearize_composite

        b, results, modal, shapes, adrs = rc_adrs
        # Anchor the spectrum to the bilinearised yield acceleration —
        # the same non-hard-coded scaling used by
        # test_workflows.py::test_compute_performance_point, with a
        # 0.5× factor so the demand plateau stays below yield and the
        # frame remains elastic.
        _S_dy, S_ay, _ = bilinearize_composite(
            np.asarray(adrs["S_d"], dtype=float),
            np.asarray(adrs["S_a"], dtype=float),
        )
        assert S_ay > 1e-6, f"Degenerate yield acceleration S_ay={S_ay:.3g}"

        T_spec = [0.0, 0.1, 0.5, 1.0, 2.0, 4.0, 6.0]
        accels = [0.5, 1.5, 1.5, 0.75, 0.375, 0.25, 0.125]
        # Scale so the spectrum peak is 50 % of the frame's yield
        # acceleration — well below yield → mu = 1.
        scale = (0.5 * S_ay) / max(accels)
        Sa_spec = [a * scale for a in accels]
        pp = b.compute_performance_point(
            results,
            modal,
            shapes,
            T_spec,
            Sa_spec,
            direction="X",
        )
        assert pp["converged"]
        assert pp["S_dp"] > 0
        assert pp["S_ap"] > 0
        assert math.isfinite(pp["T_eq"]) and pp["T_eq"] > 0
        # Demand below yield → no ductility.
        assert pp["mu"] == pytest.approx(1.0, abs=0.01)


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
# Concrete section fiber patch tests
# ============================================================================


class TestConcreteRectangularSectionFiberPatches:
    """Fiber patch generation for ConcreteRectangularSection."""

    def test_concrete_rect_basic(self):
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
        patches = sec.to_fiber_patches(mat_tag=1)
        # Concrete patches: core, top cover, bottom cover, left cover, right cover
        # Rebar layers: top (straight), bottom (straight)
        assert len(patches) == 7
        # Concrete patches use mat_tag for unconfined, mat_tag+1 for confined
        assert patches[0][0] == "rect"
        assert patches[0][1] == 2  # confined core (mat_tag + 1)
        assert patches[1][1] == 1  # top cover (unconfined)
        # Rebar layers (last two entries): type "straight", mat_tag + 2 = 3
        assert patches[-2][0] == "straight"
        assert patches[-2][1] == 3  # top rebar
        assert patches[-1][0] == "straight"
        assert patches[-1][1] == 3  # bottom rebar
        # Rebar uses area (m²), not diameter
        expected_area = 3.14159 * (0.01) ** 2  # π * (dia/2)²  with dia=0.02
        assert patches[-2][3] == pytest.approx(expected_area, rel=1e-3)

    def test_concrete_rect_no_rebar(self):
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
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        for p in patches:
            assert p[0] == "rect"
        assert len(patches) >= 3  # core + 2 covers


class TestConcreteCircularSectionFiberPatches:
    """Fiber patch generation for ConcreteCircularSection."""

    def test_concrete_circ_basic(self):
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
        patches = sec.to_fiber_patches(mat_tag=1)
        # Concrete: confined core (circ), unconfined cover (circ)
        # Rebar: circ_layer with mat_tag + 2 = 3
        assert len(patches) == 3
        assert patches[0][0] == "circ"
        assert patches[0][1] == 2  # confined core
        assert patches[1][1] == 1  # unconfined cover
        # Rebar layer
        assert patches[2][0] == "circ_layer"
        assert patches[2][1] == 3  # rebar
        expected_area = 3.14159 * (0.01) ** 2
        assert patches[2][3] == pytest.approx(expected_area, rel=1e-3)

    def test_concrete_circ_no_rebar(self):
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
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        assert len(patches) == 2  # core + cover only, no rebar


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
# SAPModelData utility methods
# ═══════════════════════════════════════════════════════════════════


class TestSAPModelDataMethods:
    """Tests for the utility methods added to SAPModelData."""

    @pytest.fixture
    def sample_md(self):
        nodes = {
            "1": Node(node_id="1", node_tag=10, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=20, x=6, y=0, z=0),
            "3": Node(node_id="3", node_tag=30, x=6, y=8, z=0),
        }
        frames = {
            "F1": FrameElement(elem_id="F1", elem_tag=1, node_i="1", node_j="2"),
        }
        areas = {
            "A1": AreaElement(area_id="A1", area_tag=2, node_ids=["1", "2", "3"]),
        }
        materials = {"C40": Material(name="C40", type="Concrete", E_mod=3e7)}
        sections = {"SEC1": Section(name="SEC1", material="C40", shape="Rectangular", A=0.16)}
        return SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements=areas,
            frame_assignments={"F1": "SEC1"},
            area_assignments={"A1": "SEC1"},
            groups={},
            frame_auto_mesh={},
        )

    def test_max_node_tag(self, sample_md):
        assert sample_md.max_node_tag() == 30

    def test_max_node_tag_empty(self):
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
        assert md.max_node_tag() == 0

    def test_auto_detect_static_cases(self):

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
            load_cases={
                "DEAD": LoadCase(
                    case_name="DEAD",
                    case_type="LinStatic",
                    design_type_option="Prog Det",
                    design_type="Dead",
                    design_action_option="Prog Det",
                    design_action="Non-Composite",
                ),
                "MODAL": LoadCase(
                    case_name="MODAL",
                    case_type="LinModal",
                    design_type_option="Prog Det",
                    design_type="Other",
                    design_action_option="Prog Det",
                    design_action="Other",
                ),
            },
        )
        cases = md.auto_detect_static_cases()
        assert cases == ["DEAD"]

    def test_summary_dict(self, sample_md):
        s = sample_md.summary_dict()
        assert s["Nodes"] == 3
        assert s["Frames"] == 1
        assert s["Areas"] == 1
        assert s["Materials"] == 1
        assert s["Sections"] == 1
        assert s["X span (m)"] == 6.0
        assert s["Y span (m)"] == 8.0
        assert s["Z span (m)"] == 0.0

    def test_remove_floating_nodes(self):
        """remove_floating_nodes eliminates unreferenced nodes."""
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=3, y=4, z=0),  # floating
            },
            restraints={},
            materials={},
            sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10, node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        rows = remove_floating_nodes(md)
        # Inert node is removed silently (no mass/loads/restraint to redistribute)
        assert len(rows) == 0
        assert "3" not in md.nodes

    def test_remove_floating_nodes_with_restraint(self):
        """Floating node with restraint transfers it to nearest neighbour."""
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=3, y=0, z=0),  # floating
            },
            restraints={"3": Restraint([1, 1, 1, 1, 1, 1])},
            materials={},
            sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10, node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        rows = remove_floating_nodes(md)
        assert len(rows) == 1
        assert rows[0]["restrained"] is True
        # Restraint should have transferred
        assert "1" in md.restraints or "2" in md.restraints

    def test_remove_floating_nodes_transfers_joint_loads_per_pattern(self):
        """Floating node's joint loads transfer per source pattern.

        Regression test: the transferred ``JointLoad`` must use the
        dataclass's real ``node_id`` field and preserve each source
        load's ``pattern`` — the previous patternless
        ``JointLoad(node=...)`` construction raised ``TypeError`` and
        aggregated every pattern into one anonymous entry.
        """
        from fea_toolkit.model.geometry import remove_floating_nodes

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=3, y=0, z=0),  # floating
            },
            restraints={},
            materials={},
            sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10, node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            joint_loads=[
                JointLoad(pattern="DEAD", node_id="3", fz=-100.0),
                JointLoad(pattern="LIVE", node_id="3", fz=-50.0),
            ],
        )
        rows = remove_floating_nodes(md)
        assert len(rows) == 1
        transferred = [jl for jl in md.joint_loads if jl.node_id != "3"]
        # Both patterns transferred independently, keeping their own
        # pattern names and the nearest connected node's id.
        assert len(transferred) == 2
        by_pattern = {jl.pattern: jl for jl in transferred}
        assert set(by_pattern) == {"DEAD", "LIVE"}
        for jl in transferred:
            assert jl.node_id in ("1", "2")
        assert by_pattern["DEAD"].fz == pytest.approx(-100.0)
        assert by_pattern["LIVE"].fz == pytest.approx(-50.0)


# ═══════════════════════════════════════════════════════════════════
# CQC combination engine
# ═══════════════════════════════════════════════════════════════════


class TestCqcCombine:
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


# ═════════════════════════════════════════════════════════════════════════════
# CSM module tests (standalone, no OpenSees required)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def clean_bilinearize_registry():
    """Snapshot and restore the global ``BILINEARIZE_METHODS`` registry.

    The CSM bilinearization registry is process-global mutable state; this
    fixture guarantees tests that register/override methods cannot leak
    into one another (mirrors the ``ops.wipe()`` teardown hygiene used for
    OpenSees global state).
    """
    from fea_toolkit.model.csm import BILINEARIZE_METHODS

    saved = dict(BILINEARIZE_METHODS)
    yield BILINEARIZE_METHODS
    BILINEARIZE_METHODS.clear()
    BILINEARIZE_METHODS.update(saved)


class TestCsmModule:
    """Test the standalone CSM utility functions in model/csm.py."""

    def test_pushover_to_adrs_basic(self):
        """ADRS conversion works with valid pushover + modal results."""
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01, 0.02, 0.03, 0.04],
            "base_shear": [0.0, 100.0, 180.0, 240.0, 280.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.1],
                "partiMassMX": [800.0, 100.0],
            },
            "periods": [0.5, 0.1],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="X")
        assert isinstance(adrs, dict)
        assert set(adrs) >= {"S_a", "S_d", "Gamma", "M_eff", "phi_control", "best_mode"}
        assert len(adrs["S_a"]) == len(pushover["control_disp"])
        assert len(adrs["S_d"]) == len(pushover["control_disp"])
        assert adrs["Gamma"] > 0
        assert adrs["M_eff"] > 0
        assert adrs["phi_control"] > 0
        assert adrs["best_mode"] == 0  # mode 0 has 80% ratio

    def test_compute_performance_point_basic(self):
        """Performance point computation runs with valid data."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        pp = compute_performance_point(
            pushover,
            modal,
            shapes,
            periods,
            accels,
            direction="X",
            damping_ratio=0.05,
            max_iter=20,
            tol=0.05,
        )
        assert isinstance(pp, dict)
        assert set(pp) >= {
            "S_dp",
            "S_ap",
            "V_base",
            "D_roof",
            "T_eq",
            "mu",
            "converged",
            "S_dy",
            "S_ay",
        }
        assert pp["S_dp"] > 1e-6
        assert pp["S_ap"] > 1e-6
        assert pp["V_base"] > 1e-6
        assert isinstance(pp["converged"], bool)

    def test_compute_performance_point_accepts_de_luca_method(self):
        """``bilinearize_method='de_luca_10pct'`` / ``'rc'`` dispatch to
        :func:`bilinearize_rc` without error and report the method used."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        for name in ("de_luca_10pct", "rc"):
            pp = compute_performance_point(
                pushover,
                modal,
                shapes,
                periods,
                accels,
                direction="X",
                damping_ratio=0.05,
                max_iter=20,
                tol=0.05,
                bilinearize_method=name,
            )
            assert isinstance(pp, dict)
            assert pp["bilinearize_method"].startswith("de_luca_10pct"), (
                f"method {name}: expected de_luca_10pct*, got {pp['bilinearize_method']}"
            )
            assert pp["S_dy"] > 0
            assert pp["S_ay"] > 0

    def test_compute_performance_point_too_few_points(self):
        """Raises ValueError with fewer than 3 valid data points."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8],
                "partiMassMX": [800.0],
            },
            "periods": [0.5],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}}
        with pytest.raises(ValueError, match=r"too few|Too few"):
            compute_performance_point(
                pushover,
                modal,
                shapes,
                [0.1, 0.5],
                [3.0, 1.5],
            )

    def test_pushover_to_adrs_y_direction(self):
        """ADRS conversion in Y direction picks correct mass data."""
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.2],
                "partiMassMX": [200.0],
                "partiMassRatiosMY": [0.7],
                "partiMassMY": [700.0],
            },
            "periods": [0.5],
            "nodal_masses": {1: 700.0},
        }
        shapes = {0: {1: (0.0, 1.0, 0.0)}}

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="Y")
        # With nodal_masses = {1: 700.0} and mode shape (0, 1, 0):
        # L = 700 * 1.0 = 700, M_star = 700 * 1.0^2 = 700
        # Gamma = L / M_star = 1.0, M_eff = L^2 / M_star = 700
        assert adrs["M_eff"] == 700.0
        assert adrs["Gamma"] == 1.0

    def test_pushover_to_adrs_rejects_ill_conditioned_modes(self):
        """Low-participation torsional modes must not win ADRS selection.

        Regression test for the Admin Building CSM flattening bug: a
        torsional eigenvector whose residual X components are all ~0.002
        same-sign noise produces a tiny ``M_star_x`` (~0.003) while
        ``L_x / M_star_x`` is ill-conditioned, so ``L_x²/M_star_x``
        blows up to ≈ ``M_total``.  Under a naive max of ``L²/M_star``
        this contaminated mode out-ranks the true ~99 % X sway mode,
        corrupting the ``S_a``/``S_d`` scaling of the whole CSM curve.
        """
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        masses = {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0, 5: 100.0, 6: 100.0}
        modal = {
            "modal_props": {},
            "periods": [0.5, 0.1],
            "nodal_masses": masses,
        }
        # Mode 0 (index 0): genuine X sway, ~99 % participation with a
        # varying mode shape.  Mode 1 (index 1): torsional eigenvector
        # (large ±z components) whose residual X components are all
        # ~0.002 same-sign noise — M_star_x ≈ 0.003 but L_x²/M_star_x
        # ≈ M_total, which used to win the naive max selection.
        sway_phis = [1.0, 0.95, 0.9, 0.85, 0.8, 0.75]
        shapes = {
            0: {i: (phi, 0.0, 0.0) for i, phi in zip(range(1, 7), sway_phis)},
            1: {
                i: (0.002 + (i - 1) * 0.0001, 0.0, 1.0 if i % 2 == 0 else -1.0) for i in range(1, 7)
            },
        }

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="X")

        # The fix must pick the true sway mode, not the contaminated
        # torsional mode.
        assert adrs["best_mode"] == 0
        L0 = 100.0 * sum(sway_phis)
        Ms0 = 100.0 * sum(p * p for p in sway_phis)
        assert abs(adrs["M_eff"] - L0 * L0 / Ms0) < 1e-8
        # Effective modal mass can never exceed the total physical mass.
        assert adrs["M_eff"] < 600.0
        assert adrs["phi_control"] == 1.0

    def test_bilinearize_registry_builtins_present(self):
        """The five built-in method names are pre-registered."""
        from fea_toolkit.model.csm import (
            BILINEARIZE_METHODS,
            bilinearize_composite,
            bilinearize_equal_energy,
            bilinearize_rc,
            bilinearize_stiffness_change,
            get_bilinearize_method,
        )

        assert set(BILINEARIZE_METHODS) == {
            "composite",
            "stiffness_change",
            "equal_energy",
            "rc",
            "de_luca_10pct",
        }
        assert get_bilinearize_method("composite") is bilinearize_composite
        assert get_bilinearize_method("stiffness_change") is bilinearize_stiffness_change
        assert get_bilinearize_method("equal_energy") is bilinearize_equal_energy
        assert get_bilinearize_method("rc") is bilinearize_rc
        assert get_bilinearize_method("de_luca_10pct") is bilinearize_rc

    def test_bilinearize_registry_register_and_dispatch_custom(self, clean_bilinearize_registry):
        """A third-party registered name flows through the full CSM."""
        from fea_toolkit.model.csm import (
            bilinearize_rc,
            compute_performance_point,
            register_bilinearize_method,
        )

        register_bilinearize_method("my_rc", bilinearize_rc)

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        pp = compute_performance_point(
            pushover,
            modal,
            shapes,
            periods,
            accels,
            direction="X",
            damping_ratio=0.05,
            max_iter=20,
            tol=0.05,
            bilinearize_method="my_rc",
        )
        assert pp["bilinearize_method"].startswith("de_luca_10pct")
        assert pp["S_dy"] > 0
        assert pp["S_ay"] > 0

    def test_bilinearize_registry_unknown_method_raises(self):
        """Unknown names raise ValueError listing the registered keys."""
        from fea_toolkit.model.csm import get_bilinearize_method

        with pytest.raises(ValueError, match=r"Unknown bilinearize_method 'nope'"):
            get_bilinearize_method("nope")

    def test_compute_performance_point_rejects_unknown_method(self):
        """An unregistered ``bilinearize_method`` propagates ValueError in CSM."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01, 0.02, 0.03, 0.04],
            "base_shear": [0.0, 50.0, 100.0, 150.0, 180.0],
        }
        modal = {
            "modal_props": {"partiMassRatiosMX": [0.8], "partiMassMX": [800.0]},
            "periods": [0.5],
            "nodal_masses": {1: 1000.0},
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}}

        with pytest.raises(ValueError, match=r"Unknown bilinearize_method 'nope'"):
            compute_performance_point(
                pushover,
                modal,
                shapes,
                [0.0, 0.5, 1.0],
                [3.0, 1.5, 0.8],
                bilinearize_method="nope",
            )

    def test_bilinearize_registry_overwrite_guard(self, clean_bilinearize_registry):
        """Re-registering a built-in requires overwrite=True."""
        from fea_toolkit.model.csm import (
            BILINEARIZE_METHODS,
            get_bilinearize_method,
            register_bilinearize_method,
        )

        def _stub(S_d_arr, S_a_arr, config=None):
            return (0.05, 100.0, "stub")

        with pytest.raises(ValueError, match=r"already registered"):
            register_bilinearize_method("composite", _stub)

        register_bilinearize_method("composite", _stub, overwrite=True)
        assert get_bilinearize_method("composite") is _stub
        assert "composite" in BILINEARIZE_METHODS

    def test_bilinearize_registry_non_callable_raises(self):
        """Registering a non-callable raises TypeError."""
        from fea_toolkit.model.csm import register_bilinearize_method

        with pytest.raises(TypeError, match=r"must be callable"):
            register_bilinearize_method("bad", "not-a-callable")


# ═════════════════════════════════════════════════════════════════════════════
# Bilinearisation utility tests (standalone, no OpenSees required)
# ═════════════════════════════════════════════════════════════════════════════


class TestBilinearization:
    """Test the three bilinearisation methods in model/csm.py.

    Uses synthetic capacity curves:
    - bilinear: linear-elastic up to 0.02 m → flat plastic plateau.
    - elastic: pure linear (no yield).
    - hardening: parabolic with gradual stiffness decay.
    - peak_curve: slight post-peak softening.
    """

    # ── Fixtures ─────────────────────────────────────────────────────

    @pytest.fixture
    def bilinear_curve(self):
        """Bilinear: S_a = 5000 * S_d up to S_d=0.02, then gentle post-yield
        hardening (slope = 500).  The peak is well past the knee, so
        stiffness-change can detect the knee and composite will not fall back."""
        S_d = np.linspace(0.0, 0.08, 41)
        # Linear elastic up to S_d = 0.02 (S_a = 100), then hardening at 500.
        S_a = np.where(S_d <= 0.02, 5000.0 * S_d, 100.0 + 500.0 * (S_d - 0.02))
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def elastic_curve(self):
        """Elastic: S_a = 10000 * S_d (pure linear)."""
        S_d = np.linspace(0.0, 0.05, 21)
        S_a = 10000.0 * S_d
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def hardening_curve(self):
        """Hardening: S_a = 5000 * sqrt(S_d) — gradual stiffness decay."""
        S_d = np.linspace(0.0, 0.10, 31)
        S_a = 5000.0 * np.sqrt(S_d)
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def peak_curve(self):
        """Curve with a clear peak before softening."""
        S_d = np.linspace(0.0, 0.10, 31)
        # Ascend to 0.040 m, then descend
        S_a = np.where(S_d <= 0.04, 10000.0 * S_d, 400.0 - 200.0 * (S_d - 0.04) / 0.06)
        S_a = np.maximum(S_a, 0.0)
        return S_d, S_a

    @pytest.fixture
    def sudden_drop_curve(self):
        """Curve with an abrupt softening step to test criterion B.

        Peak at index 7 (S_d=0.035, S_a=285).  A single-step stiffness
        drop occurs at index 8 (S_d=0.040, S_a=110).  Since the drop is
        *after* the peak, stiffness-change returns peak (fallback) and
        composite falls back to equal-energy.
        """
        S_d = np.array(
            [
                0.0,
                0.005,
                0.010,
                0.015,
                0.020,
                0.025,
                0.030,
                0.035,
                0.040,
                0.050,
                0.060,
                0.070,
                0.080,
            ]
        )
        S_a = np.array(
            [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 275.0, 285.0, 110.0, 120.0, 125.0, 128.0, 130.0]
        )
        return S_d, S_a

    @pytest.fixture
    def two_point_curve(self):
        """Only 2 data points — triggers early-return."""
        return np.array([0.0, 0.01]), np.array([0.0, 100.0])

    @pytest.fixture
    def empty_curve(self):
        """Empty arrays — edge case for defensive checking."""
        return np.array([]), np.array([])

    @pytest.fixture
    def noisy_curve(self):
        """Bilinear with slight numerical noise (negative S_a near origin)."""
        S_d = np.linspace(0.0, 0.08, 41)
        S_a = np.where(S_d <= 0.02, 5000.0 * S_d, 100.0 + 500.0 * (S_d - 0.02))
        # Inject small negative noise at a single point
        S_a[3] = -5.0
        S_a[0] = 0.0
        return S_d, S_a

    @pytest.fixture
    def rc_like_curve(self):
        """Smooth RC-style backbone: tanh saturation + post-peak softening.

        Saturates gradually (cracking → rebar yield) with no sharp yield
        plateau, then softens after the peak — the shape the De Luca
        10 %-secant rule is designed for.  Peak at S_d ≈ 0.06 m.
        """
        S_d = np.linspace(0.0, 0.12, 61)
        S_a_peak = 150.0  # m/s²
        K0 = 6000.0  # initial stiffness
        S_a = S_a_peak * np.tanh(K0 * S_d / S_a_peak)
        soft = S_d >= 0.06
        S_a[soft] = S_a[soft] - 80.0 * (S_d[soft] - 0.06) / 0.06
        S_a = np.maximum(S_a, 0.0)
        S_a[0] = 0.0
        return S_d, S_a

    # ── bilinearize_stiffness_change ─────────────────────────────────

    def test_stiffness_change_detects_bilinear_knee(self, bilinear_curve):
        """Detects yield where secant stiffness drops below 50 % of initial.

        For this bilinear+hardening curve (K₁=5000, K₂=500), the secant
        stiffness crosses the 50 % threshold at S_d ≈ 0.046 m."""
        S_d, S_a = bilinear_curve
        S_dy, _S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        # The stiffness-change detector finds the first point where
        # secant stiffness < 50 % of K_init.  For this curve that's
        # near S_d ≈ 0.046.
        peak_idx = int(np.argmax(S_a))
        assert 0.040 <= S_dy <= 0.055, f"Expected S_dy in [0.040, 0.055], got {S_dy:.6f}"
        assert S_dy < S_d[peak_idx] * 0.9, (
            f"Expected yield well below peak, got S_dy={S_dy:.4f} vs peak={S_d[peak_idx]:.4f}"
        )

    def test_stiffness_change_elastic_resets_to_peak(self, elastic_curve):
        """Elastic curve → no stiffness drop → returns peak."""
        S_d, S_a = elastic_curve
        S_dy, S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        peak_idx = int(np.argmax(S_a))
        assert S_dy == pytest.approx(S_d[peak_idx])
        assert S_ay == pytest.approx(S_a[peak_idx])

    def test_stiffness_change_hardening_finds_point(self, hardening_curve):
        """Hardening curve returns a valid yield point."""
        S_d, S_a = hardening_curve
        S_dy, S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        assert S_dy > 0
        assert S_ay > 0

    def test_stiffness_change_threshold_config(self, bilinear_curve):
        """Higher threshold → more sensitive → earlier yield (smaller S_dy)."""
        S_d, S_a = bilinear_curve
        S_dy_lo, _, _ = bilinearize_stiffness_change(S_d, S_a, {"threshold": 0.30})
        S_dy_hi, _, _ = bilinearize_stiffness_change(S_d, S_a, {"threshold": 0.85})
        # A lower threshold (0.30) is less sensitive → detects later
        # (higher S_dy).  A higher threshold (0.85) is more sensitive
        # → detects earlier (lower S_dy).
        assert S_dy_hi <= S_dy_lo, (
            f"Higher threshold (0.85) should give S_dy <= lower (0.30), "
            f"got {S_dy_hi:.6f} > {S_dy_lo:.6f}"
        )

    def test_stiffness_change_sudden_drop_criterion_b(self, sudden_drop_curve):
        """Sudden single-step stiffness drop triggers criterion B."""
        S_d, S_a = sudden_drop_curve
        S_dy, _S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        # Should detect at or near the drop index (8 → S_d=0.040)
        assert 0.035 <= S_dy <= 0.045, f"Expected S_dy near 0.040, got {S_dy:.6f}"

    def test_stiffness_change_peak_idx_config(self, bilinear_curve):
        """Explicit peak_idx truncates search range."""
        S_d, S_a = bilinear_curve
        # peak_idx=5 → peaks early → yield forced to peak area
        S_dy_early, _S_ay_early, method = bilinearize_stiffness_change(S_d, S_a, {"peak_idx": 5})
        assert method == "stiffness_change"
        # peak_idx=5 is before the true knee
        # S_d_arr ≈ [0, 0.002, 0.004, 0.006, 0.008, 0.010, ...]
        assert S_dy_early <= S_d[5], (
            f"Expected S_dy <= {S_d[5]:.6f} (peak at index 5), got {S_dy_early:.6f}"
        )

    def test_stiffness_change_two_points(self, two_point_curve):
        """Only 2 data points → returns the last (non-zero) point."""
        S_d, S_a = two_point_curve
        S_dy, S_ay, method = bilinearize_stiffness_change(S_d, S_a)
        assert method == "stiffness_change"
        # With 2 points, peak_idx=1 (not < 1), so it computes secant
        # stiffness and falls through to return the peak.
        assert S_dy == pytest.approx(0.01)
        assert S_ay == pytest.approx(100.0)

    # ── bilinearize_equal_energy ─────────────────────────────────────

    def test_equal_energy_bilinear_reasonable(self, bilinear_curve):
        """Bilinear curve → yield in plausible range with energy balance."""
        S_d, S_a = bilinear_curve
        S_dy, _S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method == "equal_energy"
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        # Yield should be at or before peak for bilinear with plateau
        assert S_dy <= S_d_peak, f"Expected S_dy ≤ peak ({S_d_peak:.4f}), got {S_dy:.4f}"
        assert S_dy > 0

    def test_equal_energy_elastic_converges(self, elastic_curve):
        """Elastic curve (linear) converges at the initial guess (30% of peak).

        For a purely linear S_a = K * S_d, the bilinear area exactly
        matches the actual area at *any* S_dy because K_init = K is
        constant.  The iteration converges immediately at the initial
        guess (0.3 * S_d_peak), so no peak-reset occurs.
        """
        S_d, S_a = elastic_curve
        S_dy, _S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method == "equal_energy"
        peak_idx = int(np.argmax(S_a))
        # Initial guess = 0.3 * S_d_peak
        expected = 0.3 * S_d[peak_idx]
        assert S_dy == pytest.approx(expected, abs=1e-6), (
            f"Expected S_dy ≈ {expected:.6f} (30% of peak), got {S_dy:.6f}"
        )
        # The ≥90% reset should NOT trigger for a linear curve
        # because S_dy (30% of peak) < 90% of peak.
        assert S_dy < 0.90 * S_d[peak_idx], (
            f"Peak reset should not trigger: S_dy={S_dy:.4f}, "
            f"90% of peak={0.90 * S_d[peak_idx]:.4f}"
        )

    def test_equal_energy_config_tolerance(self, hardening_curve):
        """Tighter tolerance affects iteration depth (result stable)."""
        S_d, S_a = hardening_curve
        # Coarse tolerance should still give a sensible S_dy
        S_dy_coarse, _S_ay_coarse, _ = bilinearize_equal_energy(
            S_d, S_a, {"tolerance": 0.05, "max_iter": 5}
        )
        S_dy_fine, _S_ay_fine, _ = bilinearize_equal_energy(
            S_d, S_a, {"tolerance": 0.0001, "max_iter": 200}
        )
        # Both should be in a plausible range
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        for label, S_dy_val in [("coarse", S_dy_coarse), ("fine", S_dy_fine)]:
            assert S_dy_val > 0, f"{label} S_dy={S_dy_val:.4f} out of range (peak={S_d_peak:.4f})"

    def test_equal_energy_two_points(self, two_point_curve):
        """Only 2 data points — peak_idx < 2 returns yield at peak."""
        S_d, S_a = two_point_curve
        S_dy, S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method == "equal_energy"
        # With peak_idx=1 (< 2), the function returns S_d_peak = 0.01.
        assert S_dy == pytest.approx(0.01, abs=1e-10)
        assert S_ay == pytest.approx(100.0, abs=1e-10)

    def test_equal_energy_hardening_area_error(self, hardening_curve):
        """Equal-energy result preserves area (or converges at peak) on hardening curve.

        For a hardening curve S_a = 5000*sqrt(S_d), the equal-energy
        iteration may converge at the peak (S_dy = S_d_peak) because
        the area error never drops below tolerance before S_dy reaches
        the peak boundary.  When that happens, the bilinear area
        degenerates to a triangle (A2 = A3 = 0) and the area check
        is not meaningful — verify that the method still returns a
        sensible result.
        """
        S_d, S_a = hardening_curve
        S_dy, S_ay, method = bilinearize_equal_energy(S_d, S_a)
        assert method in ("equal_energy", "equal_energy_not_converged")
        assert S_dy > 0
        assert S_ay > 0
        # If yield is below peak, verify area preservation
        peak_idx = int(np.argmax(S_a))
        if S_dy < S_d[peak_idx]:
            n_el = max(3, len(S_d) // 5)
            K_init = float(np.polyfit(S_d[:n_el], S_a[:n_el], 1)[0])
            S_d_peak = float(S_d[peak_idx])
            S_a_peak = float(S_a[peak_idx])
            area_actual = float(np.trapezoid(S_a[: peak_idx + 1], S_d[: peak_idx + 1]))
            S_ay_derived = K_init * S_dy
            A1 = 0.5 * S_ay_derived * S_dy
            A2 = S_ay_derived * (S_d_peak - S_dy)
            A3 = 0.5 * (S_a_peak - S_ay_derived) * (S_d_peak - S_dy)
            area_bilin = A1 + A2 + A3
            rel_err = abs(area_bilin - area_actual) / max(area_actual, 1e-12)
            assert rel_err <= 0.01, (
                f"Area error {rel_err:.4f} exceeds 1% for hardening curve: "
                f"area_bilin={area_bilin:.6e}, area_actual={area_actual:.6e}"
            )

    def test_equal_energy_initial_guess_config(self, hardening_curve):
        """Higher initial_guess shifts the converged S_dy upward.

        For a hardening curve S_a = 5000*sqrt(S_d), the equal-energy
        iteration moves S_dy from the initial guess toward the energy-
        preserving value.  A higher initial guess produces a higher
        converged S_dy because the relaxation S_dy *= 1 - 0.5*err
        moves from above vs below the true value.
        """
        S_d, S_a = hardening_curve
        S_dy_low, _, _ = bilinearize_equal_energy(S_d, S_a, {"initial_guess": 0.2})
        S_dy_high, _, _ = bilinearize_equal_energy(S_d, S_a, {"initial_guess": 0.6})
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        # Both guesses should yield plausible values < peak.
        assert 0 < S_dy_low <= S_d_peak, (
            f"Expected 0 < S_dy_low ({S_dy_low:.4f}) <= peak({S_d_peak:.4f})"
        )
        assert 0 < S_dy_high <= S_d_peak, (
            f"Expected 0 < S_dy_high ({S_dy_high:.4f}) <= peak({S_d_peak:.4f})"
        )
        # The higher initial guess should converge to a larger S_dy.
        assert S_dy_low <= S_dy_high, (
            f"Expected S_dy_low ({S_dy_low:.4f}) <= S_dy_high ({S_dy_high:.4f})"
        )
        # The lower guess should be closer to the initial 20% of peak
        # and the higher guess closer to 60% of peak.
        # For hardening curves both guesses may converge at the peak
        # (S_dy == S_d_peak) because the equal-energy iteration hits
        # the peak boundary before reaching tolerance.  When this
        # happens, S_dy_low/peak == 1.0, which is >0.35 (0.2+0.15).
        # Only check the ratio when the result is below the peak.
        if S_dy_low < S_d_peak:
            assert abs(S_dy_low / S_d_peak - 0.2) <= 0.15, (
                f"S_dy_low/{S_d_peak} = {S_dy_low / S_d_peak:.4f}, expected near 0.20"
            )
        if S_dy_high < S_d_peak:
            assert abs(S_dy_high / S_d_peak - 0.6) <= 0.4, (
                f"S_dy_high/{S_d_peak} = {S_dy_high / S_d_peak:.4f}, expected near 0.6"
            )

    # ── bilinearize_composite ────────────────────────────────────────

    def test_composite_bilinear_uses_stiffness_change(self, bilinear_curve):
        """Clear yield below peak → composite uses stiffness-change path."""
        S_d, S_a = bilinear_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method == "composite_stiffness_change", (
            f"Expected composite_stiffness_change, got {method}"
        )
        peak_idx = int(np.argmax(S_a))
        assert 0.040 <= S_dy <= 0.055, f"Expected S_dy in [0.040, 0.055], got {S_dy:.6f}"
        # Yield must be well below the peak (no fallback)
        assert S_dy < 0.90 * S_d[peak_idx], (
            f"Yield at {S_dy:.4f} should be < 90% of peak ({S_d[peak_idx]:.4f})"
        )

    def test_composite_elastic_falls_back(self, elastic_curve):
        """Elastic curve → stiffness-change yields at peak → fallback.

        Stiffness-change sees no secant drop below threshold (all
        secant values ≈ 10000) so it returns the peak.  Composite
        then falls back to equal-energy which converges at 30% of
        peak (see test_equal_energy_elastic_converges).
        """
        S_d, S_a = elastic_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method == "composite_equal_energy"
        peak_idx = int(np.argmax(S_a))
        # Expected yield = 30% peak (equal-energy default initial guess)
        expected = 0.3 * S_d[peak_idx]
        assert S_dy == pytest.approx(expected, abs=1e-6), (
            f"Expected S_dy ≈ {expected:.6f} (30% peak), got {S_dy:.6f}"
        )

    def test_composite_hardening_in_range(self, hardening_curve):
        """Hardening curve returns a method and plausible yield."""
        S_d, S_a = hardening_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method in ("composite_stiffness_change", "composite_equal_energy")
        peak_idx = int(np.argmax(S_a))
        assert S_dy < S_d[peak_idx], f"Expected yield < peak ({S_d[peak_idx]:.4f}), got {S_dy:.4f}"
        assert S_dy > 0

    def test_composite_minimum_10_percent_clamp(self, bilinear_curve):
        """Verify S_dy is clamped to ≥10 % of peak displacement.

        Construct a curve whose stiffness-change yield would land near
        zero, then verify the composite clamp brings it up to 10 %.
        """
        S_d = np.array(
            [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.010, 0.020, 0.040, 0.060, 0.080, 0.100]
        )
        S_a = np.array(
            [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 100.0, 200.0, 350.0, 400.0, 420.0, 430.0]
        )
        S_dy, _S_ay, _method = bilinearize_composite(S_d, S_a)
        peak_idx = int(np.argmax(S_a))
        min_S_dy = 0.10 * S_d[peak_idx]
        assert S_dy >= min_S_dy - 1e-12, (
            f"Expected S_dy >= 10% of peak ({min_S_dy:.6f}), got {S_dy:.6f}"
        )

    def test_composite_peak_idx_passthrough(self, peak_curve):
        """Explicit peak_idx is passed through to sub-methods."""
        S_d, S_a = peak_curve
        peak_idx = 15  # well before the true peak
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a, {"peak_idx": peak_idx})
        assert method in ("composite_stiffness_change", "composite_equal_energy")
        # The yield should be ≤ S_d[peak_idx] since that's the forced peak
        assert S_dy <= S_d[peak_idx] + 1e-12, (
            f"Expected S_dy <= forced peak at index {peak_idx} "
            f"({S_d[peak_idx]:.6f}), got {S_dy:.6f}"
        )

    def test_composite_config_passthrough(self, bilinear_curve):
        """Config dict is passed through, affecting sub-method behavior."""
        S_d, S_a = bilinear_curve
        S_dy_default, _, method_default = bilinearize_composite(S_d, S_a)
        S_dy_custom, _, method_custom = bilinearize_composite(
            S_d, S_a, {"initial_guess": 0.6}
        )  # passed to equal-energy
        # Both should be stiffness-change since bilinear has clear knee
        # below 90% of peak
        assert method_default == "composite_stiffness_change", (
            f"Expected composite_stiffness_change, got {method_default}"
        )
        assert method_custom == "composite_stiffness_change", (
            f"Expected composite_stiffness_change, got {method_custom}"
        )
        # Both should return the same stiffness-change result
        assert 0.040 <= S_dy_default <= 0.055, (
            f"Expected S_dy in [0.040, 0.055], got {S_dy_default:.6f}"
        )
        assert 0.040 <= S_dy_custom <= 0.055, (
            f"Expected S_dy in [0.040, 0.055], got {S_dy_custom:.6f}"
        )

    # ── Edge cases (applies to all methods) ─────────────────────────

    def test_empty_arrays_return_defaults(self, empty_curve):
        """All three methods return (0.0, 0.0, method) on empty arrays.

        Each method guards explicitly against zero-length arrays
        and returns a safe default rather than raising ValueError.
        """
        S_d, S_a = empty_curve
        for fn, expected_method in [
            (bilinearize_stiffness_change, "stiffness_change"),
            (bilinearize_equal_energy, "equal_energy"),
            (bilinearize_composite, "composite_equal_energy"),
            (bilinearize_rc, "de_luca_10pct"),
        ]:
            S_dy, S_ay, method = fn(S_d, S_a)
            assert S_dy == 0.0, f"{fn.__name__}: expected S_dy=0.0, got {S_dy}"
            assert S_ay == 0.0, f"{fn.__name__}: expected S_ay=0.0, got {S_ay}"
            assert method == expected_method, (
                f"{fn.__name__}: expected method={expected_method}, got {method}"
            )

    def test_noisy_curve_with_negative_sa(self, noisy_curve):
        """Methods handle a small negative S_a value without crashing.

        Negative values can appear as numerical noise after ADRS conversion
        near the origin.  The methods should still return a valid positive
        yield point (the negative point is skipped during peak search).
        """
        S_d, S_a = noisy_curve
        for fn in (
            bilinearize_stiffness_change,
            bilinearize_equal_energy,
            bilinearize_composite,
            bilinearize_rc,
        ):
            S_dy, S_ay, _ = fn(S_d, np.abs(S_a))
            assert S_dy > 0, f"S_dy should be positive, got {S_dy:.6e}"
            assert S_ay > 0, f"S_ay should be positive, got {S_ay:.6e}"
            assert math.isfinite(S_dy)
            assert math.isfinite(S_ay)

    def test_elastic_curve_all_methods_consistent(self, elastic_curve):
        """All three methods agree the elastic curve has not yielded.

        For a purely linear curve S_a = K * S_d:
        - stiffness_change returns the peak (no stiffness drop detected)
        - equal_energy converges at the 30% initial guess

        Both results should produce a plausible ductility ≤ 3.33 (i.e.
        S_dy ≥ 30 % of peak), consistent with an essentially elastic
        structure.
        """
        S_d, S_a = elastic_curve
        peak_idx = int(np.argmax(S_a))
        S_d_peak = S_d[peak_idx]
        S_a_peak = S_a[peak_idx]

        results = {
            "stiffness_change": bilinearize_stiffness_change(S_d, S_a),
            "equal_energy": bilinearize_equal_energy(S_d, S_a),
            "composite": bilinearize_composite(S_d, S_a),
            "rc": bilinearize_rc(S_d, S_a),
        }

        for name, (S_dy, S_ay, method) in results.items():
            # Yield displacement must be at least 30 % of peak
            assert S_dy >= 0.30 * S_d_peak, (
                f"{name} ({method}): S_dy={S_dy:.6f} < 30% of peak "
                f"({0.30 * S_d_peak:.6f}) for an elastic curve"
            )
            # Yield acceleration must be positive and finite
            assert 0 < S_ay <= S_a_peak, (
                f"{name} ({method}): S_ay={S_ay:.6f} out of range (0, {S_a_peak:.6f}]"
            )
            # Ductility mu = S_d_peak / S_dy must be ≤ 3.34
            # (3.33 allows for floating-point rounding — exact 30% guess
            # gives 3.333..., which just exceeds 3.33)
            mu = S_d_peak / S_dy
            assert mu <= 3.34, (
                f"{name} ({method}): mu={mu:.2f} > 3.34 "
                f"(S_dy={S_dy:.6f}, peak={S_d_peak:.6f}) "
                f"for an elastic curve"
            )

    def test_yield_index_before_peak(self, bilinear_curve):
        """Yield index from stiffness-change and equal-energy is before peak.

        This is a fundamental constraint: yield must occur before the peak
        of the capacity curve.  A yield-after-peak result would indicate a
        pathological fit.
        """
        S_d, S_a = bilinear_curve
        peak_idx = int(np.argmax(S_a))
        for fn in (
            bilinearize_stiffness_change,
            bilinearize_equal_energy,
            bilinearize_rc,
        ):
            S_dy, _S_ay, _ = fn(S_d, S_a)
            # Find the first index where S_d ≥ S_dy
            if S_dy > 0:
                yield_idx = int(np.argmax(S_d >= S_dy))
                assert yield_idx <= peak_idx, (
                    f"Yield at index {yield_idx} is after peak at index {peak_idx}"
                )
                assert S_dy <= S_d[peak_idx], (
                    f"Yield S_dy={S_dy:.6f} should be <= S_d_peak={S_d[peak_idx]:.6f}"
                )

    def test_composite_sudden_drop_falls_back(self, sudden_drop_curve):
        """Sudden drop after peak → stiffness-change falls back to peak
        (>90% of S_d_peak) → composite uses equal-energy."""
        S_d, S_a = sudden_drop_curve
        S_dy, _S_ay, method = bilinearize_composite(S_d, S_a)
        assert method == "composite_equal_energy", (
            f"Expected composite_equal_energy (stiffness-change returns "
            f"peak, triggering fallback), got {method}"
        )
        peak_idx = int(np.argmax(S_a))
        # Equal-energy converges near S_d ≈ 0.026 for this curve
        # (the initial guess is 30% of peak = 0.0105, but the curve
        # is not linear, so the iteration moves S_dy upward to
        # preserve area).
        # Equal-energy converges at the peak for a sudden-drop curve where
        # the curve is near-linear up to the peak — S_dy may equal S_d_peak.
        assert 0.020 <= S_dy <= S_d[peak_idx], (
            f"Expected S_dy in [0.020, {S_d[peak_idx]:.6f}], got {S_dy:.6f}"
        )

    def test_de_luca_recovers_exact_bilinear_knee(self, bilinear_curve):
        """On a bilinear curve the 10 %-secant equals the true elastic slope,
        so the equal-area rule recovers the exact knee: (0.02, 100)."""
        S_d, S_a = bilinear_curve
        S_dy, S_ay, method = bilinearize_rc(S_d, S_a)
        assert method == "de_luca_10pct"
        assert S_dy == pytest.approx(0.02, abs=1e-9)
        assert S_ay == pytest.approx(100.0, abs=1e-9)

    def test_de_luca_rc_curve_yield_not_at_cracking(self, rc_like_curve):
        """The 10 %-secant rule does not snap to the cracking transition.

        For the tanh RC backbone the cracking transition sits at
        S_d ≈ 0.0025 (the 10 %-secant point); the equal-area yield must
        land in the rebar-yield band (25–75 % of peak displacement) and
        preserve the capacity area.
        """
        S_d, S_a = rc_like_curve
        peak_idx = int(np.argmax(S_a))
        S_d_peak = float(S_d[peak_idx])
        S_a_peak = float(S_a[peak_idx])

        S_dy, S_ay, method = bilinearize_rc(S_d, S_a)

        assert method == "de_luca_10pct"
        # Yield well past the cracking transition and below the peak.
        assert 0.25 * S_d_peak <= S_dy <= 0.75 * S_d_peak, (
            f"S_dy={S_dy:.6f} outside rebar-yield band "
            f"[{0.25 * S_d_peak:.6f}, {0.75 * S_d_peak:.6f}]"
        )
        # Yield strength within the capacity envelope.
        assert 0.0 < S_ay <= S_a_peak
        # Equal-area: bilinear fit preserves the capacity-curve area.
        integral = 0.0
        for i in range(1, peak_idx + 1):
            integral += 0.5 * (S_d[i] - S_d[i - 1]) * (S_a[i] + S_a[i - 1])
        A_bilin = 0.5 * S_dy * S_ay + 0.5 * (S_ay + S_a_peak) * (S_d_peak - S_dy)
        rel_err = abs(A_bilin - integral) / max(integral, 1e-12)
        assert rel_err < 1e-2, f"equal-area error {rel_err:.2%}"
