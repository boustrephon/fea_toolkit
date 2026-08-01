"""Unit tests for the Mander et al. (1988) confined concrete engine.

Covers the core ``mander_confined()`` calculation in
``fea_toolkit.model.confinement`` (rectangular perimeter hoops,
cross-ties, circular spirals, and the unconfined fallback), plus
``ConfinementData`` validation and core-dimension derivation.
"""

import math

import pytest

from fea_toolkit.model.confinement import (
    ConfinementData,
    ConfinementResult,
    mander_confined,
)


# ============================================================================
# ConfinementData validation
# ============================================================================


class TestConfinementDataValidation:
    """Input validation and core-dimension derivation."""

    def test_invalid_fc_raises(self):
        with pytest.raises(ValueError, match="fc must be > 0"):
            ConfinementData(fc=0.0, tie_diameter=0.01, tie_spacing=0.1,
                            tie_fy=420e6, core_bc=0.3, core_dc=0.3)

    def test_invalid_tie_fy_raises(self):
        with pytest.raises(ValueError, match="tie_fy must be > 0"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
                            tie_fy=0.0, core_bc=0.3, core_dc=0.3)

    def test_invalid_tie_diameter_raises(self):
        with pytest.raises(ValueError, match="tie_diameter must be > 0"):
            ConfinementData(fc=30e6, tie_diameter=-0.01, tie_spacing=0.1,
                            tie_fy=420e6, core_bc=0.3, core_dc=0.3)

    def test_negative_spacing_raises(self):
        with pytest.raises(ValueError, match="tie_spacing must be >= 0"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=-0.1,
                            tie_fy=420e6, core_bc=0.3, core_dc=0.3)

    def test_spacing_smaller_than_tie_diameter_raises(self):
        with pytest.raises(ValueError, match="smaller than tie_diameter"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0.005,
                            tie_fy=420e6, core_bc=0.3, core_dc=0.3)

    def test_unsupported_tie_config_raises(self):
        with pytest.raises(ValueError, match="Unsupported tie_config"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
                            tie_fy=420e6, core_bc=0.3, core_dc=0.3,
                            tie_config="octagonal")

    def test_negative_cross_tie_count_raises(self):
        with pytest.raises(ValueError, match="cross_tie_count_x"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
                            tie_fy=420e6, core_bc=0.3, core_dc=0.3,
                            tie_config="cross_tie", cross_tie_count_x=-1)

    def test_zero_core_raises(self):
        with pytest.raises(ValueError, match="core_bc"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
                            tie_fy=420e6, core_bc=0.0, core_dc=0.3,
                            overall_b=0.0, overall_h=0.0, cover=0.0)

    def test_core_derived_from_overall_and_cover(self):
        """Core dimensions derived from overall dims when not given directly."""
        data = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1, tie_fy=420e6,
            core_bc=0.0, core_dc=0.0,
            overall_b=0.4, overall_h=0.5, cover=0.04,
        )
        # centreline-to-centreline: overall - 2*cover - tie diameter
        assert data.core_bc == pytest.approx(0.4 - 2 * 0.04 - 0.01)
        assert data.core_dc == pytest.approx(0.5 - 2 * 0.04 - 0.01)

    def test_spiral_requires_core(self):
        with pytest.raises(ValueError, match="Spiral tie_config"):
            ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0.08,
                            tie_fy=420e6, core_bc=0.0, core_dc=0.0,
                            tie_config="spiral")


# ============================================================================
# mander_confined() — rectangular perimeter hoop
# ============================================================================


class TestManderRectangularStandard:
    """Mander model for a rectangular column with a perimeter hoop.

    Reference case (verified against a hand calculation):
    f'c = 30 MPa, O10 ties @ 100 mm c/c, f_yh = 420 MPa,
    core 300 x 300 mm to hoop centreline, 4O20 long bars each way.
    """

    def _data(self):
        return ConfinementData(
            fc=30e6,
            tie_diameter=0.01,
            tie_spacing=0.1,
            tie_fy=420e6,
            core_bc=0.3,
            core_dc=0.3,
            long_diameter=0.02,
            long_count_x=4,
            long_count_y=4,
            tie_config="standard",
        )

    def test_confined_strength(self):
        res = mander_confined(self._data())
        assert res.fcc == pytest.approx(39759328.07309327, rel=1e-9)

    def test_confined_peak_strain(self):
        res = mander_confined(self._data())
        assert res.ecc == pytest.approx(0.0052531093576977566, rel=1e-9)

    def test_ultimate_strain(self):
        res = mander_confined(self._data())
        assert res.ecu == pytest.approx(0.019486986072088666, rel=1e-9)

    def test_effective_coefficient(self):
        res = mander_confined(self._data())
        assert res.ke == pytest.approx(0.719513616233591, rel=1e-9)
        assert 0.0 < res.ke < 1.0

    def test_volumetric_ratio(self):
        res = mander_confined(self._data())
        # rho_s = 2 legs x area each way summed per the model
        expected_rho = 2.0 * (math.pi * 0.01**2 / 4.0) / (0.1 * 0.3) * 2.0
        assert res.rho_s == pytest.approx(expected_rho, rel=1e-9)

    def test_lateral_confining_stress(self):
        res = mander_confined(self._data())
        assert res.f_l == pytest.approx(1582293.0836420928, rel=1e-9)
        assert res.f_l < res.fcc

    def test_relational_bounds(self):
        res = mander_confined(self._data())
        # Confinement must raise strength and strain above unconfined
        assert res.fcc > 30e6
        assert res.ecc > 0.002
        assert res.ecu > res.ecc
        assert res.ecu <= 0.025

    def test_returns_confinement_result(self):
        assert isinstance(mander_confined(self._data()), ConfinementResult)


# ============================================================================
# mander_confined() — rectangular with cross-ties
# ============================================================================


class TestManderRectangularCrossTie:
    """Cross-tie legs add transverse steel and increase confinement."""

    def _data(self):
        return ConfinementData(
            fc=30e6,
            tie_diameter=0.01,
            tie_spacing=0.1,
            tie_fy=420e6,
            core_bc=0.3,
            core_dc=0.3,
            long_diameter=0.02,
            long_count_x=4,
            long_count_y=4,
            tie_config="cross_tie",
            cross_tie_count_x=2,
            cross_tie_count_y=2,
        )

    def test_cross_tie_strength(self):
        res = mander_confined(self._data())
        assert res.fcc == pytest.approx(47714232.80795595, rel=1e-9)

    def test_cross_tie_peak_strain(self):
        res = mander_confined(self._data())
        assert res.ecc == pytest.approx(0.007904744269318649, rel=1e-9)

    def test_cross_tie_ecu_capped_at_2_5_percent(self):
        res = mander_confined(self._data())
        assert res.ecu == pytest.approx(0.025)

    def test_cross_tie_doubles_volumetric_ratio(self):
        """Two extra legs per direction double the transverse steel ratio."""
        standard = mander_confined(
            ConfinementData(
                fc=30e6, tie_diameter=0.01, tie_spacing=0.1, tie_fy=420e6,
                core_bc=0.3, core_dc=0.3, long_diameter=0.02,
                long_count_x=4, long_count_y=4, tie_config="standard",
            )
        )
        cross = mander_confined(self._data())
        assert cross.rho_s == pytest.approx(2.0 * standard.rho_s, rel=1e-9)

    def test_cross_tie_stronger_than_standard(self):
        standard = mander_confined(
            ConfinementData(
                fc=30e6, tie_diameter=0.01, tie_spacing=0.1, tie_fy=420e6,
                core_bc=0.3, core_dc=0.3, long_diameter=0.02,
                long_count_x=4, long_count_y=4, tie_config="standard",
            )
        )
        cross = mander_confined(self._data())
        assert cross.fcc > standard.fcc


# ============================================================================
# mander_confined() — circular spiral
# ============================================================================


class TestManderCircularSpiral:
    """Mander model for a circular column with spiral/hoop reinforcement.

    Reference case: f'c = 30 MPa, O10 spiral @ 80 mm pitch, f_yh = 420 MPa,
    core diameter 320 mm to hoop centreline, 8O20 long bars.
    """

    def _data(self):
        return ConfinementData(
            fc=30e6,
            tie_diameter=0.01,
            tie_spacing=0.08,
            tie_fy=420e6,
            core_bc=0.32,
            core_dc=0.32,
            long_diameter=0.02,
            long_count_x=8,
            long_count_y=8,
            tie_config="spiral",
        )

    def test_spiral_strength(self):
        res = mander_confined(self._data())
        assert res.fcc == pytest.approx(45646917.21913766, rel=1e-9)

    def test_spiral_peak_strain(self):
        res = mander_confined(self._data())
        assert res.ecc == pytest.approx(0.007215639073045888, rel=1e-9)

    def test_spiral_ultimate_strain(self):
        res = mander_confined(self._data())
        assert res.ecu == pytest.approx(0.019807958271470704, rel=1e-9)

    def test_spiral_ke_near_unity(self):
        # Circular hoops with few long bars give ke close to (but above) 1
        res = mander_confined(self._data())
        assert res.ke == pytest.approx(1.0576171875, rel=1e-9)

    def test_spiral_volumetric_ratio(self):
        res = mander_confined(self._data())
        # rho_s = 4*Ab / (s*Ds) for spirals
        expected_rho = (
            4.0 * (math.pi * 0.01**2 / 4.0) / (0.08 * 0.32)
        )
        assert res.rho_s == pytest.approx(expected_rho, rel=1e-9)

    def test_spiral_bounds(self):
        res = mander_confined(self._data())
        assert res.fcc > 30e6
        assert res.ecc > 0.002
        assert res.ecu <= 0.025


# ============================================================================
# mander_confined() — unconfined fallback + edge cases
# ============================================================================


class TestManderUnconfinedFallback:
    """Behaviour when confinement data is absent or degenerate."""

    def test_zero_spacing_returns_unconfined(self):
        """s = 0 means no confinement - the engine returns f'c properties."""
        res = mander_confined(
            ConfinementData(
                fc=30e6, tie_diameter=0.01, tie_spacing=0.0, tie_fy=420e6,
                core_bc=0.3, core_dc=0.3,
            )
        )
        assert res.fcc == pytest.approx(30e6)
        assert res.ecc == pytest.approx(0.002)
        assert res.ecu == pytest.approx(0.004)
        assert res.ke == 0.0
        assert res.rho_s == 0.0
        assert res.f_l == 0.0

    def test_standard_with_zero_long_counts_still_confines(self):
        """Mesh-only formulas (ke, rho_cc) tolerate zero longitudinal data."""
        res = mander_confined(
            ConfinementData(
                fc=30e6, tie_diameter=0.01, tie_spacing=0.1, tie_fy=420e6,
                core_bc=0.3, core_dc=0.3,
            )
        )
        assert res.fcc > 30e6
        assert res.ke > 0.0

    def test_derived_core_dimensions_flow_through(self):
        """Overall + cover path yields the same result as explicit core."""
        derived = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1, tie_fy=420e6,
            core_bc=0.0, core_dc=0.0,
            overall_b=0.4, overall_h=0.4, cover=0.04,
            long_diameter=0.02, long_count_x=4, long_count_y=4,
        )
        explicit = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1, tie_fy=420e6,
            core_bc=0.31, core_dc=0.31,
            long_diameter=0.02, long_count_x=4, long_count_y=4,
        )
        # 400 - 2*40 - 10 = 310 mm to hoop centreline
        assert mander_confined(derived).fcc == pytest.approx(
            mander_confined(explicit).fcc, rel=1e-9
        )
        # Spot-check a known value for the derived configuration
        assert mander_confined(derived).fcc == pytest.approx(
            39529032.48241702, rel=1e-9
        )
