"""Qt tests for the lazily-populated ``ModelTreeModel``.

Gated by ``needs_gui``; ``tests/conftest.py`` skips the module without the
optional ``[gui]`` extra.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture()
def model(qapp):
    """A tree model over the sample model."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.models.tree_model import ModelTreeModel

    return ModelTreeModel(make_sample_model())


def test_root_lists_groups_with_counts(model):
    assert model.columnCount() == 2
    assert model.rowCount() > 0
    assert model.data(model.index(0, 0)) == "Nodes"
    assert model.data(model.index(0, 1)) == "2"
    from qtpy.QtCore import Qt

    assert model.headerData(0, Qt.Orientation.Horizontal) == "Name"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Count"


def test_groups_stay_lazy_until_expanded(model):
    """A group reports an expander but has no rows until it is opened."""
    group = model.index(0, 0)  # Nodes
    assert model.hasChildren(group)
    assert model.canFetchMore(group)
    assert model.rowCount(group) == 0

    model.fetchMore(group)
    assert not model.canFetchMore(group)
    assert model.rowCount(group) == 2
    assert model.data(model.index(0, 0, group)) == "1"


def test_child_exposes_its_entity_and_parent_round_trips(model):
    from qtpy.QtCore import Qt

    group = model.index(0, 0)
    model.fetchMore(group)
    child = model.index(0, 0, group)
    entity = model.data(child, Qt.ItemDataRole.UserRole)
    assert entity.node_id == "1"
    assert model.parent(child) == group
    assert not model.hasChildren(child)


def test_setting_none_empties_the_tree(model):
    model.set_model(None)
    assert model.rowCount() == 0
    assert not model.hasChildren()


def test_index_for_reaches_into_an_unexpanded_group(model):
    """A viewport pick must find an entity in a group nobody opened."""
    from qtpy.QtCore import Qt

    index = model.index_for("frame_elements", "1")

    assert index.isValid()
    assert model.data(index, Qt.ItemDataRole.UserRole).elem_id == "1"
    assert model.data(model.parent(index)) == "Frame Elements"


def test_index_for_reports_unknown_groups_and_labels(model):
    """A missing row is ``None``, not an invalid index -- the caller logs it."""
    assert model.index_for("no_such_group", "1") is None
    assert model.index_for("frame_elements", "999") is None
    assert model.index_for("nodes", "2").isValid()


def test_nested_views_bundle_children_under_their_parent(model):
    """A fan-out child view nests under its parent view, and round-trips back."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.controllers.view_registry import View

    parent = View(key="table:modal", name="Modal", kind="table", source="test")
    child = View(
        key="results:Mode-1", name="Mode 1", kind="results", source="test", parent="table:modal"
    )
    model.set_model(make_sample_model(), views=[parent, child])

    views_group = model.index(0, 0)
    assert model.data(views_group) == "Views"
    model.fetchMore(views_group)

    parent_row = model.index(0, 0, views_group)
    assert model.data(parent_row) == "Modal"
    assert model.hasChildren(parent_row)
    assert model.canFetchMore(parent_row)

    model.fetchMore(parent_row)
    child_row = model.index(0, 0, parent_row)
    assert model.data(child_row) == "Mode 1"
    assert model.parent(child_row) == parent_row
    assert not model.hasChildren(child_row)
