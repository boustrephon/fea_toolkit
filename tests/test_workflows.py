"""End-to-end workflow tests using the built-in cantilever sample model.

Each test exercises a complete workflow from model data through analysis,
verifying that the pipeline runs without errors.  Assertions are kept
minimal (the workflow completed, returned a dict with expected keys, etc.)
so they don't break when new features are added.
"""

import numpy as np
import openseespy.opensees as ops
import pytest

from examples.sample_model import (
    make_nonlinear_sample_model,
    make_rc_frame_model,
    make_sample_model,
)

# The pushover tests use the base "UB300" Section (no fiber patches), so the
# AnalysisBuilder legitimately warns and falls back to elastic elements.  This
# is expected behavior — suppress the specific message so the test suite output
# stays clean, but leave any other warnings visible.
pytestmark = pytest.mark.filterwarnings("ignore:Section 'UB300' does not support fiber patches")

# ============================================================================
# Constants
# ============================================================================

_AB_CONFIG = {
    "element_type": "elasticBeamColumn",
    "verbose": False,
    "create_shells": False,
}


# ============================================================================
# Constants for nonlinear / fiber-based analysis
# ============================================================================

_NL_CONFIG = {
    "element_type": "dispBeamColumn",
    "create_fiber_sections": True,
    "use_elastic_sections": False,
    "verbose": False,
    "create_shells": False,
}


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_md():
    """Built-in 10 m steel cantilever model (no external files needed)."""
    return make_sample_model()


@pytest.fixture
def sample_ab(sample_md):
    """AnalysisBuilder pre-configured with elastic sections (no shells).

    Uses the two-stage pipeline (Preprocessor + AnalysisBuilder).
    Tears down OpenSees global state after each test.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mesh_model = preprocess_model(sample_md, _AB_CONFIG)
    b = AnalysisBuilder(mesh_model, _AB_CONFIG)
    yield b
    ops.wipe()


@pytest.fixture
def sample_nl_md():
    """Built-in 10 m steel cantilever with an ISection (fiber-based).

    ``ISection.to_fiber_patches()`` succeeds, so
    :meth:`AnalysisBuilder.run_pushover_analysis` automatically rebuilds
    the domain with ``dispBeamColumn`` + fiber sections and produces a
    genuinely nonlinear (yielding) pushover curve.
    """
    return make_nonlinear_sample_model()


@pytest.fixture
def sample_nl_ab(sample_nl_md):
    """AnalysisBuilder for the nonlinear ISection cantilever.

    The initial domain is built with elastic sections (used for the
    modal analysis); the pushover step rebuilds with fiber sections
    internally via ``rebuild_with_fiber_sections()``.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mesh_model = preprocess_model(sample_nl_md, _AB_CONFIG)
    b = AnalysisBuilder(mesh_model, _AB_CONFIG)
    yield b
    ops.wipe()


@pytest.fixture
def sample_rc_md():
    """Built-in single-storey RC moment frame with fiber-capable sections.

    ``ConcreteRectangularSection`` (columns) and ``RectangularSection``
    (beams) both override ``to_fiber_patches()``, so
    :meth:`AnalysisBuilder.run_pushover_analysis` rebuilds the domain
    with ``dispBeamColumn`` + fiber sections and produces a genuinely
    nonlinear (yielding) pushover curve with meaningful ductility.
    """
    return make_rc_frame_model()


@pytest.fixture
def sample_rc_ab(sample_rc_md):
    """AnalysisBuilder for the single-storey RC moment frame.

    The initial domain is built with elastic sections (used for the
    modal analysis); the pushover step rebuilds with fiber sections
    internally via ``rebuild_with_fiber_sections()``.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mesh_model = preprocess_model(sample_rc_md, _AB_CONFIG)
    b = AnalysisBuilder(mesh_model, _AB_CONFIG)
    yield b
    ops.wipe()


# ============================================================================
# Workflow: Build model
# ============================================================================


class TestBuildWorkflow:
    """Verify model building completes and produces expected structure."""

    def test_build_domain_returns_none(self, sample_ab):
        """Builder can construct a complete OpenSees domain from MeshModel."""
        sample_ab.build_domain()
        assert True

    def test_build_creates_frame_tag_map(self, sample_ab):
        """Build produces an element-tag mapping for load application."""
        sample_ab.build_domain()
        assert "1" in sample_ab.frame_tag_map
        assert sample_ab.frame_tag_map["1"] == 1

    def test_build_sets_load_totals(self, sample_ab):
        """Build accumulates applied load totals per pattern."""
        sample_ab.build_domain()
        assert hasattr(sample_ab, "load_totals")
        # Note: load_totals may be empty after build_domain() alone;
        # they are populated during run_static_analysis().
        # This test just verifies the attribute exists.

    def test_build_with_split_elements(self, sample_md):
        """Build with element splitting at joints."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {
            "element_type": "elasticBeamColumn",
            "split_elements": True,
            "verbose": False,
            "create_shells": False,
        }
        mesh_model = preprocess_model(sample_md, cfg)
        ab = AnalysisBuilder(mesh_model, cfg)
        try:
            ab.build_domain()
        finally:
            ops.wipe()

    def test_rebuild_preserves_geometry(self, sample_md):
        """Rebuilding with different pattern scales does not corrupt the model.

        Captures node coordinates and element tags after the first
        rebuild, then asserts they are unchanged after the second rebuild.
        """
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}
        mm = preprocess_model(sample_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            r1 = b.run_static_analysis(pattern_scales={"DEAD": 1.0})
            # Capture geometry after first rebuild
            node_tags_1 = sorted(ops.getNodeTags())
            coords_1 = {t: tuple(ops.nodeCoord(t)) for t in node_tags_1}
            ele_tags_1 = sorted(ops.getEleTags())

            r2 = b.run_static_analysis(pattern_scales={"DEAD": 1.0, "WIND": 0.5})
            # Capture geometry after second rebuild
            node_tags_2 = sorted(ops.getNodeTags())
            coords_2 = {t: tuple(ops.nodeCoord(t)) for t in node_tags_2}
            ele_tags_2 = sorted(ops.getEleTags())

            assert r1 is not None
            assert r2 is not None
            assert node_tags_1 == node_tags_2, "Node tags changed on rebuild"
            assert coords_1 == coords_2, "Node coordinates changed on rebuild"
            assert ele_tags_1 == ele_tags_2, "Element tags changed on rebuild"
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Static analysis
# ============================================================================


class TestStaticAnalysisWorkflow:
    """End-to-end linear static analysis via AnalysisBuilder."""

    @pytest.fixture
    def static_ab(self, sample_md):
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}
        mm = preprocess_model(sample_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        yield b
        ops.wipe()

    def test_static_analysis_returns_dict(self, sample_ab):
        """Static analysis produces a result dict with nodal data and reactions."""
        sample_ab.build_domain()
        results = sample_ab.run_static_analysis(extract_reactions=True)
        assert isinstance(results, dict)
        assert "nodal_displacements" in results

    def test_static_analysis_displacements(self, static_ab):
        """Cantilever tip displaces under lateral wind load."""
        static_ab.build_domain()
        results = static_ab.run_static_analysis(
            pattern_scales={"WIND": 1.0},
            extract_reactions=True,
        )
        # AnalysisBuilder keys nodal displacements by node_id (string)
        disp = results.get("nodal_displacements", {})
        assert "2" in disp, f"node '2' not in displacements: {list(disp.keys())}"
        dx, _dy, _dz = disp["2"][:3]
        assert abs(dx) > 1e-6, f"top node X displacement is zero under wind (dx={dx})"

    def test_static_element_forces(self, sample_ab):
        """Element end-forces can be extracted after static analysis."""
        sample_ab.build_domain()
        sample_ab.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        forces = sample_ab.extract_static_element_forces()
        assert isinstance(forces, dict)
        assert len(forces) > 0, "extract_static_element_forces() returned empty dict"
        tag = next(iter(forces.keys()))
        f = forces[tag]
        assert "Fx" in f
        assert "Mz" in f

    def test_static_gravity_vs_pattern(self, static_ab):
        """Multiple static analyses can be run sequentially with different load sets."""
        static_ab.build_domain()
        r1 = static_ab.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        r2 = static_ab.run_static_analysis(
            pattern_scales={"DEAD": 1.0, "WIND": 1.0},
        )
        assert isinstance(r1, dict)
        assert isinstance(r2, dict)
        assert "nodal_displacements" in r1
        assert "nodal_displacements" in r2
        # AnalysisBuilder keys by node_id (string)
        assert "2" in r1["nodal_displacements"]
        assert "2" in r2["nodal_displacements"]
        d1 = r1["nodal_displacements"]["2"]
        d2 = r2["nodal_displacements"]["2"]
        assert abs(d2[0]) >= abs(d1[0]) - 1e-12, (
            f"X displacement did not increase with wind ({d1[0]} → {d2[0]})"
        )

    def test_static_reactions_equilibrium(self, static_ab):
        """Reactions at restrained nodes balance applied gravity loads.

        Compares summed vertical reactions against the applied load totals
        from builder.load_totals, verifying equilibrium: ΣFz_reactions ≈
        -ΣFz_applied.
        """
        static_ab.build_domain()
        results = static_ab.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
            extract_reactions=True,
        )
        # AnalysisBuilder returns 'reactions' keyed by node_id, not summed
        reactions = results.get("reactions", {})
        assert reactions, "reactions missing or empty"
        summed_fz = sum(r.get("fz", 0) for r in reactions.values())
        assert abs(summed_fz) > 1e-6, f"vertical reaction Fz sum is near zero: {summed_fz}"

        # ── Verify equilibrium against applied load totals ─────────
        # load_totals maps pattern_name -> total vector magnitude in
        # opensees coordinate system (positive = upward at nodes).
        # Reactions are also in the opensees system (positive = upward).
        # For a DEAD load (gravity), applied_Fz_total should be negative
        # (downward), whereas reactions sum to positive (upward).
        load_totals = getattr(static_ab, "load_totals", {})
        if "DEAD" in load_totals:
            applied_fz = load_totals["DEAD"]
            # Equilibrium: ΣFz_reaction ≈ ΣFz_applied (both store
            # positive magnitudes in their respective sign conventions).
            # The scalar load_totals is the magnitude of applied loads,
            # while summed_fz is the reaction resultant — they should
            # be equal in magnitude.
            ratio = abs(summed_fz - applied_fz) / max(abs(applied_fz), 1.0)
            assert ratio < 0.10, (
                f"Equilibrium check failed: applied Fz = {applied_fz:.2f}, "
                f"reaction Fz = {summed_fz:.2f}, ratio = {ratio:.4f} (> 0.10)"
            )

    def test_static_self_weight_consistency(self, static_ab, sample_md):
        """Applied self-weight loads match expected values from geometry."""
        from fea_toolkit.model.checks import check_self_weight_consistency

        static_ab.build_domain()
        # Create DEAD loads to populate _sw_load_totals (build_domain alone
        # does not create loads — that happens in create_loads).
        static_ab.create_loads(pattern_scales={"DEAD": 1.0})
        report = check_self_weight_consistency(
            sample_md,
            load_totals=static_ab._sw_load_totals,
            verbose=False,
        )
        assert report["passed"], (
            f"Self-weight mismatch: expected {report['expected']}, "
            f"applied {report['applied']} "
            f"(disc. {report['discrepancy']} > tol. {report['tolerance']})"
        )
        # 10 m cantilever with real UB305×165×40 section (A = 0.00509434 m²,
        # steel unit_weight 78500 N/m³): 0.00509434 × 78500 × 10 ≈ 3999.06 N.
        assert abs(report["expected"] - 3999.0569) < 1.0


# ============================================================================
# Workflow: Modal analysis
# ============================================================================


class TestModalAnalysisWorkflow:
    """End-to-end eigenvalue / modal analysis."""

    def test_modal_analysis_returns_keys(self, sample_ab):
        """Modal analysis returns periods, eigenvalues, and frequencies."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        modal = sample_ab.run_modal_analysis(num_modes=3)
        assert isinstance(modal, dict)
        assert "periods" in modal
        assert "eigenvalues" in modal
        assert "frequencies" in modal
        assert len(modal["periods"]) == 3
        assert len(modal["eigenvalues"]) == 3
        assert len(modal["frequencies"]) == 3

    def test_modal_first_period_positive(self, sample_ab):
        """Fundamental period of a 10 m steel cantilever is in a reasonable range."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        modal = sample_ab.run_modal_analysis(num_modes=3)
        T1 = modal["periods"][0]
        assert 0.01 < T1 < 10.0, f"T1={T1} outside plausible range"

    def test_extract_mode_shapes(self, sample_ab):
        """Mode shapes can be extracted after eigenvalue analysis."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        sample_ab.run_modal_analysis(num_modes=2)
        shapes = sample_ab.extract_mode_shapes(num_modes=2)
        assert isinstance(shapes, dict)
        assert 0 in shapes, "mode 0 missing from shapes"
        assert 1 in shapes, "mode 1 missing from shapes"
        assert len(shapes) == 2, f"expected 2 modes, got {len(shapes)}"


# ============================================================================
# Workflow: Pushover analysis
# ============================================================================


class TestPushoverWorkflow:
    """End-to-end non-linear pushover analysis (truss-brace approach)."""

    def test_pushover_uniform_returns_keys(self, sample_ab):
        """Uniform-mass-proportional pushover produces a capacity curve."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        results = sample_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        assert isinstance(results, dict)
        assert "control_disp" in results
        assert "base_shear" in results
        assert "step" in results
        assert len(results["control_disp"]) > 1, "uniform: control_disp empty"
        assert len(results["base_shear"]) > 1, "uniform: base_shear empty"
        assert len(results["step"]) > 1, "uniform: step empty"
        assert abs(results["base_shear"][-1]) > 1e-6, "uniform: final base_shear zero"

    def test_pushover_triangular_returns_keys(self, sample_ab):
        """Triangular (ELF) pushover produces a valid capacity curve."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        results = sample_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="triangular",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        assert isinstance(results, dict)
        assert "control_disp" in results
        assert "base_shear" in results
        assert "step" in results
        assert len(results["control_disp"]) > 1, "triangular: control_disp empty"
        assert len(results["base_shear"]) > 1, "triangular: base_shear empty"
        assert abs(results["base_shear"][-1]) > 1e-6, "triangular: final base_shear zero"

    def test_pushover_pattern_returns_keys(self, sample_ab):
        """SAP2000-pattern-based pushover uses existing distributed loads."""
        sample_ab.build_domain()
        results = sample_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="pattern",
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        assert isinstance(results, dict)
        assert "control_disp" in results
        assert "base_shear" in results
        assert "step" in results
        assert len(results["control_disp"]) > 1, "pattern: control_disp empty"
        assert len(results["base_shear"]) > 1, "pattern: base_shear empty"
        assert abs(results["base_shear"][-1]) > 1e-6, "pattern: final base_shear near zero"


# ============================================================================
# Workflow: Response spectrum analysis
# ============================================================================


class TestResponseSpectrumWorkflow:
    """End-to-end response spectrum analysis with GB 50011 spectrum."""

    @pytest.fixture
    def spectrum(self):
        """Simple elastic design spectrum (generic, not code-specific)."""
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 6.0]
        # Moderate acceleration values (m/s²)
        accels = [0.5, 1.5, 1.5, 1.5, 0.75, 0.375, 0.25, 0.125]
        return periods, accels

    def test_rs_analysis_returns_dict(self, sample_ab, spectrum):
        """CQC response-spectrum analysis computes combined base shear."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        modal = sample_ab.run_modal_analysis(num_modes=3)
        periods, accels = spectrum
        results = sample_ab.run_response_spectrum_analysis(
            num_modes=3,
            modal_periods=modal["periods"],
            spectrum_periods=periods,
            spectrum_accels=accels,
            direction="X",
            damping_ratio=0.05,
        )
        assert isinstance(results, dict)
        assert "base_shear_cqc" in results, "base_shear_cqc missing from RS results"
        assert abs(results["base_shear_cqc"]) > 1e-6, (
            f"base_shear_cqc is near zero ({results['base_shear_cqc']})"
        )

    def test_element_rs_forces(self, sample_ab, spectrum):
        """Element-level RS forces are available after spectrum analysis."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        modal = sample_ab.run_modal_analysis(num_modes=3)
        periods, accels = spectrum
        sample_ab.run_response_spectrum_analysis(
            num_modes=3,
            modal_periods=modal["periods"],
            spectrum_periods=periods,
            spectrum_accels=accels,
            direction="X",
            damping_ratio=0.05,
        )
        rs_forces = sample_ab.extract_element_rs_forces(
            num_modes=3,
            modal_periods=modal["periods"],
            spectrum_periods=periods,
            spectrum_accels=accels,
            direction="X",
        )
        assert isinstance(rs_forces, dict)
        assert "element_results" in rs_forces, "element_results missing from RS element forces"
        er = rs_forces["element_results"]
        assert len(er) > 0, "element_results is empty"
        first = er[0]
        for key in ("Vz_i", "My_i", "Mz_i"):
            assert key in first, f"{key} missing from RS element result"
        assert abs(first["Vz_i"]) > 1e-6, "Vz_i is zero in RS element result"


# ============================================================================
# Workflow: Results export
# ============================================================================


class TestExportWorkflow:
    """End-to-end NPZ export."""

    def test_export_to_npz(self, sample_ab, tmp_path):
        """Static results can be exported to compressed NumPy archive."""
        sample_ab.build_domain()
        results = sample_ab.run_static_analysis(
            pattern_scales={"DEAD": 1.0, "WIND": 1.0},
        )
        npz_path = str(tmp_path / "test_results.npz")
        sample_ab.export_results(filepath=npz_path, static_results=results)
        import numpy as np

        with np.load(npz_path, allow_pickle=True) as data:
            # AnalysisBuilder.export_results uses frame_sap_id not sub_elem_tags
            assert "frame_sap_id" in data
            assert "node_tag" in data

    def test_export_with_section_responses(self, sample_ab, tmp_path):
        """NPZ export includes geometry arrays when requested."""
        sample_ab.build_domain()
        results = sample_ab.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        npz_path = str(tmp_path / "test_results_sec.npz")
        sample_ab.export_results(filepath=npz_path, static_results=results)
        import numpy as np

        with np.load(npz_path, allow_pickle=True) as data:
            assert "frame_sap_id" in data
            assert "node_tag" in data


# ============================================================================
# Workflow: Unified NPZ pipeline (write → read → adapters)
# ============================================================================


class TestUnifiedNpzPipeline:
    """End-to-end unified NPZ pipeline: analyse → write → read → visualise.

    Exercises the full data path that users would follow when saving results
    to a NPZ archive and then loading them for plotting or colouring.

    Pipeline::

        AnalysisBuilder.run_static_analysis()
        AnalysisBuilder.run_modal_analysis()
        AnalysisBuilder.extract_mode_shapes()
            │
            ▼
        write_results_npz()  ──→  results.npz
            │
            ▼
        read_results_npz()   ──→  data dict
            │
            ├── npz_to_pyvista_frame_mesh()   → PyVista lines
            ├── npz_to_pyvista_shell_mesh()   → PyVista quads
            ├── npz_to_pyvista_modal_mesh()   → mode shape mesh
            ├── npz_to_rhino_colour_data()   → {sap_id: value}
            ├── npz_build_id_tag_map()        → {sap_id: tag}
            ├── npz_build_child_map()         → {parent: [children]}
            └── npz_build_parent_map()        → {child: parent}
    """

    @pytest.fixture
    def analysed_builder(self, sample_md):
        """AnalysisBuilder pre-built with static + modal analysis."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mesh_model = preprocess_model(sample_md, _AB_CONFIG)
        b = AnalysisBuilder(mesh_model, _AB_CONFIG)
        b.build_domain()
        b.compute_seismic_masses()
        # Stash the original SAPModelData for NPZ writer functions
        b.sap_model_data = sample_md
        yield b
        ops.wipe()

    def test_write_and_read_static(self, analysed_builder, tmp_path):
        """Static analysis results can be written to NPZ and read back.

        Exercises:
            run_static_analysis() →
            write_results_npz() →
            read_results_npz() →
            npz_to_pyvista_frame_mesh()
        """
        md = analysed_builder.sap_model_data
        # Run static
        static_result = analysed_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        # Convert element_forces from per-element dict to per-array format
        # that _collect_static() expects: {"fx_i": [...], "fy_i": [...], ...}
        ef_by_tag = analysed_builder.extract_static_element_forces()
        if ef_by_tag:
            elem_forces_arr: dict = {}
            force_keys_lower = [
                "fx_i",
                "fy_i",
                "fz_i",
                "mx_i",
                "my_i",
                "mz_i",
                "fx_j",
                "fy_j",
                "fz_j",
                "mx_j",
                "my_j",
                "mz_j",
            ]
            upper_to_lower = {
                "Fx": "fx_i",
                "Fy": "fy_i",
                "Fz": "fz_i",
                "Mx": "mx_i",
                "My": "my_i",
                "Mz": "mz_i",
                "Fx_j": "fx_j",
                "Fy_j": "fy_j",
                "Fz_j": "fz_j",
                "Mx_j": "mx_j",
                "My_j": "my_j",
                "Mz_j": "mz_j",
            }
            for key in force_keys_lower:
                elem_forces_arr[key] = []
            for tag, fdict in ef_by_tag.items():
                for upper_key, lower_key in upper_to_lower.items():
                    elem_forces_arr[lower_key].append(fdict.get(upper_key, 0.0))
            static_result["element_forces"] = elem_forces_arr
        # Package as {case_name: result_dict}
        static_results = {"DEAD": static_result}

        from fea_toolkit.io.npz_reader import (
            _get_static_cases,
            npz_to_pyvista_frame_mesh,
            read_results_npz,
        )
        from fea_toolkit.io.npz_writer import write_results_npz

        npz_path = str(tmp_path / "test_unified_static.npz")
        write_results_npz(npz_path, md, static_results=static_results)

        # Read back
        data = read_results_npz(npz_path)
        assert "node_tag" in data
        assert "frame_sap_id" in data
        assert "static_case_labels" in data

        cases = _get_static_cases(data)
        assert "DEAD" in cases

        # Verify geometry arrays
        n_node = len(data["node_tag"])
        assert n_node > 0
        assert len(data["node_x"]) == n_node
        assert len(data["node_y"]) == n_node
        assert len(data["node_z"]) == n_node

        # Verify static force arrays exist
        assert "static/DEAD/fx_i" in data
        assert "static/DEAD/fy_i" in data
        assert "static/DEAD/fz_i" in data
        assert "static/DEAD/mz_i" in data

        # Verify nodal displacement arrays
        assert "static/DEAD/node_dx" in data
        assert "static/DEAD/node_dy" in data
        assert "static/DEAD/node_dz" in data

        # --- Adapter: PyVista frame mesh ---
        points, lines, disp, sap_ids = npz_to_pyvista_frame_mesh(
            data,
            deformed_case="DEAD",
            scale=10.0,
        )
        assert points.shape[0] > 0
        assert lines.shape[0] > 0
        assert points.shape == disp.shape
        assert len(sap_ids) == lines.shape[0]
        # Displacements should be non-zero under DEAD load
        assert np.any(np.abs(disp) > 1e-12)

    def test_write_and_read_modal(self, analysed_builder, tmp_path):
        """Modal results can be written to NPZ, read back, and used for
        mode-shape visualisation.

        Exercises:
            run_modal_analysis() →
            extract_mode_shapes() →
            write_results_npz() →
            read_results_npz() →
            npz_to_pyvista_modal_mesh()
        """
        md = analysed_builder.sap_model_data
        modal_result = analysed_builder.run_modal_analysis(num_modes=3)
        mode_shapes = analysed_builder.extract_mode_shapes(num_modes=3)

        from fea_toolkit.io.npz_reader import (
            npz_to_pyvista_modal_mesh,
            read_results_npz,
        )
        from fea_toolkit.io.npz_writer import write_results_npz

        npz_path = str(tmp_path / "test_unified_modal.npz")
        write_results_npz(npz_path, md, modal_result=modal_result, mode_shapes=mode_shapes)

        data = read_results_npz(npz_path)
        assert "modal/period" in data
        assert len(data["modal/period"]) == 3
        assert "modal/mode_dx" in data
        assert "modal/mode_dy" in data
        assert "modal/mode_dz" in data
        assert "modal/mx_ratio" in data
        assert "modal/my_ratio" in data
        assert "modal/mz_ratio" in data

        # --- Adapter: PyVista modal mesh ---
        f_pts, f_lines, _s_pts, _s_faces = npz_to_pyvista_modal_mesh(
            data,
            mode_idx=0,
            scale=10.0,
        )
        assert f_pts.shape[0] > 0
        assert f_lines.shape[0] > 0

    def test_write_and_read_static_with_split(self, sample_md, tmp_path):
        """Unified NPZ pipeline works with split_elements=True.

        The built-in cantilever has no intermediate joints so no child
        elements are produced, but the NPZ round-trip still preserves
        frame_parent_sap_id metadata (all entries are None for the
        simple model).

        Exercises:
            Preprocessor.run() →
            AnalysisBuilder.build_domain() →
            write_results_npz() →
            read_results_npz() →
            npz_build_child_map() / npz_build_parent_map()
        """
        from fea_toolkit.io.npz_reader import (
            npz_build_child_map,
            npz_build_parent_map,
            read_results_npz,
        )
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
        mm = preprocess_model(sample_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        try:
            b.build_domain()
            b.create_loads()
            static_result = b.run_static_analysis()
        finally:
            ops.wipe()

        npz_path = str(tmp_path / "test_unified_split.npz")
        write_results_npz(npz_path, sample_md, static_results={"Static": static_result})

        data = read_results_npz(npz_path)
        assert "frame_parent_sap_id" in data
        assert len(data["frame_parent_sap_id"]) == len(data["frame_sap_id"])

        child_map = npz_build_child_map(data)
        parent_map = npz_build_parent_map(data)
        assert isinstance(child_map, dict)
        assert isinstance(parent_map, dict)

    def test_read_rhino_colour_adapter(self, analysed_builder, tmp_path):
        """Rhino colour data adapter works from unified NPZ.

        Exercises:
            write_results_npz() →
            read_results_npz() →
            npz_to_rhino_colour_data()
        """
        md = analysed_builder.sap_model_data
        static_result = analysed_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        # Convert element_forces from per-element dict to per-array format
        # that _collect_static() expects: {"fx_i": [...], "fy_i": [...], ...}
        ef_by_tag = analysed_builder.extract_static_element_forces()
        if ef_by_tag:
            elem_forces_arr: dict = {}
            force_keys_lower = [
                "fx_i",
                "fy_i",
                "fz_i",
                "mx_i",
                "my_i",
                "mz_i",
                "fx_j",
                "fy_j",
                "fz_j",
                "mx_j",
                "my_j",
                "mz_j",
            ]
            upper_to_lower = {
                "Fx": "fx_i",
                "Fy": "fy_i",
                "Fz": "fz_i",
                "Mx": "mx_i",
                "My": "my_i",
                "Mz": "mz_i",
                "Fx_j": "fx_j",
                "Fy_j": "fy_j",
                "Fz_j": "fz_j",
                "Mx_j": "mx_j",
                "My_j": "my_j",
                "Mz_j": "mz_j",
            }
            for key in force_keys_lower:
                elem_forces_arr[key] = []
            for tag, fdict in ef_by_tag.items():
                for upper_key, lower_key in upper_to_lower.items():
                    elem_forces_arr[lower_key].append(fdict.get(upper_key, 0.0))
            static_result["element_forces"] = elem_forces_arr
        from fea_toolkit.io.npz_reader import (
            npz_to_rhino_colour_data,
            read_results_npz,
        )
        from fea_toolkit.io.npz_writer import write_results_npz

        npz_path = str(tmp_path / "test_rhino_colour.npz")
        write_results_npz(npz_path, md, static_results={"DEAD": static_result})

        data = read_results_npz(npz_path)
        colour_data = npz_to_rhino_colour_data(data, quantity="fx_i", case="DEAD")
        assert isinstance(colour_data, dict)
        assert len(colour_data) > 0
        # Values should be floats
        for sap_id, val in colour_data.items():
            assert isinstance(sap_id, str)
            assert isinstance(val, float)

    def test_read_id_tag_map(self, analysed_builder, tmp_path):
        """ID-to-tag mapping adapter works from unified NPZ."""
        md = analysed_builder.sap_model_data
        from fea_toolkit.io.npz_reader import (
            npz_build_id_tag_map,
            read_results_npz,
        )
        from fea_toolkit.io.npz_writer import write_results_npz

        npz_path = str(tmp_path / "test_id_map.npz")
        write_results_npz(npz_path, md)
        data = read_results_npz(npz_path)
        id_map = npz_build_id_tag_map(data)
        assert isinstance(id_map, dict)
        assert len(id_map) > 0
        # Keys are strings, values are ints
        for sid, tag in id_map.items():
            assert isinstance(sid, str)
            assert isinstance(tag, int)

    def test_metadata_arrays(self, analysed_builder, tmp_path):
        """Metadata (analysis_types, force_unit, length_unit, created)
        are present in the NPZ archive.
        """
        md = analysed_builder.sap_model_data
        static_result = analysed_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        from fea_toolkit.io.npz_reader import read_results_npz
        from fea_toolkit.io.npz_writer import write_results_npz

        npz_path = str(tmp_path / "test_meta.npz")
        write_results_npz(npz_path, md, static_results={"DEAD": static_result})

        data = read_results_npz(npz_path)
        assert "analysis_types" in data
        assert "static" in [str(t) for t in data["analysis_types"]]
        assert "force_unit" in data
        assert "length_unit" in data
        assert "created" in data
        assert len(str(data["created"])) > 0


# ============================================================================
# Workflow: CSM (Capacity Spectrum Method)
# ============================================================================


class TestCSMWorkflow:
    """End-to-end capacity spectrum method (pushover + ADRS + performance point)."""

    @pytest.fixture
    def spectrum(self):
        """Simple elastic design spectrum."""
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 6.0]
        accels = [0.5, 1.5, 1.5, 1.5, 0.75, 0.375, 0.25, 0.125]
        return periods, accels

    def test_pushover_to_adrs(self, sample_ab):
        """Pushover curve can be converted to ADRS format."""
        sample_ab.build_domain()
        sample_ab.compute_seismic_masses()
        modal = sample_ab.run_modal_analysis(num_modes=3)
        shapes = sample_ab.extract_mode_shapes(3)
        results = sample_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        adrs = sample_ab.pushover_to_adrs(
            results,
            modal,
            shapes,
            direction="X",
        )
        assert isinstance(adrs, dict)
        assert "S_a" in adrs
        assert "S_d" in adrs
        assert "Gamma" in adrs
        assert "M_eff" in adrs
        assert "phi_control" in adrs
        assert len(adrs["S_a"]) > 0
        assert len(adrs["S_d"]) > 0
        assert adrs["Gamma"] > 1e-6
        assert adrs["M_eff"] > 1e-6

    def test_compute_performance_point(self, sample_rc_ab, spectrum):
        """CSM performance point can be computed from a nonlinear pushover.

        Uses the built-in single-storey RC moment frame
        (:func:`make_rc_frame_model`), so the pushover response
        actually yields — giving a proper bilinear capacity curve and a
        converged ATC-40 performance point with meaningful ductility.

        The demand spectrum is scaled by a factor derived from the
        frame's own bilinear capacity: ``S_ay * MARGIN / max(accels)``.
        This ensures the elastic demand plateau exceeds the frame's
        yield acceleration by a fixed 20 % margin, while remaining
        robust to model changes (the hard-coded 13.0 multiplier would
        silently break if the frame's S_ay changed).  With the base
        1.0× spectrum the performance point sits below yield (mu = 1.0),
        which would not exercise the inelastic branch of the CSM.
        """
        sample_rc_ab.build_domain()
        sample_rc_ab.compute_seismic_masses()
        modal = sample_rc_ab.run_modal_analysis(num_modes=3)
        shapes = sample_rc_ab.extract_mode_shapes(3)
        results = sample_rc_ab.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type="uniform",
            lateral_direction="X",
            control_node_tag=4,
            max_disp=0.3,
            num_steps=50,
            print_progress=False,
        )
        periods, accels = spectrum

        # ── Derive the yield capacity (S_ay) from the pushover curve ──
        # Convert the capacity curve to ADRS and bilinearize with the
        # same composite method used inside the performance-point
        # solver, so the scale factor tracks the actual frame capacity.
        from fea_toolkit.model.csm import bilinearize_composite

        adrs = sample_rc_ab.pushover_to_adrs(
            results,
            modal,
            shapes,
            direction="X",
        )
        _S_dy, S_ay, _ = bilinearize_composite(
            np.asarray(adrs["S_d"], dtype=float),
            np.asarray(adrs["S_a"], dtype=float),
        )
        assert S_ay > 1e-6, (
            f"Degenerate yield acceleration S_ay={S_ay:.3g} — "
            "the capacity curve did not bilinearize properly"
        )

        # Scale demand spectrum so the frame yields: the elastic
        # plateau must exceed S_ay by a fixed 20 % margin.
        MARGIN = 1.2
        scale = (S_ay * MARGIN) / max(accels)
        accels = [a * scale for a in accels]
        # Use CSM defaults (max_iter=50, tol=0.01) — do not loosen.
        pp = sample_rc_ab.compute_performance_point(
            results,
            modal,
            shapes,
            periods,
            accels,
            direction="X",
            damping_ratio=0.05,
        )
        # ── Unit system propagated through the pipeline ────────────
        assert results["units"] == {"F": "KN", "L": "m", "T": "C"}, (
            f"Pushover units not kN-m: {results['units']}"
        )

        # ── Non-trivial performance point ──────────────────────────
        assert pp["S_dp"] > 1e-6, f"S_dp degenerate: {pp['S_dp']}"
        assert pp["S_ap"] > 1e-6, f"S_ap degenerate: {pp['S_ap']}"

        # ── RC frame must yield — elastic response is a test failure ──
        assert pp["mu"] > 1.05, (
            f"RC frame did not yield in CSM (mu={pp['mu']:.3f}) — "
            "the capacity curve or ADRS conversion is degenerate"
        )

        # ── CSM iteration must converge with default tolerances ────
        # --- Hysteretic damping must be exercised (mu > 1) ---
        assert pp["beta_eq"] > 0.05, f"beta_eq not elevated by hysteresis: {pp['beta_eq']}"
        assert pp["B"] > 1.0, f"damping reduction factor B not > 1: {pp['B']}"

        assert pp["converged"] is True, f"CSM did not converge (iterations={pp['iterations']})"

        # ── Effective modal mass must be a plausible fraction of the
        #    physical frame mass (~11.06 t) — not the old degenerate
        #    value of 1.0 nor a bloated value from SI-unit leakage. ──
        assert pp["M_eff"] > 1.0, f"M_eff degenerate ({pp['M_eff']}) — ADRS missed nodal masses"
        assert pp["M_eff"] < 50.0, f"M_eff implausibly large ({pp['M_eff']} t) for a 1-bay frame"

        # ── Base shear sanity: kN range (not mega-Newtons from the
        #    old SI-unit ISection model which gave ~4917 t mass). ──
        first_nz = next(
            (abs(s) for s in results["base_shear"] if abs(s) > 1e-9),
            0.0,
        )
        assert 0.0 < first_nz < 1000.0, (
            f"First non-zero base shear out of kN range: {first_nz:.2f} kN"
        )


# ============================================================================
# Workflow: Euler buckling check
# ============================================================================


class TestBucklingCheckWorkflow:
    """End-to-end Euler buckling check (analytical, no OpenSees needed)."""

    def test_check_brace_buckling_no_braces(self, sample_md):
        """Buckling check with no brace selection returns empty."""
        from fea_toolkit.model.checks import check_brace_buckling

        result = check_brace_buckling(
            sample_md,
            brace_ids=set(),
            print_results=False,
        )
        assert isinstance(result, dict)
        assert len(result) == 0, f"expected empty dict, got {len(result)} entries"

    def test_check_brace_buckling_with_ids(self, sample_md):
        """Euler buckling load is computed for a given frame element."""
        from fea_toolkit.model.checks import check_brace_buckling

        result = check_brace_buckling(
            sample_md,
            brace_ids={"1"},
            print_results=False,
        )
        assert isinstance(result, dict)
        assert "1" in result, "brace '1' missing from results"
        r = result["1"]
        assert "P_cr" in r
        assert r["P_cr"] > 1e-6


# ============================================================================
# Workflow: Shell subdivision
# ============================================================================


class TestShellSubdivision:
    """Shell subdivision at the model-data level (N×N refinement)."""

    @pytest.fixture
    def sample_shell_md(self):
        """Model with a simple 4-node slab/area element plus frame support.

        The slab is a 4×4 m quad at z=0 with a column at each corner
        supporting it.  This exercises the full shell subdivision → frame
        splitting → analysis pipeline.
        """
        from fea_toolkit.model.sap_data import (
            AreaElement,
            FrameElement,
            LoadPattern,
            MassSource,
            Material,
            Node,
            Restraint,
            SAPModelData,
            Section,
        )

        n = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=4.0, y=4.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=4.0, z=0.0),
            "5": Node(node_id="5", node_tag=5, x=0.0, y=0.0, z=3.0),
            "6": Node(node_id="6", node_tag=6, x=4.0, y=0.0, z=3.0),
            "7": Node(node_id="7", node_tag=7, x=4.0, y=4.0, z=3.0),
            "8": Node(node_id="8", node_tag=8, x=0.0, y=4.0, z=3.0),
        }
        r = {nid: Restraint([1, 1, 1, 1, 1, 1]) for nid in ["1", "2", "3", "4"]}
        m = {
            "Concrete": Material(
                name="Concrete",
                type="Concrete",
                E_mod=2.5e10,
                G_mod=1.0e10,
                nu=0.2,
                unit_weight=2.4e4,
            )
        }
        s = {
            "Column": Section(
                name="Column",
                shape="Rectangular",
                material="Concrete",
                A=0.25,
                I33=0.005208,
                I22=0.005208,
                J=0.001,
            ),
            "Slab": Section(
                name="Slab",
                shape="Shell",
                material="Concrete",
                A=0.0,
                I33=0.0,
                I22=0.0,
                J=0.0,
            ),
        }
        fe = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="5"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="6"),
            "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="7"),
            "4": FrameElement(elem_id="4", elem_tag=4, node_i="4", node_j="8"),
        }
        fa = dict.fromkeys(fe, "Column")

        ae = {
            "S1": AreaElement(
                area_id="S1",
                area_tag=10,
                node_ids=["5", "6", "7", "8"],
                thickness=0.2,
            ),
        }
        aa = {"S1": "Slab"}

        lp = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=1),
        }

        ms = {"MSSSRC1": MassSource(name="MSSSRC1", elements=True, masses=False, loads=False)}

        return SAPModelData(
            nodes=n,
            restraints=r,
            materials=m,
            sections=s,
            frame_elements=fe,
            area_elements=ae,
            frame_assignments=fa,
            area_assignments=aa,
            groups={},
            frame_auto_mesh={},
            load_patterns=lp,
            mass_sources=ms,
        )

    def test_subdivide_area_mesh_function(self):
        """subdivide_area_mesh() creates N² sub-elements in model data."""
        from fea_toolkit.model.geometry import subdivide_area_mesh
        from fea_toolkit.model.sap_data import (
            AreaElement as _AreaElement,
        )
        from fea_toolkit.model.sap_data import (
            Node as _Node,
        )

        # Create a simple quad
        nodes = {
            "n1": _Node(node_id="n1", node_tag=1, x=0, y=0, z=0),
            "n2": _Node(node_id="n2", node_tag=2, x=4, y=0, z=0),
            "n3": _Node(node_id="n3", node_tag=3, x=4, y=4, z=0),
            "n4": _Node(node_id="n4", node_tag=4, x=0, y=4, z=0),
        }
        areas = {
            "A1": _AreaElement(
                area_id="A1", area_tag=10, node_ids=["n1", "n2", "n3", "n4"], thickness=0.2
            ),
        }
        assignments = {"A1": "Slab"}

        # Snapshot node keys before calling (function modifies in-place)
        pre_keys = set(nodes.keys())

        result_areas, _result_assign, result_nodes, _ = subdivide_area_mesh(
            areas,
            assignments,
            nodes,
            n=2,
        )

        # Parent should be inactive with 4 children
        assert result_areas["A1"].inactive is True
        assert len(result_areas["A1"].child_ids) == 4  # 2×2 = 4

        # Sub-elements should exist
        for j in range(2):
            for i in range(2):
                sub_id = f"A1_sub_{j}_{i}"
                assert sub_id in result_areas
                assert not result_areas[sub_id].inactive
                assert result_areas[sub_id].parent_id == "A1"
                assert len(result_areas[sub_id].node_ids) == 4
                # All referenced nodes should exist
                for nid in result_areas[sub_id].node_ids:
                    assert nid in result_nodes

        # New interior nodes created (function modifies dict in-place)
        new_nodes = {k: v for k, v in result_nodes.items() if k not in pre_keys}
        assert len(new_nodes) == 5  # 3×3 grid - 4 corners = 5 interior nodes

    def test_subdivide_3x3(self):
        """3×3 subdivision creates 9 sub-elements."""
        from fea_toolkit.model.geometry import subdivide_area_mesh
        from fea_toolkit.model.sap_data import (
            AreaElement as _AreaElement,
        )
        from fea_toolkit.model.sap_data import (
            Node as _Node,
        )

        nodes = {
            "n1": _Node(node_id="n1", node_tag=1, x=0, y=0, z=0),
            "n2": _Node(node_id="n2", node_tag=2, x=6, y=0, z=0),
            "n3": _Node(node_id="n3", node_tag=3, x=6, y=6, z=0),
            "n4": _Node(node_id="n4", node_tag=4, x=0, y=6, z=0),
        }
        areas = {
            "A1": _AreaElement(
                area_id="A1", area_tag=10, node_ids=["n1", "n2", "n3", "n4"], thickness=0.2
            ),
        }
        assignments = {"A1": "Slab"}

        pre_keys = set(nodes.keys())
        result_areas, _, result_nodes, _ = subdivide_area_mesh(
            areas,
            assignments,
            nodes,
            n=3,
        )

        assert len(result_areas["A1"].child_ids) == 9  # 3×3 = 9
        new_nodes = {k: v for k, v in result_nodes.items() if k not in pre_keys}
        # 4×4 grid - 4 corners = 12 interior nodes
        assert len(new_nodes) == 12

    def test_subdivide_with_selection(self):
        """Selection limits which areas are subdivided."""
        from fea_toolkit.model.geometry import subdivide_area_mesh
        from fea_toolkit.model.sap_data import (
            AreaElement as _AreaElement,
        )
        from fea_toolkit.model.sap_data import (
            Node as _Node,
        )

        nodes = {
            "n1": _Node(node_id="n1", node_tag=1, x=0, y=0, z=0),
            "n2": _Node(node_id="n2", node_tag=2, x=4, y=0, z=0),
            "n3": _Node(node_id="n3", node_tag=3, x=4, y=4, z=0),
            "n4": _Node(node_id="n4", node_tag=4, x=0, y=4, z=0),
            "n5": _Node(node_id="n5", node_tag=5, x=0, y=0, z=4),
            "n6": _Node(node_id="n6", node_tag=6, x=4, y=0, z=4),
            "n7": _Node(node_id="n7", node_tag=7, x=4, y=4, z=4),
            "n8": _Node(node_id="n8", node_tag=8, x=0, y=4, z=4),
        }
        areas = {
            "A1": _AreaElement(
                area_id="A1", area_tag=10, node_ids=["n1", "n2", "n3", "n4"], thickness=0.2
            ),
            "A2": _AreaElement(
                area_id="A2", area_tag=11, node_ids=["n5", "n6", "n7", "n8"], thickness=0.2
            ),
        }
        assignments = {"A1": "Slab", "A2": "Slab"}

        # Only subdivide A1
        result_areas, _, _, _ = subdivide_area_mesh(
            areas,
            assignments,
            nodes,
            n=2,
            selection={"A1"},
        )

        assert result_areas["A1"].inactive is True
        assert len(result_areas["A1"].child_ids) == 4
        # A2 should be unchanged
        assert result_areas["A2"].inactive is False
        assert len(result_areas["A2"].child_ids) == 0

    def test_shell_subdivision_in_builder(self, sample_shell_md):
        """Shell subdivision via Preprocessor creates sub-elements
        and the build/analysis completes successfully.
        """
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {
            "element_type": "elasticBeamColumn",
            "split_elements": False,
            "verbose": False,
            "create_shells": True,
            "subdivide_shells": 2,
        }
        mm = preprocess_model(sample_shell_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        b.build_domain()

        # Parent area should be inactive with children
        parent = mm.area_elements.get("S1")
        assert parent is not None
        assert parent.inactive is True
        assert len(parent.child_ids) == 4

        # Sub-elements should exist and be active
        for j in range(2):
            for i in range(2):
                sub_id = f"S1_sub_{j}_{i}"
                sub = mm.area_elements.get(sub_id)
                assert sub is not None, f"Missing sub-element {sub_id}"
                assert not sub.inactive
                assert sub.parent_id == "S1"

        # Frame elements (stored in MeshModel)
        total_frames = len(mm.frame_elements)
        # Original 4 columns → should now have more after subdivision splitting
        assert total_frames >= 4

        # Build should have completed — try static analysis
        b.compute_seismic_masses()
        results = b.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        assert "nodal_displacements" in results

        # For a slab under self-weight, some nodes should displace
        max_disp = max(
            sum(abs(d) for d in disp) for disp in results["nodal_displacements"].values()
        )
        assert max_disp > 1e-8, "Slab shows no displacement under self-weight"

        # Verify export includes the frame elements (4 columns).
        # In this test model columns meet the slab only at endpoints,
        # so no frame splitting at sub-nodes occurs.
        import tempfile

        npz_path = str(tempfile.mkstemp(suffix=".npz")[1])
        try:
            b.export_results(npz_path, static_results={"DEAD": results})
            import numpy as np

            with np.load(npz_path, allow_pickle=True) as data:
                # Unified export uses frame_sap_id
                assert "frame_sap_id" in data, f"Keys: {list(data.keys())}"
                sap_ids = list(data["frame_sap_id"])
                # All 4 original columns should be present
                assert len(sap_ids) >= 4, f"Expected ≥4 frame elements in NPZ, got {len(sap_ids)}"
        finally:
            import os

            if os.path.exists(npz_path):
                os.remove(npz_path)

        ops.wipe()

    def test_subdivision_npz_export(self, sample_shell_md, tmp_path):
        """Shell subdivision is visible via export (model-data level)."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {
            "element_type": "elasticBeamColumn",
            "split_elements": False,
            "verbose": False,
            "create_shells": True,
            "subdivide_shells": 2,
        }
        mm = preprocess_model(sample_shell_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        b.build_domain()
        try:
            results = b.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            npz_path = str(tmp_path / "test_subdiv.npz")
            b.export_results(npz_path, static_results={"DEAD": results})

            data = dict(np.load(npz_path, allow_pickle=True))
            # Unified export uses frame_sap_id / shell_* arrays
            assert "frame_sap_id" in data, f"Expected frame_sap_id, got keys: {list(data.keys())}"
            assert "shell_sap_id" in data or "shell_eid" in data, (
                f"Missing shell arrays. Keys: {list(data.keys())}"
            )
            # At least the original 4 frame elements exist
            assert len(data["frame_sap_id"]) >= 4
        finally:
            ops.wipe()

    def test_unified_npz_includes_subdivided_shells(self, sample_shell_md, tmp_path):
        """Unified NPZ export includes subdivided shells as shell_* arrays."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {
            "element_type": "elasticBeamColumn",
            "split_elements": False,
            "verbose": False,
            "create_shells": True,
            "subdivide_shells": 2,
        }
        mm = preprocess_model(sample_shell_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        b.build_domain()
        try:
            results = b.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            npz_path = str(tmp_path / "test_unified_subdiv.npz")
            b.export_results(npz_path, static_results={"DEAD": results})

            import numpy as np

            data = dict(np.load(npz_path, allow_pickle=True))
            # Unified format: shell_eid should contain sub-elements
            assert "shell_eid" in data, f"shell_eid missing. Keys: {list(data.keys())}"
            assert len(data["shell_eid"]) > 0, "shell_eid is empty"

            # shell_sap_id should contain the sub-element IDs
            assert "shell_sap_id" in data
            shell_ids = list(data["shell_sap_id"])
            sub_ids = [sid for sid in shell_ids if "_sub_" in str(sid)]
            assert len(sub_ids) == 4, (
                f"Expected 4 subdivided shells in NPZ, got {len(sub_ids)}: {sub_ids}"
            )
            # Original parent should not appear as active shell
            assert "S1" not in shell_ids, "Inactive parent area should not appear in shell_sap_id"
        finally:
            ops.wipe()

    def test_extract_static_shell_forces(self, sample_shell_md):
        """extract_static_shell_forces() returns local stress resultants.

        After running a static analysis on a model with shell elements,
        the method returns per-element membrane forces (fx, fy, fxy)
        and bending moments (mx, my, mxy).
        """
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        from fea_toolkit.opensees.preprocessor import preprocess_model

        cfg = {
            "element_type": "elasticBeamColumn",
            "split_elements": False,
            "verbose": False,
            "create_shells": True,
        }
        mm = preprocess_model(sample_shell_md, cfg)
        b = AnalysisBuilder(mm, cfg)
        b.build_domain()
        try:
            results = b.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            # Run BEFORE building and analysis (no analysis yet)
            # Actually need to run after analysis — the results dict
            # confirms the analysis completed.
            assert results is not None

            shell_forces = b.extract_static_shell_forces()
            # With a single slab area (S1) that is active, we expect
            # at least one shell element whose parent is S1
            assert isinstance(shell_forces, dict)
            assert len(shell_forces) > 0, (
                f"Expected shell forces, got empty dict. shell_tag_map={b._shell_tag_map}"
            )

            for aid, f in shell_forces.items():
                assert isinstance(aid, str)
                assert f["elem_tag"] > 0
                assert isinstance(f["fx"], float)
                assert isinstance(f["fy"], float)
                assert isinstance(f["fxy"], float)
                assert isinstance(f["mx"], float)
                assert isinstance(f["my"], float)
                assert isinstance(f["mxy"], float)
                # Node tags should be present
                assert len(f["node_tags"]) >= 3
                # Sec name should be non-empty
                assert isinstance(f["sec_name"], str)

            # Verify the shell_tag_map has the expected area ID
            assert "S1" in b._shell_tag_map or any("_sub_" in k for k in b._shell_tag_map), (
                "No S1 or subdivided shell tag in _shell_tag_map"
            )
        finally:
            ops.wipe()


# ============================================================================
# HDF5 reader round-trip
# ============================================================================


class TestHdf5RoundTrip:
    """Verify HDF5 write → read produces the same data as NPZ."""

    def setup_method(self):
        pytest.importorskip("h5py")

    def test_write_hdf5_and_read_back(self, tmp_path):
        """Write a minimal schema dict to HDF5 and read it back.

        Exercises:
            unified_writer._write_h5() →
            npz_reader.read_results_hdf5()
        """
        from fea_toolkit.io.npz_reader import read_results_hdf5
        from fea_toolkit.io.unified_writer import _write_h5

        # Minimal dict matching the NPZ schema
        arrays = {
            "node_tag": np.array([1, 2, 3], dtype=int),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1], dtype=int),
            "frame_sap_id": np.array(["1", "2"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_node_i": np.array([1, 2], dtype=int),
            "frame_node_j": np.array([2, 3], dtype=int),
            "frame_t_start": np.array([0.0, 0.0]),
            "frame_t_end": np.array([1.0, 1.0]),
            "static_case_labels": np.array(["DEAD"]),
            "static/DEAD/fx_i": np.array([0.0, 0.0]),
            "static/DEAD/node_dx": np.array([0.0, 0.05, 0.08]),
            "static/DEAD/node_dy": np.array([0.0, 0.0, 0.02]),
            "static/DEAD/node_dz": np.array([0.0, 0.0, 0.0]),
        }

        h5_path = str(tmp_path / "test_roundtrip.h5")
        _write_h5(h5_path, arrays)

        # Read back
        data = read_results_hdf5(h5_path)

        # Every key should be present
        for key, expected in arrays.items():
            assert key in data, f"Missing key: {key}"
            actual = data[key]
            assert np.array_equal(actual, expected), (
                f"Mismatch for {key}: expected {expected}, got {actual}"
            )

        # String arrays should read back as numpy arrays
        assert isinstance(data["frame_sec_name"], np.ndarray)
        assert list(data["frame_sec_name"]) == ["COL", "BEAM"]

    def test_read_results_dispatcher(self, tmp_path):
        """The read_results() dispatcher auto-detects NPZ vs HDF5."""
        from fea_toolkit.io.npz_reader import read_results
        from fea_toolkit.io.unified_writer import _write_h5

        arrays = {
            "node_tag": np.array([1, 2], dtype=int),
            "node_sap_id": np.array(["1", "2"]),
            "node_x": np.array([0.0, 4.0]),
            "node_y": np.array([0.0, 0.0]),
            "node_z": np.array([0.0, 0.0]),
        }

        # Write HDF5
        h5_path = str(tmp_path / "test_dispatch.h5")
        _write_h5(h5_path, arrays)
        data_h5 = read_results(h5_path)
        assert "node_tag" in data_h5
        assert list(data_h5["node_tag"]) == [1, 2]

        # Write NPZ
        npz_path = str(tmp_path / "test_dispatch.npz")
        np.savez_compressed(npz_path, **arrays)
        data_npz = read_results(npz_path)
        assert "node_tag" in data_npz
        assert list(data_npz["node_tag"]) == [1, 2]

    def test_read_results_hdf5_dict_compatible(self, tmp_path):
        """Dict from read_results_hdf5 can be used by _resolve_mesh_data."""
        from fea_toolkit.io.npz_reader import read_results_hdf5
        from fea_toolkit.io.unified_writer import _write_h5
        from fea_toolkit.plotting.viz import _resolve_mesh_data

        arrays = {
            "node_tag": np.array([1, 2, 3], dtype=int),
            "node_sap_id": np.array(["1", "2", "3"]),
            "node_x": np.array([0.0, 4.0, 4.0]),
            "node_y": np.array([0.0, 0.0, 0.0]),
            "node_z": np.array([0.0, 0.0, 3.0]),
            "frame_eid": np.array([0, 1], dtype=int),
            "frame_sap_id": np.array(["1", "2"]),
            "frame_parent_sap_id": np.array(["", ""]),
            "frame_sec_name": np.array(["COL", "BEAM"]),
            "frame_node_i": np.array([1, 2], dtype=int),
            "frame_node_j": np.array([2, 3], dtype=int),
            "frame_t_start": np.array([0.0, 0.0]),
            "frame_t_end": np.array([1.0, 1.0]),
        }

        h5_path = str(tmp_path / "test_compat.h5")
        _write_h5(h5_path, arrays)
        data = read_results_hdf5(h5_path)

        # _resolve_mesh_data should accept it
        # Note: _resolve_mesh_data stores each node under two keys
        # (SAP ID string + node tag int) for fast lookup, so 3 nodes
        # produce 6 dict entries.
        resolved = _resolve_mesh_data(data)
        assert len(resolved["nodes"]) == 6
        # Verify dual-key contract: each node is addressable by both
        # its SAP-ID string and its integer tag to the same entry.
        for i in range(len(arrays["node_tag"])):
            tag = int(arrays["node_tag"][i])
            sid = str(arrays["node_sap_id"][i])
            assert tag in resolved["nodes"], f"node tag {tag} not in resolved nodes"
            assert sid in resolved["nodes"], f"node sap_id {sid} not in resolved nodes"
            assert resolved["nodes"][sid] is resolved["nodes"][tag], (
                f"entries for {sid} and {tag} are not the same object"
            )
        assert len(resolved["frames"]) == 2
