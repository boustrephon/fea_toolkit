"""A modeless chooser for the example builders and validated benchmarks.

The persistent Benchmark Library *dock* was replaced by this dialog: choosing
an example is an entry point — used once to preset a ``build_model`` + ``mesh``
recipe, which then lives in the Recipe dock — not a workspace, so it should not
hold dock space beside the Model Tree and the Recipe dock
(``docs/gui_roadmap.md`` §1.1 reserves the left dock for the trees and the
inspector).
"""

from typing import Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QDialog, QVBoxLayout, QWidget

from .benchmark_library_panel import BenchmarkLibraryPanel


class BenchmarkDialog(QDialog):
    """Host :class:`BenchmarkLibraryPanel` in a reusable, modeless dialog.

    Args:
        parent: Optional Qt parent widget.
    """

    #: Re-emitted with the chosen id (``builder:<name>`` or a benchmark id).
    open_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Open example")
        self.setModal(False)
        layout = QVBoxLayout(self)
        self.panel = BenchmarkLibraryPanel(self)
        layout.addWidget(self.panel)
        self.panel.open_requested.connect(self._on_open)

    def _on_open(self, example_id: str) -> None:
        """Forward a choice and close the dialog."""
        self.open_requested.emit(example_id)
        self.accept()
