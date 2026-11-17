"""The recipe authoring + run-lifecycle controller.

The main window delegates the whole workflow surface — the Recipe dock, the
menu presets that append a step, and the recipe run itself — to this controller.
It is Qt-aware (it owns the dock, the panel and the worker) and holds a
reference to the window for the collaborators it does not own: the session, the
log, the progress bar, the action registry and the viewport.  The run lifecycle
lives here end-to-end: snapshot, worker wiring, the completion/failure handlers
that adopt the document a run produced and register its results, and the
restoration of the UI once the worker stops.
"""

import gc
from typing import Any, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDockWidget, QFileDialog

from .worker import TaskWorker

__all__ = ["RecipeController"]

#: Once-only guard for :func:`_freeze_gc_once`.  A one-element list rather than a
#: bool, so marking it does not need a ``global`` rebind (ruff PLW0603).
_GC_FROZEN: list = []


def _freeze_gc_once() -> None:
    """Put this process's live objects beyond the collector's reach, once.

    ``gc.freeze()`` moves everything alive *now* — Qt, VTK, the window, the
    renderer — into the permanent generation, so no later collection looks at
    it; objects created afterwards (the preprocessed topology and its
    ``MeshModel``) stay collectable.  See ``docs/dev_notes.md`` →
    *Copy-on-write replaces the model deepcopy* for why this matters on a
    worker thread.
    """
    if _GC_FROZEN:
        return
    _GC_FROZEN.append(True)
    gc.freeze()


class RecipeController:
    """Own the recipe dock, the menu presets and the recipe run lifecycle."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._worker: Any = None
        self._recipe_panel: Any = None
        self._dock: Any = None
        #: The run's collected log lines, flushed on the GUI thread at the end.
        self._recipe_messages: Optional[list] = None
        #: The window's named selections as they were when the run started, used
        #: to merge back only what the run changed on completion.
        self._recipe_named_snapshot: Optional[dict] = None
        #: The store the run was started against, used to reject a stale run.
        self._recipe_starting_store: Any = None
        #: What the run's log line should say is happening, set by presets.
        self._pending_label: Optional[str] = None

    # ── Accessors ───────────────────────────────────────────────────

    @property
    def panel(self) -> Any:
        """The Recipe panel, or ``None`` before the dock is built."""
        return self._recipe_panel

    @property
    def worker(self) -> Any:
        """The in-flight worker, or ``None`` when no run is running."""
        return self._worker

    @property
    def dock(self) -> Any:
        """The Recipe dock widget."""
        return self._dock

    def is_running(self) -> bool:
        """Whether a recipe run is currently in flight."""
        return self._worker is not None and self._worker.isRunning()

    def set_pending_label(self, label: str) -> None:
        """Set what the next run's log line should say is happening."""
        self._pending_label = label

    # ── Dock ────────────────────────────────────────────────────────

    def build_dock(self) -> None:
        """Build the Recipe dock — the workflow as an ordered list of steps.

        It sits in the bottom area beside the Message Log, because running a
        recipe is precisely what the log narrates.  ``RecipePanel`` needs Qt, so
        it is imported here rather than at module scope (lazy-import policy).
        """
        from ..views.recipe_panel import RecipePanel

        self._recipe_panel = RecipePanel(self._window)
        self._recipe_panel.changed.connect(self.set_actions_enabled)
        # The panel is a *view* over the session's recipe (panel-as-view, P48
        # option A'): once bound it edits ``session.recipe`` directly — the
        # single object the window runs and saves — so there is no write-back.
        self._recipe_panel.set_session(self._window._session)
        self._recipe_panel.copied.connect(self._window.log)
        self._dock = QDockWidget("Recipe", self._window)
        self._dock.setObjectName("dock_recipe")
        self._dock.setWidget(self._recipe_panel)
        self._window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._dock)

    # ── Presets ─────────────────────────────────────────────────────

    def add_step(self, step: Any) -> int:
        """Append *step* to the recipe and return its index."""
        return self._recipe_panel.add_step(
            step.verb, step.selection, step.params, optional=step.optional
        )

    def preset_step(self, step: Any, label: str) -> None:
        """Append *step* to the recipe and run it — the menu-preset pattern.

        The Model-menu Mesh entry and the Analysis-menu analysis entries are all
        presets over the recipe: compose a step in a dialog, append it, and run
        the recipe, so an operation is a visible, editable step like any other.

        Args:
            step: The step to append and run.
            label: What the Message Log should say is happening.
        """
        self.add_step(step)
        from ...workflow import step_to_command

        self._window.log(f"command: {step_to_command(step)}")
        self._pending_label = label
        self.run()

    def ensure_modal_precursor(self, default_name: str = "") -> str:
        """Ensure an *enabled* ``modal`` step precedes the spectrum/pushover step.

        ``response_spectrum`` and ``pushover`` declare
        ``needs=("geometry", "modal")``, so a preset for either must run after a
        modal step has produced the modal result.  A modal step that already ran
        is unchecked (auto-"No" after a run), so it is re-enabled — and, through
        the ``set_enabled`` cascade, its geometry precursor too — rather than
        appending a duplicate.  When the recipe has no modal step, one is added
        with ``name`` set to *default_name* (the model's modal case).

        Args:
            default_name: The model's preferred modal-case name, or ``""``.

        Returns:
            The ``name`` of the modal step now enabled (or added).
        """
        from ...workflow import STEP_SPECS

        panel = self._recipe_panel
        fallback = STEP_SPECS["modal"].params["name"].default

        def _modal_name(step: Any) -> str:
            return (step.params or {}).get("name") or fallback

        chosen = None
        for index, step in enumerate(panel.recipe.steps):
            if step.verb != "modal":
                continue
            if chosen is None:
                chosen = index
            if default_name and _modal_name(step) == default_name:
                chosen = index
                break
        if chosen is not None:
            if not panel.recipe.steps[chosen].enabled:
                panel.recipe.set_enabled(chosen, True)
            return _modal_name(panel.recipe.steps[chosen])

        name = default_name or fallback
        panel.add_step("modal", params={"name": name} if name != fallback else None)
        return name

    # ── Run lifecycle ──────────────────────────────────────────────

    def run(self) -> None:
        """Run the panel's steps on a worker.

        The recipe is snapshotted before the worker starts (through its own
        serialisation), so editing the panel mid-run cannot change what is being
        run.  The steps' log lines are collected and flushed on the GUI thread
        once the run ends — a worker must not touch widgets.
        """
        from ...workflow import Recipe, run_recipe

        store = self._window._session.store
        if store is None:
            self._window.log("Open a SAP2000 model before running a recipe.", "warn")
            return
        if not len(self._window._session.recipe):
            self._window.log(
                "The recipe has no steps \u2014 use Model \u25b8 Split elements / "
                "Mesh areas, or add one from the Recipe menu.",
                "warn",
            )
            return
        if self._worker is not None and self._worker.isRunning():
            self._window.log("A run is already in progress.", "warn")
            return

        recipe = Recipe.from_dict(self._window._session.recipe.to_dict())
        model_data = store.raw()
        from .settings import load_opensees_setting

        opensees_python = load_opensees_setting()
        starting_mesh = store.preprocessed()
        messages: list = []
        self._recipe_messages = messages
        label = self._pending_label or f"Running recipe ({len(recipe)} steps)"
        self._pending_label = None

        # One PipelineLog for the run: recipe progress (``context.log``) and the
        # structured analysis events share it, and its sink collects them into
        # ``messages`` for the GUI-thread flush in ``_finished``.
        from ...io.log import PipelineLog, render_entry

        pipeline_log = PipelineLog(verbose=True, collect=False, _stdout=False)

        def _collect(entry: dict) -> None:
            if entry["level"] != "DEBUG":
                messages.append(render_entry(entry))

        pipeline_log.add_sink(_collect)

        self._window._set_model_actions_enabled(False)
        self._window._progress.setRange(0, 0)  # busy: a recipe reports no fine-grained progress
        self._window._progress.setVisible(True)
        self._window._actions["analysis.stop"].setEnabled(True)
        self._window.log(f"{label} \u2026")

        # Per-step status flows back to the panel through the worker's existing
        # ``progress`` signal (current=step index, label="<verb>:<status>"), so
        # the Status column updates live without the worker touching widgets.
        self._recipe_panel.reset_statuses()
        reporter: list = [None]
        # Snapshot the window's named selections on the GUI thread, so a recipe
        # step referencing a GUI-authored selection resolves it without the
        # worker touching the live store.  The snapshot is also kept so the
        # completion handler can merge back only what the run changed.  The store
        # the run starts from is likewise remembered, so a run whose document was
        # switched away while in flight is rejected.
        named_snapshot = dict(self._window._session.named_selections)
        self._recipe_named_snapshot = named_snapshot
        self._recipe_starting_store = store

        def task(should_cancel):
            def on_step(index, verb, status):
                if reporter[0] is not None:
                    reporter[0](index, len(recipe), f"{verb}:{status}")

            def on_progress(index, name, total):
                if reporter[0] is not None:
                    reporter[0](index, total, f"case:{name}")

            run = run_recipe(
                recipe,
                model_data,
                model=starting_mesh,
                cancel=should_cancel,
                pipeline_log=pipeline_log,
                on_step=on_step,
                on_progress=on_progress,
                opensees_python=opensees_python,
                named_selections=named_snapshot,
            )
            return run, messages

        _freeze_gc_once()
        worker = TaskWorker(task, parent=self._window)
        reporter[0] = worker.report_progress
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._finished)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._ended)
        self._worker = worker
        worker.start()

    def run_steps(self, steps: list, label: str) -> None:
        """Append *steps* to the recipe and run it, narrating *label*."""
        for step in steps:
            self.add_step(step)
        self._pending_label = label
        self.run()

    def _on_progress(self, index: int, total: int, label: str) -> None:
        """Update the UI for a worker progress event — a step's status or a case.

        The recipe worker multiplexes two streams over its one progress signal:
        a step boundary is ``"<verb>:<status>"`` (updates the Recipe panel's
        Status column), and a solve reporting the case it is about to run is
        ``"case:<name>"`` (drives the determinate bar and a live log line).
        """
        if label.startswith("case:"):
            case_name = label[len("case:") :]
            self._window._progress.setRange(0, max(int(total), 1))
            self._window._progress.setValue(int(index))
            if case_name:
                self._window.log(f"Solving {case_name} ({index}/{total}) \u2026")
            return
        _verb, _sep, status = label.partition(":")
        self._recipe_panel.set_step_status(index, status or "running")

    def _finished(self, payload: Any) -> None:
        """Report what the recipe produced and register each result as a view.

        A ``cases`` result carries the same in-memory archive the **Analysis ▸
        Run** path serves, so it becomes one view per case, exactly as there.
        A document-lifecycle step (``open`` / ``close`` / ``build_model``) is
        adopted into the window first, so every result registers against the
        document the user is now looking at; when the document is *not* replaced,
        only the named selections the run changed are merged back, so selections
        the user edited while it ran survive.  A run whose document was switched
        away while in flight is rejected rather than applied to the document now
        open.  Cancellation and failure stay distinct: a *cancelled* run
        legitimately holds only the cases it solved before stopping, so no result
        is reported as failed.

        Args:
            payload: ``(RecipeRun, [log lines])`` from the worker.
        """
        from ...workflow import CASES, FIGURE, TABLE
        from ...workflow.steps import IDA, MODAL, NLD, PUSHOVER, RS, SESSION

        run, messages = payload
        # A run started for one document must not install its mesh, selections or
        # results onto a document the user switched to while it was in flight
        # (``open_path`` replaced the store): reject the stale run's outcomes
        # rather than affecting the document now open.
        if not self._store_is_current():
            self._window.log(
                "Recipe run finished for a document that is no longer open; "
                "its outcomes were discarded.",
                "warn",
            )
            return
        # A document-lifecycle step (``open`` / ``close`` / ``build_model``)
        # replaced or closed the working document; adopt it *before* registering
        # the results below, so a meshing/analysis result attaches to the
        # document the user now sees rather than the stale one (P35 follow-up).
        if run.document_reset:
            self._adopt_document(run.document)
        elif run.document is not None:
            # The document itself was not replaced, but a ``select`` step (or any
            # verb that mutates the document's named selections) left the store
            # in place while changing the selection mapping — merge only the
            # entries the run changed back into the window's store, so selections
            # the user added or edited while the run was in flight survive.
            self._merge_named_selections(run.document)
        self._window._session.last_run = run
        # Persist each step's outcome onto the recipe (a step that ran "ok" is
        # unchecked), so the run state survives save → reopen.
        self._recipe_panel.apply_run_outcomes(run.step_status)
        for message in messages:
            self._window.log(message)
        presenter = self._window._results_presenter
        for result in run.results:
            if result.kind == "geometry":
                presenter.show_geometry_result(result.label, result.payload)
            elif result.kind == SESSION:
                if result.payload:
                    self._window.log(f"{result.label}: {result.payload}")
                else:
                    self._window.log(result.label)
            elif result.kind == CASES:
                added = presenter.show_results_result(result.label, result.payload)
                if not added and not run.cancelled:
                    self._window.log(f"{result.label}: the run produced no results.", "warn")
            elif result.kind == MODAL:
                presenter.show_modal_result(result.label, result.payload)
            elif result.kind == PUSHOVER:
                presenter.show_pushover_result(result.label, result.payload)
            elif result.kind == RS:
                presenter.show_rs_result(result.label, result.payload)
            elif result.kind == NLD:
                presenter.show_nonlinear_dynamic_result(result.label, result.payload)
            elif result.kind == IDA:
                presenter.show_ida_result(result.label, result.payload)
            elif result.kind == TABLE:
                presenter.show_table_result(result.label, result.payload)
            elif result.kind == FIGURE:
                presenter.show_figure_result(result.label, result.payload)
        for index, verb, message in run.failures:
            self._window.log(
                f"Step {index} ({verb}) failed and is optional \u2014 {message}", "warn"
            )
        if run.cancelled:
            self._window.log("The recipe was cancelled.", "warn")
        self._window._set_analysis_actions_enabled()

    def _store_is_current(self) -> bool:
        """Whether the recipe run's document is still the one the window shows.

        ``open_path`` (and ``open_project_path`` / the benchmark openers) replace
        ``self._window._session.store`` through ``_remember_source``, so a run
        started for one document must not apply its recovered mesh, named
        selections or results to a document the user switched to while it was in
        flight.  When no run is being tracked (``_recipe_starting_store`` is
        ``None`` — the tests drive the completion handlers directly), there is
        nothing to guard and the store is treated as current.
        """
        return (
            self._recipe_starting_store is None
            or self._window._session.store is self._recipe_starting_store
        )

    def _merge_named_selections(self, document: Any) -> None:
        """Adopt only the named selections a recipe run actually changed.

        The run operates on a snapshot of the window's selections taken when it
        started; merging the whole ``document.named_selections`` back would
        clobber selections the user added or edited while the run was in flight.
        Only entries whose value differs from that starting snapshot are applied,
        so the run's ``select`` steps land while the user's concurrent edits to
        *other* keys survive.
        """
        snapshot = self._recipe_named_snapshot or {}
        for name, value in document.named_selections.items():
            if name not in snapshot or snapshot[name] != value:
                self._window._session.named_selections[name] = value

    def _adopt_document(self, document: Any) -> None:
        """Adopt the document a recipe run ended with into the window.

        A document-lifecycle step (``open`` / ``close`` / ``build_model``)
        replaced or closed the worker's document; the window's store still shows
        the previous one.  This delegates to the window's shared
        :meth:`~fea_toolkit.gui.main_window.MainWindow._adopt_model` sequence — so
        the results the recipe produced register against the document the user is
        now looking at rather than the stale one (P35 follow-up) — keeping the
        recipe (``clear_recipe=False``) rather than emptying it.
        """
        self._window._adopt_model(
            document.model_data,
            source=document.document_path,
            named_selections=document.named_selections,
            clear_recipe=False,
        )

    def _failed(self, message: str) -> None:
        """Report a recipe failure, flushing buffered diagnostics first.

        The pipeline messages collected by ``_collect`` on the worker thread are
        flushed here (on the GUI thread) before the error, so the diagnostics that
        led up to the failure appear before it — matching the success path's flush
        in ``_finished``.  A non-optional ``StepError`` carries the partial run
        state, so the steps that completed before the failure are still committed
        (unchecked, like a successful run) instead of being re-run next time.  It
        also carries the document state, adopted before the lifecycle steps are
        committed — otherwise the window would keep showing the pre-recipe
        document while the recipe recorded the switch as done.
        """
        for pending in self._recipe_messages or []:
            self._window.log(pending)
        self._recipe_messages = None
        error = self._worker.error
        step_status = getattr(error, "step_status", None)
        document = getattr(error, "document", None)
        document_reset = getattr(error, "document_reset", False)
        if not self._store_is_current():
            self._window.log(
                "Recipe failed for a document that is no longer open; "
                "its recovered state was discarded.",
                "warn",
            )
        else:
            if document_reset and document is not None:
                self._adopt_document(document)
            elif document is not None:
                self._merge_named_selections(document)
            # A completed ``mesh`` step's topology has no view to register on the
            # failure path, but a retry of the analysis step needs it in the
            # store — record it so the committed mesh step need not re-run.
            if (
                document is not None
                and getattr(document, "model", None) is not None
                and self._window._session.store is not None
            ):
                self._window._session.store.set_preprocessed(document.model)
            if step_status:
                if document_reset and document is None:
                    from ...workflow.registry import STEP_SPECS

                    step_status = [
                        (index, verb, status)
                        for index, verb, status in step_status
                        if not (
                            STEP_SPECS.get(verb) is not None and STEP_SPECS[verb].resets_document
                        )
                    ]
                self._recipe_panel.apply_run_outcomes(step_status)
        self._window.log(f"Recipe failed: {message}", "error")

    def _ended(self) -> None:
        """Restore the UI once the worker has stopped, successfully or not.

        The collection here is the other half of the worker's ``gc.disable()``
        (:mod:`fea_toolkit.gui.controllers.worker`): cycles created while the
        task ran are reclaimed on the **GUI thread**, where traversing the
        PySide6/VTK objects the window owns is safe.
        """
        self._worker = None
        self._window._progress.setVisible(False)
        self._window._progress.setRange(0, 100)
        self._window._actions["analysis.stop"].setEnabled(False)
        self._window._set_model_actions_enabled(self._window._session.store is not None)
        self._window._set_analysis_actions_enabled()
        gc.collect()

    # ── Recipe actions ──────────────────────────────────────────────

    def set_actions_enabled(self) -> None:
        """Enable the recipe actions that the panel's contents allow.

        Everything except *Run* is meaningful only with a step to act on, so an
        empty recipe leaves them greyed rather than letting a click do nothing.
        """
        if self._recipe_panel is None:
            return
        has_steps = len(self._recipe_panel.recipe) > 0
        for key in ("recipe.run", "recipe.clear", "recipe.save", "recipe.export"):
            self._window._actions[key].setEnabled(has_steps)

    # ── Recipe files ────────────────────────────────────────────────

    def clear(self) -> None:
        """**Recipe ▸ Clear recipe**: empty the panel."""
        self._recipe_panel.clear()
        self._window.log("Recipe cleared.")

    def save(self) -> None:
        """**Recipe ▸ Save recipe…**: write the panel's steps to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(
            self._window, "Save recipe", "recipe.json", "Recipe JSON (*.json)"
        )
        if not path:
            return
        try:
            self._window._session.recipe.to_json(path)
        except OSError as exc:
            self._window.log(f"Could not write {path}: {exc}", "error")
            return
        self._window.log(f"Saved recipe: {path}")

    def open(self) -> None:
        """**Recipe ▸ Open recipe…**: read a recipe and show it in the panel."""
        from ...workflow import Recipe

        path, _ = QFileDialog.getOpenFileName(
            self._window, "Open recipe", "", "Recipe JSON (*.json)"
        )
        if not path:
            return
        try:
            recipe = Recipe.from_json(path)
        except (OSError, ValueError) as exc:
            self._window.log(f"Could not read {path}: {exc}", "error")
            return
        self._recipe_panel.set_recipe(recipe)
        self._window.log(f"Opened recipe: {path} ({len(recipe)} steps)")

    def export(self) -> None:
        """**Recipe ▸ Export as Python…**: write the recipe as a runnable script."""
        from pathlib import Path

        path, _ = QFileDialog.getSaveFileName(
            self._window, "Export recipe as Python", "recipe.py", "Python (*.py)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self._window._session.recipe.to_python(), encoding="utf-8")
        except OSError as exc:
            self._window.log(f"Could not write {path}: {exc}", "error")
            return
        self._window.log(f"Exported recipe: {path}")

    def command(self) -> None:
        """**Recipe ▸ Command…** (Ctrl+K): compile a command string into a step."""
        from ...workflow import step_to_command
        from ..views.command_palette import get_step

        mesh = (
            self._window._session.store.preprocessed()
            if self._window._session.store is not None
            else None
        )
        initial = {"geometry"} if mesh is not None else ()
        step = get_step(self._window, list(self._window._session.recipe), initial=initial)
        if step is None:
            return
        self._recipe_panel.add_step(step.verb, step.selection, step.params, optional=step.optional)
        self._window.log(f"command: {step_to_command(step)}")

    def import_script(self) -> None:
        """**Recipe ▸ Import script…**: read a recipe's RECIPE literal from a script."""
        from pathlib import Path

        from ...workflow import Recipe

        path, _ = QFileDialog.getOpenFileName(
            self._window, "Import recipe script", "", "Python (*.py)"
        )
        if not path:
            return
        try:
            recipe = Recipe.from_python(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, SyntaxError) as exc:
            self._window.log(f"Could not import {path}: {exc}", "error")
            return
        self._recipe_panel.set_recipe(recipe)
