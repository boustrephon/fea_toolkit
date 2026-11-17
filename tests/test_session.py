"""The Qt-free GUI session — document state with save/open round-trips.

The :class:`~fea_toolkit.gui.session.Session` object owns the GUI's document
state and is importable without the ``[gui]`` extra, so its behaviour is
tested here with no Qt or OpenSees in the process.
"""

from fea_toolkit.gui.session import Session


def test_reset_clears_document_state():
    session = Session()
    original_views = session.views
    session.store = object()
    session.named_selections["core"] = "selection"
    session.hidden_elem_ids.add("Frame:1")
    session.last_run = object()

    session.reset()

    assert session.store is None
    assert session.views is not original_views
    assert len(session.views) == 0
    assert session.named_selections == {}
    assert session.hidden_elem_ids == set()
    assert session.last_run is None


def test_reset_preserves_the_recipe():
    session = Session()
    session.recipe.add("mesh")

    session.reset()

    assert len(session.recipe) == 1
    assert session.recipe.steps[0].verb == "mesh"


def test_to_bundle_collects_the_session_into_a_project_payload():
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_store import InMemoryModelStore

    session = Session()
    model = make_sample_model()
    session.store = InMemoryModelStore(model, source="model.s2k")
    session.recipe.add("mesh")
    session.hidden_elem_ids.add("Frame:1")

    bundle = session.to_bundle(name="sample", source_label="model.s2k")

    assert set(bundle) == {"manifest", "sap", "mesh", "results_arrays", "figures", "tables"}
    assert bundle["sap"] is model
    assert bundle["mesh"] is None
    manifest = bundle["manifest"]
    assert manifest["project"]["name"] == "sample"
    assert manifest["project"]["recipe"]["steps"][0]["verb"] == "mesh"
    assert manifest["viewport"]["hidden_elem_ids"] == ["Frame:1"]
