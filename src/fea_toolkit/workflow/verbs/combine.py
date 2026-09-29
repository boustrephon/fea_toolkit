"""The ``combine`` verb — reduce solved cases into load combinations.

Wraps
:func:`~fea_toolkit.analysis.combinations.build_combination_results`: it reads
the cases a previous :mod:`fea_toolkit.workflow.verbs.run_static` step solved
and expands the requested combinations — a linear factor expansion where the
factors are linear, an operator (SRSS, Envelope) where they are not.

Keeping reduction as its own verb is what makes the two re-runnable apart: a
different combination set, or a different envelope strategy, is a re-run of
this step alone against the cases already solved.

The **raw** composite payloads stay on
:attr:`~fea_toolkit.workflow.steps.StepContext.case_results` — that is what a
later reduction reads.  The *result* this verb returns is the in-memory results
archive those composites assemble into, the same shape
:mod:`fea_toolkit.workflow.verbs.run_static` returns, so a viewer or an NPZ
write treats a solve and a reduction alike and never has to know which verb
produced a case.
"""

from typing import Any

from ..steps import CASES, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["COMBINE_PARAMS", "run_combine"]

COMBINE_PARAMS: dict[str, ParamSpec] = {
    "combinations": ParamSpec(
        default=[],
        type=list,
        help=("Combination names to expand.  Empty expands every combination the model defines."),
    ),
    "envelope_mode": ParamSpec(
        default="maxmin",
        type=str,
        choices=("maxmin", "per_path"),
        help=(
            "Envelope strategy — 'maxmin' for one composite of per-quantity "
            "maximum/minimum, 'per_path' for one composite per branch."
        ),
    ),
}


def run_combine(context: StepContext, step: Step) -> list[StepResult]:
    """Expand load combinations from the cases solved so far.

    Args:
        context: The run's working state.  ``case_results`` must hold the cases
            a previous analysis step produced; the composites are merged back
            into it.
        step: The step to run.  ``params`` selects the combinations and the
            envelope strategy.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.CASES` result whose payload is the
        in-memory results archive — ``{array name: ndarray}``, keyed
        ``static/<combination>/<quantity>`` — so a viewer serves the composites
        from the same :class:`~fea_toolkit.io.results_repository.ResultsRepository`
        a solved case uses, and needs neither the model nor a file.  It carries no
        case when the model defines no combination the step can form.

    Raises:
        ValueError: If a parameter is not declared by this verb, no case has
            been solved yet, or (from the reducer) a requested combination is
            unknown or references a case that was not solved.
    """
    from ...analysis.combinations import build_combination_results
    from ...io.npz_writer import results_arrays

    params: dict[str, Any] = validate_params("combine", COMBINE_PARAMS, step.params)
    if not context.case_results:
        raise ValueError("combine needs solved cases — run a 'run_static' step first")

    combined, case_meta = build_combination_results(
        context.case_results,
        model=context.model_data,
        combinations=params["combinations"] or None,
        envelope_mode=params["envelope_mode"],
        return_meta=True,
    )
    context.case_results.update(combined)

    mode = params["envelope_mode"]
    context.log(
        f"Combinations ({mode}): {len(combined)} formed"
        + (f" — {', '.join(sorted(combined))}" if combined else " (none defined)")
    )
    # The *result* is the archive, not the raw payloads: `results_arrays` is the
    # one place an archive is assembled (`npz_writer`), so a combination view is
    # byte-for-byte the same kind of object as a solved case's — see §3.7.
    arrays = results_arrays(
        context.model_data,
        static_results=combined,
        mesh_model=context.model,
        case_meta=case_meta,
    )
    return [StepResult(kind=CASES, label=f"Combinations ({mode})", payload=arrays)]
