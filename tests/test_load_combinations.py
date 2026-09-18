"""Tests for load-combination trees and composite-result generation.

Covers ``fea_toolkit.model.load_combinations`` — the flat-dictionary → tree →
composite pipeline, including the ± spectrum fork, the two Envelope
strategies, SRSS, nesting, cycle detection and duplicate handling.
"""

import numpy as np
import pytest

from fea_toolkit.model.load_combinations import (
    CompositeLoadCase,
    apply_composite_load_case,
    build_combo_tree,
    build_combo_tree_dict,
    calculate_aggregate_factors,
    classify_combination_refs,
    expand_linear_combination,
    generate_combination_results,
    generate_composite_results,
    to_e2k_combo_dict,
)
from fea_toolkit.model.sap_data import LoadCase, LoadCombination, LoadCombinationEntry

# ── Helpers ────────────────────────────────────────────────────────────


def _case(name: str, case_type: str = "LinStatic") -> LoadCase:
    return LoadCase(name, case_type, "Prog Det", "Dead", "Prog Det", "Non-Composite")


def _combo(name: str, combo_type: str, refs) -> LoadCombination:
    return LoadCombination(name, combo_type, [LoadCombinationEntry(n, f) for n, f in refs])


@pytest.fixture
def model():
    """A small model exercising every operator / reference kind."""
    cases = {
        n: _case(n, ct)
        for n, ct in [
            ("DEAD", "LinStatic"),
            ("SDL", "LinStatic"),
            ("WIND", "LinStatic"),
            ("RSX", "LinRespSpec"),
            ("RSY", "LinRespSpec"),
        ]
    }
    combos = {
        "GRAV": _combo("GRAV", "Linear Add", [("DEAD", 1.2), ("SDL", 1.5)]),
        "WINDX": _combo("WINDX", "Linear Add", [("WIND", 1.0)]),
        "SEISM": _combo("SEISM", "Linear Add", [("DEAD", 1.0), ("RSX", 1.0)]),
        "TWO_SPEC": _combo("TWO_SPEC", "Linear Add", [("RSX", 1.0), ("RSY", 1.0)]),
        "SRSS_C": _combo("SRSS_C", "SRSS", [("RSX", 1.0), ("RSY", 0.3)]),
        "MIX_SRSS": _combo("MIX_SRSS", "Linear Add", [("DEAD", 1.2), ("SRSS_C", 1.0)]),
        "NESTED": _combo("NESTED", "Linear Add", [("GRAV", 1.3), ("WINDX", 1.4)]),
        "ENV": _combo("ENV", "Envelope", [("GRAV", 1.0), ("WINDX", 1.0)]),
        "DUP": _combo("DUP", "Linear Add", [("DEAD", 1.0), ("DEAD", 0.5)]),
    }
    classify_combination_refs(combos, cases)
    return cases, combos


# ── Trees ──────────────────────────────────────────────────────────────


def test_build_tree_shape(model):
    _cases, combos = model
    tree = build_combo_tree(combos, "NESTED")
    assert tree.root.name == "NESTED"
    assert tree.root.node_type == "Linear Add"
    child_names = [c.name for c in tree.root.children]
    assert child_names == ["GRAV", "WINDX"]  # branches first, then leaves
    grav = tree.root.children[0]
    assert [(c.name, c.weight) for c in grav.children] == [("DEAD", 1.2), ("SDL", 1.5)]


def test_aggregate_factors_multiply_and_sum(model):
    _cases, combos = model
    factors = calculate_aggregate_factors(build_combo_tree(combos, "NESTED"))
    assert factors == pytest.approx({"DEAD": 1.56, "SDL": 1.95, "WIND": 1.4})
    # A repeated load case is summed, not overwritten.
    assert calculate_aggregate_factors(build_combo_tree(combos, "DUP")) == {"DEAD": 1.5}


def test_build_tree_cycle_raises():
    cases = {"DEAD": _case("DEAD")}
    combos = {
        "A": _combo("A", "Linear Add", [("B", 1.0)]),
        "B": _combo("B", "Linear Add", [("A", 1.0)]),
    }
    classify_combination_refs(combos, cases)
    with pytest.raises(ValueError, match="Cyclic"):
        build_combo_tree(combos, "A")
    with pytest.raises(ValueError, match="Cyclic"):
        generate_combination_results(combos["A"], cases, combos)


def test_build_tree_unknown_root():
    with pytest.raises(KeyError):
        build_combo_tree({}, "NOPE")


def test_tree_dict_splits_envelope_without_double_add(model):
    _cases, combos = model
    trees = build_combo_tree_dict(combos, "ENV")
    assert sorted(trees) == ["GRAV", "WINDX"]
    # Each subtree's children are added exactly once.
    assert [c.name for c in trees["GRAV"].root.children] == ["DEAD", "SDL"]
    assert len(trees["GRAV"].root.children) == 2


def test_tree_dict_non_envelope_is_single(model):
    _cases, combos = model
    assert list(build_combo_tree_dict(combos, "GRAV")) == ["GRAV"]


def test_tree_dict_envelope_includes_direct_case(model):
    """An envelope may reference load cases directly — they become subtrees."""
    cases, combos = model
    combos["CASEENV"] = _combo("CASEENV", "Envelope", [("DEAD", 1.0), ("WIND", 1.0)])
    classify_combination_refs(combos, cases)
    trees = build_combo_tree_dict(combos, "CASEENV")
    assert sorted(trees) == ["DEAD", "WIND"]
    assert calculate_aggregate_factors(trees["DEAD"]) == {"DEAD": 1.0}


def test_to_e2k_combo_dict_mapping(model):
    _cases, combos = model
    e2k = to_e2k_combo_dict(combos)
    assert e2k["NESTED"]["TYPE"] == "Linear Add"
    assert e2k["NESTED"]["LOADCOMBO"] == [("GRAV", 1.3), ("WINDX", 1.4)]
    assert e2k["NESTED"]["LOADCASE"] == []
    assert e2k["GRAV"]["LOADCASE"] == [("DEAD", 1.2), ("SDL", 1.5)]
    assert e2k["GRAV"]["LOADCOMBO"] == []
    assert e2k["GRAV"]["OTHER"] == []


# ── Generation: Linear Add ─────────────────────────────────────────────


def test_linear_single_composite(model):
    cases, combos = model
    results = generate_combination_results(combos["GRAV"], cases, combos)
    assert len(results) == 1
    assert results[0].name == "GRAV"
    assert results[0].operator == "linear"
    assert results[0].cases == {"DEAD": 1.2, "SDL": 1.5}
    assert results[0].children == []


def test_expand_linear_combination(model):
    cases, combos = model
    assert expand_linear_combination(combos["GRAV"], cases, combos) == {
        "DEAD": 1.2,
        "SDL": 1.5,
    }
    # Nested combinations resolve to leaf factors.
    assert expand_linear_combination(combos["NESTED"], cases, combos) == pytest.approx(
        {"DEAD": 1.56, "SDL": 1.95, "WIND": 1.4}
    )


def test_expand_raises_when_not_single(model):
    cases, combos = model
    with pytest.raises(ValueError, match="single linear factor list"):
        expand_linear_combination(combos["SEISM"], cases, combos)
    with pytest.raises(ValueError, match="single linear factor list"):
        expand_linear_combination(combos["ENV"], cases, combos)


def test_spectrum_forks_signs(model):
    """A spectrum case mixed with static loads yields + and - composites."""
    cases, combos = model
    results = generate_combination_results(combos["SEISM"], cases, combos)
    assert len(results) == 2
    factors = [r.cases["RSX"] for r in results]
    assert factors == [1.0, -1.0]
    assert all(r.cases["DEAD"] == 1.0 for r in results)


def test_pure_spectrum_does_not_fork(model):
    """A combination of only spectrum cases is already a magnitude."""
    cases, combos = model
    results = generate_combination_results(combos["TWO_SPEC"], cases, combos)
    assert len(results) == 1
    assert results[0].cases == {"RSX": 1.0, "RSY": 1.0}


def test_two_spectra_give_four_variants(model):
    """n independent spectrum leaves produce 2**n composites."""
    cases, combos = model
    combos["MULTI"] = _combo("MULTI", "Linear Add", [("DEAD", 1.0), ("RSX", 1.0), ("RSY", 1.0)])
    classify_combination_refs(combos, cases)
    results = generate_combination_results(combos["MULTI"], cases, combos)
    assert len(results) == 4
    signs = {(r.cases["RSX"], r.cases["RSY"]) for r in results}
    assert signs == {(1.0, 1.0), (1.0, -1.0), (-1.0, 1.0), (-1.0, -1.0)}


# ── Generation: SRSS ───────────────────────────────────────────────────


def test_srss_single_magnitude(model):
    cases, combos = model
    results = generate_combination_results(combos["SRSS_C"], cases, combos)
    assert len(results) == 1
    assert results[0].operator == "srss"
    assert [c.cases for _f, c in results[0].children] == [{"RSX": 1.0}, {"RSY": 0.3}]


def test_srss_child_forks_in_linear_parent(model):
    """An SRSS result is a magnitude, so it forks when mixed with statics."""
    cases, combos = model
    results = generate_combination_results(combos["MIX_SRSS"], cases, combos)
    assert len(results) == 2
    assert all(r.operator == "linear" for r in results)
    assert all(r.cases == {"DEAD": 1.2} for r in results)
    assert [r.children[0][0] for r in results] == [1.0, -1.0]
    assert all(r.children[0][1].operator == "srss" for r in results)


# ── Generation: Envelope ───────────────────────────────────────────────


def test_envelope_maxmin(model):
    cases, combos = model
    results = generate_combination_results(combos["ENV"], cases, combos)
    assert [r.name for r in results] == ["ENV [max]", "ENV [min]"]
    assert [r.operator for r in results] == ["max", "min"]
    for result in results:
        assert [c.name for _f, c in result.children] == ["GRAV", "WINDX"]


def test_envelope_maxmin_signs_spectrum(model):
    """For a magnitude constituent, max uses + and min uses -."""
    cases, combos = model
    # TWO_SPEC is a combination of response-spectrum cases, so its result is
    # itself a magnitude.
    combos["EQENV"] = _combo("EQENV", "Envelope", [("TWO_SPEC", 1.0)])
    classify_combination_refs(combos, cases)
    max_comp, min_comp = generate_combination_results(combos["EQENV"], cases, combos)
    assert [f for f, _c in max_comp.children] == [1.0]
    assert [f for f, _c in min_comp.children] == [-1.0]


def test_envelope_per_path(model):
    cases, combos = model
    results = generate_combination_results(combos["ENV"], cases, combos, envelope_mode="per_path")
    assert [r.name for r in results] == ["ENV [GRAV]", "ENV [WINDX]"]
    assert all(r.operator == "linear" for r in results)


def test_bad_envelope_mode(model):
    cases, combos = model
    with pytest.raises(ValueError, match="envelope_mode"):
        generate_combination_results(combos["ENV"], cases, combos, envelope_mode="nope")


def test_unsupported_combination_type(model):
    cases, combos = model
    combos["ABS"] = _combo("ABS", "Absolute Add", [("DEAD", 1.0)])
    classify_combination_refs(combos, cases)
    with pytest.raises(ValueError, match="Unsupported combination type"):
        generate_combination_results(combos["ABS"], cases, combos)


def test_unknown_reference_kind(model):
    cases, combos = model
    combos["DANGLING"] = _combo("DANGLING", "Linear Add", [("MISSING", 1.0)])
    classify_combination_refs(combos, cases)
    assert combos["DANGLING"].entries[0].kind == "unknown"


# ── Numerical evaluation ───────────────────────────────────────────────


def _linear(**factors) -> CompositeLoadCase:
    return CompositeLoadCase(name="C", operator="linear", cases=dict(factors))


def test_apply_linear_sums():
    result = apply_composite_load_case(
        _linear(DEAD=1.2, SDL=1.5), {"DEAD": np.array([10.0]), "SDL": np.array([2.0])}
    )
    assert result == pytest.approx([15.0])


def test_apply_max_and_min():
    composite = CompositeLoadCase(
        name="ENV [max]",
        operator="max",
        children=[(1.0, _linear(A=1.0)), (1.0, _linear(B=1.0))],
    )
    values = {"A": np.array([1.0, 5.0]), "B": np.array([3.0, 2.0])}
    assert apply_composite_load_case(composite, values) == pytest.approx([3.0, 5.0])
    composite_min = CompositeLoadCase(
        name="ENV [min]",
        operator="min",
        children=[(1.0, _linear(A=1.0)), (1.0, _linear(B=1.0))],
    )
    assert apply_composite_load_case(composite_min, values) == pytest.approx([1.0, 2.0])


def test_apply_srss():
    composite = CompositeLoadCase(
        name="SRSS",
        operator="srss",
        children=[(1.0, _linear(A=1.0)), (1.0, _linear(B=1.0))],
    )
    result = apply_composite_load_case(composite, {"A": np.array([3.0]), "B": np.array([4.0])})
    assert result == pytest.approx([5.0])


def test_apply_linear_child_term_is_signed():
    """A signed child term (forked spectrum axis) is added linearly."""
    srss = CompositeLoadCase(
        name="SRSS",
        operator="srss",
        children=[(1.0, _linear(A=1.0)), (1.0, _linear(B=1.0))],
    )
    composite = CompositeLoadCase(
        name="MIX",
        operator="linear",
        cases={"DEAD": 1.2},
        children=[(-1.0, srss)],
    )
    result = apply_composite_load_case(
        composite, {"DEAD": np.array([10.0]), "A": np.array([3.0]), "B": np.array([4.0])}
    )
    assert result == pytest.approx([10.0 * 1.2 - 5.0])


def test_apply_missing_case_raises():
    with pytest.raises(KeyError, match="no supplied values"):
        apply_composite_load_case(_linear(DEAD=1.0), {})


def test_apply_empty_composite_raises():
    with pytest.raises(ValueError, match="no terms"):
        apply_composite_load_case(CompositeLoadCase(name="E"), {})


def test_apply_unknown_operator_raises():
    composite = CompositeLoadCase(name="X", operator="bogus", children=[(1.0, _linear(A=1.0))])
    with pytest.raises(ValueError, match="Unknown composite operator"):
        apply_composite_load_case(composite, {"A": np.array([1.0])})


def test_generate_composite_results(model):
    cases, combos = model
    composites = generate_combination_results(combos["SEISM"], cases, combos)
    case_results = {
        "DEAD": {"fx": np.array([10.0])},
        "RSX": {"fx": np.array([4.0])},
    }
    out = generate_composite_results(composites, case_results)
    assert set(out) == {"SEISM #1", "SEISM #2"}
    assert out["SEISM #1"]["fx"] == pytest.approx([14.0])
    assert out["SEISM #2"]["fx"] == pytest.approx([6.0])
