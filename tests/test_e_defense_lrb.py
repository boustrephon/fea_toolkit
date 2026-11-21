"""E-Defense 5-storey base-isolated frame (LRB-CLB) — full-model shake-table check.

Reproduces the 2011 NEES/E-Defense full-scale shake-table test of a 5-storey
steel moment frame isolated by four lead-rubber bearings (LRB) and five
cross-linear bearings (CLB), and checks the published response quantities.

Published reference values (Sato et al. 2011; Ryan et al. 2012, WCEE paper
2054; Qin Dong et al. 2013, hdl.handle.net/2115/54236):

====================  ============  =======================================
Quantity              Published     This model (target)
====================  ============  =======================================
Fixed-base period     0.70 s        ``0.60–0.80 s`` (analysis 0.69 s, test 0.70 s)
Isolated period       2.86 s        ``2.6–3.1 s`` (post-yield, 4×KF)
Yield strength        V/W = 0.055   —
Peak isolator disp.   347 mm (100%   ``0.05–0.7 m`` (JMA-Kobe stand-in, a
                      Iwanuma)       lower-bound sanity check)
Peak base shear       1187 kN =      —
                      0.224 W
Story drift ratio     < 1/200       ``< 0.005``
====================  ============  =======================================

The measured isolator displacement / base shear are from Ryan et al. (2012,
WCEE paper 2054) for the 100 % Iwanuma motion; the overall peak displacement is
≤ 550 mm (Higashino et al., DOI 10.1002/eqe.3065).

The superstructure is linear-elastic (as in Qin Dong et al. 2013); the LRBs
use the manufacturer bilinear model (K0 = 6.5 kN/mm, KF = 0.65 kN/mm,
FU = 73 kN) emitted as ``elastomericBearingPlasticity``; the CLBs are
bidirectional rollers (vertical support only — their μ = 0.0019 friction is
two orders below the LRB yield).  The ground motion is the JMA Kobe
Observatory N-S record (``data/ground_motion/local/KOBE.csv``) — a stand-in of
comparable intensity, since the LRB-CLB test itself used non-redistributable
synthetic Vogtle / Diablo Canyon nuclear-site motions (Huang et al. 2009).
"""

import numpy as np
import pytest
from openseespy.opensees import wipe

from fea_toolkit.benchmarks import e_defense as ed


def _build(isolated, link_model):
    return ed.build_builder(isolated=isolated, link_model=link_model)


def _translational_periods(res, n=6):
    """Periods of the modes carrying the horizontal translational mass.

    A base-isolated model has a soft *rocking* mode (the isolators release all
    rotations) whose period is close to the isolation period but which carries
    no translational mass; selecting by mass participation (not by longest
    period) picks the true horizontal modes.
    """
    mp = res["modal_props"]
    periods = mp["eigenPeriod"][:n]
    mx = mp["partiMassMX"][:n]
    my = mp["partiMassMY"][:n]
    total = mp["totalMass"][0]
    return [p for p, x, y in zip(periods, mx, my) if (x + y) > 0.5 * total]


def test_fixed_base_period():
    """Fixed-base fundamental period ≈ 0.70 s (Qin Dong 2013)."""
    wipe()
    try:
        b = _build(isolated=False, link_model="elastic")
        periods = _translational_periods(b.run_modal_analysis(num_modes=6))
        # Exactly two horizontal fundamentals (X and Y) must be selected.
        assert len(periods) == 2, f"expected 2 horizontal modes, got {periods}"
        assert all(0.60 <= p <= 0.80 for p in periods), f"T1 = {periods}"
    finally:
        wipe()


def test_isolated_period():
    """Isolated post-yield period ≈ 2.86–2.90 s (Ryan 2012)."""
    wipe()
    try:
        b = _build(isolated=True, link_model="elastic")
        periods = _translational_periods(b.run_modal_analysis(num_modes=6))
        assert len(periods) == 2, f"expected 2 horizontal modes, got {periods}"
        assert all(2.6 <= p <= 3.1 for p in periods), f"isolated T = {periods}"
    finally:
        wipe()


@pytest.mark.needs_ground_motion("KOBE")
@pytest.mark.slow
def test_nltha_kobe():
    """NLTHA under JMA Kobe NS: peak isolator displacement and storey drift.

    The full 150 s record is integrated at dt = 0.02 s (7501 steps) with 2 %
    Rayleigh damping anchored at the isolated (2.9 s) and fixed-base (0.7 s)
    periods, gravity applied first so the isolators carry their axial load.
    """
    wipe()
    try:
        accel, dt = ed.read_kobe_record()
        b = _build(isolated=True, link_model="nonlinear")
        res = b.run_transient_analysis(
            accel,
            dt,
            direction="X",
            damping_ratio=0.02,
            period_1=2.9,
            period_2=0.7,
            gravity_patterns={"DEAD": 1.0},
        )
        assert res["converged_steps"] == len(accel)

        lv = ed.layout

        def tag(level, i, j):
            return b.mesh_model.nodes[lv.node_id(level, i, j)].node_tag

        # Peak isolator displacement — the base node's X displacement (the
        # ground node is fixed, so the base displacement *is* the isolator
        # shear deformation).
        d_base = res["displacements"][tag(0, 1, 1)][:, 0]
        iso = float(np.max(np.abs(d_base)))
        assert 0.05 <= iso <= 0.7, f"peak isolator displacement = {iso:.3f} m"

        # Peak storey drift ratio, relative to the floor below (paper: < 1/200).
        max_drift = 0.0
        for level in range(1, lv.n_storeys + 1):
            d_hi = res["displacements"][tag(level, 1, 1)][:, 0]
            d_lo = res["displacements"][tag(level - 1, 1, 1)][:, 0]
            h = lv.storey_heights[level - 1]
            max_drift = max(max_drift, float(np.max(np.abs(d_hi - d_lo))) / h)
        assert max_drift < 1.0 / 200.0, f"storey drift ratio = {max_drift:.4f}"
    finally:
        wipe()
