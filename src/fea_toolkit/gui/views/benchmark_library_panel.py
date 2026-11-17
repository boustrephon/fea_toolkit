"""The example chooser — the validated benchmarks and the example builders.

Lists the ``build_model`` *builders* (from the verb, which owns their names and
titles) first, then the ``benchmarks/catalogue.py`` entries with their tier and
status badge.  Choosing one emits ``open_requested`` with a namespaced id —
``builder:<name>`` for a builder, a plain catalogue id for a benchmark — which
the main window turns into a preset recipe (``builder_recipe`` /
``benchmark_recipe``).  The panel imports only the OpenSees-free catalogue and
the verb's titles, so it renders before any model is open.

A benchmark with no GUI builder (validated only in its test) is listed dimmed
and cannot be opened — the "gray-for-missing" principle: every benchmark is
listed, and only the openable ones are offered.
"""

from typing import Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...benchmarks.catalogue import BENCHMARK_ENTRIES, STATUS_BADGES
from ...workflow.verbs.build_model import BUILDER_TITLES


class BenchmarkLibraryPanel(QWidget):
    """A scrollable list of builders and benchmarks, with an Open action.

    Args:
        parent: Optional Qt parent widget.
    """

    #: Emitted with ``builder:<name>`` or a catalogue ``BenchmarkEntry.id``.
    open_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._list = QListWidget(self)
        self._list.setObjectName("benchmark_library_list")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemActivated.connect(self._on_activated)
        layout.addWidget(self._list, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._open_btn = QPushButton("Open", self)
        self._open_btn.setToolTip("Preset a build_model + mesh recipe for the selected benchmark")
        self._open_btn.clicked.connect(self._on_open_clicked)
        button_row.addWidget(self._open_btn)
        layout.addLayout(button_row)

        hint = QLabel(
            "Choose an example model or benchmark to preset a build_model + "
            "mesh recipe; edit the step's parameters (e.g. the frame code), "
            "then run."
        )
        hint.setWordWrap(True)
        hint.setObjectName("benchmark_library_hint")
        layout.addWidget(hint)

    def _reload(self) -> None:
        self._list.clear()
        # The example builders first — a user starts from one of these, not
        # from a validated benchmark.  The id is namespaced ``builder:`` so the
        # opener can tell a builder from a benchmark.
        for builder, title in BUILDER_TITLES.items():
            item = QListWidgetItem(f"Build — {title}")
            item.setData(Qt.ItemDataRole.UserRole, f"builder:{builder}")
            item.setToolTip(title)
            self._list.addItem(item)
        for entry in BENCHMARK_ENTRIES:
            badge = STATUS_BADGES.get(entry.status, "")
            item = QListWidgetItem(f"{badge}  {entry.tier}  {entry.title}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            if entry.builder is None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip("Validated in its test scripts only — no GUI-openable builder")
            else:
                item.setToolTip(entry.title)
            self._list.addItem(item)

    def _selected_id(self) -> Optional[str]:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_activated(self, item: QListWidgetItem) -> None:
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        if entry_id:
            self.open_requested.emit(entry_id)

    def _on_open_clicked(self) -> None:
        entry_id = self._selected_id()
        if entry_id:
            self.open_requested.emit(entry_id)
