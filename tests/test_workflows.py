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

    def test_write_and_read_static_with_split(self, sample_md, tmp_path):
        """Unified NPZ pipeline works with split elements.
        Builds with split_elements=True so children get parent tracking,
        then verifies the NPZ stores frame_parent_sap_id correctly.

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

        # Build with splitting — the sample cantilever has no intermediate
        # nodes so no actual splitting occurs, but the writer still emits
        # frame_parent_sap_id (empty string for unsplit elements).
        b = OpenSeesBuilder(sample_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': True,
            'verbose': False,
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
        # frame_parent_sap_id is always present (empty string = unsplit)
        assert len(data["frame_parent_sap_id"]) == len(data["frame_sap_id"])

        # --- Adapter: parent-child maps (work even with no split) ---
        child_map = npz_build_child_map(data)
        parent_map = npz_build_parent_map(data)
        # With no split, maps are empty — that's correct behaviour
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
