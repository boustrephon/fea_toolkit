"""Property inspector: the selected entity's fields, read-only.

Small and fixed-size by nature (a handful of fields for one object), so a
``QTableWidget`` is appropriate here -- unlike the Model Tree, which must stay
lazy (``docs/gui_roadmap.md`` design rule 6).
"""

from dataclasses import fields, is_dataclass
from typing import Any, Optional

from qtpy.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Attribute names that identify an entity, tried in order.
_ID_ATTRS = ("elem_id", "area_id", "node_id", "name", "id")
_MAX_VALUE_CHARS = 120


def _display(value: Any) -> str:
    """Format a field value compactly for a table cell."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple, dict, set)):
        text = repr(value)
        return text if len(text) <= _MAX_VALUE_CHARS else text[: _MAX_VALUE_CHARS - 3] + "..."
    return str(value)


def describe(obj: Any) -> list:
    """Return ``[(field_name, display_value), ...]`` for *obj*.

    Dataclass instances (the model layer's normal shape) are described field by
    field; anything else falls back to its ``__dict__``.

    Args:
        obj: The object to describe, or ``None``.

    Returns:
        One ``(name, value)`` pair per field, or an empty list.
    """
    if obj is None:
        return []
    if is_dataclass(obj) and not isinstance(obj, type):
        return [(f.name, _display(getattr(obj, f.name, None))) for f in fields(obj)]
    if hasattr(obj, "__dict__"):
        return [(key, _display(val)) for key, val in sorted(vars(obj).items())]
    return []


def object_title(obj: Any) -> str:
    """A short ``"ClassName  id"`` heading for *obj* (``""`` for ``None``)."""
    if obj is None:
        return "No selection"
    for attr in _ID_ATTRS:
        value = getattr(obj, attr, None)
        if value:
            return f"{type(obj).__name__}  {value}"
    return type(obj).__name__


def _support_rows(obj: Any, model: Any) -> list:
    """A node's support rows — restraints and its joint constraint.

    Only nodes have them, and a node's conditions live on the *model* rather than
    on the node, so both the object and the context have to agree.  Anything else,
    and any model that carries none (a results archive), reports nothing.
    """
    from ...model.sap_data import Node
    from ...model.supports import support_rows

    if model is None or not isinstance(obj, Node):
        return []
    return support_rows(model, obj.node_id)


class PropertyInspector(QWidget):
    """Read-only property table for the current selection.

    Args:
        parent: Optional Qt parent widget.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._source_model: Any = None
        self._title = QLabel(object_title(None), self)
        self._title.setEnabled(False)

        table = QTableWidget(0, 2, self)
        table.setObjectName("inspector_table")
        table.setHorizontalHeaderLabels(["Property", "Value"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table = table

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._title)
        layout.addWidget(table)

    def set_source_model(self, model: Any) -> None:
        """Set the model that supplies a node's restraints and constraint.

        The Inspector describes one *object*, but support conditions are stored
        on the model — ``restraints`` and ``constraint_assignments`` are keyed by
        node id — so the context is set alongside the display rather than threaded
        through every :meth:`show_object` call.  ``None`` (no model, or a results
        archive, which carries none) adds no support rows.

        Args:
            model: A ``SAPModelData`` or ``MeshModel``, or ``None``.
        """
        self._source_model = model

    def show_object(self, obj: Any) -> None:
        """Display *obj*'s fields (``None`` clears the inspector).

        A node also reports its support conditions — restrained DOFs and its
        joint constraint — taken from the model set by
        :meth:`set_source_model`.
        """
        self._title.setText(object_title(obj))
        self._title.setEnabled(obj is not None)
        rows = describe(obj) + _support_rows(obj, self._source_model)
        self._table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self._table.setItem(row, 0, QTableWidgetItem(name))
            self._table.setItem(row, 1, QTableWidgetItem(value))
