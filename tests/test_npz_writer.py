"""``io/npz_writer`` — assembling a results archive, with or without a file.

The seam this pins: :func:`results_arrays` returns the archive as a plain
``{name: ndarray}`` dict, so results can be **viewed without being written**.
``write_results_npz`` is the same dict plus a save, which is what keeps the two
from drifting into two different archive layouts.
"""

import numpy as np
import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.io.results_repository import NpzResultsRepository

_FORCE_COMPONENTS = ("fx", "fy", "fz", "mx", "my", "mz")


def _static_results(md) -> dict:
    """A one-case static payload in the shape the collectors expect.

    Values are distinguishable (100·component + end offset + element index) so a
    mis-mapped array would fail an equality rather than land on a plausible
    zero, and the force arrays are component-keyed — the shape
    ``AnalysisBuilder.static_element_force_arrays()`` produces.
    """
    tags = sorted(node.node_tag for node in md.nodes.values())
    n_frames = len(md.frame_elements)
    forces = {}
    for number, component in enumerate(_FORCE_COMPONENTS):
        for offset, end in ((0, "i"), (50, "j")):
            forces[f"{component}_{end}"] = [
                100.0 * number + offset + index for index in range(n_frames)
            ]
    return {
        "DEAD": {
            "nodal_displacements": dict.fromkeys(tags, (0.001, 0.0, 0.0, 0.0, 0.0, 0.0)),
            "element_forces": forces,
        }
    }


def test_the_assembled_arrays_serve_a_repository_with_no_file():
    """The point of the seam: a view needs the dict, not an archive.

    Every accessor a results view uses must work off the in-memory arrays —
    cases, their metadata, the display geometry, displacements and end forces —
    because that is what lets the GUI show a result it never wrote.
    """
    from fea_toolkit.io.npz_writer import results_arrays

    md = make_sample_model()
    arrays = results_arrays(md, static_results=_static_results(md))
    repository = NpzResultsRepository(arrays)

    assert repository.cases() == ["DEAD"]
    assert repository.geometry_counts()["n_nodes"] == len(md.nodes)
    model = repository.as_model()
    assert len(model.nodes) == len(md.nodes)
    assert len(model.frame_elements) == len(md.frame_elements)

    assert repository.has_displacements("DEAD") is True
    assert repository.has_forces("DEAD") is True
    displacements = repository.nodal_displacements("DEAD")
    assert set(displacements) == set(model.nodes)
    assert displacements["1"][0] == pytest.approx(0.001)
    assert set(repository.element_forces("DEAD")) == set(model.frame_elements)


def test_case_metadata_travels_with_the_in_memory_arrays():
    """``group`` / ``family`` / ``coords`` are part of the same dict, not the file."""
    from fea_toolkit.io.npz_writer import results_arrays

    md = make_sample_model()
    arrays = results_arrays(
        md,
        static_results=_static_results(md),
        case_meta={"DEAD": {"group": "DEAD", "family": "single", "coords": ""}},
    )

    assert NpzResultsRepository(arrays).case_meta("DEAD")["group"] == "DEAD"


def test_writing_is_the_same_arrays_plus_a_save(tmp_path):
    """``write_results_npz`` must not assemble its own archive.

    Compared array by array, so a future edit that adds a key (or changes one)
    on only one of the two paths fails here rather than in a consumer.
    """
    from fea_toolkit.io.npz_reader import read_results
    from fea_toolkit.io.npz_writer import results_arrays, write_results_npz

    md = make_sample_model()
    static = _static_results(md)
    path = write_results_npz(str(tmp_path / "results.npz"), md, static_results=static)

    written = read_results(path)
    assembled = results_arrays(md, static_results=static)

    assert set(written) == set(assembled)
    for name, value in assembled.items():
        if name == "created":  # a timestamp, by definition different
            continue
        assert np.array_equal(written[name], value), name


def test_a_non_local_coordinate_system_is_refused():
    """The archive records element-local forces; anything else must not be claimed."""
    from fea_toolkit.io.npz_writer import results_arrays

    md = make_sample_model()
    with pytest.raises(ValueError, match="forces_coordinate_system"):
        results_arrays(md, static_results=_static_results(md), forces_coordinate_system="global")


def test_saving_an_assembled_dict_round_trips_it(tmp_path):
    """``save_results_arrays`` writes the dict it is handed — the P27/I4 seam.

    A caller that already holds the arrays (the GUI's ``Analysis ▸ Run``)
    persists through the same function ``write_results_npz`` ends in, so the two
    can never write two different layouts.
    """
    from fea_toolkit.io.npz_reader import read_results
    from fea_toolkit.io.npz_writer import results_arrays, save_results_arrays

    md = make_sample_model()
    arrays = results_arrays(md, static_results=_static_results(md))

    path = save_results_arrays(str(tmp_path / "saved.npz"), arrays)
    written = read_results(path)

    assert set(written) == set(arrays)
    for name, value in arrays.items():
        if name == "created":  # a timestamp, by definition different
            continue
        assert np.array_equal(written[name], value), name


def test_saving_what_a_repository_serves_round_trips_a_case(tmp_path):
    """The GUI saves ``repository.raw()`` — that has to read back as a case."""
    from fea_toolkit.io.npz_writer import results_arrays, save_results_arrays

    md = make_sample_model()
    repository = NpzResultsRepository(results_arrays(md, static_results=_static_results(md)))

    path = save_results_arrays(str(tmp_path / "again.npz"), repository.raw())

    assert NpzResultsRepository(path).cases() == ["DEAD"]


# ── Node keying: the mesh's derived ids are labels, not numbers ─────────


def _model_with_a_derived_node():
    """The sample model plus one node whose id a meshed model would produce.

    A split/meshed model's derived node ids are strings like ``"5_af_0_1"``,
    which is what ``int(key)`` choked on when displacements were sorted.
    """
    from fea_toolkit.model.sap_data import Node

    md = make_sample_model()
    md.nodes["5_af_0_1"] = Node(node_id="5_af_0_1", node_tag=999, x=1.0, y=0.0, z=5.0)
    return md


def test_a_derived_node_id_does_not_break_the_displacement_arrays():
    """Displacement keyed by node **id** (what the runner produces) aligns.

    The arrays are written in the geometry's node order and read index-wise, so
    they must never be ordered by parsing the key as a number.
    """
    from fea_toolkit.io.npz_writer import results_arrays

    md = _model_with_a_derived_node()
    order = list(md.nodes)
    disp = {nid: (0.1 * (index + 1), 0.0, 0.0) for index, nid in enumerate(order)}

    arrays = results_arrays(md, static_results={"DEAD": {"nodal_displacements": disp}})

    assert arrays["static/DEAD/node_dx"].tolist() == [0.1 * (i + 1) for i in range(len(order))]


def test_tag_keyed_displacement_still_resolves():
    """Hand-built payloads key by tag — both keyings have to work."""
    from fea_toolkit.io.npz_writer import results_arrays

    md = _model_with_a_derived_node()
    disp = {nd.node_tag: (float(nd.node_tag), 0.0, 0.0) for nd in md.nodes.values()}

    arrays = results_arrays(md, static_results={"DEAD": {"nodal_displacements": disp}})

    assert arrays["static/DEAD/node_dx"].tolist() == [
        float(nd.node_tag) for nd in md.nodes.values()
    ]
