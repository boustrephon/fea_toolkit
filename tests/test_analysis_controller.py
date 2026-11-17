"""Unit tests for :class:`AnalysisController` — Qt-free analysis step composition."""

from fea_toolkit.gui.controllers.analysis_controller import AnalysisController


def test_static_steps_has_no_combine_when_none_named():
    steps = AnalysisController.static_steps({"DEAD": {"self": 1.0}})
    assert [step.verb for step in steps] == ["run_static", "load_verification"]


def test_static_steps_adds_combine_when_combinations_named():
    steps = AnalysisController.static_steps({"DEAD": {}}, combinations=["Comb1"])
    assert [step.verb for step in steps] == ["run_static", "combine", "load_verification"]
    assert steps[1].params["combinations"] == ["Comb1"]


def test_static_steps_copies_cases_and_config():
    cases = {"DEAD": {"self": 1.0}}
    config = {"solver_test_tol": 1e-6}
    steps = AnalysisController.static_steps(cases, config=config)

    assert steps[0].params["cases"] == cases
    assert steps[0].params["cases"] is not cases
    assert steps[0].params["config"] == config
    assert steps[0].params["config"] is not config


def test_preset_prefill_names_the_modal_case():
    assert AnalysisController.preset_prefill("modal", "MODAL") == {"name": "MODAL"}


def test_preset_prefill_prefills_modal_case_for_dependents():
    for verb in ("response_spectrum", "pushover", "nonlinear_dynamic", "ida"):
        assert AnalysisController.preset_prefill(verb, "MODAL") == {"modal_case": "MODAL"}


def test_preset_prefill_is_empty_without_a_default_modal():
    assert AnalysisController.preset_prefill("modal", "") == {}
    assert AnalysisController.preset_prefill("pushover", "") == {}
