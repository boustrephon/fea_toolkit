"""Benchmark model chooser (FEMA 355C / SAC Phase 2).

A small modal dialog that picks one of the encoded FEMA 355C benchmark
schedules and a 2D/3D direction, then hands the choice back to the caller
(``MainWindow._on_open_benchmark``), which builds and displays the model via
:func:`fea_toolkit.benchmarks.fema355c.build_fema355c`.

The benchmark registry is imported inside :meth:`BenchmarkDialog.__init__`, so
importing this module stays light — it does not load the ``benchmarks``
package (and therefore not OpenSees) until a dialog is actually opened.
"""

from __future__ import annotations

from typing import Optional

from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

__all__ = ["BenchmarkDialog"]

#: Friendly display labels; the stored ``itemData`` values are the
#: ``build_fema355c`` codes.
_REGION_LABELS = {
    "LA": "Los Angeles (LA)",
    "SE": "Seattle (SE)",
    "BO": "Boston (BO)",
}
_STORY_LABELS = {3: "3-story", 9: "9-story", 20: "20-story"}
_DESIGN_LABELS = {
    "pre": "Pre-Northridge",
    "post": "Post-Northridge",
    "rbs": "Reduced Beam Section (RBS)",
    "r1": "Redesign R1",
    "r2": "Redesign R2 (15-ft bays)",
    "r3": "Redesign R3 (3 moment bays)",
}
_DIRECTION_LABELS = {
    "3d": "3D \u2014 full building",
    "2d": "2D \u2014 planar N-S moment frame",
}


class BenchmarkDialog(QDialog):
    """Pick a FEMA 355C / SAC benchmark model (region, stories, design, direction).

    The **Design** choices follow the (region, stories) selection: only the
    variants actually encoded in
    :func:`fea_toolkit.benchmarks.fema355c.schedules` are offered, so the
    dialog can never produce an invalid combination.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Open benchmark model")

        from ...benchmarks.fema355c import schedules as fema_schedules

        self._by_region: dict[str, dict[int, list[str]]] = {}
        for region, stories, design in fema_schedules():
            self._by_region.setdefault(region, {}).setdefault(stories, []).append(design)

        self._region = QComboBox(self)
        self._stories = QComboBox(self)
        self._design = QComboBox(self)
        self._direction = QComboBox(self)

        for region in self._by_region:
            self._region.addItem(_REGION_LABELS[region], region)
        for stories in sorted(_STORY_LABELS):
            self._stories.addItem(_STORY_LABELS[stories], stories)
        for direction in ("3d", "2d"):
            self._direction.addItem(_DIRECTION_LABELS[direction], direction)

        self._region.currentIndexChanged.connect(self._refresh_designs)
        self._stories.currentIndexChanged.connect(self._refresh_designs)
        self._refresh_designs()

        form = QFormLayout()
        form.addRow("Region", self._region)
        form.addRow("Stories", self._stories)
        form.addRow("Design", self._design)
        form.addRow("Direction", self._direction)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(360, 160)

    # ── Design combo follows region + stories ──
    def _current_region(self) -> str:
        return self._region.currentData()

    def _current_stories(self) -> int:
        return self._stories.currentData()

    def _refresh_designs(self) -> None:
        designs = self._by_region[self._current_region()][self._current_stories()]
        self._design.blockSignals(True)
        self._design.clear()
        for design in designs:
            self._design.addItem(_DESIGN_LABELS[design], design)
        self._design.blockSignals(False)

    def value(self) -> dict[str, object]:
        """The selected ``region`` / ``stories`` / ``design`` / ``direction``."""
        return {
            "region": self._current_region(),
            "stories": self._current_stories(),
            "design": self._design.currentData(),
            "direction": self._direction.currentData(),
        }

    @staticmethod
    def edit(parent: Optional[QWidget] = None) -> Optional[dict[str, object]]:
        """Open the dialog modally; return the choice, or ``None`` on cancel."""
        dialog = BenchmarkDialog(parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.value()
