"""Qt editor for the external load-combination set.

Edits the typed ``{name: LoadCombination}`` mapping that
:func:`~fea_toolkit.model.load_combinations.combination_set_from_dict` builds —
the same artifact :func:`~fea_toolkit.io.combination_set.read_combination_set`
and ``write_combination_set`` persist.  Qt-only and self-contained: it holds no
model or file reference, so it is unit-tested the same way the other step-dialog
widgets are.
"""

from typing import Optional

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...model.sap_data import LoadCombination, LoadCombinationEntry

__all__ = ["CombinationDialog", "CombinationEditor"]

_COMBO_TYPES = ("Linear Add", "Envelope", "SRSS", "Absolute Add", "Range Add")


class CombinationEditor(QWidget):
    """Add / edit / delete load combinations in an external set."""

    def __init__(self, combinations, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._combinations = {
            name: LoadCombination(
                name=name,
                combo_type=combo.combo_type,
                entries=[
                    LoadCombinationEntry(e.name, e.factor, e.kind, e.mode, e.magnitude)
                    for e in combo.entries
                ],
                design=dict(combo.design),
            )
            for name, combo in (combinations or {}).items()
        }
        self._current: Optional[str] = None
        self._build_ui()

    # ── UI ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self._list = QListWidget(self)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        row.addWidget(self._list, 1)

        buttons = QVBoxLayout()
        for label, slot in (
            ("Add", self._on_add),
            ("Rename", self._on_rename),
            ("Delete", self._on_delete),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        layout.addLayout(row)

        layout.addWidget(QLabel("Type"))
        self._type = QComboBox(self)
        self._type.addItems(_COMBO_TYPES)
        self._type.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type)

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(["Reference", "Factor"])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

        entry_row = QHBoxLayout()
        add_entry = QPushButton("Add entry", self)
        add_entry.clicked.connect(self._on_add_entry)
        entry_row.addWidget(add_entry)
        remove_entry = QPushButton("Remove entry", self)
        remove_entry.clicked.connect(self._on_remove_entry)
        entry_row.addWidget(remove_entry)
        entry_row.addStretch(1)
        layout.addLayout(entry_row)

        self._list.blockSignals(True)
        for name in self._combinations:
            self._list.addItem(name)
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._load_combo(None)

    # ── List ↔ editor ──────────────────────────────────────────────────

    def _on_selection_changed(self, current, _previous) -> None:
        self._save_table()
        self._load_combo(current.text() if current is not None else None)

    def _load_combo(self, name: Optional[str]) -> None:
        self._current = name
        self._type.blockSignals(True)
        self._table.setRowCount(0)
        combo = self._combinations.get(name) if name else None
        if combo is None:
            self._type.setCurrentIndex(0)
            self._type.blockSignals(False)
            self._type.setEnabled(False)
            self._table.setEnabled(False)
            return
        self._type.setEnabled(True)
        self._table.setEnabled(True)
        self._type.setCurrentText(combo.combo_type)
        self._type.blockSignals(False)
        for entry in combo.entries:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item = QTableWidgetItem(entry.name)
            self._table.setItem(row, 0, item)
            spin = QDoubleSpinBox(self._table)
            spin.setRange(-1e12, 1e12)
            spin.setDecimals(12)
            spin.setValue(float(entry.factor))
            self._table.setCellWidget(row, 1, spin)

    def _save_table(self) -> None:
        if self._current is None:
            return
        combo = self._combinations.get(self._current)
        if combo is None:
            return
        original = list(combo.entries)
        entries = []
        for row in range(self._table.rowCount()):
            ref_item = self._table.item(row, 0)
            spin = self._table.cellWidget(row, 1)
            if ref_item is None or spin is None:
                continue
            ref = ref_item.text().strip()
            if not ref:
                continue
            factor = float(spin.value())
            prior = original[row] if row < len(original) else None
            if prior is not None and prior.name == ref:
                entries.append(
                    LoadCombinationEntry(
                        name=ref,
                        factor=factor,
                        kind=prior.kind,
                        mode=prior.mode,
                        magnitude=prior.magnitude,
                    )
                )
            else:
                entries.append(LoadCombinationEntry(name=ref, factor=factor))
        combo.entries = entries

    def _on_type_changed(self, index: int) -> None:
        if self._current is None or index < 0:
            return
        combo = self._combinations.get(self._current)
        if combo is not None:
            combo.combo_type = _COMBO_TYPES[index]

    # ── Combination list edits ─────────────────────────────────────────

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add combination", "Name:")
        name = name.strip()
        if not ok or not name or name in self._combinations:
            return
        self._combinations[name] = LoadCombination(name=name, combo_type="Linear Add")
        self._list.addItem(name)
        self._list.setCurrentRow(self._list.count() - 1)

    def _on_rename(self) -> None:
        if self._current is None:
            return
        name, ok = QInputDialog.getText(self, "Rename combination", "Name:", text=self._current)
        name = name.strip()
        if not ok or not name or name == self._current or name in self._combinations:
            return
        combo = self._combinations.pop(self._current)
        combo.name = name
        self._combinations[name] = combo
        self._current = name
        item = self._list.currentItem()
        if item is not None:
            item.setText(name)

    def _on_delete(self) -> None:
        if self._current is None:
            return
        self._combinations.pop(self._current, None)
        row = self._list.currentRow()
        self._list.takeItem(row)
        self._load_combo(None if self._list.count() == 0 else self._list.item(0).text())
        if self._list.count():
            self._list.setCurrentRow(0)

    # ── Entry edits ────────────────────────────────────────────────────

    def _on_add_entry(self) -> None:
        if self._current is None:
            return
        self._save_table()
        self._combinations[self._current].entries.append(LoadCombinationEntry(name="", factor=1.0))
        self._load_combo(self._current)

    def _on_remove_entry(self) -> None:
        if self._current is None:
            return
        self._save_table()
        combo = self._combinations[self._current]
        row = self._table.currentRow()
        if 0 <= row < len(combo.entries):
            del combo.entries[row]
        self._load_combo(self._current)

    # ── Read-back ──────────────────────────────────────────────────────

    def value(self) -> dict[str, LoadCombination]:
        """The edited set, with the in-progress entry table flushed."""
        self._save_table()
        return {
            name: LoadCombination(
                name=name,
                combo_type=combo.combo_type,
                entries=[
                    LoadCombinationEntry(e.name, e.factor, e.kind, e.mode, e.magnitude)
                    for e in combo.entries
                ],
                design=dict(combo.design),
            )
            for name, combo in self._combinations.items()
        }


class CombinationDialog(QDialog):
    """Wrap :class:`CombinationEditor` in an OK / Cancel dialog."""

    def __init__(self, combinations, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Load combinations")
        self._editor = CombinationEditor(combinations, self)
        layout = QVBoxLayout(self)
        layout.addWidget(self._editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(480, 420)

    def combinations(self) -> dict[str, LoadCombination]:
        return self._editor.value()

    @staticmethod
    def edit(combinations, parent: Optional[QWidget] = None):
        """Open the dialog; return the edited set, or ``None`` on cancel."""
        dialog = CombinationDialog(combinations, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.combinations()
