"""IMK (Bilin) steel-hinge machinery — A1 tests.

The Lignos-Krawinkler calibration (A0) is not yet transcribed; these tests
cover the *machinery*: the ``SteelIMKParams`` → ``Bilin`` argument mapping and
the ``hinge_backbone='imk'`` lumped-hinge build with hand-specified parameters.

See ``docs/imk_deterioration_model.md``.
"""

import pytest

from fea_toolkit.benchmarks.imk_calibration import (
    IMK_PARAM_KEYS,
    SteelIMKParams,
    lignos_krawinkler_2011,
)
from fea_toolkit.opensees._elements import _imk_bilin_args


def test_imk_bilin_args_order_and_defaults():
    args = _imk_bilin_args({}, k0=1e8, my_pos=500e3, my_neg=-500e3)
    assert len(args) == 23
    assert args[0] == 1e8  # K0
    assert args[1] == 0.03  # as_pos default
    assert args[2] == 0.03  # as_neg default
    assert args[3] == 500e3  # My_pos
    assert args[4] == -500e3  # My_neg (negative, per Bilin contract)
    assert args[5] == 0.0  # lamda_S default
    assert args[7] == 0.0  # lamda_A forced to 0 (unused for bilinear)
    assert args[11] == 1.0  # c_A forced to 1.0
    assert args[21] == 1.0  # D_pos
    assert args[22] == 1.0  # D_neg


def test_theta_u_falls_back_to_theta_p_plus_pc():
    args = _imk_bilin_args(
        {"theta_p_pos": 0.02, "theta_pc_pos": 0.08},
        k0=1e8,
        my_pos=500e3,
        my_neg=-500e3,
    )
    assert args[19] == 0.10  # theta_u_pos falls back to theta_p + theta_pc


def test_steel_imk_params_round_trip():
    p = SteelIMKParams(
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
    )
    d = p.to_dict()
    assert set(d) == set(IMK_PARAM_KEYS)
    assert d["My_pos"] == 500e3
    assert d["My_neg"] == -500e3
    args = p.as_bilin_args(k0=1e8)
    assert args[3] == 500e3  # My_pos
    assert args[4] == -500e3  # My_neg
    assert args[13] == 0.02  # theta_p_pos
    assert args[15] == 0.08  # theta_pc_pos
    assert args[17] == 0.4  # res_pos
    assert args[18] == 0.4  # res_neg
    assert args[5] == 0.6  # lamda_S


def test_lignos_krawinkler_2011_not_yet_transcribed():
    with pytest.raises(NotImplementedError):
        lignos_krawinkler_2011(
            section_depth_m=0.5,
            web_thickness_m=0.01,
            flange_width_m=0.2,
            flange_thickness_m=0.02,
            radius_of_gyration_m=0.08,
            unbraced_length_m=3.0,
            member_length_m=6.0,
            fy_pa=345e6,
        )


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
