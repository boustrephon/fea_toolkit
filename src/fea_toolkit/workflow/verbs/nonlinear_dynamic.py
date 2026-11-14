"""The ``nonlinear_dynamic`` verb — ground-motion-driven time-history analysis.

Wraps :func:`~fea_toolkit.analysis.nonlinear_dynamic.run_nonlinear_dynamic_analysis`,
which requires the modal result a preceding
:mod:`~fea_toolkit.workflow.verbs.modal` step stored on the context (its first
two periods set the Rayleigh damping).  This is the mission's "pathway to
nonlinear dynamic analysis": the same member-yielding build as the pushover verb
(``dispBeamColumn`` fibre sections), driven by a ground-motion record instead of
a static lateral pattern.

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..config_keys import BUILDER_CONFIG_KEYS
from ..steps import NLD, ParamSpec, Step, StepContext, StepResult, validate_params
from ._tcl import EXPORT_TCL_PARAMS, maybe_export_tcl
from .modal import resolve_modal_result

__all__ = ["NLD_PARAMS", "run_nonlinear_dynamic"]

NLD_PARAMS: dict[str, ParamSpec] = {
    "ground_motion_file": ParamSpec(
        default="",
        type=str,
        help=(
            "Path to the ground-motion record — one acceleration value per line, "
            "no header, in model acceleration units (length/s²)."
        ),
    ),
    "dt": ParamSpec(
        default=0.005,
        type=float,
        help="Integration time step (s).",
    ),
    "num_steps": ParamSpec(
        default=1000,
        type=int,
        help="Number of integration steps.",
    ),
    "direction": ParamSpec(
        default="X",
        type=str,
        choices=("X", "Y", "Z"),
        help="Excitation direction.",
    ),
    "damping_ratio": ParamSpec(
        default=0.05,
        type=float,
        help="Rayleigh damping ratio.",
    ),
    "gravity_patterns": ParamSpec(
        default={},
        type=dict,
        help="Gravity load patterns → scale factor, applied before the transient.",
    ),
    "modal_case": ParamSpec(
        default="",
        type=str,
        help=(
            "Name of the modal step whose periods set the Rayleigh damping.  "
            "Leave empty to use the only modal result present."
        ),
    ),
    "name": ParamSpec(
        default="NonlinearDynamic",
        type=str,
        help="Result label.",
    ),
    "config": ParamSpec(
        default={},
        type=dict,
        help="OpenSees builder overrides, merged over the fibre-section defaults.",
        manifest=BUILDER_CONFIG_KEYS,
    ),
    **EXPORT_TCL_PARAMS,
}


def run_nonlinear_dynamic(context: StepContext, step: Step) -> list[StepResult]:
    """Run a nonlinear dynamic (time-history) analysis of a ground motion.

    Args:
        context: The run's working state.  ``model`` and ``results["modal"]``
            must already be present (``mesh`` then ``modal`` steps precede this).
            The modal result is chosen by the step's ``modal_case`` parameter.
        step: The step to run.  ``params`` supplies the record path, step size,
            direction and the modal result whose periods set the damping.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.NLD` result whose payload is the
        transient :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared, or no ground-motion file is
            supplied.
        RuntimeError: If the solve fails, the run does not converge to the end
            of the requested steps, or the process cannot import ``openseespy``
            (there is no out-of-process delegate for this verb yet).
    """
    from ...opensees.runtime import opensees_available

    params: dict[str, Any] = validate_params("nonlinear_dynamic", NLD_PARAMS, step.params)
    maybe_export_tcl(context, "nonlinear_dynamic", params)

    if not params["ground_motion_file"]:
        raise ValueError(
            "nonlinear_dynamic needs a 'ground_motion_file' — the path to a "
            "record (one acceleration value per line)"
        )

    modal_result = resolve_modal_result(context, params["modal_case"])

    if not opensees_available():
        raise RuntimeError(
            "nonlinear_dynamic: the out-of-process OpenSees runner does not "
            "support time-history analysis yet — run with openseespy installed."
        )

    from ...analysis.nonlinear_dynamic import run_nonlinear_dynamic_analysis

    result = run_nonlinear_dynamic_analysis(
        context.model,
        modal_result,
        ground_motion_file=params["ground_motion_file"],
        dt=params["dt"],
        num_steps=params["num_steps"],
        direction=params["direction"],
        damping_ratio=params["damping_ratio"],
        name=params["name"],
        config=params["config"] or None,
        gravity_patterns=params["gravity_patterns"] or None,
    )
    # The runner returns a partial result on non-convergence rather than
    # raising, so signal the failure here before storing it — the completion log
    # below must describe only a fully converged solve.
    data = getattr(result, "data", {}) or {}
    converged = data.get("converged_steps")
    requested = params["num_steps"]
    if converged is not None and int(converged) < int(requested):
        raise RuntimeError(
            f"nonlinear_dynamic: non-convergence — converged {converged} of {requested} steps"
        )
    context.results["nonlinear_dynamic"] = result
    context.log(f"Nonlinear dynamic ({params['direction']}): complete")
    return [
        StepResult(kind=NLD, label=f"Nonlinear dynamic ({params['direction']})", payload=result)
    ]
