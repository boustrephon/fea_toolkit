"""Unit tests for :class:`ProjectController` — the Qt-free project round-trip.

The controller orchestrates :class:`~fea_toolkit.gui.session.Session` and the
Qt-free :mod:`fea_toolkit.gui.project` archive code, so a save → load round-trip
is testable without a running GUI (or OpenSees).
"""

from fea_toolkit.gui.controllers.project_controller import ProjectController
from fea_toolkit.gui.session import Session
from fea_toolkit.io.model_store import InMemoryModelStore


def _session_with_model() -> Session:
    from examples.sample_model import make_sample_model

    session = Session()
    session.store = InMemoryModelStore(make_sample_model())
    return session


def test_save_then_load_round_trips_the_model(tmp_path):
    controller = ProjectController()
    session = _session_with_model()
    n_nodes = len(session.store.raw().nodes)
    path = tmp_path / "project.zip"

    notes = controller.save(session, path, name="project", source_label="model.s2k")
    assert isinstance(notes, list)

    assert path.exists()
    assert controller.is_project(path)
    assert controller.member_summary(path)

    loaded, bundle = controller.load(path)
    assert bundle.get("sap") is not None
    assert loaded.store is not None
    assert len(loaded.store.raw().nodes) == n_nodes
    # ``from_bundle`` always registers the raw "unprocessed" geometry view.
    assert loaded.views.get("unprocessed") is not None
