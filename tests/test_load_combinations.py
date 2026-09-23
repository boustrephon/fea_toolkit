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
    as_combination_mapping,
    build_combo_tree,
    build_combo_tree_dict,
    calculate_aggregate_factors,
    classify_combination_refs,
    combination_case_meta,
    combination_set_from_dict,
    combination_set_to_dict,
    expand_linear_combination,
    fork_coords,
    generate_combination_results,
    generate_composite_results,
    merge_combination_sets,
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


def test_negative_magnitude_factor_tags_its_own_sign(model):
    """A negative magnitude reference forks with the sense it actually has.

    The coordinate prefix comes from the entry's own factor (``_coord_tag``),
    so ``-1.4 RSX`` emits ``-RSX`` first — its own sense — and ``+RSX`` as the
    scaled opposite, rather than a hard-coded ``+`` on the first variant.
    """
    cases, combos = model
    combos["NEG"] = _combo("NEG", "Linear Add", [("DEAD", 1.0), ("RSX", -1.4)])
    classify_combination_refs(combos, cases)
    results = generate_combination_results(combos["NEG"], cases, combos)
    assert [r.cases["RSX"] for r in results] == [-1.4, 1.4]
    assert [r.coords for r in results] == [("-RSX",), ("+RSX",)]
    assert [r.name for r in results] == ["NEG [-RSX]", "NEG [+RSX]"]
    meta = combination_case_meta(results, cases, combos)
    assert meta["NEG [-RSX]"]["family"] == "fork"
    assert meta["NEG [-RSX]"]["coords"] == "-RSX"
    assert meta["NEG [+RSX]"]["coords"] == "+RSX"


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


def test_envelope_per_path_preserves_a_branch_child_fork(model):
    """A ``per_path`` branch that expands to several variants keeps them apart.

    ``OUTER`` (Envelope) references ``SUB`` — itself an Envelope — so the
    ``SUB`` branch expands to one variant per inner branch.  Each variant keeps
    its branch name *plus* its own coordinates, both in ``coords`` and in the
    name derived from it, so :func:`combination_case_meta` (keyed by name)
    cannot overwrite one variant with another under a shared name.
    """
    cases, combos = model
    combos["SUB"] = _combo("SUB", "Envelope", [("DEAD", 1.0), ("WIND", 1.0)])
    combos["OUTER"] = _combo("OUTER", "Envelope", [("SUB", 1.0), ("GRAV", 1.0)])
    classify_combination_refs(combos, cases)
    results = generate_combination_results(combos["OUTER"], cases, combos, envelope_mode="per_path")
    assert [r.coords for r in results] == [("SUB", "DEAD"), ("SUB", "WIND"), ("GRAV",)]
    assert [r.name for r in results] == ["OUTER [SUB, DEAD]", "OUTER [SUB, WIND]", "OUTER [GRAV]"]
    # Distinct names mean every variant survives the name-keyed metadata map.
    meta = combination_case_meta(results, cases, combos)
    assert set(meta) == {"OUTER [SUB, DEAD]", "OUTER [SUB, WIND]", "OUTER [GRAV]"}


def test_per_path_duplicate_branch_names_stay_unique(model):
    """The same branch referenced twice must not give two variants one name.

    ``combination_case_meta`` is keyed by name, so a shared name silently drops
    a variant from the metadata map — and with it the archive's pairing.
    """
    cases, combos = model
    combos["DUP"] = _combo("DUP", "Envelope", [("DEAD", 1.0), ("GRAV", 1.0), ("DEAD", 0.5)])
    classify_combination_refs(combos, cases)
    results = generate_combination_results(combos["DUP"], cases, combos, envelope_mode="per_path")
    names = [r.name for r in results]
    assert names == ["DUP [DEAD]", "DUP [GRAV]", "DUP [DEAD] #2"]
    assert len(set(names)) == len(names)
    assert set(combination_case_meta(results, cases, combos)) == set(names)


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
    assert set(out) == {"SEISM [+RSX]", "SEISM [-RSX]"}
    assert out["SEISM [+RSX]"]["fx"] == pytest.approx([14.0])
    assert out["SEISM [-RSX]"]["fx"] == pytest.approx([6.0])


# ── External definition sets ───────────────────────────────────────────


def test_combination_set_from_dict_types_aliases_and_shorthand():
    combos = combination_set_from_dict(
        {
            "SEISM": {
                "type": "linear",
                "entries": [{"ref": "DEAD", "factor": 1.0}, {"ref": "RSX", "factor": 1.4}],
            },
            "ENV": {"type": "envelope", "entries": [["DEAD", 1.0], ["RSX", 1.0]]},
            "SRSS": {"type": "srss", "entries": [("RSX", 1.0)]},
            "SHORT": [["DEAD", 1.2], ["RSX", 1.0]],
            "BARE": {"type": "Linear Add", "entries": ["DEAD"]},
        }
    )
    # Shorthand spellings normalise to the canonical CSI operator names.
    assert combos["SEISM"].combo_type == "Linear Add"
    assert combos["ENV"].combo_type == "Envelope"
    assert combos["SRSS"].combo_type == "SRSS"
    # A bare entry list is implicitly Linear Add.
    assert combos["SHORT"].combo_type == "Linear Add"
    assert [(e.name, e.factor) for e in combos["SHORT"].entries] == [("DEAD", 1.2), ("RSX", 1.0)]
    # A bare reference string defaults to factor 1.0.
    assert [(e.name, e.factor) for e in combos["BARE"].entries] == [("DEAD", 1.0)]


def test_combination_set_rejects_bad_shapes():
    with pytest.raises(TypeError, match="must be a mapping"):
        combination_set_from_dict({"X": 3})
    with pytest.raises(ValueError, match="names no case"):
        combination_set_from_dict({"X": {"type": "Envelope", "entries": [{"factor": 1.0}]}})
    with pytest.raises(TypeError, match="Unsupported combination entry"):
        combination_set_from_dict({"X": {"type": "Envelope", "entries": [3.5]}})


def test_combination_set_round_trips_every_authored_field():
    combos = combination_set_from_dict(
        {
            "C": {
                "type": "Linear Add",
                "entries": [{"ref": "RSX", "factor": 1.4, "mode": 3, "magnitude": True}],
                "design": {"SteelDesign": "None"},
            }
        }
    )
    again = combination_set_from_dict(combination_set_to_dict(combos))
    entry = again["C"].entries[0]
    assert again["C"].combo_type == "Linear Add"
    assert again["C"].design == {"SteelDesign": "None"}
    assert (entry.name, entry.factor, entry.mode, entry.magnitude) == ("RSX", 1.4, 3, True)


def test_merge_combination_sets_extends_overrides_and_classifies(model):
    cases, combos = model
    external = {"EXTRA": {"type": "Envelope", "entries": [["GRAV", 1.0], ["DEAD", 1.0]]}}
    merged = merge_combination_sets(combos, external, load_cases=cases)
    assert "GRAV" in merged and "EXTRA" in merged
    # An external reference to a model combination classifies as a branch.
    assert merged["EXTRA"].entries[0].kind == "combo"
    # A later layer overrides by name without mutating its input.
    override = merge_combination_sets(
        combos, {"GRAV": {"type": "SRSS", "entries": [["DEAD", 1.0]]}}
    )
    assert override["GRAV"].combo_type == "SRSS"
    assert combos["GRAV"].combo_type == "Linear Add"


def test_merge_resolves_nested_combos_without_load_cases():
    """A reference to a set member classifies as a branch even with no model."""
    merged = merge_combination_sets(
        {
            "INNER": {"type": "SRSS", "entries": [["RSX", 1.0]]},
            "OUTER": {"type": "Linear Add", "entries": [["INNER", 1.0], ["DEAD", 1.0]]},
        }
    )
    assert merged["OUTER"].entries[0].kind == "combo"
    assert merged["OUTER"].entries[1].kind == "case"


def test_merge_does_not_mutate_the_caller_entries_or_design():
    """Classification runs on a deep copy, so the caller's objects are intact.

    ``as_combination_mapping`` is a shallow ``dict()`` copy, so without the
    deep-copy step ``classify_combination_refs`` / the combo-name loop would
    rewrite ``kind`` on the caller's own entries.
    """
    original = combination_set_from_dict(
        {
            "GRAV": {"type": "Linear Add", "entries": [["DEAD", 1.0]]},
            "C": {
                "type": "Linear Add",
                "entries": [["DEAD", 1.0], ["GRAV", 1.0]],
                "design": {"SteelDesign": "None"},
            },
        }
    )
    before = tuple(e.kind for e in original["C"].entries)
    assert before == ("case", "case")  # freshly built, unclassified

    merged = merge_combination_sets(original, load_cases={"DEAD": _case("DEAD")})

    # The returned copy is classified ...
    assert [e.kind for e in merged["C"].entries] == ["case", "combo"]
    # ... while the caller's entries and design are untouched.
    assert tuple(e.kind for e in original["C"].entries) == before
    assert original["C"].design == {"SteelDesign": "None"}
    assert merged["C"].design is not original["C"].design
    assert merged["C"].entries[0] is not original["C"].entries[0]
    assert merged["GRAV"] is not original["GRAV"]


def test_as_combination_mapping_normalises_every_accepted_input(model):
    _cases, combos = model
    assert as_combination_mapping(None) == {}
    assert as_combination_mapping({}) == {}
    # A LoadCombination mapping is copied, not returned as-is.
    copied = as_combination_mapping(combos)
    assert copied is not combos and set(copied) == set(combos)
    assert as_combination_mapping({"X": {"type": "SRSS", "entries": []}})["X"].combo_type == "SRSS"


# ── Family, coordinates and per-case metadata ──────────────────────────


def _meta_for(model, name):
    """``combination_case_meta`` for one combination of the *model* fixture."""
    cases, combos = model
    composites = generate_combination_results(combos[name], cases, combos)
    return combination_case_meta(composites, cases, combos)


def test_family_single_and_fork(model):
    cases, combos = model
    single = generate_combination_results(combos["GRAV"], cases, combos)
    assert [c.family for c in single] == ["single"]
    assert single[0].coords == ()
    forked = generate_combination_results(combos["SEISM"], cases, combos)
    assert [c.family for c in forked] == ["fork", "fork"]


def test_two_spectra_give_four_corner_coords(model):
    """A flat ``DEAD + RSX + RSY`` is a 2² sign space, not a ± pair."""
    cases, combos = model
    combos["FLAT4"] = _combo("FLAT4", "Linear Add", [("DEAD", 1.0), ("RSX", 1.0), ("RSY", 1.0)])
    classify_combination_refs(combos, cases)
    composites = generate_combination_results(combos["FLAT4"], cases, combos)
    assert [c.family for c in composites] == ["fork"] * 4
    assert [c.coords for c in composites] == [
        ("+RSX", "+RSY"),
        ("+RSX", "-RSY"),
        ("-RSX", "+RSY"),
        ("-RSX", "-RSY"),
    ]


def test_nested_spectrum_sum_is_one_magnitude(model):
    """``TWO_SPEC`` is all-magnitude, so it is a single composite, not a fork."""
    cases, combos = model
    composites = generate_combination_results(combos["TWO_SPEC"], cases, combos)
    assert len(composites) == 1
    assert composites[0].family == "single"
    # The raw geometric extraction still lists both signed terms; it is
    # combination_case_meta that clears them for a non-forking family.
    assert fork_coords(composites[0], cases, combos) == ("+RSX", "+RSY")
    assert combination_case_meta(composites, cases, combos)["TWO_SPEC"]["coords"] == ""


def test_envelope_and_path_families(model):
    cases, combos = model
    maxmin = generate_combination_results(combos["ENV"], cases, combos)
    assert [(c.name, c.family, c.coords) for c in maxmin] == [
        ("ENV [max]", "envelope", ("max",)),
        ("ENV [min]", "envelope", ("min",)),
    ]
    per_path = generate_combination_results(combos["ENV"], cases, combos, envelope_mode="per_path")
    assert [c.family for c in per_path] == ["path", "path"]
    assert [c.coords for c in per_path] == [("GRAV",), ("WINDX",)]


def test_linear_add_over_an_envelope_keeps_the_envelope_family(model):
    """A Linear Add's ``family`` describes the variant axis, not its own operator.

    ``DEAD + ENV`` inherits the Envelope's ``max``/``min`` pair, so calling it a
    ``"fork"`` (the old count-based rule: *more than one variant ⇒ fork*) would
    misdescribe it — and the name derived from the coordinates would then read
    as a signed fork.
    """
    cases, combos = model
    combos["LINEAR_OF_ENV"] = _combo("LINEAR_OF_ENV", "Linear Add", [("DEAD", 1.0), ("ENV", 1.0)])
    classify_combination_refs(combos, cases)
    composites = generate_combination_results(combos["LINEAR_OF_ENV"], cases, combos)
    assert [(c.name, c.family, c.coords) for c in composites] == [
        ("LINEAR_OF_ENV [max]", "envelope", ("max",)),
        ("LINEAR_OF_ENV [min]", "envelope", ("min",)),
    ]
    assert combination_case_meta(composites, cases, combos) == {
        "LINEAR_OF_ENV [max]": {
            "group": "LINEAR_OF_ENV",
            "family": "envelope",
            "coords": "max",
        },
        "LINEAR_OF_ENV [min]": {
            "group": "LINEAR_OF_ENV",
            "family": "envelope",
            "coords": "min",
        },
    }
    # A genuine sign fork still outranks an inherited family.
    combos["FORK_AND_ENV"] = _combo("FORK_AND_ENV", "Linear Add", [("RSX", 1.0), ("ENV", 1.0)])
    classify_combination_refs(combos, cases)
    mixed = generate_combination_results(combos["FORK_AND_ENV"], cases, combos)
    assert len(mixed) == 4
    assert {c.family for c in mixed} == {"fork"}


def test_srss_and_mixed_srss_meta(model):
    cases, combos = model
    srss = combination_case_meta(
        generate_combination_results(combos["SRSS_C"], cases, combos), cases, combos
    )
    # A one-member family has no coordinate to record.
    assert srss["SRSS_C"] == {"group": "SRSS_C", "family": "srss", "coords": ""}
    mix = _meta_for(model, "MIX_SRSS")
    assert set(mix) == {"MIX_SRSS [+SRSS_C]", "MIX_SRSS [-SRSS_C]"}
    assert mix["MIX_SRSS [+SRSS_C]"]["family"] == "fork"
    # A nested magnitude is named for the sub-combination it came from.
    assert mix["MIX_SRSS [+SRSS_C]"]["coords"] == "+SRSS_C"
    assert mix["MIX_SRSS [-SRSS_C]"]["coords"] == "-SRSS_C"


def test_case_meta_derives_family_for_hand_built_composites():
    """A hand-built composite carries no family, so it is derived from it.

    ``generate_combination_results`` tags its output at the fork point; a
    ``CompositeLoadCase`` built by hand has ``family == ""``, so
    ``combination_case_meta`` falls back to the operator and the signed coords.
    """
    composites = [
        CompositeLoadCase(
            name="ENV [max]", operator="max", cases={"GRAV": 1.0}, source="ENV", coords=("max",)
        ),
        CompositeLoadCase(
            name="SRSS_C", operator="srss", cases={"RSX": 1.0}, source="SRSS_C", coords=("srss",)
        ),
        CompositeLoadCase(
            name="FLAT4 [+RSX, +RSY]",
            operator="linear",
            cases={"DEAD": 1.0},
            source="FLAT4",
            coords=("+RSX", "+RSY"),
        ),
        CompositeLoadCase(name="GRAV", operator="linear", cases={"DEAD": 1.2}, source="GRAV"),
    ]
    meta = combination_case_meta(composites)
    assert meta["ENV [max]"]["family"] == "envelope"
    assert meta["ENV [max]"]["coords"] == "max"
    assert meta["SRSS_C"]["family"] == "srss"
    # A one-member family has no coordinate, however it was hand-declared.
    assert meta["SRSS_C"]["coords"] == ""
    assert meta["FLAT4 [+RSX, +RSY]"]["family"] == "fork"
    assert meta["FLAT4 [+RSX, +RSY]"]["coords"] == "+RSX|+RSY"
    # No coords and no forking operator: a plain single with no coordinate.
    assert meta["GRAV"]["family"] == "single"
    assert meta["GRAV"]["coords"] == ""


def test_seism_meta_carries_group_family_and_coords(model):
    meta = _meta_for(model, "SEISM")
    assert meta == {
        "SEISM [+RSX]": {"group": "SEISM", "family": "fork", "coords": "+RSX"},
        "SEISM [-RSX]": {"group": "SEISM", "family": "fork", "coords": "-RSX"},
    }


def test_magnitude_hint_forks_without_load_cases():
    """An external definition can declare the fork without the model's cases."""
    combos = combination_set_from_dict(
        {
            "M": {
                "type": "Linear Add",
                "entries": [
                    {"ref": "DEAD", "factor": 1.0},
                    {"ref": "SPECIAL", "factor": 1.0, "magnitude": True},
                ],
            }
        }
    )
    # ``load_cases={}`` throughout: the hint is the whole definition.
    composites = generate_combination_results(combos["M"], {}, combos)
    assert [c.name for c in composites] == ["M [+SPECIAL]", "M [-SPECIAL]"]
    meta = combination_case_meta(composites, {}, combos)
    assert meta["M [+SPECIAL]"]["coords"] == "+SPECIAL"
    assert meta["M [-SPECIAL]"]["coords"] == "-SPECIAL"


def test_names_derive_from_coordinates_without_load_cases():
    """Every variant is named for its coordinates, from a definition alone.

    The ``magnitude`` hint is the one thing a definition cannot infer without
    the model's load cases, so a set that declares it drives the whole
    expansion — grouping, naming and metadata — with ``load_cases={}``.  The
    name is a faithful projection of the variant identity, so nothing ever has
    to parse it back.
    """
    definitions = combination_set_from_dict(
        {
            "SEISM": {
                "type": "Linear Add",
                "entries": [
                    {"ref": "DEAD", "factor": 1.0},
                    {"ref": "RSX", "factor": 1.0, "magnitude": True},
                ],
            },
            "FLAT4": {
                "type": "Linear Add",
                "entries": [
                    {"ref": "DEAD", "factor": 1.0},
                    {"ref": "RSX", "factor": 1.0, "magnitude": True},
                    {"ref": "RSY", "factor": 1.0, "magnitude": True},
                ],
            },
            "SRSS_C": {"type": "SRSS", "entries": [["RSX", 1.0], ["RSY", 0.3]]},
            "GRAV": {"type": "Linear Add", "entries": [["DEAD", 1.2], ["SDL", 1.5]]},
            "SUB": {"type": "Envelope", "entries": [["DEAD", 1.0], ["WIND", 1.0]]},
            "ENV": {"type": "Envelope", "entries": [["DEAD", 1.0], ["WIND", 1.0]]},
            "OUTER": {"type": "Envelope", "entries": [["SUB", 1.0], ["GRAV", 1.0]]},
            "LINEAR_OF_ENV": {"type": "Linear Add", "entries": [["DEAD", 1.0], ["ENV", 1.0]]},
        }
    )
    classify_combination_refs(definitions, {})
    expected = {
        ("SEISM", "maxmin"): ["SEISM [+RSX]", "SEISM [-RSX]"],
        ("FLAT4", "maxmin"): [
            "FLAT4 [+RSX, +RSY]",
            "FLAT4 [+RSX, -RSY]",
            "FLAT4 [-RSX, +RSY]",
            "FLAT4 [-RSX, -RSY]",
        ],
        ("SRSS_C", "maxmin"): ["SRSS_C"],
        ("GRAV", "maxmin"): ["GRAV"],
        ("ENV", "maxmin"): ["ENV [max]", "ENV [min]"],
        ("ENV", "per_path"): ["ENV [DEAD]", "ENV [WIND]"],
        ("OUTER", "per_path"): ["OUTER [SUB, DEAD]", "OUTER [SUB, WIND]", "OUTER [GRAV]"],
        ("LINEAR_OF_ENV", "maxmin"): ["LINEAR_OF_ENV [max]", "LINEAR_OF_ENV [min]"],
    }
    for (name, envelope_mode), names in expected.items():
        composites = generate_combination_results(
            definitions[name], {}, definitions, envelope_mode=envelope_mode
        )
        assert [c.name for c in composites] == names, name
        meta = combination_case_meta(composites, {}, definitions)
        for composite in composites:
            info = meta[composite.name]
            # The name is the metadata's group + coordinates, rendered.
            tag = f" [{info['coords'].replace('|', ', ')}]" if info["coords"] else ""
            assert composite.name == f"{info['group']}{tag}", composite.name


def test_flat4_meta_carries_four_corner_coords(model):
    """A multi-fork's four corners are identified by their coordinates."""
    cases, combos = model
    combos["FLAT4"] = _combo("FLAT4", "Linear Add", [("DEAD", 1.0), ("RSX", 1.0), ("RSY", 1.0)])
    classify_combination_refs(combos, cases)
    meta = _meta_for(model, "FLAT4")
    assert {info["family"] for info in meta.values()} == {"fork"}
    assert {info["coords"] for info in meta.values()} == {
        "+RSX|+RSY",
        "+RSX|-RSY",
        "-RSX|+RSY",
        "-RSX|-RSY",
    }
