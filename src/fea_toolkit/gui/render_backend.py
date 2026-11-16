"""Qt render backend bridging ModelViewer to an embedded PyVista/Qt viewport."""

import contextlib
from typing import Any, Optional

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
        mirrors: Optional list of additional plotters that show the *same*
            scene.  The backend renders into *plotter* and mirrors every actor
            into these, sharing the same ``vtkActor`` objects — so a
            visibility / opacity change on an actor updates every view at
            once, and the scene is held in memory once.  Used by the quad view
            (iso / front / top / side) so one render feeds four viewports.
    """

    def __init__(self, plotter: Any = None, mirrors: Optional[list] = None) -> None:
        super().__init__(plotter=plotter)
        self._mirrors: list = []
        if mirrors:
            self.set_mirrors(mirrors)

    def set_mirrors(self, mirrors: list) -> None:
        """Set the secondary plotters this backend mirrors its actors into."""
        self._mirrors = list(mirrors)

    def render_mirrors(self) -> None:
        """Re-render every mirror plotter, best effort."""
        for plotter in self._mirrors:
            with contextlib.suppress(Exception):
                plotter.render()

    def _add_actor(self, actor: Any, category: str) -> None:
        """Track *actor* and add it to every mirror viewport too."""
        super()._add_actor(actor, category)
        for plotter in self._mirrors:
            with contextlib.suppress(Exception):
                plotter.add_actor(actor)

    def clear(self) -> None:
        """Remove every actor from the primary *and* the mirror viewports."""
        for actor in self._actors:
            for plotter in self._mirrors:
                with contextlib.suppress(Exception):
                    plotter.remove_actor(actor, render=False)
        super().clear()
        self.render_mirrors()

    def clear_category(self, category: str) -> None:
        """Remove one overlay from the primary and every mirror viewport."""
        for actor in self._categories.get(category, []):
            for plotter in self._mirrors:
                with contextlib.suppress(Exception):
                    plotter.remove_actor(actor, render=False)
        super().clear_category(category)
        self.render_mirrors()

    def set_category_visible(self, category: str, visible: bool) -> None:
        """Show/hide a category; the shared actors update every viewport."""
        super().set_category_visible(category, visible)
        self.render_mirrors()

    def set_category_opacity(self, category: str, opacity: float) -> None:
        """Set a category's opacity; the shared actors update every viewport."""
        super().set_category_opacity(category, opacity)
        self.render_mirrors()

    def show(self) -> None:
        """No-op -- the embedding Qt application owns the event loop.

        The standalone PyVista ``show()`` blocks on a modal render window; in
        an embedded viewport the host application's ``QApplication.exec()``
        already drives rendering, so this deliberately does nothing.
        """
        return None
