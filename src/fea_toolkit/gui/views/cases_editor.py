"""Structured editor for the ``run_static`` ``cases`` parameter.

The parameter is ``{case: {pattern: factor}}``, and an empty dict means "solve
every static case the model defines".  This widget lists the model's analysable
static cases (:func:`fea_toolkit.analysis.case_listing.list_static_cases`) with a
check box each, and a pattern→factor table for the selected case, so a recipe can
pick a subset or override a factor without hand-writing the dict.

It is Qt-only and self-contained: it holds no model reference beyond the
``{case: {pattern: factor}}`` listing it is seeded with, so it can be unit-tested
the same way the rest of the step dialog is.
"""

from typing import Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

__all__ = ["CasesEditor"]


class CasesEditor(QWidget):
    """Pick the static cases to solve and edit each pattern's factor.

    Args:
        static_cases: The model's analysable cases as ``{case: {pattern: factor}}``,
            exactly what :func:`fea_toolkit.analysis.case_listing.list_static_cases`
            returns.
        current: The step's current ``cases`` value (or ``None``).  Non-empty
            means "solve only these cases"; empty means "solve every case".
        parent: Optional Qt parent widget.
    """

    def __init__(
        self,
        static_cases: dict,
        current: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._static_cases = static_cases or {}
        self._case_order = list(self._static_cases)
        self._factors = {case: dict(pats) for case, pats in self._static_cases.items()}
        for case, pats in (current or {}).items():
            self._factors.setdefault(case, {}).update(dict(pats))
        self._checked = {case: (case in current) if current else True for case in self._case_order}
        #: The case whose patterns are currently shown in the table, so
        #: ``_save_table`` writes factors back to the *right* case when the
        #: list selection changes (``currentItemChanged`` fires after the list
        #: already points at the new row).
        self._current_case: Optional[str] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if not self._case_order:
            layout.addWidget(QLabel("No static load cases in the model."))
            self._list = None
            self._table = None
            return

        self._list = QListWidget(self)
        for case in self._case_order:
            item = QListWidgetItem(case)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if self._checked[case] else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._on_case_changed)
        layout.addWidget(self._list)

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(["Pattern", "Factor"])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

        self._list.setCurrentRow(0)

    # ── Case ↔ pattern table ─────────────────────────────────────────

    def _on_case_changed(self, current: Optional[QListWidgetItem], _previous) -> None:
        self._save_table()
        if current is not None:
            self._load_table(current.text())

    def _load_table(self, case: str) -> None:
        self._current_case = case
        self._table.setRowCount(0)
        for pattern, factor in self._factors.get(case, {}).items():
            row = self._table.rowCount()
            self._table.insertRow(row)
            item = QTableWidgetItem(pattern)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, item)
            spin = QDoubleSpinBox(self._table)
            spin.setRange(-1e12, 1e12)
            spin.setDecimals(6)
            spin.setValue(float(factor))
            self._table.setCellWidget(row, 1, spin)

    def _save_table(self) -> None:
        if self._table is None or self._current_case is None:
            return
        factors = {}
        for row in range(self._table.rowCount()):
            pattern = self._table.item(row, 0).text()
            factors[pattern] = float(self._table.cellWidget(row, 1).value())
        self._factors[self._current_case] = factors

    # ── Read-back ─────────────────────────────────────────────────────

    def value(self) -> dict:
        """The edited cases dict, or ``{}`` when every case is solved unmodified.

        ``{}`` is the verb's "solve all" sentinel; it is returned only when every
        case is checked *and* no factor differs from the model's own, so a recipe
        that was not touched keeps its default meaning.
        """
        self._save_table()
        if self._list is not None:
            for row in range(self._list.count()):
                item = self._list.item(row)
                self._checked[item.text()] = item.checkState() == Qt.CheckState.Checked

        all_cases = set(self._case_order)
        selected = {case for case in self._case_order if self._checked[case]}
        if selected == all_cases and all(
            self._factors[case] == self._static_cases[case] for case in self._case_order
        ):
            return {}
        return {case: dict(self._factors[case]) for case in self._case_order if self._checked[case]}
