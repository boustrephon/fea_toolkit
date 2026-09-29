"""Analysis ▸ Run in the GUI: solve on a worker, view it, save it (P27/I3–I4).

Qt-dependent (``needs_gui``).  The data path — solve, reduce, assemble, serve —
is pinned Qt-free in ``test_run_case_set.py`` and ``test_case_listing.py``; what
is tested here is the *wiring*: the enablement gate, the worker, the registered
case views, and that a run is viewable **without writing a file**.

The dialog tests at the bottom are pure Qt — no OpenSees — and pin the
per-option load multiplier the dialog exists to offer.
"""

import time

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


def _model_with_cases():
    """The built-in cantilever, given a DEAD / WIND case and one combination.

    ``make_sample_model()`` defines the ``DEAD`` (self-weight) and ``WIND``
    (frame distributed) patterns but no load cases, so they are attached here.
    """
    from examples.sample_model import make_sample_model
    from fea_toolkit.model.load_combinations import classify_combination_refs
    from fea_toolkit.model.sap_data import LoadCase, LoadCombination, LoadCombinationEntry

    md = make_sample_model()

    def case(name, assignments):
        lc = LoadCase(name, "LinStatic", "Prog Det", "Dead", "Prog Det", "Non-Composite")
        lc.case_data["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = assignments
        return lc

    md.load_cases = {
        "DEAD": case("DEAD", [{"LoadName": "DEAD"}]),
        "WIND": case("WIND", [{"LoadName": "WIND", "LoadSF": 1.0}]),
    }
    combos = {"GRAV": LoadCombination("GRAV", "Linear Add", [LoadCombinationEntry("DEAD", 1.2)])}
    classify_combination_refs(combos, md.load_cases)
    md.load_combinations = combos
    return md


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the cantilever, with no interaction config."""
    import openseespy.opensees as ops

    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_model_with_cases())
    win.resize(900, 700)
    win.show()
    try:
        yield win
    finally:
        # ``ops.wipe()`` must run even if ``win.close()`` raises, so a failing
        # close cannot leak OpenSees global state into the next test.
        try:
            win.close()
        finally:
            ops.wipe()


def _spin(window, timeout=60.0):
    """Spin the GUI until the current worker ends."""
    from qtpy.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while window._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "a worker never finished"


def _preprocess(window):
    """Run ``Model ▸ Split elements`` to completion — Run's prerequisite."""
    window._actions["model.split"].trigger()
    _spin(window)


# ── The gate ───────────────────────────────────────────────────────────


def test_run_is_greyed_until_the_model_is_preprocessed(window):
    """Preprocessing is a prerequisite, and Run says so (P27, refinement 1)."""
    from fea_toolkit.model.mesh_model import MeshModel

    assert window._actions["analysis.run"].isEnabled() is False
    assert "Model \u25b8 Split" in window._actions["analysis.run"].toolTip()

    _preprocess(window)

    assert isinstance(window._store.preprocessed(), MeshModel)
    assert window._actions["analysis.run"].isEnabled() is True


def test_running_without_a_preprocessed_model_declines(window):
    """``run_analysis`` refuses rather than preprocessing silently."""
    assert window.run_analysis({"DEAD": {"DEAD": 1.0}}) is False
    assert "needs a preprocessed model" in window._message_log.toPlainText()


# ── A run ──────────────────────────────────────────────────────────────


def test_a_run_registers_a_case_view_without_writing_a_file(window, tmp_path):
    """The whole point of I3: view a result, write nothing."""
    _preprocess(window)

    assert window.run_analysis({"DEAD": {"DEAD": 1.0}}) is True
    _spin(window)

    view = window._views.get("results:DEAD")
    assert view is not None
    assert window._views.results("results:DEAD") is not None
    assert "Run complete" in window._message_log.toPlainText()

    # Nothing was written: no NPZ anywhere under the test's temp directory.
    assert list(tmp_path.rglob("*.npz")) == []

    # …and the result is drawable, so the overlay toggles come alive.
    assert window._actions["results.deformed"].isEnabled() is True
    assert window._actions["file.save_results"].isEnabled() is True


def test_a_second_case_is_added_beside_the_first(window):
    """Different cases coexist; the same case replaces (keyed by case)."""
    _preprocess(window)

    window.run_analysis({"DEAD": {"DEAD": 1.0}})
    _spin(window)
    window.run_analysis({"WIND": {"WIND": 1.0}})
    _spin(window)

    assert window._views.get("results:DEAD") is not None
    assert window._views.get("results:WIND") is not None


def test_a_combination_is_reduced_to_its_own_view(window):
    _preprocess(window)

    assert window.run_analysis({"DEAD": {"DEAD": 1.0}}, {"GRAV": {"DEAD": 1.2}}) is True
    _spin(window)

    assert window._views.get("results:DEAD") is not None
    assert window._views.get("results:GRAV") is not None


# ── Saving (I4) ────────────────────────────────────────────────────────


def test_save_results_writes_a_round_trippable_archive(window, tmp_path, monkeypatch):
    from qtpy.QtWidgets import QFileDialog

    from fea_toolkit.io.results_repository import NpzResultsRepository

    _preprocess(window)
    window.run_analysis({"DEAD": {"DEAD": 1.0}})
    _spin(window)

    target = tmp_path / "saved.npz"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    window._on_save_results()

    assert target.exists()
    assert NpzResultsRepository(str(target)).cases() == ["DEAD"]
    assert "Saved results to" in window._message_log.toPlainText()


# ── The dialog: the per-case load multiplier ───────────────────────────


def test_the_case_factor_multiplies_the_stored_pattern_factors(qapp):
    """A ticked, scaled case gets a distinct key and scaled patterns."""
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0, "SDL": 0.5}}, [], ["DEAD", "SDL"])
    row = dialog._case_rows[0]
    row.check.setChecked(True)
    row.factor.setValue(2.0)

    # A scaled solve is stored under its own name, so a combination reduction
    # cannot mistake it for the model's own (un-scaled) case.
    assert dialog.request().cases == {"DEAD \u00d72": {"DEAD": 2.0, "SDL": 1.0}}


def test_an_unscaled_case_keeps_its_model_name(qapp):
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [], ["DEAD"])
    dialog._case_rows[0].check.setChecked(True)

    assert dialog.request().cases == {"DEAD": {"DEAD": 1.0}}


def test_a_scaled_leaf_keeps_the_unscaled_case_for_its_combination(qapp):
    """The reduction reads the model's factors, so it needs the un-scaled case."""
    from fea_toolkit.analysis.case_listing import CombinationSpec
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    spec = CombinationSpec(name="GRAV", combo_type="Linear Add", leaves={"DEAD": 1.2})
    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [spec], ["DEAD"])
    dialog._case_rows[0].check.setChecked(True)
    dialog._case_rows[0].factor.setValue(2.0)
    dialog._combo_rows[0].check.setChecked(True)

    request = dialog.request()
    assert request.combinations == ["GRAV"]
    # Both the user's scaled solve and the model's un-scaled case are present.
    assert request.cases["DEAD \u00d72"] == {"DEAD": 2.0}
    assert request.cases["DEAD"] == {"DEAD": 1.0}


def test_a_scaled_name_cannot_shadow_a_model_case_of_the_same_spelling(qapp):
    """``DEAD`` ×2 must not overwrite a model case literally named ``DEAD ×2``."""
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog(
        {"DEAD": {"DEAD": 1.0}, "DEAD \u00d72": {"SDL": 1.0}},
        [],
        ["DEAD", "SDL"],
    )
    dialog._case_rows[0].check.setChecked(True)  # DEAD …
    dialog._case_rows[0].factor.setValue(2.0)  # … scaled ×2 would be "DEAD ×2"
    dialog._case_rows[1].check.setChecked(True)  # the model's own "DEAD ×2"

    request = dialog.request()
    # The model case keeps its name and its own patterns…
    assert request.cases["DEAD \u00d72"] == {"SDL": 1.0}
    # …and the scaled DEAD lands under a distinct, non-colliding name.
    assert request.cases["DEAD \u00d72 (2)"] == {"DEAD": 2.0}
    assert set(request.cases) == {"DEAD \u00d72", "DEAD \u00d72 (2)"}


def test_an_unticked_case_is_not_run(qapp):
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [], ["DEAD"])

    assert dialog.request().cases == {}


def test_a_custom_case_is_authored_from_patterns(qapp):
    """The ``{"ULT": {"Dead": 1.4, "Live": 1.6}}`` form, as a UI."""
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog({}, [], ["DEAD", "LIVE"])
    dialog._custom_name.setText("ULT")
    dialog._pattern_rows[0].check.setChecked(True)
    dialog._pattern_rows[0].factor.setValue(1.4)
    dialog._pattern_rows[1].check.setChecked(True)
    dialog._pattern_rows[1].factor.setValue(1.6)

    assert dialog.request().cases == {"ULT": {"DEAD": 1.4, "LIVE": 1.6}}


def test_ticking_a_combination_pulls_in_the_cases_it_needs(qapp):
    from fea_toolkit.analysis.case_listing import CombinationSpec
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    spec = CombinationSpec(name="GRAV", combo_type="Linear Add", leaves={"DEAD": 1.2})
    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [spec], ["DEAD"])
    dialog._combo_rows[0].check.setChecked(True)

    request = dialog.request()
    assert request.combinations == ["GRAV"]
    assert request.cases == {"DEAD": {"DEAD": 1.0}}


def test_a_combination_needing_an_unrunnable_case_is_greyed(qapp):
    """A response-spectrum leaf cannot be solved here — offer it disabled."""
    from fea_toolkit.analysis.case_listing import CombinationSpec
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    spec = CombinationSpec(name="SEISM", combo_type="Linear Add", leaves={"RSX": 1.0})
    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [spec], ["DEAD"])

    assert dialog._combo_rows[0].check.isEnabled() is False


def test_run_is_gated_on_something_being_ticked(qapp):
    from qtpy.QtWidgets import QDialogButtonBox

    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [], ["DEAD"])
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled() is False

    dialog._case_rows[0].check.setChecked(True)
    assert ok.isEnabled() is True


def test_the_dialog_returns_the_edited_config(qapp):
    """The Configuration group's edits reach the run request."""
    from fea_toolkit.gui.views.analysis_dialog import AnalysisDialog

    dialog = AnalysisDialog({"DEAD": {"DEAD": 1.0}}, [], ["DEAD"])
    assert dialog.request().config == {}

    dialog._config_editor._widgets["verbose"].setChecked(True)
    dialog._config_editor._widgets["element_type"].setCurrentText("dispBeamColumn")

    assert dialog.request().config == {"verbose": True, "element_type": "dispBeamColumn"}
