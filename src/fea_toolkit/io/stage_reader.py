"""Stage-file reader — lossless ``MeshModel`` / ``SAPModelData`` round-trip.

Reads files written by :func:`fea_toolkit.io.stage_writer.write_model_stages`
(``.h5`` or ``.npz``) and offers three access levels:

* :func:`read_model_stages` — full lossless round-trip (``==`` equality)
  of a ``SAPModelData`` (``stage=\"sap\"``) or ``MeshModel``
  (``stage=\"mesh\"``), plus the stored builder/preprocessor config.
* :func:`read_stage_arrays` — the lightweight geometry arrays only
  (no codec involved); the fast path for Rhino / PyVista.
* :func:`read_dictionary_arrays` — the self-describing dictionary
  blocks (sections, materials, ...).
* :func:`flatten_stage` — promote one stage's geometry to the top level
  so legacy consumers (result colouring, PyVista) work against a single
  stage file.

Format detection mirrors :func:`fea_toolkit.io.npz_reader.read_results`
(by file extension).  This module never imports ``openseespy`` and only
uses ``numpy`` + (optionally) ``h5py`` for HDF5, so it is safe to import
inside Rhino 8.
"""

from __future__ import annotations

import json
import typing as t

import numpy as np

from .model_codec import json_to_model

#: Prefix under which stage payloads live in the flat array namespace.
STAGE_PREFIX = "stage/"


def _read_flat(path: str) -> dict[str, np.ndarray]:
    """Load a unified file into a flat ``{key: array}`` dict."""
    from .npz_reader import read_results

    return read_results(path)


def _get_stage_names(data: dict[str, np.ndarray]) -> list[str]:
    """Return the stage names present in a loaded file, in pipeline order."""
    names = [k.split("/")[1] for k in data if k.startswith(STAGE_PREFIX) and k.count("/") >= 2]
    from .stage_writer import STAGE_NAMES

    return [n for n in STAGE_NAMES if n in names]


def _json_scalar(data: dict[str, np.ndarray], key: str) -> t.Any:
    """Decode a JSON string array element."""
    arr = data.get(key)
    if arr is None or len(arr) == 0:
        return None
    raw = arr[0]
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return str(raw)


def get_schema_version(data: dict[str, np.ndarray]) -> int:
    """Return the file ``schema_version`` (1 for legacy files without one)."""
    from .results_schema import SCHEMA_VERSION_LEGACY

    arr = data.get("schema_version")
    if arr is None or len(arr) == 0:
        return SCHEMA_VERSION_LEGACY
    try:
        return int(arr[0])
    except (TypeError, ValueError):
        return SCHEMA_VERSION_LEGACY


def read_stage_arrays(path: str, stage: str) -> dict[str, np.ndarray]:
    """Read the lightweight geometry arrays for one stage.

    Args:
        path: ``.h5`` / ``.npz`` stage file.
        stage: Stage name, e.g. ``\"sap\"`` or ``\"mesh\"``.

    Returns:
        Dict of arrays (``node_*``, ``frame_*``, ``shell_*``) for the
        stage, or ``{}`` when the stage is absent.
    """
    data = _read_flat(path)
    prefix = f"{STAGE_PREFIX}{stage}/"
    return {k[len(prefix) :]: v for k, v in data.items() if k.startswith(prefix)}


def read_dictionary_arrays(path: str, stage: str) -> dict[str, t.Any]:
    """Read the self-describing dictionary blocks for one stage.

    Args:
        path: ``.h5`` / ``.npz`` stage file.
        stage: Stage name, e.g. ``\"sap\"`` or ``\"mesh\"``.

    Returns:
        Dict of decoded JSON blocks (``sections_json`` → dict of
        section dicts, ``materials_json`` → dict of material dicts, ...).
    """
    data = _read_flat(path)
    prefix = f"{STAGE_PREFIX}{stage}/"
    out: dict[str, t.Any] = {}
    for k, v in data.items():
        if not k.startswith(prefix):
            continue
        name = k[len(prefix) :]
        if name.endswith("_json"):
            parsed = _json_scalar(data, k)
            if isinstance(parsed, dict):
                out[name] = parsed
        elif name == "model_name":
            raw = v[0]
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            out[name] = str(raw)
    return out


def read_metadata(path: str) -> dict[str, t.Any]:
    """Read the file-level metadata dict (``metadata_json``)."""
    data = _read_flat(path)
    meta = _json_scalar(data, "metadata_json")
    return meta if isinstance(meta, dict) else {}


def flatten_stage(
    source: t.Union[str, dict[str, np.ndarray]],
    stage: t.Optional[str] = None,
) -> dict[str, np.ndarray]:
    """Promote one stage's arrays to the top level of a stage file.

    Stage files namespace their geometry under ``stage/<stage>/...`` (see
    :func:`fea_toolkit.io.stage_writer.write_model_stages`), but legacy
    consumers — e.g. :func:`fea_toolkit.rhino.colour_from_npz.colour_from_npz`
    — expect unprefixed keys such as ``frame_sap_id``.  This helper
    overlays the requested stage's arrays onto the base namespace so a
    single stage file feeds both the model-review path and the
    result-colouring path.

    Args:
        source: A stage-file path (``.h5`` / ``.npz``) or a flat dict
            already loaded with
            :func:`fea_toolkit.io.npz_reader.read_results`.
        stage: Stage to promote (``\"sap\"`` / ``\"mesh\"``).  ``None`` →
            auto-detect, preferring ``\"mesh\"`` then ``\"sap\"``.

    Returns:
        Flat ``{key: array}`` dict with the selected stage's arrays at
        the top level, overlaying any same-named base arrays.

    Raises:
        ValueError: If *stage* is not present in the file.
    """
    data = _read_flat(source) if isinstance(source, str) else source
    available = _get_stage_names(data)
    if stage is None:
        stage = "mesh" if "mesh" in available else ("sap" if "sap" in available else None)
    if stage is None or stage not in available:
        raise ValueError(
            f"flatten_stage: stage {stage!r} not present (available stages: {available or 'none'})"
        )
    prefix = f"{STAGE_PREFIX}{stage}/"
    flat = {k: v for k, v in data.items() if not k.startswith(STAGE_PREFIX)}
    flat.update({k[len(prefix) :]: v for k, v in data.items() if k.startswith(prefix)})
    return flat


def read_model_stages(
    path: str,
    stage: str,
    *,
    cls: t.Optional[type] = None,
    return_config: bool = False,
):
    """Losslessly reconstruct a model from a stage file.

    Args:
        path: ``.h5`` / ``.npz`` stage file.
        stage: Stage to read — ``\"sap\"`` (→ ``SAPModelData``) or
            ``\"mesh\"`` (→ ``MeshModel``).
        cls: Expected class.  ``None`` → resolved from the codec
            ``__type__`` discriminator embedded in the payload.
        return_config: When ``True``, also return the stored builder /
            preprocessor config dict from the file metadata
            (``(model, config)``).

    Returns:
        The reconstructed model, or ``(model, config)`` when
        ``return_config`` is ``True``.

    Raises:
        ValueError: If the stage is absent or contains no ``model_json``.
    """
    data = _read_flat(path)
    key = f"{STAGE_PREFIX}{stage}/model_json"
    arr = data.get(key)
    if arr is None or len(arr) == 0:
        raise ValueError(
            f"Stage file {path!r} has no model payload for stage {stage!r} "
            f"(available stages: {_get_stage_names(data) or 'none'})"
        )
    raw = arr[0]
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    model = json_to_model(str(raw), cls=cls)
    if return_config:
        meta = _json_scalar(data, "metadata_json")
        config = meta.get("config") if isinstance(meta, dict) else None
        return model, config
    return model
