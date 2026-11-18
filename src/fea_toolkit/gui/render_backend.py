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


#: Decoration categories that render only in the **active** viewport.  These
#: are the non-pickable annotation glyphs anchored to the model — the selection
#: cue, support symbols, text labels and applied-load glyphs.  Copying them into
#: the other three projections would re-draw the same symbol at a different
#: angle, where it reads as misplaced.  Every other category is rendered as an
#: independent **copy** in each viewport, so the four panes never share a
#: ``vtkActor`` (see :meth:`_add_actor`).
_PER_VIEW_CATEGORIES = frozenset(
    {
        "highlights",
        "restraints",
        "annotations",
        "loads_nodes",
        "loads_frames",
        "loads_shells",
    }
)


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
            scene.  The backend renders into *plotter* and gives each mirror an
            independent copy of every actor (same geometry, fresh mapper), so
            the four panes never share a ``vtkActor`` -- screen-space
            properties such as point size stay correct in every view, and the
            geometry is still held once.  Used by the quad view (iso / front /
            top / side) so one render feeds four viewports.  Annotation glyphs
            -- the selection highlight, support symbols, labels and
            applied-load glyphs -- are the exception: they stay in the active
            viewport (see :meth:`set_active_viewport`).
    """

    def __init__(self, plotter: Any = None, mirrors: Optional[list] = None) -> None:
        super().__init__(plotter=plotter)
        self._mirrors: list = []
        #: The viewport annotation glyphs render into — the active view.
        self._active_viewport: Any = plotter
        #: ``category -> [(mirror_plotter, copy_actor), …]`` — the independent
        #: copies mirrored into the secondary viewports, keyed by category so
        #: a category clear or visibility change reaches them too.
        self._mirror_copies: dict[str, list[tuple[Any, Any]]] = {}
        #: ``id(copy_actor) -> category`` — reverse lookup so a pick in a
        #: secondary viewport (which returns a copy) resolves to its category.
        self._copy_category: dict[int, str] = {}
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

    def _all_viewports(self) -> list:
        """The primary and mirror plotters, deduplicated, primary first."""
        viewports: list = []
        seen: set = set()
        for plotter in [self._plotter, *self._mirrors]:
            if plotter is None or id(plotter) in seen:
                continue
            seen.add(id(plotter))
            viewports.append(plotter)
        return viewports

    def set_active_viewport(self, plotter: Any) -> None:
        """Route the annotation glyphs to *plotter* (the active viewport).

        Called when the active view changes: every existing per-view annotation
        actor is re-homed to the new viewport, so a symbol already on screen
        follows the pane the user is now working in rather than staying in the
        pane they last clicked.
        """
        if plotter is self._active_viewport:
            return
        self._active_viewport = plotter
        for category in _PER_VIEW_CATEGORIES:
            for actor in self._categories.get(category, []):
                self._restrict_to_active_viewport(actor, render=False)
        with contextlib.suppress(Exception):
            self._plotter.render()
        self.render_mirrors()

    def _restrict_to_active_viewport(self, actor: Any, *, render: bool = True) -> None:
        """Keep *actor* (a per-view annotation) in the active viewport only.

        ``add_mesh`` dropped the actor into the primary plotter; remove it from
        every viewport and add it back to just the active one, so a support
        symbol or load glyph is not re-drawn in a pane it was not shown in —
        where, seen from a different camera, it would read as misplaced.

        ``render=False`` defers the redraw so a bulk relocation (re-homing every
        annotation when the active viewport changes) renders once at the end
        rather than once per actor; initial glyph creation keeps the default
        ``render=True``.  When the active viewport is unset, the actor is kept
        in the primary plotter rather than dropped.
        """
        target = self._active_viewport or self._plotter
        pickable = bool(actor.GetPickable())
        for plotter in self._all_viewports():
            with contextlib.suppress(Exception):
                plotter.remove_actor(actor, render=False)
        if target is not None:
            with contextlib.suppress(Exception):
                target.add_actor(actor, pickable=pickable, render=render)

    def _add_actor(self, actor: Any, category: str) -> None:
        """Track *actor* and give every mirror viewport an independent copy.

        PyVista's ``Plotter.add_actor`` defaults to ``pickable=True`` and calls
        ``actor.SetPickable(True)`` — so copying a *non-pickable* actor (an
        overlay, an extruded-solid decoration) through it would silently
        re-enable picking on the copy.  The actor's own flag is passed back so
        ``SetPickable(False)`` survives the copy.

        Each mirror gets its **own** actor (a deep copy with a fresh mapper) so
        the four panes never share a ``vtkActor``: screen-space properties such
        as a node marker's point size are computed per renderer, and sharing
        one actor across four cameras made those render in the wrong place.

        Annotation glyphs are the exception: they belong to the **active**
        viewport alone (the pane the user clicked in), so a support symbol or
        load glyph is not re-drawn in the other three panes.
        """
        super()._add_actor(actor, category)
        if category in _PER_VIEW_CATEGORIES:
            self._restrict_to_active_viewport(actor)
            return
        pickable = bool(actor.GetPickable())
        for plotter in self._mirrors:
            with contextlib.suppress(Exception):
                copy = actor.copy(deep=True)
                plotter.add_actor(copy, pickable=pickable)
                self._mirror_copies.setdefault(category, []).append((plotter, copy))
                self._copy_category[id(copy)] = category

    def _drop_mirror_copies(self, entries: list[tuple[str, list[tuple[Any, Any]]]]) -> None:
        """Remove the given ``(category, [(plotter, copy), …])`` mirror copies."""
        for _category, pairs in entries:
            for plotter, copy in pairs:
                with contextlib.suppress(Exception):
                    plotter.remove_actor(copy, render=False)
                self._copy_category.pop(id(copy), None)

    def all_actors(self, category: str) -> list:
        """Primary actors of *category* plus their per-viewport mirror copies.

        The union of a category's actors across every viewport — the primary
        actors plus one independent copy per mirror.  To scope a list to a
        single viewport (a picker's node list), use :meth:`actors_in`.
        """
        actors = list(self._categories.get(category, ()))
        for _plotter, copy in self._mirror_copies.get(category, []):
            actors.append(copy)
        return actors

    def actors_in(self, category: str, plotter: Any) -> list:
        """Actors of *category* rendered in *plotter* (primary or a mirror).

        The primary plotter owns the category's own actors; each mirror owns its
        independent copies.  Used to give each viewport's picker only the node
        cloud that viewport actually renders, so a pick in a secondary pane
        resolves to an actor local to that pane.
        """
        if plotter is self._plotter:
            return list(self._categories.get(category, ()))
        return [copy for mirror, copy in self._mirror_copies.get(category, []) if mirror is plotter]

    def category_of_actor(self, actor: Any) -> Optional[str]:
        """Resolve *actor* to its render category, including mirror copies."""
        category = self._copy_category.get(id(actor))
        if category is not None:
            return category
        return super().category_of_actor(actor)

    def clear(self) -> None:
        """Remove every actor from the primary *and* the mirror viewports."""
        self._drop_mirror_copies(list(self._mirror_copies.items()))
        self._mirror_copies = {}
        for actor in self._actors:
            for plotter in self._mirrors:
                with contextlib.suppress(Exception):
                    plotter.remove_actor(actor, render=False)
        super().clear()
        self.render_mirrors()

    def clear_category(self, category: str) -> None:
        """Remove one overlay from the primary and every mirror viewport."""
        self._drop_mirror_copies([(category, self._mirror_copies.pop(category, []))])
        for actor in self._categories.get(category, []):
            for plotter in self._mirrors:
                with contextlib.suppress(Exception):
                    plotter.remove_actor(actor, render=False)
        super().clear_category(category)
        self.render_mirrors()

    def _apply_category_visibility(self, category: str, visible: bool) -> None:
        """Set *category*'s visibility on the primary and every mirror, no render."""
        for actor in self._categories.get(category, []):
            with contextlib.suppress(Exception):
                actor.SetVisibility(bool(visible))
        for _plotter, copy in self._mirror_copies.get(category, []):
            with contextlib.suppress(Exception):
                copy.SetVisibility(bool(visible))

    def _render_all(self) -> None:
        """Render the primary plotter and every mirror, best effort."""
        if self._plotter is not None:
            with contextlib.suppress(Exception):
                self._plotter.render()
        self.render_mirrors()

    def set_category_visible(self, category: str, visible: bool) -> None:
        """Show/hide a category in the primary and every mirror viewport."""
        self._apply_category_visibility(category, visible)
        self._render_all()

    def set_categories_visible(self, visible: dict[str, bool]) -> None:
        """Apply several categories' visibility, then render once.

        The display-sync pass sets every skeleton category in one go — nodes,
        frames, shells, restraints and their load glyphs — so a batch of
        visibility changes costs one render, not one per category.
        """
        for category, value in visible.items():
            self._apply_category_visibility(category, value)
        self._render_all()

    def set_category_opacity(self, category: str, opacity: float) -> None:
        """Set a category's opacity in the primary and every mirror viewport."""
        super().set_category_opacity(category, opacity)
        for _plotter, copy in self._mirror_copies.get(category, []):
            with contextlib.suppress(Exception):
                copy.GetProperty().SetOpacity(float(opacity))
        self.render_mirrors()

    def show(self) -> None:
        """No-op -- the embedding Qt application owns the event loop.

        The standalone PyVista ``show()`` blocks on a modal render window; in
        an embedded viewport the host application's ``QApplication.exec()``
        already drives rendering, so this deliberately does nothing.
        """
        return None
