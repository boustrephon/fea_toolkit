"""The ``run_static`` verb — solve the model's static load cases.

Wraps :func:`~fea_toolkit.analysis.linear.run_case_set`, which builds the
OpenSees domain **once** for the whole case set and returns the results as
in-memory archive arrays — nothing is written to disk, so a case view needs no
file.

The per-case raw results are kept on
:attr:`~fea_toolkit.workflow.steps.StepContext.case_results`, which is what a
later :mod:`fea_toolkit.workflow.verbs.combine` step reads to form
combinations.  Splitting solve from reduce is what lets a recipe re-run the
reduction against a different combination set without re-solving.
"""

from typing import Any

from ..config_keys import BUILDER_CONFIG_KEYS
from ..steps import CASES, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["RUN_STATIC_PARAMS", "run_static"]

RUN_STATIC_PARAMS: dict[str, ParamSpec] = {
    "cases": ParamSpec(
        default={},
        type=dict,
        help=(
            "Cases to solve as {case name: {pattern: factor}}, the shape "
            "analysis.list_static_cases() returns.  Empty solves every static "
            "case the model defines."
        ),
    ),
    "config": ParamSpec(
        default={},
        type=dict,
        help=(
            "OpenSees builder overrides — element type, releases, solver settings, "
            "…  Leave a key at its default to let the builder choose."
        ),
        manifest=BUILDER_CONFIG_KEYS,
    ),
}


def run_static(context: StepContext, step: Step) -> list[StepResult]:
    """Solve the requested static cases against the prepared topology.

    Args:
        context: The run's working state.  Its ``model`` must already be
            prepared (a :mod:`fea_toolkit.workflow.verbs.mesh` step normally
            precedes this one); its ``case_results`` accumulates the raw results.
        step: The step to run.  ``params["cases"]`` selects the cases.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.CASES` result whose payload is the
        archive arrays — ``{name: ndarray}`` — ready for a viewer or an NPZ
        write.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...analysis.case_listing import list_static_cases
    from ...analysis.linear import run_case_set

    params: dict[str, Any] = validate_params("run_static", RUN_STATIC_PARAMS, step.params)

    cases = params["cases"] or list_static_cases(context.model_data, context.model)
    case_names = ", ".join(cases)

    raw: dict = {}
    result = run_case_set(
        context.model_data,
        context.model,
        cases,
        config=params["config"] or None,
        raw_out=raw,
        should_cancel=context.cancel,
    )
    context.case_results.update(raw)

    context.log(
        f"Static cases: {len(result['cases'])} solved ({case_names})"
        + (f", {len(result['failed'])} failed: {result['failed']}" if result["failed"] else "")
    )
    if result["cancelled"]:
        context.log("Static cases: run cancelled")
        # Propagate the in-step cancellation so run_recipe marks the run
        # cancelled even when run_static is the final step.
        context.cancelled = True
    return [StepResult(kind=CASES, label="Static cases", payload=result["arrays"])]
