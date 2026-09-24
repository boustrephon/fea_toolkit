"""Turn viewport mouse gestures into picks, honouring an ``InteractionPolicy``.

PyVista's ``enable_mesh_picking`` cannot express "a click selects, a drag
orbits": it picks on the raw *press*, and with the picker's default tolerance
(0.025 of the viewport diagonal) the picking region is fat enough to shadow every
joint.  This module owns the picking instead: the policy comes from
:mod:`fea_toolkit.gui.controllers.interaction`, the gesture from
:class:`~fea_toolkit.gui.controllers.interaction.ClickGesture`, and the pickers
are our own ``vtkCellPicker`` / ``vtkPointPicker``.

**Events come from Qt, not from VTK observers.**  Measured on macOS with
pyvistaqt 0.13.1 (pyvista 0.48.1): a click reaches the widget's
``mousePressEvent`` and the interactor's ``LeftButtonPressEvent``, but
``mouseReleaseEvent`` never delivers ``LeftButtonReleaseEvent`` to the
interactor -- so a release-driven gesture installed as a VTK observer simply
never completes.  The Qt side is reliable, so
:mod:`fea_toolkit.gui.views.qt_mouse` feeds this object instead.

**Coordinates.**  Qt reports *logical* pixels; VTK pickers take *device* pixels
with the origin at the **bottom** left (what ``GetEventPosition`` returns and
what ``vtkCellPicker.Pick`` expects).  The gesture works in logical pixels --
that is what a user-facing ``drag_threshold_px`` should mean on any display --
while ``pick_at`` is handed device pixels.  Keep the two apart: mixing them was
what made an early calibration miss by a factor of the device pixel ratio.

The module is deliberately **Qt-free** (the GUI adapter supplies the events), so
it is unit-tested without Qt -- including in the CI matrix that has no Qt.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from ..controllers.interaction import ClickGesture, InteractionPolicy

__all__ = ["PickResult", "ViewportInteraction"]


@dataclass
class PickResult:
    """What a click found in the viewport.

    Attributes:
        actor: The picked actor, or ``None`` when nothing was hit.
        index: Index of the entity **within its category** -- a point index when
            the node-first pick won, a cell index otherwise.
        position: World coordinates of the pick.
        node: ``True`` when the node cloud was picked rather than an element.
    """

    actor: Any = None
    index: int = -1
    position: Optional[np.ndarray] = None
    node: bool = False

    @property
    def hit(self) -> bool:
        """Whether anything was picked at all."""
        return self.actor is not None


class ViewportInteraction:
    """Own one viewport's picking: policy, gesture tracking and VTK pickers.

    Args:
        plotter: The ``pyvista.Plotter`` (or ``pyvistaqt.QtInteractor``) to
            observe.
        policy: The resolved interaction policy.
        on_pick: Called with a :class:`PickResult` for every clean click --
            including a miss, so the caller can clear the selection.
        node_actors: Callable returning the current node-cloud actors.  A
            callable, because a new model means new actors and a stale pick list
            would pin the old ones.
    """

    def __init__(
        self,
        plotter: Any,
        policy: InteractionPolicy,
        on_pick: Callable[[PickResult], None],
        node_actors: Optional[Callable[[], list]] = None,
    ) -> None:
        self._plotter = plotter
        self._policy = policy
        self._on_pick = on_pick
        self._node_actors = node_actors
        self._gesture = ClickGesture(policy)
        self._cell_picker: Any = None
        self._point_picker: Any = None

    # ── Policy ───────────────────────────────────────────────────────

    @property
    def policy(self) -> InteractionPolicy:
        """The policy in force."""
        return self._policy

    def set_policy(self, policy: InteractionPolicy) -> None:
        """Adopt *policy*: retune the pickers and restart gesture tracking.

        Args:
            policy: The new policy (from the settings file, or a user toggle).
        """
        self._policy = policy
        self._gesture = ClickGesture(policy)
        for picker, tolerance in (
            (self._cell_picker, policy.pick_tolerance),
            (self._point_picker, policy.node_snap_tolerance),
        ):
            if picker is not None:
                picker.SetTolerance(tolerance)

    # ── Gesture (fed by the Qt adapter) ──────────────────────────────

    @property
    def pick_button(self) -> str:
        """The button this policy selects with (``"left"`` or ``"right"``)."""
        return self._policy.pick_button

    def begin_gesture(self, logical_xy: tuple) -> None:
        """Start tracking a gesture.

        Args:
            logical_xy: Press position as Qt reports it (logical pixels).
        """
        self._gesture.press(logical_xy)

    def update_gesture(self, logical_xy: tuple) -> None:
        """Note pointer movement, so a drag is never mistaken for a click.

        Args:
            logical_xy: Current position as Qt reports it.
        """
        self._gesture.move(logical_xy)

    def end_gesture(self, logical_xy: tuple, device_xy: tuple) -> None:
        """Finish a gesture; pick when it was a clean click.

        Args:
            logical_xy: Release position as Qt reports it -- the space the drag
                threshold is measured in.
            device_xy: The same point in VTK *device* pixels, bottom-left
                origin, which is what the pickers expect.
        """
        if self._gesture.release(logical_xy):
            self._on_pick(self.pick_at(*device_xy))

    def cancel_gesture(self) -> None:
        """Forget any in-flight gesture (after a model or policy change)."""
        self._gesture.reset()

    # ── Picking ──────────────────────────────────────────────────────

    def pick_at(self, x: float, y: float) -> PickResult:
        """Pick at display coordinates *x*, *y*.

        Nodes win when the policy says so and one is within
        ``node_snap_tolerance``; otherwise the element batches are searched with
        ``pick_tolerance``.

        Args:
            x: Display x coordinate (pixels).
            y: Display y coordinate (pixels).

        Returns:
            The :class:`PickResult` -- empty (``hit is False``) when nothing was
            under the cursor.
        """
        renderer = getattr(self._plotter, "renderer", None)
        if renderer is None:
            return PickResult()
        if self._policy.node_priority:
            node = self._pick_node(x, y, renderer)
            if node is not None:
                return node
        return self._pick_cell(x, y, renderer)

    def _pick_cell(self, x: float, y: float, renderer: Any) -> PickResult:
        """Pick an element cell with the policy's tolerance."""
        picker = self._cell_picker or self._make_cell_picker()
        if picker.Pick(int(round(x)), int(round(y)), 0, renderer) != 1:
            return PickResult()
        return PickResult(
            actor=picker.GetActor(),
            index=int(picker.GetCellId()),
            position=np.array(picker.GetPickPosition(), dtype=float),
        )

    def _pick_node(self, x: float, y: float, renderer: Any) -> Optional[PickResult]:
        """Pick a node from the node cloud, or ``None`` when none is near.

        The pick list keeps the node-first search from latching onto a member
        that merely passes through the same place.
        """
        actors = self._current_node_actors()
        if not actors:
            return None
        picker = self._point_picker or self._make_point_picker()
        picker.InitializePickList()
        for actor in actors:
            picker.AddPickList(actor)
        picker.PickFromListOn()
        if picker.Pick(int(round(x)), int(round(y)), 0, renderer) != 1:
            return None
        point_id = int(picker.GetPointId())
        if point_id < 0:
            return None
        return PickResult(
            actor=picker.GetActor(),
            index=point_id,
            position=np.array(picker.GetPickPosition(), dtype=float),
            node=True,
        )

    def _current_node_actors(self) -> list:
        """The node-cloud actors known right now."""
        if self._node_actors is None:
            return []
        found = self._node_actors()
        return list(found or ())

    def _make_cell_picker(self) -> Any:
        """Create the element picker, toleranced from the policy."""
        import vtk

        self._cell_picker = vtk.vtkCellPicker()
        self._cell_picker.SetTolerance(self._policy.pick_tolerance)
        return self._cell_picker

    def _make_point_picker(self) -> Any:
        """Create the node picker, toleranced from the policy."""
        import vtk

        self._point_picker = vtk.vtkPointPicker()
        self._point_picker.SetTolerance(self._policy.node_snap_tolerance)
        return self._point_picker
