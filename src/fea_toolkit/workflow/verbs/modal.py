"""The ``modal`` verb — solve the eigenvalue problem.

Wraps :func:`~fea_toolkit.analysis.modal.run_modal_analysis`.  The result is
stored on :attr:`~fea_toolkit.workflow.steps.StepContext.results` under
``"modal"`` so a later
:mod:`~fea_toolkit.workflow.verbs.response_spectrum` or
:mod:`~fea_toolkit.workflow.verbs.pushover` step can consume it.  The ordering
between them is the declared ``needs`` chain, not an ad-hoc context slot.

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..steps import MODAL, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["MODAL_PARAMS", "run_modal"]

MODAL_PARAMS: dict[str, ParamSpec] = {
    "n_modes": ParamSpec(
        default=12,
        type=int,
        help="Number of modes to extract.",
    ),
    "name": ParamSpec(
        default="ModalAnalysis",
        type=str,
        help="Result label.",
    ),
}


def run_modal(context: StepContext, step: Step) -> list[StepResult]:
    """Solve the eigenvalue problem against the prepared topology.

    Args:
        context: The run's working state.  ``model`` must already be prepared
            (a ``mesh`` step normally precedes this one); the result is stored
            on ``context.results["modal"]`` for a later spectrum / pushover step.
        step: The step to run.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.MODAL` result whose payload is
        the modal :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...analysis.modal import run_modal_analysis

    params: dict[str, Any] = validate_params("modal", MODAL_PARAMS, step.params)
    result = run_modal_analysis(context.model, n_modes=params["n_modes"], name=params["name"])
    context.results["modal"] = result
    context.log(f"Modal: {params['n_modes']} modes requested")
    return [StepResult(kind=MODAL, label="Modal", payload=result)]
