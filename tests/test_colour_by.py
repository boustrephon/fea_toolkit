"""Qt-free colour-by resolver: per-element labels and colours.

The resolver walks a model's assignments (section → material) and geometry
(mid-height Z → story), so it is tested against a small hand-built model —
no Qt, no OpenSees.
"""

import pytest

from fea_toolkit.gui.controllers.colour_by import (
    COLOUR_FIELDS,
    FIELD_LABELS,
    colour_by_field,
    frame_labels,
    shell_labels,
    story_levels,
)
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    Material,
    Node,
    SAPModelData,
    Section,
    StoryDefinition,
)


def _model() -> SAPModelData:
    """A two-storey frame + one slab, with two materials and three stories."""
    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=2.5),
        "3": Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=6.0),
        "4": Node(node_id="4", node_tag=4, x=1.0, y=0.0, z=3.0),
        "5": Node(node_id="5", node_tag=5, x=1.0, y=1.0, z=3.0),
        "6": Node(node_id="6", node_tag=6, x=0.0, y=1.0, z=3.0),
    }
    materials = {
        "Steel": Material(name="Steel", type="Steel", E_mod=2.0e11),
        "Concrete": Material(name="Concrete", type="Concrete", E_mod=3.0e10),
    }
    sections = {
        "COL": Section(name="COL", shape="Rectangular", material="Steel", A=0.01),
        "SLAB": Section(name="SLAB", shape="Shell", material="Concrete", A=0.01),
    }
    frame_elements = {
        "F1": FrameElement(elem_id="F1", elem_tag=1, node_i="1", node_j="2"),
        "F2": FrameElement(elem_id="F2", elem_tag=2, node_i="2", node_j="3"),
        # A split parent, superseded by children — never drawable.
        "F3": FrameElement(elem_id="F3", elem_tag=3, node_i="1", node_j="3", inactive=True),
    }
    frame_assignments = {"F1": "COL", "F2": "COL", "F3": "COL"}
    area_elements = {
        "A1": AreaElement(area_id="A1", area_tag=1, node_ids=["4", "5", "6"]),
    }
    area_assignments = {"A1": "SLAB"}
    stories = [
        StoryDefinition(name="Base", elevation=0.0),
        StoryDefinition(name="L2", elevation=3.0),
        StoryDefinition(name="Roof", elevation=6.0),
    ]
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements=area_elements,
        frame_assignments=frame_assignments,
        area_assignments=area_assignments,
        groups={},
        frame_auto_mesh={},
        stories=stories,
    )


class TestStoryLevels:
    def test_reads_story_definitions(self):
        assert story_levels(_model()) == [("Base", 0.0), ("L2", 3.0), ("Roof", 6.0)]

    def test_empty_without_stories(self):
        model = _model()
        model.stories = []
        assert story_levels(model) == []
        assert story_levels(None) == []


class TestFrameLabels:
    def test_section(self):
        assert frame_labels(_model(), "section") == {"F1": "COL", "F2": "COL"}

    def test_material_walks_section(self):
        assert frame_labels(_model(), "material") == {"F1": "Steel", "F2": "Steel"}

    def test_story_uses_mid_height(self):
        assert frame_labels(_model(), "story") == {"F1": "Base", "F2": "L2"}

    def test_inactive_parents_are_skipped(self):
        labels = frame_labels(_model(), "section")
        assert "F3" not in labels

    def test_unassigned_element_is_unlabelled(self):
        model = _model()
        model.frame_assignments.pop("F1")
        assert frame_labels(model, "section")["F1"] == ""


class TestShellLabels:
    def test_section(self):
        assert shell_labels(_model(), "section") == {"A1": "SLAB"}

    def test_material(self):
        assert shell_labels(_model(), "material") == {"A1": "Concrete"}

    def test_story_uses_centroid(self):
        assert shell_labels(_model(), "story") == {"A1": "L2"}


class TestColourByField:
    def test_colours_share_palette_per_label(self):
        frame_colors, shell_colors = colour_by_field(_model(), "material")
        assert frame_colors["F1"] == frame_colors["F2"]  # both Steel
        assert shell_colors["A1"] != frame_colors["F1"]  # Concrete vs Steel

    def test_unresolved_elements_are_dropped(self):
        model = _model()
        model.frame_assignments.pop("F1")
        frame_colors, _ = colour_by_field(model, "section")
        assert "F1" not in frame_colors
        assert "F2" in frame_colors

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError):
            colour_by_field(_model(), "bogus")


class TestMetadata:
    def test_colour_fields_cover_labels(self):
        assert set(COLOUR_FIELDS) == set(FIELD_LABELS)
        assert FIELD_LABELS["section"] == "Section"
        assert FIELD_LABELS["material"] == "Material"
        assert FIELD_LABELS["story"] == "Story"
