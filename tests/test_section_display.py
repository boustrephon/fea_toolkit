"""Tests for :mod:`fea_toolkit.model.section_display`.

The display-box back-calculation is pure geometry, shared by the PyVista and
Rhino renderers, so it is exercised here directly with no backend import.
"""

import math

import pytest

from fea_toolkit.model.sap_data import (
    CircularSection,
    GeneralSection,
    ISection,
    Material,
    PipeSection,
    RectangularSection,
    ShellSection,
)
from fea_toolkit.model.section_display import (
    _hollow_rect_dims,
    _solid_rect_dims,
    back_calc_display_box,
    is_steel_section,
    resolve_section_display_box,
    selection_tube_diameter,
    shell_display_thickness,
)

_STEEL = {"Steel": Material(name="Steel", type="Steel")}
_CONC = {"Conc": Material(name="Conc", type="Concrete")}


def _forward_solid(B, D):
    return B * D**3 / 12.0, D * B**3 / 12.0


def _forward_hollow(B, D):
    t = D / 40.0
    Bi, Di = B - 2.0 * t, D - 2.0 * t
    return (B * D**3 - Bi * Di**3) / 12.0, (D * B**3 - Di * Bi**3) / 12.0


class TestSolidRectDims:
    def test_square(self):
        B, D = _solid_rect_dims(8e-6, 8e-6)
        assert pytest.approx((12.0 * 8e-6) ** 0.25) == B
        assert pytest.approx(B) == D

    def test_round_trip(self):
        for B, D in [(0.2, 0.4), (0.1, 0.3), (0.5, 0.15)]:
            I33, I22 = _forward_solid(B, D)
            b, d = _solid_rect_dims(I33, I22)
            assert b == pytest.approx(B, rel=1e-9)
            assert d == pytest.approx(D, rel=1e-9)

    def test_single_inertia_is_square(self):
        B, D = _solid_rect_dims(8e-6, 0.0)
        assert pytest.approx((12.0 * 8e-6) ** 0.25) == B
        assert pytest.approx(B) == D

    def test_no_inertia_is_none(self):
        assert _solid_rect_dims(0.0, 0.0) is None


class TestHollowRectDims:
    def test_round_trip(self):
        for B, D in [(0.2, 0.4), (0.1, 0.3), (0.3, 0.3), (0.15, 0.5)]:
            I33, I22 = _forward_hollow(B, D)
            b, d, t = _hollow_rect_dims(I33, I22)
            assert b == pytest.approx(B, rel=1e-9)
            assert d == pytest.approx(D, rel=1e-9)
            assert t == pytest.approx(D / 40.0, rel=1e-9)

    def test_single_inertia_is_square(self):
        B = 0.3
        I33, _ = _forward_hollow(B, B)
        b, d, t = _hollow_rect_dims(I33, 0.0)
        assert b == pytest.approx(B, rel=1e-9)
        assert d == pytest.approx(B, rel=1e-9)
        assert t == pytest.approx(B / 40.0, rel=1e-9)


class TestSteelDetection:
    def test_by_material_type(self):
        sec = GeneralSection(name="G", shape="General", material="Steel")
        assert is_steel_section(sec, _STEEL)

    def test_not_concrete(self):
        sec = GeneralSection(name="G", shape="General", material="Conc")
        assert not is_steel_section(sec, _CONC)

    def test_shape_fallback_without_materials(self):
        sec = ISection(name="I", shape="I/Wide Flange", material="S", depth=0.3, bf=0.15)
        assert is_steel_section(sec)

    def test_none(self):
        assert not is_steel_section(None)


class TestDisplayBox:
    def test_general_section_solid_for_concrete(self):
        sec = GeneralSection(name="G", shape="General", material="Conc", I33=8e-6, I22=4e-6)
        box = back_calc_display_box(sec, _CONC)
        assert box.source == "back_calc_solid"
        assert box.wall == 0.0

    def test_general_section_hollow_for_steel(self):
        sec = GeneralSection(name="G", shape="General", material="Steel", I33=8e-6, I22=4e-6)
        box = back_calc_display_box(sec, _STEEL)
        assert box.source == "back_calc_hollow"
        assert box.wall == pytest.approx(box.depth / 40.0)

    def test_explicit_dims_skip_back_calc(self):
        sec = RectangularSection(name="R", shape="Rectangular", material="C30", depth=0.4, bf=0.2)
        assert back_calc_display_box(sec) is None
        box = resolve_section_display_box(sec)
        assert box.source == "explicit"
        assert box.breadth == 0.2
        assert box.depth == 0.4

    def test_no_inertia_is_none(self):
        sec = GeneralSection(name="G", shape="General", material="Conc")
        assert back_calc_display_box(sec) is None


class TestSelectionTubeDiameter:
    def test_rectangular_diagonal(self):
        sec = RectangularSection(name="R", shape="Rectangular", material="C30", depth=0.4, bf=0.2)
        assert selection_tube_diameter(sec) == pytest.approx(math.hypot(0.2, 0.4) * 1.05)

    def test_pipe_outer_diameter(self):
        sec = PipeSection(name="P", shape="Pipe", material="S", od=0.3, t=0.01)
        assert selection_tube_diameter(sec) == pytest.approx(0.3 * 1.05)

    def test_circle_diameter(self):
        sec = CircularSection(name="C", shape="Circle", material="C30", diameter=0.5)
        assert selection_tube_diameter(sec) == pytest.approx(0.5 * 1.05)

    def test_general_back_calc(self):
        sec = GeneralSection(name="G", shape="General", material="Conc", I33=8e-6, I22=4e-6)
        box = resolve_section_display_box(sec, _CONC)
        assert selection_tube_diameter(sec, _CONC) == pytest.approx(box.diagonal() * 1.05)

    def test_none(self):
        assert selection_tube_diameter(None) is None
        assert (
            selection_tube_diameter(GeneralSection(name="G", shape="General", material="Conc"))
            is None
        )


class TestShellThickness:
    def test_explicit(self):
        sec = ShellSection(name="S", shape="Shell", material="C30", thickness=0.25)
        assert shell_display_thickness(sec, {"L": "m"}) == 0.25

    def test_default_metres(self):
        assert shell_display_thickness(None, {"L": "m"}) == pytest.approx(0.2)

    def test_default_millimetres(self):
        assert shell_display_thickness(None, {"L": "mm"}) == pytest.approx(200.0)

    def test_zero_thickness_uses_default(self):
        sec = ShellSection(name="S", shape="Shell", material="C30", thickness=0.0)
        assert shell_display_thickness(sec, {"L": "m"}) == pytest.approx(0.2)
