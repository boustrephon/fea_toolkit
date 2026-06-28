"""Tests for fea_toolkit.spectrum and fea_toolkit.utils modules."""
import sys
import math
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import numpy as np
from fea_toolkit.spectrum import _gb50011_spectrum, _build_spectrum, _interp_sa
from fea_toolkit.utils import deep_merge, infer_loads, build_gravity_patterns, pick_wind


# ── Spectrum tests ─────────────────────────────────────────────────────

def test_gb50011_spectrum_zero_period():
    """At T=0, the spectrum should return 0.45 × α_max × g."""
    Sa = _gb50011_spectrum([0.0], alpha_max=0.5, tg=0.35)
    expected = 0.45 * 0.5 * 9.81
    assert abs(Sa[0] - expected) < 1e-10, f"{Sa[0]} != {expected}"


def test_gb50011_spectrum_plateau():
    """At T=tg, the spectrum should return η₂ × α_max × g."""
    Sa = _gb50011_spectrum([0.35], alpha_max=0.5, tg=0.35, eta2=1.0)
    expected = 1.0 * 0.5 * 9.81
    assert abs(Sa[0] - expected) < 1e-10, f"{Sa[0]} != {expected}"


def test_gb50011_spectrum_descending():
    """At T=5*tg, the spectrum should be on the descending branch."""
    Sa = _gb50011_spectrum([1.75], alpha_max=0.5, tg=0.35)
    # Should be less than plateau value
    plateau = 1.0 * 0.5 * 9.81
    assert Sa[0] < plateau, f"{Sa[0]} not less than plateau {plateau}"


def test_build_spectrum_defaults():
    """_build_spectrum with minimal config should return a reasonable spectrum."""
    cfg = {"intensity": 7, "acceleration": 0.10, "site_class": "I1",
           "level": "rare", "damping": 0.05}
    T, Sa, amax, tg, zeta, label = _build_spectrum(cfg)
    assert len(T) == 300
    assert len(Sa) == 300
    assert amax == 0.50  # rare for VII
    assert tg == 0.25    # I1
    assert zeta == 0.05
    assert "Rare" in label


def test_build_spectrum_frequent():
    """Frequent level should use the frequent alpha_max."""
    cfg = {"intensity": 8, "acceleration": 0.20, "site_class": "II",
           "level": "frequent", "damping": 0.03}
    _, _, amax, _, _, label = _build_spectrum(cfg)
    assert amax == 0.16  # frequent for VIII
    assert "Frequent" in label


def test_interp_sa():
    """Interpolation should return known values at input points."""
    T = [0.0, 0.5, 1.0]
    Sa = [0.0, 1.0, 2.0]
    result = _interp_sa([0.25, 0.75], T, Sa)
    expected = np.interp([0.25, 0.75], T, Sa)
    np.testing.assert_array_almost_equal(result, expected)


# ── Utils tests ────────────────────────────────────────────────────────

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
