"""The ``open`` verb — load a model file into the working document.

A thin wrapper over :func:`fea_toolkit.io.model_loader.load_model_data`: the
working ``model_data`` is replaced and the prepared topology / results cleared,
so a recipe beginning with ``open`` is a *document recipe*.
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
            prepared ``model`` / results cleared, and ``document_path`` set.
        step: The step to run.  ``params`` carry ``path``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result carrying the path.

    Raises:
        ValueError: If ``path`` is empty.
        FileNotFoundError: If the file does not exist (from ``load_model_data``).
    """
    from ...io.model_loader import load_model_data
    from ...model.mesh_model import MeshModel

    params: dict[str, Any] = validate_params("open", OPEN_PARAMS, step.params)
    path = params["path"]
    if not path:
        raise ValueError("open: 'path' is required (the model file to load)")

    loaded = load_model_data(path)
    context.model_data = loaded
    context.model = loaded if isinstance(loaded, MeshModel) else None
    context.case_results.clear()
    context.results.clear()
    context.document_path = str(path)
    context.document_dirty = False
    context.log(f"open: loaded {path}")
    return [StepResult(kind=SESSION, label="Opened document", payload=str(path))]
