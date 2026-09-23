"""Qt tests for the lazily-populated ``ModelTreeModel``.

Gated by ``needs_gui``; ``tests/conftest.py`` skips the module without the
optional ``[gui]`` extra.
"""

import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


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
