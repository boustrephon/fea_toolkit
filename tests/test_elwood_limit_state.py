"""Tests for the Elwood & Moehle column limit-state module.

Covers the parameter extraction, the unit-agnostic drift-capacity equations
(validated against the Phase-0 prototype / PEER 2003/01 specimen), the
ThreePoint axial-surface fit used as the OpenSeesPy ``limitCurve Axial``
workaround, and the ``LimitState`` envelope helper.

The reference numbers come from ``local/elwood_prototype.py`` (Phase-0
checkpoint, ``local/elwood_phase0_checkpoint.md``) and Elwood & Moehle,
PEER 2003/01: Specimen 2 (9x9 in, L=58 in, f'c=3.52 ksi, P=70 kip) failed
in shear at 2.1% drift (test AND analytical) and axially at 5.2% drift
(frame model; 4.58% at constant P per the shear-friction equation).
"""

import math

import pytest

from fea_toolkit.capacity.elwood_limit_state import (
    ElwoodColumnGeometry,
    axial_capacity_surface,
    elwood_axial_deg_slope,
    elwood_axial_drift_at_failure,
    elwood_column_geometry,
    elwood_column_parameters,
    elwood_limit_state_envelope,
    elwood_shear_drift_at_failure,
    elwood_shear_limit_force,
    elwood_spring_slopes,
    three_point_axial_surface,
)
from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    Material,
)

# ═════════════════════════════════════════════════════════════════════
# Fixtures -- the PEER 2003/01 Specimen-2 column in two unit systems
# ═════════════════════════════════════════════════════════════════════

_KIP_IN = {"L": "in", "F": "kip"}  # model stress unit = ksi
_N_M = {"L": "m", "F": "N"}  # model stress unit = Pa


def _specimen_section(units):
    """ConcreteRectangularSection for the 9x9 in Specimen-2 column.

    Tie data: 2-legged #2 hoops @ 6 in (A_st = 0.098 in2, f_yt = 100 ksi)
    giving rho'' = 0.0018 and F_sw ~ 11.9 kip -- the prototype values.
    """
    lf = 1.0 if units["L"] == "in" else 0.0254  # in -> m
    sf = 1.0 if units["F"] == "kip" else 6894.76e3  # ksi -> Pa
    ff = 1.0 if units["F"] == "kip" else 4448.0  # kip -> N
    return (
        ConcreteRectangularSection(
            name="COL-9x9",
            shape="Concrete Rectangular",
            material="CONC",
            rebar_material="REBAR-L",
            bf=9.0 * lf,
            depth=9.0 * lf,
            cover=1.0 * lf,
            top_bars=4,
            bot_bars=4,
            top_bar_dia=0.625 * lf,
            bot_bar_dia=0.625 * lf,
            tie_diameter=0.25 * lf,
            tie_spacing=6.0 * lf,
            tie_fy=100.0 * sf,
            tie_rebar_mat="REBAR-T",
        ),
        Material(name="CONC", type="Concrete", Fc=3.517 * sf, E_mod=3400.0 * sf, nu=0.2),
        Material(name="REBAR-T", type="Rebar", Fy=100.0 * sf),
        {"ff": ff, "lf": lf},
    )


@pytest.fixture(scope="module")
def specimen_kipin():
    sec, conc, tie, _ = _specimen_section(_KIP_IN)
    return sec, conc, tie, _KIP_IN


@pytest.fixture(scope="module")
def specimen_nm():
    sec, conc, tie, _ = _specimen_section(_N_M)
    return sec, conc, tie, _N_M


# ═════════════════════════════════════════════════════════════════════
# Geometry / parameter extraction
# ═════════════════════════════════════════════════════════════════════


class TestGeometryExtraction:
    def test_specimen_geometry_kipin(self, specimen_kipin):
        sec, conc, tie, _ = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        assert g.b == pytest.approx(9.0)
        assert g.h == pytest.approx(9.0)
        assert g.d == pytest.approx(8.0)  # h - cover
        assert g.dc == pytest.approx(7.25)  # h - 2*cover + tie_dia
        assert g.ast == pytest.approx(2 * math.pi / 4 * 0.25**2, rel=1e-6)
        assert g.s == pytest.approx(6.0)
        assert g.fyt == pytest.approx(100.0)
        assert g.fc == pytest.approx(3.517)

    def test_rho_is_empirical_transverse_ratio(self):
        # rho'' = A_st/(b*s); falls back to A_st/(b*h) when s is unknown.
        g = ElwoodColumnGeometry(b=9.0, h=9.0, ast=0.098, s=6.0)
        assert g.rho == pytest.approx(0.098 / (9.0 * 6.0))
        g0 = ElwoodColumnGeometry(b=9.0, h=9.0, ast=0.098, s=0.0)
        assert g0.rho == pytest.approx(0.098 / 81.0)

    def test_fsw_matches_prototype(self, specimen_kipin):
        sec, conc, tie, _ = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        # A_st*f_yt*d_c/s = 0.098*100*7.25/6 = 11.86 kip
        assert g.fsw == pytest.approx(11.86, rel=1e-3)

    def test_parameters_with_length(self, specimen_kipin):
        sec, conc, tie, _ = specimen_kipin
        p = elwood_column_parameters(sec, conc, tie=tie, column_length=58.0)
        assert p.fsw == pytest.approx(11.86, rel=1e-3)
        assert p.axial_elastic_slope == pytest.approx(99.0 * 3400.0 * 81.0 / 58.0, rel=1e-6)
        assert p.kdeg_axial == pytest.approx(-0.02 * 3400.0 * 81.0 / 58.0, rel=1e-6)

    def test_spring_slopes(self, specimen_kipin):
        sec, conc, tie, _ = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        sh, ax = elwood_spring_slopes(conc, g, 58.0)
        gmod = 3400.0 / (2.0 * (1.0 + 0.2))
        assert sh == pytest.approx(gmod * 81.0 / 58.0)
        assert ax == pytest.approx(99.0 * 3400.0 * 81.0 / 58.0)
        assert elwood_axial_deg_slope(conc, g, 58.0) == pytest.approx(-0.02 * 3400.0 * 81.0 / 58.0)


# ═════════════════════════════════════════════════════════════════════
# Empirical drift equations
# ═════════════════════════════════════════════════════════════════════


class TestDriftEquations:
    def test_axial_drift_matches_published_value(self):
        # PEER 2003/01 shear-friction model: DR_a = 4.58% at P=70, Fsw=11.87.
        dr = elwood_axial_drift_at_failure(70.0, 11.87)
        assert dr == pytest.approx(0.0458, abs=2e-4)

    def test_shear_drift_reproduces_prototype_failure(self, specimen_kipin):
        # Phase-0 prototype: shear failure at 2.10% (V=20.62 kip, P=69.0 kip).
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        dr = elwood_shear_drift_at_failure(20.62, 69.02, g, units)
        assert 0.019 < dr < 0.025  # ~2.1-2.2%

    def test_unit_invariance(self, specimen_kipin, specimen_nm):
        sec_k, conc_k, tie_k, _ = specimen_kipin
        sec_n, conc_n, tie_n, _ = specimen_nm
        gk = elwood_column_geometry(sec_k, conc_k, tie=tie_k)
        gn = elwood_column_geometry(sec_n, conc_n, tie=tie_n)
        # Same physical column -> same drifts, whichever unit system.
        # (1 kip = 4448 N for the N-m model inputs.)
        dr_s_k = elwood_shear_drift_at_failure(20.62, 69.02, gk, _KIP_IN)
        dr_s_n = elwood_shear_drift_at_failure(20.62 * 4448.0, 69.02 * 4448.0, gn, _N_M)
        assert dr_s_n == pytest.approx(dr_s_k, rel=2e-4)
        dr_a_k = elwood_axial_drift_at_failure(70.0, gk.fsw, _KIP_IN)
        dr_a_n = elwood_axial_drift_at_failure(70.0 * 4448.0, gn.fsw, _N_M)
        assert dr_a_n == pytest.approx(dr_a_k, rel=2e-4)

    def test_axial_capacity_surface_decreases_with_drift(self):
        p1 = axial_capacity_surface(0.02, 11.87)
        p2 = axial_capacity_surface(0.06, 11.87)
        assert p1 > p2 > 0.0

    def test_axial_drift_no_ties_uses_fallback_drift(self):
        # fsw <= 0 (no ties) -> the 0.10 fallback drift, not the near-zero
        # value the shear-friction surface itself would give.
        assert elwood_axial_drift_at_failure(70.0, 0.0) > 0.05


# ═════════════════════════════════════════════════════════════════════
# Direct-form limiting shear force V(DR)
# ═════════════════════════════════════════════════════════════════════


class TestShearLimitForce:
    def test_inverse_round_trip(self, specimen_kipin):
        """V(DR) is the exact inverse of the drift-at-shear-failure equation."""
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        for dr in (0.015, 0.02, 0.025, 0.028):
            v = elwood_shear_limit_force(dr, 69.02, g, units)
            assert v > 0.0
            dr_back = elwood_shear_drift_at_failure(v, 69.02, g, units)
            assert dr_back == pytest.approx(dr, rel=1e-9, abs=1e-12)

    def test_unit_invariance(self, specimen_kipin, specimen_nm):
        """The same physical column gives the same force in kip or N."""
        sec_k, conc_k, tie_k, _ = specimen_kipin
        sec_n, conc_n, tie_n, _ = specimen_nm
        gk = elwood_column_geometry(sec_k, conc_k, tie=tie_k)
        gn = elwood_column_geometry(sec_n, conc_n, tie=tie_n)
        v_k = elwood_shear_limit_force(0.02, 69.02, gk, _KIP_IN)
        v_n = elwood_shear_limit_force(0.02, 69.02 * 4448.0, gn, _N_M)
        assert v_n == pytest.approx(v_k * 4448.0, rel=2e-4)

    def test_reproduces_prototype_shear_failure(self, specimen_kipin):
        """Inverse of the validated Phase-0 failure point (2.1% drift, 20.62 kip)."""
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        v = elwood_shear_limit_force(0.0215, 69.02, g, units)
        assert v == pytest.approx(20.62, rel=3e-2)

    def test_decreases_with_drift(self, specimen_kipin):
        """The surface force falls monotonically with drift (until exhaustion)."""
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        vs = [elwood_shear_limit_force(d, 69.02, g, units) for d in (0.012, 0.02, 0.028, 0.035)]
        assert vs[0] > vs[1] > vs[2] > vs[3] >= 0.0

    def test_one_percent_floor(self, specimen_kipin):
        """Below the 1% drift floor the surface reports 'no failure' (huge force)."""
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        v_floor = elwood_shear_limit_force(0.005, 69.02, g, units)
        v_at_floor = elwood_shear_limit_force(0.01, 69.02, g, units)
        assert v_floor > 1.0e6 * v_at_floor

    def test_zero_geometry_guard(self, specimen_kipin):
        """Degenerate geometry raises ValueError rather than returning 0.0."""
        _, _, _, units = specimen_kipin
        g0 = ElwoodColumnGeometry()  # b = h = d = 0
        with pytest.raises(ValueError):
            elwood_shear_limit_force(0.03, 10.0, g0, units)


# ═════════════════════════════════════════════════════════════════════
# OpenSeesPy bridge helpers
# ═════════════════════════════════════════════════════════════════════


class TestOpenSeesBridge:
    def test_three_point_surface_is_pinned_at_gravity_load(self, specimen_kipin):
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        pts = three_point_axial_surface(70.0, g.fsw, units)
        assert len(pts) == 3
        x1, y1 = pts[0]
        x2, y2 = pts[1]
        x3, y3 = pts[2]
        assert x1 == 0.0  # ThreePointCurve: x<x1 -> 0
        assert y1 > 2.0 * 70.0  # high plateau at zero drift
        # The middle point triggers at the Elwood axial-failure drift.
        assert y2 == pytest.approx(70.0)
        assert x2 == pytest.approx(elwood_axial_drift_at_failure(70.0, g.fsw, units), rel=1e-6)
        assert 0.04 < x2 < 0.06  # ~4.6% for this column
        assert x3 == pytest.approx(0.08)
        assert y3 < 70.0

    def test_three_point_surface_kipin_values(self, specimen_kipin):
        # Returns kip values for the OpenSees command (imperial).
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        pts = three_point_axial_surface(70.0, g.fsw, units)
        assert pts[1][1] == pytest.approx(70.0)

    def test_three_point_surface_rejects_invalid_drift_bounds(self, specimen_kipin):
        sec, conc, tie, units = specimen_kipin
        g = elwood_column_geometry(sec, conc, tie=tie)
        with pytest.raises(ValueError, match="low_drift"):
            three_point_axial_surface(70.0, g.fsw, units, low_drift=0.09, high_drift=0.08)
        with pytest.raises(ValueError, match="low_drift"):
            three_point_axial_surface(70.0, g.fsw, units, low_drift=0.0, high_drift=0.08)

    def test_limit_state_envelope_on_elastic_slope(self):
        env = elwood_limit_state_envelope([25.0, 30.0, 45.0], 1700.0)
        assert len(env) == 3
        for f, e in env:
            assert e == pytest.approx(f / 1700.0)
        assert env[0][0] == pytest.approx(25.0)
        assert env[-1][1] > env[0][1]

    def test_envelope_zero_slope_guards(self):
        env = elwood_limit_state_envelope([1.0, 2.0], 0.0)
        assert all(e == 0.0 for _, e in env)


# ═════════════════════════════════════════════════════════════════════
# Module exports
# ═════════════════════════════════════════════════════════════════════


class TestExports:
    def test_analysis_package_exports(self):
        import fea_toolkit.analysis as an

        for name in (
            "ElwoodColumnGeometry",
            "elwood_column_parameters",
            "three_point_axial_surface",
            "elwood_shear_drift_at_failure",
            "elwood_shear_limit_force",
        ):
            assert hasattr(an, name)
