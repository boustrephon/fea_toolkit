"""
Utility functions for configuration merging and load-pattern inference.

These are used primarily by ``run_all()`` to auto-detect load patterns
from raw SAP2000 table data and merge user config with defaults.
"""

from typing import Dict, Optional

# Gravitational acceleration in m/s²  (SI default)
_G_SI = 9.80665


def g_from_units(units: dict) -> float:
    """Return gravitational acceleration matching the model length unit.

    SAP2000 analysis always assumes time in seconds.  This function
    scales g from the SI value (9.80665 m/s²) to the model's length
    unit.  Falls back to 9.81 if the length unit is unrecognised.

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'KN', 'T': 'C'}``.

    Returns:
        Gravitational acceleration in the model's length-unit / s².
    """
    lu = (units or {}).get('L', 'm')
    # Normalise aliases before scaling
    _alias = {
        'meter': 'm', 'meters': 'm', 'metre': 'm', 'metres': 'm',
        'centimeter': 'cm', 'centimeters': 'cm', 'centimetre': 'cm',
        'millimeter': 'mm', 'millimeters': 'mm', 'millimetre': 'mm',
        'foot': 'ft', 'feet': 'ft',
        'inch': 'in', 'inches': 'in',
    }
    lu = _alias.get(lu.lower(), lu)
    # Scale factor relative to 1 m
    scale = {
        'm': 1.0,
        'cm': 100.0,
        'mm': 1000.0,
        'ft': 3.28084,
        'in': 39.3701,
    }.get(lu, 1.0)
    return _G_SI * scale


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


# ── Flag diagram geometry (pure NumPy, no renderer dependency) ────────

def compute_flag_parts(pt1, pt2, vn, Fi, Fj, scale):
    """Yield ``(vertices, col_val)`` for each part of a flag diagram element.

    Parameters
    ----------
    pt1, pt2 : array-like of length 3
        I-end and J-end node coordinates.
    vn : array-like of length 3
        Unit vector for positive flag offset direction.
    Fi, Fj : float
        Force/moment values (original, un-negated).
    scale : float
        Scale factor (display units per force/moment unit).

    Yields
    ------
    vertices : list of ndarray
        Corner points in perimeter order (4 for a quad, 3 for a triangle).
    col_val : float
        Signed value for colour mapping (positive → red, negative → blue).
    """
    import numpy as np

    pt1 = np.asarray(pt1, dtype=float)
    pt2 = np.asarray(pt2, dtype=float)
    vn = np.asarray(vn, dtype=float)

    if abs(Fi) < 1e-12 and abs(Fj) < 1e-12:
        return

    off_i = vn * Fi * scale       # I-end: +vn for positive Fi
    off_j = -vn * Fj * scale      # J-end: -vn for positive Fj (baked-in negation)

    if Fi * Fj < 0.0:
        # Trapezoid: [pt1, pt2, pt2+off_j, pt1+off_i]
        col_val = Fi if abs(Fi) >= abs(Fj) else Fj
        yield [pt1, pt2, pt2 + off_j, pt1 + off_i], col_val
    else:
        # Zero-crossing: split at vcp = vx · Fi / (Fi + Fj)
        if abs(Fi + Fj) < 1e-15:
            return
        ratio = Fi / (Fi + Fj)
        p_zero = pt1 + (pt2 - pt1) * ratio
        if abs(Fi) > 1e-12:
            yield [pt1, p_zero, pt1 + off_i], Fi
        if abs(Fj) > 1e-12:
            yield [p_zero, pt2, pt2 + off_j], Fj
