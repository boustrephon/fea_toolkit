"""The document envelope — a model plus its named selections, as one JSON file.

A *document* is what the ``open`` / ``save`` / ``close`` verbs exchange: a
model-codec snapshot of the working model, and — when the document carries
named selections — a top-level envelope keyed by :data:`DOCUMENT_KEY` so an
``open`` step restores them with the model.  The reader is shared by the
``open`` verb and the desktop GUI's ``File ▸ Open``, so a saved document
round-trips identically in both.  This module is OpenSees-free, like the rest
of :mod:`fea_toolkit.io`, so it is safe to import inside Rhino 8.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

__all__ = ["DOCUMENT_KEY", "load_document", "write_document"]

#: Top-level marker key distinguishing a document envelope (model + named
#: selections) from a bare model-codec snapshot.
DOCUMENT_KEY = "__document__"


def _publish_no_overwrite(tmp_path: str, base: Path) -> str:
    """Publish *tmp_path* under the first free ``_vN`` suffix of *base*.

    ``os.link`` creates the destination atomically and raises
    ``FileExistsError`` when it already exists, so the next suffix is tried
    instead — neither the new snapshot nor an existing one is lost to a
    concurrent writer.
    """
    index = 1
    while True:
        candidate = base.with_name(f"{base.stem}_v{index}{base.suffix}")
        try:
            os.link(tmp_path, candidate)
        except FileExistsError:
            index += 1
            continue
        os.unlink(tmp_path)
        return str(candidate)


def write_document(
    model_data: Any,
    path: Union[str, Path],
    *,
    named_selections: Optional[dict] = None,
    versioned: bool = False,
) -> str:
    """Write *model_data* (and its named selections) to a JSON document.

    The model is written as a model-codec snapshot; when *named_selections* are
    present they are persisted in a document envelope keyed by
    :data:`DOCUMENT_KEY`, so a later :func:`load_document` restores them with
    the model.  Writes go to a temporary file in the destination directory.  A
    versioned save publishes with atomic **no-overwrite** semantics (the next
    ``_vN`` suffix, retried on a collision), while a non-versioned save
    atomically replaces the target — a failed write never clobbers an existing
    document.

    Args:
        model_data: The model to persist (a ``SAPModelData`` or ``MeshModel``).
        path: Destination path; must end in ``.json``.
        named_selections: Optional ``{name: NamedSelection}`` to persist with
            the model.
        versioned: Version the filename (``_v1``/``_v2``…) instead of
            overwriting.

    Returns:
        The path actually written.

    Raises:
        ValueError: If *path* does not end in ``.json``.
    """
    import contextlib
    import json
    import tempfile

    from .model_codec import model_to_dict, model_to_json

    destination = Path(path)
    if destination.suffix.lower() != ".json":
        raise ValueError(
            "the document is written as model-codec JSON, so the output path "
            "must end in '.json' (an .s2k/.e2k/.f2k path would not round-trip "
            "through 'open')"
        )

    selections = (
        {}
        if not named_selections
        else {name: ns.to_dict() for name, ns in named_selections.items()}
    )
    if selections:
        payload = json.dumps(
            {
                DOCUMENT_KEY: True,
                "model": model_to_dict(model_data),
                "named_selections": selections,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    else:
        payload = model_to_json(model_data)

    fd, tmp_path = tempfile.mkstemp(dir=str(destination.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        if versioned:
            target = _publish_no_overwrite(tmp_path, destination)
        else:
            os.replace(tmp_path, destination)
            target = str(destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    return target


def load_document(path: Union[str, Path]) -> tuple[Any, dict]:
    """Load a saved document as ``(model, named_selections)``.

    A document envelope (:data:`DOCUMENT_KEY`) restores the persisted named
    selections alongside the model; a bare model-codec snapshot (or any other
    supported model file) returns an empty selection store.

    Args:
        path: Path to the document (a ``.json`` envelope or any supported model
            file).

    Returns:
        ``(model, named_selections)`` — *named_selections* is
        ``{name: NamedSelection}``, ``{}`` for a non-envelope file.
    """
    import json

    from ..model.named_selection import NamedSelection
    from .model_codec import dict_to_model
    from .model_loader import load_model_data

    p = Path(path)
    if p.suffix.lower() == ".json":
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get(DOCUMENT_KEY) is True:
            model = dict_to_model(payload.get("model"))
            selections = {
                str(name): NamedSelection.from_dict(data)
                for name, data in (payload.get("named_selections") or {}).items()
            }
            return model, selections
    return load_model_data(path), {}
