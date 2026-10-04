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


def test_build_once_reuse_matches_the_per_case_rebuild(sample_mesh, monkeypatch):
    """One domain reused across cases gives the same results as rebuilding.

    The build-once path (forced here by zeroing the gate thresholds) must
    produce summary rows and raw results identical to the default per-case
    rebuild.
    """
    import fea_toolkit.analysis.linear as linear_mod
    from fea_toolkit.analysis.linear import run_static_cases

    cases = {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}}

    raw_rebuild: dict = {}
    rows_rebuild = run_static_cases(sample_mesh, cases, raw_out=raw_rebuild)

    monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_CASES", 0)
    monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_NODES", 0)
    raw_once: dict = {}
    rows_once = run_static_cases(sample_mesh, cases, raw_out=raw_once)

    assert [r["Case"] for r in rows_once] == [r["Case"] for r in rows_rebuild]
    for a, b in zip(rows_rebuild, rows_once):
        for key in ("Fx", "Fy", "Fz", "Mx", "My", "Mz"):
            assert a[key] == pytest.approx(b[key], rel=1e-9)

    assert set(raw_once) == set(raw_rebuild)
    for case in cases:
        once = raw_once[case]["nodal_displacements"]
        rebuilt = raw_rebuild[case]["nodal_displacements"]
        assert set(once) == set(rebuilt)
        for nid in once:
            for a, b in zip(once[nid], rebuilt[nid]):
                assert abs(a - b) < 1e-9, (case, nid, a, b)


def test_build_once_is_linear_only():
    """Build-once is refused for non-linear or fiber builds, and below the gate."""
    from fea_toolkit.analysis.linear import _should_build_once

    assert _should_build_once({"element_type": "elasticBeamColumn"}, 10, 10) is True
    assert _should_build_once({"element_type": "dispBeamColumn"}, 10, 10) is False
    assert (
        _should_build_once(
            {"element_type": "elasticBeamColumn", "create_fiber_sections": True}, 10, 10
        )
        is False
    )
    assert _should_build_once({"element_type": "elasticBeamColumn"}, 1, 1) is False


def _portal_with_rigid_body_mesh():
    """A two-column portal frame whose tops are tied by a 6-DOF rigid body.

    Preprocesses the model and injects a ``rigid_body_components`` entry so the
    AnalysisBuilder emits a ``rigidLink('beam')`` MP constraint tying node 4 to
    node 3 — the multipoint constraint whose persistence across ``wipeAnalysis``
    the build-once path must preserve.
    """
    from fea_toolkit.model.sap_data import (
        FrameElement,
        JointLoad,
        LoadPattern,
        Material,
        Node,
        Restraint,
        SAPModelData,
        Section,
    )
    from fea_toolkit.opensees.preprocessor import preprocess_model

    nodes = {
        "1": Node("1", 1, 0.0, 0.0, 0.0),
        "2": Node("2", 2, 4.0, 0.0, 0.0),
        "3": Node("3", 3, 0.0, 0.0, 3.0),
        "4": Node("4", 4, 4.0, 0.0, 3.0),
    }
    md = SAPModelData(
        nodes=nodes,
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1]), "2": Restraint([1, 1, 1, 1, 1, 1])},
        materials={
            "Steel": Material(
                name="Steel",
                type="Steel",
                E_mod=2.0e11,
                G_mod=7.7e10,
                nu=0.3,
                unit_weight=7.85e4,
                Fy=2.5e8,
            )
        },
        sections={
            "COL": Section(
                name="COL",
                shape="I/Wide Flange",
                material="Steel",
                A=0.00509434,
                I33=8.3935e-5,
                I22=7.6559e-6,
                J=2.0e-6,
            )
        },
        frame_elements={
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        },
        area_elements={},
        frame_assignments={"1": "COL", "2": "COL"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        load_patterns={
            "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=0),
            "WIND": LoadPattern(name="WIND", pattern_type="Wind", self_weight_factor=0),
        },
        joint_loads=[
            JointLoad(pattern="DEAD", node_id="3", fz=-500.0),
            JointLoad(pattern="WIND", node_id="3", fx=1000.0),
        ],
    )
    mm = preprocess_model(
        md, {"element_type": "elasticBeamColumn", "split_elements": False, "verbose": False}
    )
    mm.rigid_body_components = [("Body", ["3", "4"], [True] * 6)]
    return mm


def test_build_once_preserves_rigid_body_constraints(monkeypatch):
    """Build-once keeps ``rigidLink`` MP constraints across ``wipeAnalysis``.

    Rigid-body (and diaphragm) multipoint constraints live in the domain and
    survive ``wipeAnalysis``, but the build-once path must still re-create the
    analysis objects and reset the pseudo-time so each case solves from a clean
    state.  This pins that a model whose tops are tied by a 6-DOF rigid body
    gives results identical to a per-case rebuild.
    """
    import openseespy.opensees as ops

    import fea_toolkit.analysis.linear as linear_mod
    from fea_toolkit.analysis.linear import run_static_cases

    mesh = _portal_with_rigid_body_mesh()
    cases = {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}}

    try:
        raw_rebuild: dict = {}
        rows_rebuild = run_static_cases(mesh, cases, raw_out=raw_rebuild)
        # Sanity: the rigid body ties the two tops, so their X displacement
        # under the lateral case is identical.
        wind_rebuild = raw_rebuild["WIND"]["nodal_displacements"]
        assert wind_rebuild["3"][0] == pytest.approx(wind_rebuild["4"][0], rel=1e-9)

        monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_CASES", 0)
        monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_NODES", 0)
        raw_once: dict = {}
        rows_once = run_static_cases(mesh, cases, raw_out=raw_once)

        assert [r["Case"] for r in rows_once] == [r["Case"] for r in rows_rebuild]
        for a, b in zip(rows_rebuild, rows_once):
            for key in ("Fx", "Fy", "Fz", "Mx", "My", "Mz"):
                assert a[key] == pytest.approx(b[key], rel=1e-9)
        assert set(raw_once) == set(raw_rebuild)
        for case in cases:
            once = raw_once[case]["nodal_displacements"]
            rebuilt = raw_rebuild[case]["nodal_displacements"]
            assert set(once) == set(rebuilt)
            for nid in once:
                for a, b in zip(once[nid], rebuilt[nid]):
                    # Transformation + rigidLink re-created after wipeAnalysis
                    # leaves ~1e-7 rounding noise, so use an absolute tolerance
                    # rather than pytest.approx (which cannot recurse into a
                    # dict of lists).
                    assert abs(a - b) < 1e-6, (case, nid, a, b)
    finally:
        ops.wipe()


def test_run_static_cases_announces_domain_reuse(sample_mesh, monkeypatch, capsys):
    """The run announces whether the domain is rebuilt per case or built once.

    A monitored run must show when the per-case rebuild is skipped, so a
    problem can be traced to the right path.
    """
    import fea_toolkit.analysis.linear as linear_mod
    from fea_toolkit.analysis.linear import run_static_cases

    cases = {"DEAD": {"DEAD": 1.0}}

    # Default (below the gate): the domain is rebuilt per case.
    run_static_cases(sample_mesh, cases)
    assert "Rebuilding the OpenSees domain per case" in capsys.readouterr().out

    # Forced build-once: the per-case rebuild is skipped.
    monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_CASES", 0)
    monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_NODES", 0)
    run_static_cases(sample_mesh, cases)
    assert "per-case rebuild skipped" in capsys.readouterr().out


def test_run_static_cases_records_reuse_decision_in_log(sample_mesh, monkeypatch):
    """The reuse decision is recorded in the structured log, not just stdout.

    A ``PipelineLog`` handed in as ``log=`` collects the same announcement the
    stdout sink prints, so ``generate_report``'s Analysis Log table carries it.
    """
    import fea_toolkit.analysis.linear as linear_mod
    from fea_toolkit.analysis.linear import run_static_cases
    from fea_toolkit.io.log import PipelineLog

    cases = {"DEAD": {"DEAD": 1.0}}

    # Default (below the gate): the domain is rebuilt per case.
    log = PipelineLog(verbose=False)
    run_static_cases(sample_mesh, cases, log=log)
    assert any(
        e["step"] == "static" and "Rebuilding the OpenSees domain per case" in e["msg"]
        for e in log.entries
    )

    # Forced build-once: the per-case rebuild is skipped.
    monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_CASES", 0)
    monkeypatch.setattr(linear_mod, "_BUILD_ONCE_MIN_NODES", 0)
    log = PipelineLog(verbose=False)
    run_static_cases(sample_mesh, cases, log=log)
    assert any(
        e["step"] == "static" and "per-case rebuild skipped" in e["msg"] for e in log.entries
    )
