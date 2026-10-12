"""Tests for ``fea_toolkit.analysis.linear.analysis_load_reaction_summary``.

The function is the pandas-free, all-analyses companion to
``static_load_verification``: one row per static load pattern (applied loads vs
base reactions) plus one row per response-spectrum direction (CQC base shear
and overturning moment), returned as a plain ``list[dict]``.
"""

import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.analysis.linear import (
    _rs_analysis_label,
    analysis_load_reaction_summary,
    render_load_reaction_rows,
)
from fea_toolkit.opensees.preprocessor import Preprocessor


@pytest.fixture
def sample_md():
    return make_sample_model()


@pytest.fixture
def sample_mesh(sample_md):
    pp = Preprocessor({"split_elements": True, "create_shells": False, "verbose": False})
    return pp.run(sample_md)


def test_rs_analysis_label_normalisation():
    assert _rs_analysis_label("X") == "RS-X"
    assert _rs_analysis_label("y") == "RS-Y"
    assert _rs_analysis_label("rs_x") == "RS-X"
    assert _rs_analysis_label("RS-Y") == "RS-Y"


def test_static_rows_compare_applied_against_reaction(sample_md, sample_mesh):
    import openseespy.opensees as ops

    try:
        rows = analysis_load_reaction_summary(sample_md, sample_mesh)
    finally:
        ops.wipe()

    static = [r for r in rows if r["Type"] == "Static"]
    assert static, "expected at least one static row"
    names = {r["Analysis"] for r in static}
    assert {"DEAD", "WIND"} <= names, f"expected DEAD and WIND rows, got {names}"

    fu = sample_md.units.get("F", "?")
    assert fu != "?", "sample model should carry a force unit"
    for r in static:
        # Applied/reaction/Δ columns exist with the real force unit.
        assert f"Applied Fx ({fu})" in r
        assert f"Reaction Fx ({fu})" in r
        assert f"Δ Fx ({fu})" in r
        # The RS headline columns are absent for static rows.
        assert r[f"Base shear V ({fu})"] is None
        assert r["V ratio"] is None

    dead = next(r for r in static if r["Analysis"] == "DEAD")
    # Self-weight equilibrium: applied Fz and reaction Fz are opposite-signed,
    # so their signed sum (Δ) is ~0.
    assert abs(dead[f"Δ Fz ({fu})"]) < 0.05

    wind = next(r for r in static if r["Analysis"] == "WIND")
    # Lateral equilibrium: applied Fx and reaction Fx are opposite-signed,
    # so their signed sum (Δ) is ~0.
    assert abs(wind[f"Δ Fx ({fu})"]) < 0.05


def test_rs_rows_render_base_shear_and_reactions(sample_md, sample_mesh):
    import openseespy.opensees as ops

    rs = {
        "X": {
            "base_shear_cqc": 120.0,
            "base_moment_cqc": 360.0,
            "base_reactions_cqc": {
                "fx": 120.0,
                "fy": 0.0,
                "fz": 0.0,
                "mx": 0.0,
                "my": 360.0,
                "mz": 0.0,
            },
        },
        "Y": {
            "base_shear_cqc": 90.0,
            "base_moment_cqc": 270.0,
            "base_reactions_cqc": {
                "fx": 0.0,
                "fy": 90.0,
                "fz": 0.0,
                "mx": 270.0,
                "my": 0.0,
                "mz": 0.0,
            },
        },
    }
    try:
        rows = analysis_load_reaction_summary(sample_md, sample_mesh, rs_results=rs)
    finally:
        ops.wipe()

    fu = sample_md.units.get("F", "?")
    mu = f"{fu}·{sample_md.units.get('L', '?')}"
    rs_rows = [r for r in rows if r["Type"] == "Response Spectrum"]
    assert [r["Analysis"] for r in rs_rows] == ["RS-X", "RS-Y"]

    x = rs_rows[0]
    assert x[f"Base shear V ({fu})"] == 120.0
    assert x[f"Base moment M ({mu})"] == 360.0
    assert x[f"Reaction Fx ({fu})"] == 120.0
    assert x[f"Reaction My ({mu})"] == 360.0
    # RS has no applied load in the static sense, so those cells stay None.
    assert x[f"Applied Fx ({fu})"] is None
    assert x[f"Δ Fx ({fu})"] is None
    # The expected-shear sanity check is opt-in and absent by default.
    assert x[f"Expected V ({fu})"] is None
    assert x["V ratio"] is None


def test_rs_expected_shear_sanity_is_opt_in(sample_md, sample_mesh):
    import openseespy.opensees as ops

    rs = {
        "X": {
            "base_shear_cqc": 100.0,
            "base_moment_cqc": 300.0,
            "base_reactions_cqc": {
                "fx": 100.0,
                "fy": 0.0,
                "fz": 0.0,
                "mx": 0.0,
                "my": 300.0,
                "mz": 0.0,
            },
        },
    }
    try:
        rows = analysis_load_reaction_summary(
            sample_md,
            sample_mesh,
            rs_results=rs,
            total_weight=1000.0,
            seismic_coefficient=0.2,
        )
    finally:
        ops.wipe()

    fu = sample_md.units.get("F", "?")
    x = next(r for r in rows if r["Type"] == "Response Spectrum")
    assert x[f"Expected V ({fu})"] == 200.0  # total_weight × seismic_coefficient
    assert x["V ratio"] == 0.5  # |V| / Expected V


def test_render_load_reaction_rows_formats_static_and_rs():
    """``render_load_reaction_rows`` is the pure formatting half.

    It takes pre-collected ``{analysis, applied, summed}`` summaries plus RS
    results and produces the same unit-labelled rows as the re-solving wrapper —
    no OpenSees domain, no second pass.
    """
    md = make_sample_model()
    static_summaries = [
        {
            "analysis": "DEAD",
            "applied": {"fx": 0.0, "fy": 0.0, "fz": -50.0, "mx": 0.0, "my": 0.0, "mz": 0.0},
            "summed": {"fx": 0.0, "fy": 0.0, "fz": 50.0, "mx": 0.0, "my": 0.0, "mz": 0.0},
        },
    ]
    rs = {
        "X": {
            "base_shear_cqc": 120.0,
            "base_moment_cqc": 360.0,
            "base_reactions_cqc": {
                "fx": 120.0,
                "fy": 0.0,
                "fz": 0.0,
                "mx": 0.0,
                "my": 360.0,
                "mz": 0.0,
            },
        },
    }
    rows = render_load_reaction_rows(md, static_summaries, rs_results=rs)

    fu = md.units.get("F", "?")
    mu = f"{fu}·{md.units.get('L', '?')}"
    assert [r["Type"] for r in rows] == ["Static", "Response Spectrum"]
    static_row = rows[0]
    assert static_row["Analysis"] == "DEAD"
    assert static_row[f"Δ Fz ({fu})"] == 0.0  # applied -50 + reaction +50
    rs_row = rows[1]
    assert rs_row["Analysis"] == "RS-X"
    assert rs_row[f"Base shear V ({fu})"] == 120.0
    assert rs_row[f"Base moment M ({mu})"] == 360.0


def test_run_case_set_returns_applied_and_summed_summaries(sample_md, sample_mesh):
    """``run_case_set`` returns per-case applied + summed summaries.

    The load/reaction table is driven from the solve itself: each solved case
    carries its factored applied totals and its summed base reactions, so the
    summary needs no second pass.
    """
    import openseespy.opensees as ops

    from fea_toolkit.analysis.linear import run_case_set

    try:
        result = run_case_set(
            sample_md,
            sample_mesh,
            {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}},
        )
    finally:
        ops.wipe()

    summaries = result["summaries"]
    assert len(summaries) == 2
    by_case = {s["case"]: s for s in summaries}
    assert set(by_case) == {"DEAD", "WIND"}
    for s in summaries:
        assert set(s["applied"]) == {"fx", "fy", "fz", "mx", "my", "mz"}
        assert set(s["summed"]) == {"fx", "fy", "fz", "mx", "my", "mz"}
    # Self-weight DEAD case: applied vertical load is non-zero and its signed
    # sum with the reaction is near zero (equilibrium).
    dead = by_case["DEAD"]
    assert abs(dead["applied"]["fz"]) > 0.0
    assert abs(dead["applied"]["fz"] + dead["summed"]["fz"]) < 1e-6
