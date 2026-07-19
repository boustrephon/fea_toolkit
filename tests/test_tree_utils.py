"""Tests for frame-element tree-traversal utilities."""

import pytest
from fea_toolkit.model.sap_data import FrameElement
from fea_toolkit.model.tree_utils import (
    collect_descendants,
    get_root_parent,
    get_element_chain,
    frame_split_summary,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def simple_split():
    """A single frame split into two children."""
    return {
        "3": FrameElement(
            elem_id="3", elem_tag=3, node_i="1", node_j="2",
            inactive=True, child_ids=["3-0", "3-1"],
        ),
        "3-0": FrameElement(
            elem_id="3-0", elem_tag=4, node_i="1", node_j="5",
            inactive=False, parent_id="3",
        ),
        "3-1": FrameElement(
            elem_id="3-1", elem_tag=5, node_i="5", node_j="2",
            inactive=False, parent_id="3",
        ),
    }


@pytest.fixture
def nested_split():
    """A frame split twice — root → child → grandchild."""
    return {
        "3": FrameElement(
            elem_id="3", elem_tag=3, node_i="1", node_j="2",
            inactive=True, child_ids=["3-0", "3-1"],
        ),
        "3-0": FrameElement(
            elem_id="3-0", elem_tag=4, node_i="1", node_j="6",
            inactive=True, parent_id="3", child_ids=["3-0-0", "3-0-1"],
        ),
        "3-0-0": FrameElement(
            elem_id="3-0-0", elem_tag=5, node_i="1", node_j="7",
            inactive=False, parent_id="3-0",
        ),
        "3-0-1": FrameElement(
            elem_id="3-0-1", elem_tag=6, node_i="7", node_j="6",
            inactive=False, parent_id="3-0",
        ),
        "3-1": FrameElement(
            elem_id="3-1", elem_tag=7, node_i="6", node_j="2",
            inactive=False, parent_id="3",
        ),
    }


@pytest.fixture
def no_split():
    """A single frame that was never split."""
    return {
        "1": FrameElement(
            elem_id="1", elem_tag=1, node_i="1", node_j="2",
            inactive=False,
        ),
    }


# ═══════════════════════════════════════════════════════════════════
# Tests: collect_descendants
# ═══════════════════════════════════════════════════════════════════


class TestCollectDescendants:
    def test_simple_split(self, simple_split):
        leaves = collect_descendants("3", simple_split)
        assert sorted(leaves) == ["3-0", "3-1"]

    def test_nested_split(self, nested_split):
        leaves = collect_descendants("3", nested_split)
        assert sorted(leaves) == ["3-0-0", "3-0-1", "3-1"]

    def test_partial_subtree(self, nested_split):
        leaves = collect_descendants("3-0", nested_split)
        assert sorted(leaves) == ["3-0-0", "3-0-1"]

    def test_leaf_element(self, nested_split):
        leaves = collect_descendants("3-0-0", nested_split)
        assert leaves == ["3-0-0"]

    def test_no_split(self, no_split):
        leaves = collect_descendants("1", no_split)
        assert leaves == ["1"]

    def test_missing_element(self, simple_split):
        leaves = collect_descendants("nonexistent", simple_split)
        assert leaves == []

    def test_cache_reduces_calls(self, nested_split):
        cache = {}
        r1 = collect_descendants("3", nested_split, cache)
        r2 = collect_descendants("3", nested_split, cache)
        assert r1 == r2
        assert "3-0" in cache  # intermediate results cached


# ═══════════════════════════════════════════════════════════════════
# Tests: get_root_parent
# ═══════════════════════════════════════════════════════════════════


class TestGetRootParent:
    def test_leaf_finds_root(self, nested_split):
        assert get_root_parent("3-0-0", nested_split) == "3"

    def test_intermediate_finds_root(self, nested_split):
        assert get_root_parent("3-0", nested_split) == "3"

    def test_root_returns_self(self, nested_split):
        assert get_root_parent("3", nested_split) == "3"

    def test_no_split(self, no_split):
        assert get_root_parent("1", no_split) == "1"

    def test_missing_element(self, simple_split):
        assert get_root_parent("nonexistent", simple_split) == "nonexistent"


# ═══════════════════════════════════════════════════════════════════
# Tests: get_element_chain
# ═══════════════════════════════════════════════════════════════════


class TestGetElementChain:
    def test_leaf_chain(self, nested_split):
        chain = get_element_chain("3-0-0", nested_split)
        assert chain == ["3", "3-0", "3-0-0"]

    def test_root_chain(self, nested_split):
        chain = get_element_chain("3", nested_split)
        assert chain == ["3"]

    def test_no_split(self, no_split):
        chain = get_element_chain("1", no_split)
        assert chain == ["1"]


# ═══════════════════════════════════════════════════════════════════
# Tests: frame_split_summary
# ═══════════════════════════════════════════════════════════════════


class TestFrameSplitSummary:
    def test_simple_summary(self, simple_split):
        summary = frame_split_summary(simple_split)
        roots = {s["root_id"] for s in summary}
        assert "3" in roots

    def test_root_leaf_counts(self, nested_split):
        summary = frame_split_summary(nested_split)
        for s in summary:
            if s["root_id"] == "3":
                assert s["leaf_count"] == 3  # 3-0-0, 3-0-1, 3-1
            if s["root_id"] == "3-0":
                assert s["leaf_count"] == 2  # 3-0-0, 3-0-1

    def test_no_split(self, no_split):
        summary = frame_split_summary(no_split)
        assert len(summary) == 1
        assert summary[0]["root_id"] == "1"
        assert summary[0]["leaf_count"] == 1
