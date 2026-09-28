"""The view registry — Qt-free, no OpenSees.

A view is a **lens**, not a copy: it carries display-safe metadata (name, kind,
provenance, counts) while the geometry stays in the registry under the view's
key.  These tests pin that contract, because it is what keeps several views of a
large model from multiplying it in memory.
"""


def _model(elements: int = 2):
    """A minimal stand-in: the registry never inspects the model internals."""
    from types import SimpleNamespace

    return SimpleNamespace(
        units={"F": "N", "L": "m", "T": "C"},
        nodes={"1": object(), "2": object()},
        frame_elements={str(i): SimpleNamespace(inactive=False) for i in range(elements)},
        area_elements={},
        materials={},
        sections={},
    )


def test_a_view_reports_its_counts_and_never_its_model():
    """``View`` is display-safe: every field is a scalar, never a model."""
    from fea_toolkit.gui.controllers.view_registry import GEOMETRY, ViewRegistry

    model = _model()
    registry = ViewRegistry()
    view = registry.add_geometry("unprocessed", "Unprocessed", model, source="sample.s2k")

    assert view.kind == GEOMETRY
    assert view.source == "sample.s2k"
    assert view.n_frames == 2
    assert view.n_nodes == 2
    # The model itself is never a view field — the registry holds it.
    assert model not in vars(view).values()
    assert registry.source("unprocessed") is model


def test_the_active_view_is_reported_on_every_lookup():
    """``active`` is derived from the registry, not stored on the view."""
    from fea_toolkit.gui.controllers.view_registry import ViewRegistry

    registry = ViewRegistry()
    registry.add_geometry("unprocessed", "Unprocessed", _model())
    registry.add_geometry("processed", "Processed", _model(3))

    assert registry.active.key == "processed"
    assert [view.name for view in registry.views()] == ["Unprocessed", "Processed"]
    assert [view.active for view in registry.views()] == [False, True]

    assert registry.set_active("unprocessed") is True
    assert registry.get("unprocessed").active is True
    assert registry.get("processed").active is False


def test_registering_the_same_key_replaces_the_view():
    """Re-running an action refreshes its view instead of piling up copies."""
    from fea_toolkit.gui.controllers.view_registry import ViewRegistry

    registry = ViewRegistry()
    registry.add_geometry("processed", "Processed", _model())
    registry.add_geometry("processed", "Processed", _model(5))

    assert len(registry) == 1
    assert registry.get("processed").n_frames == 5


def test_an_unknown_view_is_reported_as_missing():
    """Unknown keys answer ``None``/``False`` rather than raising."""
    from fea_toolkit.gui.controllers.view_registry import ViewRegistry

    registry = ViewRegistry()

    assert registry.get("nope") is None
    assert registry.source("nope") is None
    assert registry.set_active("nope") is False
    assert registry.active is None


def test_reset_drops_the_views_and_their_geometry():
    """Loading a different model must not leave the old geometry reachable."""
    from fea_toolkit.gui.controllers.view_registry import ViewRegistry

    registry = ViewRegistry()
    registry.add_geometry("unprocessed", "Unprocessed", _model())
    registry.reset()

    assert len(registry) == 0
    assert registry.views() == []
    assert registry.source("unprocessed") is None
    assert registry.active is None
