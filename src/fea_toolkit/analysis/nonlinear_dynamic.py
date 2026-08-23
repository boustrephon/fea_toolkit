"""Nonlinear dynamic (time-history) analysis.

Runs a ground-motion-driven transient analysis through the Tcl export +
:class:`~fea_toolkit.opensees.recorder.XaraTclRunner` backend.  Requires
the result of :func:`~fea_toolkit.analysis.modal.run_modal_analysis` for
Rayleigh damping periods.
"""

from typing import TYPE_CHECKING, Optional

import numpy as np

from fea_toolkit.analysis.base import (
    _NONLINEAR_DYNAMIC_DEFAULTS,
    AnalysisResult,
)

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

    Uses the two-stage pipeline: exports the ``MeshModel`` to Tcl via
    :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`,
    appends :func:`~fea_toolkit.opensees.builder.dynamic_time_history_tcl`
    commands, and runs via
    :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.

    Args:
        mesh_model: Pre-processed topology.
        modal_result: Result from a preceding :func:`run_modal_analysis`
            (for periods used in Rayleigh damping).
        ground_motion_file: Path to the ground motion file.  One
            acceleration value per line, no header, authored in **model
            acceleration units** (length-unit per s^2).  The Tcl path
            applies values with ``-factor 1.0`` — no g-division is
            performed.
        dt: Time step of the ground motion record (s, default 0.005).
        num_steps: Number of analysis steps (default 1000).
        direction: Excitation direction ("X", "Y", "Z"; default "X").
        damping_ratio: Rayleigh damping ratio (default 0.05).
        name: Result label (default "NonlinearDynamic").
        config: Builder config overrides.

    Returns:
        :class:`AnalysisResult` holding time-history results
        (times, displacements, envelope, peak displacement).  Output-file
        parse failures (``np.loadtxt`` on the displacement / envelope
        recorders) are surfaced under ``metadata["parse_error"]`` (``None``
        when all recorder outputs parse cleanly) instead of being silently
        dropped.
    """
    import os
    import re
    import tempfile

    from fea_toolkit.opensees.builder import (
        dynamic_time_history_tcl,
        mesh_model_to_gravity_loads,
    )
    from fea_toolkit.opensees.recorder import (
        XaraTclRunner,
        export_mesh_model_to_tcl,
    )

    mm = mesh_model

    # ── Gravity loads from sections/materials ──
    gravity_loads = mesh_model_to_gravity_loads(mm)

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

    # ── Load ground motion ──
    try:
        accel = np.loadtxt(ground_motion_file)
    except Exception as e:
        raise ValueError(
            f"Cannot read ground motion file: {ground_motion_file}. "
            "Each line should be one acceleration value in model "
            "acceleration units."
        ) from e

    # ── Create single temporary directory for all outputs ──
    with tempfile.TemporaryDirectory() as tmp_dir:
        gm_file = os.path.join(tmp_dir, "gm_accel.txt")
        np.savetxt(gm_file, accel, fmt="%.8f")

        # ── Generate dynamic Tcl suffix ──
        tcl_suffix = dynamic_time_history_tcl(
            ground_motion_file=gm_file,
            output_prefix=os.path.join(tmp_dir, "dyn"),
            dt=dt,
            num_steps=num_steps,
            damping=damping_ratio,
            period_1=period_1,
            period_2=period_2,
            direction=direction,
            gravity_loads=gravity_loads,
        )

        # ── RC config with fiber sections for nonlinear modeling ──
        dyn_config = dict(_NONLINEAR_DYNAMIC_DEFAULTS)
        dyn_config.update(config or {})
        dyn_config["create_fiber_sections"] = True

        # ── Write Tcl inside tmp_dir — recorder outputs resolve here ──
        tcl_path = os.path.join(tmp_dir, "run_dyn.tcl")
        export_mesh_model_to_tcl(
            mm,
            tcl_path,
            config=dyn_config,
            tcl_suffix=tcl_suffix,
        )

        runner = XaraTclRunner()
        ret, output = runner.run(tcl_path)

        # ── Check return status ──
        if ret != 0:
            return AnalysisResult(
                name=name,
                analysis_type="NonlinearDynamicAnalysis",
                data={
                    "times": np.array([]),
                    "displacements": None,
                    "envelope": None,
                    "peak_displacement": 0.0,
                    "converged_steps": 0,
                    "gm_file": ground_motion_file,
                    "direction": direction,
                    "output_raw": output,
                    "return_code": ret,
                },
                metadata={
                    "ground_motion_file": ground_motion_file,
                    "direction": direction,
                    "num_steps": num_steps,
                    "dt": dt,
                    "damping_ratio": damping_ratio,
                    "period_1": period_1,
                    "period_2": period_2,
                    "config": config,
                    "error": f"XaraTclRunner returned status {ret}",
                },
            )

        # ── Parse output ──
        converged_steps = 0
        for line in output.splitlines():
            if "complete:" in line and "steps converged" in line:
                m = re.search(r"(\d+) steps converged", line)
                if m:
                    converged_steps = int(m.group(1))
                    break

        # Try to read output files
        disp_file = os.path.join(tmp_dir, "dyn_disp.out")
        env_disp_file = os.path.join(tmp_dir, "dyn_env_disp.out")

        peak_displacement = 0.0
        disp_data = None
        env_data = None
        parse_errors = []

        if os.path.exists(disp_file):
            try:
                disp_data = np.loadtxt(disp_file)
                if disp_data.ndim > 1 and disp_data.shape[1] >= 2:
                    peak_displacement = float(np.max(np.abs(disp_data[:, 1:])))
            except Exception as e:
                parse_errors.append(f"displacement output ({disp_file}): {e}")

        if os.path.exists(env_disp_file):
            try:
                env_data = np.loadtxt(env_disp_file)
            except Exception as e:
                parse_errors.append(f"envelope output ({env_disp_file}): {e}")
        # tmp_dir cleaned up on context exit

    # Derive times from recorded data rows when available
    if disp_data is not None and hasattr(disp_data, "shape") and disp_data.ndim > 0:
        n_rows = disp_data.shape[0]
        times = np.arange(n_rows) * dt
    else:
        times = np.arange(num_steps) * dt

    result = {
        "times": times,
        "displacements": disp_data,
        "envelope": env_data,
        "peak_displacement": peak_displacement,
        "converged_steps": converged_steps,
        "gm_file": ground_motion_file,
        "direction": direction,
        "output_raw": output,
        "return_code": ret,
    }

    return AnalysisResult(
        name=name,
        analysis_type="NonlinearDynamicAnalysis",
        data=result,
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
            "parse_error": "; ".join(parse_errors) if parse_errors else None,
        },
    )
