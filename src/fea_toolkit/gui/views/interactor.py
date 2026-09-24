"""Turn viewport mouse events into picks, honouring an ``InteractionPolicy``.

PyVista's ``enable_mesh_picking`` cannot express "a click selects, a drag
orbits": it picks on the raw *press*, and with the picker's default tolerance
(0.025 of the viewport diagonal) the picking region is fat enough to shadow
every joint.  This adapter installs its own VTK observers instead, so the policy
from :mod:`fea_toolkit.gui.controllers.interaction` is what decides:

* a **press** of the policy's button starts a ``ClickGesture``;
* **movement** beyond ``drag_threshold_px`` marks the gesture a drag, leaving it
  to the camera;
* a **release** of a clean click runs the pick -- the node cloud first when
  ``node_priority`` is on (restricted with ``AddPickList``), then the element
  batches with ``pick_tolerance``.

The event logic is deliberately thin: the decisions live in the Qt-free policy
and gesture classes, which are unit-tested without a render window.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from ..controllers.interaction import ClickGesture, InteractionPolicy

__all__ = ["PickResult", "ViewportInteraction"]

#: VTK event name per mouse button.
_PRESS_EVENT = {"left": "LeftButtonPressEvent", "right": "RightButtonPressEvent"}
_RELEASE_EVENT = {"left": "LeftButtonReleaseEvent", "right": "RightButtonReleaseEvent"}


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
        self._observers: list = []

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

    # ── Setup ────────────────────────────────────────────────────────

    def install(self) -> bool:
        """Attach the observers for the policy's button.

        Returns:
            ``False`` when the viewport has no interactor -- a headless host,
            or the ``offscreen`` Qt platform, which cannot create a GL context.
        """
        iren = getattr(self._plotter, "iren", None)
        if iren is None:
            return False
        button = self._policy.pick_button
        self._observers = [
            iren.add_observer(_PRESS_EVENT[button], self._on_press),
            iren.add_observer("MouseMoveEvent", self._on_move),
            iren.add_observer(_RELEASE_EVENT[button], self._on_release),
        ]
        return True

    def uninstall(self) -> None:
        """Detach the observers (idempotent)."""
        iren = getattr(self._plotter, "iren", None)
        if iren is not None:
            for observer in self._observers:
                iren.remove_observer(observer)
        self._observers = []

    # ── Events ───────────────────────────────────────────────────────

    def _on_press(self, obj: Any, _event: Any) -> None:
        """Begin tracking a gesture at the press position."""
        self._gesture.press(self._event_position(obj))

    def _on_move(self, obj: Any, _event: Any) -> None:
        """Note movement, so a drag is never mistaken for a click."""
        self._gesture.move(self._event_position(obj))

    def _on_release(self, obj: Any, _event: Any) -> None:
        """Pick when the gesture was a clean click."""
        position = self._event_position(obj)
        if self._gesture.release(position):
            self._on_pick(self.pick_at(*position))

    @staticmethod
    def _event_position(obj: Any) -> tuple:
        """Display coordinates carried by a VTK event (``(0, 0)`` if absent)."""
        try:
            x, y = obj.GetEventPosition()
            return (float(x), float(y))
        except Exception:
            return (0.0, 0.0)

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
