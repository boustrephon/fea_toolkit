"""The ``pushover`` verb — nonlinear static (pushover) analysis.

Wraps :func:`~fea_toolkit.analysis.pushover.run_pushover_analysis`, which
requires the modal result a preceding
:mod:`~fea_toolkit.workflow.verbs.modal` step stored on the context.  Builder
overrides travel in the ``config`` dict; the demand spectrum is left to the
analysis function's GB 50011 rare-event default (a future refinement may expose
it as a parameter).

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..config_keys import BUILDER_CONFIG_KEYS
from ..steps import PUSHOVER, ParamSpec, Step, StepContext, StepResult, validate_params
from ._tcl import EXPORT_TCL_PARAMS, maybe_export_tcl

__all__ = ["PUSHOVER_PARAMS", "run_pushover"]

PUSHOVER_PARAMS: dict[str, ParamSpec] = {
    "material_type": ParamSpec(
        default="steel",
        type=str,
        choices=("steel", "rc"),
        help="Section material — steel, or reinforced concrete (forceBeamColumn fibres).",
    ),
    "lateral_load_type": ParamSpec(
        default="mode1",
        type=str,
        choices=("uniform", "triangular", "mode1"),
        help="Lateral load distribution shape.",
    ),
    "max_disp_val": ParamSpec(
        default=0.30,
        type=float,
        help="Maximum control displacement, in the model's length units.",
    ),
    "num_steps": ParamSpec(
        default=50,
        type=int,
        help="Number of push increments.",
    ),
    "directions": ParamSpec(
        default="4dir",
        type=str,
        help="Push direction(s) — '4dir' or a single '+X'/'-X'/'+Y'/'-Y'.",
    ),
    "gravity_patterns": ParamSpec(
        default={},
        type=dict,
        help="Gravity load patterns → scale factor, applied before the lateral push.",
    ),
    "brace_type": ParamSpec(
        default="truss",
        type=str,
        choices=("truss", "beam"),
        help="Brace modelling — 'truss' for steel braces, 'beam' for elastic.",
    ),
    "brace_sections": ParamSpec(
        default=[],
        type=list,
        help="Brace section assignments for the 'beam' brace type.",
    ),
    "step_stride": ParamSpec(
        default=1,
        type=int,
        help="Record every Nth converged push step (subsample the retained history).",
    ),
    "store_node_disp": ParamSpec(
        default=True,
        type=bool,
        help="Retain per-step node displacements (the deformed-shape history; cheap).",
    ),
    "store_frame_forces": ParamSpec(
        default=False,
        type=bool,
        help="Retain per-step frame end forces (expensive — off unless force/DCR review is wanted).",
    ),
    "store_shell_forces": ParamSpec(
        default=False,
        type=bool,
        help="Retain per-step shell stress resultants (expensive — off by default).",
    ),
    "store_section_deformations": ParamSpec(
        default=False,
        type=bool,
        help="Retain per-step section deformations (ε, κ_y, κ_z, φ) at each frame "
        "integration point — the condensed fiber-reconstruction key.",
    ),
    "name": ParamSpec(
        default="Pushover",
        type=str,
        help="Result label.",
    ),
    "config": ParamSpec(
        default={},
        type=dict,
        help="OpenSees builder overrides, merged over the material-type defaults.",
        manifest=BUILDER_CONFIG_KEYS,
    ),
    "return_builders": ParamSpec(
        default=False,
        type=bool,
        help="Retain each direction's builder for stage-file export.",
    ),
    **EXPORT_TCL_PARAMS,
}


def run_pushover(context: StepContext, step: Step) -> list[StepResult]:
    """Run a pushover analysis against the prepared topology.

    Args:
        context: The run's working state.  ``model`` and ``results["modal"]``
            must already be present (``mesh`` then ``modal`` steps precede this).
        step: The step to run.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.PUSHOVER` result whose payload is
        the pushover :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...analysis.pushover import run_pushover_analysis

    params: dict[str, Any] = validate_params("pushover", PUSHOVER_PARAMS, step.params)
    maybe_export_tcl(context, "pushover", params)

    # ── Retention knobs → builder config ──────────────────────────
    # The retention params are the authoring surface; the runner reads the
    # flat builder-config keys below.  ``record_pushover_steps`` is the master
    # gate (any requested block turns recording on), and the per-block flags
    # keep a default run cheap: node displacements only.
    config = dict(params["config"] or {})
    config["record_pushover_steps"] = bool(
        params["store_node_disp"]
        or params["store_frame_forces"]
        or params["store_shell_forces"]
        or params["store_section_deformations"]
    )
    config["pushover_step_stride"] = params["step_stride"]
    config["pushover_record_node_disp"] = params["store_node_disp"]
    config["pushover_record_frame_forces"] = params["store_frame_forces"]
    config["pushover_record_shell_forces"] = params["store_shell_forces"]
    config["pushover_record_section_deformations"] = params["store_section_deformations"]

    result = run_pushover_analysis(
        context.model,
        context.results["modal"],
        material_type=params["material_type"],
        gravity_patterns=params["gravity_patterns"] or None,
        lateral_load_type=params["lateral_load_type"],
        max_disp_val=params["max_disp_val"],
        num_steps=params["num_steps"],
        brace_type=params["brace_type"],
        brace_sections=params["brace_sections"] or None,
        directions=params["directions"],
        name=params["name"],
        config=config or None,
        return_builders=params["return_builders"],
        log=context.pipeline_log,
    )
    context.results["pushover"] = result
    context.log(f"Pushover ({params['directions']}): complete")
    return [StepResult(kind=PUSHOVER, label="Pushover", payload=result)]
