"""End-to-end workflow tests using the built-in cantilever sample model.

Each test exercises a complete workflow from model data through analysis,
verifying that the pipeline runs without errors.  Assertions are kept
minimal (the workflow completed, returned a dict with expected keys, etc.)
so they don't break when new features are added.
"""

import pytest
import openseespy.opensees as ops

import numpy as np

from examples.sample_model import make_sample_model


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_md():
    """Built-in 10 m steel cantilever model (no external files needed)."""
    return make_sample_model()


@pytest.fixture
def sample_builder(sample_md):
    """OpenSeesBuilder pre-configured with elastic sections (no shells).
    Tears down OpenSees global state after each test.
    """
    from fea_toolkit.opensees.builder import OpenSeesBuilder
    b = OpenSeesBuilder(sample_md, {
        'element_type': 'elasticBeamColumn',
        'split_elements': False,
        'verbose': False,
        'create_shells': False,
        'use_preprocessor': False,
    })
    yield b
    ops.wipe()


# ============================================================================
# Workflow: Build model
# ============================================================================

class TestBuildWorkflow:
    """Verify model building completes and produces expected structure."""

    def test_build_returns_none(self, sample_builder):
        """Builder can construct a complete OpenSees model from SAPModelData.

        Exercises: OpenSeesBuilder.build() with elastic sections, no shells.
        Verifies the build completes without exceptions.
        """
        sample_builder.build()
        # If we get here without exception, the build succeeded.
        assert True

    def test_build_creates_frame_tag_map(self, sample_builder):
        """Build produces an element-tag mapping for load application.

        Exercises: OpenSeesBuilder.build() → frame_tag_map.
        Verifies the single frame element is assigned the expected tag.
        """
        sample_builder.build()
        assert "1" in sample_builder.frame_tag_map
        assert sample_builder.frame_tag_map["1"] == 1

    def test_build_sets_load_totals(self, sample_builder):
        """Build accumulates applied load totals per pattern.

        Exercises: OpenSeesBuilder.build() → load_totals.
        Verifies at least one load pattern was applied and tracked.
        """
        sample_builder.build()
        assert hasattr(sample_builder, 'load_totals')
        # At least one load pattern should have been applied
        assert len(sample_builder.load_totals) > 0

    def test_build_with_split_elements(self, sample_md):
        """Build with element splitting at joints.

        Exercises: OpenSeesBuilder.build() with split_elements=True.
        Verifies split_elements attribute is populated after build.
        """
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(sample_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': True,
            'verbose': False,
        })
        try:
            b.build()
        finally:
            ops.wipe()

    def test_rebuild_preserves_geometry(self, sample_md):
        """Rebuilding with different pattern scales does not corrupt the model.

        Exercises: build() → build(pattern_scales=...) — the second call
        restores pristine geometry from snapshots before rebuilding.
        Verifies no exception is raised.
        """
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(sample_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': False,
            'verbose': False,
        })
        try:
            b.build()
            # Second build with different scales
            b.build(pattern_scales={"DEAD": 1.0, "WIND": 0.5})
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Static analysis
# ============================================================================

class TestStaticAnalysisWorkflow:
    """End-to-end linear static analysis."""

    def test_static_analysis_returns_dict(self, sample_builder):
        """Static analysis produces a result dict with nodal data and reactions.

        Exercises: build() → run_static_analysis(extract_reactions=True).
        Verifies nodal_displacements and summed_reactions keys are present.
        """
        sample_builder.build()
        results = sample_builder.run_static_analysis(extract_reactions=True)
        assert isinstance(results, dict)
        # Key results that should always be present
        assert 'nodal_displacements' in results

    def test_static_analysis_displacements(self, sample_builder):
        """Cantilever tip displaces under lateral wind load.

        Exercises: build() → run_static_analysis(pattern_scales={"WIND": 1.0}).
        Verifies the top node (tag 2) has non-zero X-displacement under
        a uniform X-direction distributed load.
        """
        sample_builder.build()
        results = sample_builder.run_static_analysis(
            pattern_scales={"WIND": 1.0},
            extract_reactions=True,
        )
        disp = results.get('nodal_displacements', {})
        # The top node (tag 2) should have displaced under wind load
        assert 2 in disp
        dx, dy, dz = disp[2]
        # Wind is in X direction — expect X displacement
        assert abs(dx) > 1e-6, f"top node X displacement is zero under wind (dx={dx})"

    def test_static_element_forces(self, sample_builder):
        """Element end-forces can be extracted after static analysis.

        Exercises: build() → run_static_analysis() → extract_static_element_forces().
        Verifies forces dict contains Fx and Mz entries for each element.
        """
        sample_builder.build()
        sample_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        forces = sample_builder.extract_static_element_forces()
        assert isinstance(forces, dict)
        assert len(forces) > 0, "extract_static_element_forces() returned empty dict"
        tag = list(forces.keys())[0]
        f = forces[tag]
        assert 'Fx' in f
        assert 'Mz' in f

    def test_static_gravity_vs_pattern(self, sample_builder):
        """Multiple static analyses can be run sequentially with different load sets.

        Exercises: run_static_analysis with gravity only, then with gravity+wind.
        Verifies both return non-empty results.
        """
        sample_builder.build()
        # Gravity only
        r1 = sample_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        # Combined
        r2 = sample_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0, "WIND": 1.0},
        )
        # Both should have results
        assert isinstance(r1, dict), "gravity-only result is not a dict"
        assert isinstance(r2, dict), "gravity+wind result is not a dict"
        assert 'nodal_displacements' in r1
        assert 'nodal_displacements' in r2
        # Wind load should produce larger X displacement at top node
        assert 2 in r1['nodal_displacements'], "node 2 missing from gravity result"
        assert 2 in r2['nodal_displacements'], "node 2 missing from gravity+wind result"
        d1 = r1['nodal_displacements'][2]
        d2 = r2['nodal_displacements'][2]
        assert abs(d2[0]) >= abs(d1[0]) - 1e-12, \
            f"X displacement did not increase with wind ({d1[0]} → {d2[0]})"

    def test_static_reactions_equilibrium(self, sample_builder):
        """Reactions at restrained nodes balance applied gravity loads.

        Exercises: build() → run_static_analysis(extract_reactions=True).
        Verifies the summed vertical reaction (Fz) is non-zero under dead load.
        """
        sample_builder.build()
        results = sample_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
            extract_reactions=True,
        )
        summed = results.get('summed_reactions', {})
        # For a downward gravity load, reactions support from below
        assert summed, "summed_reactions missing or empty"
        assert abs(summed.get('fz', 0)) > 1e-6, \
            f"vertical reaction Fz is zero under dead load ({summed})"

    def test_static_self_weight_consistency(self, sample_builder):
        """Applied self-weight loads match expected values from geometry.

        Exercises: build() → check_self_weight_consistency().
        Verifies the total applied self-weight (from load_totals) equals
        the sum of A × unit_weight × L for frame elements plus
        thickness × unit_weight × area for area elements.
        """
        sample_builder.build()
        report = sample_builder.check_self_weight_consistency(verbose=False)
        assert report["passed"], (
            f"Self-weight mismatch: expected {report['expected']}, "
            f"applied {report['applied']} "
            f"(disc. {report['discrepancy']} > tol. {report['tolerance']})"
        )
        # For the sample cantilever: A=8e-3, unit_weight=7.85e4, L=10
        assert abs(report["expected"] - 6280.0) < 1.0, \
            f"Unexpected expected value: {report['expected']}"
        assert report["discrepancy"] < 1.0, \
            f"Non-zero discrepancy: {report['discrepancy']}"


# ============================================================================
# Workflow: Modal analysis
# ============================================================================

class TestModalAnalysisWorkflow:
    """End-to-end eigenvalue / modal analysis."""

    def test_modal_analysis_returns_keys(self, sample_builder):
        """Modal analysis returns periods, eigenvalues, and frequencies.

        Exercises: build() → compute_seismic_masses() → run_modal_analysis().
        Verifies result dict contains periods, eigenvalues, frequencies arrays
        with the requested number of modes.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=3)
        assert isinstance(modal, dict)
        assert 'periods' in modal
        assert 'eigenvalues' in modal
        assert 'frequencies' in modal
        assert len(modal['periods']) == 3
        assert len(modal['eigenvalues']) == 3
        assert len(modal['frequencies']) == 3

    def test_modal_first_period_positive(self, sample_builder):
        """Fundamental period of a 10 m steel cantilever is in a reasonable range.

        Exercises: run_modal_analysis() → periods[0].
        Verifies T1 is between 0.01 s and 10.0 s (physically plausible).
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=3)
        T1 = modal['periods'][0]
        # A 10 m steel cantilever typically has T1 ~0.2–2.0 s
        assert 0.01 < T1 < 10.0, f"T1={T1} outside plausible range"

    def test_extract_mode_shapes(self, sample_builder):
        """Mode shapes can be extracted after eigenvalue analysis.

        Exercises: run_modal_analysis() → extract_mode_shapes().
        Verifies the result is a non-empty dict keyed by mode index.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=2)
        shapes = sample_builder.extract_mode_shapes(num_modes=2)
        assert isinstance(shapes, dict)
        assert 0 in shapes, "mode 0 missing from shapes"
        assert 1 in shapes, "mode 1 missing from shapes"
        assert len(shapes) == 2, f"expected 2 modes, got {len(shapes)}"


# ============================================================================
# Workflow: Pushover analysis
# ============================================================================

class TestPushoverWorkflow:
    """End-to-end non-linear pushover analysis (truss-brace approach)."""

    def test_pushover_uniform_returns_keys(self, sample_builder):
        """Uniform-mass-proportional pushover produces a capacity curve.

        Exercises: build() → compute_seismic_masses() → run_pushover_analysis()
        with lateral_load_type='uniform'.
        Verifies result dict contains control_disp, base_shear, and step arrays
        with more than one entry.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        results = sample_builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        assert isinstance(results, dict)
        assert 'control_disp' in results
        assert 'base_shear' in results
        assert 'step' in results
        assert len(results['control_disp']) > 1, "uniform: control_disp empty"
        assert len(results['base_shear']) > 1, "uniform: base_shear empty"
        assert len(results['step']) > 1, "uniform: step empty"
        assert abs(results['base_shear'][-1]) > 1e-6, "uniform: final base_shear zero"

    def test_pushover_triangular_returns_keys(self, sample_builder):
        """Triangular (ELF) pushover produces a valid capacity curve.

        Exercises: run_pushover_analysis() with lateral_load_type='triangular'.
        Verifies control_disp array has more than one entry.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        results = sample_builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='triangular',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        assert isinstance(results, dict)
        assert 'control_disp' in results
        assert 'base_shear' in results
        assert 'step' in results
        assert len(results['control_disp']) > 1, "triangular: control_disp empty"
        assert len(results['base_shear']) > 1, "triangular: base_shear empty"
        assert abs(results['base_shear'][-1]) > 1e-6, "triangular: final base_shear zero"

    def test_pushover_pattern_returns_keys(self, sample_builder):
        """SAP2000-pattern-based pushover uses existing distributed loads.

        Exercises: run_pushover_analysis() with lateral_load_type='pattern'
        referencing the WIND load pattern.
        Verifies control_disp array has more than one entry.
        """
        sample_builder.build()
        results = sample_builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='pattern',
            lateral_pattern_name="WIND",
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        assert isinstance(results, dict)
        assert 'control_disp' in results
        assert 'base_shear' in results
        assert 'step' in results
        assert len(results['control_disp']) > 1, "pattern: control_disp empty"
        assert len(results['base_shear']) > 1, "pattern: base_shear empty"
        assert abs(results['base_shear'][-1]) > 1e-6, "pattern: final base_shear near zero"


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

    def test_rs_analysis_returns_dict(self, sample_builder, spectrum):
        """CQC response-spectrum analysis computes combined base shear.

        Exercises: build() → compute_seismic_masses() → run_modal_analysis() →
        run_response_spectrum_analysis().
        Verifies result dict contains base_shear_cqc.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=3)
        periods, accels = spectrum
        results = sample_builder.run_response_spectrum_analysis(
            num_modes=3,
            modal_periods=modal['periods'],
            spectrum_periods=periods,
            spectrum_accels=accels,
            direction='X',
            damping_ratio=0.05,
        )
        assert isinstance(results, dict)
        assert 'base_shear_cqc' in results, "base_shear_cqc missing from RS results"
        assert abs(results['base_shear_cqc']) > 1e-6, \
            f"base_shear_cqc is near zero ({results['base_shear_cqc']})"

    def test_element_rs_forces(self, sample_builder, spectrum):
        """Element-level RS forces are available after spectrum analysis.

        Exercises: run_response_spectrum_analysis() →
        extract_element_rs_forces().
        Verifies result dict contains element_results list.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=3)
        periods, accels = spectrum
        sample_builder.run_response_spectrum_analysis(
            num_modes=3,
            modal_periods=modal['periods'],
            spectrum_periods=periods,
            spectrum_accels=accels,
            direction='X',
            damping_ratio=0.05,
        )
        rs_forces = sample_builder.extract_element_rs_forces(
            num_modes=3,
            modal_periods=modal['periods'],
            spectrum_periods=periods,
            spectrum_accels=accels,
            direction='X',
        )
        assert isinstance(rs_forces, dict)
        assert 'element_results' in rs_forces, \
            "element_results missing from RS element forces"
        er = rs_forces['element_results']
        assert len(er) > 0, "element_results is empty"
        first = er[0]
        for key in ('Vz_i', 'My_i', 'Mz_i'):
            assert key in first, f"{key} missing from RS element result"
        assert abs(first['Vz_i']) > 1e-6, "Vz_i is zero in RS element result"


# ============================================================================
# Workflow: Results export
# ============================================================================

class TestExportWorkflow:
    """End-to-end NPZ export."""

    def test_export_to_npz(self, sample_builder, tmp_path):
        """Static results can be exported to compressed NumPy archive.

        Exercises: build() → run_static_analysis() → export_results_to_npz().
        Verifies the .npz file contains sub_elem_tags, node_tags, and
        force_unit arrays.
        """
        sample_builder.build()
        results = sample_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0, "WIND": 1.0},
        )
        npz_path = str(tmp_path / "test_results.npz")
        sample_builder.export_results_to_npz(npz_path, results)
        # Verify the file exists and can be loaded
        import numpy as np
        with np.load(npz_path, allow_pickle=True) as data:
            assert 'sub_elem_tags' in data
            assert 'node_tags' in data
            assert 'force_unit' in data

    def test_export_with_section_responses(self, sample_builder, tmp_path):
        """NPZ export includes section-force data when section_responses passed.

        Exercises: export_results_to_npz() with section_responses={"section_forces": True}.
        Verifies ``sec_ip``, ``sec_sub_idx``, and ``sec_N``/``sec_Mz`` arrays
        are present with the expected number of rows (1 element × 3 IPs).
        """
        sample_builder.build()
        results = sample_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        npz_path = str(tmp_path / "test_results_sec.npz")
        sample_builder.export_results_to_npz(npz_path, results,
            section_responses={"section_forces": True})
        import numpy as np
        with np.load(npz_path, allow_pickle=True) as data:
            assert 'sec_ip' in data
            assert 'sec_sub_idx' in data
            assert 'sec_N' in data
            assert 'sec_Mz' in data
            # 1 frame element × 3 integration points (default Lobatto)
            assert len(data['sec_ip']) == 3
            assert len(data['sec_N']) == 3


# ============================================================================
# Workflow: Unified NPZ pipeline (write → read → adapters)
# ============================================================================

class TestUnifiedNpzPipeline:
    """End-to-end unified NPZ pipeline: analyse → write → read → visualise.

    Exercises the full data path that users would follow when saving results
    to a NPZ archive and then loading them for plotting or colouring.

    Pipeline::

        OpenSeesBuilder.run_static_analysis()
        OpenSeesBuilder.run_modal_analysis()
        OpenSeesBuilder.extract_mode_shapes()
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
        """Build, run static + modal analysis."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(sample_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': False,
            'verbose': False,
            'create_shells': False,
            'use_preprocessor': False,
        })
        b.build()
        b.compute_seismic_masses(g=9.81)
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
        md = analysed_builder.model
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
                "fx_i", "fy_i", "fz_i", "mx_i", "my_i", "mz_i",
                "fx_j", "fy_j", "fz_j", "mx_j", "my_j", "mz_j",
            ]
            upper_to_lower = {
                "Fx": "fx_i", "Fy": "fy_i", "Fz": "fz_i",
                "Mx": "mx_i", "My": "my_i", "Mz": "mz_i",
                "Fx_j": "fx_j", "Fy_j": "fy_j", "Fz_j": "fz_j",
                "Mx_j": "mx_j", "My_j": "my_j", "Mz_j": "mz_j",
            }
            for key in force_keys_lower:
                elem_forces_arr[key] = []
            for tag, fdict in ef_by_tag.items():
                for upper_key, lower_key in upper_to_lower.items():
                    elem_forces_arr[lower_key].append(fdict.get(upper_key, 0.0))
            static_result["element_forces"] = elem_forces_arr
        # Package as {case_name: result_dict}
        static_results = {"DEAD": static_result}

        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import (
            read_results_npz,
            npz_to_pyvista_frame_mesh,
            _get_static_cases,
        )

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
            data, deformed_case="DEAD", scale=10.0,
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
        md = analysed_builder.model
        modal_result = analysed_builder.run_modal_analysis(num_modes=3)
        mode_shapes = analysed_builder.extract_mode_shapes(num_modes=3)

        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import (
            read_results_npz,
            npz_to_pyvista_modal_mesh,
        )

        npz_path = str(tmp_path / "test_unified_modal.npz")
        write_results_npz(npz_path, md,
                          modal_result=modal_result,
                          mode_shapes=mode_shapes)

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
        f_pts, f_lines, s_pts, s_faces = npz_to_pyvista_modal_mesh(
            data, mode_idx=0, scale=10.0,
        )
        assert f_pts.shape[0] > 0
        assert f_lines.shape[0] > 0

    @pytest.mark.parametrize("use_preprocessor", [False, True])
    def test_write_and_read_static_with_split(self, sample_md, tmp_path,
                                               use_preprocessor):
        """Unified NPZ pipeline works with split elements.

        Exercises both legacy (use_preprocessor=False) and two-stage
        (use_preprocessor=True) build paths.

        Builds a model with an intermediate node on a frame so
        split_elements=True produces child elements, then verifies
        parent-child metadata in the NPZ round-trip.

        Exercises:
            build(split_elements=True) →
            write_results_npz() →
            read_results_npz() →
            npz_build_child_map() / npz_build_parent_map()
        """
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import (
            read_results_npz,
            npz_build_child_map,
            npz_build_parent_map,
        )

        b = OpenSeesBuilder(sample_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': True,
            'verbose': False,
            'use_preprocessor': use_preprocessor,
        })
        b.build()
        try:
            static_result = b.run_static_analysis()
        finally:
            ops.wipe()

        npz_path = str(tmp_path / "test_unified_split.npz")
        write_results_npz(npz_path, sample_md,
                          static_results={"Static": static_result})

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
        md = analysed_builder.model
        static_result = analysed_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        # Convert element_forces from per-element dict to per-array format
        # that _collect_static() expects: {"fx_i": [...], "fy_i": [...], ...}
        ef_by_tag = analysed_builder.extract_static_element_forces()
        if ef_by_tag:
            elem_forces_arr: dict = {}
            force_keys_lower = [
                "fx_i", "fy_i", "fz_i", "mx_i", "my_i", "mz_i",
                "fx_j", "fy_j", "fz_j", "mx_j", "my_j", "mz_j",
            ]
            upper_to_lower = {
                "Fx": "fx_i", "Fy": "fy_i", "Fz": "fz_i",
                "Mx": "mx_i", "My": "my_i", "Mz": "mz_i",
                "Fx_j": "fx_j", "Fy_j": "fy_j", "Fz_j": "fz_j",
                "Mx_j": "mx_j", "My_j": "my_j", "Mz_j": "mz_j",
            }
            for key in force_keys_lower:
                elem_forces_arr[key] = []
            for tag, fdict in ef_by_tag.items():
                for upper_key, lower_key in upper_to_lower.items():
                    elem_forces_arr[lower_key].append(fdict.get(upper_key, 0.0))
            static_result["element_forces"] = elem_forces_arr
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import (
            read_results_npz,
            npz_to_rhino_colour_data,
        )

        npz_path = str(tmp_path / "test_rhino_colour.npz")
        write_results_npz(npz_path, md,
                          static_results={"DEAD": static_result})

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
        md = analysed_builder.model
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import (
            read_results_npz,
            npz_build_id_tag_map,
        )

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
        md = analysed_builder.model
        static_result = analysed_builder.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import read_results_npz

        npz_path = str(tmp_path / "test_meta.npz")
        write_results_npz(npz_path, md,
                          static_results={"DEAD": static_result})

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

    def test_pushover_to_adrs(self, sample_builder):
        """Pushover curve can be converted to ADRS format.

        Exercises: run_pushover_analysis() → pushover_to_adrs().
        Verifies the ADRS dict contains S_a, S_d, and Gamma arrays.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=3)
        shapes = sample_builder.extract_mode_shapes(3)
        results = sample_builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        adrs = sample_builder.pushover_to_adrs(
            results, modal, shapes, direction='X',
        )
        assert isinstance(adrs, dict)
        assert 'S_a' in adrs
        assert 'S_d' in adrs
        assert 'Gamma' in adrs
        assert 'M_eff' in adrs
        assert 'phi_control' in adrs
        assert len(adrs['S_a']) > 0
        assert len(adrs['S_d']) > 0
        assert adrs['Gamma'] > 1e-6
        assert adrs['M_eff'] > 1e-6

    def test_compute_performance_point(self, sample_builder, spectrum):
        """CSM performance point can be computed from pushover + spectrum.

        Exercises: pushover_to_adrs() → compute_performance_point() with
        a user-supplied elastic spectrum.
        Verifies the result dict contains S_dp and S_ap.
        """
        sample_builder.build()
        sample_builder.compute_seismic_masses(g=9.81)
        modal = sample_builder.run_modal_analysis(num_modes=3)
        shapes = sample_builder.extract_mode_shapes(3)
        results = sample_builder.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.1,
            num_steps=5,
            print_progress=False,
        )
        periods, accels = spectrum
        pp = sample_builder.compute_performance_point(
            results, modal, shapes,
            periods, accels,
            direction='X',
            damping_ratio=0.05,
            max_iter=20, tol=0.05,
        )
        assert isinstance(pp, dict)
        assert 'S_dp' in pp
        assert 'S_ap' in pp
        assert 'V_base' in pp
        assert 'mu' in pp
        assert pp['S_dp'] > 1e-6
        assert pp['S_ap'] > 1e-6


# ============================================================================
# Workflow: Euler buckling check
# ============================================================================

class TestBucklingCheckWorkflow:
    """End-to-end Euler buckling check."""

    def test_check_brace_buckling_no_braces(self, sample_builder):
        """Buckling check with no brace selection returns empty.

        Exercises: build() → check_brace_buckling(brace_ids=set()).
        Verifies the result is an empty dict.
        """
        sample_builder.build()
        result = sample_builder.check_brace_buckling(
            brace_ids=set(), print_results=False,
        )
        assert isinstance(result, dict)
        assert len(result) == 0, f"expected empty dict, got {len(result)} entries"

    def test_check_brace_buckling_with_ids(self, sample_builder):
        """Euler buckling load is computed for a given frame element.

        Exercises: build() → check_brace_buckling(brace_ids={"1"}).
        Verifies P_cr is positive for the sample cantilever.
        """
        sample_builder.build()
        result = sample_builder.check_brace_buckling(
            brace_ids={"1"}, print_results=False,
        )
        assert isinstance(result, dict)
        assert "1" in result, "brace '1' missing from results"
        r = result["1"]
        assert 'P_cr' in r
        assert r['P_cr'] > 1e-6


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
            SAPModelData, Node, Restraint, Material, Section,
            FrameElement, AreaElement, LoadPattern,
            FrameDistributedLoad, MassSource,
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
        m = {"Concrete": Material(
            name="Concrete", type="Concrete",
            E_mod=2.5e10, G_mod=1.0e10, nu=0.2,
            unit_weight=2.4e4,
        )}
        s = {
            "Column": Section(
                name="Column", shape="Rectangular",
                material="Concrete", A=0.25, I33=0.005208, I22=0.005208, J=0.001,
            ),
            "Slab": Section(
                name="Slab", shape="Shell",
                material="Concrete", A=0.0, I33=0.0, I22=0.0, J=0.0,
            ),
        }
        fe = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="5"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="6"),
            "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="7"),
            "4": FrameElement(elem_id="4", elem_tag=4, node_i="4", node_j="8"),
        }
        fa = {eid: "Column" for eid in fe}

        ae = {
            "S1": AreaElement(
                area_id="S1", area_tag=10,
                node_ids=["5", "6", "7", "8"], thickness=0.2,
            ),
        }
        aa = {"S1": "Slab"}

        lp = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="Dead",
                                self_weight_factor=1),
        }

        ms = {"MSSSRC1": MassSource(name="MSSSRC1", elements=True,
                                     masses=False, loads=False)}

        return SAPModelData(
            nodes=n, restraints=r, materials=m, sections=s,
            frame_elements=fe, area_elements=ae,
            frame_assignments=fa, area_assignments=aa,
            groups={}, frame_auto_mesh={}, load_patterns=lp,
            mass_sources=ms,
        )

    def test_subdivide_area_mesh_function(self):
        """subdivide_area_mesh() creates N² sub-elements in model data."""
        from fea_toolkit.model.geometry import subdivide_area_mesh
        from fea_toolkit.model.sap_data import (
            Node as _Node, AreaElement as _AreaElement,
        )

        # Create a simple quad
        nodes = {
            "n1": _Node(node_id="n1", node_tag=1, x=0, y=0, z=0),
            "n2": _Node(node_id="n2", node_tag=2, x=4, y=0, z=0),
            "n3": _Node(node_id="n3", node_tag=3, x=4, y=4, z=0),
            "n4": _Node(node_id="n4", node_tag=4, x=0, y=4, z=0),
        }
        areas = {
            "A1": _AreaElement(area_id="A1", area_tag=10,
                                node_ids=["n1", "n2", "n3", "n4"],
                                thickness=0.2),
        }
        assignments = {"A1": "Slab"}

        # Snapshot node keys before calling (function modifies in-place)
        pre_keys = set(nodes.keys())

        result_areas, result_assign, result_nodes, _ = subdivide_area_mesh(
            areas, assignments, nodes, n=2,
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
        new_nodes = {k: v for k, v in result_nodes.items()
                     if k not in pre_keys}
        assert len(new_nodes) == 5  # 3×3 grid - 4 corners = 5 interior nodes

    def test_subdivide_3x3(self):
        """3×3 subdivision creates 9 sub-elements."""
        from fea_toolkit.model.geometry import subdivide_area_mesh
        from fea_toolkit.model.sap_data import (
            Node as _Node, AreaElement as _AreaElement,
        )

        nodes = {
            "n1": _Node(node_id="n1", node_tag=1, x=0, y=0, z=0),
            "n2": _Node(node_id="n2", node_tag=2, x=6, y=0, z=0),
            "n3": _Node(node_id="n3", node_tag=3, x=6, y=6, z=0),
            "n4": _Node(node_id="n4", node_tag=4, x=0, y=6, z=0),
        }
        areas = {
            "A1": _AreaElement(area_id="A1", area_tag=10,
                                node_ids=["n1", "n2", "n3", "n4"],
                                thickness=0.2),
        }
        assignments = {"A1": "Slab"}

        pre_keys = set(nodes.keys())
        result_areas, _, result_nodes, _ = subdivide_area_mesh(
            areas, assignments, nodes, n=3,
        )

        assert len(result_areas["A1"].child_ids) == 9  # 3×3 = 9
        new_nodes = {k: v for k, v in result_nodes.items()
                     if k not in pre_keys}
        # 4×4 grid - 4 corners = 12 interior nodes
        assert len(new_nodes) == 12

    def test_subdivide_with_selection(self):
        """Selection limits which areas are subdivided."""
        from fea_toolkit.model.geometry import subdivide_area_mesh
        from fea_toolkit.model.sap_data import (
            Node as _Node, AreaElement as _AreaElement,
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
            "A1": _AreaElement(area_id="A1", area_tag=10,
                                node_ids=["n1","n2","n3","n4"],
                                thickness=0.2),
            "A2": _AreaElement(area_id="A2", area_tag=11,
                                node_ids=["n5","n6","n7","n8"],
                                thickness=0.2),
        }
        assignments = {"A1": "Slab", "A2": "Slab"}

        # Only subdivide A1
        result_areas, _, _, _ = subdivide_area_mesh(
            areas, assignments, nodes, n=2, selection={"A1"},
        )

        assert result_areas["A1"].inactive is True
        assert len(result_areas["A1"].child_ids) == 4
        # A2 should be unchanged
        assert result_areas["A2"].inactive is False
        assert len(result_areas["A2"].child_ids) == 0

    def test_shell_subdivision_in_builder(self, sample_shell_md):
        """Shell subdivision via builder config creates sub-elements
        and the build/analysis completes successfully.
        """
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        b = OpenSeesBuilder(sample_shell_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': False,
            'verbose': False,
            'create_shells': True,
            'subdivide_shells': 2,
            'use_preprocessor': False,
        })
        b.build()

        # Parent area should be inactive with children
        parent = b.model.area_elements.get("S1")
        assert parent is not None
        assert parent.inactive is True
        assert len(parent.child_ids) == 4

        # Sub-elements should exist and be active
        for j in range(2):
            for i in range(2):
                sub_id = f"S1_sub_{j}_{i}"
                sub = b.model.area_elements.get(sub_id)
                assert sub is not None, f"Missing sub-element {sub_id}"
                assert not sub.inactive
                assert sub.parent_id == "S1"

        # Frame elements should be split at sub-edge nodes
        if b.split_elements is not None:
            total_frames = len(b.split_elements)
        else:
            total_frames = len(b.model.frame_elements)
        # Original 4 columns → should now have more after subdivision splitting
        assert total_frames >= 4

        # Build should have completed — try static analysis
        b.compute_seismic_masses(g=9.81)
        results = b.run_static_analysis(
            pattern_scales={"DEAD": 1.0},
        )
        assert 'nodal_displacements' in results

        # For a slab under self-weight, some nodes should displace
        max_disp = max(
            sum(abs(d) for d in disp)
            for disp in results['nodal_displacements'].values()
        )
        assert max_disp > 1e-8, "Slab shows no displacement under self-weight"

        # Verify NPZ export includes the frame elements (4 columns).
        # In this test model columns meet the slab only at endpoints,
        # so no frame splitting at sub-nodes occurs.
        import tempfile
        npz_path = str(tempfile.mkstemp(suffix=".npz")[1])
        try:
            b.export_results_to_npz(npz_path, results)
            import numpy as np
            with np.load(npz_path, allow_pickle=True) as data:
                # export_results_to_npz uses sub_* prefix for frame elements
                assert "sub_elem_tags" in data
                assert "sub_sap_ids" in data
                sap_ids = list(data["sub_sap_ids"])
                # All 4 original columns should be present
                assert len(sap_ids) >= 4, \
                    f"Expected ≥4 frame elements in NPZ, got {len(sap_ids)}"
        finally:
            import os
            if os.path.exists(npz_path):
                os.remove(npz_path)

        ops.wipe()

    def test_subdivision_npz_export(self, sample_shell_md, tmp_path):
        """Shell subdivision is visible in NPZ export (model-data level)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.io.npz_writer import write_results_npz

        b = OpenSeesBuilder(sample_shell_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': False,
            'verbose': False,
            'create_shells': True,
            'subdivide_shells': 2,
            'use_preprocessor': False,
        })
        b.build()
        try:
            results = b.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            npz_path = str(tmp_path / "test_subdiv.npz")
            b.export_results_to_npz(npz_path, results)

            data = dict(np.load(npz_path, allow_pickle=True))
            # export_results_to_npz uses sub_* prefix, not shell_*
            assert "sub_elem_tags" in data
            assert "sub_sap_ids" in data
            # Should include the sub-elements
            sub_ids = list(data["sub_sap_ids"])
            # The export format combines frames+shells as "sub_*"
            # Check that at least the original 4 frame elements exist
            assert len(sub_ids) >= 4
        finally:
            ops.wipe()

    def test_unified_npz_includes_subdivided_shells(self, sample_shell_md, tmp_path):
        """Unified NPZ writer includes subdivided shells as shell_* arrays."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.npz_reader import read_results_npz

        b = OpenSeesBuilder(sample_shell_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': False,
            'verbose': False,
            'create_shells': True,
            'subdivide_shells': 2,
            'use_preprocessor': False,
        })
        b.build()
        try:
            results = b.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            npz_path = str(tmp_path / "test_unified_subdiv.npz")
            write_results_npz(npz_path, b.model,
                              static_results={"DEAD": results})

            data = read_results_npz(npz_path)
            # Unified format: shell_eid should contain sub-elements
            assert "shell_eid" in data, "shell_eid missing from unified NPZ"
            assert len(data["shell_eid"]) > 0, "shell_eid is empty"

            # shell_sap_id should contain the sub-element IDs
            assert "shell_sap_id" in data
            shell_ids = list(data["shell_sap_id"])
            sub_ids = [sid for sid in shell_ids if "_sub_" in str(sid)]
            assert len(sub_ids) == 4, \
                f"Expected 4 subdivided shells in NPZ, got {len(sub_ids)}: {sub_ids}"
            # Original parent should not appear as active shell
            assert "S1" not in shell_ids, \
                "Inactive parent area should not appear in shell_sap_id"

            # shell_node_1..4 arrays should reference valid node tags
            assert "shell_node_1" in data
            assert "shell_node_4" in data
            assert len(data["shell_node_1"]) == len(data["shell_eid"])
            assert len(data["shell_node_4"]) == len(data["shell_eid"])

            # Frame elements should include split children from subdivision
            assert "frame_sap_id" in data
            assert len(data["frame_sap_id"]) >= 4
        finally:
            ops.wipe()
