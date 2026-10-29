"""IMK (IMKBilin) steel-hinge machinery (A1) and the A0 beam/column calibration.

The Lignos-Krawinkler (2011) *beam* equations are transcribed (from Lignos'
EPFL component tool) and the Lignos et al. (2019) *column* equations (the
monotonic backbone).  These tests cover the ``SteelIMKParams`` -> ``IMKBilin``
argument mapping, the beam and column regressions, the ``theta_u`` conventions,
and the ``hinge_backbone='imk'`` lumped-hinge build.
"""

import pytest

from fea_toolkit.benchmarks.imk_calibration import (
    IMK_PARAM_KEYS,
    SteelIMKParams,
    _beam_rotations,
    _resolve_theta_u,
    lignos_2019_wide_flange_column,
    lignos_krawinkler_2011,
)
from fea_toolkit.opensees._elements import _imk_bilin_args


def test_imk_bilin_args_order_and_defaults():
    args = _imk_bilin_args({}, k0=1e8, my_pos=500e3, my_neg=-500e3)
    assert len(args) == 21
    assert args[0] == 1e8  # Ke
    assert args[4] == 500e3  # Fy_pos
    assert args[5] == 1.0  # FmaxFy_pos (mc_my defaults to 1.0)
    assert args[10] == 500e3  # Fy_neg (positive magnitude)
    assert args[19] == 1.0  # D_pos
    assert args[20] == 1.0  # D_neg


def test_capping_ratio_passed_directly():
    # mc_my (FmaxFy) is passed straight through to IMKBilin — no derived 'as'.
    args = _imk_bilin_args(
        {"mc_my": 1.05, "theta_p_pos": 0.02, "theta_p_neg": 0.02},
        k0=1e8,
        my_pos=500e3,
        my_neg=-500e3,
    )
    assert args[5] == pytest.approx(1.05)  # FmaxFy_pos
    assert args[11] == pytest.approx(1.05)  # FmaxFy_neg


def test_theta_u_falls_back_to_theta_p_plus_pc():
    args = _imk_bilin_args(
        {"theta_p_pos": 0.02, "theta_pc_pos": 0.08},
        k0=1e8,
        my_pos=500e3,
        my_neg=-500e3,
    )
    assert args[3] == 0.10  # du_pos falls back to theta_p + theta_pc


def test_beam_rotations_prefactor_identity():
    # All dimensionless ratios = 1 with d = 533 mm and Fy = 355 MPa, so each
    # regression evaluates to its leading coefficient.
    tp, tpc, lam = _beam_rotations(1.0, 1.0, 1.0, 1.0, 533.0, 355.0, rbs=True)
    assert tp == pytest.approx(0.19)
    assert tpc == pytest.approx(9.52)
    assert lam == pytest.approx(585.0)
    # d >= 533 mm other-than-RBS branch (d = 533 is the boundary).
    tp, tpc, lam = _beam_rotations(1.0, 1.0, 1.0, 1.0, 533.0, 355.0, rbs=False)
    assert tp == pytest.approx(0.318)
    assert tpc == pytest.approx(7.5)
    assert lam == pytest.approx(536.0)


def test_beam_theta_p_decreases_with_web_slenderness():
    # More slender web (higher h/tw) -> smaller pre-capping rotation.
    tp_slender, _, _ = _beam_rotations(60.0, 8.0, 50.0, 6.0, 500.0, 355.0, rbs=False)
    tp_stocky, _, _ = _beam_rotations(20.0, 8.0, 50.0, 6.0, 500.0, 355.0, rbs=False)
    assert tp_slender < tp_stocky


def test_lignos_krawinkler_2011_beam():
    p = lignos_krawinkler_2011(
        section_depth_m=0.5,
        web_thickness_m=0.01,
        flange_width_m=0.2,
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.08,
        unbraced_length_m=3.0,
        member_length_m=6.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
    )
    assert p.My_pos == pytest.approx(345e6 * 2.0e-3)
    assert p.My_neg == -p.My_pos
    assert p.theta_p_pos > 0.0
    assert p.theta_pc_pos > 0.0
    assert p.lamda_S == p.lamda_C == p.lamda_K > 0.0
    assert p.res_pos == pytest.approx(0.4)
    assert p.theta_u_pos == pytest.approx(p.theta_p_pos + p.theta_pc_pos + 0.02)
    assert set(p.to_dict()) == set(IMK_PARAM_KEYS)


def test_lignos_2019_column_prefactor_and_caps():
    # h/tw = 1, Lb/ry = 1, Pg/Pye = 0: each regression hits its prefactor/cap.
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.48,
        flange_thickness_m=0.01,
        radius_of_gyration_m=1.0,
        unbraced_length_m=1.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.0,
    )
    assert p.mc_my == pytest.approx(1.3)  # a = 12.5 capped to 1.3 (Eq. 3)
    assert p.theta_p_pos == pytest.approx(0.20)  # 294 capped to 0.20 (Eq. 7)
    assert p.theta_pc_pos == pytest.approx(0.30)  # 90 capped to 0.30 (Eq. 9)
    assert p.res_pos == pytest.approx(0.5)  # 0.5 - 0 (Eq. 5)


def test_lignos_2019_column_equations():
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.01,
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.08,
        unbraced_length_m=3.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.1,
    )
    # Eq. 2: My = 1.15·Z·Ry·Fy·(1 - Pg/Pye), Ry = 1.1 default, Pg/Pye <= 0.2
    assert p.My_pos == pytest.approx(1.15 * 2.0e-3 * 1.1 * 345e6 * (1 - 0.1))
    assert p.My_neg == -p.My_pos
    assert p.mc_my >= 1.0
    # Eqs. 7 & 9: monotonic-backbone pre-/post-capping plastic rotations
    # (h/tw = 0.46/0.01 = 46, Lb/ry = 3.0/0.08 = 37.5, 1 − Pg/Pye = 0.9).
    assert p.theta_p_pos == pytest.approx(294.0 * 46.0**-1.7 * 37.5**-0.7 * 0.9**1.6)
    assert p.theta_pc_pos == pytest.approx(90.0 * 46.0**-0.8 * 37.5**-0.8 * 0.9**2.5)
    assert p.res_pos == pytest.approx(0.5 - 0.4 * 0.1)
    # Eq. 13: column cyclic-deterioration Λ (Λc = Λk = 0.9·Λs), capped at 3.0.
    assert p.lamda_S == pytest.approx(25500.0 * 46.0**-2.14 * 37.5**-0.53 * 0.9**4.92)
    assert p.lamda_C == pytest.approx(0.9 * p.lamda_S)
    assert p.lamda_K == pytest.approx(0.9 * p.lamda_S)


def test_lignos_2019_column_monotonic_capping_ratio():
    """Eq. 3: a = 12.5·(h/tw)^-0.2·(Lb/ry)^-0.4·(1−Pg/Pye)^0.4, unclamped here."""
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.01,  # h/tw = 46
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.05,  # Lb/ry = 3.0/0.05 = 60
        unbraced_length_m=3.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.1,  # 1 − Pg/Pye = 0.9
    )
    # Raw a ≈ 1.08, inside (1.0, 1.3) — the cap does not bind.
    assert p.mc_my == pytest.approx(12.5 * 46.0**-0.2 * 60.0**-0.4 * 0.9**0.4)
    assert 1.0 < p.mc_my < 1.3


def test_resolve_theta_u_conventions():
    """Each named ``theta_u_model`` convention resolves to the documented value."""
    # Slender (θp + θpc = 0.08 < 0.15): hybrid floors at the paper's 0.15 rad.
    assert _resolve_theta_u(None, "plateau", 0.03, 0.05) == pytest.approx(0.10)
    assert _resolve_theta_u(None, "none", 0.03, 0.05) == pytest.approx(0.08)
    assert _resolve_theta_u(None, "lignos", 0.03, 0.05) == pytest.approx(0.15)
    assert _resolve_theta_u(None, "hybrid", 0.03, 0.05) == pytest.approx(0.15)
    # Stocky (θp + θpc = 0.20 > 0.15): hybrid keeps the plateau convention.
    assert _resolve_theta_u(None, "plateau", 0.10, 0.10) == pytest.approx(0.22)
    assert _resolve_theta_u(None, "none", 0.10, 0.10) == pytest.approx(0.20)
    assert _resolve_theta_u(None, "lignos", 0.10, 0.10) == pytest.approx(0.15)
    assert _resolve_theta_u(None, "hybrid", 0.10, 0.10) == pytest.approx(0.22)


def test_resolve_theta_u_explicit_override_wins():
    """An explicit ``theta_u`` value takes precedence over the convention."""
    assert _resolve_theta_u(0.30, "plateau", 0.03, 0.05) == pytest.approx(0.30)
    assert _resolve_theta_u(0.30, "lignos", 0.03, 0.05) == pytest.approx(0.30)


def test_resolve_theta_u_rejects_unknown_model():
    with pytest.raises(ValueError, match="theta_u_model"):
        _resolve_theta_u(None, "bogus", 0.03, 0.05)


def test_resolve_theta_u_rejects_theta_u_not_exceeding_theta_p():
    """``lignos`` (0.15 rad) and an explicit ``theta_u`` are rejected when they
    do not exceed the capping rotation (a stocky section)."""
    with pytest.raises(ValueError, match="theta_p"):
        _resolve_theta_u(None, "lignos", 0.20, 0.30)  # 0.15 < 0.20
    with pytest.raises(ValueError, match="theta_p"):
        _resolve_theta_u(0.10, "plateau", 0.20, 0.30)  # explicit 0.10 < 0.20


def test_resolve_theta_u_rejects_nan():
    """A NaN ultimate rotation never compares greater than theta_p."""
    with pytest.raises(ValueError, match="theta_p"):
        _resolve_theta_u(float("nan"), "plateau", 0.03, 0.05)


def test_column_theta_u_model_records_convention():
    """The convention is recorded on ``SteelIMKParams`` and drives ``theta_u``."""
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.01,
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.08,
        unbraced_length_m=3.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.1,
        theta_u_model="lignos",
    )
    assert p.theta_u_model == "lignos"
    assert p.theta_u_pos == pytest.approx(0.15)
    assert p.theta_u_neg == pytest.approx(0.15)


def test_lignos_2019_column_cyclic_lambda_above_axial_split():
    """Eq. 13's second branch (Pg/Pye > 0.35) — a numeric value below the cap."""
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.01,
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.08,
        unbraced_length_m=3.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.5,
    )
    # h/tw = 46, Lb/ry = 37.5, 1 − Pg/Pye = 0.5 → the Pg/Pye > 0.35 branch.
    assert p.lamda_S == pytest.approx(268000.0 * 46.0**-2.30 * 37.5**-1.30 * 0.5**1.19)
    assert p.lamda_C == pytest.approx(0.9 * p.lamda_S)
    assert p.lamda_K == pytest.approx(0.9 * p.lamda_S)
    assert p.lamda_S < 3.0  # below the cap; the cap itself is tested separately


def test_lignos_2019_column_cyclic_lambda_cap():
    # Eq. 13: with all slenderness/axial ratios = 1 (Pg/Pye = 0), Λs = 25,500,
    # which is capped at 3.0; Λc = Λk = 0.9·Λs.
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.48,  # h/tw = 1
        flange_thickness_m=0.01,
        radius_of_gyration_m=1.0,  # Lb/ry = 1
        unbraced_length_m=1.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.0,  # 1 − Pg/Pye = 1
    )
    assert p.lamda_S == pytest.approx(3.0)  # capped
    assert p.lamda_C == pytest.approx(2.7)  # 0.9 · 3.0
    assert p.lamda_K == pytest.approx(2.7)


def test_lignos_2019_column_yield_moment_axial_gt_0_2():
    p = lignos_2019_wide_flange_column(
        section_depth_m=0.5,
        web_thickness_m=0.01,
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.08,
        unbraced_length_m=3.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.3,
    )
    # Eq. 2 (Pg/Pye > 0.2): My = 1.15·Z·Ry·Fy·(9/8)(1 - Pg/Pye)
    assert p.My_pos == pytest.approx(1.15 * 2.0e-3 * 1.1 * 345e6 * (9 / 8) * (1 - 0.3))


def test_lignos_krawinkler_2011_column_delegates():
    p = lignos_krawinkler_2011(
        section_depth_m=0.5,
        web_thickness_m=0.01,
        flange_width_m=0.2,
        flange_thickness_m=0.02,
        radius_of_gyration_m=0.08,
        unbraced_length_m=3.0,
        member_length_m=6.0,
        fy_pa=345e6,
        plastic_modulus_m3=2.0e-3,
        axial_ratio=0.1,
        column=True,
    )
    assert p.res_pos == pytest.approx(0.5 - 0.4 * 0.1)
    assert p.mc_my >= 1.0
    assert 0.0 < p.lamda_S <= 3.0


def test_imk_hinge_builds():
    """``hinge_backbone='imk'`` builds a lumped-hinge domain with stamped params."""
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks import sac
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.opensees.preprocessor import preprocess_model

    wipe()
    try:
        md = sac.load_model(nonlinear=True)
        params = SteelIMKParams(
            My_pos=500e3,
            My_neg=-500e3,
            theta_p_pos=0.02,
            theta_p_neg=0.02,
            theta_pc_pos=0.08,
            theta_pc_neg=0.08,
            res_pos=0.4,
            res_neg=0.4,
            lamda_S=0.6,
            lamda_C=0.6,
            lamda_K=0.6,
        ).to_dict()
        for sec in md.sections.values():
            sec.imk_params = dict(params)
        mesh = preprocess_model(md, sac.preprocess_config())
        b = AnalysisBuilder(mesh, sac.builder_config("imk", "lumped"))
        b.build_domain()
        assert b is not None
    finally:
        wipe()
