"""The Recipe panel — author a workflow, then run it.

The panel is a *view over a* :class:`~fea_toolkit.workflow.Recipe`: it keeps no
second copy of the workflow state.  It lists the steps and hands the editing of a
step to :class:`~fea_toolkit.gui.views.step_dialog.StepDialog`, a modal dialog
built from the verb's metadata in ``STEP_SPECS`` — so a parameter a verb does not
declare cannot be edited, and a new verb appears in the "Add" menu without
touching this file.  The step inspector used to live inline here; it moved to the
dialog so the dock stays short beside the Message Log.

Steps are :class:`~fea_toolkit.workflow.steps.Step` instances, which are frozen,
so an edit *replaces* the step in the recipe rather than mutating in place; the
recipe object is the single source of truth.
"""

from typing import Any, Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QBrush, QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...model.selection import Selection
from ...workflow import STEP_SPECS, Recipe, Step
from .step_dialog import StepDialog
from .verb_help_dialog import VerbHelpDialog

#: Status cell → (glyph, colour) for the Recipe panel's Status column.  Empty
#: (pending) is deliberately no glyph; the rest follow the project's green/red
#: pass/fail vocabulary.
_STATUS_GLYPHS = {
    "": ("", None),
    "running": ("\u2026", "#808080"),
    "ok": ("\u2713", "#1a7f37"),
    "failed": ("\u2717", "#b00020"),
    "cancelled": ("\u2298", "#9a6700"),
    "skipped": ("\u2014", "#aaaaaa"),
}


class RecipePanel(QWidget):
    """A list of steps, with a one-line summary of the selected step.

    Args:
        parent: Optional Qt parent widget.
    """

    #: Emitted whenever the recipe's steps or their parameters change.
    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._recipe = Recipe()
        self._selected = -1
        self._statuses: dict[int, str] = {}
        #: Parsed source model, set by :meth:`set_model_data` when a model opens;
        #: handed to the step dialog so the ``cases`` editor can seed itself.
        self._model_data: Any = None
        self._build_ui()
        self._reload_list()

    def set_model_data(self, model_data: Any) -> None:
        """Provide the parsed source model for the step dialog to seed from.

        Called by the main window when a ``SAPModelData`` opens; ``None`` clears
        it (the dialog then falls back to a literal dict editor for ``cases``).

        Args:
            model_data: The parsed source model, or ``None``.
        """
        self._model_data = model_data

    # ── Construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out the step list, its buttons, and the selected-step summary."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        row = QHBoxLayout()
        self._table = QTableWidget(0, 3, self)
        self._table.setObjectName("table_recipe_steps")
        self._table.setHorizontalHeaderLabels(["Run", "Step", "Status"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.currentCellChanged.connect(self._on_current_cell_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_step_context_menu)
        self._table.cellDoubleClicked.connect(lambda _row, _col: self._edit_step_interactive())
        row.addWidget(self._table, 1)

        buttons = QVBoxLayout()
        self._add_button = QToolButton(self)
        self._add_button.setText("Add")
        self._add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._add_menu = QMenu(self._add_button)
        for verb, spec in STEP_SPECS.items():
            action = self._add_menu.addAction(f"{verb} \u2014 {spec.help}")
            action.triggered.connect(lambda _=False, name=verb: self._add_step_interactive(name))
        self._add_button.setMenu(self._add_menu)
        buttons.addWidget(self._add_button)

        for label, slot in (
            ("Remove", self.remove_selected),
            ("Up", lambda: self.move_selected(-1)),
            ("Down", lambda: self.move_selected(1)),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        outer.addLayout(row, 1)

        summary_row = QHBoxLayout()
        self._summary = QLabel("No steps yet \u2014 use Add to build a recipe.", self)
        self._summary.setWordWrap(True)
        summary_row.addWidget(self._summary, 1)
        self._edit_button = QPushButton("Edit step\u2026", self)
        self._edit_button.clicked.connect(self._edit_step_interactive)
        summary_row.addWidget(self._edit_button)
        outer.addLayout(summary_row)

    @property
    def recipe(self) -> Any:
        """The recipe this panel is editing."""
        return self._recipe

    # ── Public API ───────────────────────────────────────────────────

    def set_recipe(self, recipe: Any) -> None:
        """Show *recipe* in the panel, replacing what is displayed.

        Args:
            recipe: The :class:`~fea_toolkit.workflow.Recipe` to display.
        """
        self._recipe = recipe
        self._selected = 0 if len(recipe) else -1
        self._reload_list()
        self.changed.emit()

    def clear(self) -> None:
        """Replace the displayed recipe with an empty one."""
        self.set_recipe(Recipe())

    def add_step(
        self,
        verb: str,
        selection: Optional[Selection] = None,
        params: Optional[dict] = None,
        *,
        optional: bool = False,
    ) -> int:
        """Append a step and select it.

        This is the programmatic path (Model-menu presets, tests).  The
        interactive path is :meth:`_add_step_interactive`, which opens the
        :class:`StepDialog` first.

        Args:
            verb: The verb to add; must be a key of ``STEP_SPECS``.
            selection: The elements the step acts on, or ``None`` for all.
            params: Verb parameters; omissions take their declared defaults.
            optional: Whether a failure of this step should be tolerated.

        Returns:
            The new step's index, so a caller can log or focus it.

        Raises:
            ValueError: If the verb is unknown or a parameter is not declared.
        """
        self._recipe.add(verb, selection, params, optional=optional)
        self._selected = len(self._recipe) - 1
        self._reload_list()
        self.changed.emit()
        return self._selected

    def remove_selected(self) -> None:
        """Drop the selected step, if there is one."""
        if not 0 <= self._selected < len(self._recipe):
            return
        del self._recipe.steps[self._selected]
        self._selected = min(self._selected, len(self._recipe) - 1)
        self._reload_list()
        self.changed.emit()

    def move_selected(self, delta: int) -> None:
        """Move the selected step by *delta* places, keeping it selected.

        Args:
            delta: ``-1`` to move it earlier, ``+1`` to move it later.
        """
        target = self._selected + delta
        if not (0 <= self._selected < len(self._recipe)) or not 0 <= target < len(self._recipe):
            return
        steps = self._recipe.steps
        steps[self._selected], steps[target] = steps[target], steps[self._selected]
        self._selected = target
        self._reload_list()
        self.changed.emit()

    # ── Interactive add / edit ───────────────────────────────────────

    def _add_step_interactive(self, verb: str) -> None:
        """Open the step dialog for *verb* and append the edited step."""
        edited = StepDialog.edit(Step(verb=verb), self, self._model_data)
        if edited is not None:
            self.add_step(edited.verb, edited.selection, edited.params, optional=edited.optional)

    def _edit_step_interactive(self) -> None:
        """Open the step dialog for the selected step and replace it."""
        step = self._selected_step()
        if step is None:
            return
        edited = StepDialog.edit(step, self, self._model_data)
        if edited is None:
            return
        self._recipe.steps[self._selected] = edited
        self._reload_list()
        self.changed.emit()

    # ── The list ─────────────────────────────────────────────────────

    def _selected_step(self) -> Optional[Any]:
        """The selected :class:`Step`, or ``None`` when nothing is selected."""
        if 0 <= self._selected < len(self._recipe):
            return self._recipe.steps[self._selected]
        return None

    def _reload_list(self) -> None:
        """Rebuild the step table from the recipe, keeping the selection."""
        self._statuses = {}  # the recipe changed; run status no longer applies
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._recipe))
        for index, step in enumerate(self._recipe):
            label = f"{index + 1}. {step.verb}"
            if step.optional:
                label += "  (optional)"
            item = QTableWidgetItem(label)
            if step.selection is not None:
                item.setToolTip(f"selection: {step.selection}")
            self._table.setItem(index, 1, item)
            self._table.setCellWidget(index, 0, self._make_run_toggle(index, step.enabled))
            self._refresh_status_cell(index)
        self._table.blockSignals(False)
        if 0 <= self._selected < len(self._recipe):
            self._table.selectRow(self._selected)
        self._update_summary()

    def _make_run_toggle(self, index: int, checked: bool) -> QWidget:
        """A centred ``Run`` checkbox for step *index*, bound to its ``enabled``.

        ``setChecked`` runs before the ``toggled`` signal is connected, so
        building the table never re-fires the handler.
        """
        checkbox = QCheckBox(self._table)
        checkbox.setChecked(checked)
        checkbox.setToolTip("Run this step")
        checkbox.toggled.connect(lambda state: self._on_run_toggled(index, state))
        container = QWidget(self._table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(checkbox)
        return container

    def _on_run_toggled(self, index: int, checked: bool) -> None:
        """Apply a Run-checkbox change, cascading to precursors when checked."""
        if not (0 <= index < len(self._recipe)):
            return
        if self._recipe.steps[index].enabled == checked:
            return
        self._recipe.set_enabled(index, checked)
        self._reload_list()
        self.changed.emit()

    def _on_current_cell_changed(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        """Track the newly selected step and refresh its summary."""
        self._selected = row
        self._update_summary()

    # ── Run status ───────────────────────────────────────────────────

    def reset_statuses(self) -> None:
        """Clear every step's run status (pending), e.g. before a new run.

        Both the transient live status and the step's persisted ``status`` are
        reset; the ``enabled`` flags are left alone (they are the user's "run
        this" selection for the run that is about to start).
        """
        from dataclasses import replace

        self._statuses = {}
        self._table.blockSignals(True)
        for index, step in enumerate(self._recipe):
            if step.status:
                self._recipe.steps[index] = replace(step, status="")
            self._refresh_status_cell(index)
        self._table.blockSignals(False)

    def set_step_status(self, index: int, status: str) -> None:
        """Set the status shown beside step *index* (live, without a rebuild).

        Args:
            index: Zero-based step position in the recipe.
            status: One of ``"running"``, ``"ok"``, ``"failed"``,
                ``"cancelled"`` or ``"skipped"``.
        """
        if not (0 <= index < len(self._recipe)):
            return
        self._statuses[index] = status
        self._refresh_status_cell(index)

    def apply_run_outcomes(self, step_status: Any) -> None:
        """Commit a run's outcomes onto the steps, so they persist with the recipe.

        A step that finished ``"ok"`` is unchecked (``enabled=False``) and marked
        done; a ``"failed"`` or ``"cancelled"`` step keeps its run flag but
        records its outcome; a ``"skipped"`` step (already disabled) is left as
        it is.

        Args:
            step_status: The ``(index, verb, status)`` tuples from
                :class:`~fea_toolkit.workflow.recipe.RecipeRun.step_status`.
        """
        from dataclasses import replace

        changed = False
        for index, _verb, status in step_status:
            if status == "skipped" or not (0 <= index < len(self._recipe)):
                continue
            step = self._recipe.steps[index]
            enabled = False if status == "ok" else step.enabled
            if step.status != status or step.enabled != enabled:
                self._recipe.steps[index] = replace(step, status=status, enabled=enabled)
                changed = True
        if changed:
            self._reload_list()
            self.changed.emit()

    def _refresh_status_cell(self, index: int) -> None:
        """Render the status glyph for step *index* into the Status column.

        The live run status (the transient ``_statuses`` map) wins; otherwise the
        step's persisted ``status`` is shown, so a recipe re-opened after a run
        still carries its outcome glyph.
        """
        status = self._statuses.get(index) or self._recipe.steps[index].status
        glyph, color = _STATUS_GLYPHS.get(status or "", ("", None))
        item = self._table.item(index, 2)
        if item is None:
            item = QTableWidgetItem()
            self._table.setItem(index, 2, item)
        item.setText(glyph)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color is not None:
            item.setForeground(QBrush(QColor(color)))
        else:
            item.setForeground(QBrush())

    def _update_summary(self) -> None:
        """Show a one-line summary of the selected step, or the empty hint."""
        step = self._selected_step()
        if step is None:
            self._summary.setText("No steps yet \u2014 use Add to build a recipe.")
            self._edit_button.setEnabled(False)
            return
        self._summary.setText(_step_summary(step))
        self._edit_button.setEnabled(True)

    # ── Help ─────────────────────────────────────────────────────────

    def _on_step_context_menu(self, pos) -> None:
        """Right-click a step: offer per-verb help for the step under the cursor."""
        item = self._table.itemAt(pos)
        if item is None:
            return
        index = item.row()
        if not (0 <= index < len(self._recipe)):
            return
        self._table.selectRow(index)
        self._selected = index
        menu = QMenu(self._table)
        help_action = menu.addAction("Help\u2026")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is help_action:
            self._show_verb_help(index)

    def _show_verb_help(self, index: int) -> None:
        """Open the read-only help dialog for the step's verb."""
        verb = self._recipe.steps[index].verb
        VerbHelpDialog(verb, STEP_SPECS[verb], self).exec()


def _step_summary(step: Any) -> str:
    """One line describing *step*, for the panel's summary label.

    Args:
        step: The step to describe.

    Returns:
        ``verb \u00b7 selection: ... \u00b7 params: ...`` — parameters omitted when the
        step supplies none.
    """
    selection = "everything"
    if step.selection is not None:
        selection = step.selection.to_string() or "everything"
    parts = [step.verb, f"selection: {selection}"]
    if step.params:
        rendered = ", ".join(f"{key}={value!r}" for key, value in step.params.items())
        parts.append(f"params: {rendered}")
    if step.optional:
        parts.append("optional")
    return " \u00b7 ".join(parts)
