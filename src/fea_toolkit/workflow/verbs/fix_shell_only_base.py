"""The ``fix_shell_only_base`` verb — upgrade shell-only base restraints to full fixity.

Wraps :func:`fea_toolkit.model.supports.fix_shell_only_base_restraints`, which
edits the **parsed model before it is meshed**, so the Preprocessor propagates the
full fixity along the wall's base edge into the meshed nodes and the meshed shell
has no drilling mechanism at its base.

This is the *convenience* form of the fix: it is applied once, up front, so every
later analysis step (``run_static``, ``modal``, ``response_spectrum``,
``pushover``) inherits a well-posed base without repeating a flag.  The same
result is available per-analysis through the ``fix_shell_only_base`` build-time
config key on the analysis verbs, which fixes the meshed shell-only base nodes'
rotations in the OpenSees domain during the build.  The two are equivalent in
effect — a recipe may use either.
"""

from ..steps import MODEL, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["FIX_SHELL_ONLY_BASE_PARAMS", "run_fix_shell_only_base"]

FIX_SHELL_ONLY_BASE_PARAMS: dict[str, ParamSpec] = {}


def run_fix_shell_only_base(context: StepContext, step: Step) -> list[StepResult]:
    """Upgrade the model's shell-only base nodes to full fixity, in place.

    Args:
        context: The run's working state.  ``model_data`` is replaced with a
            copy whose shell-only base restraints are ``[1,1,1,1,1,1]``; the
            caller's model is left untouched.
        step: The step to run (no parameters).

    Returns:
        One :data:`~fea_toolkit.workflow.steps.MODEL` result whose payload is the
        edited ``SAPModelData``.
    """
    from ...model.supports import fix_shell_only_base_restraints
    from ...opensees.preprocessor import _copy_for_preprocessing

    validate_params("fix_shell_only_base", FIX_SHELL_ONLY_BASE_PARAMS, step.params)
    model = _copy_for_preprocessing(context.model_data)
    count, _node_ids = fix_shell_only_base_restraints(model)
    context.model_data = model

    if count:
        context.log(
            f"fix_shell_only_base: {count} shell-only base node(s) upgraded to "
            "full fixity (pinned → fixed rotations)"
        )
    else:
        context.log("fix_shell_only_base: no shell-only base nodes to fix")
    return [StepResult(kind=MODEL, label="Shell-only base fixed", payload=model)]
