"""Unit conversion tests for fea_toolkit utilities.

Verifies that all *_scale_factor functions convert SI values to model
values::

    model_value = si_value * factor(units)

The reverted utils.py returns factors where length_scale_factor values
are model-unit-per-SI-metre (0.001 for mm, 0.0254 for in).
"""

import importlib.util
from typing import ClassVar

import pytest

# Load module directly to avoid package-level pandas dependency
_spec = importlib.util.spec_from_file_location("utils", "src/fea_toolkit/utils.py")
utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(utils)

from fea_toolkit.model.sap_data import Material, SAPModelData


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

    def test_n_to_GN(self):
        """1 N → 1e-9 GN."""
        f = utils.force_scale_factor({"F": "GN"})
        assert 1.0 * f == pytest.approx(1e-9)

    def test_alias_giganewton(self):
        assert utils.force_scale_factor({"F": "giganewton"}) == pytest.approx(1e-9)

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
        """1 kg in (kN,mm) → 1e-6 (F_factor=0.001, L_factor=1000)."""
        f = utils.mass_scale_factor({"F": "kN", "L": "mm"})
        assert 1.0 * f == pytest.approx(1e-6)

    def test_kgf_m_to_kg(self):
        """1 kg in (kgf,m) → 0.10197162 hyl."""
        f = utils.mass_scale_factor({"F": "kgf", "L": "m"})
        assert 1.0 * f == pytest.approx(0.10197162)

    def test_kgf_cm_to_glug(self):
        """1 kg in (kgf,cm) → F_factor / L_factor = (1/9.80665) / 100."""
        f = utils.mass_scale_factor({"F": "kgf", "L": "cm"})
        assert 1.0 * f == pytest.approx(0.001019716)

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
        """1 kg/m³ in (kgf,cm) → M_factor / L_factor³ = (1/9.80665/100) / 100³"""
        f = utils.mass_density_scale_factor({"F": "kgf", "L": "cm"})
        assert 1.0 * f == pytest.approx(1.019716e-09)


class TestScaleMaterialDict:
    """``scale_material_dict`` scales stress-valued fields from SI to model units."""

    def test_kN_m_scales_stress_fields(self):
        """(kN,m): stress fields ÷1000, non-stress fields unchanged."""
        mat = {"E": 200.0e9, "nu": 0.3, "fy": 500.0e6, "Hiso": 0.0, "material_type": "J2PlateFibre"}
        result = utils.scale_material_dict(mat, {"F": "kN", "L": "m"})
        assert result["E"] == pytest.approx(200.0e6)  # 200e9 * 0.001
        assert result["fy"] == pytest.approx(500.0e3)  # 500e6 * 0.001
        assert result["Hiso"] == pytest.approx(0.0)
        assert result["nu"] == pytest.approx(0.3)  # dimensionless, unchanged
        assert result["material_type"] == "J2PlateFibre"  # string, unchanged

    def test_SI_N_m_no_scaling(self):
        """(N,m): stress_scale ≈ 1.0, dict returned as-is."""
        mat = {"E": 200.0e9, "fc": 30.0e6}
        result = utils.scale_material_dict(mat, {"F": "N", "L": "m"})
        assert result is not mat  # returns a copy
        assert result["E"] == pytest.approx(200.0e9)
        assert result["fc"] == pytest.approx(30.0e6)

    def test_precomputed_stress_scale(self):
        """Pre-computed stress_scale overrides computed factor."""
        mat = {"E": 200.0e9, "fy": 500.0e6}
        result = utils.scale_material_dict(mat, {"F": "N", "L": "m"}, stress_scale=0.001)
        assert result["E"] == pytest.approx(200.0e6)
        assert result["fy"] == pytest.approx(500.0e3)

    def test_unknown_keys_pass_through(self):
        """Keys not in _STRESS_KEYS are not scaled (mass density is)."""
        mat = {"E": 200.0e9, "E_mod": 200.0e9, "rho": 7850.0, "notes": "test"}
        result = utils.scale_material_dict(mat, {"F": "kN", "L": "m"})
        assert result["E"] == pytest.approx(200.0e6)  # stress key → scaled
        assert result["E_mod"] == pytest.approx(200.0e9)  # NOT in _STRESS_KEYS → unchanged
        # Mass density is authored in SI (kg/m³) and rescaled to model
        # mass-density units (t/m³ for kN-m): 7850 kg/m³ → 7.85 t/m³.
        assert result["rho"] == pytest.approx(
            7850.0 * utils.mass_density_scale_factor({"F": "kN", "L": "m"})
        )
        assert result["notes"] == "test"  # string → unchanged

    def test_empty_dict(self):
        """Empty dict returns empty dict."""
        assert utils.scale_material_dict({}, {"F": "kN", "L": "m"}) == {}


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


class TestInverseScaleFactors:
    """Model→SI functions are exact inverses of SI→model functions.

    For any valid units dict:

        value_in_SI = value_in_model_units * to_si_factor(units)
        value_in_model_units = value_in_SI * scale_factor(units)

    so ``scale_factor * to_si_factor == 1.0`` for every pair.
    """

    _UNIT_SET: ClassVar = [
        {"F": "N", "L": "m"},
        {"F": "N", "L": "mm"},
        {"F": "kN", "L": "m"},
        {"F": "kN", "L": "mm"},
        {"F": "MN", "L": "cm"},
        {"F": "lb", "L": "in"},
        {"F": "kip", "L": "ft"},
        {"F": "kgf", "L": "cm"},
        {"F": "tonf", "L": "m"},
    ]

    def test_length_roundtrip(self):
        """length_to_si_factor is the reciprocal of length_scale_factor."""
        for u in self._UNIT_SET:
            assert utils.length_scale_factor(u) * utils.length_to_si_factor(u) == pytest.approx(
                1.0, rel=1e-12
            )

    def test_force_roundtrip(self):
        """force_to_si_factor is the reciprocal of force_scale_factor."""
        for u in self._UNIT_SET:
            assert utils.force_scale_factor(u) * utils.force_to_si_factor(u) == pytest.approx(
                1.0, rel=1e-12
            )

    def test_mass_roundtrip(self):
        """mass_to_si_factor is the reciprocal of mass_scale_factor."""
        for u in self._UNIT_SET:
            assert utils.mass_scale_factor(u) * utils.mass_to_si_factor(u) == pytest.approx(
                1.0, rel=1e-12
            )

    def test_stress_roundtrip(self):
        """stress_to_si_factor is the reciprocal of stress_scale_factor."""
        for u in self._UNIT_SET:
            assert utils.stress_scale_factor(u) * utils.stress_to_si_factor(u) == pytest.approx(
                1.0, rel=1e-12
            )

    def test_mass_density_roundtrip(self):
        """mass_density_to_si_factor is the reciprocal of mass_density_scale_factor."""
        for u in self._UNIT_SET:
            assert utils.mass_density_scale_factor(u) * utils.mass_density_to_si_factor(
                u
            ) == pytest.approx(1.0, rel=1e-12)

    def test_weight_density_roundtrip(self):
        """weight_density_to_si_factor is the reciprocal of weight_density_scale_factor."""
        for u in self._UNIT_SET:
            assert utils.weight_density_scale_factor(u) * utils.weight_density_to_si_factor(
                u
            ) == pytest.approx(1.0, rel=1e-12)

    def test_lineal_force_consistency(self):
        """lineal_force_to_si_factor == force_to_si / length_to_si."""
        for u in self._UNIT_SET:
            expected = utils.force_to_si_factor(u) / utils.length_to_si_factor(u)
            assert utils.lineal_force_to_si_factor(u) == pytest.approx(expected, rel=1e-12)

    def test_alias_inverse_matches_bare_string(self):
        """Length aliases normalise identically in both factor systems."""
        assert utils.length_to_si_factor({"L": "millimetre"}) == pytest.approx(
            utils.length_to_si_factor({"L": "mm"})
        )
        assert utils.force_to_si_factor({"F": "kilonewtons"}) == pytest.approx(
            utils.force_to_si_factor({"F": "kN"})
        )


class TestApplyMaterialDefaultsScaleFactors:
    """`SAPModelData.apply_material_defaults()` must scale SI authoring
    values to the model unit system.

    Defaults are authored in SI (Pa, N/m³, kg/m³) and converted via
    the canonical utils factors (multiplication convention):

        model_value = SI_default * scale_factor(units)

    Round-trip: model_value * to_si_factor(units) == SI_default.
    """

    def test_apply_material_defaults_sets_si_defaults_in_model_units(self):
        """apply_material_defaults() fills zero concrete properties with
        SI defaults scaled to the model unit system."""
        units = {"F": "kN", "L": "m", "T": "C"}
        md = SAPModelData(
            nodes={},
            restraints={},
            materials={
                "C30": Material(
                    name="C30",
                    type="Concrete",
                    E_mod=0.0,
                    unit_weight=0.0,
                    unit_mass=0.0,
                ),
            },
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            units=units,
        )

        md.apply_material_defaults()

        mat = md.materials["C30"]
        assert mat.E_mod * utils.stress_to_si_factor(units) == pytest.approx(
            utils.DEFAULT_E_C_PA, rel=1e-12
        )
        assert mat.unit_weight * utils.weight_density_to_si_factor(units) == pytest.approx(
            utils.DEFAULT_RHO_WC_SI, rel=1e-12
        )
        assert mat.unit_mass * utils.mass_density_to_si_factor(units) == pytest.approx(
            utils.DEFAULT_RHO_MC_SI, rel=1e-12
        )

    def test_concrete_defaults_roundtrip_non_si(self):
        """Concrete stress/weight/mass defaults round-trip in kN-m."""
        mds = utils.mass_density_scale_factor({"F": "kN", "L": "m"})
        wds = utils.weight_density_scale_factor({"F": "kN", "L": "m"})
        ssf = utils.stress_scale_factor({"F": "kN", "L": "m"})
        ssti = utils.stress_to_si_factor({"F": "kN", "L": "m"})
        mdsi = utils.mass_density_to_si_factor({"F": "kN", "L": "m"})
        wdsi = utils.weight_density_to_si_factor({"F": "kN", "L": "m"})

        E = utils.DEFAULT_E_C_PA * ssf
        assert E * ssti == pytest.approx(utils.DEFAULT_E_C_PA, rel=1e-12)
        assert E > 0 and E < 1e12  # sane model-unit magnitude

        uw = utils.DEFAULT_RHO_WC_SI * wds
        assert uw * wdsi == pytest.approx(utils.DEFAULT_RHO_WC_SI, rel=1e-12)

        um = utils.DEFAULT_RHO_MC_SI * mds
        assert um * mdsi == pytest.approx(utils.DEFAULT_RHO_MC_SI, rel=1e-12)

    def test_builder_style_density_division_is_not_roundtrip(self):
        """Regression — division convention (pre-fix builder.py) does NOT
        round-trip; multiplication is the correct SI→model conversion."""
        mds = utils.mass_density_scale_factor({"F": "kN", "L": "m"})
        mdsi = utils.mass_density_to_si_factor({"F": "kN", "L": "m"})
        division_value = utils.DEFAULT_RHO_MC_SI / mds
        assert division_value * mdsi != pytest.approx(utils.DEFAULT_RHO_MC_SI)
        # 2450 / 0.001 = 2.45e6; * 1000 = 2.45e9 — wrong by a factor of 1000
        assert abs(division_value * mdsi / utils.DEFAULT_RHO_MC_SI - 1.0) > 100
        multiplication_value = utils.DEFAULT_RHO_MC_SI * mds
        assert (
            abs(multiplication_value * mdsi / utils.DEFAULT_RHO_MC_SI - 1.0) < 1e-12
        )  # multiplication round-trips exactly


class TestUnitLabels:
    """Display labels derived from model units dicts (force_unit_label /
    length_unit_label)."""

    def test_kn_m(self):
        assert utils.force_unit_label({"F": "KN", "L": "m"}) == "kN"
        assert utils.length_unit_label({"F": "KN", "L": "m"}) == "m"

    def test_si_n_m(self):
        assert utils.force_unit_label({"F": "N", "L": "m"}) == "N"
        assert utils.length_unit_label({"F": "N", "L": "m"}) == "m"

    def test_mn(self):
        assert utils.force_unit_label({"F": "MN"}) == "MN"

    def test_gn(self):
        assert utils.force_unit_label({"F": "GN"}) == "GN"

    def test_kgf(self):
        assert utils.force_unit_label({"F": "kgf"}) == "kgf"

    def test_lb(self):
        assert utils.force_unit_label({"F": "lb"}) == "lb"
        assert utils.force_unit_label({"F": "lbf"}) == "lb"

    def test_kip_in(self):
        assert utils.force_unit_label({"F": "kip"}) == "kip"
        assert utils.length_unit_label({"L": "in"}) == "in"

    def test_cm_ft_mm(self):
        assert utils.length_unit_label({"L": "cm"}) == "cm"
        assert utils.length_unit_label({"L": "ft"}) == "ft"
        assert utils.length_unit_label({"L": "mm"}) == "mm"

    def test_long_forms(self):
        assert utils.force_unit_label({"F": "kilonewton", "L": "metre"}) == "kN"
        assert utils.force_unit_label({"F": "giganewton"}) == "GN"
        assert utils.length_unit_label({"L": "millimetre"}) == "mm"

    def test_unrecognised_length_falls_back_to_m(self):
        assert utils.length_unit_label({"L": "yard"}) == "m"
        assert utils.length_unit_label({"L": "cubit"}) == "m"
        # A force unit (or its alias) in the length slot is not a length label
        assert utils.length_unit_label({"L": "kn"}) == "m"
        assert utils.length_unit_label({"L": "kg"}) == "m"

    def test_defaults(self):
        assert utils.force_unit_label({}) == "kN"
        assert utils.length_unit_label({}) == "m"
        assert utils.force_unit_label(None) == "kN"
        assert utils.length_unit_label(None) == "m"
