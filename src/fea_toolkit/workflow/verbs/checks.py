"""The model-check verbs — pre-analysis checks, as ``table`` steps.

These wrap :mod:`fea_toolkit.model.checks`, which works on the parsed model
alone: no OpenSees domain, no results.  That is why they can run *before* a
solve, and why they are the first ``table`` steps — a chart needs results, a
model check does not.

**Why three verbs rather than one `check` verb with a "which check" parameter.**
The checks take different arguments (a tolerance, a load-totals mapping, brace
ids and a K factor).  One verb with a `choices` selector would have to declare
every check's parameters and leave most of them inapplicable to most runs — a
parameter surface that *lies*.  Separate verbs keep each surface flat and true,
and adding a check is adding a verb.

Each verb keeps its heavy import inside the function, so importing the manifest
still does not load OpenSees.
"""

from typing import Any

from ..steps import (
    TABLE,
    ParamSpec,
    Step,
    StepContext,
    StepResult,
    Table,
    validate_params,
)

__all__ = [
    "BRACE_BUCKLING_PARAMS",
    "CONNECTIVITY_PARAMS",
    "SELF_WEIGHT_PARAMS",
    "run_check_brace_buckling",
    "run_check_connectivity",
    "run_check_self_weight",
]

CONNECTIVITY_PARAMS: dict[str, ParamSpec] = {
    "tol": ParamSpec(
        default=1e-6,
        type=float,
        help="Coordinate tolerance for duplicate-node detection, in model length units.",
    ),
}

SELF_WEIGHT_PARAMS: dict[str, ParamSpec] = {
    "atol": ParamSpec(
        default=0.0,
        type=float,
        help="Absolute tolerance for the comparison; 0.0 lets the check use 1% of expected.",
    ),
    "verbose": ParamSpec(
        default=False,
        type=bool,
        help="Print the check's own summary to stdout as well as returning it.",
    ),
}

BRACE_BUCKLING_PARAMS: dict[str, ParamSpec] = {
    "brace_ids": ParamSpec(
        default=[],
        type=list,
        help="Element ids to check.  Empty uses the step's selection, or checks nothing.",
    ),
    "K": ParamSpec(
        default=1.0,
        type=float,
        help="Effective length factor (1.0 = pinned-pinned).",
    ),
    "print_results": ParamSpec(
        default=False,
        type=bool,
        help="Print the check's own table to stdout as well as returning it.",
    ),
}


def _pairs(title: str, mapping: dict) -> Table:
    """A two-column ``check / value`` table from an ordered mapping.

    Args:
        title: The table's title.
        mapping: The rows, in order.

    Returns:
        The table, every value rendered as text.
    """
    return Table(
        title=title,
        columns=("check", "value"),
        rows=tuple((str(name), str(value)) for name, value in mapping.items()),
    )


def run_check_connectivity(context: StepContext, step: Step) -> list[StepResult]:
    """Check the model for the faults that make a stiffness matrix singular.

    Wraps :func:`fea_toolkit.model.checks.check_model_connectivity`.

    Args:
        context: The run's working state.  ``model_data`` is the model checked —
            the copy an earlier step may have edited, not the caller's original.
        step: The step to run; ``params`` carries the tolerance.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result counting each fault
        the check looks for.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...model.checks import check_model_connectivity

    params: dict[str, Any] = validate_params("check_connectivity", CONNECTIVITY_PARAMS, step.params)
    result = check_model_connectivity(context.model_data, tol=params["tol"])

    table = _pairs(
        "Connectivity",
        {
            "orphan nodes": len(result.get("orphan_nodes") or []),
            "shell-only base nodes": len(result.get("shell_only_base_nodes") or []),
            "duplicate coordinates": len(result.get("duplicate_coords") or []),
            "zero-area sections": len(result.get("zero_area_sections") or []),
            "summary": result.get("summary", ""),
        },
    )
    context.log(f"Connectivity: {table.rows[-1][1]}")
    return [StepResult(kind=TABLE, label="Connectivity", payload=table)]


def run_check_self_weight(context: StepContext, step: Step) -> list[StepResult]:
    """Compare the model's self-weight against the weight its geometry implies.

    Wraps :func:`fea_toolkit.model.checks.check_self_weight_consistency`.

    Args:
        context: The run's working state.  ``model_data`` supplies the geometry.
        step: The step to run; ``params`` carries the tolerance and verbosity.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result: the expected and
        applied totals, their discrepancy, the tolerance and the verdict.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...model.checks import check_self_weight_consistency

    params: dict[str, Any] = validate_params("check_self_weight", SELF_WEIGHT_PARAMS, step.params)
    result = check_self_weight_consistency(
        context.model_data,
        atol=params["atol"] or None,
        verbose=params["verbose"],
    )

    table = _pairs(
        "Self-weight",
        {
            "expected": result.get("expected"),
            "applied": result.get("applied"),
            "discrepancy": result.get("discrepancy"),
            "tolerance": result.get("tolerance"),
            "passed": result.get("passed"),
        },
    )
    context.log(f"Self-weight: passed={result.get('passed')}")
    return [StepResult(kind=TABLE, label="Self-weight", payload=table)]


def run_check_brace_buckling(context: StepContext, step: Step) -> list[StepResult]:
    """Check the named braces against Euler buckling.

    Wraps :func:`fea_toolkit.model.checks.check_brace_buckling`.

    Args:
        context: The run's working state.  ``model_data`` supplies the sections.
        step: The step to run.  ``selection`` narrows the braces when
            ``brace_ids`` is empty, so a check can be scoped like any other step;
            ``params`` carries the K factor and the printing flag.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result — one row per brace,
        holding whatever quantities the check reported for it.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...model.checks import check_brace_buckling

    params: dict[str, Any] = validate_params(
        "check_brace_buckling", BRACE_BUCKLING_PARAMS, step.params
    )
    brace_ids = set(params["brace_ids"])
    if not brace_ids and step.selection is not None:
        # The step's selection scopes the check exactly as it scopes any other
        # step, so a recipe does not have to name ids a second way.
        brace_ids = set(step.selection.get_frame_ids(context.model_data))

    result = check_brace_buckling(
        context.model_data,
        brace_ids=brace_ids,
        K=params["K"],
        print_results=params["print_results"],
    )
    first = next(iter(result.values())) if result else {}
    columns = ("element", *first.keys())
    rows = tuple(
        (str(elem_id), *(str(value) for value in values.values()))
        for elem_id, values in result.items()
    )
    table = Table(title="Brace buckling", columns=columns, rows=rows)
    context.log(f"Brace buckling: {len(rows)} brace(s) checked")
    return [StepResult(kind=TABLE, label="Brace buckling", payload=table)]
