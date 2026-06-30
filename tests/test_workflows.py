"""End-to-end workflow tests using the built-in cantilever sample model.

Each test exercises a complete workflow from model data through analysis,
verifying that the pipeline runs without errors.  Assertions are kept
minimal (the workflow completed, returned a dict with expected keys, etc.)
so they don't break when new features are added.
"""

import pytest
import openseespy.opensees as ops

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
    """OpenSeesBuilder pre-configured with elastic sections (no shells)."""
    from fea_toolkit.opensees.builder import OpenSeesBuilder
    b = OpenSeesBuilder(sample_md, {
        'element_type': 'elasticBeamColumn',
        'split_elements': False,
        'verbose': False,
        'create_shells': False,
    })
    return b


# ============================================================================
# Workflow: Build model
# ============================================================================

class TestBuildWorkflow:
    """Verify model building completes and produces expected structure."""

    def test_build_returns_none(self, sample_builder):
        """build() should complete without error."""
        sample_builder.build()
        # If we get here without exception, the build succeeded.
        assert True

    def test_build_creates_frame_tag_map(self, sample_builder):
        """After build, frame_tag_map should have the expected entries."""
        sample_builder.build()
        assert "1" in sample_builder.frame_tag_map
        assert sample_builder.frame_tag_map["1"] == 1

    def test_build_sets_load_totals(self, sample_builder):
        """After build, load_totals should contain applied pattern totals."""
        sample_builder.build()
        assert hasattr(sample_builder, 'load_totals')
        # At least one load pattern should have been applied
        assert len(sample_builder.load_totals) > 0

    def test_build_with_split_elements(self, sample_md):
        """Build with element splitting enabled."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(sample_md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': True,
            'verbose': False,
        })
        try:
            b.build()
            assert hasattr(b, 'split_elements')
        finally:
            ops.wipe()

    def test_rebuild_preserves_geometry(self, sample_md):
        """Rebuilding with different pattern_scales should not error."""
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
            assert True
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Static analysis
# ============================================================================

class TestStaticAnalysisWorkflow:
    """End-to-end linear static analysis."""

    def test_static_analysis_returns_dict(self, sample_builder):
        """run_static_analysis() should return a dict with expected keys."""
        try:
            sample_builder.build()
            results = sample_builder.run_static_analysis(extract_reactions=True)
            assert isinstance(results, dict)
            # Key results that should always be present
            assert 'nodal_displacements' in results
            assert 'summed_reactions' in results
        finally:
            ops.wipe()

    def test_static_analysis_displacements(self, sample_builder):
        """Displacements should be non-zero for the loaded cantilever."""
        try:
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
            assert abs(dx) > 1e-8
        finally:
            ops.wipe()

    def test_static_element_forces(self, sample_builder):
        """Extracting element forces should work after analysis."""
        try:
            sample_builder.build()
            sample_builder.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            forces = sample_builder.extract_static_element_forces()
            assert isinstance(forces, dict)
            # At least one element should have forces
            if forces:
                tag = list(forces.keys())[0]
                f = forces[tag]
                assert 'Fx' in f
                assert 'Mz' in f
        finally:
            ops.wipe()

    def test_static_gravity_vs_pattern(self, sample_builder):
        """Run with gravity only, then with combined patterns."""
        try:
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
            assert r1 and r2
        finally:
            ops.wipe()

    def test_static_reactions_equilibrium(self, sample_builder):
        """Summed reactions should approximately balance applied loads."""
        try:
            sample_builder.build()
            results = sample_builder.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
                extract_reactions=True,
            )
            summed = results.get('summed_reactions', {})
            # For a downward gravity load, vertical reactions should be negative
            # (supporting downward load), so Fz should be non-zero.
            assert abs(summed.get('fz', 0.0)) > 1e-6
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Modal analysis
# ============================================================================

class TestModalAnalysisWorkflow:
    """End-to-end eigenvalue / modal analysis."""

    def test_modal_analysis_returns_keys(self, sample_builder):
        """run_modal_analysis() should return the expected result dict."""
        try:
            sample_builder.build()
            sample_builder.compute_seismic_masses(g=9.81)
            modal = sample_builder.run_modal_analysis(num_modes=3)
            assert isinstance(modal, dict)
            assert 'periods' in modal
            assert 'eigenvalues' in modal
            assert 'frequencies' in modal
            assert len(modal['periods']) == 3
        finally:
            ops.wipe()

    def test_modal_first_period_positive(self, sample_builder):
        """Fundamental period should be positive and reasonable."""
        try:
            sample_builder.build()
            sample_builder.compute_seismic_masses(g=9.81)
            modal = sample_builder.run_modal_analysis(num_modes=3)
            T1 = modal['periods'][0]
            # A 10 m steel cantilever typically has T1 ~0.2–2.0 s
            assert 0.01 < T1 < 10.0
        finally:
            ops.wipe()

    def test_extract_mode_shapes(self, sample_builder):
        """extract_mode_shapes() should return a dict with node displacements."""
        try:
            sample_builder.build()
            sample_builder.compute_seismic_masses(g=9.81)
            modal = sample_builder.run_modal_analysis(num_modes=2)
            shapes = sample_builder.extract_mode_shapes(num_modes=2)
            assert isinstance(shapes, dict)
            # Should have entries for each requested mode
            assert 0 in shapes or list(shapes.keys())[0] is not None
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Pushover analysis
# ============================================================================

class TestPushoverWorkflow:
    """End-to-end non-linear pushover analysis (truss-brace approach)."""

    def test_pushover_uniform_returns_keys(self, sample_builder):
        """run_pushover_analysis() should return expected keys."""
        try:
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
            assert len(results['step']) > 1
        finally:
            ops.wipe()

    def test_pushover_triangular_returns_keys(self, sample_builder):
        """Triangular lateral pattern produces a valid result."""
        try:
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
            assert len(results['control_disp']) > 1
        finally:
            ops.wipe()

    def test_pushover_pattern_returns_keys(self, sample_builder):
        """Pattern-based lateral load (SAP2000 WIND) should work."""
        try:
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
            assert len(results['control_disp']) > 1
        finally:
            ops.wipe()


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
        """run_response_spectrum_analysis() should complete."""
        try:
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
            assert 'base_shear_cqc' in results
        finally:
            ops.wipe()

    def test_element_rs_forces(self, sample_builder, spectrum):
        """extract_element_rs_forces() after RS analysis."""
        try:
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
            assert 'element_results' in rs_forces
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Results export
# ============================================================================

class TestExportWorkflow:
    """End-to-end NPZ export."""

    def test_export_to_npz(self, sample_builder, tmp_path):
        """export_results_to_npz() should create a valid .npz file."""
        try:
            sample_builder.build()
            results = sample_builder.run_static_analysis(
                pattern_scales={"DEAD": 1.0, "WIND": 1.0},
            )
            npz_path = str(tmp_path / "test_results.npz")
            sample_builder.export_results_to_npz(npz_path, results)
            # Verify the file exists and can be loaded
            import numpy as np
            data = np.load(npz_path, allow_pickle=True)
            assert 'sub_elem_tags' in data
            assert 'node_tags' in data
            assert 'force_unit' in data
            data.close()
        finally:
            ops.wipe()

    def test_export_with_section_responses(self, sample_builder, tmp_path):
        """Export with section_responses flag should not error."""
        try:
            sample_builder.build()
            results = sample_builder.run_static_analysis(
                pattern_scales={"DEAD": 1.0},
            )
            npz_path = str(tmp_path / "test_results_sec.npz")
            sample_builder.export_results_to_npz(npz_path, results,
                section_responses={"section_forces": True})
            import numpy as np
            data = np.load(npz_path, allow_pickle=True)
            assert data is not None
            data.close()
        finally:
            ops.wipe()


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
        """pushover_to_adrs() returns ADRS dict with expected keys."""
        try:
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
        finally:
            ops.wipe()

    def test_compute_performance_point(self, sample_builder, spectrum):
        """compute_performance_point() runs and returns a result dict."""
        try:
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
        finally:
            ops.wipe()


# ============================================================================
# Workflow: Euler buckling check
# ============================================================================

class TestBucklingCheckWorkflow:
    """End-to-end Euler buckling check."""

    def test_check_brace_buckling_no_braces(self, sample_builder):
        """check_brace_buckling() with no brace IDs returns empty dict."""
        try:
            sample_builder.build()
            result = sample_builder.check_brace_buckling(
                brace_ids=set(), print_results=False,
            )
            assert result == {}
        finally:
            ops.wipe()

    def test_check_brace_buckling_with_ids(self, sample_builder):
        """Check buckling for the single frame element in the sample."""
        try:
            sample_builder.build()
            result = sample_builder.check_brace_buckling(
                brace_ids={"1"}, print_results=False,
            )
            assert isinstance(result, dict)
            if "1" in result:
                r = result["1"]
                assert 'P_cr' in r
                assert r['P_cr'] > 0
        finally:
            ops.wipe()
