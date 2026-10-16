"""Applied-load glyph extraction for rendering (OpenSees-free, Qt-free).

Turns a model's load collections into a flat list of display glyphs:

* joint loads → ``point`` glyphs (arrows),
* frame distributed loads → ``line`` glyphs — a rectangular flag for a
  uniform load and a trapezoidal flag otherwise, read straight from ``shape``
  / ``val_a`` / ``val_b`` / ``rdist_*``, i.e. **before** the AnalysisBuilder
  decomposes non-uniform loads into uniform segments,
* frame point loads → ``point`` glyphs anchored on the frame.

Area and gravity loads are designed for but not yet produced: ``kind``
reserves ``"area"`` and ``"gravity"``, and the docstrings document the
intended representation so the follow-up only adds extraction.

Magnitudes are read **in model units** and scaled only for display by the
renderer — no physical-constant hardcoding; gravity is implied through
``g_from_units(units)`` when it lands.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .geometry_core import get_local_axes
from .load_combinations import combination_pattern_factors, merge_combination_sets
from .sap_data import patterns_from_case

# ── Global direction vocabulary (shared with opensees/_loads.py) ──────────
# Kept local so the model layer never imports OpenSees.
_DIR = {
    "Gravity": np.array([0.0, 0.0, -1.0]),
    "X": np.array([1.0, 0.0, 0.0]),
    "Y": np.array([0.0, 1.0, 0.0]),
    "Z": np.array([0.0, 0.0, 1.0]),
}

# Joint-load force components → global direction, in SAP2000 axis order.
# Moment components (mx/my/mz) are deliberately absent: a moment needs a curl
# glyph, not a straight arrow, and is deferred to the area/gravity follow-up.
_FORCE_COMPONENTS = (
    ("fx", np.array([1.0, 0.0, 0.0])),
    ("fy", np.array([0.0, 1.0, 0.0])),
    ("fz", np.array([0.0, 0.0, 1.0])),
)


@dataclass
class LoadGeom:
    """One applied-load glyph, resolved and ready for a render backend.

    * ``kind == "point"`` — an arrow at :attr:`anchor` pointing along
      :attr:`direction`, sized by :attr:`magnitude`.
    * ``kind == "line"`` — a flag along :attr:`start` → :attr:`end` covering
      the relative span ``[rdist_a, rdist_b]``, extruded along
      :attr:`direction` by the intensity at each end (:attr:`val_a` /
      :attr:`val_b`).  Equal intensities give a rectangle, unequal a trapezoid.

    ``"area"`` and ``"gravity"`` are reserved for the follow-up and carry no
    geometry in this release.
    """

    kind: str  # 'point' | 'line' | 'area' | 'gravity'
    pattern: str
    label: str
    # ── point glyph ──
    anchor: Optional[np.ndarray] = None  # (3,)
    direction: Optional[np.ndarray] = None  # unit vector (3,)
    magnitude: float = 0.0
    # ── line glyph ──
    start: Optional[np.ndarray] = None  # (3,) I-end
    end: Optional[np.ndarray] = None  # (3,) J-end
    val_a: float = 0.0
    val_b: float = 0.0
    rdist_a: float = 0.0
    rdist_b: float = 1.0
    load_type: str = "Force"


# ── Geometry helpers ──────────────────────────────────────────────────────


def _node_pos(nodes, node_id) -> Optional[np.ndarray]:
    nd = (nodes or {}).get(str(node_id))
    if nd is None:
        return None
    return np.array([nd.x, nd.y, nd.z], dtype=float)


def _frame_geometry(frames, nodes, frame_id):
    """``(start, end, angle)`` for a frame, or ``None`` if unresolvable."""
    elem = (frames or {}).get(str(frame_id))
    if elem is None:
        return None
    ni = (nodes or {}).get(getattr(elem, "node_i", ""))
    nj = (nodes or {}).get(getattr(elem, "node_j", ""))
    if ni is None or nj is None:
        return None
    start = np.array([ni.x, ni.y, ni.z], dtype=float)
    end = np.array([nj.x, nj.y, nj.z], dtype=float)
    return start, end, float(getattr(elem, "angle", 0.0) or 0.0)


def _load_direction(direction, start, end, angle) -> np.ndarray:
    """Unit vector for a load direction, resolving local axes when needed."""
    key = str(direction or "").strip()
    if key in _DIR:
        return _DIR[key].copy()
    if key in ("LocalX", "LocalY", "LocalZ"):
        axis = end - start
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            return np.array([0.0, 0.0, -1.0])
        try:
            vx, vy, vz = get_local_axes(axis / norm, angle)
        except Exception:
            return np.array([0.0, 0.0, -1.0])
        return {"LocalX": vx, "LocalY": vy, "LocalZ": vz}[key].copy()
    # Unknown direction — fall back to gravity (mirrors opensees/_loads.py).
    return np.array([0.0, 0.0, -1.0])


# ── Load-source listing ───────────────────────────────────────────────────

#: Static case-type spellings (mirrors ``analysis/case_listing._STATIC_CASE_TYPES``,
#: kept local so the model layer stays analysis-free).
_STATIC_CASE_TYPES = ("linstatic", "static")


@dataclass(frozen=True)
class LoadSource:
    """One selectable source of applied loads for display.

    A ``"pattern"`` is the loads authored under that pattern name (factor 1.0);
    a ``"case"`` is a static load case's ``{pattern: factor}`` assignments; a
    ``"combination"`` is a linear combination resolved to the same
    ``{pattern: factor}`` shape.
    """

    name: str
    kind: str  # 'pattern' | 'case' | 'combination'
    patterns: dict[str, float]


def _pattern_names(source) -> list[str]:
    """Pattern names that appear on any load collection, plus defined patterns."""
    names: list[str] = []
    seen: set[str] = set()
    collections = (
        "frame_dist_loads",
        "joint_loads",
        "frame_point_loads",
        "area_uniform_loads",
        "frame_gravity_loads",
        "area_gravity_loads",
    )
    for coll in collections:
        for ld in getattr(source, coll, None) or []:
            pattern = getattr(ld, "pattern", "") or ""
            if pattern and pattern not in seen:
                seen.add(pattern)
                names.append(pattern)
    for name in getattr(source, "load_patterns", None) or {}:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def list_load_sources(source, external_combinations=None) -> list[LoadSource]:
    """List the selectable applied-load sources — patterns, cases, combinations.

    Args:
        source: An ``SAPModelData`` or ``MeshModel``.
        external_combinations: Optional extra ``{name: LoadCombination}`` (or
            canonical definition dict) layered over the model's own
            combinations — the external combination set the GUI edits.  A later
            definition wins (see :func:`merge_combination_sets`).

    Returns:
        One :class:`LoadSource` per pattern, static case and linear
        combination, in that order.  Combinations that do not reduce to a
        single signed load set (envelope / SRSS / magnitude fork) are omitted,
        because they have no single applied-load picture.

    Examples:
        >>> sources = list_load_sources(md)
        >>> sources[0].name  # the first pattern (or case) in model order
    """
    load_cases = getattr(source, "load_cases", None) or {}
    combos = merge_combination_sets(
        getattr(source, "load_combinations", None),
        external_combinations,
        load_cases=load_cases,
    )

    sources: list[LoadSource] = []
    for name in _pattern_names(source):
        sources.append(LoadSource(name=name, kind="pattern", patterns={name: 1.0}))
    for cname, lc in load_cases.items():
        if str(getattr(lc, "case_type", "") or "").lower() not in _STATIC_CASE_TYPES:
            continue
        pats = patterns_from_case(lc)
        if pats:
            sources.append(LoadSource(name=cname, kind="case", patterns=dict(pats)))
    for cname, combo in combos.items():
        factors = combination_pattern_factors(combo, load_cases, combos)
        if factors:
            sources.append(LoadSource(name=cname, kind="combination", patterns=factors))
    return sources


# ── Extraction ────────────────────────────────────────────────────────────


def extract_load_glyphs(
    source, pattern_factors: Optional[dict[str, float]] = None
) -> list[LoadGeom]:
    """Resolve a model's applied loads into display glyphs.

    Accepts an :class:`~fea_toolkit.model.sap_data.SAPModelData` or a
    :class:`~fea_toolkit.model.mesh_model.MeshModel` — both expose the same
    load collections.  Joint loads and frame point loads become ``point``
    arrows; frame distributed loads become ``line`` flags preserving their
    original shape (uniform → rectangle, linear/trapezoidal → trapezoid),
    never the AnalysisBuilder's uniform decomposition.

    Args:
        source: A model exposing ``nodes``, ``frame_elements``,
            ``joint_loads``, ``frame_dist_loads`` and ``frame_point_loads``.
        pattern_factors: Optional ``{pattern: factor}`` narrowing and scaling
            the loads — only loads whose pattern is a key are kept, and their
            magnitudes are multiplied by the factor (e.g. a
            :class:`LoadSource`'s ``patterns``).  ``None`` (the default)
            extracts every load at factor 1.0.

    Returns:
        A list of :class:`LoadGeom` in insertion order.

    Examples:
        >>> glyphs = extract_load_glyphs(mesh_model)
        >>> [g.kind for g in glyphs]
        ['point', 'line']
    """
    glyphs: list[LoadGeom] = []
    nodes = getattr(source, "nodes", None) or {}
    frames = getattr(source, "frame_elements", None) or {}
    factors = dict(pattern_factors) if pattern_factors is not None else None

    # Joint (point) loads → one arrow per non-zero force component.
    for jl in getattr(source, "joint_loads", None) or []:
        pattern = getattr(jl, "pattern", "")
        if factors is not None and pattern not in factors:
            continue
        scale = factors[pattern] if factors is not None else 1.0
        anchor = _node_pos(nodes, getattr(jl, "node_id", ""))
        if anchor is None:
            continue
        for comp, vec in _FORCE_COMPONENTS:
            mag = float(getattr(jl, comp, 0.0) or 0.0) * scale
            if mag == 0.0:
                continue
            glyphs.append(
                LoadGeom(
                    kind="point",
                    pattern=pattern,
                    label=f"{pattern} {comp}",
                    anchor=anchor,
                    direction=vec,
                    magnitude=mag,
                )
            )

    # Frame distributed loads → line flags (original shape preserved).
    for dl in getattr(source, "frame_dist_loads", None) or []:
        pattern = getattr(dl, "pattern", "")
        if factors is not None and pattern not in factors:
            continue
        scale = factors[pattern] if factors is not None else 1.0
        geom = _frame_geometry(frames, nodes, getattr(dl, "frame_id", ""))
        if geom is None:
            continue
        start, end, angle = geom
        direction = _load_direction(getattr(dl, "direction", "Gravity"), start, end, angle)
        glyphs.append(
            LoadGeom(
                kind="line",
                pattern=pattern,
                label=f"{pattern} {getattr(dl, 'shape', '')}",
                start=start,
                end=end,
                direction=direction,
                val_a=float(getattr(dl, "val_a", 0.0) or 0.0) * scale,
                val_b=float(getattr(dl, "val_b", 0.0) or 0.0) * scale,
                rdist_a=float(getattr(dl, "rdist_a", 0.0) or 0.0),
                rdist_b=float(getattr(dl, "rdist_b", 1.0) or 1.0),
                load_type=getattr(dl, "load_type", "Force"),
            )
        )

    # Frame point loads → point glyphs on the frame.
    for pl in getattr(source, "frame_point_loads", None) or []:
        pattern = getattr(pl, "pattern", "")
        if factors is not None and pattern not in factors:
            continue
        scale = factors[pattern] if factors is not None else 1.0
        geom = _frame_geometry(frames, nodes, getattr(pl, "frame_id", ""))
        if geom is None:
            continue
        start, end, angle = geom
        rdist = max(0.0, min(1.0, float(getattr(pl, "rdist", 0.0) or 0.0)))
        anchor = start + (end - start) * rdist
        direction = _load_direction(getattr(pl, "direction", "Gravity"), start, end, angle)
        glyphs.append(
            LoadGeom(
                kind="point",
                pattern=pattern,
                label=f"{pattern} {getattr(pl, 'load_type', '')}",
                anchor=anchor,
                direction=direction,
                magnitude=float(getattr(pl, "value", 0.0) or 0.0) * scale,
            )
        )

    return glyphs
