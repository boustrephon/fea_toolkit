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
