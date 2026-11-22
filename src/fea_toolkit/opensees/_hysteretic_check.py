"""The one-time cyclic ``Hysteretic``-backbone check (plan §7, D7/D8).

Drives the emitted ``Hysteretic`` backbones — one-sided for Gap/Hook, symmetric
bilinear for a ``Plastic (Wen)`` link on the ``hysteretic`` path or a bilinear
damper — through a load–unload–reverse–reload cycle on a 1-DOF truss and
asserts the response stays on the backbone (no pinching, no strength
degradation).  A monotonic test cannot catch a wrong pinch/damage value, so
this is the cyclic guard.

The logic lives here (next to the emitters) rather than in the
``check_hysteretic_backbone`` verb so both the in-process verb and the
out-of-process driver (:mod:`fea_toolkit.opensees.subprocess_driver`) run the
same code — one operation, one spelling.  Nothing here imports OpenSees at
module level (``ops`` is passed in), so the module is importable in the
"separate" build.
"""

from __future__ import annotations

from typing import Any

from ._material_rig import drive_truss_history


def _peak_displacement(open: float, ke: float, fy: float) -> float:
    """A displacement large enough to close the gap / pass the yield point."""
    candidates = [x for x in (abs(open), fy / ke if ke > 0.0 else 0.0) if x > 0.0]
    return 10.0 * max(candidates, default=1.0e-3)


def collect_backbones(model_data: Any):
    """Yield ``(label, args, history, symmetric)`` for every eligible DOF.

    Gap/Hook are one-sided: the cycle closes the gap (transmits force), opens
    it (releases) and re-closes it.  The bilinear backbone (a ``Plastic (Wen)``
    link on the ``hysteretic`` path, or a bilinear damper) is symmetric.  Each
    DOF with nonzero effective stiffness is checked, and its ``open``/``fy``
    value is read from the *same* DOF index, so a mixed-DOF property cannot
    pair one DOF's stiffness with another's yield force.
    """
    from ..model.sap_data import DamperParams, GapParams, HookParams, WenParams
    from ._links import _hysteretic_bilinear_args, _hysteretic_one_sided_args

    props = getattr(model_data, "link_properties", None) or {}
    for name in sorted(props):
        prop = props[name]
        p = prop.nonlinear_params
        for idx, ke in enumerate(prop.ke):
            if ke <= 0.0:
                continue
            label = f"{name}[{idx}]"
            if prop.link_type == "Gap" and isinstance(p, GapParams):
                open_ = p.open[idx]
                peak = _peak_displacement(open_, ke, 0.0)
                args = _hysteretic_one_sided_args(ke, open_, "gap")
                # compression (negative) closes the gap; tension opens it.  The
                # intermediate ``-0.5*peak`` sample probes the reload branch (not
                # just the extrema), so a pinched one-sided response is compared
                # with the neutral reference along the re-closing path.
                yield label, args, [-peak, 0.0, peak, 0.0, -0.5 * peak, -peak], False
            elif prop.link_type == "Hook" and isinstance(p, HookParams):
                open_ = p.open[idx]
                peak = _peak_displacement(open_, ke, 0.0)
                args = _hysteretic_one_sided_args(ke, open_, "hook")
                yield label, args, [peak, 0.0, -peak, 0.0, 0.5 * peak, peak], False
            elif prop.link_type == "Plastic (Wen)" and isinstance(p, WenParams):
                fy = p.fy[idx]
                if fy > 0.0:
                    peak = _peak_displacement(0.0, ke, fy)
                    args = _hysteretic_bilinear_args(ke, fy, p.ratio)
                    yield label, args, [peak, 0.0, -peak, 0.0, 0.5 * peak, peak], True
            elif prop.link_type.startswith("Damper") and isinstance(p, DamperParams):
                fy = p.fy[idx]
                if fy > 0.0:
                    peak = _peak_displacement(0.0, ke, fy)
                    args = _hysteretic_bilinear_args(ke, fy, 0.0)
                    yield label, args, [peak, 0.0, -peak, 0.0, 0.5 * peak, peak], True


def cyclic_backbone_check(
    ops, args: list, tol: float, history: list, symmetric: bool
) -> tuple[bool, str]:
    """Drive a ``Hysteretic`` material through a cycle; report pinching/degradation.

    The material is driven alongside a *neutral-pinch reference* (the same
    backbone with ``pinchX=pinchY=1`` and zero damage/beta forced): the peak
    checks catch strength degradation, and the reload-branch comparison catches
    pinching, which shifts the reload forces between the extrema without moving
    the peaks themselves.
    """
    neutral_args = list(args)
    neutral_args[-5:] = [1.0, 1.0, 0.0, 0.0, 0.0]

    def _drive(mat_args):
        return drive_truss_history(
            ops, lambda tag: ops.uniaxialMaterial("Hysteretic", tag, *mat_args), history
        )

    forces = _drive(args)
    if forces is None:
        return False, "analysis failed to converge"
    reference = _drive(neutral_args)
    if reference is None:
        return False, "reference analysis failed to converge"

    peak_force = forces[0]
    if abs(peak_force) < tol:
        return False, "no force transmitted in the active direction"
    if abs(forces[-1] - peak_force) > tol:
        return False, "strength degraded after a cycle"
    if symmetric and abs(forces[2] + peak_force) > tol:
        return False, "asymmetric reverse (pinching)"
    for i, (force, ref) in enumerate(zip(forces, reference)):
        if abs(force - ref) > tol:
            return False, f"non-neutral (pinched) reload at sample {i}"
    return True, "clean cyclic backbone"
