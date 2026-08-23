"""Tests for the report orchestration module (:mod:`fea_toolkit.report`).

Covers the pure config logic and public signature of
:func:`fea_toolkit.report.generate_report` — no OpenSeesPy required.
The heavy analysis pipeline itself is exercised by the model-level
integration tests (e.g. ``tests/test_model.py``).
"""

import inspect

from fea_toolkit.report import (
    _DEFAULT_CONFIG,
    _merge_report_config,
    generate_report,
)


class TestDefaultConfig:
    """``_DEFAULT_CONFIG`` is a complete, project-agnostic config skeleton."""

    def test_required_sections_present(self):
        for key in (
            "general",
            "loads",
            "spectrum",
            "pushover",
            "linear",
            "checks",
            "stories",
            "storey_response",
            "static_verification",
            "model_viewer",
            "analysis_log",
        ):
            assert key in _DEFAULT_CONFIG

    def test_spectrum_defaults_project_agnostic(self):
        # Generic defaults must never carry project-specific settings
        # (spectrum codes, paths, ...) — those belong in the caller.
        assert _DEFAULT_CONFIG["spectrum"] == {}

    def test_pushover_defaults_shape(self):
        push = _DEFAULT_CONFIG["pushover"]
        assert push["brace_type"] == "truss"
        assert "patterns" in push
        assert "directions" in push
        assert "num_steps" in push
        assert "max_disp" in push


class TestMergeReportConfig:
    """``_merge_report_config`` merges defaults < config < overrides."""

    def test_defaults_used_when_nothing_supplied(self):
        assert _merge_report_config() == _DEFAULT_CONFIG
        assert _merge_report_config(None, None) == _DEFAULT_CONFIG

    def test_config_deep_merged_over_defaults(self):
        cfg = _merge_report_config({"general": {"n_modes": 6}})
        assert cfg["general"]["n_modes"] == 6
        # Untouched sibling survives the deep merge
        assert cfg["general"]["verbose"] is True
        assert cfg["pushover"] == _DEFAULT_CONFIG["pushover"]

    def test_double_underscore_overrides_nest(self):
        cfg = _merge_report_config({}, {"general__n_modes": 6})
        assert cfg["general"]["n_modes"] == 6

    def test_flat_override_keys_pass_through(self):
        cfg = _merge_report_config({}, {"extra": 1})
        assert cfg["extra"] == 1

    def test_overrides_beat_config(self):
        cfg = _merge_report_config({"general": {"n_modes": 3}}, {"general__n_modes": 12})
        assert cfg["general"]["n_modes"] == 12

    def test_nested_override_preserves_siblings(self):
        cfg = _merge_report_config({}, {"pushover__num_steps": 10})
        assert cfg["pushover"]["num_steps"] == 10
        assert cfg["pushover"]["max_disp"] == _DEFAULT_CONFIG["pushover"]["max_disp"]

    def test_multi_level_override_nests(self):
        """Multi-``__`` keys build nested dicts for every component."""
        cfg = _merge_report_config({}, {"pushover__spectrum__damping": 0.03})
        assert cfg["pushover"]["spectrum"]["damping"] == 0.03
        assert cfg["pushover"]["max_disp"] == _DEFAULT_CONFIG["pushover"]["max_disp"]

    def test_returned_config_is_independent_of_defaults(self):
        # Nested dicts of the merged result must not alias the module-level
        # ``_DEFAULT_CONFIG`` — mutating a returned config would otherwise
        # corrupt every subsequent ``generate_report()`` call.
        cfg = _merge_report_config()
        cfg["general"]["n_modes"] = 99
        assert _DEFAULT_CONFIG["general"]["n_modes"] == 12


class TestGenerateReportSignature:
    """Public contract of the orchestrator entry point."""

    def test_parameter_contract(self):
        sig = inspect.signature(generate_report)
        assert list(sig.parameters) == ["md", "mesh_model", "config", "out_dir", "overrides"]
        assert sig.parameters["md"].annotation is not inspect.Parameter.empty
        assert sig.parameters["mesh_model"].default is None
        assert sig.parameters["config"].default is None
        assert sig.parameters["out_dir"].default is None
