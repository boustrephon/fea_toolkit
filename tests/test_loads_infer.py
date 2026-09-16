"""Tests for fea_toolkit._loads_infer — load-inference helpers.

Covers :func:`deep_merge`, :func:`infer_loads`,
:func:`build_gravity_patterns` and :func:`pick_wind` (re-exported through
``fea_toolkit.utils``).
"""

from fea_toolkit.utils import (
    build_gravity_patterns,
    deep_merge,
    infer_loads,
    pick_wind,
)


def test_deep_merge_scalar():
    """Scalar overrides should replace base values."""
    base = {"a": 1, "b": 2}
    result = deep_merge(base, {"b": 3})
    assert result == {"a": 1, "b": 3}


def test_deep_merge_nested():
    """Nested dicts should be merged recursively."""
    base = {"a": {"x": 1, "y": 2}}
    result = deep_merge(base, {"a": {"y": 99}})
    assert result == {"a": {"x": 1, "y": 99}}


def test_deep_merge_none_removes():
    """A None value should remove the key."""
    base = {"a": 1, "b": 2}
    result = deep_merge(base, {"a": None})
    assert "a" not in result
    assert result == {"b": 2}


def test_infer_loads_empty():
    """Empty raw_tables should return empty categories."""
    result = infer_loads({})
    assert result == {"dead": [], "live": [], "wind": [], "quake": []}


def test_infer_loads_basic():
    """Raw tables with LOAD PATTERN DEFINITIONS should be parsed."""
    raw = {
        "LOAD PATTERN DEFINITIONS": [
            {"LoadPat": "DEAD", "DesignType": "Dead"},
            {"LoadPat": "LL", "DesignType": "Live"},
            {"LoadPat": "WINDX", "DesignType": "Wind"},
            {"LoadPat": "QX", "DesignType": "Quake"},
        ]
    }
    result = infer_loads(raw)
    assert result["dead"] == ["DEAD"]
    assert result["live"] == ["LL"]
    assert result["wind"] == ["WINDX"]
    assert result["quake"] == ["QX"]


def test_build_gravity_patterns():
    """Dead loads get 1.0, Live loads get 0.5."""
    inferred = {"dead": ["DEAD", "SDL"], "live": ["LL"], "wind": [], "quake": []}
    result = build_gravity_patterns(inferred)
    assert result == {"DEAD": 1.0, "SDL": 1.0, "LL": 0.5}


def test_pick_wind():
    """Should match axis and sign in wind pattern names."""
    inferred = {"wind": ["Wind +X", "Wind -X", "Wind +Y"], "dead": [], "live": [], "quake": []}
    result = pick_wind(inferred, "+X")
    assert result == {"Wind +X": 1.0}
    result2 = pick_wind(inferred, "-X")
    assert result2 == {"Wind -X": 1.0}
    result3 = pick_wind(inferred, "+Y")
    assert result3 == {"Wind +Y": 1.0}
