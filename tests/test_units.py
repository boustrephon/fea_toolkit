"""Unit conversion tests for fea_toolkit utilities.

Verifies that all *_scale_factor functions convert SI values to model
values::

    model_value = si_value * factor(units)

The reverted utils.py returns factors where length_scale_factor values
are model-unit-per-SI-metre (0.001 for mm, 0.0254 for in).
"""

import math
import pytest
import importlib.util
import sys

# Load module directly to avoid package-level pandas dependency
_spec = importlib.util.spec_from_file_location(
    "utils", "src/fea_toolkit/utils.py"
)
utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(utils)


class TestLengthScaleFactor:
    """SI metre → model length unit.  factor × SI = model."""

    def test_m_to_m(self):
        """1 m → 1 m."""
        f = utils.length_scale_factor({"L": "m"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_m_to_mm(self):
        """1 m → 1000.0 mm (model thinks in mm)."""
        f = utils.length_scale_factor({"L": "mm"})
        assert 1.0 * f == pytest.approx(1000.0)

    def test_m_to_in(self):
        """1 m → 0.0254 in (model thinks in inches)."""
        f = utils.length_scale_factor({"L": "in"})
        assert 1.0 * f == pytest.approx(39.3701)

    def test_m_to_ft(self):
        """1 m → 3.28084 ft."""
        f = utils.length_scale_factor({"L": "ft"})
        assert 1.0 * f == pytest.approx(3.28084)

    def test_m_to_cm(self):
        """1 m → 100.0 cm."""
        f = utils.length_scale_factor({"L": "cm"})
        assert 1.0 * f == pytest.approx(100.0)

    def test_alias_metre(self):
        assert utils.length_scale_factor({"L": "metre"}) == pytest.approx(1.0)

    def test_alias_inches(self):
        assert utils.length_scale_factor({"L": "inches"}) == pytest.approx(39.3701)

    def test_alias_millimeter(self):
        assert utils.length_scale_factor({"L": "millimeter"}) == pytest.approx(1000.0)

    def test_default_empty(self):
        assert utils.length_scale_factor({}) == pytest.approx(1.0)

    def test_default_none(self):
        assert utils.length_scale_factor(None) == pytest.approx(1.0)


class TestForceScaleFactor:
    """SI Newton → model force unit.  factor × SI = model."""

    def test_n_to_n(self):
        """1 N → 1 N."""
        f = utils.force_scale_factor({"F": "N"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_n_to_kN(self):
        """1 N → 0.001 kN."""
        f = utils.force_scale_factor({"F": "kN"})
        assert 1.0 * f == pytest.approx(0.001)

    def test_n_to_MN(self):
        f = utils.force_scale_factor({"F": "MN"})
        assert 1.0 * f == pytest.approx(1e-6)

    def test_n_to_lbf(self):
        """1 N → 0.2248 lbf."""
        f = utils.force_scale_factor({"F": "lb"})
        assert 1.0 * f == pytest.approx(0.2248, rel=1e-3)

    def test_n_to_kip(self):
        """1 N → 0.0002248 kip."""
        f = utils.force_scale_factor({"F": "kip"})
        assert 1.0 * f == pytest.approx(0.0002248, rel=1e-3)

    def test_n_to_kgf(self):
        """1 N → 0.1020 kgf."""
        f = utils.force_scale_factor({"F": "kgf"})
        assert 1.0 * f == pytest.approx(0.10197, rel=1e-4)

    def test_alias_newton(self):
        assert utils.force_scale_factor({"F": "newton"}) == pytest.approx(1.0)

    def test_alias_kilonewton(self):
        assert utils.force_scale_factor({"F": "kilonewton"}) == pytest.approx(0.001)

    def test_default_empty(self):
        assert utils.force_scale_factor({}) == pytest.approx(1.0)


class TestMassScaleFactor:
    """SI kilogram → model mass unit.  factor × SI = model.

    Known bugs in reverted code:
    - (kN,m) → expects 0.001 tonne but gets 1.0 (missing tonne mapping)
    - (lb,in) → expects 0.00571 blob but gets 0.4536 (blob = 0.4536 lb not 175 kg)
    - (lb,ft) → expects 0.0685 slug but gets 32.174 (slug = 32.174 lb not 14.6 kg)
    - (kip,ft) → expects 6.85e-5 kiloslug but gets 32174
    """

    def test_n_m_to_kg(self):
        """1 kg in (N,m) model → 1.0 (model mass unit = kg)."""
        f = utils.mass_scale_factor({"F": "N", "L": "m"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_n_mm_to_tonne(self):
        """1 kg in (N,mm) → 0.001 (model mass unit = tonne)."""
        f = utils.mass_scale_factor({"F": "N", "L": "mm"})
        assert 1.0 * f == pytest.approx(0.001)

    def test_kn_m_to_tonne(self):
        """1 kg in (kN,m) → 0.001 tonne.  (BUG: currently returns 1.0)"""
        f = utils.mass_scale_factor({"F": "kN", "L": "m"})
        assert 1.0 * f == pytest.approx(0.001)

    def test_kn_mm_to_kg(self):
        """1 kg in (kN,mm) → 1.0 kg."""
        f = utils.mass_scale_factor({"F": "kN", "L": "mm"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_kgf_m_to_kg(self):
        """1 kg in (kgf,m) → 0.10197162 hyl."""
        f = utils.mass_scale_factor({"F": "kgf", "L": "m"})
        assert 1.0 * f == pytest.approx(0.10197162)

    def test_kgf_cm_to_glug(self):
        """1 kg in (kgf,m) → 0.10197162 glug."""
        f = utils.mass_scale_factor({"F": "kgf", "L": "cm"})
        assert 1.0 * f == pytest.approx(1.0197162)

    def test_lb_in_to_blob(self):
        """1 kg in (lb,in) → 0.00571 blob.
        BUG: currently returns 0.4536 (pound-mass value)."""
        f = utils.mass_scale_factor({"F": "lb", "L": "in"})
        assert 1.0 * f == pytest.approx(0.00571, abs=7e-4)

    def test_lb_ft_to_slug(self):
        """1 kg in (lb,ft) → 0.0685 slug.
        BUG: currently returns 32.174 (pound-mass value)."""
        f = utils.mass_scale_factor({"F": "lb", "L": "ft"})
        assert 1.0 * f == pytest.approx(0.06852, abs=7e-3)

    def test_kip_in_to_kiloblob(self):
        """1 kg in (kip,in) → 5.71e-6 kiloblob.
        BUG: currently returns 453.6."""
        f = utils.mass_scale_factor({"F": "kip", "L": "in"})
        assert 1.0 * f == pytest.approx(5.71e-6, abs=6e-7)

    def test_kip_ft_to_kiloslug(self):
        """1 kg in (kip,ft) → 6.85e-5 kiloslug.
        BUG: currently returns 32174."""
        f = utils.mass_scale_factor({"F": "kip", "L": "ft"})
        assert 1.0 * f == pytest.approx(6.85e-5, abs=7e-6)

    def test_kgf_m_to_hyl(self):
        """1 kg in (kgf,m) → 0.102 hyl."""
        f = utils.mass_scale_factor({"F": "kgf", "L": "m"})
        assert 1.0 * f == pytest.approx(0.102, abs=1e-3)

    def test_default_empty(self):
        assert utils.mass_scale_factor({}) == pytest.approx(1.0)

    def test_default_none(self):
        assert utils.mass_scale_factor(None) == pytest.approx(1.0)


class TestStressScaleFactor:
    """SI Pascal → model stress unit.  factor × SI = model."""

    def test_n_m_to_pa(self):
        """1 Pa in (N,m) → 1.0 (model unit = Pa)."""
        f = utils.stress_scale_factor({"F": "N", "L": "m"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_n_mm_to_mpa(self):
        """1 Pa in (N,mm) → 1e-6 (model unit = MPa)."""
        f = utils.stress_scale_factor({"F": "N", "L": "mm"})
        assert 1.0 * f == pytest.approx(1e-6)

    def test_lb_in_to_psi(self):
        """1 Pa in (lb,in) → 1.45e-4 psi."""
        f = utils.stress_scale_factor({"F": "lb", "L": "in"})
        assert 1.0 * f == pytest.approx(1.4504e-4, abs=1e-6)

    def test_kip_in_to_ksi(self):
        """1 Pa in (kip,in) → 1.45e-7 ksi."""
        f = utils.stress_scale_factor({"F": "kip", "L": "in"})
        assert 1.0 * f == pytest.approx(1.4504e-7, abs=2e-10)

    def test_default_empty(self):
        assert utils.stress_scale_factor({}) == pytest.approx(1.0)


class TestGFromUnits:
    """g in model length-unit/s²."""

    def test_m_s2(self):
        assert utils.g_from_units({"L": "m"}) == pytest.approx(9.80665)

    def test_mm_s2(self):
        assert utils.g_from_units({"L": "mm"}) == pytest.approx(9806.65)

    def test_in_s2(self):
        assert utils.g_from_units({"L": "in"}) == pytest.approx(386.09, rel=1e-3)

    def test_ft_s2(self):
        assert utils.g_from_units({"L": "ft"}) == pytest.approx(32.174, rel=1e-4)


class TestMassDensityScaleFactor:
    """SI kg/m³ → model mass density unit."""

    def test_n_m_to_kg_m3(self):
        """1 kg/m³ in (N,m) → 1.0."""
        f = utils.mass_density_scale_factor({"F": "N", "L": "m"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_kgf_cm_to_glug_cm3(self):
        """1 kg/m³ in (kgf,cm) → 1.0197e-6 glug/cm^3"""
        f = utils.mass_density_scale_factor({"F": "kgf", "L": "cm"})
        assert 1.0 * f == pytest.approx(1.019716e-6)


class TestWeightDensityScaleFactor:
    """SI N/m³ → model weight density unit."""

    def test_n_m_to_n_m3(self):
        """1 N/m³ in (N,m) → 1.0."""
        f = utils.weight_density_scale_factor({"F": "N", "L": "m"})
        assert 1.0 * f == pytest.approx(1.0)

    def test_n_mm_to_n_mm3(self):
        """1 N/m³ in (N,mm) → 1e-9."""
        f = utils.weight_density_scale_factor({"F": "N", "L": "mm"})
        assert 1.0 * f == pytest.approx(1e-9)