"""Fiber-section stress-strain replay (``docs/pushover_results_storage_viz.md`` §5.3).

Rebuild a fiber section in a fresh OpenSees domain and replay a recorded
section-deformation history through it, reading back every fiber's
stress-strain.  This is the reconstruction half of the condensed
section-deformation provision (§5.1): a recorded ε,κ history plus the section's
fiber discretisation and material definitions fully determine every fiber's
strain (``ε_fiber = ε + κ_z·y + κ_y·z`` under plane-sections-remain-plane), and
replaying the history through a fresh copy of the section reproduces the
path-dependent stress.

The OpenSees ``FiberSection3d`` deformation vector is ordered ``[ε, κ_z, κ_y]``
(``FiberSection3d::setTrialSectionDeformation`` computes
``strain = d0 + y*d1 + z*d2`` with ``d1 = κ_z``, ``d2 = κ_y``), and the element
response ``eleResponse(tag, 'section', n, 'deformation')`` appends the elastic
torsion ``φ`` as a fourth component.  The replay drives a single-integration-
point ``dispBeamColumn`` (unit length along global X, local axes aligned with
global) so the imposed free-node displacement maps one-to-one onto the section
deformation: ``[ε, κ_z, κ_y, φ] = [ux, rz, ry, rx]``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import openseespy.opensees as ops


def replay_fiber_section(
    patches: Sequence[tuple],
    materials: dict[int, tuple[str, Sequence[float]]],
    deform_history: Sequence[Sequence[float]],
    J: float,
    *,
    nfy: int = 8,
    nfz: int = 4,
    sec_tag: int = 1,
    elem_tag: int = 1,
) -> dict[str, Any]:
    """Rebuild a fiber section and replay a section-deformation history.

    Args:
        patches: Fiber patch/layer entries as returned by
            ``Section.to_fiber_patches(mat_tag, nfy, nfz)``.  Each entry is an
            ``ops.patch`` / ``ops.layer`` argument tuple: ``("rect", …)``,
            ``("circ", …)``, ``("quad", …)``, ``("straight", …)`` or
            ``("circ_layer", …)``.
        materials: Mapping of uniaxial material tag → ``(mat_type, args)``
            where ``mat_type`` is e.g. ``"Concrete01"`` / ``"Steel02"`` and
            ``args`` are the trailing constructor arguments (after the tag).
            Must cover every material tag referenced by *patches*.
        deform_history: ``(n_step, 4)`` section-deformation rows in OpenSees
            order ``[ε, κ_z, κ_y, φ]``.
        J: Torsional constant for the section's ``-GJ`` term (model units).
        nfy: Fibers along the local y direction (patch refinement).
        nfz: Fibers along the local z direction (patch refinement).
        sec_tag: OpenSees section tag to build into.
        elem_tag: OpenSees element tag to build into.

    Returns:
        Dict with keys:

        * ``"stress"`` — ``(n_step, n_fiber)`` float array
        * ``"strain"`` — ``(n_step, n_fiber)`` float array
        * ``"n_fiber"`` — int

    Raises:
        ValueError: If *deform_history* is empty or not shaped ``(n_step, 4)``.

    Note:
        Calls :func:`openseespy.opensees.wipe` at entry (to guarantee a clean
        domain) and at exit (to restore OpenSees hygiene), so it is safe to
        call between tests or after a live analysis whose results have already
        been extracted.
    """
    history = np.asarray(deform_history, dtype=float)
    if history.ndim != 2 or history.shape[1] != 4:
        raise ValueError(
            "deform_history must be (n_step, 4) in order [ε, κ_z, κ_y, φ], "
            f"got shape {history.shape}"
        )
    if history.shape[0] == 0:
        raise ValueError("deform_history must contain at least one step")

    try:
        # ── Build a fresh single-section domain ─────────────────
        ops.wipe()
        ops.model("basic", "-ndm", 3, "-ndf", 6)
        ops.node(1, 0.0, 0.0, 0.0)
        ops.node(2, 1.0, 0.0, 0.0)  # unit length along global X
        ops.fix(1, 1, 1, 1, 1, 1, 1)

        for tag, (mat_type, args) in materials.items():
            ops.uniaxialMaterial(mat_type, tag, *args)

        ops.section("Fiber", sec_tag, "-GJ", J)
        for entry in patches:
            if entry[0] in ("rect", "circ", "quad"):
                ops.patch(*entry)
            elif entry[0] == "straight":
                ops.layer("straight", *entry[1:])
            elif entry[0] == "circ_layer":
                ops.layer("circ", *entry[1:])
            else:
                raise ValueError(f"unsupported patch entry {entry[0]!r}")

        # Local z = global z (vecxz); element along global X → local axes
        # align with global, so node-2 DOFs map onto the section deformation
        # [ε, κ_z, κ_y, φ] = [ux, rz, ry, rx] with unit length.
        ops.geomTransf("Linear", 1, 0, 0, 1)
        ops.beamIntegration("Lobatto", 10001, sec_tag, 1)  # single IP (midspan)
        ops.element("dispBeamColumn", elem_tag, 1, 2, sec_tag, 10001)

        # Reference zero load so the static analysis has a load vector.
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        ops.load(2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        # ── Count fibers ────────────────────────────────────────
        n_fiber = 0
        while True:
            try:
                resp = ops.eleResponse(elem_tag, "section", 1, "fiber", n_fiber, "stressStrain")
            except Exception:
                break
            if not resp or len(resp) < 2:
                break
            n_fiber += 1

        stress = np.empty((history.shape[0], n_fiber), dtype=float)
        strain = np.empty((history.shape[0], n_fiber), dtype=float)

        # ── Replay the history (path-dependent commit per step) ──
        # One Constant time series + Plain pattern drives every step; the
        # prescribed node-2 DOFs are updated by removing the previous
        # single-point constraint before adding its replacement, so each DOF
        # carries exactly one active constraint as the recorded values change
        # step to step while the analysis is initialised once.
        ops.timeSeries("Constant", 2)
        ops.pattern("Plain", 2, 2)
        for dof in range(1, 7):
            ops.sp(2, dof, 0.0)

        ops.system("UmfPack")
        ops.numberer("Plain")
        ops.constraints("Transformation")
        ops.test("NormDispIncr", 1e-12, 50)
        ops.algorithm("Newton")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")

        for k, row in enumerate(history):
            eps, kz, ky, phi = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            # Node-2 DOFs map onto the section deformation as
            # [ε, κ_z, κ_y, φ] = [ux, rz, ry, rx], with uy/uz restrained.
            node2_dofs = (eps, 0.0, 0.0, phi, ky, kz)
            for dof, value in enumerate(node2_dofs, start=1):
                ops.remove("sp", 2, dof, 2)
                ops.sp(2, dof, value)
            if ops.analyze(1) != 0:
                raise RuntimeError(f"replay step {k} failed to converge")
            for i in range(n_fiber):
                ss = ops.eleResponse(elem_tag, "section", 1, "fiber", i, "stressStrain")
                stress[k, i] = float(ss[0])
                strain[k, i] = float(ss[1])
    finally:
        ops.wipe()

    return {"stress": stress, "strain": strain, "n_fiber": n_fiber}
