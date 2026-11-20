"""The ``check_hysteretic_backbone`` verb — the one-time cyclic check (D8).

Re-runs the neutral-defaults check on the ``Hysteretic``-backbone nonlinear
links (Gap, Hook, and the bilinear backbone used for Wen-``hysteretic`` and
bilinear dampers): drive the emitted material through a
load → unload → reverse → reload cycle and assert the response stays on the
backbone — no pinching, no strength degradation.  A monotonic test cannot
catch a wrong pinch/damage value, so this is the cyclic guard (plan §7, D7/D8),
exposed as a verb so a generated model's script re-runs it.

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


def _first_positive(values) -> float:
    """The first positive entry of a per-DOF list, else ``0.0``."""
    for v in values or ():
        if v and v > 0.0:
            return float(v)
    return 0.0


def _collect_backbones(model_data):
    """Yield ``(name, hysteretic_args)`` for every bilinear ``Hysteretic`` link.

    Only the *symmetric bilinear* backbone (Wen-``hysteretic`` and the bilinear
    damper) is checked cyclically — that is where a wrong pinch/damage value
    would go unnoticed by a monotonic test.  The one-sided Gap/Hook backbones
    are pinned monotonically by the D9 comparison tests instead.
    """
    from ...model.sap_data import DamperParams, WenParams
    from ...opensees._links import _hysteretic_bilinear_args

    props = getattr(model_data, "link_properties", None) or {}
    for name in sorted(props):
        prop = props[name]
        p = prop.nonlinear_params
        ke = _first_positive(prop.ke)
        if ke <= 0.0:
            continue
        if prop.link_type == "Plastic (Wen)" and isinstance(p, WenParams):
            fy = _first_positive(p.fy)
            if fy > 0.0:
                yield name, _hysteretic_bilinear_args(ke, fy, p.ratio)
        elif prop.link_type.startswith("Damper") and isinstance(p, DamperParams):
            fy = _first_positive(p.fy)
            if fy > 0.0:
                yield name, _hysteretic_bilinear_args(ke, fy, 0.0)


def _cyclic_backbone_check(ops, args: list, tol: float) -> tuple[bool, str]:
    """Drive a ``Hysteretic`` material through a cycle; report pinching/degradation.

    A 1-DOF truss rig mirrors the component test in ``tests/test_link_nonlinear.py``.
    """
    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.node(1, 0.0)
    ops.node(2, 1.0)
    ops.fix(1, 1)
    ops.uniaxialMaterial("Hysteretic", 1, *args)
    ops.element("truss", 1, 1, 2, 1.0, 1)
    times = [0, 1, 2, 3, 4, 5]
    values = [0.0, 0.1, 0.0, -0.1, 0.0, 0.1]
    ops.timeSeries("Path", 1, "-time", *times, "-values", *values, "-factor", 1.0)
    ops.pattern("Plain", 1, 1)
    ops.sp(2, 1, 1.0)
    ops.constraints("Penalty", 1.0e14, 1.0e14)
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-12, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    forces = []
    for _ in range(5):
        if ops.analyze(1) != 0:
            return False, "analysis failed to converge"
        forces.append(float(ops.eleResponse(1, "axialForce")[0]))
    peak = forces[0]
    if abs(peak) < tol:
        return False, "zero peak force"
    if abs(forces[2] + peak) > tol:
        return False, "asymmetric reverse (pinching)"
    if abs(forces[4] - peak) > tol:
        return False, "strength degraded after a cycle"
    return True, "symmetric, non-degrading"


def run_check_hysteretic_backbone(context: StepContext, step: Step) -> list[StepResult]:
    """Re-run the cyclic check on the model's ``Hysteretic``-backbone links.

    Args:
        context: The run's working state; ``model_data`` is the model inspected.
        step: The step to run; ``params`` carries the tolerance.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result with one row per
        checked link property.
    """
    import openseespy.opensees as ops

    params = validate_params("check_hysteretic_backbone", HYSTERETIC_CHECK_PARAMS, step.params)
    tol = params["tol"]

    rows = []
    for name, args in _collect_backbones(context.model_data):
        ok, detail = _cyclic_backbone_check(ops, args, tol)
        rows.append((name, "passed" if ok else "failed", detail))
    ops.wipe()

    table = Table(
        title="Hysteretic backbone",
        columns=("link", "result", "detail"),
        rows=tuple(rows) if rows else (("(none)", "passed", "no Hysteretic links"),),
    )
    return [StepResult(kind="table", label="Hysteretic backbone", payload=table)]
