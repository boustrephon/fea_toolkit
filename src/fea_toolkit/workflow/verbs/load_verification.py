"""The ``load_verification`` verb — applied loads vs base reactions.

Wraps :func:`~fea_toolkit.analysis.linear.static_load_verification`, which builds
the OpenSees domain once, applies every load pattern, runs a static solve per
pattern, and compares the summed applied loads against the summed base reactions.
The difference Δ should be near zero, confirming equilibrium — the same check the
report's "Static load verification" table presents, surfaced as a ``table`` step
so the GUI and a report share one computation.
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
    """Render one cell as a string — floats to one decimal, everything else verbatim.

    :func:`~fea_toolkit.analysis.linear.static_load_verification` already rounds
    the applied/reaction/Δ values to one decimal place, so a fixed ``.1f`` keeps
    that precision without scientific notation for large force totals.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def run_load_verification(context: StepContext, step: Step) -> list[StepResult]:
    """Compare each load pattern's applied loads against its base reactions.

    Args:
        context: The run's working state.  ``model_data`` supplies the load
            patterns; ``model`` must be the prepared topology (a ``mesh`` step
            normally precedes this one).
        step: The step to run; ``params["config"]`` overrides the builder.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result whose payload is the
        applied/reaction/Δ comparison, one row per load pattern — or no result
        when the model defines no load patterns.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...analysis.linear import static_load_verification

    params: dict[str, Any] = validate_params(
        "load_verification", LOAD_VERIFICATION_PARAMS, step.params
    )
    if not context.model_data.load_patterns:
        context.log("Load verification: the model defines no load patterns — nothing to check")
        return []

    frame = static_load_verification(context.model_data, context.model, params["config"] or None)

    rows = tuple(
        tuple(_cell(cell) for cell in row) for row in frame.itertuples(index=False, name=None)
    )
    table = Table(
        title="Load verification",
        columns=tuple(str(column) for column in frame.columns),
        rows=rows,
    )
    context.log(f"Load verification: {len(frame)} load pattern(s) checked")
    return [StepResult(kind=TABLE, label="Load verification", payload=table)]
