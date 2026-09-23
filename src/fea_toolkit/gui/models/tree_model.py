"""Lazy ``QAbstractItemModel`` over a model's group/entity tree.

Groups (Nodes, Materials, ...) appear as soon as a model is set; their
entities materialise only when a group is first expanded, so a model with
hundreds of thousands of elements costs nothing until it is browsed.  That is
why the tree is a ``QTreeView`` + custom model rather than a ``QTreeWidget``
(``docs/gui_roadmap.md`` design rule 6).

Indices carry an **integer node id**, resolved through :attr:`_nodes` and read
back with ``QModelIndex.internalId()``.  They deliberately do *not* use
``internalPointer()``: PySide6 6.11 segfaults there for an index built from an
integer (it resolves the integer as an address), and hands back a
half-constructed instance for an arbitrary object.
"""

from typing import Any, Optional

from qtpy.QtCore import QAbstractItemModel, QModelIndex, Qt
from qtpy.QtWidgets import QWidget

from .model_index import TreeGroup, build_groups

# A module-level null index: the Qt-idiomatic default argument, without calling
# QModelIndex() in a signature (ruff B008).
_ROOT = QModelIndex()


class ModelTreeModel(QAbstractItemModel):
    """Hierarchical, lazily-populated view of a ``SAPModelData`` / ``MeshModel``.

    Args:
        model: Optional model to populate from (``None`` gives an empty tree).
        parent: Optional Qt parent object.
    """

    def __init__(self, model: Optional[Any] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._groups: list = []
        self._group_ids: list = []
        self._children: dict = {}
        self._nodes: dict = {}
        self._next_id = 0
        self.set_model(model)

    # ── Population ──────────────────────────────────────────────────

    def set_model(self, model: Optional[Any]) -> None:
        """Rebuild the tree for *model*; ``None`` empties it.

        Args:
            model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``.
        """
        self.beginResetModel()
        self._groups = build_groups(model) if model is not None else []
        self._group_ids = []
        self._children = {}
        self._nodes = {}
        self._next_id = 0
        for group in self._groups:
            self._nodes[self._next_id] = group
            self._group_ids.append(self._next_id)
            self._next_id += 1
        self.endResetModel()

    # ── QAbstractItemModel ──────────────────────────────────────────

    def columnCount(self, parent: QModelIndex = _ROOT) -> int:
        return 2

    def rowCount(self, parent: QModelIndex = _ROOT) -> int:
        if not parent.isValid():
            return len(self._groups)
        group = self._group_of(parent)
        if group is None:
            return 0
        return len(self._children.get(group.key, ()))

    def hasChildren(self, parent: QModelIndex = _ROOT) -> bool:
        """A group always reports children, so its expander appears before
        ``fetchMore`` has materialised any rows (``rowCount`` is 0 until then)."""
        if not parent.isValid():
            return bool(self._groups)
        return parent.column() == 0 and self._is_group(parent)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = _ROOT,
    ) -> QModelIndex:
        if row < 0 or column < 0 or column >= self.columnCount():
            return _ROOT
        if not parent.isValid():
            if row >= len(self._groups):
                return _ROOT
            return self.createIndex(row, column, self._group_ids[row])
        group = self._group_of(parent)
        if group is None:
            return _ROOT
        rows = self._children.get(group.key, ())
        if row >= len(rows):
            return _ROOT
        return self.createIndex(row, column, rows[row][2])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return _ROOT
        node = self._nodes.get(index.internalId())
        if not isinstance(node, tuple):
            return _ROOT  # a group sits at the root
        group_row = node[0]
        if not 0 <= group_row < len(self._groups):
            return _ROOT
        return self.createIndex(group_row, 0, self._group_ids[group_row])

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self._nodes.get(index.internalId())

        if isinstance(node, TreeGroup):
            if role == Qt.ItemDataRole.DisplayRole:
                return node.label if index.column() == 0 else str(node.count)
            if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
                return f"{node.label}: {node.count}"
            return None

        if not isinstance(node, tuple):
            return None
        group_row, child_row = node
        if not 0 <= group_row < len(self._groups):
            return None
        rows = self._children.get(self._groups[group_row].key, ())
        if child_row >= len(rows):
            return None
        label, entity, _node_id = rows[child_row]
        if role == Qt.ItemDataRole.DisplayRole:
            return label if index.column() == 0 else ""
        if role == Qt.ItemDataRole.UserRole:
            return entity  # the inspector reads this
        return None

    def headerData(
        self,
        section: int,
        orientation: int,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return ("Name", "Count")[section] if 0 <= section < 2 else None
        return None

    # ── Lazy loading ────────────────────────────────────────────────

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if not parent.isValid():
            return False
        node = self._nodes.get(parent.internalId())
        return isinstance(node, TreeGroup) and node.key not in self._children

    def fetchMore(self, parent: QModelIndex) -> None:
        group = self._group_of(parent) if self._is_group(parent) else None
        if group is None or group.key in self._children:
            return
        group_row = self._groups.index(group)
        items = group.load_items()
        if not items:
            self._children[group.key] = []
            return
        rows = []
        for child_row, (label, entity) in enumerate(items):
            node_id = self._next_id
            self._nodes[node_id] = (group_row, child_row)
            self._next_id += 1
            rows.append((label, entity, node_id))
        self.beginInsertRows(parent, 0, len(rows) - 1)
        self._children[group.key] = rows
        self.endInsertRows()

    # ── Lookup ──────────────────────────────────────────────────────

    def index_for(self, group_key: str, label: str) -> Optional[QModelIndex]:
        """Row of the entity labelled *label* inside the group *group_key*.

        Materialises the group's rows if the user never expanded it -- the tree
        is lazy, and a viewport pick can name an entity nobody has browsed to.

        Args:
            group_key: Group key, e.g. ``"frame_elements"``.
            label: The entity's SAP label, as the row displays it.

        Returns:
            The child index, or ``None`` when the group or label is unknown.
        """
        group_row = next(
            (row for row, group in enumerate(self._groups) if group.key == group_key),
            None,
        )
        if group_row is None:
            return None
        parent = self.index(group_row, 0)
        # ``fetchMore`` is itself a no-op once the group is loaded.
        self.fetchMore(parent)
        for child_row, (child_label, _entity, _node_id) in enumerate(
            self._children.get(group_key, ())
        ):
            if str(child_label) == str(label):
                return self.index(child_row, 0, parent)
        return None

    # ── Helpers ─────────────────────────────────────────────────────

    def _is_group(self, index: QModelIndex) -> bool:
        return isinstance(self._nodes.get(index.internalId()), TreeGroup)

    def _group_of(self, index: QModelIndex) -> Optional[TreeGroup]:
        """The group an index belongs to, or ``None`` for an invalid index."""
        if not index.isValid():
            return None
        node = self._nodes.get(index.internalId())
        if isinstance(node, TreeGroup):
            return node
        if isinstance(node, tuple) and 0 <= node[0] < len(self._groups):
            return self._groups[node[0]]
        return None
