"""P-Δ support in the SAC benchmark (``add_pdelta_gravity`` / ``builder_config``)."""

import pytest

from fea_toolkit._unit_scaling import g_from_units
from fea_toolkit.benchmarks.sac import (
    SAC3_UNITS,
    add_pdelta_gravity,
    build_builder,
    builder_config,
    load_model,
)

# Total seismic mass of the 3-story frame (one frame, half the building): kg.
SEISMIC_MASS = 4.7835e5 + 4.7835e5 + 5.1779e5


def test_add_pdelta_gravity_totals_1_4x_seismic_weight():
    md = load_model(building=3, nonlinear=False)
    add_pdelta_gravity(md)
    total = sum(-jl.fz for jl in md.joint_loads if jl.pattern == "GRAV")
    expected = 1.4 * SEISMIC_MASS * g_from_units(SAC3_UNITS)
    assert md.joint_loads
    assert pytest.approx(total, rel=1e-12) == expected


def test_add_pdelta_gravity_idempotent_and_replaces():
    md = load_model(building=3, nonlinear=False)
    add_pdelta_gravity(md)
    n = len(md.joint_loads)
    add_pdelta_gravity(md)  # second call replaces, not appends
    assert len(md.joint_loads) == n


def test_builder_config_pdelta_switches_geom_transf():
    assert builder_config(pdelta=False)["geom_transf_type"] == "Linear"
    assert builder_config(pdelta=True)["geom_transf_type"] == "PDelta"


def test_build_builder_pdelta_gravity_smoke():
    from openseespy.opensees import wipe

    wipe()
    try:
        b = build_builder(
            building=3,
            nonlinear=True,
            backbone="imk",
            hinge_model="lumped",
            derive_column_axial_ratio_by_member=True,
            pdelta=True,
            pdelta_gravity=True,
        )
        assert b.config["geom_transf_type"] == "PDelta"
        assert [jl for jl in b.mesh_model.joint_loads if jl.pattern == "GRAV"]
    finally:
        wipe()
