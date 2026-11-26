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
    """Reset clears the document state but keeps the workflow recipe.

    A recipe run that adopts a new document (``RecipeController._adopt_document``) calls
    ``reset()`` and then applies the run's outcomes onto the recipe, so
    ``reset()`` must not drop it — only a user-initiated ``open_path`` clears
    the recipe (through the panel).
    """
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


def test_from_bundle_reconstructs_geometry_views_recipe_and_hidden():
    from examples.sample_model import make_sample_model

    model = make_sample_model()
    bundle = {
        "manifest": {
            "project": {
                "name": "sample",
                "source_label": "model.s2k",
                "recipe": {
                    "name": "r",
                    "steps": [{"verb": "mesh", "params": {"split_slabs_at_walls": True}}],
                },
            },
            "views": [
                {
                    "key": "unprocessed",
                    "name": "Unprocessed",
                    "kind": "geometry",
                    "source": "model.s2k",
                }
            ],
            "active_view": "unprocessed",
            "viewport": {},
            "display": {},
        },
        "sap": model,
        "mesh": None,
        "results": None,
        "tables": {},
        "figures": {},
    }

    session = Session.from_bundle(bundle)

    assert session.store.raw() is model
    assert [view.key for view in session.views.views()] == ["unprocessed"]
    assert session.recipe.steps[0].verb == "mesh"
    assert session.hidden_elem_ids == set()
    assert session.last_run is None


def test_from_bundle_drops_hidden_ids_from_another_topology():
    from examples.sample_model import make_sample_model

    model = make_sample_model()
    bundle = {
        "manifest": {
            "project": {"name": "sample", "source_label": ""},
            "views": [],
            "viewport": {"hidden_elem_ids": ["Frame:definitely-missing"]},
        },
        "sap": model,
        "mesh": None,
        "results": None,
        "tables": {},
        "figures": {},
    }

    session = Session.from_bundle(bundle)

    assert session.hidden_elem_ids == set()


def test_to_bundle_round_trips_through_the_zip_codec(tmp_path):
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.project import read_project, write_project_zip
    from fea_toolkit.io.model_store import InMemoryModelStore

    session = Session()
    model = make_sample_model()
    session.store = InMemoryModelStore(model, source="model.s2k")
    session.recipe.add("mesh")

    bundle = session.to_bundle(name="sample", source_label="model.s2k")
    zip_path = tmp_path / "session.zip"
    write_project_zip(zip_path, **bundle)

    restored = Session.from_bundle(read_project(zip_path))

    assert restored.store is not None
    assert [step.verb for step in restored.recipe] == ["mesh"]
    assert restored.hidden_elem_ids == set()
    assert restored.last_run is None


def test_record_run_stores_the_producing_mesh_signature():
    """``record_run`` keys the retained transient to the mesh it was passed."""
    from types import SimpleNamespace

    import numpy as np

    session = Session()
    node = SimpleNamespace(node_tag=7, x=1.0, y=2.0, z=3.0)
    mesh = SimpleNamespace(nodes={7: node})
    result = SimpleNamespace(
        kind="nonlinear_dynamic",
        label="A",
        payload=SimpleNamespace(
            data={
                "times": np.arange(2, dtype=float) * 0.01,
                "displacements": {7: np.zeros((2, 3))},
            }
        ),
    )
    run = SimpleNamespace(results=[result])

    session.record_run(run, mesh=mesh)

    assert session._retained_transient_geometry == {7: (1.0, 2.0, 3.0)}
    assert "A" in session._retained_transient


def test_record_run_modal_only_invalidates_stale_retained_histories():
    """A modal-only run over a re-meshed model drops stale retained histories."""
    from types import SimpleNamespace

    import numpy as np

    session = Session()
    session._retained_transient = {
        "A": {"times": np.arange(2), "displacements": {7: np.zeros((2, 3))}}
    }
    session._retained_transient_geometry = {7: (1.0, 2.0, 3.0)}

    moved = SimpleNamespace(node_tag=7, x=9.0, y=9.0, z=9.0)
    mesh = SimpleNamespace(nodes={7: moved})
    run = SimpleNamespace(results=[])

    session.record_run(run, mesh=mesh)

    assert session._retained_transient == {}
    assert session._retained_transient_geometry == {}


def test_record_run_modal_only_preserves_compatible_histories():
    """A modal-only run over a compatible mesh preserves retained histories."""
    from types import SimpleNamespace

    import numpy as np

    session = Session()
    session._retained_transient = {
        "A": {"times": np.arange(2), "displacements": {7: np.zeros((2, 3))}}
    }
    session._retained_transient_geometry = {7: (1.0, 2.0, 3.0)}

    node = SimpleNamespace(node_tag=7, x=1.0, y=2.0, z=3.0)
    mesh = SimpleNamespace(nodes={7: node})
    run = SimpleNamespace(results=[])

    session.record_run(run, mesh=mesh)

    assert "A" in session._retained_transient
    assert session._retained_transient_geometry == {7: (1.0, 2.0, 3.0)}


def test_merge_results_view_arrays_skips_transient_blocks():
    """A loaded repository's transient blocks do not leak into static arrays."""
    import numpy as np

    from fea_toolkit.io.results_repository import NpzResultsRepository
    from fea_toolkit.io.results_schema import make_transient_key

    repo_arrays = {
        "node_tag": np.array([1, 2]),
        "static_case_labels": np.array(["DEAD"]),
        "static/DEAD/node_dx": np.array([0.0, 0.0]),
        make_transient_key("A", "transient/{case}/time"): np.arange(3),
    }
    session = Session()
    session.views.add_results("results:DEAD", "DEAD", NpzResultsRepository(repo_arrays))

    arrays: dict = {}
    session._merge_results_view_arrays(arrays)

    assert "static/DEAD/node_dx" in arrays
    assert not any(key.startswith("transient/") for key in arrays)


def test_reconcile_analysis_types_keeps_transient_only_when_a_block_remains():
    """``analysis_types`` drops ``"transient"`` once no transient block survives."""
    import numpy as np

    from fea_toolkit.io.results_schema import make_transient_key

    arrays = {"analysis_types": np.array(["static", "transient", "rs"])}
    Session._reconcile_analysis_types(arrays)
    assert list(arrays["analysis_types"]) == ["static", "rs"]

    arrays = {
        "analysis_types": np.array(["static", "transient"]),
        make_transient_key("A", "transient/{case}/time"): np.arange(3),
    }
    Session._reconcile_analysis_types(arrays)
    assert list(arrays["analysis_types"]) == ["static", "transient"]
