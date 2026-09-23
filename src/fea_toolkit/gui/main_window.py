"""Main application window for the fea_toolkit desktop GUI.

Milestone 1 is deliberately minimal: a :class:`QMainWindow` whose central
widget is an embedded ``pyvistaqt.QtInteractor`` rendering a model through the
existing :class:`~fea_toolkit.plotting.viewer.ModelViewer`.  The menubar, dock
layout, trees and toolbars arrive in later milestones -- see
``docs/gui_roadmap.md`` section 9.6.
"""

import contextlib
from typing import Any, Optional

from qtpy.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from .render_backend import QtRenderBackend


class MainWindow(QMainWindow):
    """Top-level window hosting the 3-D viewport.

    Args:
        model: Optional model to render immediately -- a ``SAPModelData``, a
            ``MeshModel`` or an ``AnalysisBuilder``.
        parent: Optional Qt parent widget.
    """

    def __init__(self, model: Optional[Any] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("fea_toolkit")

        # Quad-view-ready container: holds a single viewport today, a grid of
        # them later (roadmap design rule 10).
        self._viewport_container = QWidget(self)
        self._viewport_layout = QVBoxLayout(self._viewport_container)
        self._viewport_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(self._viewport_container)

        self._interactor = None
        self._backend = None
        self._viewer = None

        self._create_viewport()
        if model is not None:
            self.show_model(model)

    # -- Viewport ------------------------------------------------------

    def _create_viewport(self) -> None:
        """Create the embedded PyVistaQt interactor and its render backend."""
        from pyvistaqt import QtInteractor

        self._interactor = QtInteractor(self._viewport_container)
        self._viewport_layout.addWidget(self._interactor)
        self._backend = QtRenderBackend(self._interactor)

    def show_model(self, model: Any, color_by_section: bool = True) -> None:
        """Render *model* into the embedded viewport.

        Args:
            model: A ``SAPModelData``, a ``MeshModel`` or an ``AnalysisBuilder``.
            color_by_section: Colour elements by section name.
        """
        from ..model.mesh_model import MeshModel
        from ..model.sap_data import SAPModelData
        from ..plotting.viewer import ModelViewer

        if isinstance(model, MeshModel):
            viewer = ModelViewer(mesh_model=model, backend=self._backend)
        elif isinstance(model, SAPModelData):
            viewer = ModelViewer(model_data=model, backend=self._backend)
        else:
            viewer = ModelViewer(builder=model, backend=self._backend)

        viewer.show_model(show_nodes=True, color_by_section=color_by_section)
        self._viewer = viewer
        self._interactor.reset_camera()

    def closeEvent(self, event):
        """Release the VTK render window before the window closes."""
        if self._interactor is not None:
            with contextlib.suppress(Exception):
                self._interactor.close()
        super().closeEvent(event)
