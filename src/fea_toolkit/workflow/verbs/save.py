"""The ``save`` verb — write the working document to a (versioned) JSON file.

Persists ``context.model_data`` as a model-codec snapshot and, when named
selections are present, wraps it in a document envelope (keyed by
:data:`DOCUMENT_KEY`) so an ``open`` step restores them with the model.  The
write goes to a temporary file and is atomically moved over the target, and the
output path must end in ``.json`` (the payload is JSON regardless of the
original file's format).
"""

from typing import Any

from ..steps import SESSION, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["SAVE_PARAMS", "run_save"]

#: Top-level marker key distinguishing a document envelope (model + named
#: selections) from a bare model-codec snapshot.
DOCUMENT_KEY = "__document__"

SAVE_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(
        default="",
        type=str,
        help="Output path; empty uses the open document's path.",
    ),
    "versioned": ParamSpec(
        default=False,
        type=bool,
        help="Version the filename (_v1/_v2…) instead of overwriting.",
    ),
}


def _write_document(context: StepContext, path: str, *, versioned: bool = False) -> str:
    """Write ``context.model_data`` (and its named selections) to a JSON document.

    The model is written as a model-codec snapshot; when the context holds named
    selections they are persisted in a document envelope keyed by
    :data:`DOCUMENT_KEY`, so an ``open`` step restores them with the model.
    Writes go to a temporary file in the destination directory and are
    atomically moved over the target only once the write has succeeded, so a
    failed write never clobbers an existing document.
    """
    import contextlib
    import json
    import os
    import tempfile
    from pathlib import Path

    from ...io.model_codec import model_to_dict, model_to_json
    from ...io.model_loader import next_version_path

    target = next_version_path(path) if versioned else path
    destination = Path(target)
    if destination.suffix.lower() != ".json":
        raise ValueError(
            "save: the document is written as model-codec JSON, so the output "
            "path must end in '.json' (an .s2k/.e2k/.f2k path would not "
            "round-trip through 'open')"
        )

    selections = {name: ns.to_dict() for name, ns in context.named_selections.items()}
    if selections:
        payload = json.dumps(
            {
                DOCUMENT_KEY: True,
                "model": model_to_dict(context.model_data),
                "named_selections": selections,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    else:
        payload = model_to_json(context.model_data)

    fd, tmp_path = tempfile.mkstemp(dir=str(destination.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    context.document_dirty = False
    return str(target)


def _load_document(path: str) -> tuple[Any, dict]:
    """Load a saved document as ``(model, named_selections)``.

    A document envelope (:data:`DOCUMENT_KEY`) restores the persisted named
    selections alongside the model; a bare model-codec snapshot (or any other
    supported model file) returns an empty selection store.
    """
    import json
    from pathlib import Path

    from ...io.model_codec import dict_to_model
    from ...io.model_loader import load_model_data
    from ...model.named_selection import NamedSelection

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


def run_save(context: StepContext, step: Step) -> list[StepResult]:
    """Write the working document to a file.

    Args:
        context: The run's working state; ``model_data`` is the document,
            ``document_path`` the fallback output path.
        step: The step to run.  ``params`` carry ``path`` and ``versioned``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result carrying the path.

    Raises:
        ValueError: If no ``path`` is given and no document has been opened, or
            the resolved output path does not end in ``.json``.
    """
    params: dict[str, Any] = validate_params("save", SAVE_PARAMS, step.params)
    path = params["path"] or context.document_path
    if not path:
        raise ValueError("save: 'path' is required (or the document must have been opened)")

    written = _write_document(context, path, versioned=params["versioned"])
    context.document_path = str(written)
    context.log(f"save: wrote {written}")
    return [StepResult(kind=SESSION, label="Saved document", payload=str(written))]
