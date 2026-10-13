"""Tests for the pure-geometry section property builders (M2 hardening).

Golden values are hand-checked from the standard section-property formulas
(``A``/``I33``/``I22``/``J``/``Z33``/``Z22``), not from the reference's
(known-stale) doctests.
"""

from __future__ import annotations

import math

import pytest

from fea_toolkit.model.section_properties import (
    angle_props,
    box_props,
    channel_props,
    circle_props,
    double_angle_props,
    i_props,
    pipe_props,
    rect_props,
    tee_props,
)


def test_rect_props():
    props = rect_props(0.5, 0.3)
    assert props["A"] == pytest.approx(0.15)
    assert props["I33"] == pytest.approx(0.3 * 0.5**3 / 12)  # strong axis
    assert props["I22"] == pytest.approx(0.5 * 0.3**3 / 12)  # weak axis
    assert props["Z33"] == pytest.approx(0.3 * 0.5**2 / 6)
    assert props["Z22"] == pytest.approx(0.5 * 0.3**2 / 6)
    assert props["J"] > 0


def test_rect_props_zero_for_invalid_dims():
    assert rect_props(0.0, 0.3)["I33"] == 0.0
    assert rect_props(0.5, -0.1)["A"] == 0.0


def test_circle_props():
    props = circle_props(0.4)
    assert props["A"] == pytest.approx(math.pi * 0.4**2 / 4)
    assert props["I33"] == pytest.approx(math.pi * 0.4**4 / 64)
    assert props["I22"] == pytest.approx(props["I33"])
    assert props["J"] == pytest.approx(2 * props["I33"])  # polar
    assert props["Z33"] == pytest.approx(math.pi * 0.4**3 / 32)


def test_pipe_props():
    od, t = 0.3, 0.01
    di = od - 2 * t
    props = pipe_props(od, t)
    assert props["A"] == pytest.approx(math.pi * (od**2 - di**2) / 4)
    assert props["I33"] == pytest.approx(math.pi * (od**4 - di**4) / 64)
    assert props["I22"] == pytest.approx(props["I33"])
    assert props["J"] == pytest.approx(2 * props["I33"])


def test_pipe_props_degenerate():
    assert pipe_props(0.3, 0.0)["A"] == 0.0
    assert pipe_props(0.3, 0.2)["I33"] == 0.0  # wall >= radius


def test_i_props_golden():
    # depth=15, bf=12, tf=3, tw=2 → textbook A/I33/I22.
    props = i_props(15.0, 12.0, 3.0, 2.0)
    assert props["A"] == pytest.approx(90.0)
    assert props["I33"] == pytest.approx(2767.5)
    assert props["I22"] == pytest.approx(870.0)
    assert props["J"] == pytest.approx(240.0)  # (2·b·tf³ + web·tw³)/3
    assert props["Z33"] == pytest.approx(2 * 2767.5 / 15)
    assert props["Z22"] == pytest.approx(2 * 870.0 / 12)


def test_channel_props_golden():
    # depth=15, bf=12, tf=3, tw=2; centroidal weak axis via Cy=5.
    props = channel_props(15.0, 12.0, 3.0, 2.0)
    assert props["A"] == pytest.approx(90.0)
    assert props["I33"] == pytest.approx(2767.5)
    assert props["I22"] == pytest.approx(1230.0)
    assert props["J"] == pytest.approx(240.0)
    assert props["Z33"] == pytest.approx(2 * 2767.5 / 15)
    assert props["Z22"] == 0.0  # asymmetric weak axis


def test_box_props_golden():
    props = box_props(0.2, 0.2, 0.01, 0.01)
    assert props["A"] == pytest.approx(0.2**2 - 0.18**2)
    assert props["I33"] == pytest.approx((0.2**4 - 0.18**4) / 12)
    assert props["I22"] == pytest.approx(props["I33"])  # square box
    # Bredt thin-wall torsion.
    u = v = 0.2 - 0.01
    j = 2 * u**2 * v**2 * 0.01 * 0.01 / (u * 0.01 + v * 0.01)
    assert props["J"] == pytest.approx(j)
    assert props["Z33"] == pytest.approx(2 * props["I33"] / 0.2)


def test_asymmetric_shapes_have_zero_elastic_moduli():
    assert angle_props(0.1, 0.1, 0.008, 0.008)["Z33"] == 0.0
    assert angle_props(0.1, 0.1, 0.008, 0.008)["Z22"] == 0.0
    assert tee_props(0.2, 0.2, 0.01, 0.01)["Z33"] == 0.0
    assert double_angle_props(0.1, 0.1, 0.008, 0.008, 0.01)["Z33"] == 0.0


def test_angle_props():
    props = angle_props(0.1, 0.1, 0.008, 0.008)
    assert props["A"] == pytest.approx(0.1**2 - 0.092**2)
    assert props["I33"] == pytest.approx(props["I22"])  # equal legs
    assert props["I33"] > 0
    assert props["J"] > 0


def test_tee_props():
    props = tee_props(0.2, 0.2, 0.01, 0.01)
    assert props["A"] == pytest.approx(0.19 * 0.01 + 0.2 * 0.01)
    assert props["I33"] > 0
    assert props["I22"] > 0
    assert props["J"] > 0


def test_double_angle_props():
    d, b, tf, tw, dis = 0.1, 0.1, 0.008, 0.008, 0.01
    b0 = 0.5 * (b - dis)
    props = double_angle_props(d, b, tf, tw, dis)
    assert props["A"] == pytest.approx(2 * (b0 * d - (d - tf) * (b0 - tw)))
    assert props["I33"] > 0
    assert props["I22"] > 0
    assert props["J"] > 0
