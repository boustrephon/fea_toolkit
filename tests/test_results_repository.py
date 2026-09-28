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


def _geometry() -> dict:
    """Geometry arrays for a split member plus a triangular panel.

    Nothing here is a model: these are the arrays an archive carries, which is
    exactly what :func:`mesh_model_from_geometry` rebuilds a display model from.
    """
    return {
        "node_tag": np.array([1, 2, 3], dtype=int),
        "node_sap_id": np.array(["1", "2", "3"], dtype=str),
        "node_x": np.zeros(3),
        "node_y": np.zeros(3),
        "node_z": np.array([0.0, 10.0, 20.0]),
        "frame_eid": np.array([1, 2, 3, 4], dtype=int),
        "frame_sap_id": np.array(["1", "1-0", "1-1", "2"], dtype=str),
        "frame_sec_name": np.array(["UB300"] * 4, dtype=str),
        "frame_parent_sap_id": np.array(["", "1", "1", ""], dtype=str),
        "frame_node_i": np.array([1, 1, 2, 2], dtype=int),
        "frame_node_j": np.array([3, 2, 3, 3], dtype=int),
        "shell_eid": np.array([11], dtype=int),
        "shell_sap_id": np.array(["A1"], dtype=str),
        "shell_sec_name": np.array(["Slab"], dtype=str),
        "shell_parent_sap_id": np.array([""], dtype=str),
        "shell_node_1": np.array([1], dtype=int),
        "shell_node_2": np.array([2], dtype=int),
        "shell_node_3": np.array([3], dtype=int),
        "shell_node_4": np.array([0], dtype=int),  # a triangle stores a blank corner
    }


class TestModelFromGeometry:
    """An archive rebuilds a *display* model, so a view needs no ``.s2k``."""

    def test_it_rebuilds_the_drawable_model(self):
        from fea_toolkit.io.results_repository import mesh_model_from_geometry

        md = mesh_model_from_geometry(_geometry())

        assert sorted(md.nodes) == ["1", "2", "3"]
        assert md.nodes["3"].z == 20.0
        assert sorted(md.frame_elements) == ["1", "1-0", "1-1", "2"]
        # Connectivity is stored as node *tags*, and comes back as SAP labels.
        assert md.frame_elements["1-0"].node_i == "1"
        assert md.frame_elements["1-0"].node_j == "2"
        assert md.frame_assignments["1-0"] == "UB300"

    def test_it_rebuilds_the_split_hierarchy(self):
        """Parents and children are recovered, so the model collapses like the real one."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry

        md = mesh_model_from_geometry(_geometry())

        assert md.frame_elements["1"].inactive is True
        assert md.frame_elements["1"].child_ids == ["1-0", "1-1"]
        assert md.frame_elements["1-0"].parent_id == "1"
        assert md.frame_elements["2"].inactive is False

    def test_a_triangle_stored_with_a_blank_corner_keeps_three_nodes(self):
        from fea_toolkit.io.results_repository import mesh_model_from_geometry

        md = mesh_model_from_geometry(_geometry())

        assert md.area_elements["A1"].node_ids == ["1", "2", "3"]
        assert md.area_assignments["A1"] == "Slab"

    def test_an_element_without_geometry_is_skipped(self):
        """A dangling connectivity reference is dropped, not drawn at the origin."""
        from fea_toolkit.io.results_repository import mesh_model_from_geometry

        geometry = _geometry()
        geometry["frame_node_j"] = np.array([3, 2, 3, 99], dtype=int)

        md = mesh_model_from_geometry(geometry)

        assert "2" not in md.frame_elements
        assert "1" in md.frame_elements

    def test_the_rebuilt_model_agrees_with_the_reported_counts(self):
        """``as_model`` and ``geometry_counts`` must not disagree about size."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        geometry = _geometry()
        repository = NpzResultsRepository(geometry)
        model = repository.as_model()
        counts = repository.geometry_counts()

        assert len(model.nodes) == counts["n_nodes"]
        assert len(model.frame_elements) == counts["n_frames"]
        assert len(model.area_elements) == counts["n_shells"]


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


# ═══════════════════════════════════════════════════════════════════
# Nodal displacement — the deformed-shape input
# ═══════════════════════════════════════════════════════════════════


class TestNodalDisplacements:
    """The deformed shape's input: node-keyed, and tied to the display model."""

    def test_the_ids_match_the_display_model(self, archive):
        """Ids must be ``as_model``'s — that is what ``render_deformed`` looks up."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(str(archive))
        displacements = repository.nodal_displacements("COMB1")
        assert set(displacements) == set(repository.as_model().nodes)

    def test_the_values_come_from_the_case_arrays(self, archive):
        """``static/COMB1/node_dx`` is ones(3), so x is 1 and y/z are unset."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        displacements = NpzResultsRepository(str(archive)).nodal_displacements("COMB1")
        assert np.allclose(displacements["2"], [1.0, 0.0, 0.0])

    def test_a_component_the_archive_omits_reads_as_zero(self, archive):
        """Only ``node_dx`` was written; the shape must not be corrupted by that."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        displacements = NpzResultsRepository(str(archive)).nodal_displacements("COMB1")
        assert all(np.allclose(value, [1.0, 0.0, 0.0]) for value in displacements.values())

    def test_an_archive_without_displacement_returns_empty(self, tmp_path):
        """Displacement recording is optional: absence is a state, not an error."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        path = tmp_path / "forces_only.npz"
        np.savez_compressed(
            path,
            node_tag=np.array([1, 2], dtype=int),
            node_x=np.zeros(2),
            node_y=np.zeros(2),
            node_z=np.array([0.0, 10.0]),
            frame_eid=np.array([1], dtype=int),
            frame_node_i=np.array([1], dtype=int),
            frame_node_j=np.array([2], dtype=int),
            static_case_labels=np.array(["DEAD"], dtype=str),
            **{"static/DEAD/fx_i": np.zeros(1)},
        )
        repository = NpzResultsRepository(str(path))
        assert repository.nodal_displacements("DEAD") == {}

    def test_has_displacements_is_the_cheap_pre_check(self, archive):
        """What a caller consults before offering to draw a deformed shape."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(str(archive))
        assert repository.has_displacements("DEAD") is True
        assert repository.has_displacements("NOT_A_CASE") is False


# ═══════════════════════════════════════════════════════════════════
# Element end forces — the flag-diagram input
# ═══════════════════════════════════════════════════════════════════


def _forces_archive(*, local: bool = True, sap_ids=None) -> dict:
    """Geometry plus a static case whose force arrays cover its four frames.

    Every component carries a value that identifies it —
    ``100 * component + (50 if J-end) + frame`` — so a mis-indexed component or
    a mis-mapped element fails an equality instead of landing on a plausible
    zero.
    """
    arrays = dict(_geometry())
    if sap_ids is not None:
        arrays["frame_sap_id"] = np.array(sap_ids, dtype=str)
    arrays["static_case_labels"] = np.array(["COMB1"], dtype=str)
    for number, component in enumerate(("fx", "fy", "fz", "mx", "my", "mz")):
        for offset, end in ((0, "i"), (50, "j")):
            arrays[f"static/COMB1/{component}_{end}"] = np.array(
                [100.0 * number + offset + frame for frame in range(4)]
            )
    if local:
        arrays["forces_coordinate_system"] = np.array(["local"], dtype=str)
    return arrays


class TestElementForces:
    """The force diagram's input: frame-keyed, and read in geometry order."""

    def test_the_ids_match_the_display_model(self):
        """Ids must be ``as_model``'s — those are the frames a viewer draws."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(_forces_archive())

        assert set(repository.element_forces("COMB1")) == set(repository.as_model().frame_elements)

    def test_the_values_come_from_the_case_arrays_by_index(self):
        """``mz_i`` of the second frame is ``500 + 1``: component and frame both."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        forces = NpzResultsRepository(_forces_archive()).element_forces("COMB1")

        assert forces["1-0"]["fx_i"] == 1.0
        assert forces["1-0"]["mz_i"] == 501.0
        assert forces["1-0"]["mz_j"] == 551.0
        assert forces["2"]["my_j"] == 453.0

    def test_the_arrays_are_read_in_geometry_order_not_sorted_by_id(self):
        """The join is positional, so an unsorted id order must not be re-sorted."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(_forces_archive(sap_ids=["B", "A", "D", "C"]))
        forces = repository.element_forces("COMB1")

        assert forces["B"]["mz_i"] == 500.0  # frame 0, as the geometry orders it
        assert forces["D"]["mz_i"] == 502.0  # frame 2 — not the third row by id

    def test_a_local_archive_mirrors_its_values_under_the_local_keys(self):
        """``use_local=True`` readers take the verbatim path: nothing to rotate."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        forces = NpzResultsRepository(_forces_archive(local=True)).element_forces("COMB1")

        assert forces["1-0"]["mz_i_local"] == forces["1-0"]["mz_i"] == 501.0
        assert forces["1-0"]["mz_j_local"] == 551.0
        assert forces["2"]["fy_i_local"] == 103.0

    def test_a_global_archive_offers_no_local_keys(self):
        """Without the flag and without aliases a reader must rotate for itself."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        forces = NpzResultsRepository(_forces_archive(local=False)).element_forces("COMB1")

        assert [key for key in forces["1-0"] if key.endswith("_local")] == []
        assert forces["1-0"]["mz_i"] == 501.0

    def test_an_explicit_local_block_is_read_when_the_flag_is_absent(self):
        """A producer's own local arrays win over anything this seam would derive."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        arrays = _forces_archive(local=False)
        arrays["static/COMB1/mz_i_local"] = np.array([900.0, 901.0, 902.0, 903.0])

        forces = NpzResultsRepository(arrays).element_forces("COMB1")

        assert forces["1-0"]["mz_i_local"] == 901.0  # the alias, not the bare array
        assert forces["1-0"]["mz_i"] == 501.0
        assert "my_i_local" not in forces["1-0"]  # never written — absent, not zero

    def test_a_component_the_archive_omits_is_absent_not_zero(self):
        """A missing component must not read as a zero force."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        arrays = _forces_archive()
        del arrays["static/COMB1/fy_i"]

        forces = NpzResultsRepository(arrays).element_forces("COMB1")

        assert "fy_i" not in forces["1-0"]
        assert forces["1-0"]["fy_j"] == 151.0
        assert forces["1-0"]["fx_i"] == 1.0

    def test_a_short_component_array_drops_only_itself(self):
        """A truncated array covers fewer frames; every other component survives."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        arrays = _forces_archive()
        arrays["static/COMB1/mz_j"] = np.array([550.0, 551.0])  # two of four frames

        forces = NpzResultsRepository(arrays).element_forces("COMB1")

        assert forces["1-0"]["mz_j"] == 551.0
        assert "mz_j" not in forces["1-1"]
        assert forces["1-1"]["mz_i"] == 502.0

    def test_an_archive_without_forces_returns_empty(self):
        """Force recording is optional: absence is a state, not an error."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        assert NpzResultsRepository(_geometry()).element_forces("COMB1") == {}
        assert NpzResultsRepository(_forces_archive()).element_forces("NOT_A_CASE") == {}

    def test_the_local_flag_is_reported(self):
        """What a caller consults before choosing ``use_local`` for the overlay."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        assert NpzResultsRepository(_forces_archive(local=True)).forces_are_local() is True
        assert NpzResultsRepository(_forces_archive(local=False)).forces_are_local() is False

    def test_has_forces_is_the_cheap_pre_check(self):
        """What a caller consults before offering to draw a flag diagram."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        repository = NpzResultsRepository(_forces_archive())

        assert repository.has_forces("COMB1") is True
        assert repository.has_forces("NOT_A_CASE") is False

    def test_has_forces_sees_an_archive_that_wrote_only_the_local_block(self):
        """The optional aliases are still end forces — the check is not too narrow."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        arrays = dict(_geometry())
        arrays["static/COMB1/mz_i_local"] = np.zeros(4)

        repository = NpzResultsRepository(arrays)

        assert repository.has_forces("COMB1") is True
        assert repository.element_forces("COMB1")["1"]["mz_i_local"] == 0.0

    def test_the_id_falls_back_to_the_frame_tag_without_sap_ids(self, archive):
        """A hand-built archive may carry tags only — mirror the node accessor."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        forces = NpzResultsRepository(str(archive)).element_forces("DEAD")

        assert sorted(forces) == ["1", "2"]
        assert forces["2"] == {"fx_i": 0.0}


def test_metadata_reads_a_file_level_array():
    """Units and the coordinate system are written once, not per case."""
    from fea_toolkit.io.results_repository import NpzResultsRepository

    repository = NpzResultsRepository(
        {"force_unit": np.array(["kN"], dtype=str), "static/DEAD/fx_i": np.zeros(1)}
    )

    assert repository.metadata("force_unit").tolist() == ["kN"]
    assert repository.metadata("no_such_meta") is None
    assert repository.metadata("no_such_meta", default="kN") == "kN"
