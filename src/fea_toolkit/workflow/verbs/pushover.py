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
from ..steps import CASES, ParamSpec, Step, StepContext, StepResult, validate_params

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
}


def run_pushover(context: StepContext, step: Step) -> list[StepResult]:
    """Run a pushover analysis against the prepared topology.

    Args:
        context: The run's working state.  ``model`` and ``results["modal"]``
            must already be present (``mesh`` then ``modal`` steps precede this).
        step: The step to run.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.CASES` result whose payload is
        the pushover :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared by this verb.
    """
    from ...analysis.pushover import run_pushover_analysis

    params: dict[str, Any] = validate_params("pushover", PUSHOVER_PARAMS, step.params)
    result = run_pushover_analysis(
        context.model,
        context.results["modal"],
        material_type=params["material_type"],
        lateral_load_type=params["lateral_load_type"],
        max_disp_val=params["max_disp_val"],
        num_steps=params["num_steps"],
        directions=params["directions"],
        name=params["name"],
        config=params["config"] or None,
    )
    context.results["pushover"] = result
    context.log(f"Pushover ({params['directions']}): complete")
    return [StepResult(kind=CASES, label="Pushover", payload=result)]
