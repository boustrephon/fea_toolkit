"""A2 — single-member IMK (IMKBilin) cyclic M-θ validation vs the calibrated backbones.

The A0 calibration returns the *expected* backbone (``My``, ``Mc = a*·My``,
``θp``, ``θpc``, ``Mr = κ·My``) for a section; A1 builds it as an OpenSees
``IMKBilin`` material.  These tests drive a single ``IMKBilin`` spring through
monotonic and cyclic rotation histories (``DisplacementControl``) and assert the
*measured* M-θ loop reproduces the calibrated backbone.

See ``docs/imk_deterioration_model.md`` (§5, A2).
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.benchmarks.imk_calibration import (
    SteelIMKParams,
    lignos_2019_wide_flange_column,
    lignos_krawinkler_2011,
)


# ── Material-level drivers (DisplacementControl) ────────────────────────
def _build_bilin_spring(params: SteelIMKParams, k0: float) -> None:
    """Two-node model: node 1 fixed; node 2 (rz free) with a unit reference moment."""
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    ops.node(1, 0.0, 0.0)
    ops.node(2, 0.0, 0.0)
    ops.fix(1, 1, 1, 1)
    ops.fix(2, 1, 1, 0)
    ops.uniaxialMaterial("IMKBilin", 1, *params.as_bilin_args(k0))
    ops.element("zeroLength", 1, 1, 2, "-mat", 1, "-dir", 3)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, 1.0)  # unit reference moment on rz
    ops.constraints("Plain")
    ops.numberer("Plain")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-10, 100, 0)
    ops.algorithm("Newton")


def _record(rotations: list, moments: list) -> None:
    rotations.append(float(ops.nodeDisp(2, 3)))
    # zeroLength force = [N_i, V_i, M_i, N_j, V_j, M_j]; [5] is the j-end moment
    # (signed with the rotation direction — [2] is the negative i-end reaction).
    moments.append(float(ops.eleResponse(1, "force")[5]))


def _run_bilin_monotonic(params, k0, theta_max, n_steps=400):
    """Push the spring rotation 0 → theta_max; return (rotations, moments)."""
    _build_bilin_spring(params, k0)
    ops.integrator("DisplacementControl", 2, 3, theta_max / n_steps)
    ops.analysis("Static")
    rotations, moments = [], []
    for _ in range(n_steps):
        if ops.analyze(1) != 0:
            break
        _record(rotations, moments)
    return rotations, moments


def _run_bilin_cyclic(params, k0, amplitude, n_cycles, n_half=30):
    """Cycle the spring ±amplitude n_cycles times; return (rotations, moments)."""
    _build_bilin_spring(params, k0)
    dtheta = amplitude / n_half
    ops.integrator("DisplacementControl", 2, 3, dtheta)
    ops.analysis("Static")
    rotations, moments = [], []
    for _ in range(n_cycles):
        for _ in range(n_half):  # push up to +amplitude
            if ops.analyze(1) != 0:
                return rotations, moments
            _record(rotations, moments)
        ops.integrator("DisplacementControl", 2, 3, -dtheta)
        for _ in range(2 * n_half):  # push down to -amplitude
            if ops.analyze(1) != 0:
                return rotations, moments
            _record(rotations, moments)
        ops.integrator("DisplacementControl", 2, 3, dtheta)
        for _ in range(n_half):  # push back up to 0
            if ops.analyze(1) != 0:
                return rotations, moments
            _record(rotations, moments)
    return rotations, moments


def _extract_backbone(rotations, moments, my, k0) -> dict:
    """Measured backbone quantities from a monotonic M-θ history."""
    theta_y = my / k0
    mc = max(moments)
    i_mc = moments.index(mc)
    theta_mc = rotations[i_mc]
    mr = moments[-1]  # protocol ends deep in the residual plateau
    theta_mr = None
    for t, m in zip(rotations[i_mc:], moments[i_mc:]):
        # IMKBilin's post-capping is concave (drops fast then flattens to Mr at
        # θp+θpc); use a tight tolerance so θpc is measured at the true knee.
        if m <= mr * 1.001:
            theta_mr = t
            break
    return {
        "Mc": mc,
        "Mr": mr,
        "theta_p": theta_mc - theta_y,
        "theta_pc": (theta_mr - theta_mc) if theta_mr is not None else float("nan"),
    }


def _assert_close(measured, expected, tol_abs, tol_rel):
    assert measured == pytest.approx(expected, abs=tol_abs, rel=tol_rel)


# ── Representative SAC member geometry (AISC, m) ─────────────────────────
W30X116 = {
    "section_depth_m": 0.762,
    "web_thickness_m": 0.0144,
    "flange_width_m": 0.267,
    "flange_thickness_m": 0.0216,
    "radius_of_gyration_m": 0.0513,
    "unbraced_length_m": 3.0,
    "member_length_m": 6.0,
    "fy_pa": 248e6,
    "plastic_modulus_m3": 6.194e-3,
}

W14X257 = {
    "section_depth_m": 0.417,
    "web_thickness_m": 0.0435,
    "flange_thickness_m": 0.0439,
    "radius_of_gyration_m": 0.106,
    "unbraced_length_m": 3.6,
    "fy_pa": 345e6,
    "plastic_modulus_m3": 7.98e-3,
}


# ── A2a: monotonic backbone ─────────────────────────────────────────────
def test_bilin_material_backbone_beam():
    """The IMKBilin spring reproduces the calibrated W30x116 beam backbone."""
    params = lignos_krawinkler_2011(**W30X116)
    k0 = params.My_pos / 0.004  # θy = 0.004 rad (measurable knee)
    theta_max = params.theta_u_pos - 0.005  # on the residual plateau (before fracture)
    try:
        rot, mom = _run_bilin_monotonic(params, k0, theta_max)
        assert len(mom) > 0, "monotonic analysis failed to converge"
        b = _extract_backbone(rot, mom, params.My_pos, k0)
        _assert_close(b["Mc"], params.mc_my * params.My_pos, 0.0, 0.02)
        _assert_close(b["Mr"], params.res_pos * params.My_pos, 0.0, 0.02)
        _assert_close(b["theta_p"], params.theta_p_pos, 0.001, 0.05)
        _assert_close(b["theta_pc"], params.theta_pc_pos, 0.001, 0.05)
    finally:
        ops.wipe()


def test_bilin_material_backbone_column():
    """The IMKBilin spring reproduces the calibrated W14x257 column backbone."""
    params = lignos_2019_wide_flange_column(axial_ratio=0.15, **W14X257)
    k0 = params.My_pos / 0.004
    theta_max = params.theta_u_pos - 0.005  # on the residual plateau (before fracture)
    try:
        rot, mom = _run_bilin_monotonic(params, k0, theta_max)
        assert len(mom) > 0, "monotonic analysis failed to converge"
        b = _extract_backbone(rot, mom, params.My_pos, k0)
        _assert_close(b["Mc"], params.mc_my * params.My_pos, 0.0, 0.02)
        _assert_close(b["Mr"], params.res_pos * params.My_pos, 0.0, 0.02)
        _assert_close(b["theta_p"], params.theta_p_pos, 0.001, 0.05)
        _assert_close(b["theta_pc"], params.theta_pc_pos, 0.001, 0.05)
    finally:
        ops.wipe()


# ── A2a: cyclic deterioration ───────────────────────────────────────────
def test_bilin_material_cyclic_deterioration():
    """Repeated post-capping cycles degrade the peak moment (IMK rule)."""
    params = lignos_krawinkler_2011(**W30X116)
    k0 = params.My_pos / 0.004
    amplitude = 0.004 + params.theta_p_pos + 0.3 * params.theta_pc_pos  # just past Mc
    try:
        _, mom = _run_bilin_cyclic(params, k0, amplitude, n_cycles=4)
        assert len(mom) > 0, "cyclic analysis failed to converge"
        half = len(mom) // 2
        peak_first = max(abs(m) for m in mom[:half])
        peak_last = max(abs(m) for m in mom[half:])
        assert peak_first == pytest.approx(params.mc_my * params.My_pos, rel=0.02)
        assert peak_last < peak_first  # strength deterioration
        assert peak_last > params.res_pos * params.My_pos * 0.9  # bounded above residual
    finally:
        ops.wipe()


# ── A2c: SAC member parameter sanity ────────────────────────────────────
def test_sac_imk_params_plausible_ranges():
    """θp/θpc/Λ for the SAC 3-story sections land in physically plausible ranges."""
    sections = [
        ("W14x257-col", lignos_2019_wide_flange_column(axial_ratio=0.15, **W14X257), False),
        ("W30x116-beam", lignos_krawinkler_2011(**W30X116), True),
    ]
    for name, p, is_beam in sections:
        assert p.My_pos > 0.0, name
        assert 0.005 <= p.theta_p_pos <= 0.12, (name, p.theta_p_pos)
        assert 0.02 <= p.theta_pc_pos <= 0.20, (name, p.theta_pc_pos)
        if is_beam:
            assert 0.1 <= p.lamda_S <= 3.0, (name, p.lamda_S)
        else:
            assert p.lamda_S == 0.0  # column Λ not in the 2019 paper
        assert p.mc_my >= 1.0, name
        assert 0.0 <= p.res_pos <= 0.6, name
