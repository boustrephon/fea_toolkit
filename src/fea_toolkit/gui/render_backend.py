"""Qt render backend bridging ModelViewer to an embedded PyVista/Qt viewport."""

from typing import Any

from pyvistaqt import QtInteractor

from ..plotting.renderers.pyvista import PyVistaRenderer


class MainThreadQtInteractor(QtInteractor):
    """``QtInteractor`` that renders **without spawning a thread**.

    On macOS, pyvistaqt wraps ``render()`` in a ``threading.Thread`` -- the
    decorator is ``@conditional_decorator(threaded, platform.system() == "Darwin")``
    in ``pyvistaqt/plotting.py``.  That thread does nothing but ``emit()`` the
    render signal: the render itself runs on the Qt thread from that signal,
    exactly as it does on Linux and Windows, where pyvistaqt creates no thread at
    all.

    This subclass emits the signal on the calling thread, which restores the
    cross-platform behaviour.  It matters because a thread created per render is
    both wasteful and hazardous here: if the cyclic collector runs while that
    thread bootstraps, the process segfaults, shiboken/VTK objects not being
    traversable from another thread (reproduced in this project's GUI tests --
    see ``docs/dev_notes.md`` → *The macOS GUI segfault*).

    Rendering is only ever driven from the GUI thread, in response to user
    actions, so the deferred emit the thread provided is not needed.
    """

    def render(self) -> None:
        """Emit the render signal on this thread (pyvistaqt's Linux path)."""
        # Never render after the plotter has been closed: the render window has
        # been finalized and touching its OpenGL context can crash (pyvistaqt
        # issue #762).
        if getattr(self, "_closed", False):
            return None
        self._rendered = True  # BasePlotter needs to know this has rendered
        try:
            return self.render_signal.emit()
        except RuntimeError:  # the wrapped C/C++ object has been deleted
            return None


def make_select_style(button: str) -> Any:
    """A trackball camera style whose *button* cannot orbit.

    Select mode (``docs/_pending_work.md`` P23) has to be able to *disable*
    rotation, because a drag in that mode is a rubber-band selection.  This
    style subclasses ``vtkInteractorStyleTrackballCamera`` and swallows the
    select button's press/release, so a drag over that button leaves the camera
    untouched while the marquee gesture runs; the other buttons (zoom / pan)
    keep their normal behaviour.

    Args:
        button: The policy's pick button, ``"left"`` or ``"right"``.

    Returns:
        A fresh style instance, ready to hand to ``SetInteractorStyle``.
    """
    import vtk

    class _SelectStyle(vtk.vtkInteractorStyleTrackballCamera):
        select_button = button

        def OnLeftButtonDown(self):
            if self.select_button != "left":
                super().OnLeftButtonDown()

        def OnLeftButtonUp(self):
            if self.select_button != "left":
                super().OnLeftButtonUp()

        def OnRightButtonDown(self):
            if self.select_button != "right":
                super().OnRightButtonDown()

        def OnRightButtonUp(self):
            if self.select_button != "right":
                super().OnRightButtonUp()

    return _SelectStyle()


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
