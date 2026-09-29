"""``analysis.run_case_set`` — the in-memory run the GUI's Run action drives.

Qt-free (no ``needs_gui``): this is the whole of ``Analysis ▸ Run`` minus the
dialog and the worker, so the *data path* P27 is about — solve, reduce, assemble
an archive dict, serve it through a repository — is pinned without Qt.

The point of the seam is that **nothing is written to disk**: the archive is a
``{name: ndarray}`` dict handed to ``NpzResultsRepository``, which is what lets a
result be viewed without an NPZ.  The GUI test file covers the wiring around it.
"""

import numpy as np
import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.io.results_repository import NpzResultsRepository
from fea_toolkit.model.load_combinations import classify_combination_refs
from fea_toolkit.model.sap_data import LoadCase, LoadCombination, LoadCombinationEntry

#: The elastic static config the cantilever is solved with.
_AB_CONFIG = {
    "element_type": "elasticBeamColumn",
    "verbose": False,
    "create_shells": False,
}


def _case(name: str, assignments) -> LoadCase:
    lc = LoadCase(name, "LinStatic", "Prog Det", "Dead", "Prog Det", "Non-Composite")
    lc.case_data["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = assignments
    return lc


@pytest.fixture
def model():
    """The built-in cantilever, given a DEAD / WIND case and one combination.

    ``make_sample_model()`` defines the ``DEAD`` (self-weight) and ``WIND``
    (frame distributed) patterns but no load cases, so the cases and the
    combination are attached here.
    """
    md = make_sample_model()
    md.load_cases = {
        "DEAD": _case("DEAD", [{"LoadName": "DEAD"}]),
        "WIND": _case("WIND", [{"LoadName": "WIND", "LoadSF": 1.0}]),
    }
    combos = {"GRAV": LoadCombination("GRAV", "Linear Add", [LoadCombinationEntry("DEAD", 1.2)])}
    classify_combination_refs(combos, md.load_cases)
    md.load_combinations = combos
    return md


@pytest.fixture
def mesh(model):
    """The preprocessed cantilever, with ``ops.wipe()`` on teardown."""
    import openseespy.opensees as ops

    from fea_toolkit.opensees.preprocessor import preprocess_model

    yield preprocess_model(model, _AB_CONFIG)
    ops.wipe()


# ── The archive ────────────────────────────────────────────────────────


def test_one_solve_becomes_an_in_memory_case_view(model, mesh):
    from fea_toolkit.analysis.linear import run_case_set

    result = run_case_set(model, mesh, {"DEAD": {"DEAD": 1.0}})

    # Served through a repository — the same read seam a file-backed archive
    # uses, so nothing downstream knows there is no file (P27, refinement 1).
    repository = NpzResultsRepository(result["arrays"])
    assert repository.cases() == ["DEAD"]
    assert repository.has_displacements("DEAD")
    assert repository.nodal_displacements("DEAD")
    assert result["cases"] == ["DEAD"]
    assert result["failed"] == []
    assert result["unreduced"] == []
    assert result["cancelled"] is False


def test_the_arrays_are_a_plain_dict_no_file_is_needed(model, mesh):
    from fea_toolkit.analysis.linear import run_case_set

    result = run_case_set(model, mesh, {"DEAD": {"DEAD": 1.0}})

    assert isinstance(result["arrays"], dict)
    assert all(isinstance(name, str) for name in result["arrays"])
    assert all(isinstance(value, np.ndarray) for value in result["arrays"].values())
    assert "static_case_labels" in result["arrays"]


def test_the_display_geometry_matches_the_preprocessed_model(model, mesh):
    """``mesh_model`` is what the arrays are written from, so views line up."""
    from fea_toolkit.analysis.linear import run_case_set

    result = run_case_set(model, mesh, {"DEAD": {"DEAD": 1.0}})
    repository = NpzResultsRepository(result["arrays"])

    assert len(repository.display_geometry()["node_tag"]) == len(mesh.nodes)
    assert repository.geometry_counts()["n_frames"] == len(mesh.frame_elements)


# ── Combinations ───────────────────────────────────────────────────────


def test_a_combination_is_reduced_from_its_case_results(model, mesh):
    from fea_toolkit.analysis.linear import run_case_set

    result = run_case_set(
        model, mesh, {"DEAD": {"DEAD": 1.0}}, combinations={"GRAV": {"DEAD": 1.2}}
    )

    assert "GRAV" in result["cases"]
    repository = NpzResultsRepository(result["arrays"])
    assert set(repository.cases()) >= {"DEAD", "GRAV"}
    # The composite records which combination it came from.
    assert repository.case_meta("GRAV").get("group") == "GRAV"


def test_a_combination_whose_leaf_did_not_solve_is_not_reduced(model, mesh):
    """An envelope over a missing case would be quietly wrong — say so instead."""
    from fea_toolkit.analysis.linear import run_case_set

    result = run_case_set(model, mesh, {}, combinations={"GRAV": {"DEAD": 1.2}})

    assert result["cases"] == []
    assert result["unreduced"] == ["GRAV"]
    assert "GRAV" not in NpzResultsRepository(result["arrays"]).cases()


# ── Cancellation ───────────────────────────────────────────────────────


def test_cancelling_before_the_first_case_produces_nothing(model, mesh):
    from fea_toolkit.analysis.linear import run_case_set

    result = run_case_set(
        model, mesh, {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}}, should_cancel=lambda: True
    )

    assert result["cancelled"] is True
    assert result["cases"] == []
    # A cancelled case did not fail — it was never run.
    assert result["failed"] == []


def test_cancelling_after_one_case_keeps_it_and_reports_no_failure(model, mesh):
    """A skipped case is not a failure: cancellation is reported separately."""
    from fea_toolkit.analysis.linear import run_case_set

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # False before DEAD, True before WIND

    result = run_case_set(
        model, mesh, {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}}, should_cancel=cancel
    )

    assert result["cases"] == ["DEAD"]
    assert result["cancelled"] is True
    # WIND was never attempted, so it is not reported as a failure.
    assert result["failed"] == []


# ── The builder's config follows the mesh ──────────────────────────────


class _Stub:
    """Just the ``MeshModel`` attribute the shell decision reads."""

    def __init__(self, area_element_types) -> None:
        self.area_element_types = area_element_types


def test_shells_are_built_when_the_mesh_records_shell_element_types():
    """``create_shells`` follows the mesh's own record, not a hard-coded default.

    A preprocessed model whose areas are shells needs the builder to create
    them, or a wall/slab-stiffened model is a mechanism — the singular-matrix
    failure the Admin Building run hit.  ``area_element_types`` is non-empty
    exactly when the Preprocessor built shells.
    """
    from fea_toolkit.analysis.linear import _mesh_has_shells

    assert _mesh_has_shells(_Stub({"1": "ShellMITC4"})) is True
    assert _mesh_has_shells(_Stub({})) is False  # split only / no shells


def test_a_split_only_mesh_is_not_mistaken_for_a_meshed_one():
    """Regression: a split-only mesh must not force ``create_shells`` on.

    ``loads_only_area_ids`` is **empty** for a plain ``create_shells=False`` run
    (the Preprocessor only fills it on the selection path), while every area is
    in fact loads-only.  Deriving from it made a split-only mesh look meshed, so
    the builder was told to create 323 shells the user had not asked for.
    """
    from fea_toolkit.analysis.linear import _mesh_has_shells

    split_only = _Stub({})
    split_only.area_elements = {"1": object(), "2": object()}  # areas do exist…
    split_only.loads_only_area_ids = set()  # …and this is empty for split-only

    assert _mesh_has_shells(split_only) is False
