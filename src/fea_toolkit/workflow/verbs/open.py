"""The ``open`` verb — load a model file into the working document.

Loads the model through :func:`~fea_toolkit.workflow.verbs.save._load_document`
(a wrapper over :func:`fea_toolkit.io.model_loader.load_model_data` that also
restores a document's persisted named selections).  The working ``model_data``
is replaced, the prepared topology / results are cleared, and the
named-selection store is replaced with the document's own — so a recipe
beginning with ``open`` is a *document recipe*.
"""

from typing import Any

from ..steps import SESSION, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["OPEN_PARAMS", "run_open"]

OPEN_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(default="", type=str, help="Path to the model file (required)."),
}


def run_open(context: StepContext, step: Step) -> list[StepResult]:
    """Load a model file as the working document.

    Args:
        context: The run's working state; ``model_data`` is replaced, the
            prepared ``model`` / results cleared, ``named_selections`` replaced
            with the document's own, and ``document_path`` set.
        step: The step to run.  ``params`` carry ``path``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result carrying the path.

    Raises:
        ValueError: If ``path`` is empty.
        FileNotFoundError: If the file does not exist.
    """
    from ...model.mesh_model import MeshModel
    from .save import _load_document

    params: dict[str, Any] = validate_params("open", OPEN_PARAMS, step.params)
    path = params["path"]
    if not path:
        raise ValueError("open: 'path' is required (the model file to load)")

    loaded, selections = _load_document(path)
    context.model_data = loaded
    context.model = loaded if isinstance(loaded, MeshModel) else None
    context.case_results.clear()
    context.results.clear()
    # Named selections belong to the document being opened — replace the
    # previous document's store so a stale reference can never resolve against
    # the new model.
    context.named_selections.clear()
    context.named_selections.update(selections)
    context.document_path = str(path)
    context.document_dirty = False
    context.log(f"open: loaded {path}")
    return [StepResult(kind=SESSION, label="Opened document", payload=str(path))]
