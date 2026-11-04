"""JSON-safe codec for the nested-array payloads exchanged across the subprocess.

The out-of-process solver writes its non-archive results — the per-case raw
results, the load/reaction summaries, and (for modal / response-spectrum /
pushover) the :class:`~fea_toolkit.analysis.base.AnalysisResult` — into
``meta.json`` alongside the ``results.npz`` archive.  Those payloads are nested
dicts/lists that contain NumPy arrays and scalars, integer dict keys and
tuples, so a plain ``json.dumps`` cannot write them losslessly.  This module's
functions are the two halves of that round-trip:

* :func:`nested_to_jsonable` — ndarray → ``{"__ndarray__": [dtype, shape,
  flat]}``, ``np.generic`` → its Python scalar, tuple → ``{"__tuple__": […]}``,
  a dict with any non-string key → ``{"__dict_items__": [[k, v], …]}``, then
  containers recurse.
* :func:`nested_from_jsonable` — the inverse.

The non-string-key and tuple encodings keep the round-trip **faithful** — the
modal result's mode shapes are ``{mode_index: {node_tag: (dx, dy, dz)}}`` and a
consumer that indexes ``shapes[mode][node]`` with integers would silently get
nothing back from a string-keyed reconstruction.

Only NumPy arrays/scalars, tuples, JSON-native values (str/int/float/bool/None)
and plain dict/list containers are accepted; anything else raises, so an
unexpected object type cannot be silently corrupted.

:func:`analysis_result_to_jsonable` / :func:`analysis_result_from_jsonable`
encode and restore a whole :class:`~fea_toolkit.analysis.base.AnalysisResult`
on top of the same codec.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..analysis.base import AnalysisResult

__all__ = [
    "analysis_result_from_jsonable",
    "analysis_result_to_jsonable",
    "nested_from_jsonable",
    "nested_to_jsonable",
]

#: Marker key identifying an encoded ndarray inside a JSON tree.
_MARKER = "__ndarray__"
#: Marker key identifying an encoded tuple (lists stay plain lists).
_TUPLE_MARKER = "__tuple__"
#: Marker key identifying a dict whose keys are not all strings.
_DICT_MARKER = "__dict_items__"


def nested_to_jsonable(obj: Any) -> Any:
    """Recursively convert a nested NumPy payload into JSON-native form.

    Args:
        obj: A dict/list tree whose leaves are NumPy arrays/scalars, tuples or
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
    if isinstance(obj, tuple):
        return {_TUPLE_MARKER: [nested_to_jsonable(value) for value in obj]}
    if isinstance(obj, dict):
        if all(isinstance(key, str) for key in obj):
            return {key: nested_to_jsonable(value) for key, value in obj.items()}
        return {
            _DICT_MARKER: [
                [nested_to_jsonable(key), nested_to_jsonable(value)] for key, value in obj.items()
            ]
        }
    if isinstance(obj, list):
        return [nested_to_jsonable(value) for value in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    raise TypeError(f"cannot encode {type(obj).__name__!r} in an exchange payload")


def nested_from_jsonable(obj: Any) -> Any:
    """Recursively reconstruct the NumPy payload :func:`nested_to_jsonable` wrote.

    Args:
        obj: The JSON-native tree (already parsed by ``json.loads``).

    Returns:
        The original nested dict/list tree with NumPy arrays, scalars, integer
        keys and tuples restored.
    """
    if isinstance(obj, dict):
        marker = obj.get(_MARKER)
        if isinstance(marker, list) and len(marker) == 3:
            dtype, shape, flat = marker
            return np.array(flat, dtype=dtype).reshape(shape)
        tuple_marker = obj.get(_TUPLE_MARKER)
        if isinstance(tuple_marker, list):
            return tuple(nested_from_jsonable(value) for value in tuple_marker)
        dict_marker = obj.get(_DICT_MARKER)
        if isinstance(dict_marker, list):
            return {
                nested_from_jsonable(key): nested_from_jsonable(value) for key, value in dict_marker
            }
        return {key: nested_from_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [nested_from_jsonable(value) for value in obj]
    return obj


def analysis_result_to_jsonable(result: AnalysisResult) -> dict:
    """Encode an :class:`~fea_toolkit.analysis.base.AnalysisResult` as JSON.

    Args:
        result: The result to encode; ``data`` and ``metadata`` must be trees of
            NumPy arrays/scalars, tuples and JSON-native values.

    Returns:
        A JSON-native mapping ``{"name", "analysis_type", "data", "metadata"}``.
    """
    return {
        "name": result.name,
        "analysis_type": result.analysis_type,
        "data": nested_to_jsonable(result.data),
        "metadata": nested_to_jsonable(result.metadata),
    }


def analysis_result_from_jsonable(obj: Any) -> AnalysisResult:
    """Reconstruct the :class:`~fea_toolkit.analysis.base.AnalysisResult` written
    by :func:`analysis_result_to_jsonable`.

    Args:
        obj: The JSON-native mapping (already parsed by ``json.loads``).

    Returns:
        An :class:`~fea_toolkit.analysis.base.AnalysisResult` with its ``data``
        and ``metadata`` restored.
    """
    return AnalysisResult(
        name=obj["name"],
        analysis_type=obj["analysis_type"],
        data=nested_from_jsonable(obj["data"]),
        metadata=nested_from_jsonable(obj["metadata"]),
    )
