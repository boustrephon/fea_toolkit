"""Tests for the analysis helpers (analysis/base.py, modal.py, static.py,
rs.py, pushover.py, nonlinear_dynamic.py).

The analysis subpackage exposes module-level functions that return typed
:class:`AnalysisResult` containers — no manager / dependency graph.
"""

import inspect

from fea_toolkit.analysis import (
    AnalysisCaseSpec,
    AnalysisResult,
    run_modal_analysis,
    run_nonlinear_dynamic_analysis,
    run_pushover_analysis,
    run_response_spectrum_analysis,
    run_static_analysis,
)
from fea_toolkit.analysis.base import (
    _LINEAR_ELASTIC_DEFAULTS,
    _NONLINEAR_DYNAMIC_DEFAULTS,
    _PUSHOVER_RC_DEFAULTS,
    _PUSHOVER_STEEL_DEFAULTS,
)

# ── AnalysisResult ──────────────────────────────────────────────────────────


class TestAnalysisResult:
    def test_basic_construction(self):
        r = AnalysisResult(
            name="test",
            analysis_type="ModalAnalysis",
            data={"periods": [1.0, 0.5]},
        )
        assert r.name == "test"
        assert r.analysis_type == "ModalAnalysis"
        assert r.data == {"periods": [1.0, 0.5]}
        assert r.metadata == {}

    def test_with_metadata(self):
        r = AnalysisResult(
            name="modal_12",
            analysis_type="ModalAnalysis",
            data={"periods": [1.0]},
            metadata={"n_modes": 12},
        )
        assert r.metadata == {"n_modes": 12}

    def test_repr(self):
        r = AnalysisResult(name="x", analysis_type="T", data={})
        assert "AnalysisResult" in repr(r)
        assert "x" in repr(r)


# ── AnalysisCaseSpec ────────────────────────────────────────────────────────


class TestAnalysisCaseSpec:
    def test_to_dict(self):
        spec = AnalysisCaseSpec(name="test", analysis_type="pushover")
        data = spec.to_dict()
        assert data["name"] == "test"
        assert data["analysis_type"] == "pushover"
        assert data["config"] == {}
        assert data["kwargs"] == {}


# ── Defaults (single linear-elastic dict + distinct nonlinear dicts) ──────


class TestAnalysisDefaults:
    def test_linear_elastic_defaults_exist(self):
        d = dict(_LINEAR_ELASTIC_DEFAULTS)
        assert d["element_type"] == "elasticBeamColumn"
        assert d["solver_algorithm"] == "Newton"

    def test_pushover_steel_defaults_exist(self):
        d = dict(_PUSHOVER_STEEL_DEFAULTS)
        assert d["element_type"] == "nonlinearBeamColumn"

    def test_pushover_rc_defaults_relaxed(self):
        """RC fibre pushover must use the relaxed, validated solver settings.

        ``forceBeamColumn`` carries member loads internally and performs its
        own state-determination iteration, so the strict generic defaults
        (``NormDispIncr 1e-6 / 10``) stall around 0.006 m control
        displacement (see docs/_pending_work.md, 2026-08-04).  The confirmed
        working contract is ``NormDispIncr 1e-4 / 20 / Newton`` plus the
        per-step fallback chain.
        """
        d = dict(_PUSHOVER_RC_DEFAULTS)
        assert d["element_type"] == "forceBeamColumn"
        assert d["solver_test_type"] == "NormDispIncr"
        assert d["solver_test_tol"] == 1e-4
        assert d["solver_test_max_iter"] == 20
        assert d["solver_algorithm"] == "Newton"
        assert d["gravity_num_substeps"] >= 1
        assert "pushover_fallback_defaults" in d

    def test_pushover_rc_fallback_matches_builder(self):
        """The RC-preset fallback must stay in sync with the builder's
        ``PUSHOVER_FALLBACK_DEFAULTS`` (NormUnbalance / 1000 iter /
        ModifiedNewton with a runtime-scaled tolerance)."""
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        fb = dict(_PUSHOVER_RC_DEFAULTS["pushover_fallback_defaults"])
        assert fb == dict(AnalysisBuilder.PUSHOVER_FALLBACK_DEFAULTS)

    def test_nonlinear_dynamic_defaults_exist(self):
        d = dict(_NONLINEAR_DYNAMIC_DEFAULTS)
        assert d["element_type"] == "forceBeamColumn"
        assert d["rayleigh_damping"] is True


# ── Analysis helper functions (no OpenSeesPy required) ────────────────────


class TestAnalysisFunctions:
    def test_run_modal_signature(self):
        params = inspect.signature(run_modal_analysis).parameters
        assert "n_modes" in params
        assert params["n_modes"].default == 12

    def test_run_static_requires_md(self):
        params = inspect.signature(run_static_analysis).parameters
        assert "md" in params
        assert "mesh_model" in params
        assert "collect_raw" in params
        assert params["collect_raw"].default is False

    def test_run_rs_requires_modal_result(self):
        params = inspect.signature(run_response_spectrum_analysis).parameters
        assert "modal_result" in params
        assert "direction" in params
        assert "T_spec" in params

    def test_run_pushover_signature(self):
        params = inspect.signature(run_pushover_analysis).parameters
        assert "material_type" in params
        assert "lateral_load_type" in params
        assert params["material_type"].default == "steel"
        assert "return_builders" in params
        assert params["return_builders"].default is False

    def test_run_nonlinear_dynamic_signature(self):
        params = inspect.signature(run_nonlinear_dynamic_analysis).parameters
        assert "ground_motion_file" in params
        assert "direction" in params
        assert "modal_result" in params

    def test_functions_return_analysis_result_annotations(self):
        for fn in (
            run_modal_analysis,
            run_static_analysis,
            run_response_spectrum_analysis,
            run_pushover_analysis,
            run_nonlinear_dynamic_analysis,
        ):
            assert fn.__annotations__["return"] is AnalysisResult


# ── generate_report integration test (no OpenSeesPy) ──────────────────


class TestGenerateReportManagerPath:
    """Verify generate_report no longer routes through a manager.

    The old ``run_via_manager`` parameter is gone and the default config
    carries no manager key — the pipeline is now an explicit sequence.
    """

    def test_run_via_manager_param_removed(self):
        import inspect

        from fea_toolkit.report import generate_report

        sig = inspect.signature(generate_report)
        assert "run_via_manager" not in sig.parameters

    def test_inline_path_still_default(self):
        from fea_toolkit.report import _DEFAULT_CONFIG

        assert "run_via_manager" not in _DEFAULT_CONFIG
