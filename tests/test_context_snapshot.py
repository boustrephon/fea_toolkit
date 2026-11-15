"""Tests for the Qt-free context-snapshot formatter."""

from fea_toolkit.gui.controllers.context_snapshot import build_context_snapshot


def test_empty_snapshot_has_sentinels():
    text = build_context_snapshot()
    assert "fea_toolkit model context" in text
    assert "Source: (none)" in text
    assert "Units: (unknown)" in text
    assert "Selection: (none)" in text


def test_snapshot_renders_counts_and_views():
    text = build_context_snapshot(
        source="model.s2k",
        units="N · m · C",
        counts={"nodes": 12, "frames": 8},
        selection="type=Frame",
        views=[("Unprocessed", "geometry"), ("Case DEAD", "results")],
    )
    assert "Source: model.s2k" in text
    assert "Units: N · m · C" in text
    assert "nodes: 12, frames: 8" in text
    assert "Selection: type=Frame" in text
    assert "- Unprocessed (geometry)" in text
    assert "- Case DEAD (results)" in text


def test_snapshot_embeds_the_recipe_when_present():
    text = build_context_snapshot(recipe_json='{"name": "", "steps": []}')
    assert "Recipe:" in text
    assert '{"name": "", "steps": []}' in text

    text = build_context_snapshot(recipe_json="")
    assert "Recipe:" not in text
