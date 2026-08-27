"""Tests for fea_toolkit.capacity — GB 50010-2010 member capacities."""

import math

import pytest

from fea_toolkit.capacity._common import capacity_dcr
from fea_toolkit.capacity.gb50010 import (
    WallShearCheckResult,
    axial_capacity,
    moment_capacity,
    shear_capacity,
    wall_shear_check,
)
from fea_toolkit.model.sap_data import ConcreteRectangularSection, Material

# ── SI strengths (Pa) — capacity functions are unit-aware ─────────────
FC_PA = 20.0e6  # C40-ish concrete compressive strength
FY_PA = 413.0e6  # HRB400 longitudinal rebar yield

CONCRETE = Material(name="C40", type="Concrete", Fc=FC_PA)
STEEL = Material(name="HRB400", type="Rebar", Fy=FY_PA)

# Model units dicts: N·m (Pa → Pa) and kN·m (Pa → kPa, factor 1e-3)
UNITS_NM = {"F": "N", "L": "m", "T": "C"}
UNITS_KNM = {"F": "kN", "L": "m", "T": "C"}


def _beam_section(**overrides):
    """400×600 doubly-reinforced beam (top+bottom 4D20)."""
    defaults = {
        "name": "B1",
        "shape": "Concrete Rectangular",
        "material": "C40",
        "depth": 0.6,
        "bf": 0.3,
        "cover": 0.04,
        "top_bars": 4,
        "bot_bars": 4,
        "top_bar_dia": 0.02,
        "bot_bar_dia": 0.02,
    }
    defaults.update(overrides)
    return ConcreteRectangularSection(**defaults)


class TestMomentCapacity:
    def test_under_reinforced_rectangular(self):
        res = moment_capacity(_beam_section(), CONCRETE, STEEL, UNITS_NM)
        assert res.units == "N·m"
        assert res.value == pytest.approx(5.59496e5, rel=1e-4)
        assert res.extra["h0"] == pytest.approx(0.55)
        assert res.extra["a_top"] == pytest.approx(4 * math.pi * 0.01**2)

    def test_unit_agnostic_scaling(self):
        sec = _beam_section()
        res_nm = moment_capacity(sec, CONCRETE, STEEL, UNITS_NM)
        res_knm = moment_capacity(sec, CONCRETE, STEEL, UNITS_KNM)
        assert res_knm.units == "kN·m"
        assert res_knm.value == pytest.approx(res_nm.value * 1e-3, rel=1e-9)
        assert res_knm.value == pytest.approx(559.5, rel=1e-3)

    def test_elastic_fallback_when_no_rebar(self):
        sec = _beam_section(top_bars=0, bot_bars=0, Z33=0.006)
        res = moment_capacity(sec, CONCRETE, STEEL, UNITS_NM)
        # M_y = fy · Z33 (all model units)
        assert res.value == pytest.approx(FY_PA * 0.006, rel=1e-9)

    def test_explicit_strength_overrides(self):
        res = moment_capacity(
            _beam_section(),
            None,
            None,
            UNITS_NM,
            fc_pa=FC_PA,
            fy_pa=FY_PA,
        )
        assert res.value == pytest.approx(5.59496e5, rel=1e-4)

    def test_degenerate_section_returns_zero(self):
        res = moment_capacity(
            _beam_section(depth=0.0),
            CONCRETE,
            STEEL,
            UNITS_NM,
        )
        assert res.value == 0.0


class TestAxialCapacity:
    def test_axial_capacity_value(self):
        sec = _beam_section()
        res = axial_capacity(sec, CONCRETE, STEEL, UNITS_NM)
        ag = 0.3 * 0.6  # section.A is 0 → bf·depth fallback
        a_steel = 0.006 * ag
        expected = 0.9 * (FC_PA * ag + FY_PA * a_steel)
        assert res.units == "N"
        assert res.value == pytest.approx(expected, rel=1e-9)
        assert res.extra["rho"] == pytest.approx(0.006)

    def test_gross_area_taken_from_section(self):
        sec = _beam_section(A=0.25)
        res = axial_capacity(sec, CONCRETE, STEEL, UNITS_NM)
        a_steel = 0.006 * 0.25
        expected = 0.9 * (FC_PA * 0.25 + FY_PA * a_steel)
        assert res.value == pytest.approx(expected, rel=1e-9)


class TestShearCapacity:
    def test_shear_capacity_beam(self):
        sec = _beam_section()
        res = shear_capacity(sec, CONCRETE, STEEL, UNITS_NM, is_column=False)
        b, h, cv = 0.3, 0.6, 0.04
        h0 = h - cv
        ft = 0.26 * (FC_PA / 1e6) ** (2.0 / 3.0) * 1e6
        vc = 0.7 * ft * b * h0
        asw = 2.0 * math.pi * (0.010 / 2.0) ** 2
        vs = FY_PA * asw / 0.300 * h0
        expected = min(vc + vs, 0.25 * FC_PA * b * h0)
        assert res.units == "N"
        assert res.value == pytest.approx(expected, rel=1e-9)

    def test_column_axial_enhancement(self):
        sec = _beam_section()
        res_ax = shear_capacity(sec, CONCRETE, STEEL, UNITS_NM, axial=1000.0)
        res_no = shear_capacity(sec, CONCRETE, STEEL, UNITS_NM, axial=0.0)
        assert res_ax.value == pytest.approx(res_no.value + 0.07 * 1000.0, rel=1e-9)

    def test_explicit_ft_override(self):
        sec = _beam_section()
        res_default = shear_capacity(sec, CONCRETE, STEEL, UNITS_NM, is_column=False)
        res_explicit = shear_capacity(
            sec,
            CONCRETE,
            STEEL,
            UNITS_NM,
            is_column=False,
            ft_pa=3.0e6,
        )
        # A higher f_t must increase V_c (3 MPa > the ~1.92 MPa default).
        assert res_explicit.value > res_default.value
        b, h, cv = 0.3, 0.6, 0.04
        assert res_explicit.extra["vc"] == pytest.approx(0.7 * 3.0e6 * b * (h - cv), rel=1e-9)

    def test_section_tie_geometry_used(self):
        sec = _beam_section(tie_diameter=0.012, tie_spacing=0.150)
        res = shear_capacity(sec, CONCRETE, STEEL, UNITS_NM, is_column=False)
        asw = 2.0 * math.pi * (0.012 / 2.0) ** 2
        vs = FY_PA * asw / 0.150 * (0.6 - 0.04)
        res_default = shear_capacity(_beam_section(), CONCRETE, STEEL, UNITS_NM, is_column=False)
        assert res.extra["vs"] == pytest.approx(vs, rel=1e-9)
        assert res.value > res_default.value


class TestWallShearCheck:
    def test_wall_check_pass(self):
        res = wall_shear_check(
            Nxy=300000.0,
            Ny=100000.0,
            t=0.15,
            concrete=CONCRETE,
            units=UNITS_NM,
            tie_fy_pa=FY_PA,
        )
        assert isinstance(res, WallShearCheckResult)
        assert res.tau == pytest.approx(300000.0 / 0.15, rel=1e-9)
        assert res.sigma == pytest.approx(100000.0 / 0.15, rel=1e-9)
        assert res.ok_shear is True
        assert res.ok_normal is True

    def test_wall_check_shear_governs(self):
        res = wall_shear_check(
            Nxy=5e6,
            Ny=1e6,
            t=0.15,
            concrete=CONCRETE,
            units=UNITS_NM,
            tie_fy_pa=FY_PA,
        )
        assert res.ok_shear is False
        # For fc = 20 MPa + T10@300, the diagonal-tension cap governs over the
        # strut limit (0.20·fc = 4 MPa): tau_cap = 0.7·f_t + f_yv·rho_sh.
        ft = 0.26 * (FC_PA / 1e6) ** (2.0 / 3.0) * 1e6
        rho_sh = 2.0 * math.pi * (0.010 / 2.0) ** 2 / 0.300 / 0.15
        assert res.tau_limit == pytest.approx(0.7 * ft + FY_PA * rho_sh, rel=1e-9)

    def test_wall_check_strut_limit_governs(self):
        res = wall_shear_check(
            Nxy=5e6,
            Ny=1e6,
            t=0.15,
            concrete=CONCRETE,
            units=UNITS_NM,
            tie_fy_pa=FY_PA,
            strut_factor=0.05,
        )
        assert res.tau_limit == pytest.approx(0.05 * FC_PA, rel=1e-9)

    def test_zero_thickness(self):
        res = wall_shear_check(
            Nxy=300000.0,
            Ny=100000.0,
            t=0.0,
            concrete=CONCRETE,
            units=UNITS_NM,
        )
        assert res.ok_shear is False and res.ok_normal is False


class TestCapacityDcr:
    def test_basic(self):
        assert capacity_dcr(200.0, 400.0) == pytest.approx(0.5)

    def test_positive_demand_zero_capacity_is_inf(self):
        assert capacity_dcr(10.0, 0.0) == float("inf")

    def test_both_zero_returns_zero(self):
        assert capacity_dcr(0.0, 0.0) == 0.0


def _beam_section_for_units(units):
    """Same physical 400×600 beam, geometry authored in the model's units."""
    lf = {"m": 1.0, "mm": 1000.0}[units["L"]]
    return _beam_section(
        depth=0.6 * lf,
        bf=0.3 * lf,
        cover=0.04 * lf,
        top_bar_dia=0.02 * lf,
        bot_bar_dia=0.02 * lf,
    )


class TestCapacityUnitLabels:
    """Unit labels in capacity results go through the canonical normaliser."""

    def test_force_unit_label_normalises_sap_short_form(self):
        """``"KN"`` (SAP2000 short form) renders as ``"kN"`` in capacity results."""
        from fea_toolkit.capacity._common import force_unit_label

        assert force_unit_label({"F": "KN", "L": "m", "T": "C"}) == "kN"
        assert force_unit_label({"F": "N", "L": "m", "T": "C"}) == "N"
        assert force_unit_label({"F": "kilonewton", "L": "metre"}) == "kN"

    def test_force_length_unit_label_uses_length_label(self):
        from fea_toolkit.capacity._common import force_length_unit_label

        assert force_length_unit_label({"F": "KN", "L": "M"}) == "kN·m"
        assert force_length_unit_label({"F": "kn", "L": "mm"}) == "kN·mm"


class TestUnitMatrixScaling:
    """Capacities must scale exactly with the model's force/length units.

    Same physical 400×600 beam with fc = 20 MPa, fy = 413 MPa, T10@300 ties,
    computed in N·m / kN·m / N·mm / kN·mm.  Values scale by F·L (moment) and
    F (axial/shear) relative to the N·m model; unit labels follow the model.
    """

    @pytest.mark.parametrize(
        "units, moment_factor, force_factor",
        [
            ({"F": "N", "L": "m", "T": "C"}, 1.0, 1.0),
            ({"F": "kN", "L": "m", "T": "C"}, 1e-3, 1e-3),
            ({"F": "N", "L": "mm", "T": "C"}, 1e3, 1.0),
            ({"F": "kN", "L": "mm", "T": "C"}, 1.0, 1e-3),
        ],
    )
    def test_moment_axial_shear_scale_with_units(self, units, moment_factor, force_factor):
        lf = {"m": 1.0, "mm": 1000.0}[units["L"]]
        sec = _beam_section_for_units(units)
        res_m = moment_capacity(sec, CONCRETE, STEEL, units)
        res_a = axial_capacity(sec, CONCRETE, STEEL, units)
        res_s = shear_capacity(
            sec,
            CONCRETE,
            STEEL,
            units,
            is_column=False,
            tie_dia=0.010 * lf,
            tie_spacing=0.300 * lf,
        )

        res_m_nm = moment_capacity(_beam_section(), CONCRETE, STEEL, UNITS_NM)
        res_a_nm = axial_capacity(_beam_section(), CONCRETE, STEEL, UNITS_NM)
        res_s_nm = shear_capacity(
            _beam_section(),
            CONCRETE,
            STEEL,
            UNITS_NM,
            is_column=False,
            tie_dia=0.010,
            tie_spacing=0.300,
        )

        # Unit labels follow the model system.
        assert res_m.units == f"{units['F']}·{units['L']}"
        assert res_a.units == units["F"]
        assert res_s.units == units["F"]

        # Values scale by F·L (moment) and F (axial/shear) vs the N·m model.
        assert res_m.value == pytest.approx(res_m_nm.value * moment_factor, rel=1e-9)
        assert res_a.value == pytest.approx(res_a_nm.value * force_factor, rel=1e-9)
        assert res_s.value == pytest.approx(res_s_nm.value * force_factor, rel=1e-9)

    def test_wall_physical_invariance(self):
        """Wall DCR/OK flags are unit-invariant; tau & limits scale by force.

        Physically equivalent demands: 300 kN/m (kN·m model) vs 3e5 N/m
        (N·m model) on the same 0.15 m wall.
        """
        r_nm = wall_shear_check(3e5, 1e5, 0.15, CONCRETE, UNITS_NM, tie_fy_pa=FY_PA)
        r_knm = wall_shear_check(300.0, 100.0, 0.15, CONCRETE, UNITS_KNM, tie_fy_pa=FY_PA)
        assert r_knm.tau == pytest.approx(r_nm.tau * 1e-3, rel=1e-9)
        assert r_knm.tau_limit == pytest.approx(r_nm.tau_limit * 1e-3, rel=1e-9)
        assert r_knm.sigma_limit == pytest.approx(r_nm.sigma_limit * 1e-3, rel=1e-9)
        assert (r_knm.ok_shear, r_knm.ok_normal) == (r_nm.ok_shear, r_nm.ok_normal)
