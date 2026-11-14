"""The ``close`` verb — close the working document (optionally saving it).

Drops the prepared topology and results, and the current document path; the
parsed ``model_data`` stays in memory until an ``open`` step replaces it.
"""

from typing import Any

from ..steps import SESSION, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["CLOSE_PARAMS", "run_close"]

CLOSE_PARAMS: dict[str, ParamSpec] = {
    "save": ParamSpec(default=False, type=bool, help="Write the document before closing."),
    "versioned": ParamSpec(default=False, type=bool, help="Version the filename when saving."),
}


def run_close(context: StepContext, step: Step) -> list[StepResult]:
    """Close the working document, optionally saving it first.

    Args:
        context: The run's working state; the prepared ``model``, results and
            document path are cleared.
        step: The step to run.  ``params`` carry ``save`` and ``versioned``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result.

    Raises:
        ValueError: If ``save`` is requested but no document path is set.
    """
    params: dict[str, Any] = validate_params("close", CLOSE_PARAMS, step.params)
    if params["save"]:
        from .save import _write_document

        if not context.document_path:
            raise ValueError(
                "close: 'save' requested but no document path is set — run a 'save' step first"
            )
        written = _write_document(context, context.document_path, versioned=params["versioned"])
        context.log(f"close: saved {written}")

    context.model = None
    context.case_results.clear()
    context.results.clear()
    context.document_path = ""
    context.document_dirty = False
    context.log("close: document closed")
    return [StepResult(kind=SESSION, label="Closed document", payload="")]
