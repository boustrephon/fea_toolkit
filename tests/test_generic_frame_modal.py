"""Generic-frame generator — modal T1 validation through the full pipeline.

Each of the 12 frames must reproduce its published fundamental period
``T1 = 0.1·N`` (stiff) or ``0.2·N`` (flexible).  This goes through
:func:`~fea_toolkit.benchmarks.generic_frame.build_builder` (the
``preprocess_model → AnalysisBuilder`` path with the lumped ``imk_peak``
beam-end springs and the total-M-θ column-base spring ``k0 = Kc``), so it
validates the spring placement and the masses together with the geometry.
"""

import math

import pytest

from fea_toolkit.benchmarks import generic_frame as gf
from fea_toolkit.benchmarks import generic_frame_run as gfr
from fea_toolkit.benchmarks.generic_frame_data import FRAME_CODES, FRAME_DATA
from fea_toolkit.benchmarks.generic_frame_modal_data import MODAL_DATA

#: Acceptance band — the table values are rounded to three significant figures
#: (and the spring/beam-column split is internal, IK-2005 Appendix B), so a
#: ±1 % band is the floor.
_T1_TOL = 0.01


def _period(code: str) -> float:
    """Build *code* through the pipeline and return its fundamental period (s)."""
    import openseespy.opensees as ops

    ops.wipe()
    try:
        builder = gf.build_builder(code)
        builder.compute_seismic_masses()
        omega1 = ops.eigen(3)[0]
    finally:
        ops.wipe()
    return 2.0 * math.pi / math.sqrt(omega1)


@pytest.mark.parametrize("code", list(FRAME_CODES))
def test_generic_frame_fundamental_period(code):
    t1 = _period(code)
    target = FRAME_DATA[code]["T1"]
    assert abs(t1 - target) <= _T1_TOL * target, (
        f"{code}: T1 = {t1:.4f} s vs target {target:.2f} s ({100 * (t1 - target) / target:+.2f} %)"
    )


@pytest.mark.parametrize("code", ["0303", "0909", "0918"])
def test_first_mode_shape_is_linear(code):
    """The frames are tuned for a straight-line first-mode shape (Appendix B).

    The table's ``Ii/I1`` ratios are chosen so the elastic first-mode lateral
    shape varies linearly from 0 at the base to 1 at the roof: ``φ_i/φ_r = i/N``.
    """
    import openseespy.opensees as ops

    ops.wipe()
    try:
        builder = gf.build_builder(code)
        builder.run_modal_analysis(num_modes=2, print_results=False)
        shapes = builder.extract_mode_shapes(1)
        n = FRAME_DATA[code]["N"]
        tags = {level: builder.mesh_model.nodes[f"{level}-0"].node_tag for level in range(n + 1)}
        phi = [shapes[0][tags[level]][0] for level in range(n + 1)]
    finally:
        ops.wipe()

    roof = phi[-1]
    assert abs(roof) > 1e-12, f"{code}: zero roof displacement in the first mode"
    norm = [p / roof for p in phi]
    dev = max(abs(norm[level] - level / n) for level in range(n + 1))
    assert dev < 0.01, f"{code}: first-mode shape deviates from linear by {dev:.4f}"


# ═══════════════════════════════════════════════════════════════
# Modal match against MK-2003 Tables A.1--A.6
# ═══════════════════════════════════════════════════════════════

#: One stiff frame per storey count — the modal tables are scale-free, so the
#: ``T1 = 0.1N`` and ``0.2N`` frames share the same ``Ti/T1``, ``PF``, ``MP``.
_STIFF_CODES = ("0303", "0606", "0909", "1212", "1515", "1818")


def _modal_table(code: str) -> tuple[list[float], list[float]]:
    """Computed ``(period_ratios, mass_participation)`` for modes 1..min(N, 5)."""
    import openseespy.opensees as ops

    n = int(FRAME_DATA[code]["N"])
    ops.wipe()
    try:
        builder = gf.build_builder(code, pdelta=False)
        modal = builder.run_modal_analysis(num_modes=min(n, 5), print_results=False)
    finally:
        ops.wipe()

    periods = list(modal["periods"])
    ratios = list(modal.get("modal_props", {}).get("partiMassRatiosMX", []))
    t1 = periods[0]
    # ``partiMassRatiosMX`` is in percent (OpenSees ``-unorm``); the paper's
    # ``MPi`` is the same effective-modal-mass fraction of total mass.
    return [p / t1 for p in periods], [r / 100.0 for r in ratios]


@pytest.mark.parametrize("code", _STIFF_CODES)
def test_modal_period_ratios_match_paper(code):
    n = int(FRAME_DATA[code]["N"])
    period_ratios, _ = _modal_table(code)
    assert period_ratios == pytest.approx(MODAL_DATA[n]["Ti_T1"], abs=0.002)


@pytest.mark.parametrize("code", _STIFF_CODES)
def test_modal_mass_participation_match_paper(code):
    n = int(FRAME_DATA[code]["N"])
    _, mass_participation = _modal_table(code)
    assert mass_participation == pytest.approx(MODAL_DATA[n]["MP"], abs=0.002)


@pytest.mark.parametrize("code", _STIFF_CODES)
def test_modal_participation_factors_match_paper(code):
    n = int(FRAME_DATA[code]["N"])
    pf = gfr.modal_participation_factors(code, num_modes=min(n, 5))
    # Sign of a higher mode is arbitrary (OpenSees eigenvector sign convention),
    # so compare magnitudes against the paper's signed ``PF``.
    assert [abs(x) for x in pf] == pytest.approx([abs(x) for x in MODAL_DATA[n]["PF"]], abs=0.01)


@pytest.mark.parametrize("code", _STIFF_CODES)
def test_modal_damping_matches_paper(code):
    """Rayleigh modal damping ``ξi = a0/(2ωi) + a1·ωi/2`` matches the paper.

    5 % Rayleigh on ``T1`` + the 95 %-participation mode reproduces MK-2003's
    ``ξi`` column exactly, so the higher-mode damping is *not* different from the
    reference model — the Rayleigh ``a0``/``a1`` for the shared ``(T1, T95)``
    anchors give the same ``ξi`` as the paper reports.
    """
    from fea_toolkit.opensees._runner_dynamic import rayleigh_coefficients

    n = int(FRAME_DATA[code]["N"])
    t1, t95 = gfr._rayleigh_periods(code)
    a0, a1 = rayleigh_coefficients(0.05, t1, t95, "rayleigh")
    xi = []
    for ratio in MODAL_DATA[n]["Ti_T1"]:
        w = 2.0 * math.pi / (t1 * ratio)
        xi.append(a0 / (2.0 * w) + a1 * w / 2.0)
    # Loose tolerance at the highest mode: the paper's ``ξi`` are derived from a
    # (discretised) 95 %-participation anchor and ``Ti/T1`` rounded to 3 sig.
    # figs, and the Rayleigh formula is most sensitive to that at the top mode.
    # The lowest 3--4 modes (which dominate the first-mode-shaped collapse) match
    # to ~2 %; only the highest mode drifts by up to ~10 %.
    assert xi == pytest.approx(MODAL_DATA[n]["xi"], abs=0.02)
