"""A shared 1-DOF truss rig for driving a uniaxial material through a cycle.

The ``Hysteretic``-backbone cyclic check (:mod:`fea_toolkit.opensees._hysteretic_check`)
and the component tests (``tests/test_link_nonlinear.py``) both drive a
uniaxial material through a displacement history on a 1-DOF truss
(``ndm=1, ndf=1``).  This is a **check/test-only** rig — the production
workflow stays 3-D (``.clinerules`` §3.11) — so it lives in the ``opensees``
package but is never used by the :class:`AnalysisBuilder`.

Nothing here imports OpenSees at module level — the ``ops`` handle is passed
in — so this module stays importable in the "separate" build (no bundled
``openseespy``).
"""

from __future__ import annotations

from typing import Callable, Optional


def drive_truss_history(
    ops, emit: Callable[[int], None], history: list[float]
) -> Optional[list[float]]:
    """Drive a 1-DOF truss through ``history``; return the axial forces.

    The displacement history is imposed through a ``Path`` time series on a
    unit ``sp``, which is the reliable way to reverse a
    displacement-controlled analysis mid-run (the material's path-dependent
    state is preserved across the whole history).  ``Penalty`` constraints
    keep the free-DOF system non-singular when the material's tangent
    stiffness is zero (e.g. the released direction of a one-sided backbone).

    Args:
        ops: The ``openseespy.opensees`` module handle.
        emit: Builds the uniaxial material with the given integer tag — e.g.
            ``lambda tag: ops.uniaxialMaterial("Hysteretic", tag, *args)``.
        history: Displacement values, applied in order.

    Returns:
        The truss ``axialForce`` at each history point, or ``None`` if any
        step fails to converge.
    """
    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.node(1, 0.0)
    ops.node(2, 1.0)
    ops.fix(1, 1)
    emit(1)
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
    forces: list[float] = []
    for _ in history:
        if ops.analyze(1) != 0:
            return None
        forces.append(float(ops.eleResponse(1, "axialForce")[0]))
    return forces
