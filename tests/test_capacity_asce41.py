"""Tests for fea_toolkit.capacity.asce41 — ASCE 41-17 member capacities.

The suite is deliberately heavy on *unit checkers*: the hinge-length formula
must return the same physical length regardless of the model's unit system
(stress: Pa / kPa / MPa / kN-mm²; length: m / mm), with material strengths
and section dimensions authored in the model's own units.
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

# ── Physical reference beam: 6 m span, UB300 steel, fy = 250 MPa ───
# For each unit system, the model values are authored in THAT system.
STEEL_UNIT_CASES = [
    # (units, fy_model, e_model, depth_model, length_model, expected_Lp)
    ({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0, 1.98),  # Pa, m
    ({"F": "kN", "L": "m", "T": "C"}, 250.0e3, 2.0e8, 0.3, 6.0, 1.98),  # kPa, m
    ({"F": "N", "L": "mm", "T": "C"}, 250.0, 2.0e5, 300.0, 6000.0, 1980.0),  # MPa, mm
    ({"F": "kN", "L": "mm", "T": "C"}, 0.25, 200.0, 300.0, 6000.0, 1980.0),  # kN/mm², mm
]

# Physical RC beam: 6 m span, 400×600, 4D20 top+bottom, fc = 30 MPa.
RC_METRE_CASES = [
    # (units, fc_model, depth_model, cover_model, dia_model, length_model)
    ({"F": "N", "L": "m", "T": "C"}, 30.0e6, 0.6, 0.04, 0.02, 6.0),  # Pa, m
    ({"F": "kN", "L": "m", "T": "C"}, 30.0e3, 0.6, 0.04, 0.02, 6.0),  # kPa, m
]

_DEFAULT_FC_PA = 30.0e6  # utils.DEFAULT_FC_PA fallback constant
_DEFAULT_FY_STEEL_PA = 250.0e6  # utils.DEFAULT_FY_STEEL_PA fallback constant


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


def _rc_md(units, fc_model, depth, cover, dia, length):
    """Single RC beam (concrete material, no rebar material → fy fallback)."""
    lf = {"m": 1.0, "mm": 1000.0}[units["L"]]
    nodes = {"1": Node("1", 1, 0, 0, 0), "2": Node("2", 2, length, 0, 0)}
    mats = {"C30": Material("C30", "Concrete", Fc=fc_model)}
    secs = {
        "B1": ConcreteRectangularSection(
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
    def test_legacy_value_metre_model(self):
        """Migrated regression: steel UB300 beam, N·m model → Lp = 1.98 m."""
        md = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        assert hinge_length(md, "UB300", 6.0) == pytest.approx(1.98, abs=0.01)

    @pytest.mark.parametrize(
        "units, fy_model, e_model, depth_model, length_model, expected_lp",
        STEEL_UNIT_CASES,
    )
    def test_physical_invariance_across_unit_systems(
        self, units, fy_model, e_model, depth_model, length_model, expected_lp
    ):
        """Same physical beam → same Lp, expressed in the model's length units."""
        md = _steel_md(units, fy=fy_model, e=e_model, depth=depth_model, length=length_model)
        assert hinge_length(md, "UB300", length_model) == pytest.approx(expected_lp, rel=1e-9)

    def test_stress_unit_conversion_internal(self):
        """Model kPa strengths must convert to Pa internally (kN·m model)."""
        md_knm = _steel_md({"F": "kN", "L": "m", "T": "C"}, 250.0e3, 2.0e8, 0.3, 6.0)
        md_nm = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        # Same physical fy (250 MPa), different stress units → identical Lp.
        assert hinge_length(md_knm, "UB300", 6.0) == pytest.approx(
            hinge_length(md_nm, "UB300", 6.0), rel=1e-9
        )

    def test_legacy_alias_delegates(self):
        """model.checks.compute_asce41_hinge_length still works and delegates."""
        from fea_toolkit.model.checks import compute_asce41_hinge_length

        md = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        assert compute_asce41_hinge_length(md, "UB300", 6.0) == pytest.approx(
            hinge_length(md, "UB300", 6.0), rel=1e-12
        )

    def test_fallback_missing_section(self):
        md = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        assert hinge_length(md, "NO_SUCH_SECTION", 6.0) == pytest.approx(
            max(0.05, 6.0 * 0.1), rel=1e-12
        )

    def test_fallback_missing_material(self):
        md = _steel_md({"F": "N", "L": "m", "T": "C"}, 250.0e6, 2.0e11, 0.3, 6.0)
        md.materials = {}
        assert hinge_length(md, "UB300", 6.0) == pytest.approx(max(0.05, 6.0 * 0.1), rel=1e-12)

    def test_brace_branch(self):
        """Brace member (od/t) uses the 0.015·d_b·f_y term."""
        brace = SimpleNamespace(material="Steel", od=0.2, t=0.01)
        md = SimpleNamespace(
            sections={"CHS200": brace},
            materials={"Steel": Material("Steel", "Steel", Fy=250.0e6)},
            units={"F": "N", "L": "m", "T": "C"},
        )
        expected = 0.08 * 6.0 + 0.015 * 200.0 * 250.0 / 1000.0  # 1.23 m
        assert hinge_length(md, "CHS200", 6.0) == pytest.approx(expected, rel=1e-9)

    def test_units_dict_fallback_path(self):
        """MeshModel-style object (units dict, no stress_factor properties)."""
        sec = ISection(
            name="UB300",
            shape="I/Wide Flange",
            material="Steel",
            depth=0.3,
            bf=0.15,
            tf=0.01,
            tw=0.006,
            A=8e-3,
            I33=1.2e-4,
            I22=4e-5,
            J=2e-6,
        )
        md = SimpleNamespace(
            sections={"UB300": sec},
            materials={"Steel": Material("Steel", "Steel", Fy=250.0e3)},  # kPa
            units={"F": "kN", "L": "m", "T": "C"},
        )
        assert hinge_length(md, "UB300", 6.0) == pytest.approx(1.98, rel=1e-9)


class TestHingeLengthRC:
    @pytest.mark.parametrize(
        "units, fc_model, depth_model, cover_model, dia_model, length_model",
        RC_METRE_CASES,
    )
    def test_rc_metre_models_agree(
        self, units, fc_model, depth_model, cover_model, dia_model, length_model
    ):
        """RC branch across Pa/kPa metre models → same Lp (≈0.3913 m)."""
        md = _rc_md(units, fc_model, depth_model, cover_model, dia_model, length_model)
        # fy falls back to the SI Pa constant (concrete has no Fy); the RC
        # expression is 0.05·L + 0.1·d_b·f_y/√f_c, with d_b in mm.
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        fc_mpa = fc_model / 1e6 if units["F"] == "N" else fc_model / 1e3  # kPa → MPa
        db_mm = dia_model * 1000.0  # model length (m) → mm
        expected = 0.05 * 6.0 + 0.1 * db_mm * fy_mpa / math.sqrt(fc_mpa) / 1000.0
        assert hinge_length(md, "B1", length_model) == pytest.approx(expected, rel=1e-9)

    def test_rc_mm_model_returns_mm(self):
        """N·mm model returns Lp in mm (same physical length as the m models)."""
        md = _rc_md(
            {"F": "N", "L": "mm", "T": "C"},
            fc_model=30.0,
            depth=600.0,
            cover=40.0,
            dia=20.0,
            length=6000.0,
        )
        lp_mm = hinge_length(md, "B1", 6000.0)
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        expected_m = 0.05 * 6.0 + 0.1 * 20.0 * fy_mpa / math.sqrt(30.0) / 1000.0
        assert lp_mm == pytest.approx(expected_m * 1000.0, rel=1e-9)

    def test_rc_uses_rebar_diameter_not_depth(self):
        """db for RC = longitudinal rebar diameter, not the section depth."""
        md = _rc_md({"F": "N", "L": "m", "T": "C"}, 30.0e6, 0.6, 0.04, 0.02, 6.0)
        lp = hinge_length(md, "B1", 6.0)
        fy_mpa = _DEFAULT_FY_STEEL_PA / 1e6
        # db = 20 mm (dia), NOT 600 mm (depth): using depth would give 0.3 + 2.74.
        expected = 0.05 * 6.0 + 0.1 * 20.0 * fy_mpa / math.sqrt(30.0) / 1000.0
        assert lp == pytest.approx(expected, rel=1e-9)
        assert lp < 1.0


def test_capacity_package_reexports_hinge_length():
    from fea_toolkit.capacity import hinge_length as package_hinge_length

    assert package_hinge_length is hinge_length
