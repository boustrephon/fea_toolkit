"""The ``chart`` verb — render a solved case's storey response as a figure.

Wraps :func:`~fea_toolkit.plotting.report.plot_storey_displacements`, the one
``plotting.report.*`` chart the current results plumbing can feed: it reads
**nodal displacements**, which a solved case already carries in the results
archive.  The storey *force* profile needs base reactions, and the pushover /
modal / capacity-spectrum charts need their own results — none of which a
recipe has solved yet, so they arrive with P32's analysis verbs.

The figure is drawn from the run's own results — nothing is re-solved — so a
``chart`` step is a pure rendering of what a previous :mod:`run_static` or
:mod:`combine` step produced.
"""

from typing import Any

from ..steps import FIGURE, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["CHART_PARAMS", "run_chart"]

CHART_PARAMS: dict[str, ParamSpec] = {
    "chart": ParamSpec(
        default="storey_displacements",
        type=str,
        choices=("storey_displacements",),
        help=(
            "Which chart to draw — 'storey_displacements' is the storey "
            "displacement / drift profile."
        ),
    ),
    "case": ParamSpec(
        default="",
        type=str,
        help="Solved case to plot.  Empty plots every case the run solved.",
    ),
}


def run_chart(context: StepContext, step: Step) -> list[StepResult]:
    """Plot the solved cases' storey displacement and drift profiles.

    Args:
        context: The run's working state.  ``case_results`` must hold the cases
            a previous analysis step produced; the figure is assembled from
            those, never by re-solving.
        step: The step to run.  ``params["chart"]`` selects the chart and
            ``params["case"]`` a single case (empty for all).

    Returns:
        One :data:`~fea_toolkit.workflow.steps.FIGURE` result whose payload is a
        ``matplotlib.figure.Figure``, or ``None`` when the model has no storey
        response to draw.

    Raises:
        ValueError: If a parameter is not declared by this verb, no case has
            been solved yet, or the model has no identifiable storeys.
    """
    import pandas as pd

    from ...io.npz_writer import results_arrays
    from ...io.results_repository import NpzResultsRepository
    from ...model.storey_response import (
        build_storey_table,
        storey_displacements,
        storey_drifts,
    )
    from ...model.stories import identify_stories
    from ...plotting.report import plot_storey_displacements

    params: dict[str, Any] = validate_params("chart", CHART_PARAMS, step.params)
    if not context.case_results:
        raise ValueError("chart needs solved cases — run a 'run_static' step first")

    # Rebuild the archive from the raw results, exactly as ``combine`` does, so
    # the chart reads the same object a results view would.
    arrays = results_arrays(
        context.model_data,
        static_results=context.case_results,
        mesh_model=context.model,
    )
    repository = NpzResultsRepository(arrays)
    cases = [params["case"]] if params["case"] else repository.cases()
    if not cases:
        raise ValueError("chart has no solved case to plot")

    stories = identify_stories(context.model_data)
    if not stories:
        raise ValueError("chart: the model has no identifiable storeys to profile")

    md = context.model_data
    min_z = min(node.z for node in md.nodes.values())
    all_disp: dict[str, pd.DataFrame] = {}
    all_drift: dict[str, pd.DataFrame] = {}

    for case in cases:
        displacements = repository.nodal_displacements(case)
        if not displacements:
            continue
        node_ux = {node_id: float(d[0]) for node_id, d in displacements.items()}
        node_uy = {node_id: float(d[1]) for node_id, d in displacements.items()}
        df_disp = storey_displacements(md, stories, node_ux, node_uy)
        if df_disp.empty:
            continue
        all_disp[case] = pd.DataFrame(
            [
                {
                    "Storey": row["Storey"],
                    "Elevation": row["Elevation"],
                    case: round(float(row["Peak_disp"]), 4),
                }
                for row in df_disp.to_dict("records")
            ]
        )
        df_drift = storey_drifts(df_disp, stories)
        if not df_drift.empty:
            all_drift[case] = pd.DataFrame(
                [
                    {
                        "Storey": row["Storey"],
                        "Elevation (m)": row["Elevation (m)"],
                        case: round(float(row["Drift_peak"]), 6),
                    }
                    for row in df_drift.to_dict("records")
                ]
            )

    if not all_disp:
        context.log("chart: no storey displacement to plot")
        return [StepResult(kind=FIGURE, label="Storey displacements", payload=None)]

    df_disp = build_storey_table("Elevation", all_disp, min_z=min_z)
    df_drift = build_storey_table("Elevation (m)", all_drift, min_z=min_z)
    figure = plot_storey_displacements(
        df_disp,
        df_drift,
        source_length_unit=md.units.get("L", "m"),
    )
    context.log(f"Chart: storey displacement profile for {', '.join(all_disp)}")
    return [StepResult(kind=FIGURE, label="Storey displacements", payload=figure)]
