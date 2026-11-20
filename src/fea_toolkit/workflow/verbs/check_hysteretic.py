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


def _peak_displacement(open, ke, fy) -> float:
    """A displacement large enough to close the gap / pass the yield point."""
    candidates = [x for x in (abs(open), fy / ke if ke > 0.0 else 0.0) if x > 0.0]
    return 10.0 * max(candidates, default=1.0e-3)


def _collect_backbones(model_data):
    """Yield ``(name, args, history, symmetric)`` for every ``Hysteretic`` link.

    Gap/Hook are one-sided: the cycle closes the gap (transmits force), opens
    it (releases) and re-closes it.  The bilinear backbone (a ``Plastic (Wen)``
    link on the ``hysteretic`` path, or a bilinear damper) is symmetric.
    """
    from ...model.sap_data import DamperParams, GapParams, HookParams, WenParams
    from ...opensees._links import _hysteretic_bilinear_args, _hysteretic_one_sided_args

    props = getattr(model_data, "link_properties", None) or {}
    for name in sorted(props):
        prop = props[name]
        p = prop.nonlinear_params
        ke = _first_positive(prop.ke)
        if ke <= 0.0:
            continue
        if prop.link_type == "Gap" and isinstance(p, GapParams):
            open_ = _first_positive(p.open)
            peak = _peak_displacement(open_, ke, 0.0)
            args = _hysteretic_one_sided_args(ke, open_, "gap")
            # compression (negative) closes the gap; tension opens it.
            yield name, args, [-peak, 0.0, peak, 0.0, -peak], False
        elif prop.link_type == "Hook" and isinstance(p, HookParams):
            open_ = _first_positive(p.open)
            peak = _peak_displacement(open_, ke, 0.0)
            args = _hysteretic_one_sided_args(ke, open_, "hook")
            yield name, args, [peak, 0.0, -peak, 0.0, peak], False
        elif prop.link_type == "Plastic (Wen)" and isinstance(p, WenParams):
            fy = _first_positive(p.fy)
            if fy > 0.0:
                peak = _peak_displacement(0.0, ke, fy)
                args = _hysteretic_bilinear_args(ke, fy, p.ratio)
                yield name, args, [peak, 0.0, -peak, 0.0, peak], True
        elif prop.link_type.startswith("Damper") and isinstance(p, DamperParams):
            fy = _first_positive(p.fy)
            if fy > 0.0:
                peak = _peak_displacement(0.0, ke, fy)
                args = _hysteretic_bilinear_args(ke, fy, 0.0)
                yield name, args, [peak, 0.0, -peak, 0.0, peak], True


def _cyclic_backbone_check(
    ops, args: list, tol: float, history: list, symmetric: bool
) -> tuple[bool, str]:
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
    times = list(range(len(history) + 1))
    values = [0.0, *history]
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
    for _ in history:
        if ops.analyze(1) != 0:
            return False, "analysis failed to converge"
        forces.append(float(ops.eleResponse(1, "axialForce")[0]))
    peak_force = forces[0]
    if abs(peak_force) < tol:
        return False, "no force transmitted in the active direction"
    if abs(forces[-1] - peak_force) > tol:
        return False, "strength degraded after a cycle"
    if symmetric and abs(forces[2] + peak_force) > tol:
        return False, "asymmetric reverse (pinching)"
    return True, "clean cyclic backbone"


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
    for name, args, history, symmetric in _collect_backbones(context.model_data):
        ok, detail = _cyclic_backbone_check(ops, args, tol, history, symmetric)
        rows.append((name, "passed" if ok else "failed", detail))
    ops.wipe()

    table = Table(
        title="Hysteretic backbone",
        columns=("link", "result", "detail"),
        rows=tuple(rows) if rows else (("(none)", "passed", "no Hysteretic links"),),
    )
    return [StepResult(kind="table", label="Hysteretic backbone", payload=table)]
