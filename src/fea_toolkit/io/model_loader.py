"""Load a model from any supported on-disk representation.

Three inputs are recognised, all producing the object a ``.s2k`` parse would
(so every downstream consumer — viewer, review, preprocessing — behaves
identically):

* **``.s2k`` / ``.$2k``** — the SAP2000 text export, parsed directly.
* **Raw-table JSON** — as written by
  :meth:`~fea_toolkit.io.s2k_parser.SAP2000Parser.to_json`: the parsed
  tables are restored and the model rebuilt exactly as from text.
* **Model-codec JSON** — as written by
  :func:`~fea_toolkit.io.model_codec.model_to_json`: a snapshot of a built
  dataclass model, tagged with a top-level ``__type__`` key and stamped
  with ``__schema_version__`` (validated on read).

The returned object is a :class:`~fea_toolkit.model.sap_data.SAPModelData`,
or — for a codec snapshot of a post-preprocessing model — the
:class:`~fea_toolkit.model.mesh_model.MeshModel` itself.

This module imports nothing from the ``opensees`` package and never imports
``openseespy``, so it is safe to import inside Rhino 8.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from ..model.mesh_model import MeshModel
from ..model.sap_data import SAPModelData
from .s2k_parser import SAP2000Parser

#: Suffixes routed to the SAP2000 text parser.
TEXT_SUFFIXES: tuple[str, ...] = (".s2k", ".$2k")

#: Top-level marker keys identifying a model-codec payload.
CODEC_MARKER_KEYS: tuple[str, ...] = ("__schema_version__", "__type__")


def load_model_data(path: Union[str, Path]) -> Union[SAPModelData, MeshModel]:
    """Load a model from a ``.s2k``, raw-table JSON or model-codec JSON file.

    Args:
        path: Path to the model file.  Recognised forms are the SAP2000 text
            export (``.s2k`` / ``.$2k``), a raw-table cache written by
            :meth:`~fea_toolkit.io.s2k_parser.SAP2000Parser.to_json`, and a
            model-codec snapshot written by
            :func:`~fea_toolkit.io.model_codec.model_to_json`.

    Returns:
        A ``SAPModelData``, or — for a codec snapshot of a post-preprocessing
        model — the ``MeshModel`` itself.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the suffix is unsupported, the JSON is malformed, or the
            payload is neither a model snapshot nor a table cache.  Note that
            parsing a raw-table JSON as *text* would silently yield an empty
            model, so an unrecognised payload is an error here rather than an
            empty result.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        parser = SAP2000Parser(path)
        parser.parse()
        return parser.get_model_data()

    if suffix != ".json":
        raise ValueError(
            f"unsupported model file {path.name!r}: expected a SAP2000 text export "
            f"({' / '.join(TEXT_SUFFIXES)}) or a parsed-model .json"
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    if isinstance(payload, dict) and any(key in payload for key in CODEC_MARKER_KEYS):
        # Model-codec snapshot: dict_to_model validates __schema_version__ and
        # resolves the class (SAPModelData / MeshModel) from __type__.
        from .model_codec import dict_to_model

        try:
            return dict_to_model(payload)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path} is not a decodable model snapshot: {exc}") from exc

    # Raw-table cache: ``{table_name: [row, ...]}``.
    looks_like_tables = (
        isinstance(payload, dict)
        and bool(payload)
        and all(isinstance(rows, list) for rows in payload.values())
    )
    if not looks_like_tables:
        raise ValueError(
            f"{path} holds neither a model snapshot (no '__type__' / "
            "'__schema_version__') nor SAP2000 tables — was it written by "
            "SAP2000Parser.to_json() or model_codec.model_to_json()?"
        )
    return SAP2000Parser.from_json(path).get_model_data()
