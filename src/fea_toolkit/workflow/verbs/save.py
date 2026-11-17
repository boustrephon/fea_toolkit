"""The ``save`` verb — write the working document to a (versioned) JSON file.

Persists ``context.model_data`` as a model-codec snapshot and, when named
selections are present, wraps it in a document envelope (see
:func:`fea_toolkit.io.document.write_document`) so an ``open`` step restores
them with the model.  The
write goes to a temporary file and is atomically moved over the target, and the
output path must end in ``.json`` (the payload is JSON regardless of the
original file's format).
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
    from ...io.document import write_document

    params: dict[str, Any] = validate_params("save", SAVE_PARAMS, step.params)
    path = params["path"] or context.document_path
    if not path:
        raise ValueError("save: 'path' is required (or the document must have been opened)")

    written = write_document(
        context.model_data,
        path,
        named_selections=context.named_selections,
        versioned=params["versioned"],
    )
    context.document_dirty = False
    context.document_path = str(written)
    context.log(f"save: wrote {written}")
    return [StepResult(kind=SESSION, label="Saved document", payload=str(written))]
