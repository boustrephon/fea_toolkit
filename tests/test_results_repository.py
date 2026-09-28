"""The results repository: one NumPy-typed seam over whatever holds results.

Qt-free and independent of OpenSees: the archive below is written with numpy
alone, which is the point — a repository reads a *format*, not a model.
"""

import numpy as np
import pytest


@pytest.fixture()
def archive(tmp_path):
    """A minimal results archive: geometry plus two static cases."""
    path = tmp_path / "results.npz"
    np.savez_compressed(
        path,
        node_tag=np.array([1, 2, 3], dtype=int),
        node_x=np.array([0.0, 5.0, 10.0]),
        node_y=np.zeros(3),
        node_z=np.zeros(3),
        frame_eid=np.array([1, 2], dtype=int),
        frame_node_i=np.array([1, 2], dtype=int),
        frame_node_j=np.array([2, 3], dtype=int),
        static_case_labels=np.array(["DEAD", "COMB1"], dtype=str),
        static_case_group=np.array(["DEAD", "COMB1"], dtype=str),
        static_case_family=np.array(["static", "static"], dtype=str),
        static_case_coords=np.array(["0", "1"], dtype=str),
        **{
            "static/DEAD/node_dx": np.zeros(3),
            "static/DEAD/fx_i": np.array([0.0, 0.0]),
            "static/COMB1/node_dx": np.ones(3),
            "static/COMB1/fx_i": np.array([1.0, 2.0]),
        },
    )
    return path


def test_the_cases_and_their_metadata_come_from_explicit_columns(archive):
    """``group`` / ``family`` / ``coords`` are columns, not name-mangled keys."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    repo = NpzResultsRepository(archive)

    assert repo.cases() == ["DEAD", "COMB1"]
    assert repo.case_meta("DEAD")["group"] == "DEAD"
    assert repo.case_meta("COMB1")["coords"] == "1"
    assert repo.case_meta("no-such-case") == {}


def test_arrays_for_a_case_strips_the_case_prefix(archive):
    """A caller asks for a case, not for ``static/<case>/...`` keys."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    repo = NpzResultsRepository(archive)
    arrays = repo.arrays_for("COMB1")

    assert set(arrays) == {"node_dx", "fx_i"}
    assert arrays["fx_i"].tolist() == [1.0, 2.0]


def test_display_geometry_is_geometry_not_results(archive):
    """Geometry is the schema's geometry key set — never the result arrays."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    geometry = NpzResultsRepository(archive).display_geometry()

    assert {"node_tag", "node_x", "frame_node_i"} <= set(geometry)
    assert not [name for name in geometry if name.startswith("static/")]


def test_table_returns_named_columns(archive):
    """Analytics asks for columns by name; the backend decides how to get them."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    columns = NpzResultsRepository(archive).table("node_x", "frame_eid")

    assert columns["node_x"].tolist() == [0.0, 5.0, 10.0]
    assert columns["frame_eid"].tolist() == [1, 2]


def test_an_unknown_column_is_reported(archive):
    """A typo is an error, not a silently empty result."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    with pytest.raises(KeyError, match="unknown column"):
        NpzResultsRepository(archive).table("node_x", "no_such_array")


def test_is_results_archive_recognises_the_unified_schema(archive):
    """The sniff lives in the reader, so consumers never call ``np.load``."""
    from fea_toolkit.io.npz_reader import is_results_archive

    assert is_results_archive(archive) is True


def test_is_results_archive_rejects_another_npz(tmp_path):
    """An archive of somebody else's arrays is not a results file."""
    import numpy as np

    from fea_toolkit.io.npz_reader import is_results_archive

    path = tmp_path / "other.npz"
    np.savez_compressed(path, something=np.zeros(3))

    assert is_results_archive(path) is False


def test_an_hdf5_path_is_treated_as_unified(tmp_path):
    """Stage files share the container, so the extension decides — no file read."""
    from fea_toolkit.io.npz_reader import is_results_archive

    assert is_results_archive(tmp_path / "model.h5") is True


def test_geometry_counts_come_from_the_array_shapes(archive):
    """Counts without converting anything — the same discipline as the model store."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    assert NpzResultsRepository(archive).geometry_counts() == {
        "n_nodes": 3,
        "n_frames": 2,
        "n_shells": 0,
    }


def test_geometry_counts_of_an_empty_archive_are_zero():
    """A missing block reads as zero, never an error — archives vary by analysis."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    assert NpzResultsRepository({}).geometry_counts() == {
        "n_nodes": 0,
        "n_frames": 0,
        "n_shells": 0,
    }


def test_a_repository_can_be_backed_by_a_plain_dict():
    """The seam does not require a file — a caller may already hold the arrays."""
    from fea_toolkit.io.results_repository import NpzResultsRepository, ResultsRepository

    repo = NpzResultsRepository({"node_x": np.zeros(2)})

    assert isinstance(repo, ResultsRepository)
    assert repo.path == ""
    assert len(repo) == 1
    assert repo.get("node_x").tolist() == [0.0, 0.0]
    assert repo.get("absent") is None
