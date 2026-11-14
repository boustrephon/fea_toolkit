"""The ``save`` verb — write the working document to a (versioned) file.

A thin wrapper over :func:`fea_toolkit.io.model_codec.model_to_json` and
:func:`fea_toolkit.io.model_loader.next_version_path`, so a recipe persists the
working document exactly as the codec does.
"""

from typing import Any

from ..steps import SESSION, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["SAVE_PARAMS", "run_save"]

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
    """Write ``context.model_data`` as a model-codec JSON snapshot and return the path."""
    from pathlib import Path

    from ...io.model_codec import model_to_json
    from ...io.model_loader import next_version_path

    target = next_version_path(path) if versioned else path
    Path(target).write_text(model_to_json(context.model_data), encoding="utf-8")
    context.document_dirty = False
    return str(target)


def run_save(context: StepContext, step: Step) -> list[StepResult]:
    """Write the working document to a file.

    Args:
        context: The run's working state; ``model_data`` is the document,
            ``document_path`` the fallback output path.
        step: The step to run.  ``params`` carry ``path`` and ``versioned``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result carrying the path.

    Raises:
        ValueError: If no ``path`` is given and no document has been opened.
    """
    params: dict[str, Any] = validate_params("save", SAVE_PARAMS, step.params)
    path = params["path"] or context.document_path
    if not path:
        raise ValueError("save: 'path' is required (or the document must have been opened)")

    written = _write_document(context, path, versioned=params["versioned"])
    context.document_path = str(written)
    context.log(f"save: wrote {written}")
    return [StepResult(kind=SESSION, label="Saved document", payload=str(written))]
