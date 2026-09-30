"""How mouse gestures map to *selecting* versus *orbiting* the viewport.

The kinds of mouse behaviour people expect differ by the program they came
from, so this is **configuration, not code**: a small set of policy knobs, a
``presets`` table naming the common combinations, and a settings file that
overrides either.  Qt- and VTK-free, so it is unit-tested on its own
(``tests/test_gui_interaction.py``).

Presets:

* ``click_drag`` (default) -- a clean left click selects; left-drag orbits, as
  it always did.  Nothing is modal.  Hold the *add* modifier (Shift) to add a
  pick, the *toggle* modifier (Ctrl/Cmd) to toggle one, and drag with the
  *marquee* modifier (Alt/Opt) to rubber-band select.
* ``right_click`` -- the right button selects, the left button stays pure
  camera control (the pre-2026-09 behaviour).
* ``select_orbit`` -- the explicit Select / Orbit arrangement (the SAP2000
  arrangement): in Select mode a drag is a rubber-band selection and orbiting
  is disabled.  This preset sets ``select_mode``; the GUI additionally switches
  the VTK interactor style (tracked in ``docs/_pending_work.md`` P23).

Settings file: ``$FEA_TOOLKIT_GUI_CONFIG``, else
``~/.config/fea_toolkit/gui.json``::

    {"preset": "click_drag", "drag_threshold_px": 4, "pick_tolerance": 0.002}

Anything not understood is reported back for the message log rather than
silently dropped.
"""

import json
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Optional, Union

__all__ = [
    "CONFIG_ENV_VAR",
    "DEFAULT_PRESET",
    "PRESETS",
    "ClickGesture",
    "InteractionPolicy",
    "default_config_path",
    "load_policy",
    "resolve_policy",
]

#: Environment variable that redirects the settings file.
CONFIG_ENV_VAR = "FEA_TOOLKIT_GUI_CONFIG"

#: Preset used when the settings file says nothing.
DEFAULT_PRESET = "click_drag"

#: Mouse buttons a policy may nominate as the selecting button.
_BUTTONS = ("left", "right")

#: Modifier keys a policy may bind a selection gesture to.  Qt- and VTK-free
#: canonical names; the Qt adapter maps ``Qt.KeyboardModifier`` onto these.
_MODIFIERS = ("shift", "control", "alt", "meta")


#: JSON keys that name a policy field (the preset name is handled separately).
@dataclass(frozen=True)
class InteractionPolicy:
    """The resolved mouse-interaction policy.

    Attributes:
        preset: Name of the preset this came from (``"custom"`` for raw knobs).
        pick_button: Button that selects -- ``"left"`` or ``"right"``.
        drag_threshold_px: Press-to-release movement below which a gesture is a
            *click* (and therefore selects).  A larger value is more forgiving;
            ``0`` means "no movement at all".
        pick_tolerance: ``vtkCellPicker`` tolerance as a fraction of the
            viewport diagonal -- the size of the invisible picking region around
            an element.  VTK's default (0.025) is fat enough to shadow the
            joints; calibrated on macOS/Retina (1026x860 device px), 0.010 is
            about 13 device px, which a person can actually hit, while 0.003
            (4 px) turned out to be too tight to click at all.
        node_priority: Try the node cloud first, so a click at a joint selects
            the node instead of the member passing through it.
        node_snap_tolerance: Tolerance for that node-first pick.
        add_modifier: Modifier key held to *add* a pick to the current selection
            (``"shift"`` by default).
        toggle_modifier: Modifier key held to *toggle* a pick (``"control"``).
        marquee_modifier: Modifier key that turns a drag into a rubber-band
            selection (``"alt"``).  Only consulted when ``select_mode`` is off.
        select_mode: When ``True`` (the explicit Select / Orbit arrangement), a
            plain drag is a rubber-band selection and orbiting is disabled; when
            ``False`` (modeless modifiers), a drag orbits unless the marquee
            modifier is held.
    """

    preset: str = DEFAULT_PRESET
    pick_button: str = "left"
    drag_threshold_px: float = 5.0
    pick_tolerance: float = 0.010
    node_priority: bool = True
    node_snap_tolerance: float = 0.012
    add_modifier: str = "shift"
    toggle_modifier: str = "control"
    marquee_modifier: str = "alt"
    select_mode: bool = False

    def __post_init__(self) -> None:
        if self.pick_button not in _BUTTONS:
            raise ValueError(f"pick_button must be one of {_BUTTONS}, got {self.pick_button!r}")
        if self.drag_threshold_px < 0:
            raise ValueError(f"drag_threshold_px must not be negative: {self.drag_threshold_px}")
        if not 0.0 <= self.pick_tolerance <= 1.0:
            raise ValueError(f"pick_tolerance must be within 0..1: {self.pick_tolerance}")
        if self.node_snap_tolerance < 0.0:
            raise ValueError(
                f"node_snap_tolerance must not be negative: {self.node_snap_tolerance}"
            )
        for name in ("add_modifier", "toggle_modifier", "marquee_modifier"):
            modifier = getattr(self, name)
            if modifier not in _MODIFIERS:
                raise ValueError(f"{name} must be one of {_MODIFIERS}, got {modifier!r}")


#: The named combinations, so the four "approaches" are one file edit apart.
PRESETS = {
    # A clean click selects, a drag orbits: nothing modal, no button juggling.
    "click_drag": InteractionPolicy(preset="click_drag"),
    # The right button selects; the left button stays pure camera control.
    "right_click": InteractionPolicy(
        preset="right_click",
        pick_button="right",
        drag_threshold_px=0.0,
        node_snap_tolerance=0.012,
    ),
    # The explicit Select / Orbit arrangement: a drag marquee-selects; orbiting
    # is disabled while in Select mode (the GUI switches the interactor style).
    "select_orbit": InteractionPolicy(preset="select_orbit", select_mode=True),
}

#: Policy fields a settings file may set (the preset name is handled apart).
_FIELDS = tuple(f.name for f in fields(InteractionPolicy) if f.name != "preset")


@dataclass
class ClickGesture:
    """Press -> move -> release bookkeeping for one policy.

    Qt- and VTK-free: the interactor adapter feeds it event positions and it
    decides whether the gesture was a *click* (select) or a *drag* (leave the
    camera alone).  That is the whole of "no modes, but a drag must never
    select".

    It also records the modifier keys held at the press, so a click can be an
    *add* or a *toggle* rather than a replace, and answers whether a drag should
    be a rubber-band marquee instead of an orbit (:attr:`marquee_wanted`).

    Attributes:
        policy: The policy being honoured.
        press_xy: Where the tracked press happened, or ``None`` between
            gestures.
        dragged: Set once the pointer has strayed beyond the threshold.
        modifiers: Canonical modifier names held at the press (e.g.
            ``("shift",)``).  Kept until the next press so the caller can read
            them straight after :meth:`release`.
    """

    policy: InteractionPolicy
    press_xy: Optional[tuple] = None
    dragged: bool = False
    modifiers: tuple = ()

    def handles(self, button: str) -> bool:
        """Whether a press of *button* begins a selectable gesture."""
        return button == self.policy.pick_button

    def press(self, xy: tuple, modifiers: tuple = ()) -> None:
        """Start tracking a gesture at *xy* (display coordinates).

        Args:
            xy: Press position.
            modifiers: Canonical modifier names held at the press.
        """
        self.press_xy = (float(xy[0]), float(xy[1]))
        self.dragged = False
        self.modifiers = tuple(modifiers)

    def move(self, xy: tuple) -> None:
        """Note pointer movement; beyond the threshold the gesture is a drag."""
        if self.press_xy is None:
            return
        if self._distance(self.press_xy, xy) > self.policy.drag_threshold_px:
            self.dragged = True

    def release(self, xy: tuple) -> bool:
        """Finish the gesture.

        Args:
            xy: Display coordinates of the release.

        Returns:
            ``True`` when it was a clean click and should select.
        """
        press_xy, self.press_xy = self.press_xy, None
        if press_xy is None or self.dragged:
            return False
        # Re-check on release: a quick drag can arrive without move events.
        return self._distance(press_xy, xy) <= self.policy.drag_threshold_px

    def reset(self) -> None:
        """Forget any in-flight gesture (after a mode or model change)."""
        self.press_xy = None
        self.dragged = False
        self.modifiers = ()

    @property
    def marquee_wanted(self) -> bool:
        """Whether a drag should be a rubber-band selection, not an orbit.

        In explicit Select mode any drag is a marquee; otherwise the drag must
        carry the policy's marquee modifier.
        """
        if self.policy.select_mode:
            return True
        return self.policy.marquee_modifier in self.modifiers

    @staticmethod
    def _distance(first: tuple, second: tuple) -> float:
        """Euclidean distance between two display-coordinate pairs."""
        dx = float(second[0]) - first[0]
        dy = float(second[1]) - first[1]
        return float((dx * dx + dy * dy) ** 0.5)


def resolve_policy(config: Optional[dict] = None) -> "tuple[InteractionPolicy, list[str]]":
    """Resolve a settings-file mapping into a policy.

    Args:
        config: Mapping loaded from the settings file, e.g.
            ``{"preset": "right_click", "pick_tolerance": 0.002}``.

    Returns:
        ``(policy, notes)``.  *notes* explains anything ignored or rejected, for
        the message log; it is empty when the settings were fully understood.
    """
    settings = dict(config or {})
    notes: list[str] = []

    preset_name = settings.pop("preset", DEFAULT_PRESET)
    base = PRESETS.get(preset_name)
    if base is None:
        notes.append(f"unknown interaction preset {preset_name!r}; using {DEFAULT_PRESET!r}")
        base = PRESETS[DEFAULT_PRESET]

    for key in sorted(set(settings) - set(_FIELDS)):
        notes.append(f"ignoring unknown interaction setting {key!r}")

    overrides = {key: settings[key] for key in _FIELDS if key in settings}
    if not overrides:
        return base, notes
    try:
        return replace(base, **overrides), notes
    except (TypeError, ValueError) as exc:
        notes.append(f"interaction setting rejected ({exc}); using preset {base.preset!r}")
        return base, notes


def default_config_path() -> Path:
    """Where the user's GUI settings file lives.

    Returns:
        ``$FEA_TOOLKIT_GUI_CONFIG`` when set, else
        ``~/.config/fea_toolkit/gui.json``.
    """
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "fea_toolkit" / "gui.json"


def load_policy(
    path: Optional[Union[str, Path]] = None,
) -> "tuple[InteractionPolicy, list[str]]":
    """Load the interaction policy from a settings file.

    A missing file simply yields the defaults.  A file that cannot be read,
    parsed or contains unrecognised settings yields the defaults *with* a note,
    so a typo shows up in the message log instead of silently doing nothing.

    Args:
        path: Settings file to read; defaults to :func:`default_config_path`.

    Returns:
        ``(policy, notes)``, as :func:`resolve_policy`.
    """
    target = Path(path).expanduser() if path is not None else default_config_path()
    if not target.is_file():
        return resolve_policy(None)
    try:
        data: Any = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        policy, notes = resolve_policy(None)
        return policy, [f"could not read {target}: {exc}", *notes]
    if not isinstance(data, dict):
        policy, notes = resolve_policy(None)
        return policy, [f"{target} must contain a JSON object", *notes]
    policy, notes = resolve_policy(data)
    return policy, [f"{target}: {note}" for note in notes]
