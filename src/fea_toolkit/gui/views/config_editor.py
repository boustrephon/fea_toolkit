"""A structured editor for a verb's ``config`` dict parameter.

The ``run_static`` verb's ``config`` is a flat dict of OpenSees builder options.
A raw literal is honest but opaque; this widget renders a curated manifest of
those keys (:data:`~fea_toolkit.workflow.config_keys.BUILDER_CONFIG_KEYS`) as one
editor per key.  It is a **collapsible** group: it starts collapsed, so a long
manifest never crowds the dialog it sits in, and its contents scroll once
expanded.  Each key's help is shown as a **tooltip** on its widget.  It writes
back a dict holding **only the keys the user changed from their declared
default** — so an untouched key is omitted and the builder applies its own
default.

Qt-only; the manifest itself is Qt-free.
"""

from typing import Any, Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

__all__ = ["ConfigEditor"]


class ConfigEditor(QGroupBox):
    """One editor per manifest key, emitting a minimal override dict.

    Args:
        manifest: ``{key: ParamSpec}`` describing the dict's keys.
        value: The current ``config`` dict (keys absent from it fall back to
            the manifest default).
        parent: Optional Qt parent.
        title: The group's title, e.g. ``"Configuration"``.
    """

    changed = Signal()

    def __init__(
        self,
        manifest: dict,
        value: Optional[dict] = None,
        parent: Optional[QWidget] = None,
        title: str = "Configuration",
    ) -> None:
        super().__init__(title, parent)
        self._manifest = manifest
        self._current = dict(value or {})
        self._widgets: dict[str, Any] = {}

        # Collapsed by default: a long manifest must not stretch the dialog it
        # sits in.  Unchecking hides the form (a plain checkable group only
        # greys it out), and a capped scroll area keeps it bounded once open.
        self.setCheckable(True)
        self.setChecked(False)

        form = QWidget(self)
        form_layout = QFormLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key, spec in self._manifest.items():
            editor = self._editor_for(spec, self._current.get(key, spec.default))
            self._widgets[key] = editor
            form_layout.addRow(key, editor)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setMaximumHeight(260)
        self._scroll.setWidget(form)
        self._scroll.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll)
        self.toggled.connect(self._scroll.setVisible)

    def _editor_for(self, spec: Any, current: Any) -> QWidget:
        """A widget for one key, with its help as a tooltip, emitting ``changed``."""
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
        if spec.help:
            widget.setToolTip(spec.help)
        return widget

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
