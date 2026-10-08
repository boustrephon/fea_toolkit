"""Unit tests for the Qt-free model-tree index.

``model_index`` holds the tree's structure and labelling, deliberately without
Qt, so these run on a minimal install (no ``[gui]`` extra).
"""

from examples.sample_model import make_sample_model
from fea_toolkit.gui.models.model_index import build_groups, element_label, load_label


def _by_label(model):
    return {group.label: group for group in build_groups(model)}


def test_sample_model_groups_and_counts():
    groups = _by_label(make_sample_model())
    assert groups["Nodes"].count == 2
    assert groups["Frame Elements"].count == 1
    assert groups["Materials"].count == 1
    assert groups["Sections"].count == 1
    assert groups["Load Patterns"].count == 2


def test_empty_groups_are_omitted():
    """A group with nothing in it never becomes a row."""
    md = make_sample_model()
    assert not md.area_elements
    labels = _by_label(md)
    assert "Area Elements" not in labels


def test_items_are_labelled_by_the_sap_key():
    groups = _by_label(make_sample_model())
    items = groups["Nodes"].load_items()
    assert [label for label, _ in items] == ["1", "2"]
    assert dict(items)["1"].node_id == "1"


def test_load_items_are_labelled_by_pattern_and_target():
    groups = _by_label(make_sample_model())
    items = groups["Frame Distributed Loads"].load_items()
    assert [label for label, _ in items] == ["WIND @ 1"]


def test_element_label_prefers_the_entity_id():
    md = make_sample_model()
    frame = next(iter(md.frame_elements.values()))
    assert element_label(frame) == "1"


def test_load_label_falls_back_to_the_class_name():
    class Opaque:
        pass

    assert load_label(Opaque()) == "Opaque"


def test_response_spectrum_functions_appear_as_a_group():
    from fea_toolkit.model.sap_data import ResponseSpectrumFunction

    md = make_sample_model()
    md.response_spectrum_functions = {
        "UNIFRS": ResponseSpectrumFunction("UNIFRS", [0.0, 1.0], [1.0, 1.0])
    }
    groups = _by_label(md)
    items = groups["Response Spectra"].load_items()
    assert [label for label, _ in items] == ["UNIFRS"]


def test_views_group_nests_children_under_their_parent():
    """A fan-out child (a mode, an RS direction) nests under its summary view."""
    from fea_toolkit.gui.controllers.view_registry import View
    from fea_toolkit.gui.models.model_index import views_group

    parent = View(key="table:modal", name="Modal", kind="table", source="test")
    mode1 = View(
        key="results:Mode-1", name="Mode 1", kind="results", source="test", parent="table:modal"
    )
    mode2 = View(
        key="results:Mode-2", name="Mode 2", kind="results", source="test", parent="table:modal"
    )
    top = View(key="unprocessed", name="Unprocessed", kind="geometry", source="test")

    group = views_group([parent, mode1, mode2, top])

    assert group.count == 4
    assert [name for name, _ in group.load_items()] == ["Modal", "Unprocessed"]
    assert [name for name, _ in group.child_items(parent)] == ["Mode 1", "Mode 2"]
    assert group.child_items(top) == []


def test_views_group_treats_an_unknown_parent_as_top_level():
    """A view whose parent is not registered is listed at the top, not lost."""
    from fea_toolkit.gui.controllers.view_registry import View
    from fea_toolkit.gui.models.model_index import views_group

    orphan = View(key="results:X", name="X", kind="results", source="test", parent="missing")
    group = views_group([orphan])
    assert [name for name, _ in group.load_items()] == ["X"]
    assert group.child_items(orphan) == []
