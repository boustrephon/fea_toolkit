"""JSON-safe codec for the nested-array payloads exchanged across the subprocess.

The out-of-process solver writes its non-archive results — the per-case raw
results and the load/reaction summaries — into ``meta.json`` alongside the
``results.npz`` archive.  Those payloads are nested dicts/lists that contain
NumPy arrays and scalars, so a plain ``json.dumps`` cannot write them.  This
module's two functions are the two halves of the lossless round-trip:

* :func:`nested_to_jsonable` — ndarray → ``{"__ndarray__": [dtype, shape,
  flat]}``, ``np.generic`` → its Python scalar, containers recurse.
* :func:`nested_from_jsonable` — the inverse.

Only NumPy arrays/scalars and JSON-native values (str/int/float/bool/None)
plus plain dict/list containers are accepted; anything else raises, so an
unexpected object type cannot be silently corrupted.
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["nested_from_jsonable", "nested_to_jsonable"]

#: Marker key identifying an encoded ndarray inside a JSON tree.
_MARKER = "__ndarray__"


def nested_to_jsonable(obj: Any) -> Any:
    """Recursively convert a nested NumPy payload into JSON-native form.

    Args:
        obj: A dict/list tree whose leaves are NumPy arrays/scalars or
            JSON-native values.

    Returns:
        An equivalent tree of plain Python objects.

    Raises:
        TypeError: If a value is neither a recognised NumPy type, a JSON-native
            value, nor a dict/list/tuple container.
    """
    if isinstance(obj, np.ndarray):
        return {_MARKER: [str(obj.dtype), list(obj.shape), np.asarray(obj).reshape(-1).tolist()]}
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, dict):
        return {str(key): nested_to_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [nested_to_jsonable(value) for value in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    raise TypeError(f"cannot encode {type(obj).__name__!r} in an exchange payload")


def nested_from_jsonable(obj: Any) -> Any:
    """Recursively reconstruct the NumPy payload :func:`nested_to_jsonable` wrote.

    Args:
        obj: The JSON-native tree (already parsed by ``json.loads``).

    Returns:
        The original nested dict/list tree with NumPy arrays and scalars
        restored.
    """
    if isinstance(obj, dict):
        marker = obj.get(_MARKER)
        if isinstance(marker, list) and len(marker) == 3:
            dtype, shape, flat = marker
            return np.array(flat, dtype=dtype).reshape(shape)
        return {key: nested_from_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [nested_from_jsonable(value) for value in obj]
    return obj
