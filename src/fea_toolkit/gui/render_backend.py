"""Qt render backend bridging ModelViewer to an embedded PyVista/Qt viewport."""

from typing import Any

from ..plotting.renderers.pyvista import PyVistaRenderer


class QtRenderBackend(PyVistaRenderer):
    """Render backend that draws into a ``pyvistaqt.QtInteractor``.

    This is :class:`~fea_toolkit.plotting.renderers.pyvista.PyVistaRenderer`
    with an **injected** plotter: ``pyvistaqt.QtInteractor`` is a
    ``pyvista.Plotter`` subclass that owns a Qt widget, so every ``render_*``
    method works unchanged.  Only :meth:`show` differs -- the Qt event loop
    owns rendering, so an embedded backend must never block on ``show()``.

    Args:
        plotter: A live ``pyvistaqt.QtInteractor`` (or any ``pyvista.Plotter``)
            to render into.
    """

    def __init__(self, plotter: Any) -> None:
        super().__init__(plotter=plotter)

    def show(self) -> None:
        """No-op -- the embedding Qt application owns the event loop.

        The standalone PyVista ``show()`` blocks on a modal render window; in
        an embedded viewport the host application's ``QApplication.exec()``
        already drives rendering, so this deliberately does nothing.
        """
        return None
