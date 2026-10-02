"""Display settings for the desktop GUI, persisted beside the interaction policy.

The interaction policy (:mod:`fea_toolkit.gui.controllers.interaction`) already
owns ``~/.config/fea_toolkit/gui.json``.  This module extends that **same file**
with a ``display`` section for the viewport's quality knobs -- shell opacity,
element shrink, and the deformed-shape / force-diagram scales -- so a user's
preferred look survives a restart.

The file stays flat: the interaction keys live at the top level (exactly as
``docs/gui.md`` documents them) and the display knobs live under one ``display``
object::

    {"preset": "click_drag", "display": {"shrink": 0.9, "shell_opacity": 0.7}}

A pre-existing settings file keeps working because :func:`load_policy` is never
asked to parse the ``display`` key -- :func:`load_gui_settings` strips it before
delegating.  Anything not understood is reported back for the message log rather
than silently dropped, the same contract ``resolve_policy`` keeps.
"""

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from ._atomic import atomic_write_text
from .interaction import (
    default_config_path,
    resolve_policy,
)

if TYPE_CHECKING:
    from .interaction import InteractionPolicy

__all__ = [
    "DISPLAY_DEFAULTS",
    "DISPLAY_FIELDS",
    "DisplaySettings",
    "load_gui_settings",
    "resolve_display",
    "save_display_settings",
]

#: JSON keys a ``display`` section may carry, mirroring the toolbar knobs.
_DISPLAY_NUMERIC_FIELDS = ("shell_opacity", "shrink", "deformed_scale", "force_scale")

#: Inclusive range each numeric knob may take, mirroring the toolbar spin boxes
#: in ``main_window`` (kept here, Qt-free, so :func:`resolve_display` can reject
#: out-of-range values without importing the widget).
_DISPLAY_RANGES = {
    "shell_opacity": (0.05, 1.0),
    "shrink": (0.5, 1.0),
    "deformed_scale": (0.1, 1000.0),
    "force_scale": (0.1, 1000.0),
}

#: Every field a ``display`` section may name (the numeric knobs plus the
#: force-diagram quantity label).
DISPLAY_FIELDS = frozenset((*_DISPLAY_NUMERIC_FIELDS, "force_quantity"))


@dataclass(frozen=True)
class DisplaySettings:
    """The resolved viewport display knobs.

    Defaults match the GUI's out-of-the-box toolbar values (``main_window``).

    Attributes:
        shell_opacity: Opacity of area elements, 0.05–1.0.
        shrink: Fraction of full size elements are drawn at, 0.5–1.0.
        deformed_scale: Deformed-shape size as % of the model diagonal, 0.1–1000.0.
        force_scale: Force-diagram size as % of the model diagonal, 0.1–1000.0.
        force_quantity: SAP local-DOF label for the drawn end-force component
            (a key of :data:`fea_toolkit.model.sap_data.FORCE_QUANTITY_LABELS`).
    """

    shell_opacity: float = 0.7
    shrink: float = 1.0
    deformed_scale: float = 10.0
    force_scale: float = 10.0
    force_quantity: str = "M3"


#: The defaults, as a reusable constant (callers may ``replace`` over it).
DISPLAY_DEFAULTS = DisplaySettings()


def resolve_display(config: Any = None) -> "tuple[DisplaySettings, list[str]]":
    """Resolve a ``display`` mapping into a :class:`DisplaySettings`.

    Args:
        config: The ``display`` section of the settings file, e.g.
            ``{"shrink": 0.9, "shell_opacity": 0.7}``.

    Returns:
        ``(settings, notes)``.  *notes* explains anything ignored or rejected,
        for the message log; it is empty when the section was fully understood.
    """
    if config is not None and not isinstance(config, dict):
        return DISPLAY_DEFAULTS, [
            f"display section must be a JSON object, not {type(config).__name__}"
        ]
    settings = dict(config or {})
    notes: list[str] = []

    for key in sorted(set(settings) - DISPLAY_FIELDS):
        notes.append(f"ignoring unknown display setting {key!r}")

    overrides: dict[str, Any] = {}
    for key in _DISPLAY_NUMERIC_FIELDS:
        if key in settings:
            try:
                value = float(settings[key])
                if not math.isfinite(value):
                    raise ValueError(f"{value!r} is not finite")
                low, high = _DISPLAY_RANGES[key]
                if not low <= value <= high:
                    raise ValueError(f"{value!r} is outside {low}\u2013{high}")
                overrides[key] = value
            except (TypeError, ValueError) as exc:
                notes.append(f"display setting {key!r} rejected ({exc}); using default")

    if "force_quantity" in settings:
        label = str(settings["force_quantity"])
        from ...model.sap_data import FORCE_QUANTITY_LABELS

        if label not in FORCE_QUANTITY_LABELS:
            notes.append(
                f"display setting 'force_quantity' {label!r} is not a known force "
                f"component; using {DISPLAY_DEFAULTS.force_quantity!r}"
            )
        else:
            overrides["force_quantity"] = label

    if not overrides:
        return DISPLAY_DEFAULTS, notes
    try:
        return replace(DISPLAY_DEFAULTS, **overrides), notes
    except (TypeError, ValueError) as exc:
        notes.append(f"display settings rejected ({exc}); using defaults")
        return DISPLAY_DEFAULTS, notes


def load_gui_settings(
    path: Optional[Union[str, Path]] = None,
) -> "tuple[InteractionPolicy, DisplaySettings, list[str]]":
    """Load both the interaction policy and the display settings from one file.

    A missing file yields the defaults for both.  A file that cannot be read,
    parsed or contains unrecognised keys yields the defaults *with* a note, so a
    typo shows up in the message log instead of silently doing nothing.

    Args:
        path: Settings file to read; defaults to :func:`default_config_path`.

    Returns:
        ``(policy, display, notes)``.
    """
    target = Path(path).expanduser() if path is not None else default_config_path()
    if not target.is_file():
        policy, _notes = resolve_policy(None)
        return policy, DISPLAY_DEFAULTS, []

    try:
        data: Any = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        policy, notes = resolve_policy(None)
        return policy, DISPLAY_DEFAULTS, [f"could not read {target}: {exc}", *notes]

    if not isinstance(data, dict):
        policy, notes = resolve_policy(None)
        return policy, DISPLAY_DEFAULTS, [f"{target} must contain a JSON object", *notes]

    # The ``display`` section is stripped before the interaction resolver sees
    # the file, so a display key never surfaces as "ignoring unknown interaction
    # setting 'display'" — the two readers each own their own keys.
    interaction_data = {key: value for key, value in data.items() if key != "display"}
    policy, policy_notes = resolve_policy(interaction_data)
    policy_notes = [f"{target}: {note}" for note in policy_notes]
    display, display_notes = resolve_display(data.get("display"))
    display_notes = [f"{target}: {note}" for note in display_notes]

    return policy, display, [*policy_notes, *display_notes]


def save_display_settings(
    path: Union[str, Path],
    values: dict,
) -> list[str]:
    """Persist *values* into the file's ``display`` section, preserving the rest.

    Reads the existing file (if any), replaces only the ``display`` object with
    *values*, and writes it back -- so interaction keys and any keys this module
    does not own survive unchanged.  A missing file is created.

    Args:
        path: Settings file to write; typically :func:`default_config_path`.
        values: The display knobs to store, e.g.
            ``{"shrink": 0.9, "shell_opacity": 0.7}``.

    Returns:
        Notes to log, or ``[]`` when the write succeeded silently.
    """
    target = Path(path).expanduser()
    data: dict = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # A malformed file is left untouched rather than overwritten, and the
            # note tells the user their display settings were not saved.
            return [f"could not read {target}: {exc}; display settings not saved"]
        if not isinstance(loaded, dict):
            # A valid-but-non-object file is likewise left untouched: silently
            # clobbering it would destroy whatever (broken) content it holds.
            return [f"{target} must contain a JSON object; display settings not saved"]
        data = loaded

    data["display"] = {name: _jsonable(value) for name, value in values.items()}
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    try:
        atomic_write_text(target, payload)
    except OSError as exc:
        return [f"could not write {target}: {exc}; display settings not saved"]
    return []


def _jsonable(value: Any) -> Any:
    """Coerce a widget value to something ``json.dumps`` accepts."""
    if value is None or isinstance(value, (int, float, str)):
        return value
    return str(value)
