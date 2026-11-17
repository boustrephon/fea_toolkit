"""The ``open_results`` verb — load a results archive's cases into the session.

Loads a results archive (``.npz`` / ``.h5`` / ``.hdf5``) and hands back its
cases as a :data:`~fea_toolkit.workflow.steps.CASES` result, so a recipe — or
the GUI's ``File ▸ Open results…`` — registers one view per case through the
same fan-out a static solve uses.  The archive carries its own display geometry,
so this verb needs no model and mutates no document state.
"""

from typing import Any

from ..steps import CASES, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["OPEN_RESULTS_PARAMS", "run_open_results"]

OPEN_RESULTS_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(default="", type=str, help="Path to the results archive (required)."),
}


def run_open_results(context: StepContext, step: Step) -> list[StepResult]:
    """Load a results archive and hand back its cases.

    Args:
        context: The run's working state — unused; the archive carries its own
            geometry, so ``model_data`` / ``model`` are never read.
        step: The step to run.  ``params`` carry ``path``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.CASES` result whose payload is the
        archive's full ``{name: ndarray}`` bundle.

    Raises:
        ValueError: If ``path`` is empty.
    """
    from ...io.results_repository import NpzResultsRepository

    params: dict[str, Any] = validate_params("open_results", OPEN_RESULTS_PARAMS, step.params)
    path = params["path"]
    if not path:
        raise ValueError("open_results: 'path' is required (the results archive to load)")

    repository = NpzResultsRepository(path)
    context.log(f"open_results: loaded {path} ({len(repository.cases())} case(s))")
    return [StepResult(kind=CASES, label="Opened results", payload=repository.to_arrays())]
