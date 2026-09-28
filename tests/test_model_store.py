"""The model store and its metadata header — Qt-free, no OpenSees.

These pin the storage seam (``docs/dev_notes.md`` → *Results repository and the
NumPy-typed seam*): a header answers the counts a view needs **without walking
geometry**, and the store is the handle the GUI holds instead of a graph.
"""


def test_header_counts_the_model_without_walking_geometry():
    """Every count is a dictionary size, and nothing is extracted or copied."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_store import model_header

    md = make_sample_model()
    header = model_header(md)

    assert header.n_nodes == len(md.nodes)
    assert header.n_frames == len(md.frame_elements)
    assert header.n_frames_active == len(md.frame_elements)  # nothing split yet
    assert header.n_shells == len(md.area_elements)
    assert header.n_materials == len(md.materials)
    assert header.n_sections == len(md.sections)


def test_header_separates_active_from_superseded_frames():
    """A split parent counts in the total, not in the analysis-ready beams."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_store import model_header
    from fea_toolkit.opensees.preprocessor import preprocess_model

    mesh = preprocess_model(make_sample_model(), {"split_elements": True})
    header = model_header(mesh)
    inactive = sum(1 for elem in mesh.frame_elements.values() if elem.inactive)

    assert header.n_frames == len(mesh.frame_elements)
    assert header.n_frames_active == len(mesh.frame_elements) - inactive


def test_an_unknown_unit_system_reads_honestly():
    """No units is reported as no units — never an invented default."""
    from fea_toolkit.io.model_store import ModelHeader

    assert ModelHeader().units_label() == "units \u2014"
    assert (
        ModelHeader(units={"F": "kN", "L": "m", "T": "C"}).units_label() == "kN \u00b7 m \u00b7 C"
    )


def test_the_in_memory_store_hands_back_what_it_holds():
    """The default backend wraps the objects it was given, unchanged."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_store import InMemoryModelStore, ModelStore
    from fea_toolkit.model.mesh_model import MeshModel

    md = make_sample_model()
    store = InMemoryModelStore(md, source="sample.s2k")

    assert isinstance(store, ModelStore)
    assert store.raw() is md
    assert store.header().n_nodes == len(md.nodes)
    assert store.source == "sample.s2k"
    assert isinstance(store.mesh({"split_elements": True}), MeshModel)


def test_the_store_answers_a_header_for_any_model_it_is_given():
    """``header(model)`` summarises the argument, not only the stored raw model."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_store import InMemoryModelStore
    from fea_toolkit.opensees.preprocessor import preprocess_model

    md = make_sample_model()
    mesh = preprocess_model(md, {"split_elements": True})
    store = InMemoryModelStore(md)

    assert store.header(mesh).n_frames == len(mesh.frame_elements)
    assert store.header().n_frames == len(md.frame_elements)
