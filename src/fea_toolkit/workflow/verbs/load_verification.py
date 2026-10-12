"""The ``load_verification`` verb — applied loads vs base reactions.

The **load/reaction summary** table: one row per solved static case (applied
loads, summed base reactions, and the signed Δ equilibrium check) plus one row
per response-spectrum direction (CQC base shear and overturning moment).

When a preceding :mod:`~fea_toolkit.workflow.verbs.run_static` step has solved
cases, the verb reads the per-case applied + summed summaries that step stored
on the context and formats them with
:func:`~fea_toolkit.analysis.linear.render_load_reaction_rows` — no second
solve.  Otherwise it falls back to the re-solving
:func:`~fea_toolkit.analysis.linear.analysis_load_reaction_summary` (one row per
load pattern), so the step still works standalone after just a ``mesh``.
"""

from typing import Any

from ..config_keys import BUILDER_CONFIG_KEYS
from ..steps import TABLE, ParamSpec, Step, StepContext, StepResult, Table, validate_params

__all__ = ["LOAD_VERIFICATION_PARAMS", "run_load_verification"]

LOAD_VERIFICATION_PARAMS: dict[str, ParamSpec] = {
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


def _cell(value: Any) -> str:
    """Render one cell as a string — floats to one decimal, ``None`` empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _rs_result_dicts(context: StepContext) -> dict:
    """``{direction: rs_result_dict}`` from the run's response-spectrum results.

    Reads the per-direction results a
    :mod:`~fea_toolkit.workflow.verbs.response_spectrum` step stored on
    ``context.results["rs"]`` and unwraps each ``AnalysisResult`` to its
    ``data["rs_result"]`` dict — the shape
    :func:`~fea_toolkit.analysis.linear.render_load_reaction_rows` reads.
    """
    rs = context.results.get("rs") or {}
    out: dict = {}
    for direction, result in rs.items():
        data = getattr(result, "data", None) or {}
        rs_dict = data.get("rs_result") or {}
        if isinstance(rs_dict, dict) and rs_dict:
            out[str(direction).upper()] = rs_dict
    return out


def run_load_verification(context: StepContext, step: Step) -> list[StepResult]:
    """Render the load/reaction summary across the run's analyses.

    Args:
        context: The run's working state.  ``model_data`` supplies the load
            patterns and unit labels; ``model`` must be the prepared topology
            (a ``mesh`` step normally precedes this one).  When a ``run_static``
            step has run, its per-case ``static_summaries`` and any
            ``response_spectrum`` results are read from ``context.results``.
        step: The step to run; ``params["config"]`` overrides the builder for
            the standalone re-solve fallback.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result whose payload is
        the applied/reaction/Δ comparison — or no result when there is nothing
        to check.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...analysis.linear import analysis_load_reaction_summary, render_load_reaction_rows

    params: dict[str, Any] = validate_params(
        "load_verification", LOAD_VERIFICATION_PARAMS, step.params
    )
    md = context.model_data
    rs_results = _rs_result_dicts(context)

    if not md.load_patterns and not rs_results:
        context.log("Load/reaction summary: the model has no loads to check")
        return []

    static_summaries = context.results.get("static_summaries")
    if static_summaries:
        # Drive from the solve a preceding run_static step already did.
        rows = render_load_reaction_rows(md, static_summaries, rs_results=rs_results or None)
    else:
        # Standalone: re-solve one pattern at a time (folds in any RS that ran).
        rows = analysis_load_reaction_summary(
            md, context.model, config=params["config"] or None, rs_results=rs_results or None
        )

    if not rows:
        context.log("Load/reaction summary: nothing to check")
        return []

    columns = tuple(rows[0].keys())
    table = Table(
        title="Load/reaction summary",
        columns=columns,
        rows=tuple(tuple(_cell(row[column]) for column in columns) for row in rows),
    )
    context.log(f"Load/reaction summary: {len(rows)} analysis row(s)")
    return [StepResult(kind=TABLE, label="Load/reaction summary", payload=table)]
