"""IMK (IMKBilin) steel-hinge machinery (A1) and the A0 beam calibration.

The Lignos-Krawinkler (2011) *beam* equations are transcribed (from Lignos'
EPFL component tool); *column* equations are not yet transcribed.  These tests
cover the ``SteelIMKParams`` -> ``IMKBilin`` argument mapping, the beam regression,
and the ``hinge_backbone='imk'`` lumped-hinge build.
"""

import pytest

from fea_toolkit.benchmarks.imk_calibration import (
    IMK_PARAM_KEYS,
    SteelIMKParams,
    _beam_rotations,
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
    assert p.mc_my == pytest.approx(9.5)  # a* (Eq. 7)
    assert p.theta_p_pos == pytest.approx(0.10)  # 15 capped to 0.10 (Eq. 8)
    assert p.theta_pc_pos == pytest.approx(0.10)  # 14 capped to 0.10 (Eq. 9)
    assert p.res_pos == pytest.approx(0.4)  # 0.4 - 0 (Eq. 10)


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
    # Eq. 6: My = 1.15·Z·Ry·Fy·(1 - Pg/Pye), Ry = 1.1 default, Pg/Pye <= 0.2
    assert p.My_pos == pytest.approx(1.15 * 2.0e-3 * 1.1 * 345e6 * (1 - 0.1))
    assert p.My_neg == -p.My_pos
    assert p.mc_my >= 1.0
    assert 0 < p.theta_p_pos <= 0.10
    assert 0 < p.theta_pc_pos <= 0.10
    assert p.res_pos == pytest.approx(0.4 - 0.4 * 0.1)
    # No column cyclic-deterioration Lambda in the 2019 paper.
    assert p.lamda_S == p.lamda_C == p.lamda_K == 0.0


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
    # Eq. 6 (Pg/Pye > 0.2): My = 1.15·Z·Ry·Fy·(9/8)(1 - Pg/Pye)
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
    assert p.res_pos == pytest.approx(0.4 - 0.4 * 0.1)
    assert p.mc_my >= 1.0
    assert p.lamda_S == 0.0


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
