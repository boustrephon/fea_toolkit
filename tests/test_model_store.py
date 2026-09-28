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


def _slab_model():
    """A four-node area — enough to tell a meshed run from a split-only one."""
    from fea_toolkit.model.sap_data import (
        AreaElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        ShellSection,
    )

    return SAPModelData(
        nodes={
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.0),
            "4": Node("4", 4, 0.0, 4.0, 0.0),
        },
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        materials={"C30": Material(name="C30", type="Concrete", E_mod=3.0e10, unit_weight=25.0)},
        sections={"SLAB": ShellSection(name="SLAB", shape="Shell", material="C30", thickness=0.2)},
        frame_elements={},
        area_elements={"A1": AreaElement("A1", 1, ["1", "2", "3", "4"])},
        frame_assignments={},
        area_assignments={"A1": "SLAB"},
        groups={},
        frame_auto_mesh={},
    )


def test_a_new_config_is_not_defeated_by_the_last_preprocessed_model():
    """``mesh(config)`` must run with *config*, never return a stale mesh.

    ``Model ▸ Split elements`` then ``Model ▸ Mesh areas`` are two different
    configs.  ``mesh()`` short-circuited on the model ``set_preprocessed()`` had
    stored, so the second action silently did nothing — the Admin Building was
    then analysed with 323 **un-meshed** areas (a mechanism, hence the singular
    matrix) instead of 1177 shells.
    """
    from fea_toolkit.io.model_store import InMemoryModelStore

    store = InMemoryModelStore(_slab_model())

    split_only = store.mesh({"split_elements": True})
    store.set_preprocessed(split_only)  # what the GUI records after the action
    assert not split_only.area_element_types  # split only: no shells built

    meshed = store.mesh({"split_elements": True, "create_shells": True})

    assert meshed is not split_only
    assert meshed.area_element_types  # the config was honoured
    # ``set_preprocessed`` is a record, not a cache: it still reports the last
    # model the *caller* recorded, until the caller records the new one.
    assert store.preprocessed() is split_only


def test_an_injected_mesh_short_circuits_the_pipeline():
    """A mesh handed to the constructor is served as-is (its documented job)."""
    from fea_toolkit.io.model_store import InMemoryModelStore
    from fea_toolkit.opensees.preprocessor import preprocess_model

    md = _slab_model()
    injected = preprocess_model(md, {"split_elements": True})
    store = InMemoryModelStore(md, mesh_model=injected)

    assert store.mesh({"split_elements": True, "create_shells": True}) is injected
    assert store.preprocessed() is injected
