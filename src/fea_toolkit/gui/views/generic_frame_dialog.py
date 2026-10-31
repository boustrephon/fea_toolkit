"""IK-2005 generic-frame benchmark chooser.

A small modal dialog that picks one of the 12 Medina Appendix-A generic frames
(``generic_frame_data.FRAME_DATA``) and hands the four-digit code back to the
caller (``MainWindow._on_open_generic_frame``), which builds and displays the
model via :func:`fea_toolkit.benchmarks.generic_frame.build_model`.

``FRAME_DATA`` is imported inside :meth:`GenericFrameDialog.__init__`, so
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

__all__ = ["GenericFrameDialog"]


class GenericFrameDialog(QDialog):
    """Pick an IK-2005 generic frame (code, storeys, T1)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Open generic-frame benchmark")

        from ...benchmarks.generic_frame_data import FRAME_DATA

        self._frame = QComboBox(self)
        for code in FRAME_DATA:
            meta = FRAME_DATA[code]
            self._frame.addItem(f"{code}  —  N = {meta['N']}, T1 = {meta['T1']:.1f} s", code)

        form = QFormLayout()
        form.addRow("Frame", self._frame)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(360, 120)

    def value(self) -> str:
        """The selected frame code (e.g. ``"0909"``)."""
        return self._frame.currentData()

    @staticmethod
    def edit(parent: Optional[QWidget] = None) -> Optional[str]:
        """Open the dialog modally; return the code, or ``None`` on cancel."""
        dialog = GenericFrameDialog(parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.value()
