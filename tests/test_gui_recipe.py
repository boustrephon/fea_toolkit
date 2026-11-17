"""The Recipe panel, and the Model-menu presets that feed it (Phase B).

Qt-dependent (``needs_gui``).  What is tested here is the *panel* — that a preset
writes the step it stands for, that an edit reaches the step, and that the
Recipe actions follow the recipe's contents.  The verbs themselves are tested
without Qt in ``tests/test_recipe.py``.
"""

import time

import pytest

pytestmark = pytest.mark.needs_gui


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
    try:
        yield win
    finally:
        # A ``run_static`` recipe step builds an OpenSees domain, so the global
        # state must be cleared even if the window fails to close (guardrails
        # §5.6) — a leaked domain would show up as a failure in an unrelated test.
        try:
            win.close()
        finally:
            import openseespy.opensees as ops

            ops.wipe()


def _await(window, timeout=60.0):
    """Spin the GUI until the recipe worker has finished."""
    from qtpy.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while window._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "the recipe worker never finished"


class TestSessionRecipeOwnership:
    """The session owns the recipe; the panel is a view over it.

    The panel edits the session's recipe *in place*, and ``changed`` carries
    the rebinds back, so ``session.recipe is panel.recipe`` always holds.  This
    pins the single-object invariant the Option B ownership relies on (the
    panel's editing behaviour is covered elsewhere).
    """

    def test_the_session_and_panel_share_one_recipe_after_load(self, window):
        assert window._session.recipe is window._recipe_panel.recipe

    def test_an_in_place_edit_stays_on_the_shared_object(self, window):
        window._recipe_panel.add_step("mesh")
        assert window._session.recipe is window._recipe_panel.recipe
        assert len(window._session.recipe) == 1
        assert window._session.recipe.steps[0].verb == "mesh"

    def test_a_rebind_resyncs_the_session(self, window):
        from fea_toolkit.workflow import Recipe

        replacement = Recipe()
        window._recipe_panel.set_recipe(replacement)
        assert window._session.recipe is replacement
        assert window._session.recipe is window._recipe_panel.recipe

    def test_a_clear_resyncs_the_session(self, window):
        window._recipe_panel.add_step("mesh")
        window._recipe_panel.clear()
        assert window._session.recipe is window._recipe_panel.recipe
        assert len(window._session.recipe) == 0


class TestDocumentAdoption:
    """A document-lifecycle recipe (``open`` / ``close``) drives the window (P35)."""

    def test_a_document_reset_run_replaces_the_store(self, window, tmp_path):
        from examples.sample_model import make_sample_model
        from fea_toolkit.workflow import RecipeRun
        from fea_toolkit.workflow.steps import DocumentState, StepResult

        opened = make_sample_model()  # a fresh instance, distinct from the window's
        path = str(tmp_path / "doc.json")
        run = RecipeRun(
            document=DocumentState(model_data=opened, document_path=path),
            document_reset=True,
            results=[StepResult(kind="session", label="Opened document", payload=path)],
        )
        window._recipe_finished((run, []))
        assert window._session.store.raw() is opened
        assert window._source_label == path

    def test_a_save_only_run_keeps_the_store(self, window, tmp_path):
        from fea_toolkit.workflow import RecipeRun
        from fea_toolkit.workflow.steps import DocumentState, StepResult

        before = window._session.store.raw()
        path = str(tmp_path / "doc.json")
        run = RecipeRun(
            document=DocumentState(model_data=before, document_path=path),
            document_reset=False,
            results=[StepResult(kind="session", label="Saved document", payload=path)],
        )
        window._recipe_finished((run, []))
        assert window._session.store.raw() is before

    def test_a_select_only_run_merges_only_changed_selections(self, window):
        """Only the entries a recipe changed are merged; a concurrent user edit survives."""
        from fea_toolkit.model.named_selection import NamedSelection
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.workflow import RecipeRun
        from fea_toolkit.workflow.steps import DocumentState

        core = NamedSelection(name="core", selection=Selection(sections=["COL"]))
        other = NamedSelection(name="other", selection=Selection(sections=["BEAM"]))
        window._recipe_named_snapshot = {"core": core, "other": other}
        # The user edits "other" while the run is in flight.
        other_edited = NamedSelection(name="other", selection=Selection(sections=["SLAB"]))
        window._session.named_selections = {"core": core, "other": other_edited}

        core_changed = NamedSelection(name="core", selection=Selection(sections=["BRACE"]))
        run = RecipeRun(
            document=DocumentState(
                model_data=window._session.store.raw(),
                named_selections={"core": core_changed, "other": other},
            ),
            document_reset=False,
        )
        window._recipe_finished((run, []))

        assert (
            window._session.named_selections["core"] == core_changed
        )  # the recipe's change landed
        assert window._session.named_selections["other"] == other_edited  # the user's edit survived

    def test_a_stale_run_is_rejected_after_switching_documents(self, window):
        """A run started for document A cannot affect document B opened mid-run."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.workflow import RecipeRun
        from fea_toolkit.workflow.steps import DocumentState

        starting_store = window._session.store
        window._recipe_starting_store = starting_store
        # The user switches documents while the run is in flight.
        window._remember_source(make_sample_model())
        new_store = window._session.store
        assert new_store is not starting_store

        run = RecipeRun(
            document=DocumentState(model_data=starting_store.raw()),
            document_reset=True,
        )
        window._recipe_finished((run, []))

        # The stale run's document was not adopted — B's store is untouched.
        assert window._session.store is new_store
        assert "no longer open" in window._message_log.toPlainText()


class TestExampleChooser:
    """The "Open example" chooser presets a builder/benchmark recipe (P46)."""

    def test_the_chooser_lists_builders_then_every_benchmark(self, window):
        from qtpy.QtCore import Qt

        from fea_toolkit.benchmarks.catalogue import BENCHMARK_ENTRIES
        from fea_toolkit.workflow.verbs.build_model import BUILDER_TITLES

        panel = window._example_dialog.panel
        ids = [
            panel._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(panel._list.count())
        ]
        builders = [f"builder:{name}" for name in BUILDER_TITLES]
        benchmark_ids = [entry.id for entry in BENCHMARK_ENTRIES]
        assert ids == builders + benchmark_ids
        enabled = sum(
            1
            for i in range(panel._list.count())
            if panel._list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled
        )
        assert enabled == len(builders) + sum(
            1 for entry in BENCHMARK_ENTRIES if entry.builder is not None
        )

    def test_open_presets_a_build_model_plus_mesh_recipe(self, window):
        window._open_example("ik2005_generic_frame")
        recipe = window._recipe_panel.recipe
        assert [step.verb for step in recipe.steps] == ["build_model", "mesh"]
        assert recipe.steps[0].params["builder"] == "generic_frame"

    def test_open_builder_presets_a_fema355c_recipe(self, window):
        window._open_example("builder:fema355c")
        recipe = window._recipe_panel.recipe
        assert [step.verb for step in recipe.steps] == ["build_model", "mesh"]
        assert recipe.steps[0].params == {"builder": "fema355c"}

    def test_open_emits_the_catalogue_id(self, window):
        from qtpy.QtCore import Qt

        panel = window._example_dialog.panel
        received: list = []
        panel.open_requested.connect(received.append)
        for i in range(panel._list.count()):
            if panel._list.item(i).data(Qt.ItemDataRole.UserRole) == "ik2005_generic_frame":
                panel._list.setCurrentRow(i)
                break
        panel._on_open_clicked()
        assert received == ["ik2005_generic_frame"]

    def test_open_rejects_an_unknown_benchmark(self, window):
        window._open_example("nope")
        assert "Could not open benchmark" in window._message_log.toPlainText()


class TestModelPresets:
    """The Model menu writes steps into the recipe rather than hiding the work."""

    def test_split_elements_appends_a_split_only_step(self, window):
        window._mesh_preset(
            {"split_elements": True, "create_shells": False}, "Splitting elements at joints"
        )
        _await(window)

        (step,) = window._recipe_panel.recipe.steps
        assert step.verb == "mesh"
        assert step.params == {"split_elements": True, "create_shells": False}
        assert window._model is not None  # the run still produced a topology

    def test_mesh_areas_appends_a_meshing_step(self, window):
        window._mesh_preset(
            {"split_elements": True, "create_shells": True},
            "Splitting elements at joints and meshing areas",
        )
        _await(window)

        (step,) = window._recipe_panel.recipe.steps
        assert step.params.get("create_shells") is True

    def test_the_preset_registers_a_view_and_logs_the_summary(self, window):
        window._mesh_preset(
            {"split_elements": True, "create_shells": True},
            "Splitting elements at joints and meshing areas",
        )
        _await(window)

        assert [view.key for view in window._session.views.views()] == ["unprocessed", "meshed"]
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
        assert panel._table.rowCount() == 2
        assert panel._table.item(1, 1).text() == "2. run_static"

    def test_an_edit_via_the_dialog_reaches_the_step(self, window, monkeypatch):
        from fea_toolkit.workflow import Step

        panel = window._recipe_panel
        panel.add_step("mesh")
        monkeypatch.setattr(
            "fea_toolkit.gui.views.step_dialog.StepDialog.edit",
            staticmethod(
                lambda step, parent=None, model_data=None: Step(
                    verb=step.verb, params={"split_slabs_at_walls": True}
                )
            ),
        )
        panel._edit_step_interactive()
        assert panel.recipe.steps[0].params["split_slabs_at_walls"] is True

    def test_an_optional_step_is_marked_in_the_list(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh", optional=True)
        assert panel.recipe.steps[0].optional is True
        assert "(optional)" in panel._table.item(0, 1).text()

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
        assert window._recipe_panel._table.rowCount() == 1
        assert window._recipe_panel.recipe.steps[0].params == {"split_slabs_at_walls": True}

    def test_the_run_checkbox_controls_the_step_enabled_flag(self, window):
        from qtpy.QtWidgets import QCheckBox

        panel = window._recipe_panel
        panel.add_step("mesh")
        checkbox = panel._table.cellWidget(0, 0).findChild(QCheckBox)
        assert checkbox.isChecked() is True
        checkbox.setChecked(False)
        assert panel.recipe.steps[0].enabled is False

    def test_checking_a_step_enables_its_precursors(self, window):
        from qtpy.QtWidgets import QCheckBox

        panel = window._recipe_panel
        panel.add_step("mesh")
        panel.add_step("modal")
        panel.add_step("response_spectrum")
        for index in range(3):
            checkbox = panel._table.cellWidget(index, 0).findChild(QCheckBox)
            checkbox.setChecked(False)
        assert [step.enabled for step in panel.recipe] == [False, False, False]
        checkbox = panel._table.cellWidget(2, 0).findChild(QCheckBox)
        checkbox.setChecked(True)
        assert [step.enabled for step in panel.recipe] == [True, True, True]

    def test_apply_run_outcomes_persists_the_tick(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh")
        panel.add_step("mesh")
        panel.apply_run_outcomes([(0, "mesh", "ok"), (1, "mesh", "ok")])
        assert panel.recipe.steps[0].status == "ok"
        assert panel.recipe.steps[0].enabled is False
        assert panel.recipe.steps[1].status == "ok"
        assert panel.recipe.steps[1].enabled is False
        assert panel._table.item(0, 2).text() == "\u2713"


class TestRunOrder:
    """A recipe runs its steps in order, and the panel shows the workflow."""

    def test_a_two_step_recipe_runs_both(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh", params={"create_shells": False})
        panel.add_step("mesh", params={"create_shells": True})
        window._actions["recipe.run"].trigger()
        _await(window)

        assert [view.key for view in window._session.views.views()] == [
            "unprocessed",
            "processed",
            "meshed",
        ]

    def test_a_run_marks_each_step_with_a_success_tick(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh", params={"create_shells": False})
        panel.add_step("mesh", params={"create_shells": True})
        window._actions["recipe.run"].trigger()
        _await(window)

        assert panel._table.columnCount() == 3
        assert panel._table.item(0, 2).text() == "\u2713"  # green tick
        assert panel._table.item(1, 2).text() == "\u2713"

    def test_an_empty_recipe_warns_rather_than_running(self, window):
        window._on_recipe_run()
        assert "The recipe has no steps" in window._message_log.toPlainText()


class TestResultViews:
    """A recipe's results reach the viewport, not just the log (P30 phase C).

    Both ``cases`` verbs return the same in-memory archive the **Analysis ▸ Run**
    path serves, so one registration path covers a solve and a combination
    reduction alike — and nothing is written to disk to view either.
    """

    def test_a_solved_case_registers_a_results_view(self, window):
        panel = window._recipe_panel
        panel.add_step("mesh", params={"create_shells": False})
        panel.add_step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}})
        window._actions["recipe.run"].trigger()
        _await(window)

        view = window._session.views.get("results:DEAD")
        assert view is not None, "the solved case registered no view"
        assert view.kind == "results"
        assert window._session.views.results("results:DEAD") is not None
        assert view.source == "Recipe: Static cases \u00b7 DEAD"
        assert "Static cases: 1 case(s) added as views." in window._message_log.toPlainText()

    def test_a_combination_registers_its_own_view(self, window):
        """``combine`` returns a case's archive shape, so it registers the same way."""
        from fea_toolkit.model.sap_data import LoadCombination, LoadCombinationEntry

        window._model.load_combinations = {
            "GRAV": LoadCombination(
                name="GRAV",
                combo_type="Linear Add",
                entries=[LoadCombinationEntry(name="DEAD", factor=1.0, kind="case")],
            )
        }
        panel = window._recipe_panel
        panel.add_step("mesh", params={"create_shells": False})
        panel.add_step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}})
        panel.add_step("combine", params={"combinations": ["GRAV"]})
        window._actions["recipe.run"].trigger()
        _await(window)

        keys = [view.key for view in window._session.views.views()]
        assert "results:DEAD" in keys
        assert "results:GRAV" in keys

    def test_an_empty_archive_registers_no_view(self, window):
        """An archive with no case in it adds nothing — it does not invent a view."""
        assert window._show_results_result("Static cases", {}) == []
        assert window._session.views.get("results:") is None

    def test_a_cancelled_run_that_solved_nothing_is_not_a_failure(self, window):
        """A cancelled run's empty result is explained by cancelling, not as an error.

        The two are genuinely different: a case that ran and threw is a *failure*,
        while one the cancellation stopped from ever being attempted is not — the
        distinction ``run_case_set`` draws.  Here the same empty archive is
        reported on one run and passed over on the other.
        """
        from fea_toolkit.workflow import CASES, RecipeRun, StepResult

        def empty_result() -> RecipeRun:
            run = RecipeRun()
            run.results.append(StepResult(kind=CASES, label="Static cases", payload={}))
            return run

        window._recipe_finished((empty_result(), []))
        assert "the run produced no results" in window._message_log.toPlainText()

        window._message_log.clear()
        cancelled = empty_result()
        cancelled.cancelled = True
        window._recipe_finished((cancelled, []))

        log = window._message_log.toPlainText()
        assert "The recipe was cancelled." in log
        assert "produced no results" not in log

    def test_an_rs_result_registers_a_deformed_shape_view(self, window):
        """An RS result with a displacement field becomes a results view, like static."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.analysis.base import AnalysisResult
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mesh = preprocess_model(make_sample_model(), {"split_elements": True})
        window._session.store.set_preprocessed(mesh)
        node_tags = [node.node_tag for node in mesh.nodes.values()]
        field = dict.fromkeys(node_tags, (0.001, 0.0, 0.0))

        payload = AnalysisResult(
            name="ResponseSpectrum",
            analysis_type="ResponseSpectrumAnalysis",
            data={
                "rs_result": {
                    "base_shear_cqc": 120.0,
                    "base_shear_srss": 130.0,
                    "base_shear_total": 160.0,
                    "base_moment_cqc": 140.0,
                    "base_moment_srss": 150.0,
                },
                "direction": "X",
                "node_displacements": field,
            },
        )
        window._show_rs_result("Response spectrum (X)", payload)

        view = window._session.views.get("results:RS-X")
        assert view is not None, "the RS result registered no deformed-shape view"
        assert view.kind == "results"
        assert view.parent == "table:response-spectrum-(x)", "RS view should nest under its table"
        repository = window._session.views.results("results:RS-X")
        assert repository.has_displacements("RS-X")
        assert repository.nodal_displacements("RS-X")

    def test_a_modal_result_registers_a_view_per_mode(self, window):
        """A modal result with shapes becomes one deformed-shape view per mode."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.analysis.base import AnalysisResult
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mesh = preprocess_model(make_sample_model(), {"split_elements": True})
        window._session.store.set_preprocessed(mesh)
        node_tags = [node.node_tag for node in mesh.nodes.values()]
        shapes = {
            0: dict.fromkeys(node_tags, (0.001, 0.0, 0.0)),
            1: dict.fromkeys(node_tags, (0.0, 0.002, 0.0)),
        }

        payload = AnalysisResult(
            name="Modal",
            analysis_type="ModalAnalysis",
            data={
                "modal": {
                    "periods": [1.0, 0.5],
                    "frequencies": [1.0, 2.0],
                    "modal_props": {
                        "partiMassRatiosMX": [0.8, 0.1],
                        "partiMassRatiosMY": [0.0, 0.0],
                        "partiMassRatiosMZ": [0.0, 0.0],
                    },
                },
                "shapes": shapes,
            },
        )
        window._show_modal_result("Modal", payload)

        for key in ("results:Mode-1", "results:Mode-2"):
            view = window._session.views.get(key)
            assert view is not None, f"{key} registered no mode-shape view"
            assert view.kind == "results"
            assert view.parent == "table:modal", f"{key} should nest under the modal table"
            assert window._session.views.results(key).has_displacements(key.split(":")[1])

    def test_animate_toggle_starts_and_stops_the_timer(self, window):
        """Results ▸ Animate starts a timer over the active view's displacement field."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.analysis.base import AnalysisResult
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mesh = preprocess_model(make_sample_model(), {"split_elements": True})
        window._session.store.set_preprocessed(mesh)
        node_tags = [node.node_tag for node in mesh.nodes.values()]
        field = dict.fromkeys(node_tags, (0.001, 0.0, 0.0))

        payload = AnalysisResult(
            name="ResponseSpectrum",
            analysis_type="ResponseSpectrumAnalysis",
            data={
                "rs_result": {
                    "base_shear_cqc": 120.0,
                    "base_shear_srss": 130.0,
                    "base_shear_total": 160.0,
                    "base_moment_cqc": 140.0,
                    "base_moment_srss": 150.0,
                },
                "direction": "X",
                "node_displacements": field,
            },
        )
        window._show_rs_result("Response spectrum (X)", payload)

        window._on_animate_toggled(True)
        assert window._anim_timer is not None
        assert window._anim_timer.isActive()
        assert window._anim_displacements

        window._on_animate_toggled(False)
        assert not window._anim_timer.isActive()

    def test_a_nonlinear_dynamic_result_steps_through_its_history(self, window):
        """An NLD result animates per recorded step, not as a single field."""
        import numpy as np

        from fea_toolkit.analysis.base import AnalysisResult

        node_ids = list(window._model.nodes)
        node_tags = [window._model.nodes[nid].node_tag for nid in node_ids]
        n_steps = 5
        # A decaying history whose peak (0.008) is mid-record and whose final
        # value (0.0001) is near zero — so a last-frame amplification is caught.
        envelope = np.array([0.0, 0.004, 0.008, 0.002, 0.0001])
        history = {
            tag: np.column_stack(
                (
                    envelope,
                    np.zeros(n_steps),
                    np.zeros(n_steps),
                )
            )
            for tag in node_tags
        }
        payload = AnalysisResult(
            name="NonlinearDynamic",
            analysis_type="NonlinearDynamicAnalysis",
            data={
                "times": np.arange(n_steps) * 0.005,
                "displacements": history,
                "peak_displacement": 0.008,
                "converged_steps": n_steps,
                "direction": "X",
                "gm_file": "record.txt",
            },
        )
        window._show_nonlinear_dynamic_result("Nonlinear dynamic", payload)

        key = "table:nonlinear-dynamic"
        assert len(window._nld_frames[key]) == n_steps
        assert set(window._nld_frames[key].frame(0)) == set(node_ids)
        assert window._nld_times[key] == [0.0, 0.005, 0.01, 0.015, 0.02]
        # The table view carries a transient history, so Results ▸ Animate is
        # enabled even though it is not a displacement-field results view.
        assert window._actions["results.animate"].isEnabled() is True

        window._on_animate_toggled(True)
        assert window._anim_timer is not None and window._anim_timer.isActive()
        assert window._anim_step_mode is True
        assert len(window._anim_frames) == n_steps
        assert window._anim_times == window._nld_times[key]

        # The base scale must come from the peak frame (0.008 at step 2), not
        # the near-zero final frame (0.0001) — a last-frame scale is ~80x larger.
        fraction = window._deformed_scale.value() / 100.0
        last_frame_scale = window._viewer.deformed_amplification(
            window._nld_frames[key].frame(n_steps - 1), fraction
        )
        assert window._anim_base_scale < last_frame_scale

        window._on_animate_toggled(False)
        assert not window._anim_timer.isActive()

    def test_switching_views_stops_the_animation(self, window):
        """Switching to a different view stops a running animation and clears the toggle."""
        import numpy as np

        from fea_toolkit.analysis.base import AnalysisResult

        node_ids = list(window._model.nodes)
        node_tags = [window._model.nodes[nid].node_tag for nid in node_ids]
        n_steps = 5
        history = {
            tag: np.column_stack((np.full(n_steps, 0.001), np.zeros(n_steps), np.zeros(n_steps)))
            for tag in node_tags
        }
        payload = AnalysisResult(
            name="NonlinearDynamic",
            analysis_type="NonlinearDynamicAnalysis",
            data={
                "times": np.arange(n_steps) * 0.005,
                "displacements": history,
                "converged_steps": n_steps,
                "direction": "X",
                "gm_file": "record.txt",
            },
        )
        window._show_nonlinear_dynamic_result("Nonlinear dynamic", payload)

        # Trigger the toolbar toggle itself so the checked state is what a real
        # user action produces; the later uncheck must then reflect a
        # checked-to-unchecked transition, not a toggle that was never on.
        window._actions["results.animate"].setChecked(True)
        assert window._actions["results.animate"].isChecked() is True
        assert window._anim_timer is not None and window._anim_timer.isActive()

        # Switch to the unprocessed geometry view — the animation must stop and
        # the toggle must clear rather than keep drawing the old view's history.
        window._show_view(window._session.views.get("unprocessed"))

        assert not window._anim_timer.isActive()
        assert window._actions["results.animate"].isChecked() is False


class TestTableAndFigureViews:
    """P30 phase C: a check's table and a chart's figure reach the viewport."""

    def test_a_check_registers_a_table_view(self, window):
        panel = window._recipe_panel
        panel.add_step("check_connectivity")
        window._actions["recipe.run"].trigger()
        _await(window)

        view = window._session.views.get("table:connectivity")
        assert view is not None, "the check registered no table view"
        assert view.kind == "table"
        assert window._session.views.table("table:connectivity") is not None
        assert window._stack.currentWidget() is window._table_page

    def test_a_table_result_renders_its_cells(self, window):
        """The cells are the verb's own strings — the view never reformats them."""
        from fea_toolkit.workflow import Table

        table = Table(title="Probe", columns=("a", "b"), rows=(("1", "2"), ("3", "4")))
        window._show_table_result("Probe", table)

        view = window._session.views.get("table:probe")
        assert view is not None and view.kind == "table"
        assert window._session.views.table("table:probe") is table
        assert window._table_widget.rowCount() == 2
        assert window._table_widget.columnCount() == 2
        assert window._table_widget.item(0, 1).text() == "2"
        assert window._stack.currentWidget() is window._table_page

    def test_a_figure_result_renders_on_a_canvas(self, window):
        from matplotlib.figure import Figure

        figure = Figure()
        figure.gca().plot([0.0, 1.0], [0.0, 1.0])
        window._show_figure_result("Storey displacements", figure)

        view = window._session.views.get("figure:storey-displacements")
        assert view is not None and view.kind == "figure"
        assert window._session.views.figure("figure:storey-displacements") is figure
        assert window._stack.currentWidget() is window._figure_page
        assert window._figure_layout.count() == 1

    def test_table_and_figure_views_appear_in_the_tree(self, window):
        """A table/figure view registers in the **Views** group, not only its page."""
        from matplotlib.figure import Figure

        from fea_toolkit.workflow import Table

        window._show_table_result("Probe", Table(title="Probe", columns=("a",), rows=(("1",),)))
        assert window._tree_model.index_for("views", "Probe") is not None

        figure = Figure()
        figure.gca().plot([0.0, 1.0], [0.0, 1.0])
        window._show_figure_result("Storey displacements", figure)
        assert window._tree_model.index_for("views", "Storey displacements") is not None

    def test_switching_figures_reuses_canvases(self, window):
        """Each figure is embedded once; switching back reuses its canvas."""
        from matplotlib.figure import Figure

        fig_a = Figure()
        fig_a.gca().plot([0.0, 1.0], [0.0, 1.0])
        fig_b = Figure()
        fig_b.gca().plot([0.0, 1.0], [1.0, 0.0])

        window._show_figure_result("Alpha", fig_a)
        canvas_a = window._figure_canvases["figure:alpha"]
        window._show_figure_result("Beta", fig_b)
        canvas_b = window._figure_canvases["figure:beta"]

        assert canvas_a is not canvas_b
        assert len(window._figure_canvases) == 2

        # Switching back must re-attach the *same* canvas, not wrap the figure in
        # a fresh one (re-embedding a figure re-runs its layout and inflates the
        # legend/markers).
        window._show_figure_result("Alpha", fig_a)
        assert window._figure_canvases["figure:alpha"] is canvas_a
        assert window._figure_layout.count() == 1

    def test_replacing_a_figure_refreshes_its_canvas(self, window):
        """Re-running a figure under an existing key invalidates the cached canvas.

        The canvas caches the *figure object* it was built around; a rerun that
        registers a new figure under the same key must draw the new figure, not
        the stale cached one.
        """
        from matplotlib.figure import Figure

        fig_a = Figure()
        fig_a.gca().plot([0.0, 1.0], [0.0, 1.0])
        window._show_figure_result("Alpha", fig_a)
        canvas_a = window._figure_canvases["figure:alpha"]

        fig_b = Figure()
        fig_b.gca().plot([0.0, 1.0], [1.0, 0.0])
        window._show_figure_result("Alpha", fig_b)

        canvas_b = window._figure_canvases["figure:alpha"]
        assert canvas_b is not canvas_a
        assert getattr(canvas_b, "figure", None) is fig_b

    def test_a_pushover_result_registers_a_shape_view_per_direction(self, window):
        """Pushover directions become deformed-shape views nested under the first figure."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.analysis.base import AnalysisResult
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mesh = preprocess_model(make_sample_model(), {"split_elements": True})
        window._session.store.set_preprocessed(mesh)
        node_tags = [node.node_tag for node in mesh.nodes.values()]
        field = dict.fromkeys(node_tags, (0.004, 0.0, 0.0))

        payload = AnalysisResult(
            name="Pushover",
            analysis_type="PushoverAnalysis",
            data={
                "+X": {
                    "adrs": {"S_a": [0.0, 1.0], "S_d": [0.0, 0.1]},
                    "pp": {"S_dp": 0.1, "S_ap": 1.0, "S_dy": 0.05, "S_ay": 0.9},
                    "spectrum": {"T": [0.0, 1.0], "Sa": [1.0, 1.0]},
                    "results": {
                        "step": [0, 1, 2],
                        "control_disp": [0.0, 0.1, 0.2],
                        "base_shear": [0.0, 5.0, 10.0],
                        "lateral_load_type": "uniform",
                        "step_results": [
                            {
                                "step": 1,
                                "frame_forces": {},
                                "shell_forces": {},
                                "node_displacements": dict.fromkeys(node_tags, (0.002, 0.0, 0.0)),
                            },
                            {
                                "step": 2,
                                "frame_forces": {},
                                "shell_forces": {},
                                "node_displacements": field,
                            },
                        ],
                    },
                },
            },
        )
        window._show_pushover_result("Pushover", payload)

        assert window._session.views.get("table:pushover") is not None, (
            "pushover registered no summary table"
        )
        figure = window._session.views.get("figure:pushover-plusx-uniform")
        assert figure is not None, "pushover registered no ADRS figure"
        view = window._session.views.get("results:Pushover+X-uniform")
        assert view is not None, "pushover registered no deformed-shape view"
        assert view.parent == "table:pushover"
        assert window._session.views.results("results:Pushover+X-uniform").has_displacements(
            "Pushover+X-uniform"
        )

        # The recorded step history is threaded through, so the animation steps
        # through it rather than oscillating a single field.
        window._session.views.set_active("results:Pushover+X-uniform")
        window._on_animate_toggled(True)
        assert window._anim_step_mode is True
        assert len(window._anim_frames) == 2
        window._on_animate_toggled(False)
        assert not window._anim_timer.isActive()


def test_the_config_editor_emits_only_the_keys_a_user_changes(qapp):
    """The structured config editor writes back a minimal override dict."""
    from fea_toolkit.gui.views.config_editor import ConfigEditor
    from fea_toolkit.workflow import BUILDER_CONFIG_KEYS

    editor = ConfigEditor(BUILDER_CONFIG_KEYS, {})
    assert editor.value() == {}

    # Collapsible: closed by default, so a long manifest never crowds its dialog.
    assert editor.isCheckable() is True
    assert editor.isChecked() is False
    assert editor._scroll.isHidden() is True

    # Each key's help is a tooltip (hover), not an inline label.
    assert editor._widgets["verbose"].toolTip() == BUILDER_CONFIG_KEYS["verbose"].help

    editor._widgets["verbose"].setChecked(True)
    assert editor.value() == {"verbose": True}

    editor._widgets["element_type"].setCurrentText("dispBeamColumn")
    assert editor.value() == {"verbose": True, "element_type": "dispBeamColumn"}

    # Expanding the group reveals the (height-capped) scroll area.
    editor.setChecked(True)
    assert editor._scroll.isHidden() is False
    assert editor._scroll.maximumHeight() > 0


def test_the_config_editor_preserves_unlisted_config_keys(qapp):
    """Keys the manifest does not curate survive a round-trip through the editor."""
    from fea_toolkit.gui.views.config_editor import ConfigEditor
    from fea_toolkit.workflow import BUILDER_CONFIG_KEYS

    editor = ConfigEditor(BUILDER_CONFIG_KEYS, {"stiffness_factors": {"beam": 0.35}})
    assert editor.value() == {"stiffness_factors": {"beam": 0.35}}

    # Editing a curated key must not drop the unlisted option.
    editor._widgets["verbose"].setChecked(True)
    assert editor.value() == {
        "stiffness_factors": {"beam": 0.35},
        "verbose": True,
    }


def test_the_config_editor_deep_copies_unlisted_config_keys(qapp):
    """Mutating a returned unlisted option must not reach ``self._current``."""
    from fea_toolkit.gui.views.config_editor import ConfigEditor
    from fea_toolkit.workflow import BUILDER_CONFIG_KEYS

    editor = ConfigEditor(BUILDER_CONFIG_KEYS, {"stiffness_factors": {"beam": 0.35}})

    returned = editor.value()
    returned["stiffness_factors"]["beam"] = 0.9
    assert editor.value() == {"stiffness_factors": {"beam": 0.35}}
    assert editor._current["stiffness_factors"]["beam"] == 0.35


def test_the_config_editor_preserves_tolerances_below_six_decimals(qapp):
    """``solver_test_tol=1e-8`` stays exact through ``editor.value()``."""
    from fea_toolkit.gui.views.config_editor import ConfigEditor
    from fea_toolkit.workflow import BUILDER_CONFIG_KEYS

    editor = ConfigEditor(BUILDER_CONFIG_KEYS, {"solver_test_tol": 1e-8})

    # Changing an unrelated field must not clobber the tolerance.
    editor._widgets["verbose"].setChecked(True)
    assert editor.value()["verbose"] is True
    assert editor.value()["solver_test_tol"] == 1e-8


def test_the_config_editor_preserves_choice_type(qapp):
    """A choice key reads back the declared-typed entry, not its text form."""
    from fea_toolkit.gui.views.config_editor import ConfigEditor
    from fea_toolkit.workflow import ParamSpec

    manifest = {"levels": ParamSpec(default=1, type=int, choices=(1, 2, 3), help="levels")}
    editor = ConfigEditor(manifest, {})
    editor._widgets["levels"].setCurrentIndex(2)
    value = editor.value()["levels"]
    assert value == 3
    assert type(value) is int


def test_the_step_list_has_a_help_context_menu(window):
    """Right-click help is wired to the recipe step list."""
    from qtpy.QtCore import Qt

    assert window._recipe_panel._table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


def test_the_verb_help_dialog_shows_the_verbs_surface(qapp):
    """The per-verb help dialog is rendered straight from the StepSpec."""
    from qtpy.QtWidgets import QTableWidget

    from fea_toolkit.gui.views.verb_help_dialog import VerbHelpDialog
    from fea_toolkit.workflow import STEP_SPECS

    spec = STEP_SPECS["run_static"]
    dialog = VerbHelpDialog("run_static", spec)

    table = dialog.findChild(QTableWidget)
    assert table is not None
    assert table.rowCount() == len(spec.params)
    assert table.columnCount() == 4
    assert table.item(0, 0).text() == "cases"
    assert "run_static" in dialog.windowTitle()


def test_the_verb_help_dialog_links_docs_and_dois(qapp):
    """A verb's docs / citations render as clickable external links."""
    from qtpy.QtWidgets import QLabel

    from fea_toolkit.gui.views.verb_help_dialog import VerbHelpDialog
    from fea_toolkit.workflow import STEP_SPECS

    dialog = VerbHelpDialog("pushover", STEP_SPECS["pushover"])

    labels = dialog.findChildren(QLabel)
    docs_label = next(label for label in labels if "Documentation" in label.text())
    cites_label = next(label for label in labels if "Citations" in label.text())
    assert "pushover_analysis" in docs_label.text()
    assert "10.14359/1283" in cites_label.text()
    assert docs_label.openExternalLinks()
    assert cites_label.openExternalLinks()


class TestStepDialog:
    """The step inspector moved from the Recipe panel to a modal dialog (P33)."""

    def _dialog(self, verb="mesh"):
        from fea_toolkit.gui.views.step_dialog import StepDialog
        from fea_toolkit.workflow import Step

        return StepDialog(Step(verb=verb))

    def test_only_non_default_params_are_emitted(self, qapp):
        dialog = self._dialog("mesh")
        result = dialog.edited_step()
        assert result.verb == "mesh"
        assert result.params == {}  # every mesh param was left at its default
        assert result.selection is None
        assert result.optional is False

    def test_an_edit_reaches_the_resulting_step(self, qapp):
        dialog = self._dialog("mesh")
        dialog._widgets["split_slabs_at_walls"].setChecked(True)
        assert dialog.edited_step().params == {"split_slabs_at_walls": True}

    def test_the_selection_is_parsed_from_the_field(self, qapp):
        from fea_toolkit.model.selection import Selection

        dialog = self._dialog("scale_sections")
        dialog._selection_field.setText("section=brick wall")
        assert dialog.edited_step().selection == Selection(sections=["brick wall"])

    def test_an_invalid_selection_gates_ok(self, qapp):
        from qtpy.QtWidgets import QDialogButtonBox

        dialog = self._dialog("mesh")
        dialog._selection_field.setText("section")
        ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False

    def test_the_optional_flag_is_recorded(self, qapp):
        dialog = self._dialog("mesh")
        dialog._optional_box.setChecked(True)
        assert dialog.edited_step().optional is True

    def test_an_optional_int_reads_none_for_all(self, qapp):
        """``n_modes`` defaults to 'all' (None); a chosen value reads back as an int."""
        dialog = self._dialog("response_spectrum")
        widget = dialog._widgets["n_modes"]
        assert widget.value() == 0  # 'all'
        assert dialog._param_value("n_modes") is None
        widget.setValue(5)
        assert dialog._param_value("n_modes") == 5

    def test_float_precision_survives_an_unrelated_edit(self, qapp):
        """A high-precision float keeps its full value when only the optional flag changes."""
        from fea_toolkit.gui.views.step_dialog import StepDialog
        from fea_toolkit.workflow import Step

        factor = 0.123456789012345
        dialog = StepDialog(Step(verb="scale_sections", params={"factor": factor}))
        dialog._optional_box.setChecked(True)
        result = dialog.edited_step()
        assert result.optional is True
        assert result.params["factor"] == factor

    def test_choice_param_preserves_declared_type(self, qapp, monkeypatch):
        """A choice parameter reads back the declared-typed entry, not its text form."""
        import fea_toolkit.gui.views.step_dialog as step_dialog_mod
        from fea_toolkit.workflow import ParamSpec, Step, StepSpec

        fake_spec = StepSpec(
            verb="combine",
            run=lambda ctx, step: [],
            params={
                "envelope_mode": ParamSpec(default=1, type=int, choices=(1, 2, 3), help="mode")
            },
            kind="cases",
        )
        monkeypatch.setattr(step_dialog_mod, "STEP_SPECS", {"combine": fake_spec})

        dialog = step_dialog_mod.StepDialog(Step(verb="combine"))
        combo = dialog._widgets["envelope_mode"]
        combo.setCurrentIndex(2)
        value = dialog._param_value("envelope_mode")
        assert value == 3
        assert type(value) is int

    def test_a_noneable_choice_defaults_to_none_not_the_first_choice(self, qapp):
        """A ``choices`` + ``allow_none`` param reads ``None`` at its default."""
        dialog = self._dialog("mesh")
        widget = dialog._widgets["panel_zone_model"]
        assert dialog._param_value("panel_zone_model") is None
        widget.setCurrentIndex(1)
        assert dialog._param_value("panel_zone_model") == "krawinkler_gupta"

    def test_run_static_cases_uses_the_structured_editor_when_a_model_is_open(self, qapp):
        """With a model, the ``cases`` param is a structured editor, not a literal."""
        from fea_toolkit.gui.views.cases_editor import CasesEditor
        from fea_toolkit.gui.views.step_dialog import StepDialog
        from fea_toolkit.workflow import Step

        dialog = StepDialog(Step(verb="run_static"), model_data=_model())
        assert isinstance(dialog._widgets["cases"], CasesEditor)


class TestCasesEditor:
    """The ``run_static`` cases picker — check cases, edit per-pattern factors."""

    @staticmethod
    def _editor(current=None):
        from fea_toolkit.gui.views.cases_editor import CasesEditor

        static = {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.2}}
        return CasesEditor(static, current)

    def test_untouched_returns_the_solve_all_default(self, qapp):
        """Every case checked, factors unchanged → ``{}`` (the verb's sentinel)."""
        assert self._editor().value() == {}

    def test_unchecking_a_case_narrows_the_solve(self, qapp):
        from qtpy.QtCore import Qt

        editor = self._editor()
        editor._list.item(1).setCheckState(Qt.CheckState.Unchecked)  # drop WIND
        assert editor.value() == {"DEAD": {"DEAD": 1.0}}

    def test_an_edited_factor_survives(self, qapp):
        editor = self._editor()
        editor._list.setCurrentRow(1)  # WIND
        editor._table.cellWidget(0, 1).setValue(1.5)
        assert editor.value() == {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.5}}

    def test_a_seeded_subset_is_preserved(self, qapp):
        editor = self._editor({"WIND": {"WIND": 1.2}})
        assert editor.value() == {"WIND": {"WIND": 1.2}}


def test_show_beams_toggle_hides_frames(window):
    """P33: a frame-visibility toggle joins Show nodes and Show shells."""
    action = window._actions["view.show_frames"]
    assert action.isCheckable() is True
    assert action.isChecked() is True
    action.setChecked(False)
    assert action.isChecked() is False  # toggling does not raise on a live view


def test_extruded_shapes_toggle_is_off_by_default_and_re_renders(window):
    """P34: 'Extruded shapes' starts unchecked and toggling updates the display."""
    action = window._actions["view.show_extruded"]
    assert action.isCheckable() is True
    assert action.isChecked() is False

    action.setChecked(True)
    assert window._display.show_extruded is True

    action.setChecked(False)
    assert window._display.show_extruded is False


class TestDetectStoriesDialog:
    """The mesh dialog's storey-detection flag is format-aware."""

    def _dialog_for(self, program):
        from fea_toolkit.gui.views.step_dialog import StepDialog
        from fea_toolkit.workflow import Step

        model = _model()
        model.program = program
        return StepDialog(Step(verb="mesh"), model_data=model)

    def test_defaults_on_for_sap(self, window):
        dialog = self._dialog_for("SAP2000")
        widget = dialog._widgets["detect_stories"]
        assert widget.isChecked() is True
        assert widget.isEnabled() is True
        assert dialog.edited_step().params.get("detect_stories") is True

    def test_reopening_keeps_an_explicit_uncheck_for_sap(self, window):
        from fea_toolkit.gui.views.step_dialog import StepDialog
        from fea_toolkit.workflow import Step

        model = _model()
        model.program = "SAP2000"

        # Unchecking an otherwise-untouched SAP2000 step must persist as False
        # (not be re-checked on reopen), even though False equals the declared
        # default and would otherwise be dropped from the emitted params.
        dialog = StepDialog(Step(verb="mesh"), model_data=model)
        dialog._widgets["detect_stories"].setChecked(False)
        assert dialog.edited_step().params.get("detect_stories") is False

        reopened = StepDialog(Step(verb="mesh", params={"detect_stories": False}), model_data=model)
        assert reopened._widgets["detect_stories"].isChecked() is False
        assert reopened.edited_step().params.get("detect_stories") is False

    def test_defaults_off_for_safe(self, window):
        dialog = self._dialog_for("SAFE")
        widget = dialog._widgets["detect_stories"]
        assert widget.isChecked() is False
        assert widget.isEnabled() is True
        assert "detect_stories" not in dialog.edited_step().params

    def test_greyed_out_for_etabs(self, window):
        dialog = self._dialog_for("ETABS")
        widget = dialog._widgets["detect_stories"]
        assert widget.isChecked() is False
        assert widget.isEnabled() is False
        assert "detect_stories" not in dialog.edited_step().params
