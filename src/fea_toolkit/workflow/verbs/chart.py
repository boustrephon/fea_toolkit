"""The ``chart`` verb — render a step's results as a figure.

Wraps the ``plotting.report.*`` chart functions: storey displacement and force
profiles (from a solved case's results), response spectra (from the model), and
pushover / modal / capacity-spectrum charts (from the modal and pushover results
a preceding :mod:`~fea_toolkit.workflow.verbs.modal` /
:mod:`~fea_toolkit.workflow.verbs.pushover` step stored on the context).

The figure is always drawn from the run's own results — nothing is re-solved —
so a ``chart`` step is a pure rendering of what a previous step produced.
"""

from typing import Any

from ..steps import FIGURE, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["CHART_PARAMS", "run_chart"]

CHART_PARAMS: dict[str, ParamSpec] = {
    "chart": ParamSpec(
        default="storey_displacements",
        type=str,
        choices=(
            "storey_displacements",
            "storey_forces",
            "response_spectra",
            "pushover",
            "modal",
            "csm",
        ),
        help=(
            "Which chart to draw — 'storey_displacements' / 'storey_forces' "
            "profile a solved case; 'response_spectra' plots the model's parsed "
            "spectra; 'pushover' / 'modal' / 'csm' plot the corresponding "
            "analysis results."
        ),
    ),
    "case": ParamSpec(
        default="",
        type=str,
        help="Solved case to plot.  Empty plots every case the run solved.",
    ),
}


def _run_spectra_chart(context: StepContext, params: dict[str, Any]) -> list[StepResult]:
    """Plot the model's parsed response-spectrum functions — nothing is solved.

    Args:
        context: The run's working state.  ``model_data`` supplies the parsed
            response-spectrum functions to plot.
        params: The step's validated parameters (``chart`` is
            ``"response_spectra"``).

    Returns:
        One :data:`~fea_toolkit.workflow.steps.FIGURE` result whose payload is
        the rendered ``matplotlib.figure.Figure``, or ``None`` when the model
        defines no response-spectrum functions.
    """
    from ...plotting.seismic_spectrum import plot_response_spectra

    functions = getattr(context.model_data, "response_spectrum_functions", None) or {}
    if not functions:
        context.log("chart: the model defines no response-spectrum functions")
        return [StepResult(kind=FIGURE, label="Response spectra", payload=None)]

    figure = plot_response_spectra(functions, units=context.model_data.units)
    context.log(f"Chart: response spectra for {', '.join(functions)}")
    return [StepResult(kind=FIGURE, label="Response spectra", payload=figure)]


def _run_modal_chart(context: StepContext) -> list[StepResult]:
    """Plot modal mass participation from the modal step's result."""
    from ...io.report import modal_participation_df
    from ...plotting.report import plot_modal_participation

    modal = context.results.get("modal")
    if modal is None:
        raise ValueError("chart: 'modal' needs a preceding modal step")
    df = modal_participation_df(modal.data)
    if df is None or df.empty:
        context.log("chart: no modal participation to plot")
        return [StepResult(kind=FIGURE, label="Modal participation", payload=None)]
    figure = plot_modal_participation(df)
    context.log("Chart: modal mass participation")
    return [StepResult(kind=FIGURE, label="Modal participation", payload=figure)]


def _run_pushover_chart(context: StepContext) -> list[StepResult]:
    """Plot the pushover capacity curves from the pushover step's result."""
    from ...plotting.report import plot_pushover_curves

    pushover = context.results.get("pushover")
    if pushover is None:
        raise ValueError("chart: 'pushover' needs a preceding pushover step")
    figure = plot_pushover_curves(pushover.data, units=context.model_data.units)
    context.log("Chart: pushover capacity curves")
    return [StepResult(kind=FIGURE, label="Pushover curves", payload=figure)]


def _run_csm_chart(context: StepContext) -> list[StepResult]:
    """Plot the CSM 4-panel ADRS chart from the modal + pushover results."""
    from ...plotting.report import plot_csm_4panel

    pushover = context.results.get("pushover")
    modal = context.results.get("modal")
    if pushover is None or modal is None:
        raise ValueError("chart: 'csm' needs preceding modal and pushover steps")
    figure = plot_csm_4panel(pushover.data, modal.data, units=context.model_data.units)
    context.log("Chart: capacity spectrum method (4 directions)")
    return [StepResult(kind=FIGURE, label="CSM", payload=figure)]


def _run_storey_forces_chart(
    context: StepContext, params: dict[str, Any], repository: Any, cases: list[str]
) -> list[StepResult]:
    """Plot the solved cases' storey shear and moment profiles.

    Sums each case's frame end forces into per-level global shear and moment —
    rotating the archive's local forces into global coordinates first — then
    hands the resulting table to :func:`plot_storey_forces`, which reconstructs
    the smooth profile from the base row.  Nothing is re-solved.
    """
    import math

    import numpy as np
    import pandas as pd

    from ...model.geometry import get_local_axes
    from ...model.storey_response import build_storey_table, sum_storey_forces
    from ...model.stories import identify_stories
    from ...plotting.report import plot_storey_forces

    md = context.model_data
    if not identify_stories(md):
        raise ValueError("chart: the model has no identifiable storeys to profile")

    nodes = context.model.nodes
    frames = context.model.frame_elements
    node_xyz = {nid: (nd.x, nd.y, nd.z) for nid, nd in nodes.items()}
    is_local = repository.forces_are_local()

    def _global_end(entry: dict, rot, end: str) -> list[float]:
        f = np.array(
            [entry.get(f"fx_{end}", 0.0), entry.get(f"fy_{end}", 0.0), entry.get(f"fz_{end}", 0.0)],
            dtype=float,
        )
        m = np.array(
            [entry.get(f"mx_{end}", 0.0), entry.get(f"my_{end}", 0.0), entry.get(f"mz_{end}", 0.0)],
            dtype=float,
        )
        if rot is not None:
            f = rot @ f
            m = rot @ m
        return [*f.tolist(), *m.tolist()]

    all_shear: dict[str, pd.DataFrame] = {}
    all_moment: dict[str, pd.DataFrame] = {}

    for case in cases:
        forces = repository.element_forces(case)
        if not forces:
            continue
        members = []
        for eid, entry in forces.items():
            fr = frames.get(eid)
            if fr is None:
                continue
            nd_i = nodes.get(fr.node_i)
            nd_j = nodes.get(fr.node_j)
            if nd_i is None or nd_j is None:
                continue
            if abs(nd_j.z - nd_i.z) <= 1e-9:
                continue  # horizontal member — no storey contribution
            axis = np.array([nd_j.x - nd_i.x, nd_j.y - nd_i.y, nd_j.z - nd_i.z], dtype=float)
            if np.linalg.norm(axis) < 1e-12:
                continue
            rot = None
            if is_local:
                vx, vy, vz = get_local_axes(axis, float(getattr(fr, "angle", 0.0)))
                rot = np.vstack([vx, vy, vz]).T  # local -> global

            members.append(
                {
                    "node_i": fr.node_i,
                    "node_j": fr.node_j,
                    "f_i": _global_end(entry, rot, "i"),
                    "f_j": _global_end(entry, rot, "j"),
                }
            )
        if not members:
            continue
        levels = sum_storey_forces(node_xyz, members, mode="cut", z_tolerance=0.5)
        if not levels:
            continue
        shear_rows = []
        moment_rows = []
        for rank, lv in enumerate(levels):
            label = (
                "Base" if rank == 0 else ("Roof" if rank == len(levels) - 1 else f"Level {rank}")
            )
            shear_rows.append(
                {
                    "Storey": label,
                    "Elevation": round(lv["elevation"], 3),
                    case: round(math.hypot(lv["Fx"], lv["Fy"]), 4),
                }
            )
            moment_rows.append(
                {
                    "Storey": label,
                    "Elevation": round(lv["elevation"], 3),
                    case: round(math.hypot(lv["Mx"], lv["My"]), 4),
                }
            )
        all_shear[case] = pd.DataFrame(shear_rows)
        all_moment[case] = pd.DataFrame(moment_rows)

    if not all_shear:
        context.log("chart: no storey force profile to plot")
        return [StepResult(kind=FIGURE, label="Storey forces", payload=None)]

    min_z = min(nd.z for nd in nodes.values())
    df_shear = build_storey_table("Elevation", all_shear, min_z=min_z)
    df_moment = build_storey_table("Elevation", all_moment, min_z=min_z)
    figure = plot_storey_forces(
        df_shear,
        df_moment,
        force_unit=md.units.get("F", "?"),
        moment_unit=f"{md.units.get('F', '?')}·{md.units.get('L', 'm')}",
    )
    context.log(f"Chart: storey force profile for {', '.join(all_shear)}")
    return [StepResult(kind=FIGURE, label="Storey forces", payload=figure)]


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
        ValueError: If a parameter is not declared by this verb, or the model
            has no identifiable storeys.
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

    # The spectra chart reads the model directly; it needs no solved case.
    if params["chart"] == "response_spectra":
        return _run_spectra_chart(context, params)
    # The analysis-result charts read the modal / pushover results a preceding
    # verb stored on the context; they need no solved case either.
    if params["chart"] == "modal":
        return _run_modal_chart(context)
    if params["chart"] == "pushover":
        return _run_pushover_chart(context)
    if params["chart"] == "csm":
        return _run_csm_chart(context)

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

    if params["chart"] == "storey_forces":
        return _run_storey_forces_chart(context, params, repository, cases)

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
