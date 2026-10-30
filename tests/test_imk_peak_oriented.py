"""``IMKPeakOriented`` (peak-oriented IMK) hinge material — argument mapping + build.

The peak-oriented Ibarra–Medina–Krawinkler material is the IK-2005 (PEER
2005/06) reference hysteretic law.  Its OpenSees signature is ``IMKBilin``'s
21-argument backbone plus the accelerated-reloading deterioration mode
(``Lamda_A``, ``c_A``), so ``_imk_peak_oriented_args`` reuses
``_imk_bilin_args`` and inserts those two values.
"""

from fea_toolkit.opensees._elements import _imk_bilin_args, _imk_peak_oriented_args


def test_imk_peak_oriented_args_order_and_defaults():
    args = _imk_peak_oriented_args({}, k0=1e8, my_pos=500e3, my_neg=-500e3)
    assert len(args) == 23
    assert args[0] == 1e8  # Ke
    assert args[4] == 500e3  # Fy_pos
    assert args[10] == 500e3  # Fy_neg (positive magnitude)
    assert args[15] == 0.0  # Lamda_A defaults to 0 (no accelerated reloading)
    assert args[19] == 1.0  # c_A defaults to 1
    assert args[21] == 1.0  # D_pos
    assert args[22] == 1.0  # D_neg


def test_imk_peak_oriented_inserts_lambda_a_and_c_a():
    params = {"lamda_A": 3.2, "c_A": 0.7}
    base = _imk_bilin_args(params, k0=1e8, my_pos=500e3, my_neg=-500e3)
    peak = _imk_peak_oriented_args(params, k0=1e8, my_pos=500e3, my_neg=-500e3)
    # Everything up to Lamda_C is shared, then Lamda_A is inserted.
    assert peak[:15] == base[:15]
    assert peak[15] == 3.2  # Lamda_A
    assert peak[16:19] == base[15:18]  # Lamda_K, c_S, c_C
    assert peak[19] == 0.7  # c_A
    assert peak[20:] == base[18:]  # c_K, D_pos, D_neg


def test_imk_peak_oriented_lumped_hinge_builds():
    """``hinge_backbone='imk_peak'`` builds a lumped-hinge domain with stamped params."""
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks import sac
    from fea_toolkit.benchmarks.imk_calibration import SteelIMKParams
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
        b = AnalysisBuilder(mesh, sac.builder_config("imk_peak", "lumped"))
        b.build_domain()
        assert b is not None
    finally:
        wipe()
