"""The ``isolate_base`` verb — turn fixed base supports into grounded isolators.

Wraps :func:`fea_toolkit.model.supports.isolate_base`, which edits the parsed
model *before it is meshed*: each fully-restrained base node's support is
removed and replaced by a grounded isolator link assigned the step's
``property``.  The builder then emits the isolator (a bearing element for a
Rubber/Friction Isolator) at each base node.

This is the *convenience* form — applied once, up front, so every later
analysis step inherits the isolated base without repeating a flag.
"""

from ..steps import MODEL, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["ISOLATE_BASE_PARAMS", "run_isolate_base"]

ISOLATE_BASE_PARAMS: dict[str, ParamSpec] = {
    "property": ParamSpec(
        default="",
        type=str,
        help="Name of the isolator link property (Rubber/Friction Isolator) to assign.",
    ),
    "coord_dir1": ParamSpec(
        default="+Z",
        type=str,
        help="Local-1 (axial) reference direction for the grounded isolator (default +Z).",
    ),
}


def run_isolate_base(context: StepContext, step: Step) -> list[StepResult]:
    """Replace the model's fully-restrained base supports with grounded isolators.

    Args:
        context: The run's working state.  ``model_data`` is replaced with a
            copy whose base supports are grounded isolator links; the caller's
            model is left untouched.
        step: The step to run; ``selection`` scopes the base nodes (default
            every fully-restrained node).

    Returns:
        One :data:`~fea_toolkit.workflow.steps.MODEL` result whose payload is the
        edited ``SAPModelData``.

    Raises:
        ValueError: If a parameter is not declared, or ``property`` is empty.
    """
    from ...model.supports import isolate_base
    from ...opensees.preprocessor import _copy_for_preprocessing

    params = validate_params("isolate_base", ISOLATE_BASE_PARAMS, step.params)
    if not params["property"]:
        raise ValueError("isolate_base: 'property' is required (the isolator link property name)")

    model = _copy_for_preprocessing(context.model_data)
    count, node_ids = isolate_base(
        model, params["property"], selection=step.selection, coord_dir1=params["coord_dir1"]
    )
    context.model_data = model

    if count:
        context.log(
            f"isolate_base: {count} base node(s) replaced by grounded "
            f"{params['property']!r} isolators ({', '.join(node_ids)})"
        )
    else:
        context.log("isolate_base: no fully-restrained base nodes to isolate")
    return [StepResult(kind=MODEL, label="Base isolated", payload=model)]
