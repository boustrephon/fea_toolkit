"""A 2×2 grid of viewports (iso / front / top / side) sharing one scene.

The quad view is the desktop GUI's milestone-10 layout: the central widget is a
grid of four :class:`~fea_toolkit.gui.render_backend.MainThreadQtInteractor`s,
each locked to a canonical orientation.  The **primary** viewport (top-left,
isometric) renders the scene through one
:class:`~fea_toolkit.gui.render_backend.QtRenderBackend`; the other three are
*mirror* plotters of that backend, so the scene exists once in memory and every
actor is shared — a visibility / opacity change therefore updates all four
views at once.

Camera semantics:

* Each viewport has its own orientation (``"iso"`` / ``"xz"`` / ``"xy"`` /
  ``"yz"``) that can be cycled with its per-view button, or set by the
  active-view camera menu.
* The four views stay in sync through a shared focal point: re-aiming one view
  re-targets the others at the same world point while each keeps its own
  direction and zoom (see :meth:`sync_focus`).

Qt-free apart from the widgets themselves, so the geometry/state logic stays
testable; the widget is exercised headlessly under the ``needs_gui`` marker.
"""

from __future__ import annotations

import contextlib
from typing import Any, Optional

import numpy as np
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

__all__ = ["ORIENTATIONS", "ORIENTATION_LABELS", "QuadViewport"]

#: Canonical view orientations, in registration (grid) order.
ORIENTATIONS = ("iso", "xz", "xy", "yz")

#: Human-readable caption per orientation.
ORIENTATION_LABELS = {"iso": "Isometric", "xz": "Front", "xy": "Top", "yz": "Side"}

#: The ``Plotter`` method that orients a camera for each orientation.
_ORIENTATION_METHODS = {
    "iso": "view_isometric",
    "xz": "view_xz",
    "xy": "view_xy",
    "yz": "view_yz",
}


def _as_array(point: Any) -> np.ndarray:
    """Coerce a camera point (list or array) to a float array."""
    return np.asarray(point, dtype=float)


class QuadViewport(QWidget):
    """Four viewports in a 2×2 grid, sharing one rendered scene.

    Args:
        parent: Optional Qt parent.
        orientations: The four orientations, in grid order (row-major).  Defaults
            to ``("iso", "xz", "xy", "yz")`` — isometric top-left, front (XZ)
            top-right, top (XY) bottom-left, side (YZ) bottom-right.
    """

    #: Emitted (with the view index) when a viewport becomes the active one.
    view_activated = Signal(int)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        orientations: tuple = ORIENTATIONS,
    ) -> None:
        super().__init__(parent)
        from ..render_backend import MainThreadQtInteractor, QtRenderBackend

        self._orientations: list = list(orientations)
        self._interactors: list = []
        self._captions: list[QLabel] = []
        self._buttons: list[QToolButton] = []
        self._cells: list[QWidget] = []
        self._active_index = 0
        self._single = False

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        for index in range(4):
            cell = QWidget(self)
            lay = QVBoxLayout(cell)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)

            header = QHBoxLayout()
            header.setContentsMargins(2, 0, 2, 0)
            caption = QLabel(ORIENTATION_LABELS[self._orientations[index]])
            cycle = QToolButton()
            cycle.setText("\u21bb")  # clockwise arrow — cycle the orientation
            cycle.setToolTip("Cycle this view's orientation")
            cycle.clicked.connect(lambda _checked=False, i=index: self.cycle_orientation(i))
            header.addWidget(caption)
            header.addStretch(1)
            header.addWidget(cycle)

            interactor = MainThreadQtInteractor(cell)
            lay.addLayout(header)
            lay.addWidget(interactor, 1)

            self._interactors.append(interactor)
            self._captions.append(caption)
            self._buttons.append(cycle)
            self._cells.append(cell)
            grid.addWidget(cell, index // 2, index % 2)

        # One backend renders the scene; the other three plotters mirror it.
        self._backend = QtRenderBackend(self._interactors[0])
        self._backend.set_mirrors(self._interactors[1:])

        self._refresh_active_captions()

    # ── Viewports ────────────────────────────────────────────────────

    @property
    def interactors(self) -> list:
        """The four :class:`MainThreadQtInteractor` widgets, in grid order."""
        return self._interactors

    @property
    def backend(self) -> Any:
        """The primary :class:`QtRenderBackend` (renders and mirrors the scene)."""
        return self._backend

    @property
    def primary_interactor(self) -> Any:
        """The primary (index 0) interactor — the isometric view."""
        return self._interactors[0]

    @property
    def active_index(self) -> int:
        """Index of the viewport that owns picking and the camera menu."""
        return self._active_index

    @property
    def active_interactor(self) -> Any:
        """The viewport that owns picking and the camera menu."""
        return self._interactors[self._active_index]

    def set_active(self, index: int) -> None:
        """Make viewport *index* the active one and announce it."""
        if not 0 <= index < len(self._interactors):
            return
        if index == self._active_index:
            return
        self._active_index = index
        self._refresh_active_captions()
        self.view_activated.emit(index)

    def _refresh_active_captions(self) -> None:
        """Bold the active viewport's caption, normal-weight the rest."""
        for index, caption in enumerate(self._captions):
            font = caption.font()
            font.setBold(index == self._active_index)
            caption.setFont(font)

    # ── Single view ─────────────────────────────────────────────────

    @property
    def single_view(self) -> bool:
        """Whether the grid is collapsed to the active viewport only."""
        return self._single

    def set_single_view(self, single: bool) -> None:
        """Collapse to the active viewport, or restore the 2×2 grid.

        Args:
            single: When true, only the active viewport is shown and it
                expands to fill the container; when false, all four
                viewports are shown.
        """
        self._single = bool(single)
        for index, cell in enumerate(self._cells):
            cell.setVisible(not self._single or index == self._active_index)

    # ── Orientations ─────────────────────────────────────────────────

    def orientation(self, index: int) -> str:
        """The orientation name of viewport *index*."""
        return self._orientations[index]

    def set_orientation(self, index: int, name: str) -> None:
        """Orient viewport *index* to *name* and update its caption."""
        if name not in ORIENTATION_LABELS:
            raise ValueError(f"unknown orientation {name!r}")
        self._orientations[index] = name
        self._captions[index].setText(ORIENTATION_LABELS[name])
        self._apply_orientation_to(index)

    def cycle_orientation(self, index: int) -> None:
        """Advance viewport *index* to the next orientation."""
        current = self._orientations[index]
        nxt = ORIENTATIONS[(ORIENTATIONS.index(current) + 1) % len(ORIENTATIONS)]
        self.set_orientation(index, nxt)

    def _apply_orientation_to(self, index: int) -> None:
        """Point viewport *index* at its stored orientation (and fit its bounds)."""
        interactor = self._interactors[index]
        method = getattr(interactor, _ORIENTATION_METHODS[self._orientations[index]], None)
        if method is None:
            return
        with contextlib.suppress(Exception):
            method()

    def apply_orientations(self, skip: Optional[int] = None) -> None:
        """Point every viewport at its stored orientation, except *skip*.

        Args:
            skip: A viewport index to leave untouched (e.g. the primary view
                whose saved camera is being restored).
        """
        for index in range(len(self._interactors)):
            if index == skip:
                continue
            self._apply_orientation_to(index)

    def reset_orientations(self) -> None:
        """Restore the four viewports to their default orientations and re-fit.

        The per-view ↻ button cycles an orientation *away* from its default, so
        ``View ▸ Reset view`` must not just re-fit the current (cycled) set —
        it has to put iso / front / top / side back first.
        """
        for index, name in enumerate(ORIENTATIONS):
            self._orientations[index] = name
            self._captions[index].setText(ORIENTATION_LABELS[name])
        self.apply_orientations()

    # ── Camera sync ──────────────────────────────────────────────────

    def sync_focus(self, source_index: int) -> None:
        """Re-aim every other view at viewport *source_index*'s focal point.

        Each non-source view keeps its own view direction and zoom — only the
        point it looks at moves — so orbiting or zooming one view keeps the four
        projections centred on the same world point.
        """
        if not 0 <= source_index < len(self._interactors):
            return
        try:
            source_cam = self._interactors[source_index].camera_position
        except Exception:
            return
        if not source_cam:
            return
        _, source_focal, _ = source_cam
        source_focal = _as_array(source_focal)
        for index, interactor in enumerate(self._interactors):
            if index == source_index:
                continue
            with contextlib.suppress(Exception):
                pos, old_focal, up = interactor.camera_position
                direction = _as_array(old_focal) - _as_array(pos)
                interactor.camera_position = (source_focal - direction, source_focal, up)

    def install_camera_sync(self) -> None:
        """Wire live camera sync: after any view finishes an interaction, the
        others re-aim at its focal point (best effort — inert off-screen)."""
        for index, interactor in enumerate(self._interactors):
            iren = getattr(interactor, "iren", None)
            if iren is None:
                continue
            vtk_iren = getattr(iren, "interactor", None) or iren
            with contextlib.suppress(Exception):
                vtk_iren.AddObserver(
                    "EndInteractionEvent", lambda *_args, i=index: self.sync_focus(i)
                )
