"""Nonlinear dynamic (time-history) analysis.

Runs a ground-motion-driven transient analysis in-process through the native
Newmark runner
(:meth:`fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_transient_analysis`).
Requires the result of :func:`~fea_toolkit.analysis.modal.run_modal_analysis`
for Rayleigh damping periods.
"""

from typing import TYPE_CHECKING, Optional

import numpy as np

from .base import AnalysisResult

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


def run_nonlinear_dynamic_analysis(
    mesh_model: "MeshModel",
    modal_result: AnalysisResult,
    ground_motion_file: str,
    dt: float = 0.005,
    num_steps: int = 1000,
    direction: str = "X",
    damping_ratio: float = 0.05,
    name: str = "NonlinearDynamic",
    config: Optional[dict] = None,
) -> AnalysisResult:
    """Run a nonlinear dynamic (time-history) analysis.

    Builds the OpenSees domain from *mesh_model* and integrates a ground motion
    with the native Newmark runner (no Tcl/Xara export).  The domain is built
    as the config's ``element_type`` specifies; for genuinely nonlinear
    (fiber / plastic-hinge) response the caller rebuilds with fiber sections
    first, exactly as :func:`run_pushover_analysis` does.

    Args:
        mesh_model: Pre-processed topology.
        modal_result: Result from a preceding :func:`run_modal_analysis`
            (for periods used in Rayleigh damping).
        ground_motion_file: Path to the ground motion file.  One acceleration
            value per line, no header, in **model acceleration units**
            (length / s²).
        dt: Time step (s, default 0.005).
        num_steps: Number of integration steps (default 1000).
        direction: Excitation direction ("X", "Y", "Z"; default "X").
        damping_ratio: Rayleigh damping ratio (default 0.05).
        name: Result label (default "NonlinearDynamic").
        config: Builder config overrides.

    Returns:
        :class:`AnalysisResult` with ``times``, ``displacements``
        (``{node_tag: (n_steps, 3) ndarray}``), ``peak_displacement`` and
        ``converged_steps``.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    # ── Ground motion (one acceleration value per line) ──
    try:
        accel = np.loadtxt(ground_motion_file)
    except Exception as e:
        raise ValueError(
            f"Cannot read ground motion file: {ground_motion_file}. "
            "Each line should be one acceleration value in model "
            "acceleration units."
        ) from e

    # ── Rayleigh damping periods from modal result ──
    modal_data = modal_result.data
    if not isinstance(modal_data, dict):
        modal_data = {"modal": modal_data}
    modal = modal_data.get("modal", modal_data)
    periods = modal.get("periods", [])

    if len(periods) >= 2:
        period_1 = float(periods[0])
        period_2 = float(periods[-1])
    elif len(periods) == 1:
        period_1 = float(periods[0])
        period_2 = float(periods[0]) * 0.5
    else:
        period_1 = 0.2
        period_2 = 2.0

    # ── Build + run the native transient ──
    builder = AnalysisBuilder(mesh_model, config or {})
    builder.build_domain()
    res = builder.run_transient_analysis(
        accel,
        dt,
        direction=direction,
        damping_ratio=damping_ratio,
        period_1=period_1,
        period_2=period_2,
        num_steps=num_steps,
    )

    return AnalysisResult(
        name=name,
        analysis_type="NonlinearDynamicAnalysis",
        data={
            "times": res["times"],
            "displacements": res["displacements"],
            "envelope": None,
            "peak_displacement": res["peak_displacement"],
            "converged_steps": res["converged_steps"],
            "gm_file": ground_motion_file,
            "direction": direction,
        },
        metadata={
            "ground_motion_file": ground_motion_file,
            "dt": dt,
            "num_steps": num_steps,
            "direction": direction,
            "damping_ratio": damping_ratio,
            "period_1": period_1,
            "period_2": period_2,
            "config": config,
            "error": None,
        },
    )
