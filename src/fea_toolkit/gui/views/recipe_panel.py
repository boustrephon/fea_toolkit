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
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...model.selection import Selection
from ...workflow import STEP_SPECS, Recipe, Step
from .step_dialog import StepDialog
from .verb_help_dialog import VerbHelpDialog


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
        self._build_ui()
        self._reload_list()

    # ── Construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out the step list, its buttons, and the selected-step summary."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        row = QHBoxLayout()
        self._list = QListWidget(self)
        self._list.setObjectName("list_recipe_steps")
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_step_context_menu)
        self._list.itemDoubleClicked.connect(lambda _item: self._edit_step_interactive())
        row.addWidget(self._list, 1)

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
        edited = StepDialog.edit(Step(verb=verb), self)
        if edited is not None:
            self.add_step(edited.verb, edited.selection, edited.params, optional=edited.optional)

    def _edit_step_interactive(self) -> None:
        """Open the step dialog for the selected step and replace it."""
        step = self._selected_step()
        if step is None:
            return
        edited = StepDialog.edit(step, self)
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
        """Rebuild the step list from the recipe, keeping the selection."""
        self._list.blockSignals(True)
        self._list.clear()
        for index, step in enumerate(self._recipe):
            item = QListWidgetItem(f"{index + 1}. {step.verb}", self._list)
            if step.selection is not None:
                item.setToolTip(f"selection: {step.selection}")
            if step.optional:
                item.setText(f"{item.text()}  (optional)")
        self._list.blockSignals(False)
        if 0 <= self._selected < len(self._recipe):
            self._list.setCurrentRow(self._selected)
        self._update_summary()

    def _on_row_changed(self, row: int) -> None:
        """Track the newly selected step and refresh its summary.

        Args:
            row: The newly current row, or ``-1`` when the list is emptied.
        """
        self._selected = row
        self._update_summary()

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
        item = self._list.itemAt(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)
        index = self._list.row(item)
        if not (0 <= index < len(self._recipe)):
            return
        menu = QMenu(self._list)
        help_action = menu.addAction("Help\u2026")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
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
