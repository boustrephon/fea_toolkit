"""The ``build_model`` verb — build a benchmark example model in memory.

Delegates to the benchmark builders (``fema355c.build_fema355c`` /
``generic_frame.build_model`` + ``enrich_imk`` / ``sac.load_model``), which
already return a :class:`~fea_toolkit.model.sap_data.SAPModelData`.  The built
model *replaces* the working document, so a following ``mesh`` / analysis step
solves the example, not whatever model the recipe started from.  The heavy
builder imports stay inside the function, so importing the manifest never loads
OpenSees (see :mod:`fea_toolkit.workflow.registry`).
"""

from typing import Any

from ..steps import MODEL, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["BUILD_MODEL_PARAMS", "run_build_model"]

BUILD_MODEL_PARAMS: dict[str, ParamSpec] = {
    "builder": ParamSpec(
        default="",
        type=str,
        help="Which builder: 'fema355c', 'sac' or 'generic_frame'.",
    ),
    # FEMA 355C / SAC Phase 2
    "region": ParamSpec(default="LA", type=str, help="FEMA 355C region: 'LA', 'SE' or 'BO'."),
    "stories": ParamSpec(default=3, type=int, help="FEMA 355C storey count: 3, 9 or 20."),
    "design": ParamSpec(
        default="pre",
        type=str,
        help="FEMA 355C design: 'pre', 'post', 'rbs', or LA9 'r1'/'r2'/'r3'.",
    ),
    "direction": ParamSpec(default="3d", type=str, help="FEMA 355C direction: '3d' or '2d'."),
    "restrain_ground_laterally": ParamSpec(
        default=True,
        type=bool,
        help="FEMA 355C: laterally restrain the ground level of basement buildings.",
    ),
    # IK-2005 generic frame
    "code": ParamSpec(default="", type=str, help="Generic-frame code, e.g. '0909'."),
    # SAC
    "building": ParamSpec(default=3, type=int, help="SAC building: 3, 9 or 20."),
    "backbone": ParamSpec(default="bilinear", type=str, help="SAC hinge backbone."),
    "nonlinear": ParamSpec(default=True, type=bool, help="SAC: enrich for nonlinear use."),
}


def run_build_model(context: StepContext, step: Step) -> list[StepResult]:
    """Build a benchmark example model as the working document.

    The built model replaces ``context.model_data``; the prepared topology,
    solved cases and named selections are cleared, so a later step cannot read
    a stale payload left over from the model the recipe started from.

    Args:
        context: The run's working state; ``model_data`` is replaced with the
            built model.
        step: The step to run.  ``params`` carry ``builder`` plus the
            builder-specific parameters.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.MODEL` result carrying the built
        :class:`~fea_toolkit.model.sap_data.SAPModelData`.

    Raises:
        ValueError: If ``builder`` is unknown, or a required builder parameter
            (``code`` for ``generic_frame``) is missing.
    """
    params: dict[str, Any] = validate_params("build_model", BUILD_MODEL_PARAMS, step.params)
    builder = params["builder"]

    if builder == "fema355c":
        from ...benchmarks.fema355c import build_fema355c

        model = build_fema355c(
            params["region"],
            params["stories"],
            params["design"],
            params["direction"],
            params["restrain_ground_laterally"],
        )
    elif builder == "generic_frame":
        if not params["code"]:
            raise ValueError("build_model: 'code' is required for builder 'generic_frame'")
        from ...benchmarks.generic_frame import build_model as _build_generic
        from ...benchmarks.generic_frame import enrich_imk

        model = enrich_imk(_build_generic(params["code"]), params["code"])
    elif builder == "sac":
        from ...benchmarks.sac import load_model

        model = load_model(
            params["nonlinear"],
            building=params["building"],
            backbone=params["backbone"],
        )
    else:
        raise ValueError(
            f"build_model: unknown builder {builder!r} "
            "(expected 'fema355c', 'sac' or 'generic_frame')"
        )

    context.model_data = model
    context.model = None
    context.case_results.clear()
    context.results.clear()
    context.log(f"build_model: built {builder} ({context.model_data})")
    return [StepResult(kind=MODEL, label="Built model", payload=model)]
