"""The ``check_hysteretic_backbone`` verb — the one-time cyclic check (D8).

Re-runs the neutral-defaults check on the ``Hysteretic``-backbone nonlinear
links: drive the emitted material through a cycle and assert the response stays
on the backbone — no pinching, no strength degradation.  The check is
two-sided for the symmetric bilinear backbone (a ``Plastic (Wen)`` link on the
``hysteretic`` path, or a bilinear damper) and one-sided for Gap/Hook (the gap
closes to transmit force, then opens to release — and re-closes unchanged).  A
monotonic test cannot catch a wrong pinch/damage value, so this is the cyclic
guard (plan §7, D7/D8), exposed as a verb so a generated model's script re-runs
it.

The check logic lives in :mod:`fea_toolkit.opensees._hysteretic_check` (shared
with the out-of-process driver); this verb is the thin recipe-facing wrapper.
OpenSees is imported inside the function only, so importing the manifest does
not load it.
"""

from ..steps import ParamSpec, Step, StepContext, StepResult, Table, validate_params

__all__ = ["HYSTERETIC_CHECK_PARAMS", "run_check_hysteretic_backbone"]

HYSTERETIC_CHECK_PARAMS: dict[str, ParamSpec] = {
    "tol": ParamSpec(
        default=1.0e-6,
        type=float,
        help="Force tolerance (model units) for the symmetric / non-degrading assertion.",
    ),
}


def run_check_hysteretic_backbone(context: StepContext, step: Step) -> list[StepResult]:
    """Re-run the cyclic check on the model's ``Hysteretic``-backbone links.

    In the "separate" build (no bundled ``openseespy``) the check is delegated
    to the runtime interpreter via
    :func:`~fea_toolkit.opensees.delegate.run_check_hysteretic_out_of_process`.

    Args:
        context: The run's working state; ``model_data`` is the model inspected.
        step: The step to run; ``params`` carries the tolerance.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result with one row per
        checked link DOF (labelled ``<property>[<dof-index>]``).
    """
    from ...opensees._hysteretic_check import collect_backbones, cyclic_backbone_check
    from ...opensees.runtime import opensees_available

    params = validate_params("check_hysteretic_backbone", HYSTERETIC_CHECK_PARAMS, step.params)
    tol = params["tol"]

    if opensees_available():
        import openseespy.opensees as ops

        rows = []
        try:
            for name, args, history, symmetric in collect_backbones(context.model_data):
                ok, detail = cyclic_backbone_check(ops, args, tol, history, symmetric)
                rows.append((name, "passed" if ok else "failed", detail))
        finally:
            # The check owns the temporary 1-DOF truss domain it creates, so wipe it
            # even when a material build or solve raises — never leave a live domain
            # behind for a later step to accidentally reuse.
            ops.wipe()
    else:
        from ...opensees.delegate import run_check_hysteretic_out_of_process

        rows = run_check_hysteretic_out_of_process(context, tol)

    table = Table(
        title="Hysteretic backbone",
        columns=("link", "result", "detail"),
        rows=tuple(rows) if rows else (("(none)", "passed", "no Hysteretic links"),),
    )
    return [StepResult(kind="table", label="Hysteretic backbone", payload=table)]
