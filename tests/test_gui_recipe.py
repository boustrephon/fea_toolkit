"""The Recipe panel, and the Model-menu presets that feed it (Phase B).

Qt-dependent (``needs_gui``).  What is tested here is the *panel* — that a preset
writes the step it stands for, that an edit reaches the step, and that the
Recipe actions follow the recipe's contents.  The verbs themselves are tested
without Qt in ``tests/test_recipe.py``.
"""

import time

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


def _model():
    """A two-node cantilever — enough for the Preprocessor to have a model."""
    from examples.sample_model import make_sample_model

    return make_sample_model()


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the sample model, with the Recipe dock built."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _await(window, timeout=60.0):
    """Spin the GUI until the recipe worker has finished."""
    from qtpy.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while window._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "the recipe worker never finished"


class TestModelPresets:
    """The Model menu writes steps into the recipe rather than hiding the work."""

    def test_split_elements_appends_a_split_only_step(self, window):
        window._actions["model.split"].trigger()
        _await(window)

        (step,) = window._recipe_panel.recipe.steps
        assert step.verb == "mesh"
        assert step.params == {"split_elements": True, "create_shells": False}
        assert window._model is not None  # the run still produced a topology

    def test_mesh_areas_appends_a_meshing_step(self, window):
        window._actions["model.mesh"].trigger()
        _await(window)

        (step,) = window._recipe_panel.recipe.steps
        assert step.params.get("create_shells") is True

    def test_the_preset_registers_a_view_and_logs_the_summary(self, window):
        window._actions["model.mesh"].trigger()
        _await(window)

        assert [view.key for view in window._views.views()] == ["unprocessed", "meshed"]
        log = window._message_log.toPlainText()
        assert "Splitting elements at joints and meshing areas" in log
        assert "Preprocessed:" in log
        assert "Added view: Meshed." in log


class TestPanel:
    """The panel is a view over the recipe — edits reach the step."""

    def test_the_list_has_one_row_per_step(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh")
        panel.add_step("run_static")
        assert panel._list.count() == 2
        assert panel._list.item(1).text() == "2. run_static"

    def test_a_parameter_edit_reaches_the_step(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh")
        panel._set_param("split_slabs_at_walls", True)
        assert panel.recipe.steps[0].params["split_slabs_at_walls"] is True

    def test_an_optional_step_is_marked_in_the_list(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh")
        panel._on_optional_toggled(True)
        assert panel.recipe.steps[0].optional is True
        assert "(optional)" in panel._list.item(0).text()

    def test_a_step_carries_the_selection_it_was_given(self, window):
        from fea_toolkit.model.selection import Selection

        panel = window._recipe_panel
        selection = Selection(sections=["brick wall"], element_types=["Area"])
        panel.add_step("scale_sections", selection)
        assert panel.recipe.steps[0].selection == selection

    def test_a_step_can_be_removed_and_reordered(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh")
        panel.add_step("run_static")
        panel.move_selected(-1)
        assert [step.verb for step in panel.recipe] == ["run_static", "mesh"]
        panel.remove_selected()
        assert [step.verb for step in panel.recipe] == ["mesh"]

    def test_the_recipe_actions_follow_the_contents(self, window):
        assert window._actions["recipe.run"].isEnabled() is False
        window._recipe_panel.add_step("mesh")
        assert window._actions["recipe.run"].isEnabled() is True
        window._recipe_panel.clear()
        assert window._actions["recipe.run"].isEnabled() is False

    def test_a_loaded_recipe_is_displayed(self, window):
        from fea_toolkit.workflow import Recipe

        recipe = Recipe.from_dict(
            {"name": "t", "steps": [{"verb": "mesh", "params": {"split_slabs_at_walls": True}}]}
        )
        window._recipe_panel.set_recipe(recipe)
        assert window._recipe_panel._list.count() == 1
        assert window._recipe_panel.recipe.steps[0].params == {"split_slabs_at_walls": True}


class TestRunOrder:
    """A recipe runs its steps in order, and the panel shows the workflow."""

    def test_a_two_step_recipe_runs_both(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh", params={"create_shells": False})
        panel.add_step("mesh", params={"create_shells": True})
        window._actions["recipe.run"].trigger()
        _await(window)

        assert [view.key for view in window._views.views()] == [
            "unprocessed",
            "processed",
            "meshed",
        ]

    def test_an_empty_recipe_warns_rather_than_running(self, window):
        window._on_recipe_run()
        assert "The recipe has no steps" in window._message_log.toPlainText()
