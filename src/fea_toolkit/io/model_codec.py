"""Dataclass ⟷ JSON-safe dict codec for the model layer.

Round-trips :class:`~fea_toolkit.model.sap_data.SAPModelData` and
:class:`~fea_toolkit.model.mesh_model.MeshModel` (including every nested
dataclass, the polymorphic :class:`~fea_toolkit.model.sap_data.Section`
hierarchy, and all 40 ``MeshModel`` fields) through a JSON-safe
representation.

Design rules (see ``docs/model_stage_file.md``):

* **Introspection-driven** — both directions walk ``dataclasses.fields()``
  + ``typing.get_type_hints()``.  Adding a field to a model class is
  handled automatically; the guard test
  (:func:`check_round_trip_types`) fails if a new field's type has no
  codec rule.
* **``__type__`` discriminator** — polymorphic types (``Section`` and its
  15 subclasses) are tagged with the runtime class name so
  reconstruction dispatches to the correct subclass.
* **JSON-safe** — tuples/sets are converted to lists, ``None`` for
  ``Optional`` values, and numpy scalars are coerced to Python natives.
  On decode, configured tuple fields are re-tupled so ``==`` round-trips
  exactly.
* **Deterministic** — dicts are written with ``sort_keys=True`` so the
  same model yields byte-identical JSON (stage files can be diffed).

This module imports nothing from the ``opensees`` package and never
imports ``openseespy``, so it is safe to import inside Rhino 8.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import typing as t
import warnings

import numpy as np

from ..model.mesh_model import MeshModel, WallElement
from ..model.sap_data import SAPModelData, Section

# ═══════════════════════════════════════════════════════════════════
# Schema versioning
# ═══════════════════════════════════════════════════════════════════

#: Version of the ``stage/*/model_json`` payload.  Bump only on a
#: backward-incompatible layout change; the file-level
#: ``schema_version`` (see :mod:`fea_toolkit.io.results_schema`) tracks
#: the whole results-file schema.
MODEL_SCHEMA_VERSION = 1

#: Marker key carrying the runtime class name of an encoded dataclass.
TYPE_KEY = "__type__"


# ═══════════════════════════════════════════════════════════════════
# Field-level codec rules for types the annotations can't express
# ═══════════════════════════════════════════════════════════════════


class _LazyTarget:
    """Deferred class reference resolved on first access.

    Keeps :mod:`fea_toolkit.io.model_codec` import-light: the actual
    class is imported lazily rather than at module import time.
    """

    def __init__(self, dotted_path: str) -> None:
        self._dotted_path = dotted_path
        self._cls: t.Optional[type] = None

    @property
    def cls(self) -> type:
        if self._cls is None:
            import importlib

            mod_name, _, attr = self._dotted_path.rpartition(".")
            self._cls = getattr(importlib.import_module(mod_name), attr)
        return self._cls


def _resolve(cls: t.Any) -> type:
    return cls.cls if isinstance(cls, _LazyTarget) else cls


# ``MeshModel.detected_edge_pairs`` / ``offset_rigid_links`` /
# ``edge_constraint_args`` are annotated ``list[tuple]`` (no element
# types) but JSON converts tuples to lists.  These fields are re-tupled
# on decode so ``==`` round-trips exactly.  Re-tupling is applied to the
# top-level elements of the list only (nested lists are preserved).
TUPLE_FIELDS: frozenset[str] = frozenset(
    {
        "detected_edge_pairs",
        "offset_rigid_links",
        "edge_constraint_args",
        # ``(z_level, [node_id, ...])`` — outer element is a tuple.
        "diaphragm_components",
    }
)

# ``MeshModel.edge_loads_from_areas`` is a *bare* ``list`` annotation but
# its runtime contents are :class:`FrameDistributedLoad` instances
# (produced by ``convert_area_loads_to_edge_loads``).  Decode them as a
# dataclass list.
BARE_LIST_TYPES: dict[str, t.Any] = {
    "edge_loads_from_areas": _LazyTarget("fea_toolkit.model.sap_data.FrameDistributedLoad"),
}


# ═══════════════════════════════════════════════════════════════════
# Class registry — discovered from the model classes
# ═══════════════════════════════════════════════════════════════════


def _iter_type_args(h: t.Any) -> t.Iterator[type]:
    """Yield the concrete classes reachable inside a type hint."""
    origin = t.get_origin(h)
    if origin is not None:
        for a in t.get_args(h):
            yield from _iter_type_args(a)
        return
    if isinstance(h, type):
        yield h


def _collect_dataclasses() -> dict[str, type]:
    """Build ``{class_name: class}`` for every dataclass reachable from
    ``SAPModelData`` and ``MeshModel`` type hints, plus all ``Section``
    subclasses."""
    registry: dict[str, type] = {}

    def _add(cls: type) -> None:
        if dataclasses.is_dataclass(cls):
            registry[cls.__name__] = cls

    def _walk(model_cls: type) -> None:
        try:
            hints = t.get_type_hints(model_cls)
        except Exception:  # pragma: no cover - defensive
            hints = {}
        for h in hints.values():
            for candidate in _iter_type_args(h):
                if dataclasses.is_dataclass(candidate):
                    _add(candidate)

    _walk(SAPModelData)
    _walk(MeshModel)
    _add(WallElement)
    _add(SAPModelData)
    _add(MeshModel)
    for sub in Section.__subclasses__():
        _add(sub)
    return registry


_CLASS_REGISTRY: dict[str, type] = _collect_dataclasses()

#: Cached ``get_type_hints`` per model class (forward refs resolved once).
_MODEL_HINTS_CACHE: dict[type, dict[str, t.Any]] = {}


def _model_hints(cls: type) -> dict[str, t.Any]:
    """Resolve ``get_type_hints`` once per class (cached).

    ``get_type_hints`` does not always resolve string forward references
    (e.g. ``dict[str, 'LoadPattern']`` under deferred annotation
    evaluation), so string hints are additionally resolved against the
    defining module's namespace and the codec registry — recursively,
    since the string may sit inside a generic subscript.
    """
    if cls not in _MODEL_HINTS_CACHE:
        try:
            hints = t.get_type_hints(cls)
        except Exception:  # pragma: no cover - defensive
            hints = {}
        module = getattr(cls, "__module__", None)
        import importlib

        ns = getattr(importlib.import_module(module), "__dict__", {}) if module else {}

        def _sub(h: t.Any) -> t.Any:
            if isinstance(h, str):
                resolved = _CLASS_REGISTRY.get(h) or ns.get(h)
                return resolved if resolved is not None else h
            origin = t.get_origin(h)
            if origin is not None:
                args = t.get_args(h)
                if not args:
                    return h
                try:
                    return origin[tuple(_sub(a) for a in args)]
                except Exception:  # pragma: no cover - defensive
                    return h
            return h

        for key, h in list(hints.items()):
            hints[key] = _sub(h)
        _MODEL_HINTS_CACHE[cls] = hints
    return _MODEL_HINTS_CACHE[cls]


# ═══════════════════════════════════════════════════════════════════
# Encoding (model objects → JSON-safe dict)
# ═══════════════════════════════════════════════════════════════════


def _json_safe(value: t.Any) -> t.Any:
    """Coerce numpy scalars/arrays and unknown values to JSON-safe forms."""
    if value is None or isinstance(value, (bool, str, int, float, bytes)):
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        f = float(value)
        return None if not np.isfinite(f) else f
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return sorted(_json_safe(v) for v in value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value):
        return _encode_dataclass(value)
    return str(value)


def _value_matches_hint(value: t.Any, hint: t.Any) -> bool:
    """Best-effort check that *value* fits *hint* (Union dispatch)."""
    if dataclasses.is_dataclass(value):
        return dataclasses.is_dataclass(hint)
    if hint is str:
        return isinstance(value, str)
    if hint is int:
        return isinstance(value, (int, np.integer)) and not isinstance(value, bool)
    if hint is float:
        return isinstance(value, (float, int, np.floating, np.integer)) and not isinstance(
            value, bool
        )
    if hint is bool:
        return isinstance(value, bool)
    if isinstance(hint, type):
        return isinstance(value, hint)
    return True


def _encode_value(value: t.Any, hint: t.Any) -> t.Any:
    """Encode *value* against its resolved type hint *hint*."""
    if value is None:
        return None
    if hint is t.Any or hint is None or hint is inspect.Parameter.empty:
        return _json_safe(value)

    origin = t.get_origin(hint)
    if origin is not None:
        args = t.get_args(hint)
        if origin is t.Union:
            for a in args:
                if a is type(None):
                    continue
                if _value_matches_hint(value, a):
                    return _encode_value(value, a)
            return _json_safe(value)
        if origin in (list, tuple):
            elem = args[0] if args else t.Any
            if elem is t.Any:
                return [_json_safe(v) for v in value]
            return [_encode_value(v, elem) for v in value]
        if origin is dict:
            v_t = args[1] if len(args) == 2 else t.Any
            return {str(k): _encode_value(v, v_t) for k, v in value.items()}
        if origin is set:
            elem = args[0] if args else t.Any
            return sorted(_encode_value(v, elem) for v in value)
        if origin is t.Sequence:
            elem = args[0] if args else t.Any
            return [_encode_value(v, elem) for v in value]
        return _json_safe(value)

    if dataclasses.is_dataclass(value):
        return _encode_dataclass(value)
    return _json_safe(value)


def _encode_dataclass(obj: t.Any) -> dict[str, t.Any]:
    """Encode a dataclass instance with a ``__type__`` discriminator."""
    hints = _model_hints(type(obj))
    out: dict[str, t.Any] = {TYPE_KEY: type(obj).__name__}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if value is None:
            out[f.name] = None
            continue
        if f.name in TUPLE_FIELDS and isinstance(value, list):
            out[f.name] = [_json_safe(v) for v in value]
            continue
        if f.name in BARE_LIST_TYPES:
            out[f.name] = [_encode_value(v, _resolve(BARE_LIST_TYPES[f.name])) for v in value]
            continue
        hint = hints.get(f.name)
        out[f.name] = _encode_value(value, hint)
    return out


# ═══════════════════════════════════════════════════════════════════
# Decoding (JSON-safe dict → model objects)
# ═══════════════════════════════════════════════════════════════════


def _decode_applicable(data: t.Any, hint: t.Any) -> bool:
    """Whether *data* can be decoded as *hint* (Union dispatch)."""
    if hint is None or hint is t.Any:
        return True
    origin = t.get_origin(hint)
    if origin is not None:
        return isinstance(data, (list, dict, tuple, set)) or data is None
    if isinstance(hint, str):
        return True
    if dataclasses.is_dataclass(hint):
        return isinstance(data, dict)
    if hint is str:
        return isinstance(data, str)
    if hint is int:
        return isinstance(data, int) and not isinstance(data, bool)
    if hint is float:
        return isinstance(data, (float, int)) and not isinstance(data, bool)
    if hint is bool:
        return isinstance(data, bool)
    return True


def _decode_value(data: t.Any, hint: t.Any) -> t.Any:
    """Decode *data* against its resolved type hint *hint*."""
    if data is None:
        return None
    if hint is t.Any or hint is None or hint is inspect.Parameter.empty:
        return data

    origin = t.get_origin(hint)
    if origin is not None:
        args = t.get_args(hint)
        if origin is t.Union:
            for a in args:
                if a is type(None):
                    continue
                if _decode_applicable(data, a):
                    return _decode_value(data, a)
            return data
        if origin in (list, tuple):
            elem = args[0] if args else t.Any
            return [_decode_value(v, elem) for v in data]
        if origin is dict:
            v_t = args[1] if len(args) == 2 else t.Any
            return {str(k): _decode_value(v, v_t) for k, v in data.items()}
        if origin is set:
            elem = args[0] if args else t.Any
            return {_decode_value(v, elem) for v in data}
        if origin is t.Sequence:
            elem = args[0] if args else t.Any
            return [_decode_value(v, elem) for v in data]
        return data

    if isinstance(hint, str):
        # Unresolved forward reference — caller should have resolved it.
        return data
    if dataclasses.is_dataclass(hint):
        return _decode_dataclass(data, hint)
    return data


def _decode_dataclass(data: dict[str, t.Any], cls: type) -> t.Any:
    """Reconstruct a dataclass from an encoded dict.

    Uses the ``__type__`` discriminator when present (polymorphic
    dispatch) and ignores unknown keys so forward-compatible files keep
    working.
    """
    type_name = data.get(TYPE_KEY)
    target: t.Optional[type] = None
    if isinstance(type_name, str):
        target = _CLASS_REGISTRY.get(type_name)
        if target is None:
            warnings.warn(
                f"model_codec: unknown __type__ {type_name!r} — falling back to {cls.__name__}",
                stacklevel=3,
            )
    if target is None:
        target = cls
    if not dataclasses.is_dataclass(target):
        target = cls

    hints = _model_hints(target)
    kwargs: dict[str, t.Any] = {}
    for f in dataclasses.fields(target):
        if f.name not in data:
            continue
        raw = data[f.name]
        if raw is None:
            kwargs[f.name] = None
            continue
        if f.name in TUPLE_FIELDS and isinstance(raw, list):
            kwargs[f.name] = [tuple(v) if isinstance(v, list) else v for v in raw]
            continue
        if f.name in BARE_LIST_TYPES:
            kwargs[f.name] = [_decode_value(v, _resolve(BARE_LIST_TYPES[f.name])) for v in raw]
            continue
        hint = hints.get(f.name)
        kwargs[f.name] = _decode_value(raw, hint)

    # Missing fields use their dataclass defaults.
    return target(**kwargs)


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════


def model_to_dict(model: t.Any) -> dict[str, t.Any]:
    """Encode a ``SAPModelData`` or ``MeshModel`` to a JSON-safe dict.

    Args:
        model: A ``SAPModelData`` or ``MeshModel`` instance.

    Returns:
        Dict with a ``__type__`` key and one entry per dataclass field,
        recursively encoded.
    """
    if not dataclasses.is_dataclass(model):
        raise TypeError(f"model_to_dict expects a dataclass model, got {type(model).__name__}")
    return _encode_dataclass(model)


def dict_to_model(data: dict[str, t.Any], cls: t.Optional[type] = None) -> t.Any:
    """Reconstruct a ``SAPModelData`` / ``MeshModel`` from an encoded dict.

    Args:
        data: Dict produced by :func:`model_to_dict`.
        cls: Expected class.  ``None`` → resolved from the ``__type__``
            discriminator.

    Returns:
        A new model instance, field-for-field equal to the original
        (``==`` passes for ``dataclasses``).
    """
    if cls is None:
        type_name = data.get(TYPE_KEY)
        cls = _CLASS_REGISTRY.get(type_name) if isinstance(type_name, str) else None
        if cls is None:
            raise ValueError(f"Cannot resolve __type__ {type_name!r} to a known dataclass")
    return _decode_dataclass(data, cls)


def model_to_json(model: t.Any, *, sort_keys: bool = True) -> str:
    """Encode a model to a compact JSON string (deterministic when
    ``sort_keys`` is ``True``)."""
    return json.dumps(
        model_to_dict(model),
        sort_keys=sort_keys,
        separators=(",", ":"),
        allow_nan=False,
    )


def json_to_model(payload: str, cls: t.Optional[type] = None) -> t.Any:
    """Decode a model from a JSON string produced by :func:`model_to_json`."""
    return dict_to_model(json.loads(payload), cls=cls)


def check_round_trip_types() -> list[str]:
    """Verify every ``SAPModelData`` / ``MeshModel`` field type is codec-safe.

    Returns a list of diagnostics (empty when all fields are covered).
    The corresponding test asserts the list is empty, so adding a model
    field with an unknown type fails loudly instead of silently producing
    lossy round-trips.
    """
    diagnostics: list[str] = []
    allowed_primitives = (str, int, float, bool, bytes, type(None), t.Any)

    for model_cls in (SAPModelData, MeshModel):
        hints = _model_hints(model_cls)
        for f in dataclasses.fields(model_cls):
            if f.name in TUPLE_FIELDS or f.name in BARE_LIST_TYPES:
                continue
            hint = hints.get(f.name)
            if hint is None or hint is t.Any:
                continue
            problems = _check_hint(hint, allowed_primitives, model_cls, f.name)
            diagnostics.extend(problems)
    return diagnostics


def _check_hint(hint: t.Any, prims: tuple, model_cls: type, field: str) -> list[str]:
    """Recursively check that a resolved type hint is codec-supported."""
    if hint in prims:
        return []
    origin = t.get_origin(hint)
    if origin is not None:
        args = t.get_args(hint)
        if origin is t.Union:
            out: list[str] = []
            for a in args:
                out.extend(_check_hint(a, prims, model_cls, field))
            return out
        if origin in (list, tuple, set, t.Sequence):
            if not args:
                return [
                    f"{model_cls.__name__}.{field}: bare {origin} has no element type "
                    f"(add an explicit rule in BARE_LIST_TYPES/TUPLE_FIELDS)"
                ]
            return _check_hint(args[0], prims, model_cls, field)
        if origin is dict:
            out = []
            for a in args:
                out.extend(_check_hint(a, prims, model_cls, field))
            return out
        return [f"{model_cls.__name__}.{field}: unsupported origin {origin}"]
    if isinstance(hint, str):
        return [f"{model_cls.__name__}.{field}: unresolved forward reference {hint!r}"]
    if dataclasses.is_dataclass(hint):
        return []
    return [f"{model_cls.__name__}.{field}: unsupported type {hint!r}"]
