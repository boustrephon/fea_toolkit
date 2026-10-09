"""Validation of the fiber-stress-strain replay tool (``docs/pushover_results_storage_viz.md`` §5.3).

The replay tool rebuilds a fiber section and replays a recorded section
deformation history, reading back per-fiber stress-strain.  This test is the
"benchmark" the spec requires: it pushes a small RC cantilever *directly*,
records the section-deformation history and the per-fiber ``stressStrain``
response at the same time, then replays the recorded history through a fresh
copy of the section and asserts the replayed stress-strain matches the direct
recording.
"""

from __future__ import annotations

import numpy as np
import pytest

from fea_toolkit.model.sap_data import ConcreteRectangularSection
from fea_toolkit.opensees.fiber_replay import replay_fiber_section


def _build_and_push():
    """Push a 3D RC cantilever directly; return the replay inputs + reference.

    Returns a 6-tuple ``(patches, materials, J, deform_history, direct_stress,
    direct_strain)`` where the *direct* arrays are the per-step per-fiber
    stress/strain recorded from the live element, and *deform_history* is the
    per-step section-deformation vector of section 1 (``[ε, κ_z, κ_y, φ]``).
    """
    import openseespy.opensees as ops

    sec = ConcreteRectangularSection(
        name="COL",
        shape="Concrete Rectangular",
        material="C30",
        rebar_material="Rebar",
        A=0.09,
        I33=6.75e-4,
        I22=6.75e-4,
        J=1.14e-3,
        depth=0.3,
        bf=0.3,
        cover=0.04,
        top_bars=4,
        bot_bars=4,
        top_bar_dia=0.016,
        bot_bar_dia=0.016,
    )
    patches = sec.to_fiber_patches(mat_tag=1)
    fpc, fy, es = 3.0e7, 400e3, 2.0e8
    materials = {
        1: ("Concrete01", (-fpc, -0.002, -0.2 * fpc, -0.006)),
        2: ("Concrete01", (-1.25 * fpc, -0.0025, -0.3 * 1.25 * fpc, -0.02)),
        3: ("Steel02", (fy, es, 0.01, 18.0, 0.925, 0.15)),
    }

    try:
        ops.wipe()
        ops.model("basic", "-ndm", 3, "-ndf", 6)
        ops.node(1, 0.0, 0.0, 0.0)
        ops.node(2, 3.0, 0.0, 0.0)  # 3 m cantilever along global X
        ops.fix(1, 1, 1, 1, 1, 1, 1)
        for tag, (mat_type, args) in materials.items():
            ops.uniaxialMaterial(mat_type, tag, *args)
        ops.section("Fiber", 1, "-GJ", sec.J)
        for entry in patches:
            if entry[0] in ("rect", "circ", "quad"):
                ops.patch(*entry)
            elif entry[0] == "straight":
                ops.layer("straight", *entry[1:])
            elif entry[0] == "circ_layer":
                ops.layer("circ", *entry[1:])
        # Local axes align with global (element along X, vecxz = global z).
        ops.geomTransf("PDelta", 1, 0, 0, 1)
        ops.beamIntegration("Lobatto", 10001, 1, 3)
        ops.element("dispBeamColumn", 1, 1, 2, 1, 10001)

        # Reference lateral load (Y) so DisplacementControl has a load vector.
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        ops.load(2, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
        ops.system("UmfPack")
        ops.numberer("RCM")
        ops.constraints("Plain")
        ops.test("NormDispIncr", 1e-6, 30)
        ops.algorithm("Newton")
        # Push the tip laterally (Y) in 5 mm increments → κ_z bending, into the
        # nonlinear concrete range (5 % drift ≈ 0.75 % extreme-fiber strain).
        ops.integrator("DisplacementControl", 2, 2, 0.005)
        ops.analysis("Static")

        n_fiber = 0
        while True:
            ss = ops.eleResponse(1, "section", 1, "fiber", n_fiber, "stressStrain")
            if not ss or len(ss) < 2:
                break
            n_fiber += 1

        deform_history: list[list[float]] = []
        direct_stress: list[list[float]] = []
        direct_strain: list[list[float]] = []
        for _ in range(20):
            if ops.analyze(1) != 0:
                break
            deform_history.append(list(ops.eleResponse(1, "section", 1, "deformation")))
            srow: list[float] = []
            erow: list[float] = []
            for i in range(n_fiber):
                ss = ops.eleResponse(1, "section", 1, "fiber", i, "stressStrain")
                srow.append(float(ss[0]))
                erow.append(float(ss[1]))
            direct_stress.append(srow)
            direct_strain.append(erow)
    finally:
        ops.wipe()

    return (
        patches,
        materials,
        sec.J,
        np.asarray(deform_history, dtype=float),
        np.asarray(direct_stress, dtype=float),
        np.asarray(direct_strain, dtype=float),
    )


def test_replay_matches_direct_recording():
    """Replayed fiber stress-strain must reproduce the direct recording."""
    patches, materials, J, deform_history, direct_stress, direct_strain = _build_and_push()

    assert deform_history.shape[1] == 4
    assert len(deform_history) > 10, "push should produce a non-trivial history"

    result = replay_fiber_section(patches, materials, deform_history, J)

    assert result["n_fiber"] == direct_stress.shape[1]
    assert result["stress"].shape == direct_stress.shape
    assert result["strain"].shape == direct_strain.shape

    # Fiber strain is reconstructed exactly (ε + κ·position); stress is the
    # path-dependent material response to that strain history, so both should
    # match the direct recording to numerical precision.
    np.testing.assert_allclose(result["strain"], direct_strain, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(result["stress"], direct_stress, rtol=1e-8, atol=1e-2)


def test_replay_rejects_bad_history_shape():
    """A non-(n_step, 4) history is rejected before touching OpenSees."""
    patches = []
    materials = {1: ("Steel01", (400e3, 2.0e8, 0.01))}
    with pytest.raises(ValueError, match="n_step, 4"):
        replay_fiber_section(patches, materials, [[0.0, 0.0, 0.0]], J=1.0)
