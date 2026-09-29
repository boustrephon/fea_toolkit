"""A structured editor for a verb's ``config`` dict parameter.

The ``run_static`` verb's ``config`` is a flat dict of OpenSees builder options.
A raw literal is honest but opaque; this widget renders a curated manifest of
those keys (:data:`~fea_toolkit.workflow.config_keys.BUILDER_CONFIG_KEYS`) as one
editor per key, with each key's help shown inline, and writes back a dict holding
**only the keys the user changed from their declared default** — so an untouched
key is omitted and the builder applies its own default.

Qt-only; the manifest itself is Qt-free.
"""

from typing import Any, Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

__all__ = ["ConfigEditor"]


class ConfigEditor(QWidget):
    """One editor per manifest key, emitting a minimal override dict.

    Args:
        manifest: ``{key: ParamSpec}`` describing the dict's keys.
        value: The current ``config`` dict (keys absent from it fall back to
            the manifest default).
        parent: Optional Qt parent.
    """

    changed = Signal()

    def __init__(
        self,
        manifest: dict,
        value: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._current = dict(value or {})
        self._widgets: dict[str, Any] = {}
        self._build()

    # ── Construction ─────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key, spec in self._manifest.items():
            editor = self._editor_for(spec, self._current.get(key, spec.default))
            self._widgets[key] = editor
            field: Any = editor
            if spec.help:
                field = self._with_help(editor, spec.help)
            layout.addRow(key, field)

    def _editor_for(self, spec: Any, current: Any) -> QWidget:
        """A widget for one key, wired to emit :attr:`changed` on commit."""
        kind = spec.type
        if kind is bool:
            widget = QCheckBox(self)
            widget.setChecked(bool(current))
            widget.toggled.connect(lambda _: self.changed.emit())
        elif spec.choices:
            widget = QComboBox(self)
            widget.addItems([str(choice) for choice in spec.choices])
            widget.setCurrentText(str(current))
            widget.currentTextChanged.connect(lambda _: self.changed.emit())
        elif kind is int:
            widget = QSpinBox(self)
            widget.setRange(-1_000_000, 1_000_000)
            widget.setValue(int(current))
            widget.editingFinished.connect(self.changed.emit)
        elif kind is float:
            widget = QDoubleSpinBox(self)
            widget.setRange(-1e12, 1e12)
            widget.setDecimals(6)
            widget.setValue(float(current))
            widget.editingFinished.connect(self.changed.emit)
        else:
            widget = QLineEdit(self)
            widget.setText(str(current))
            widget.editingFinished.connect(self.changed.emit)
        return widget

    @staticmethod
    def _with_help(editor: QWidget, help_text: str) -> QWidget:
        """The editor with its help text shown inline beneath it."""
        container = QWidget(editor.parentWidget())
        box = QVBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        box.addWidget(editor)
        label = QLabel(help_text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #6a6a6a;")
        box.addWidget(label)
        return container

    # ── Query ────────────────────────────────────────────────────────

    def value(self) -> dict:
        """The keys the user changed from their manifest default, in manifest order."""
        result: dict[str, Any] = {}
        for key, spec in self._manifest.items():
            current = self._read(key, spec)
            if current != spec.default:
                result[key] = current
        return result

    def _read(self, key: str, spec: Any) -> Any:
        widget = self._widgets[key]
        kind = spec.type
        if kind is bool:
            return bool(widget.isChecked())
        if spec.choices:
            return widget.currentText()
        if kind is int:
            return int(widget.value())
        if kind is float:
            return float(widget.value())
        return widget.text()
