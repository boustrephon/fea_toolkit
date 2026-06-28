"""
Utility functions for configuration merging and load-pattern inference.

These are used primarily by ``run_all()`` to auto-detect load patterns
from raw SAP2000 table data and merge user config with defaults.
"""

from typing import Dict, Optional


def deep_merge(base: dict, override: dict) -> dict:
    """Merge *override* into *base*.

    *   Scalar values in *override* replace *base*.
    *   Dicts are merged recursively.
    *   ``None`` in *override* removes the key from *base* (opt-out).
    """
    result = dict(base)
    for k, v in override.items():
        if v is None:
            result.pop(k, None)
        elif k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def infer_loads(raw_tables: dict) -> dict:
    """Auto-detect load patterns by DesignType from the raw s2k tables.

    Returns ``{"dead": [names], "live": [names], "wind": [names],
    "quake": [names]}``.
    """
    result = {"dead": [], "live": [], "wind": [], "quake": []}
    for tname, records in raw_tables.items():
        if "LOAD PATTERN DEFINITIONS" not in tname:
            continue
        for rec in records:
            name = rec.get("LoadPat", "")
            dtype = rec.get("DesignType", "")
            if dtype == "Dead":
                result["dead"].append(name)
            elif dtype == "Live":
                result["live"].append(name)
            elif dtype == "Wind":
                result["wind"].append(name)
            elif dtype == "Quake":
                result["quake"].append(name)
    return result


def build_gravity_patterns(inferred: dict) -> dict:
    """Build the gravity load combination dict from auto-detected loads.

    Uses 1.0 for Dead, 0.5 for Live (GB 50011 seismic combos).
    """
    patterns = {}
    for name in inferred.get("dead", []):
        patterns[name] = 1.0
    for name in inferred.get("live", []):
        patterns[name] = 0.5
    return patterns


def pick_wind(inferred: dict, direction: str) -> dict:
    """Pick the wind pattern matching *direction* (e.g. '+X')."""
    sign, axis = direction[0], direction[1]
    for name in inferred.get("wind", []):
        if axis in name and sign in name:
            return {name: 1.0}
    # Fallback: first wind pattern with the right axis
    for name in inferred.get("wind", []):
        if axis in name:
            return {name: 1.0}
    return {}


# ── Legacy aliases with underscore prefixes (for backward compat) ──────

def _deep_merge(base: dict, override: dict) -> dict:
    return deep_merge(base, override)


def _infer_loads(raw_tables: dict) -> dict:
    return infer_loads(raw_tables)


def _build_gravity_patterns(inferred: dict) -> dict:
    return build_gravity_patterns(inferred)


def _pick_wind(inferred: dict, direction: str) -> dict:
    return pick_wind(inferred, direction)
