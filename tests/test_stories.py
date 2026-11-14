"""Storey detection (``model/stories.py``) — the multi-tower ETABS warning."""

import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.model.stories import identify_stories


def test_multi_tower_story_table_warns_on_duplicate_names():
    """Duplicate story names are the multi-tower signature — warn, don't merge silently."""
    raw_tables = {
        "STORY DATA": [
            {"Name": "Story1", "Z": "0.0"},
            {"Name": "Story1", "Z": "3.0"},
        ]
    }
    with pytest.warns(UserWarning, match="multi-tower"):
        identify_stories(make_sample_model(), raw_tables)


def test_duplicate_elevations_also_warn():
    """Coincident levels from different towers (distinct names, same Z) also warn."""
    raw_tables = {
        "STORY DATA": [
            {"Name": "TowerA_1", "Z": "0.0"},
            {"Name": "TowerB_1", "Z": "0.0"},
        ]
    }
    with pytest.warns(UserWarning, match="multi-tower"):
        identify_stories(make_sample_model(), raw_tables)


def test_a_unique_story_table_does_not_warn():
    """A normal single-tower story table passes through silently."""
    import warnings

    raw_tables = {
        "STORY DATA": [
            {"Name": "Base", "Z": "0.0"},
            {"Name": "Roof", "Z": "3.0"},
        ]
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning fails the test
        stories = identify_stories(make_sample_model(), raw_tables)
    assert [s.name for s in stories] == ["Base", "Roof"]
