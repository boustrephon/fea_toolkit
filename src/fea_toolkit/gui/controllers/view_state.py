"""Per-model view state for the desktop GUI, persisted beside the user settings.

The user's interaction and display preferences live in ``gui.json``
(:mod:`fea_toolkit.gui.controllers.settings`).  This module persists *per-model
view state* -- the camera and the hidden-element set -- so reopening a model
restores how it was last left.  It is state, not configuration: it is keyed by
the model's absolute path and stored in a ``state/`` directory beside the
settings file, so the two never compete for one file.

Missing or malformed state recovers gracefully (the same contract
:func:`fea_toolkit.gui.controllers.settings.load_gui_settings` keeps), and every
write is atomic, mirroring
:func:`fea_toolkit.gui.controllers.settings.save_display_settings`.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Union

from ._atomic import atomic_write_text
from .interaction import default_config_path

__all__ = [
    "clear_view_state",
    "load_view_state",
    "save_view_state",
    "view_state_path",
]

#: JSON keys a per-model state file may carry (the view knobs).
_STATE_FIELDS = frozenset({"camera_position", "hidden_elem_ids"})

#: The reserved key holding the model's content hash, used to drop stale state.
_MODEL_HASH_KEY = "model_hash"

#: Keys this module understands -- the knobs plus the staleness guard.
_KNOWN_KEYS = _STATE_FIELDS | {_MODEL_HASH_KEY}


def view_state_path(source: Union[str, Path]) -> Path:
    """Return the path where *source*'s view state lives.

    The file name is a short hash of the source's absolute path, so two models
    cannot collide, and the directory is the settings file's directory plus
    ``state/`` -- which keeps it machine-local and, in tests, under the pinned
    ``FEA_TOOLKIT_GUI_CONFIG`` directory.

    Args:
        source: The model's path, used as the state key.

    Returns:
        The path to the per-model view state JSON file.
    """
    key = hashlib.sha256(str(Path(source).expanduser().resolve()).encode()).hexdigest()[:16]
    return default_config_path().parent / "state" / f"{key}.json"


def _content_hash(source: Union[str, Path]) -> Optional[str]:
    """Return the SHA-256 digest of the model file's bytes.

    Args:
        source: The model's path whose file content is hashed.

    Returns:
        The hexadecimal digest, or ``None`` when the file cannot be read.
    """
    digest = hashlib.sha256()
    try:
        with Path(source).expanduser().resolve().open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_view_state(source: Union[str, Path]) -> "tuple[dict[str, Any], list[str]]":
    """Read the saved view state for *source*.

    A missing file is the normal case (first open) and yields empty state with
    no note.  An unreadable, malformed or non-object file yields empty state
    with a note, so the model still opens.  State saved for a different
    *content* of the same path is dropped as stale, so a re-edited model cannot
    inherit a hidden set or camera that no longer applies.

    Args:
        source: The model's path, used as the state key.

    Returns:
        ``(state, notes)`` -- *notes* explains anything that could not be used.
    """
    path = view_state_path(source)
    if not path.is_file():
        return {}, []
    try:
        data: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"could not read view state {path}: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"view state {path} must be a JSON object"]
    saved_hash = data.get(_MODEL_HASH_KEY)
    current_hash = _content_hash(source)
    if saved_hash is None or current_hash is None or saved_hash != current_hash:
        return {}, [f"view state {path} is stale (the model changed since it was saved)"]
    notes: list[str] = []
    for key in sorted(set(data) - _KNOWN_KEYS):
        notes.append(f"ignoring unknown view state key {key!r}")
    state = {key: data[key] for key in _STATE_FIELDS if key in data}
    return state, notes


def save_view_state(source: Union[str, Path], state: dict) -> list[str]:
    """Persist *state* for *source*, atomically.

    Only the keys in ``_STATE_FIELDS`` are stored; anything else is dropped, so
    the file stays exactly what :func:`load_view_state` understands.  The model's
    content hash is stored alongside the state so a later load can detect that
    the file changed.

    Args:
        source: The model's path, used as the state key.
        state: The view knobs to store, e.g.
            ``{"camera_position": [...], "hidden_elem_ids": [...]}``.

    Returns:
        Notes to log, or ``[]`` when the write succeeded silently.
    """
    path = view_state_path(source)
    payload_state = {key: state[key] for key in _STATE_FIELDS if key in state}
    content_hash = _content_hash(source)
    if content_hash is not None:
        payload_state[_MODEL_HASH_KEY] = content_hash
    payload = json.dumps(payload_state, indent=2, sort_keys=True) + "\n"
    try:
        atomic_write_text(path, payload)
    except OSError as exc:
        return [f"could not write view state {path}: {exc}"]
    return []


def clear_view_state(source: Union[str, Path]) -> list[str]:
    """Forget *source*'s saved view state.

    Args:
        source: The model's path, used as the state key.

    Returns:
        Notes to log, or ``[]`` when the file was removed (or already absent).
    """
    path = view_state_path(source)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return [f"could not clear view state {path}: {exc}"]
    return []
