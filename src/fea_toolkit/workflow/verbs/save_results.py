"""The ``save_results`` verb — write the solved cases to a results archive.

Wraps :func:`~fea_toolkit.io.npz_writer.results_arrays` +
:func:`~fea_toolkit.io.npz_writer.save_results_arrays`: it assembles the
in-memory archive from the cases available so far (a solve's, or an
``open_results`` step's) and writes it to ``.npz``, so a run can solve and stop,
or solve then persist — the write is its own step, not part of the solve.

A results archive is the *display/exchange* form (geometry + displacements +
member forces), not the model: re-open it with ``open_results`` or the GUI's
``File ▸ Open results…``, not with ``open``.
"""

from typing import Any

from ..steps import SESSION, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["SAVE_RESULTS_PARAMS", "run_save_results"]

SAVE_RESULTS_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(
        default="",
        type=str,
        help="Output .npz path (required).",
    ),
}


def run_save_results(context: StepContext, step: Step) -> list[StepResult]:
    """Write the solved cases to a ``.npz`` results archive.

    Args:
        context: The run's working state.  ``case_results`` must hold the cases
            a previous ``run_static`` (optionally ``combine``) step solved, or
            an ``open_results`` step loaded; ``model`` and ``model_data`` supply
            the display geometry and unit labels.
        step: The step to run.  ``params["path"]`` is the output path.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result carrying the
        written path, or nothing when no case has been solved.

    Raises:
        ValueError: If ``path`` is missing.
    """
    params: dict[str, Any] = validate_params("save_results", SAVE_RESULTS_PARAMS, step.params)
    path = params["path"]
    if not path:
        raise ValueError("save_results: 'path' is required (the results archive to write)")

    if not context.case_results:
        context.log("save_results: no solved cases to write")
        return []

    from ...io.npz_writer import results_arrays, save_results_arrays

    arrays = results_arrays(
        context.model_data,
        static_results=dict(context.case_results),
        mesh_model=context.model,
    )
    written = save_results_arrays(path, arrays)
    context.log(f"save_results: wrote {written}")
    return [StepResult(kind=SESSION, label="Saved results", payload=str(written))]
