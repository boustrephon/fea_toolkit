"""IK-2005 generic single-bay frame generator — data layer + backbone mapping.

These tests are pure Python (no OpenSees): they check the SI unit conversion,
the frame geometry/masses, and the §5.3.2 backbone → ``imk_params`` mapping.
"""

import pytest

from fea_toolkit.benchmarks.generic_frame import (
    ALPHA_C,
    base_spring_props,
    beam_spring_props,
    build_model,
    imk_params,
    span_m,
    steel_e_pa,
    storey_height_m,
)
from fea_toolkit.benchmarks.generic_frame_data import FRAME_CODES, FRAME_DATA, STOREY_HEIGHT_IN


def test_geometry_is_si():
    # 12 ft → 3.6576 m, 24 ft → 7.3152 m.
    assert storey_height_m() == pytest.approx(3.6576)
    assert span_m() == pytest.approx(7.3152)
    assert span_m() == pytest.approx(2.0 * storey_height_m())


def test_steel_modulus_is_si():
    # 29000 ksi → 1.99948e11 Pa.
    assert steel_e_pa() == pytest.approx(29000 * 6.894757293168361e6)


def test_build_model_geometry_and_masses():
    md = build_model("0303")
    # 3 storeys → 2 lines × 4 levels = 8 nodes; 6 columns + 3 beams = 9 elements.
    assert len(md.nodes) == 8
    assert len(md.frame_elements) == 9
    assert len(md.sections) == 3
    assert md.units == {"F": "N", "L": "m", "T": "C"}
    # Total mass = W/g · Σ(Wi/W) = 600 kip / 9.80665 × 0.999 (the three 0.333
    # ratios sum to 0.999 after rounding 1/3).
    g = 9.80665
    total = sum(md.added_masses.values())
    assert total == pytest.approx(600 * 4448.2216152605 / g * 0.999)
    # Equal per floor: each of the 3 floors carries one third of the mass.
    for level in (1, 2, 3):
        floor = md.added_masses[f"{level}-0"] + md.added_masses[f"{level}-1"]
        assert floor == pytest.approx(total / 3.0)
    # Base is fixed (the column-base spring k0=Kc anchors the column); other
    # nodes are free in-plane (ux, uz, ry) with out-of-plane DOFs restrained.
    assert md.restraints["0-0"].dofs == [1, 1, 1, 1, 1, 1]
    assert md.restraints["1-0"].dofs == [0, 1, 0, 1, 0, 1]


def test_sections_share_inertia_per_storey():
    md = build_model("0909")
    # Storey 1 section carries I1 (converted), storey 2 carries I1 × I2/I1.
    data = __import__("fea_toolkit.benchmarks.generic_frame_data", fromlist=["FRAME_DATA"])
    d = data.FRAME_DATA["0909"]
    i1_m4 = d["norm"]["I1"] * (0.0254**4)
    assert pytest.approx(i1_m4) == md.sections["sec1"].I33
    assert pytest.approx(i1_m4 * d["stories"][2]["Ii_I1"]) == md.sections["sec2"].I33


def test_imk_params_reference_backbone():
    # ke=1e8, my=5e5, theta_y = my/ke = 0.005.
    p = imk_params(ke=1e8, my=5e5, theta_y=0.005)
    # mc_my = 1 + αs·(δc/δy − 1) = 1 + 0.03·3 = 1.09.
    assert p["mc_my"] == pytest.approx(1.09)
    # θp = (δc/δy − 1)·θy = 3·0.005 = 0.015.
    assert p["theta_p_pos"] == pytest.approx(0.015)
    # θpc = (mc_my − res)·My/(|αc|·ke) = 1.09·5e5/(0.1·1e8) = 0.0545.
    assert p["theta_pc_pos"] == pytest.approx(1.09 * 5e5 / (0.1 * 1e8))
    assert p["res_pos"] == 0.0
    assert p["lamda_S"] == p["lamda_C"] == p["lamda_A"] == p["lamda_K"] == 0.0


def test_imk_params_post_capping_drops_to_zero_at_alpha_c():
    # The post-capping slope, in member-end terms, is αc·ke: over θpc the moment
    # drops from Mc = mc_my·My to 0 (residual = 0).
    ke, my, theta_y = 1e8, 5e5, 0.005
    p = imk_params(ke=ke, my=my, theta_y=theta_y)
    drop = (p["mc_my"] - p["res_pos"]) * my / p["theta_pc_pos"]
    assert drop == pytest.approx(abs(ALPHA_C) * ke)


def test_base_and_beam_spring_props_are_si():
    base = base_spring_props("0303")
    # Kc = 8458858 kip·in/rad → N·m/rad (×112.9848…).
    assert base["Kc"] == pytest.approx(8458858 * 4448.2216152605 * 0.0254)
    assert base["Myc"] == pytest.approx(21450 * 4448.2216152605 * 0.0254)
    beam = beam_spring_props("0303", storey=1)
    assert beam["Myb"] == pytest.approx(42440 * 4448.2216152605 * 0.0254)
    assert beam["theta_yb"] == pytest.approx(0.003)


def test_all_frames_build():
    for code in FRAME_CODES:
        md = build_model(code)
        n = md.sections.__len__()
        assert len(md.frame_elements) == 3 * n  # 2N columns + N beams


def test_gamma_yield_base_shear_is_unity():
    """The frames are designed (IK-2005 Appendix B) for ``γ = Vy/W = 1.0``.

    First-yield base shear (in the table's kip/in units): the first-storey beam
    sway yields at ``V = 2·Myb1/h`` (portal, ``M_beam = V·h/2``) and the column
    base at ``V = 4·Myc/h`` (``M_base = V·h/4``); the lower one is ``Vy``.
    """
    for code in FRAME_CODES:
        d = FRAME_DATA[code]
        w = d["norm"]["W"]
        h = STOREY_HEIGHT_IN
        myb1 = d["norm"]["Myb2"] * d["stories"][1]["Mybi_Myb2"]
        myc = d["stories"][0]["Myc"]
        gamma = min(2 * myb1 / h, 4 * myc / h) / w
        assert gamma == pytest.approx(1.0, rel=0.02), f"{code}: γ = {gamma:.4f}"
