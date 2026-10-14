"""Tests for fea_toolkit.capacity.asce41 — ASCE 41-17 member capacities.

The suite is deliberately heavy on *unit checkers*: the hinge-length formula
must return the same physical length regardless of the model's unit system
(length: m / mm).  Material strengths are authored in SI (Pa) per the capacity
convention; section dimensions and ``elem_length`` are in model units.
"""

import math
from types import SimpleNamespace

import pytest

from fea_toolkit.capacity.asce41 import hinge_length
from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    ISection,
    Material,
    Node,
    SAPModelData,
)

# ── Physical reference beam: 6 m span, UB300 steel, fy = 250 MPa (SI Pa) ──
FY_STEEL_PA = 250.0e6
FC_PA = 30.0e6
_DEFAULT_FC_PA = 30.0e6  # utils.DEFAULT_FC_PA fallback constant
_DEFAULT_FY_STEEL_PA = 250.0e6  # utils.DEFAULT_FY_STEEL_PA fallback constant

# (units, depth_model, length_model, expected_Lp_model)
STEEL_UNIT_CASES = [
    ({"F": "N", "L": "m", "T": "C"}, 0.3, 6.0, 1.98),
    ({"F": "kN", "L": "m", "T": "C"}, 0.3, 6.0, 1.98),
    ({"F": "N", "L": "mm", "T": "C"}, 300.0, 6000.0, 1980.0),
    ({"F": "kN", "L": "mm", "T": "C"}, 300.0, 6000.0, 1980.0),
]

RC_METRE_CASES = [
    # (units, depth_model, cover_model, dia_model, length_model)
    ({"F": "N", "L": "m", "T": "C"}, 0.6, 0.04, 0.02, 6.0),
    ({"F": "kN", "L": "m", "T": "C"}, 0.6, 0.04, 0.02, 6.0),
]


def _ub300(depth):
    """UB300 steel section authored in the given length unit (depth m/mm)."""
    lf = depth / 0.3
    return ISection(
        name="UB300",
        shape="I/Wide Flange",
        material="Steel",
        depth=depth,
        bf=0.15 * lf,
        tf=0.01 * lf,
        tw=0.006 * lf,
        A=8e-3 * lf**2,
        I33=1.2e-4 * lf**4,
        I22=4e-5 * lf**4,
        J=2e-6 * lf**4,
    )


def _steel():
    return Material("Steel", "Steel", Fy=FY_STEEL_PA)


def _concrete():
    return Material("C30", "Concrete", Fc=FC_PA)


def _rc_section(depth, cover, dia, lf):
    return ConcreteRectangularSection(
        name="B1",
        shape="Concrete Rectangular",
        material="C30",
        depth=depth,
        bf=0.3 * lf,
        cover=cover,
        top_bars=4,
        bot_bars=4,
        top_bar_dia=dia,
        bot_bar_dia=dia,
    )


# ── Legacy-adapter fixtures (model-unit materials) ──
def _steel_md(units, fy, e, depth, length):
    """Two-node single-beam SAPModelData with a UB300 steel beam."""
    lf = {"m": 1.0, "mm": 1000.0}[units["L"]]
    nodes = {"1": Node("1", 1, 0, 0, 0), "2": Node("2", 2, length, 0, 0)}
    mats = {"Steel": Material("Steel", "Steel", E_mod=e, Fy=fy)}
    secs = {
        "UB300": ISection(
            name="UB300",
            shape="I/Wide Flange",
            material="Steel",
            depth=depth,
            bf=0.15 * lf,
            tf=0.01 * lf,
            tw=0.006 * lf,
            A=8e-3 * lf**2,
            I33=1.2e-4 * lf**4,
            I22=4e-5 * lf**4,
            J=2e-6 * lf**4,
        ),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials=mats,
        sections=secs,
        frame_elements={},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        units=units,
    )


class TestHingeLengthSteel:
    def test_steel_metre_model(self):
        """UB300 steel beam, fy = 250 MPa (SI Pa), N·m model → Lp = 1.98 m."""
        assert hinge_length(
            _ub300(0.3), None, _steel(), {"F": "N", "L": "m", "T": "C"}, elem_length=6.0
        ) == pytest.approx(1.98, abs=0.01)

    @pytest.mark.parametrize("units, depth, length, expected", STEEL_UNIT_CASES)
    def test_physical_invariance_across_unit_systems(self, units, depth, length, expected):
        """Same physical beam → same Lp, expressed in the model's length units."""
        assert hinge_length(
            _ub300(depth), None, _steel(), units, elem_length=length
        ) == pytest.approx(expected, rel=1e-9)

    def test_length_unit_conversion_internal(self):
        """Same SI fy across m/mm models → Lp differs only by the length factor."""
        lp_m = hinge_length(
            _ub300(0.3), None, _steel(), {"F": "N", "L": "m", "T": "C"}, elem_length=6.0
        )
        lp_mm = hinge_length(
            _ub300(300.0), None, _steel(), {"F": "N", "L": "mm", "T": "C"}, elem_length=6000.0
        )
        assert lp_mm == pytest.approx(lp_m * 1000.0, rel=1e-9)

    def test_legacy_alias_delegates(self):
        """model.checks.compute_asce41_hinge_length still works and delegates."""
        from fea_toolkit.model.checks import compute_asce41_hinge_length

        md = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        assert compute_asce41_hinge_length(md, "UB300", 6.0) == pytest.approx(
            hinge_length(md.sections["UB300"], None, _steel(), md.units, elem_length=6.0),
            rel=1e-12,
        )

    def test_fallback_missing_section(self):
        assert hinge_length(
            None, None, None, {"F": "N", "L": "m", "T": "C"}, elem_length=6.0
        ) == pytest.approx(max(0.05, 6.0 * 0.1), rel=1e-12)

    def test_fallback_missing_material_in_legacy_alias(self):
        """Legacy alias returns 0.1·L when the section's material is absent."""
        from fea_toolkit.model.checks import compute_asce41_hinge_length

        md = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        md.materials = {}
        assert compute_asce41_hinge_length(md, "UB300", 6.0) == pytest.approx(
            max(0.05, 6.0 * 0.1), rel=1e-12
        )

    def test_missing_strength_falls_back_to_default(self):
        """steel=None → default fy (not the 0.1·L fallback)."""
        lp = hinge_length(_ub300(0.3), None, None, {"F": "N", "L": "m", "T": "C"}, elem_length=6.0)
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        expected = 0.08 * 6.0 + 0.022 * 300.0 * fy_mpa / 1000.0  # depth 300 mm
        assert lp == pytest.approx(min(expected, 0.33 * 6.0), rel=1e-9)

    def test_brace_branch(self):
        """Brace member (od/t) uses the 0.015·d_b·f_y term."""
        brace = SimpleNamespace(od=0.2, t=0.01)
        expected = 0.08 * 6.0 + 0.015 * 200.0 * 250.0 / 1000.0  # 1.23 m
        assert hinge_length(
            brace, None, _steel(), {"F": "N", "L": "m", "T": "C"}, elem_length=6.0
        ) == pytest.approx(expected, rel=1e-9)


class TestHingeLengthRC:
    @pytest.mark.parametrize("units, depth, cover, dia, length", RC_METRE_CASES)
    def test_rc_metre_models_agree(self, units, depth, cover, dia, length):
        """RC branch across Pa/kPa metre models → same Lp (≈0.3913 m)."""
        sec = _rc_section(depth, cover, dia, 1.0)
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        fc_mpa = FC_PA / 1e6
        db_mm = dia * 1000.0  # model length (m) → mm
        expected = 0.05 * 6.0 + 0.1 * db_mm * fy_mpa / math.sqrt(fc_mpa) / 1000.0
        assert hinge_length(sec, _concrete(), None, units, elem_length=length) == pytest.approx(
            expected, rel=1e-9
        )

    def test_rc_mm_model_returns_mm(self):
        """N·mm model returns Lp in mm (same physical length as the m models)."""
        sec = _rc_section(600.0, 40.0, 20.0, 1000.0)
        lp_mm = hinge_length(
            sec, _concrete(), None, {"F": "N", "L": "mm", "T": "C"}, elem_length=6000.0
        )
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        expected_m = 0.05 * 6.0 + 0.1 * 20.0 * fy_mpa / math.sqrt(30.0) / 1000.0
        assert lp_mm == pytest.approx(expected_m * 1000.0, rel=1e-9)

    def test_rc_uses_rebar_diameter_not_depth(self):
        """db for RC = longitudinal rebar diameter, not the section depth."""
        sec = _rc_section(0.6, 0.04, 0.02, 1.0)
        lp = hinge_length(sec, _concrete(), None, {"F": "N", "L": "m", "T": "C"}, elem_length=6.0)
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        expected = 0.05 * 6.0 + 0.1 * 20.0 * fy_mpa / math.sqrt(30.0) / 1000.0
        assert lp == pytest.approx(expected, rel=1e-9)
        assert lp < 1.0


def test_capacity_package_reexports_hinge_length():
    from fea_toolkit.capacity import hinge_length as package_hinge_length

    assert package_hinge_length is hinge_length
