"""E-Defense 5-storey base-isolated frame (TPB) — triple pendulum benchmark.

Reproduces the triple friction pendulum (TPB) configuration of the 2011
NEES/E-Defense full-scale shake-table test (Dao & Ryan 2015, CCEER 15-01):
nine identical EPS triple pendulum bearings under the 3x3 column grid.

Published reference values (Dao & Ryan 2015, CCEER 15-01):

====================  ============  =========================================
Quantity              Published     This model (target)
====================  ============  =========================================
Isolator μ            (0.02,        ``TPB_MU`` (Table 3-8)
                      0.08, 0.08)
Effective radii       (0.419,       ``TPB_RADIUS`` = R - h (Table 3-8)
                      3.848, 3.848)
Slider limits         (0.051,       ``TPB_UBAR`` (Table 3-8)
                      0.514, 0.514)
Displacement capacity 1.13 m        ``≈ 1.13 m`` at F/W = 0.275 (backbone push)
First-slope period    1.84 s        ``1.7–2.2 s`` (isolator + superstructure)
Peak isolator disp.   38.36 cm      0.05–0.7 m (JMA-Kobe stand-in; the 100 %
                      (100% Iwanuma) Iwanuma record is pending K-NET download)
====================  ============  =========================================

The element is OpenSees' ``TripleFrictionPendulum`` (Dao et al. 2013), whose
``L`` are the effective radii and ``Ubar`` the slider displacement limits; the
backbone push below reproduces the published base-shear coefficient of 0.275 at
the 1.13 m capacity.
"""

import numpy as np
import pytest
from openseespy.opensees import wipe

from fea_toolkit.benchmarks import e_defense as ed


def _push_tpb(weight: float = 1000.0, u_lat: float = 1.15) -> tuple[np.ndarray, np.ndarray]:
    """Gravity-load a single TPB and push it laterally; return ``(disp, force)``.

    ``force`` is the local shear (``localForce`` index 2) in the pushed
    direction; ``weight`` is the vertical (axial) load in kN.
    """
    import openseespy.opensees as ops

    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 0.0)  # zero-length bearing
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 0, 0, 1, 1, 1)  # free translations, fixed rotations
    for i, k in enumerate((ed.TPB_KV, 1.0e6, 1.0e6, 1.0e6), start=10):
        ops.uniaxialMaterial("Elastic", i, k)  # matP / matT / matMy / matMz
    for i, m in enumerate(ed.TPB_MU, start=1):
        ops.frictionModel("Coulomb", i, m)
    ops.element(
        "TripleFrictionPendulum",
        1,
        1,
        2,
        1,
        2,
        3,
        10,
        11,
        12,
        13,
        *ed.TPB_RADIUS,
        *ed.TPB_UBAR,
        weight,
        ed.TPB_UY,
        ed.TPB_KV,
        ed.TPB_MIN_FV,
        1.0e-8,
    )
    # Gravity (axial = local x = global z).
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -weight, 0.0, 0.0, 0.0)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-10, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    assert ops.analyze(1) == 0, "gravity stage failed"
    ops.loadConst("-time", 0.0)
    # Lateral push (dof 2 = y), displacement-controlled.
    ops.pattern("Plain", 2, 1)
    ops.load(2, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    ops.integrator("DisplacementControl", 2, 2, 0.001)
    ops.analysis("Static")
    disp: list[float] = []
    force: list[float] = []
    for _ in range(int(u_lat / 0.001)):
        # A failed step must fail the benchmark, not silently truncate the
        # push history (which would let the capacity check pass at an early
        # displacement).
        assert ops.analyze(1) == 0, "lateral push failed"
        disp.append(float(ops.nodeDisp(2, 2)))
        force.append(float(list(ops.eleResponse(1, "localForce")[:6])[2]))
    return np.asarray(disp), np.asarray(force)


def test_tpb_backbone_capacity():
    """The TPB backbone reaches 0.275 W at its 1.13 m displacement capacity."""
    try:
        disp, force = _push_tpb(u_lat=1.15)
        # The push must reach the target displacement — a truncated history
        # (from a failed step) is not a valid capacity check.
        assert disp[-1] >= 1.149, f"push stopped at {disp[-1]:.3f} m, expected 1.15 m"
        f = np.abs(force)
        i_cap = int(np.argmin(np.abs(disp - 1.13)))
        assert f[i_cap] / 1000.0 == pytest.approx(0.275, rel=0.03)
        # The circular gap locks just past the capacity — the force jumps.
        i_lock = int(np.argmin(np.abs(disp - 1.15)))
        assert f[i_lock] > 1.5 * f[i_cap]
    finally:
        wipe()


def test_tpb_isolated_period():
    """Isolated first-slope period ≈ 1.84–2.2 s (isolator + superstructure)."""
    wipe()
    try:
        b = ed.build_builder(isolated=True, config="tpb", link_model="elastic")
        res = b.run_modal_analysis(num_modes=6)
        mp = res["modal_props"]
        periods = mp["eigenPeriod"][:2]  # the two horizontal translational modes
        assert all(1.7 <= p <= 2.2 for p in periods), f"isolated T = {periods}"
    finally:
        wipe()


@pytest.mark.needs_ground_motion("KOBE")
@pytest.mark.slow
def test_tpb_nltha_kobe():
    """NLTHA under JMA Kobe NS: the TPB system runs and isolates the frame.

    A sanity check that the ``TripleFrictionPendulum`` elements survive the
    gravity + transient stages and keep the peak isolator displacement within
    the tested range (the published 100 % Iwanuma peak is 38.4 cm).
    """
    wipe()
    try:
        accel, dt = ed.read_kobe_record()
        b = ed.build_builder(isolated=True, config="tpb", link_model="nonlinear")
        res = b.run_transient_analysis(
            accel,
            dt,
            direction="X",
            damping_ratio=0.02,
            period_1=2.0,
            period_2=0.7,
            gravity_patterns={"DEAD": 1.0},
        )
        assert res["converged_steps"] == len(accel)

        lv = ed.layout

        def tag(level, i, j):
            return b.mesh_model.nodes[lv.node_id(level, i, j)].node_tag

        d_base = res["displacements"][tag(0, 1, 1)][:, 0]
        iso = float(np.max(np.abs(d_base)))
        assert 0.05 <= iso <= 0.7, f"peak isolator displacement = {iso:.3f} m"
    finally:
        wipe()
