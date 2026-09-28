"""``analysis/case_listing`` — what a model offers to run (P27 / I2).

Qt-free and OpenSees-free: the listing is a plain read over a parsed
``SAPModelData``.  The *runner* it feeds (``run_static_cases``) is exercised
against the built-in cantilever at the bottom of the file, where an OpenSees
domain is actually built.

The seam these pin is the one the Analysis ▸ Run dialog draws on: exactly the
cases ``run_linear_cases`` would solve, exactly the load cases each combination
needs, and the load patterns a hand-authored case may name.
"""

import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.model.load_combinations import classify_combination_refs
from fea_toolkit.model.sap_data import (
    FrameDistributedLoad,
    LoadCase,
    LoadCombination,
    LoadCombinationEntry,
    LoadPattern,
    SAPModelData,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _case(name: str, assignments=None, case_type: str = "LinStatic") -> LoadCase:
    """A load case whose static assignments are *assignments* (or none)."""
    lc = LoadCase(name, case_type, "Prog Det", "Dead", "Prog Det", "Non-Composite")
    if assignments is not None:
        lc.case_data["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = assignments
    return lc


def _combo(name: str, combo_type: str, refs) -> LoadCombination:
    return LoadCombination(name, combo_type, [LoadCombinationEntry(n, f) for n, f in refs])


def _frame_load(pattern: str) -> FrameDistributedLoad:
    """A uniform gravity load on frame ``1``, belonging to *pattern*."""
    return FrameDistributedLoad(
        pattern=pattern,
        frame_id="1",
        direction="Gravity",
        load_type="Force",
        shape="Uniform",
        val_a=1.0,
        val_b=1.0,
        rdist_a=0.0,
        rdist_b=1.0,
        dist_a=0.0,
        dist_b=1.0,
    )


class _EdgeLoad:
    """Just enough of a derived edge load for the ``pattern`` test."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern


class _Mesh:
    """A mesh stand-in carrying only ``edge_loads_from_areas``."""

    def __init__(self, patterns) -> None:
        self.edge_loads_from_areas = [_EdgeLoad(p) for p in patterns]


def _empty_model(**attributes) -> SAPModelData:
    """A geometry-free ``SAPModelData`` — the listing needs no topology."""
    md = SAPModelData(
        nodes={},
        restraints={},
        materials={},
        sections={},
        frame_elements={},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )
    for name, value in attributes.items():
        setattr(md, name, value)
    return md


# ── The listing model ──────────────────────────────────────────────────


@pytest.fixture
def model():
    """Loaded / unloaded patterns, six static cases, one combination tree.

    ``DEAD`` loads by self-weight, ``LIVE`` and ``WIND`` by a frame load, and
    ``POND`` only by an *area edge* load — which lives on the preprocessed
    model, so ``EDGE`` (the case that assigns it) is listed only when a mesh is
    supplied.  ``GHOST`` names a pattern that does not exist.
    """
    patterns = {
        "DEAD": LoadPattern("DEAD", "Dead", self_weight_factor=1.0),
        "LIVE": LoadPattern("LIVE", "Live"),
        "WIND": LoadPattern("WIND", "Wind"),
        "POND": LoadPattern("POND", "Other"),
    }
    cases = {
        "DEAD": _case("DEAD", [{"LoadName": "DEAD"}]),
        "LIVE": _case("LIVE", [{"LoadName": "LIVE", "LoadSF": 1.0}]),
        "WIND": _case("WIND", [{"LoadName": "WIND", "LoadSF": 1.0}]),
        "EDGE": _case("EDGE", [{"LoadName": "POND"}]),
        "GHOST": _case("GHOST", [{"LoadName": "MISSING"}]),
        "MODAL": _case("MODAL", case_type="Modal"),
    }
    combos = {
        "GRAV": _combo("GRAV", "Linear Add", [("DEAD", 1.2), ("LIVE", 1.5)]),
        "NESTED": _combo("NESTED", "Linear Add", [("GRAV", 1.3), ("WIND", 1.0)]),
        "A": _combo("A", "Linear Add", [("B", 1.0)]),
        "B": _combo("B", "Linear Add", [("A", 1.0)]),
    }
    classify_combination_refs(combos, cases)
    return _empty_model(
        load_patterns=patterns,
        load_cases=cases,
        load_combinations=combos,
        frame_dist_loads=[_frame_load("LIVE"), _frame_load("WIND")],
    )


# ── Static cases ───────────────────────────────────────────────────────


def test_lists_each_static_case_with_its_pattern_factors(model):
    from fea_toolkit.analysis.case_listing import list_static_cases

    cases = list_static_cases(model)

    assert cases["DEAD"] == {"DEAD": 1.0}
    assert cases["LIVE"] == {"LIVE": 1.0}
    assert cases["WIND"] == {"WIND": 1.0}


def test_only_static_cases_are_listed(model):
    """A modal case drives a different analysis — it is not a static run."""
    from fea_toolkit.analysis.case_listing import list_static_cases

    assert "MODAL" not in list_static_cases(model)


def test_a_case_whose_patterns_carry_no_load_is_dropped(model):
    from fea_toolkit.analysis.case_listing import list_static_cases

    cases = list_static_cases(model)
    assert "GHOST" not in cases  # names a pattern the model never defines
    assert "EDGE" not in cases  # POND is applied only via the mesh


def test_a_pattern_loaded_only_through_the_mesh_counts_when_a_mesh_is_given(model):
    from fea_toolkit.analysis.case_listing import list_static_cases

    cases = list_static_cases(model, _Mesh(["POND"]))
    assert cases["EDGE"] == {"POND": 1.0}
    assert "GHOST" not in cases


def test_config_cases_merge_over_the_models_own(model):
    from fea_toolkit.analysis.case_listing import list_static_cases

    cases = list_static_cases(model, config_cases=[{"ULT": {"DEAD": 1.4, "LIVE": 1.6}}, "WIND"])

    assert cases["ULT"] == {"DEAD": 1.4, "LIVE": 1.6}
    assert cases["WIND"] == {"WIND": 1.0}  # still there alongside the custom case
    assert cases["DEAD"] == {"DEAD": 1.0}


def test_the_models_own_order_is_kept(model):
    from fea_toolkit.analysis.case_listing import list_static_cases

    assert list(list_static_cases(model)) == ["DEAD", "LIVE", "WIND"]


def test_patterns_are_listed_in_model_order(model):
    from fea_toolkit.analysis.case_listing import list_patterns

    assert list_patterns(model) == ["DEAD", "LIVE", "WIND", "POND"]


def test_pattern_has_load_reads_every_load_family(model):
    from fea_toolkit.analysis.case_listing import pattern_has_load

    assert pattern_has_load(model, "DEAD")  # self-weight
    assert pattern_has_load(model, "LIVE")  # frame distributed
    assert not pattern_has_load(model, "POND")
    assert pattern_has_load(model, "POND", _Mesh(["POND"]))


# ── Combinations ───────────────────────────────────────────────────────


def test_a_combination_carries_the_cases_it_needs(model):
    from fea_toolkit.analysis.case_listing import list_combinations

    specs = {spec.name: spec for spec in list_combinations(model)}

    assert specs["GRAV"].leaves == pytest.approx({"DEAD": 1.2, "LIVE": 1.5})
    # The nested combination's factors multiply down the path.
    assert specs["NESTED"].leaves == pytest.approx({"DEAD": 1.56, "LIVE": 1.95, "WIND": 1.0})
    assert specs["GRAV"].combo_type == "Linear Add"


def test_an_unexpandable_combination_is_reported_not_raised(model):
    from fea_toolkit.analysis.case_listing import list_combinations

    specs = {spec.name: spec for spec in list_combinations(model)}

    assert specs["A"].leaves == {}
    assert "Cyclic" in specs["A"].error
    # …and the good combinations are still listed beside it.
    assert specs["GRAV"].leaves


# ── The runner: run_static_cases ───────────────────────────────────────

_AB_CONFIG = {
    "element_type": "elasticBeamColumn",
    "verbose": False,
    "create_shells": False,
}


@pytest.fixture
def sample_mesh():
    """The built-in 10 m cantilever, preprocessed once for the static runs."""
    import openseespy.opensees as ops

    from fea_toolkit.opensees.preprocessor import preprocess_model

    mesh = preprocess_model(make_sample_model(), _AB_CONFIG)
    yield mesh
    ops.wipe()


def test_runs_each_case_and_fills_raw_out(sample_mesh):
    from fea_toolkit.analysis.linear import run_static_cases

    raw: dict = {}
    rows = run_static_cases(
        sample_mesh, {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}}, raw_out=raw
    )

    assert [row["Case"] for row in rows] == ["DEAD", "WIND"]
    assert all(row["Type"] == "Static" for row in rows)
    assert set(raw) == {"DEAD", "WIND"}
    # The shape results_arrays() consumes as static_results.
    assert raw["DEAD"]["nodal_displacements"]
    assert abs(rows[0]["Fz"]) > 0.0  # a real solve, not the zeroed fallback


def test_the_factor_scales_the_load_not_the_result(sample_mesh):
    """A case's factor is a LOAD multiplier — doubling it doubles the reaction."""
    from fea_toolkit.analysis.linear import run_static_cases

    once = run_static_cases(sample_mesh, {"DEAD": {"DEAD": 1.0}})
    twice = run_static_cases(sample_mesh, {"DEAD": {"DEAD": 2.0}})

    assert twice[0]["Fz"] == pytest.approx(2.0 * once[0]["Fz"], rel=1e-6)


def test_progress_is_reported_before_each_case(sample_mesh):
    from fea_toolkit.analysis.linear import run_static_cases

    seen: list = []
    run_static_cases(
        sample_mesh,
        {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}},
        on_progress=lambda index, name, total: seen.append((index, name, total)),
    )

    assert seen == [(1, "DEAD", 2), (2, "WIND", 2)]


def test_cancelling_before_the_first_case_runs_nothing(sample_mesh):
    from fea_toolkit.analysis.linear import run_static_cases

    raw: dict = {}
    rows = run_static_cases(
        sample_mesh,
        {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}},
        raw_out=raw,
        should_cancel=lambda: True,
    )

    assert rows == []
    assert raw == {}
