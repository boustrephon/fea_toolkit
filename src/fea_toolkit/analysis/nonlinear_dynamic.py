"""Nonlinear dynamic (time-history) analysis.

Wraps Tcl export + XaraTclRunner for executing ground-motion-driven
transient analyses.  Requires a preceding :class:`ModalAnalysis` for
Rayleigh damping coefficients.
"""

from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel

import contextlib

from fea_toolkit.analysis.base import (
    _NONLINEAR_DYNAMIC_DEFAULTS,
    Analysis,
    AnalysisResult,
)
from fea_toolkit.analysis.modal import ModalAnalysis


class NonlinearDynamicAnalysis(Analysis):
    """Run a nonlinear dynamic (time-history) analysis.

    Uses the two-stage pipeline: exports the ``MeshModel`` to Tcl
    via :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`,
    appends :func:`~fea_toolkit.opensees.builder.dynamic_time_history_tcl`
    commands, and runs via
    :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.

    Parameters
    ----------
    mesh_model : MeshModel
    modal_result : AnalysisResult
        Result from a preceding :class:`ModalAnalysis` (for periods
        used in Rayleigh damping).
    ground_motion_file : str
        Path to the ground motion file.  One acceleration value
        (m/s²) per line, no header.
    dt : float
        Time step of the ground motion record (s, default 0.005).
    num_steps : int
        Number of analysis steps (default 1000).
    direction : str
        Excitation direction (``"X"``, ``"Y"``, ``"Z"``; default
        ``"X"``).
    damping_ratio : float
        Rayleigh damping ratio (default 0.05).
    name : str, optional
    config : dict, optional
        Builder config overrides.
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        modal_result: AnalysisResult,
        ground_motion_file: str,
        dt: float = 0.005,
        num_steps: int = 1000,
        direction: str = "X",
        damping_ratio: float = 0.05,
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(mesh_model, name, config)
        self._modal_result = modal_result
        self.ground_motion_file = ground_motion_file
        self.dt = dt
        self.num_steps = num_steps
        self.direction = direction
        self.damping_ratio = damping_ratio

    @classmethod
    def defaults(cls) -> dict:
        return dict(_NONLINEAR_DYNAMIC_DEFAULTS)

    @property
    def requires(self) -> list:
        return [ModalAnalysis]

    @property
    def provides(self) -> set:
        return {
            "times",
            "displacements",
            "envelope",
            "peak_drift",
            "converged_steps",
            "gm_file",
            "direction",
            "output_raw",
        }

    def run(self) -> AnalysisResult:
        import os
        import re
        import tempfile

        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.opensees.builder import dynamic_time_history_tcl
        from fea_toolkit.opensees.recorder import (
            XaraTclRunner,
            export_mesh_model_to_tcl,
        )

        mm: MeshModel = self.mesh_model

        # ── Gravity loads from sections/materials ──
        from fea_toolkit.opensees.builder import mesh_model_to_gravity_loads

        gravity_loads = mesh_model_to_gravity_loads(mm)

        # ── Rayleigh damping periods from modal result ──
        modal_data = self._modal_result.data
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
            accel = np.loadtxt(self.ground_motion_file)
        except Exception as e:
            raise ValueError(
                f"Cannot read ground motion file: {self.ground_motion_file}. "
                "Each line should be one acceleration value (m/s²)."
            ) from e

        # ── Create single temporary directory for all outputs ──
        with tempfile.TemporaryDirectory() as tmp_dir:
            gm_file = os.path.join(tmp_dir, "gm_accel.txt")
            np.savetxt(gm_file, accel, fmt="%.8f")

            # ── Generate dynamic Tcl suffix ──
            tcl_suffix = dynamic_time_history_tcl(
                ground_motion_file=gm_file,
                output_prefix=os.path.join(tmp_dir, "dyn"),
                dt=self.dt,
                num_steps=self.num_steps,
                damping=self.damping_ratio,
                period_1=period_1,
                period_2=period_2,
                direction=self.direction,
                gravity_loads=gravity_loads,
            )

            # ── RC config with fiber sections for nonlinear modeling ──
            dyn_config = dict(_NONLINEAR_DYNAMIC_DEFAULTS)
            dyn_config.update(self.config)
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
                    name=self.name,
                    analysis_type="NonlinearDynamicAnalysis",
                    data={
                        "times": np.array([]),
                        "displacements": None,
                        "envelope": None,
                        "peak_drift": 0.0,
                        "converged_steps": 0,
                        "gm_file": self.ground_motion_file,
                        "direction": self.direction,
                        "output_raw": output,
                        "return_code": ret,
                    },
                    metadata={
                        "ground_motion_file": self.ground_motion_file,
                        "direction": self.direction,
                        "num_steps": self.num_steps,
                        "dt": self.dt,
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

            peak_drift = 0.0
            disp_data = None
            env_data = None

            if os.path.exists(disp_file):
                try:
                    disp_data = np.loadtxt(disp_file)
                    if disp_data.ndim > 1 and disp_data.shape[1] >= 2:
                        peak_drift = float(np.max(np.abs(disp_data[:, 1:])))
                except Exception:
                    pass

            if os.path.exists(env_disp_file):
                with contextlib.suppress(Exception):
                    env_data = np.loadtxt(env_disp_file)
            # tmp_dir cleaned up on context exit

        # Derive times from recorded data rows when available
        if disp_data is not None and hasattr(disp_data, "shape") and disp_data.ndim > 0:
            n_rows = disp_data.shape[0]
            times = np.arange(n_rows) * self.dt
        else:
            times = np.arange(self.num_steps) * self.dt

        result = {
            "times": times,
            "displacements": disp_data,
            "envelope": env_data,
            "peak_drift": peak_drift,
            "converged_steps": converged_steps,
            "gm_file": self.ground_motion_file,
            "direction": self.direction,
            "output_raw": output,
        }

        return AnalysisResult(
            name=self.name,
            analysis_type="NonlinearDynamicAnalysis",
            data=result,
            metadata={
                "ground_motion_file": self.ground_motion_file,
                "dt": self.dt,
                "num_steps": self.num_steps,
                "direction": self.direction,
                "damping_ratio": self.damping_ratio,
                "period_1": period_1,
                "period_2": period_2,
                "config": self.config,
            },
        )
