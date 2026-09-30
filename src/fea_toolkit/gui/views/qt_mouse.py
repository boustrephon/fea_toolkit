"""Feed Qt mouse events into a :class:`ViewportInteraction`.

The Qt side of the viewport is the reliable one.  Measured on macOS with
pyvistaqt 0.13.1 / pyvista 0.48.1: a click reaches the widget's
``mousePressEvent`` and the interactor's ``LeftButtonPressEvent``, but
``mouseReleaseEvent`` never delivers ``LeftButtonReleaseEvent`` to the
interactor -- so a release-driven gesture installed as a VTK observer never
completes (``docs/dev_notes.md``).  An event filter on the widget sees every
press, move and release, so it drives the gesture instead.

Qt positions are **logical** pixels while VTK's pickers take **device** pixels
with a bottom-left origin, so this module converts -- ``x * devicePixelRatio``
and ``height - y * devicePixelRatio``, the same conversion the Qt widget itself
performs before invoking ``LeftButtonPressEvent``.
"""

from typing import Any, Optional

from qtpy.QtCore import QEvent, QObject, Qt

__all__ = ["QtMouseFilter", "install_mouse_filter"]

#: Qt mouse-button value per policy button name.
_QT_BUTTONS = {
    "left": Qt.MouseButton.LeftButton,
    "right": Qt.MouseButton.RightButton,
}

#: Qt keyboard modifier -> the policy's canonical modifier name, so the
#: Qt-free interaction layer never sees a Qt enum.
_MODIFIER_MAP = (
    (Qt.KeyboardModifier.ShiftModifier, "shift"),
    (Qt.KeyboardModifier.ControlModifier, "control"),
    (Qt.KeyboardModifier.AltModifier, "alt"),
    (Qt.KeyboardModifier.MetaModifier, "meta"),
)


class QtMouseFilter(QObject):
    """Drive *interaction* from a viewport widget's mouse events.

    Args:
        widget: The viewport widget to watch (a ``pyvistaqt.QtInteractor``).
        interaction: The picking adapter to drive.
        parent: Optional Qt parent.
    """

    def __init__(self, widget: Any, interaction: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._widget = widget
        self._interaction = interaction
        self._owned = False

    # ── Helpers ──────────────────────────────────────────────────────

    def _button(self) -> Any:
        """The Qt button the current policy selects with."""
        return _QT_BUTTONS.get(self._interaction.pick_button, Qt.MouseButton.LeftButton)

    @staticmethod
    def _logical(event: Any) -> tuple:
        """Position of a Qt mouse event, in logical pixels."""
        position = event.position()
        return (float(position.x()), float(position.y()))

    @staticmethod
    def _modifier_names(modifiers: Any) -> tuple:
        """Map a Qt modifier bitfield onto the policy's canonical names."""
        names = []
        for flag, name in _MODIFIER_MAP:
            if modifiers & flag:
                names.append(name)
        return tuple(names)

    def _device_height(self) -> float:
        """Render-window height in device pixels (``0.0`` before it is sized)."""
        render_window = getattr(self._widget, "render_window", None)
        try:
            return float(render_window.GetSize()[1])
        except Exception:
            return 0.0

    def to_device(self, logical_xy: tuple) -> tuple:
        """Convert logical pixels to VTK device pixels (bottom-left origin).

        Args:
            logical_xy: ``(x, y)`` as Qt reports it, top-left origin.

        Returns:
            ``(x, y)`` in device pixels, bottom-left origin -- what the VTK
            pickers expect.
        """
        ratio = float(self._widget.devicePixelRatioF())
        return (
            logical_xy[0] * ratio,
            self._device_height() - logical_xy[1] * ratio,
        )

    # ── QObject ──────────────────────────────────────────────────────

    def eventFilter(self, obj: Any, event: Any) -> bool:
        """Track press/move/release; consume the gesture only when it is a marquee.

        Returning ``False`` lets VTK keep orbiting/zooming; returning ``True``
        for a marquee-owned gesture stops VTK from orbiting while the rubber-band
        drag runs.  A gesture is *owned* when the policy says a drag is a marquee
        (``select_mode`` is on, or the marquee modifier is held).
        """
        if obj is not self._widget:
            return False
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            button = getattr(event, "button", lambda: None)()
            if button != self._button():
                return False
            logical = self._logical(event)
            self._owned = self._interaction.begin_gesture(
                logical, self._modifier_names(event.modifiers()), self.to_device(logical)
            )
            return self._owned
        if event_type == QEvent.Type.MouseMove:
            self._interaction.update_gesture(self._logical(event))
            return self._owned
        if event_type == QEvent.Type.MouseButtonRelease:
            button = getattr(event, "button", lambda: None)()
            if button != self._button():
                return False
            logical = self._logical(event)
            self._interaction.end_gesture(logical, self.to_device(logical))
            owned = self._owned
            self._owned = False
            return owned
        return False


def install_mouse_filter(
    widget: Any,
    interaction: Any,
    parent: Optional[QObject] = None,
) -> QtMouseFilter:
    """Install a :class:`QtMouseFilter` on *widget*.

    Args:
        widget: The viewport widget.
        interaction: The picking adapter to drive.
        parent: Optional Qt parent.

    Returns:
        The installed filter -- the caller must keep it alive (``parent`` does
        that when one is given).
    """
    mouse_filter = QtMouseFilter(widget, interaction, parent)
    widget.installEventFilter(mouse_filter)
    return mouse_filter
