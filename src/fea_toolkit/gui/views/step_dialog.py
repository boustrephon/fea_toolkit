"""Edit one recipe step — a modal dialog, mirroring ``AnalysisDialog``.

The Recipe panel's "Step" inspector used to live inline in the dock, which made
the dock too tall beside the Message Log.  This dialog is the same form, moved to
the foreground: it opens for a newly added step (Add) and for an existing one
(double-click / Edit), and returns the edited
:class:`~fea_toolkit.workflow.steps.Step` or ``None`` when cancelled.

It reuses the same building blocks the panel did — :class:`ConfigEditor` for a
dict parameter that declares a ``manifest``, and the same ``Selection`` grammar
(with :class:`SelectionDialog` as a composer) for the scope — so a parameter and
a selection render identically here and anywhere else they are edited.  A step's
parameters are only emitted when they differ from their declared default, the
same "defaults applied at run time, not baked in" rule the panel followed.
"""

import ast
from typing import Any, Optional

from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...model.selection import SELECT_KEYS_HELP, Selection
from ...workflow import STEP_SPECS, Step
from .config_editor import ConfigEditor
from .selection_dialog import SelectionDialog

__all__ = ["StepDialog"]

#: Parameter types edited as a literal in a single field (the panel's
#: ``_from_literal``): a dict/list is data, not prose, so it is parsed on the
#: spot rather than given a bespoke editor.
_LITERAL_TYPES = (dict, list)


class StepDialog(QDialog):
    """Edit a step's selection, parameters and optional flag.

    Args:
        step: The step to edit.  For a newly added step, pass a template
            ``Step(verb=...)`` whose parameters fall back to the verb's defaults.
        parent: Optional Qt parent widget.
    """

    def __init__(self, step: Step, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._verb = step.verb
        self._spec = STEP_SPECS[self._verb]
        self._widgets: dict[str, Any] = {}
        self.setWindowTitle(f"Step — {self._verb}")

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_selection_group(step))
        layout.addWidget(self._build_params_group(step))

        self._optional_box = QCheckBox("Optional — continue if this step fails", self)
        self._optional_box.setChecked(step.optional)
        layout.addWidget(self._optional_box)

        self._error = QLabel("", self)
        self._error.setStyleSheet("color: #b00020;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._validate()

    # ── Selection ────────────────────────────────────────────────────

    def _build_selection_group(self, step: Step) -> QGroupBox:
        """The scope field, with the selection dialog as a composer."""
        group = QGroupBox("Selection", self)
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self._selection_field = QLineEdit(self)
        self._selection_field.setText(
            step.selection.to_string() if step.selection is not None else ""
        )
        self._selection_field.setPlaceholderText("empty — the whole model")
        self._selection_field.textChanged.connect(self._validate)
        row.addWidget(self._selection_field, 1)

        compose = QPushButton("\u2026", self)
        compose.setToolTip("Compose the expression in the selection dialog")
        compose.clicked.connect(self._on_compose_selection)
        row.addWidget(compose)
        layout.addLayout(row)

        keys = QLabel(f"Keys: {SELECT_KEYS_HELP}", group)
        keys.setEnabled(False)
        layout.addWidget(keys)
        return group

    def _on_compose_selection(self) -> None:
        """Open the selection dialog, writing its result back to the field."""
        try:
            current = Selection.from_string(self._selection_field.text())
        except ValueError:
            current = None
        chosen = SelectionDialog.edit(current, self)
        if chosen is not None:
            self._selection_field.setText(chosen.to_string())

    # ── Parameters ───────────────────────────────────────────────────

    def _build_params_group(self, step: Step) -> QGroupBox:
        """One editor per declared parameter, seeded from *step* or its default."""
        group = QGroupBox("Parameters", self)
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for name, spec in self._spec.params.items():
            value = step.params.get(name, spec.default)
            editor = self._param_editor(spec, value)
            self._widgets[name] = editor
            if spec.manifest is not None:
                form.addRow(editor)  # a collapsible ConfigEditor spans the row
            else:
                form.addRow(name, editor)
        return group

    def _param_editor(self, spec: Any, value: Any) -> QWidget:
        """A widget for one parameter, with its help as a tooltip."""
        if spec.manifest is not None:
            return ConfigEditor(spec.manifest, value, self, title="Configuration")

        kind = spec.type
        if kind is bool:
            widget = QCheckBox(self)
            widget.setChecked(bool(value))
        elif spec.choices:
            widget = QComboBox(self)
            widget.addItems([str(choice) for choice in spec.choices])
            widget.setCurrentText(str(value))
        elif kind is int:
            widget = QSpinBox(self)
            widget.setRange(-1_000_000, 1_000_000)
            widget.setValue(int(value))
        elif kind is float:
            widget = QDoubleSpinBox(self)
            widget.setRange(-1e12, 1e12)
            widget.setDecimals(6)
            widget.setValue(float(value))
        else:
            widget = QLineEdit(self)
            widget.setText(str(value))
            if kind in _LITERAL_TYPES:
                widget.editingFinished.connect(self._validate)
        if spec.help:
            widget.setToolTip(spec.help)
        return widget

    def _param_value(self, name: str) -> Any:
        """Read one parameter back from its widget."""
        spec = self._spec.params[name]
        if spec.manifest is not None:
            return self._widgets[name].value()
        widget = self._widgets[name]
        if spec.type is bool:
            return bool(widget.isChecked())
        if spec.choices:
            return widget.currentText()
        if spec.type is int:
            return int(widget.value())
        if spec.type is float:
            return float(widget.value())
        if spec.type is str:
            return widget.text()
        return self._literal_value(name)

    def _literal_value(self, name: str) -> Any:
        """Parse a free-form dict/list field, refusing text that will not parse."""
        spec = self._spec.params[name]
        text = self._widgets[name].text()
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"expected a {spec.type.__name__} literal, got {text!r}") from exc
        if not isinstance(parsed, spec.type):
            raise ValueError(f"expected a {spec.type.__name__}, got {type(parsed).__name__}")
        return parsed

    # ── Validation & result ──────────────────────────────────────────

    def _validate(self) -> None:
        """Gate OK on a parseable selection and parseable literal parameters."""
        errors: list[str] = []
        text = self._selection_field.text()
        if text.strip():
            try:
                Selection.from_string(text)
            except ValueError as exc:
                errors.append(f"selection: {exc}")
        for name, spec in self._spec.params.items():
            if spec.manifest is None and spec.type in _LITERAL_TYPES:
                try:
                    self._literal_value(name)
                    self._widgets[name].setStyleSheet("")
                except ValueError as exc:
                    errors.append(f"{name}: {exc}")
                    self._widgets[name].setStyleSheet("color: #b00020;")
        self._error.setText("\n".join(errors))
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(not errors)

    def result(self) -> Step:
        """The edited step, with only the parameters that differ from defaults."""
        text = self._selection_field.text()
        selection = Selection.from_string(text) if text.strip() else None
        params: dict[str, Any] = {}
        for name, spec in self._spec.params.items():
            value = self._param_value(name)
            if value != spec.default:
                params[name] = value
        return Step(
            verb=self._verb,
            selection=selection,
            params=params,
            optional=self._optional_box.isChecked(),
        )

    # ── Entry point ──────────────────────────────────────────────────

    @staticmethod
    def edit(step: Step, parent: Optional[Any] = None) -> Optional[Step]:
        """Run the dialog modally and return the edited step, or ``None``.

        Args:
            step: The step to edit.
            parent: Optional Qt parent widget.

        Returns:
            The edited :class:`~fea_toolkit.workflow.steps.Step`, or ``None``
            when the dialog is cancelled.
        """
        dialog = StepDialog(step, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result()
