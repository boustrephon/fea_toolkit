"""The ``ida`` verb — incremental dynamic analysis.

Wraps :func:`~fea_toolkit.analysis.ida.run_ida`, which re-runs the
:mod:`~fea_toolkit.workflow.verbs.nonlinear_dynamic` runner over a record
intensity ladder and reports the collapse intensity.  Requires the modal result
a preceding :mod:`~fea_toolkit.workflow.verbs.modal` step stored on the context.

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..config_keys import BUILDER_CONFIG_KEYS
from ..steps import IDA, ParamSpec, Step, StepContext, StepResult, validate_params
from ._tcl import EXPORT_TCL_PARAMS, maybe_export_tcl
from .modal import resolve_modal_result

__all__ = ["IDA_PARAMS", "run_ida"]

IDA_PARAMS: dict[str, ParamSpec] = {
    "ground_motion_file": ParamSpec(
        default="",
        type=str,
        help=(
            "Path to the ground-motion record — one acceleration value per line, "
            "no header, in model acceleration units (length/s²)."
        ),
    ),
    "intensities": ParamSpec(
        default=[0.5, 1.0, 1.5, 2.0],
        type=list,
        help="Ascending record scale factors — the intensity (IM) ladder.",
    ),
    "dt": ParamSpec(
        default=0.005,
        type=float,
        help="Integration time step (s).",
    ),
    "num_steps": ParamSpec(
        default=1000,
        type=int,
        help="Number of integration steps per rung.",
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
    "collapse_drift": ParamSpec(
        default=0.10,
        type=float,
        help="Peak interstorey-drift ratio above which the structure is deemed collapsed.",
    ),
    "tolerance": ParamSpec(
        default=0.01,
        type=float,
        help="Bisection relative tolerance on the collapse intensity.",
    ),
    "max_scale": ParamSpec(
        default=None,
        type=float,
        allow_none=True,
        help="Optional upper bound for the geometric ladder extension.",
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
        default="IDA",
        type=str,
        help="Result label.",
    ),
    "export_path": ParamSpec(
        default="",
        type=str,
        help="Optional NPZ path to persist the IDA result for standalone plotting.",
    ),
    "config": ParamSpec(
        default={},
        type=dict,
        help="OpenSees builder overrides, merged over the fibre-section defaults.",
        manifest=BUILDER_CONFIG_KEYS,
    ),
    **EXPORT_TCL_PARAMS,
}


def run_ida(context: StepContext, step: Step) -> list[StepResult]:
    """Run an incremental dynamic analysis of a ground motion.

    Args:
        context: The run's working state.  ``model`` and ``results["modal"]``
            must already be present (``mesh`` then ``modal`` steps precede this).
            The modal result is chosen by the step's ``modal_case`` parameter.
        step: The step to run.  ``params`` supplies the record path, the IM
            ladder, the collapse threshold and the modal result whose periods
            set the damping.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.IDA` result whose payload is the
        :func:`~fea_toolkit.analysis.ida.run_ida` result dict (the intensity
        ladder, per-rung drift ratios and convergence, and the collapse
        intensity / criterion / bisection bracket).

    Raises:
        ValueError: If a parameter is not declared, or no ground-motion file or
            an empty intensity ladder is supplied.
        RuntimeError: If the solve fails, or the process cannot import
            ``openseespy`` (there is no out-of-process delegate for this verb yet).
    """
    from ...opensees.runtime import opensees_available

    params: dict[str, Any] = validate_params("ida", IDA_PARAMS, step.params)
    maybe_export_tcl(context, "ida", params)

    if not params["ground_motion_file"]:
        raise ValueError(
            "ida needs a 'ground_motion_file' — the path to a record "
            "(one acceleration value per line)"
        )
    if not params["intensities"]:
        raise ValueError("ida needs a non-empty 'intensities' ladder (record scale factors)")

    modal_result = resolve_modal_result(context, params["modal_case"])

    if not opensees_available():
        raise RuntimeError(
            "ida: the out-of-process OpenSees runner does not support IDA yet "
            "— run with openseespy installed."
        )

    from ...analysis.ida import run_ida as run_ida_analysis
    from ...model.stories import identify_stories

    # Resolve storey levels from the parsed model (ETABS's STORY DATA table is
    # only available at parse time; the diaphragm → area → node-clustering
    # fallback chain covers SAP2000 and SAFE).  The driver falls back to
    # roof-drift when fewer than two levels resolve.
    stories = identify_stories(context.model_data)

    result = run_ida_analysis(
        context.model,
        modal_result,
        ground_motion_file=params["ground_motion_file"],
        intensities=params["intensities"],
        dt=params["dt"],
        num_steps=params["num_steps"],
        direction=params["direction"],
        damping_ratio=params["damping_ratio"],
        collapse_drift=params["collapse_drift"],
        tolerance=params["tolerance"],
        max_scale=params["max_scale"],
        config=params["config"] or None,
        gravity_patterns=params["gravity_patterns"] or None,
        name=params["name"],
        stories=stories,
    )
    context.results["ida"] = result
    if params["export_path"]:
        from ...io.npz_writer import write_results_npz

        write_results_npz(
            params["export_path"],
            context.model_data,
            mesh_model=context.model,
            ida_results=result,
        )
        context.log(f"IDA results written to {params['export_path']}")
    context.log(f"IDA ({params['direction']}): complete")
    return [StepResult(kind=IDA, label="IDA", payload=result)]
