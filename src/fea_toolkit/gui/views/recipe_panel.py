"""The Recipe panel — author a workflow, then run it.

The panel is a *view over a* :class:`~fea_toolkit.workflow.Recipe`: it keeps no
second copy of the workflow state, and it builds every parameter field from the
verb's metadata in ``STEP_SPECS``.  That is what stops the form drifting from
the schema — a parameter a verb does not declare cannot be edited here, and a
new verb appears in the "Add" menu without touching this file.

Steps are :class:`~fea_toolkit.workflow.steps.Step` instances, which are frozen,
so an edit *replaces* the step in the recipe rather than mutating in place; the
recipe object is the single source of truth.

Mapping and list parameters (a case set, a combination list) are edited as a
literal in a single field and parsed on the spot, which is honest about what
they are — data, not prose — without a bespoke editor for each verb.
"""

import ast
from dataclasses import replace
from typing import Any, Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...model.selection import Selection
from ...workflow import STEP_SPECS, Recipe
from .config_editor import ConfigEditor
from .selection_dialog import SelectionDialog
from .verb_help_dialog import VerbHelpDialog


class RecipePanel(QWidget):
    """A list of steps, the selected step's parameters, and its selection.

    Args:
        parent: Optional Qt parent widget.
    """

    #: Emitted whenever the recipe's steps or their parameters change.
    changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._recipe = Recipe()
        self._selected = -1
        self._loading = False  # suppress write-back while rebuilding the form
        self._build_ui()
        self._reload_list()

    # ── Construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out the step list, its buttons, and the step inspector."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        row = QHBoxLayout()
        self._list = QListWidget(self)
        self._list.setObjectName("list_recipe_steps")
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_step_context_menu)
        row.addWidget(self._list, 1)

        buttons = QVBoxLayout()
        self._add_button = QToolButton(self)
        self._add_button.setText("Add")
        self._add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._add_menu = QMenu(self._add_button)
        for verb, spec in STEP_SPECS.items():
            action = self._add_menu.addAction(f"{verb} \u2014 {spec.help}")
            action.triggered.connect(lambda _=False, name=verb: self.add_step(name))
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

        self._detail = QGroupBox("Step", self)
        self._detail_layout = QVBoxLayout(self._detail)
        self._selection_label = QLabel("Selection: everything", self._detail)
        self._detail_layout.addWidget(self._selection_label)
        edit = QPushButton("Edit selection\u2026", self._detail)
        edit.clicked.connect(self._on_edit_selection)
        self._detail_layout.addWidget(edit)

        self._form = QFormLayout()
        self._detail_layout.addLayout(self._form)

        self._optional = QCheckBox("Optional \u2014 continue if this step fails", self._detail)
        self._optional.toggled.connect(self._on_optional_toggled)
        self._detail_layout.addWidget(self._optional)
        outer.addWidget(self._detail, 2)

        self._empty = QLabel("No steps yet \u2014 use Add to build a recipe.", self)
        self._detail_layout.addWidget(self._empty)

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
        self._build_form()

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

    def _build_form(self) -> None:
        """Render the selected step's selection, parameters and optional flag.

        A spin box writes back on ``editingFinished`` rather than on every
        ``valueChanged``: the form is rebuilt from the recipe, so writing per
        keystroke would destroy the widget the user is typing in.
        """
        self._loading = True
        try:
            while self._form.rowCount():
                self._form.removeRow(0)
            step = self._selected_step()
            self._detail.setEnabled(step is not None)
            self._empty.setVisible(step is None)
            if step is None:
                self._selection_label.setText("Selection: \u2014")
                return
            described = step.selection if step.selection is not None else "everything"
            self._selection_label.setText(f"Selection: {described}")
            for name, parameter in STEP_SPECS[step.verb].params.items():
                value = step.params.get(name, parameter.default)
                editor = self._make_editor(name, parameter, value)
                if parameter.manifest is not None:
                    # A structured dict editor is a self-contained collapsible
                    # group, so it spans the row rather than sitting under a
                    # redundant "config" label.
                    self._form.addRow(editor)
                else:
                    self._form.addRow(name, editor)
            self._optional.setChecked(step.optional)
        finally:
            self._loading = False

    def _make_editor(self, name: str, parameter: Any, value: Any) -> QWidget:
        """A widget for one parameter — the editor plus its inline help.

        A ``dict`` parameter whose spec declares a ``manifest`` renders a
        structured :class:`~fea_toolkit.gui.views.config_editor.ConfigEditor`
        (a collapsible group) instead of a raw literal, so the dict's keys are
        discoverable and type-checked.  Every other parameter's help text is
        shown **inline** beneath the editor, not only as a hover tooltip.

        Args:
            name: Parameter name, for the write-back.
            parameter: The verb's :class:`~fea_toolkit.workflow.steps.ParamSpec`.
            value: The step's current value (or the spec default).

        Returns:
            The editor, already connected to the recipe.
        """

        def write(new_value: Any) -> None:
            if not self._loading:
                self._set_param(name, new_value)

        if parameter.manifest is not None:
            editor = ConfigEditor(parameter.manifest, value, self._detail, title="Configuration")
            editor.setToolTip(parameter.help)
            editor.changed.connect(lambda: write(editor.value()))
            return editor

        editor = self._scalar_editor(parameter, value, write)

        container = QWidget(self._detail)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(editor)
        if parameter.help:
            help_label = QLabel(parameter.help)
            help_label.setWordWrap(True)
            # A word-wrapped label reports a single-line height at its preferred
            # width, so a narrower form column clips multi-line text.  Capping
            # the width makes it wrap at a known width, so its sizeHint is the
            # wrapped height and the form reserves the full text.
            help_label.setMaximumWidth(260)
            help_label.setStyleSheet("color: #6a6a6a;")
            layout.addWidget(help_label)
        return container

    def _scalar_editor(self, parameter: Any, value: Any, write: Any) -> QWidget:
        """The editor for a scalar or free-form literal parameter."""
        kind = parameter.type
        if kind is bool:
            widget = QCheckBox(self._detail)
            widget.setChecked(bool(value))
            widget.toggled.connect(write)
        elif kind is int:
            widget = QSpinBox(self._detail)
            widget.setRange(-1_000_000, 1_000_000)
            widget.setValue(int(value))
            widget.editingFinished.connect(lambda w=widget: write(w.value()))
        elif kind is float:
            widget = QDoubleSpinBox(self._detail)
            widget.setRange(-1e12, 1e12)
            widget.setDecimals(6)
            widget.setValue(float(value))
            widget.editingFinished.connect(lambda w=widget: write(w.value()))
        elif parameter.choices:
            widget = QComboBox(self._detail)
            widget.addItems([str(choice) for choice in parameter.choices])
            widget.setCurrentText(str(value))
            widget.currentTextChanged.connect(write)
        else:
            widget = QLineEdit(self._detail)
            widget.setText(value if kind is str else repr(value))
            if kind is str:
                widget.editingFinished.connect(lambda w=widget: write(w.text()))
            else:
                widget.editingFinished.connect(
                    lambda w=widget, k=kind: self._from_literal(w, k, write)
                )
        widget.setToolTip(parameter.help)
        return widget

    def _from_literal(self, widget: QLineEdit, kind: type, write: Any) -> None:
        """Parse a mapping/list editor, refusing to write what will not parse.

        A field that does not parse is marked rather than silently ignored: a
        parameter that reads back as authored is the point of the panel.

        Args:
            widget: The line edit holding the literal.
            kind: The required type — ``dict`` or ``list``.
            write: The write-back callable.
        """
        try:
            parsed = ast.literal_eval(widget.text())
        except (ValueError, SyntaxError):
            parsed = None
        if not isinstance(parsed, kind):
            widget.setStyleSheet("color: #b00020;")
            return
        widget.setStyleSheet("")
        write(parsed)

    def _set_param(self, name: str, value: Any) -> None:
        """Write one parameter of the selected step back to the recipe."""
        step = self._selected_step()
        if step is None:
            return
        params = dict(step.params)
        params[name] = value
        self._replace_selected(params=params)

    def _replace_selected(self, *, refresh_list: bool = False, **changes: Any) -> None:
        """Swap the selected step for an edited copy.

        :class:`~fea_toolkit.workflow.steps.Step` is frozen, so an edit replaces
        the step and the recipe stays the single source of truth.  The list is
        rebuilt only when its *text* changed (the optional flag), so editing a
        parameter cannot steal focus from the field being edited.

        Args:
            refresh_list: Whether the list labels need rebuilding.
            **changes: Fields to replace on the step.
        """
        step = self._selected_step()
        if step is None:
            return
        self._recipe.steps[self._selected] = replace(step, **changes)
        if refresh_list:
            self._reload_list()
        self.changed.emit()

    def _on_edit_selection(self) -> None:
        """Open the expression editor for the selected step's selection.

        An empty expression is a valid selection that matches everything, and a
        verb treats it exactly as it treats no selection at all.
        """
        step = self._selected_step()
        if step is None:
            return
        selection = SelectionDialog.edit(step.selection, self)
        if selection is None:  # the dialog was cancelled
            return
        self._replace_selected(selection=selection)

    def _on_optional_toggled(self, flag: bool) -> None:
        """Record that a failure of the selected step may be tolerated."""
        if not self._loading:
            self._replace_selected(refresh_list=True, optional=flag)

    def _on_row_changed(self, row: int) -> None:
        """Show the newly selected step's parameters.

        Args:
            row: The newly current row, or ``-1`` when the list is emptied.
        """
        self._selected = row
        self._build_form()
