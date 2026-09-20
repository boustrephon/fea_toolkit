"""
Unified force/moment diagram plotting.

The single unit-aware entry point :func:`plot_force_diagram` replaces the
four legacy force-diagram functions (``plot_force_diagram_3d``,
``plot_rs_force_diagram``, ``plot_npz_force_diagram``,
``plot_npz_moment_3d``), which were thin wrappers over it and were
removed in the deprecation cleanup (2026-08-24).

Inputs
------
The dispatcher accepts any input the toolkit already supports:

* an ``AnalysisBuilder`` (or ``MeshModel`` / ``SAPModelData``) together with
  a static force dict from ``extract_static_element_forces()``,
* an ``AnalysisBuilder`` with an RS results dict (``element_results``) or
  the per-element list,
* an RS ``element_results`` list or the full ``extract_element_rs_forces()``
  dict,
* an NPZ path (``.npz`` or ``.h5``) or a raw NPZ data dict.

NPZ inputs must contain the canonical static frame-force arrays
(``static/{case}/fx_i`` … ``mz_j``) — component-keyed arrays, one per force
component indexed by frame order.  The element-keyed dict returned by
``extract_static_element_forces()`` must be transposed into that form before
export; see ``docs/force_diagram_unification.md``.

Units are resolved from the source in this order: explicit ``force_unit`` /
``length_unit`` arguments, builder/model units, an in-memory ``"units"``
key, then NPZ ``force_unit`` / ``length_unit`` metadata.  Bare RS lists
carry no unit metadata, so they retain the legacy ``"kN"`` / ``"m"`` labels.

Rendering dimensions — 2D vs 3D
-------------------------------
``dimension`` picks between two deliberately different views of the same
resolved series, and is independent of ``kind`` (static vs response
spectrum): the *same* ``quantity`` and ``combo`` render in either dimension —
only the encoding changes.

**2D** (Matplotlib) is a *chart*, not a picture of the structure.  The
horizontal axis is the force/moment value and the vertical axis is elevation
``z``.  By default it draws the **storey profile** — one line through the value
summed at each distinct elevation over the elevation-changing members (see
:func:`_build_storey_series`) — so a result set reads as a single line rather
than one segment per element.  Pass ``by_storey=False`` for the legacy
per-element form, where each element is a segment from ``(v_i, z_i)`` to
``(v_j, z_j)``: a uniform axial force reads as a vertical line and a sign change
as a zero-axis crossing.  The RS path currently plots one point per element at
its mid-height (``z_mid``).  Matplotlib is a core dependency, so 2D is always
available.

**3D** (PyVista) is a *spatial model*: the real member centrelines at their
true 3D positions, with each member's quantity drawn as a ribbon offset
perpendicular to it (``mode="flag"``) or as a tube whose radius encodes the
value (``mode="tube"``).  A sign change flips the ribbon to the other side of
the member — the 3D counterpart of the 2D triangle.  The view is set
isometric.  PyVista is a core dependency but may be absent from an embedded
interpreter (e.g. Rhino 8), in which case only the 2D path is available.

The RS ``element_results`` records are a de-facto contract of the response-
spectrum extraction pipeline: each carries ``z_mid`` and per-end quantity
keys such as ``My_i`` / ``My_j`` (CQC-combined).  ``plot_force_diagram``
normalises both the ``'My_i'`` and ``'My'`` key styles internally.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

# ── Quantity key helpers ─────────────────────────────────────────────

_QTY_COMPONENTS = ("mx", "my", "mz", "fx", "fy", "fz")


def _normalise_quantity(quantity: str) -> Optional[str]:
    """Return the canonical quantity key (e.g. ``'My_i'`` -> ``'My'``).

    Strips a trailing ``_i`` suffix (the RS key style) and title-cases the
    component so ``'my_i'``, ``'My_i'`` and ``'My'`` all resolve to ``'My'``.

    Args:
        quantity: Quantity string to normalise.

    Returns:
        Canonical quantity key, or ``None`` if not a string.
    """
    if not isinstance(quantity, str) or not quantity:
        return None
    q = quantity.strip()
    q = q.removesuffix("_i")
    if q.lower()[:2] in _QTY_COMPONENTS:
        q = q[:2].title() + q[2:]
    return q


def _canonical_force_key(key: str) -> str:
    """Uppercase the base of a force key, preserving the suffix case.

    ``'My_j'`` -> ``'MY_j'``, ``'fx_i_local'`` -> ``'FX_i_local'``.  Already
    canonical keys pass through unchanged (idempotent).

    Args:
        key: Force key from a result dict.

    Returns:
        Canonical uppercase force key.
    """
    parts = key.split("_", 1)
    base = parts[0].upper()
    return base + ("_" + parts[1] if len(parts) > 1 else "")


# ── Canonical intermediate ───────────────────────────────────────────


@dataclass
class ForceDiagramData:
    """Canonical intermediate for force-diagram rendering.

    Any supported input is normalised to this shape by :func:`_resolve_source`.
    ``nodes`` / ``frames`` / ``force_map`` feed the 3D (PyVista) path;
    ``series`` feeds the 2D (matplotlib) paths.

    Attributes:
        kind: ``'static'`` or ``'rs'``.
        quantity: Canonical quantity key (e.g. ``'My'``).
        force_unit: Force-unit label for axes/legend text.
        length_unit: Length-unit label for axes/legend text.
        nodes: ``node_tag -> {tag, x, y, z}`` geometry lookup (3D).
        frames: Element connectivity in ``_resolve_mesh_data`` format (3D).
        force_map: ``frame_idx -> {FX..MZ, *_j, *_i_local...}`` (3D).
        series: Per-element records ``{z_i, z_j, z_mid, forces}`` (2D).
        storey_series: Per-level summed records ``{elevation, cx, cy, forces,
            n_ends}`` ascending by elevation — the storey profile the 2D path
            draws in preference to the per-element ``series``.
        storey_cm: Centre-of-magnitude method behind *storey_series* —
            ``"bbox"`` (bounding-box midpoint, default) or ``"mass"``.
        storey_mode: Level-summation rule behind *storey_series*.
        storey_extras: Other members of the same combination group, as
            ``(storey_series, label)`` pairs — the opposite spectrum fork for a
            single-sense fork, or the remaining corners of a multi-fork / the
            other extreme of an envelope pair, summed identically and drawn as
            additional curves so a two-sided result is read as all its signs.
            Empty when the case belongs to no group.
        storey_label: Legend text for *storey_series*.
    """

    kind: str
    quantity: str
    force_unit: str
    length_unit: str
    nodes: dict = field(default_factory=dict)
    frames: list = field(default_factory=list)
    force_map: dict = field(default_factory=dict)
    series: list = field(default_factory=list)
    storey_series: list = field(default_factory=list)
    storey_cm: str = "bbox"
    storey_mode: str = "cut"
    storey_extras: list = field(default_factory=list)
    storey_label: str = ""

    @property
    def empty(self) -> bool:
        """True when there is nothing to plot."""
        return not self.force_map and not self.series and not self.storey_series


# ── Series builders ──────────────────────────────────────────────────


def _build_series_from_rs(records: list, quantity: str) -> list:
    """Convert RS ``element_results`` records to canonical 2D series.

    Each record is mapped to ``{z_i, z_j, z_mid, forces}`` with canonical
    uppercase force keys (``'MY'`` / ``'MY_j'``).

    Args:
        records: Per-element RS result dicts (each with ``z_mid``).
        quantity: Canonical quantity key (e.g. ``'My'``).

    Returns:
        List of canonical series records.
    """
    q_upper = quantity.upper()
    series = []
    for r in records:
        v_i = r.get(f"{quantity}_i", r.get(quantity, 0.0))
        v_j = r.get(f"{quantity}_j", v_i)
        z_mid = r.get("z_mid", 0.0)
        series.append(
            {
                "z_i": z_mid,
                "z_j": z_mid,
                "z_mid": z_mid,
                "forces": {q_upper: v_i, f"{q_upper}_j": v_j},
            }
        )
    return series


def _node_z(nodes: dict, fr: dict, end: str) -> Optional[float]:
    """Return the z-coordinate of a frame endpoint from resolved geometry.

    Args:
        nodes: Node lookup from :func:`_resolve_mesh_data`.
        fr: Frame record (with ``ni_tag``/``nj_tag`` or ``ni_id``/``nj_id``).
        end: ``'i'`` or ``'j'``.

    Returns:
        The endpoint z-coordinate, or ``None`` if unresolved.
    """
    tag = fr.get(f"n{end}_tag")
    if tag is not None:
        nd = nodes.get(int(tag))
        if nd:
            return nd["z"]
    nid = fr.get(f"n{end}_id")
    if nid is not None:
        nd = nodes.get(str(nid)) or nodes.get(nid)
        if nd:
            return nd["z"]
    return None


def _build_series_from_force_map(force_map: dict, frames: list, nodes: dict) -> list:
    """Build the 2D series from a 3D-style force map + geometry.

    Args:
        force_map: ``frame_idx -> {FX..MZ, *_j, *_i_local...}``.
        frames: Element connectivity from :func:`_resolve_mesh_data`.
        nodes: Node lookup from :func:`_resolve_mesh_data`.

    Returns:
        List of canonical series records (``{z_i, z_j, z_mid, forces}``).
    """
    series = []
    for idx, fr in enumerate(frames):
        if idx not in force_map:
            continue
        z_i = _node_z(nodes, fr, "i")
        z_j = _node_z(nodes, fr, "j")
        if z_i is None or z_j is None:
            continue
        series.append(
            {
                "z_i": z_i,
                "z_j": z_j,
                "z_mid": (z_i + z_j) / 2.0,
                "forces": force_map[idx],
            }
        )
    return series


def _build_static_force_map(source, geometry: dict, force_data: dict) -> dict:
    """Map a builder static force dict onto resolved frame indices.

    Replicates the legacy ``plot_force_diagram_3d`` builder mapping: forces
    keyed by element tag are matched to resolved frames via their node pairs.

    Args:
        source: ``AnalysisBuilder`` (or builder-like object).
        geometry: Output of :func:`_resolve_mesh_data` for *source*.
        force_data: ``{elem_tag: {Fx..Mz, Fx_j..}}`` from
            ``extract_static_element_forces()``.

    Returns:
        ``{frame_idx: canonical_force_dict}``.
    """
    model = getattr(source, "model", None) or getattr(source, "mesh_model", None) or source
    # The resolved model always holds the post-split topology, so its
    # ``frame_elements`` is the single source of truth; the legacy
    # ``source.split_elements`` fallback no longer exists.
    elements = model.frame_elements

    # Match the keying used by ``extract_static_element_forces()``: the
    # generated OpenSees tag comes from ``frame_tag_map`` (falling back to the
    # element's own ``elem_tag``) so forces are not silently dropped when the
    # Preprocessor assigned deterministic tags.
    frame_tag_map = getattr(source, "frame_tag_map", {}) or {}

    elem_by_node_pair: dict[tuple[int, int], int] = {}
    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        eni = model.nodes.get(elem.node_i)
        enj = model.nodes.get(elem.node_j)
        if eni is None or enj is None:
            continue
        elem_by_node_pair[(eni.node_tag, enj.node_tag)] = frame_tag_map.get(eid, elem.elem_tag)

    force_map = {}
    for idx, fr in enumerate(geometry["frames"]):
        ni_tag = fr.get("ni_tag")
        nj_tag = fr.get("nj_tag")
        if ni_tag is None:
            nd_i = model.nodes.get(fr.get("ni_id"))
            nd_j = model.nodes.get(fr.get("nj_id"))
            if nd_i is None or nd_j is None:
                continue
            ni_tag, nj_tag = nd_i.node_tag, nd_j.node_tag
        target_tag = elem_by_node_pair.get((ni_tag, nj_tag))
        if target_tag is not None and target_tag in force_data:
            force_map[idx] = {_canonical_force_key(k): v for k, v in force_data[target_tag].items()}
    return force_map


def _build_rs_force_map(records: list, geometry: dict) -> dict:
    """Map RS ``element_results`` records onto resolved frame indices.

    RS records are keyed by SAP element id (``frame_sap_id`` in the archive
    geometry), not by node pair, so this matches on ``frame["id"]`` rather than
    the static path's node-pair lookup.

    The stored RS forces are already in the element **local** system, so each
    component is exposed under the ``*_i_local`` / ``*_j_local`` variant keys.
    That makes :func:`_compute_local_forces` take its verbatim fast path instead
    of rotating already-local values a second time.

    Args:
        records: Per-element RS records (see ``extract_element_rs_forces``).
        geometry: Output of ``_resolve_mesh_data`` (uses ``frames``).

    Returns:
        ``{frame_idx: {"FX_i_local": …, "MY_i_local": …, "MY_j_local": …}}``.
    """
    by_id = {str(r.get("elem_id")): r for r in records}
    force_map = {}
    for idx, fr in enumerate(geometry.get("frames", [])):
        record = by_id.get(str(fr.get("id")))
        if record is None:
            continue
        entry = {}
        for comp in ("Fx", "Fy", "Fz", "Mx", "My", "Mz"):
            v_i = record.get(f"{comp}_i")
            v_j = record.get(f"{comp}_j")
            if v_i is not None:
                entry[f"{comp.upper()}_i_local"] = float(v_i)
            if v_j is not None:
                entry[f"{comp.upper()}_j_local"] = float(v_j)
        if entry:
            force_map[idx] = entry
    return force_map


# ── Storey-level series (global-force summation) ─────────────────────


def _force_component(entry: dict, comp: str, end: str) -> float:
    """Read one force component for one member end.

    Prefers the stored element-local value (``FX_i_local``), falling back to
    the plain key (``FX`` for the I-end, ``FX_j`` for the J-end).

    Args:
        entry: A ``force_map`` entry.
        comp: Uppercase component key (``"FX"`` … ``"MZ"``).
        end: ``"i"`` or ``"j"``.

    Returns:
        The component value, or ``0.0`` when no key is present.
    """
    if end == "i":
        keys = (f"{comp}_i_local", comp, comp.lower())
    else:
        keys = (f"{comp}_j_local", f"{comp}_j", f"{comp.lower()}_j")
    for key in keys:
        if key in entry:
            return float(entry[key])
    return 0.0


def _member_global_forces(entry, node_i, node_j, angle):
    """Return one member's I- and J-end forces in **global** coordinates.

    The current NPZ schema stores element-local forces
    (``forces_coordinate_system == "local"``), so they are rotated with
    :func:`~fea_toolkit.model.geometry.get_local_axes` — the inverse of the
    global-to-local rotation :func:`_compute_local_forces` applies for the 3D
    flag path.

    Args:
        entry: ``force_map`` entry for one member.
        node_i, node_j: Endpoint dictionaries with ``x`` / ``y`` / ``z``.
        angle: SAP section rotation (degrees) about the local x-axis.

    Returns:
        ``(f_i, f_j)`` — two 6-component global ``[Fx, Fy, Fz, Mx, My, Mz]``
        lists — or ``None`` for a zero-length member.
    """
    import numpy as np

    from ..model.geometry import get_local_axes

    axis = np.array(
        [node_j["x"] - node_i["x"], node_j["y"] - node_i["y"], node_j["z"] - node_i["z"]],
        dtype=float,
    )
    if np.linalg.norm(axis) < 1e-12:
        return None
    vx, vy, vz = get_local_axes(axis, angle)
    rot = np.vstack([vx, vy, vz]).T  # local -> global (rows are local axes)

    def _vec(end: str):
        # Force and moment triads are rotated independently by the same 3x3
        # local-to-global matrix (the rotation acts on each 3-vector).
        f_loc = np.array([_force_component(entry, c, end) for c in ("FX", "FY", "FZ")], dtype=float)
        m_loc = np.array([_force_component(entry, c, end) for c in ("MX", "MY", "MZ")], dtype=float)
        return [*(rot @ f_loc).tolist(), *(rot @ m_loc).tolist()]

    return _vec("i"), _vec("j")


def _npz_frame_angles(source, n_frames: int) -> list:
    """Read per-frame SAP section angles (degrees) from an NPZ source.

    Returns zeros when the archive predates the ``frame_angle`` array, which
    matches the default basis the rest of the plotting layer assumes.
    """
    import numpy as np

    try:
        arr = source["frame_angle"]
    except (KeyError, TypeError, IndexError):
        return [0.0] * n_frames
    vals = [float(v) for v in np.asarray(arr).ravel()]
    if len(vals) < n_frames:
        vals = vals + [0.0] * (n_frames - len(vals))
    return vals[:n_frames]


def _build_storey_series(
    geometry, force_map, source=None, cm_method: str = "bbox", mode: str = "cut"
) -> list:
    """Sum a case's member forces by storey level into a 2D profile.

    Only **elevation-changing** members (columns/braces) contribute — a
    horizontal member's two ends share one elevation and cancel out of a level
    profile.  End forces are rotated from the archive's stored local system
    into **global** coordinates first, so the level sums are physically
    meaningful (storey shear, overturning, torsion).  Each level's moments
    include the force x lever-arm term about the level's reference point; see
    :func:`~fea_toolkit.model.storey_response.sum_storey_forces`.

    Args:
        geometry: Output of :func:`_resolve_mesh_data`.
        force_map: ``{frame_idx: force entry}`` for the selected case.
        source: NPZ data dict (or ``NpzFile``), read for ``frame_angle``.
        cm_method: ``"bbox"`` (default, bounding-box midpoint) or ``"mass"``.
        mode: ``"cut"`` (default) — every member that reaches a level, with its
            internal force evaluated at the cut, so a member spanning a level
            without a node there still contributes; or ``"end"`` — only member
            ends, each credited to its own node's level (the load-path view: a
            restrained base reads the reaction).

    Returns:
        List of ``{elevation, cx, cy, n_ends, forces}`` records ascending by
        elevation, or ``[]`` when there is nothing to sum.
    """
    from ..model.storey_response import sum_storey_forces
    from .viz_model import _resolve_frame_node

    nodes = geometry.get("nodes", {})
    frames = geometry.get("frames", [])
    if not nodes or not frames or not force_map:
        return []

    node_xyz = {}
    for nd in nodes.values():
        tag = nd.get("tag")
        if tag is not None:
            node_xyz[int(tag)] = (float(nd["x"]), float(nd["y"]), float(nd["z"]))
    if not node_xyz:
        return []

    angles = _npz_frame_angles(source, len(frames))

    members = []
    for idx, fr in enumerate(frames):
        entry = force_map.get(idx)
        if not entry:
            continue
        nd_i = _resolve_frame_node(nodes, fr, "i")
        nd_j = _resolve_frame_node(nodes, fr, "j")
        if nd_i is None or nd_j is None:
            continue
        if abs(nd_j["z"] - nd_i["z"]) <= 1e-9:
            continue  # horizontal — cannot contribute to a level profile
        ends = _member_global_forces(entry, nd_i, nd_j, angles[idx])
        if ends is None:
            continue
        members.append(
            {
                "node_i": int(nd_i["tag"]),
                "node_j": int(nd_j["tag"]),
                "f_i": ends[0],
                "f_j": ends[1],
            }
        )

    return [
        {
            "elevation": lv["elevation"],
            "cx": lv["cx"],
            "cy": lv["cy"],
            "n_ends": lv["n_ends"],
            "forces": {k.upper(): lv[k] for k in ("Fx", "Fy", "Fz", "Mx", "My", "Mz")},
        }
        for lv in sum_storey_forces(node_xyz, members, cm_method=cm_method, mode=mode)
    ]


def _npz_unit(source: dict, key: str, default: str = "?") -> str:
    """Read a length-1 string array from NPZ data (e.g. ``force_unit``).

    Returns *default* when the requested unit is missing or empty.
    """
    arr = source.get(key)
    if arr is not None and len(arr):
        return str(arr[0])
    return default


# ── Input resolution ─────────────────────────────────────────────────


# ── Two-sided (fork) pairing ─────────────────────────────────────────

#: ``static_case_kind`` markers for the two signed senses of a magnitude.  A
#: combination that mixes a spectrum magnitude with signed terms is emitted
#: twice by
#: :func:`~fea_toolkit.model.load_combinations.generate_combination_results`.
_FORK_KINDS = {"+QE": 1, "-QE": -1}

#: Colour / marker cycle for the extra curves of a combination group — the
#: primary curve is drawn in blue with circles, so these start at orange with
#: squares.  A 2ⁿ fork of three magnitudes needs seven.
_GROUP_COLOURS = (
    "tab:orange",
    "tab:green",
    "tab:red",
    "tab:purple",
    "tab:brown",
    "tab:pink",
    "tab:olive",
)
_GROUP_MARKERS = ("s", "^", "D", "v", "P", "X", "<")

#: Fallback for archives written before ``static_case_kind`` existed:
#: ``_name_variants`` names the forks ``"<combo> #1"`` / ``"<combo> #2"``.
_FORK_LABEL_RE = re.compile(r"^(?P<base>.*?)\s*#(?P<n>\d+)$")


def _str_array(source, key) -> list:
    """Read a 1-D string array from NPZ data; ``[]`` when absent or empty."""
    arr = source.get(key)
    if arr is None:
        return []
    return [str(v) for v in np.asarray(arr).ravel()]


def _case_pairs(source, combinations=None, load_cases=None) -> dict:
    """Map each static case to ``(group, sense)`` for spectrum-fork pairing.

    A combination that superposes a spectrum magnitude forks into the two
    senses the earthquake can act in, and **both** are needed to read the
    result.  The pairing is resolved from data where possible:

    1. an explicit *combinations* definition — see :func:`_definition_pairs` —
       else
    2. the ``static_case_group`` / ``static_case_kind`` arrays — ``kind`` is
       ``"+QE"`` / ``"-QE"`` for a single-sense fork and ``group`` is the
       combination every variant was generated from, else
    3. the ``"<combo> #1"`` / ``"<combo> #2"`` label convention that
       :func:`~fea_toolkit.model.load_combinations._name_variants` emits, so
       archives predating the metadata still pair.

    Args:
        source: NPZ data dict or ``NpzFile``.
        combinations: Optional external definition set
            (``{name: {"type", "entries", ...}}`` or
            ``{name: LoadCombination}``).  Its variants outrank the archive's
            own annotations for the names it generates.
        load_cases: Optional ``{name: LoadCase}`` mapping, used only to expand
            *combinations* faithfully (a response-spectrum reference forks).

    Returns:
        ``{case_name: (group, sense)}`` — *sense* is ``+1`` / ``-1`` for a
        **single-sense** forked magnitude combination and ``0`` for an ordinary
        case or a multi-member group (a 2ⁿ fork's sense lives in its
        coordinates, not in one sign).
    """
    from ..io.npz_reader import _get_static_cases

    names = list(_get_static_cases(source))
    groups = _str_array(source, "static_case_group")
    kinds = _str_array(source, "static_case_kind")

    pairs: dict = {}
    if combinations is not None:
        # Only the definition's variants this archive actually holds, so a set
        # covering combinations that were never exported cannot pollute the
        # grouping of the ones that were.
        available = set(names)
        pairs.update(
            {
                name: pair
                for name, pair in _definition_pairs(combinations, load_cases).items()
                if name in available
            }
        )

    # Label-derived bases, counted BEFORE assigning a sense.  The ``#1`` /
    # ``#2`` convention is only trustworthy when a base appears exactly twice:
    # two forked spectrum entries give four cartesian variants, where ``#1``
    # and ``#2`` are *not* the two senses of one magnitude.  A longer group is
    # therefore left unpaired rather than mismatched.
    numbered: dict = {}
    for name in names:
        match = _FORK_LABEL_RE.match(name)
        if match and int(match.group("n")) in (1, 2):
            numbered.setdefault(match.group("base"), []).append(name)

    for i, name in enumerate(names):
        if name in pairs:
            continue
        group = groups[i] if i < len(groups) else ""
        kind = kinds[i] if i < len(kinds) else ""
        if kind in _FORK_KINDS:
            pairs[name] = (group or name, _FORK_KINDS[kind])
            continue
        if group:
            # Annotated (P20 / P21) but not a single-sense fork: the archive
            # says which combination generated it, so group it and let the
            # coordinates distinguish the members.
            pairs[name] = (group, 0)
            continue
        # No metadata — fall back to the label convention.
        match = _FORK_LABEL_RE.match(name)
        base = match.group("base") if match else name
        if match and len(numbered.get(base, [])) == 2:
            pairs[name] = (base, 1 if int(match.group("n")) == 1 else -1)
        else:
            pairs[name] = (name, 0)
    return pairs


def _definition_pairs(combinations, load_cases=None) -> dict:
    """``{variant_name: (group, sense)}`` for a definition's own variants.

    Expands *combinations* through the same code path that generated the
    archive, so the resolved names are the archive's case names and the
    pairing comes from the definition rather than from the ``"#n"`` label.

    Args:
        combinations: Canonical definition dict or ``{name: LoadCombination}``.
        load_cases: Optional ``{name: LoadCase}`` mapping.  Without it a
            reference forks only when the definition marks it
            ``"magnitude": true`` — nothing else says which load cases are
            response spectra.

    Returns:
        ``{case_name: (group, sense)}``.  A definition that cannot be expanded
        (it references cases the archive never ran) contributes nothing, so
        the caller falls back to the archive's own annotations.
    """
    from ..model.load_combinations import (
        as_combination_mapping,
        combination_case_meta,
        generate_combination_results,
    )

    try:
        combos = as_combination_mapping(combinations)
    except (TypeError, ValueError):
        return {}
    pairs: dict = {}
    for combo in combos.values():
        try:
            composites = generate_combination_results(combo, load_cases or {}, combos)
        except (KeyError, ValueError):
            continue
        meta = combination_case_meta(composites, load_cases or {}, combos)
        for name, info in meta.items():
            pairs[name] = (info.get("group") or name, _FORK_KINDS.get(info.get("kind", ""), 0))
    return pairs


def _case_coords(source) -> dict:
    """``{case: coords}`` from the optional ``static_case_coords`` array."""
    from ..io.npz_reader import _get_static_cases

    coords = _str_array(source, "static_case_coords")
    return {
        name: (coords[i] if i < len(coords) else "")
        for i, name in enumerate(_get_static_cases(source))
    }


def _group_members(source, case_name, combinations=None, load_cases=None) -> list:
    """Every static case generated from the same combination as *case_name*.

    Includes *case_name* itself, in archive order.  A case with no group (an
    ordinary load case) is its own only member.
    """
    if not case_name:
        return []
    pairs = _case_pairs(source, combinations, load_cases)
    group = pairs.get(case_name, (case_name, 0))[0]
    return [name for name, (grp, _) in pairs.items() if grp == group]


def _companion_case(source, case_name, combinations=None, load_cases=None) -> Optional[str]:
    """Name of the opposite spectrum fork of *case_name*, else ``None``.

    Returns ``None`` unless **exactly one** other case shares the group with
    the opposite sense, so a combination with more than two variants is left
    unpaired rather than mismatched.  For a multi-member group use
    :func:`_group_members`, which needs no such restriction.
    """
    if not case_name:
        return None
    pairs = _case_pairs(source, combinations, load_cases)
    group, sense = pairs.get(case_name, (case_name, 0))
    if sense == 0:
        return None
    matches = [n for n, (g, s) in pairs.items() if g == group and s == -sense]
    return matches[0] if len(matches) == 1 else None


def _fork_legend(source, case_name, combinations=None, load_cases=None) -> str:
    """Legend text for *case_name* — its group plus its sense or coordinates.

    ``"COMB1 [+QE]"`` for a single-sense fork, ``"FLAT4 [+RSX, -RSY]"`` for a
    multi-fork coordinate, ``"ENV [max]"`` for an envelope extreme, else the
    group name.
    """
    if not case_name:
        return ""
    group, sense = _case_pairs(source, combinations, load_cases).get(case_name, (case_name, 0))
    if sense:
        return f"{group} [{'+QE' if sense > 0 else '-QE'}]"
    coords = _case_coords(source).get(case_name, "")
    if coords:
        return f"{group} [{coords.replace('|', ', ')}]"
    return group


def _storey_xy(storey_series, q_upper) -> tuple:
    """Plot-ready ``(values, elevations)`` for a storey profile."""
    return (
        [r["forces"].get(q_upper, 0.0) for r in storey_series],
        [r["elevation"] for r in storey_series],
    )


def _resolve_source(
    source,
    force_data=None,
    combo=None,
    collapse_to_parents=False,
    kind=None,
    quantity=None,
    cm_method: str = "bbox",
    storey_mode: str = "cut",
    both_sides: bool = True,
    combinations=None,
    load_cases=None,
) -> Optional[ForceDiagramData]:
    """Normalise any supported input into a :class:`ForceDiagramData`.

    Args:
        source: Builder, NPZ path, NPZ data dict, RS list, or RS dict.
        force_data: Static force dict or RS results (Builder path).
        combo: Static case name (NPZ sources).
        collapse_to_parents: Collapse split children to parents (3D).
        kind: Pinned kind (``'static'`` / ``'rs'``) or ``None`` to infer.
        quantity: Canonical quantity key (used for RS series extraction).
        cm_method: Storey reference-point rule for the 2D storey profile —
            ``'bbox'`` (bounding-box midpoint, default) or ``'mass'``.
        storey_mode: 2D storey summation rule — ``'cut'`` (default) or
            ``'end'``; see
            :func:`~fea_toolkit.model.storey_response.sum_storey_forces`.
        both_sides: When the resolved case belongs to a combination group
            (a spectrum fork, a multi-fork or an envelope pair), also sum every
            other member of that group and attach them as
            :attr:`ForceDiagramData.storey_extras`, so the 2D profile shows all
            the signs rather than one.  Default ``True``.
        combinations: Optional external combination definition set used to
            resolve the grouping (see :func:`_case_pairs`); when omitted the
            archive's ``static_case_*`` arrays are used, then the ``#1`` /
            ``#2`` label convention.
        load_cases: Optional ``{name: LoadCase}`` mapping used only to expand
            *combinations* faithfully.

    Returns:
        :class:`ForceDiagramData`, or ``None`` when there is nothing to plot.

    Raises:
        ValueError: If an NPZ source has no static cases.
    """
    from ..utils import force_unit_label, length_unit_label
    from .viz_forces import (
        _extract_npz_frame_forces,
        _extract_npz_rs_forces,
        _resolve_npz_static_case,
    )
    from .viz_model import _resolve_mesh_data

    quantity = _normalise_quantity(quantity) or "My"

    # ── RS list / dict ────────────────────────────────────────────────
    # A pinned kind="rs" keeps dict sources in the RS branch (empty dict →
    # no records → None); builders are not dicts so they fall through to
    # the Builder+RS handling below.
    if isinstance(source, list) or (
        isinstance(source, dict) and ("element_results" in source or kind == "rs")
    ):
        units = None
        geometry = None
        if isinstance(source, dict):
            records = source.get("element_results") or []
            if not records and "rs/elem_sap_id" in source:
                # NPZ archive — the per-element forces live in the flat
                # ``rs/elem_*`` block, so rebuild the record list from it.
                records = _extract_npz_rs_forces(source)
            units = source.get("units")
            if records:
                geometry = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
        else:
            records = list(source)
        if not records:
            return ForceDiagramData(
                kind="rs", quantity=quantity or "My", force_unit="kN", length_unit="m"
            )
        if units is None and isinstance(force_data, dict):
            units = force_data.get("units")
        if units is not None:
            fu, lu = force_unit_label(units), length_unit_label(units)
        elif isinstance(source, dict) and "force_unit" in source:
            # NPZ metadata carries the archive's model units.
            fu = _npz_unit(source, "force_unit", "kN")
            lu = _npz_unit(source, "length_unit", "m")
        else:
            fu, lu = "kN", "m"
        series = _build_series_from_rs(records, quantity or "My")
        return ForceDiagramData(
            kind="rs",
            quantity=quantity or "My",
            force_unit=fu,
            length_unit=lu,
            nodes=(geometry or {}).get("nodes", {}),
            frames=(geometry or {}).get("frames", []),
            force_map=_build_rs_force_map(records, geometry) if geometry else {},
            series=series,
        )

    # ── NPZ path ──────────────────────────────────────────────────────
    if isinstance(source, (str, Path)):
        from ..io.npz_reader import read_results

        source = read_results(str(source))

    # ── NPZ data dict: RS element forces (flat rs/elem_* block) ───────
    # Reached for a *path* input (the branch above only matches dicts), and for
    # dicts whose ``kind`` was not pinned — RS is inferred from the presence of
    # the ``rs/elem_sap_id`` array.
    if (
        isinstance(source, (dict, np.lib.npyio.NpzFile))
        and "element_results" not in source
        and "rs/elem_sap_id" in source
    ):
        records = _extract_npz_rs_forces(source)
        if records:
            geometry = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
            return ForceDiagramData(
                kind="rs",
                quantity=quantity or "My",
                force_unit=_npz_unit(source, "force_unit", "kN"),
                length_unit=_npz_unit(source, "length_unit", "m"),
                nodes=geometry.get("nodes", {}),
                frames=geometry.get("frames", []),
                force_map=_build_rs_force_map(records, geometry),
                series=_build_series_from_rs(records, quantity or "My"),
            )

    # ── NPZ data dict ─────────────────────────────────────────────────
    if isinstance(source, (dict, np.lib.npyio.NpzFile)) and "element_results" not in source:
        geometry = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
        case_prefix = _resolve_npz_static_case(source, combo)
        case_name = case_prefix[len("static/") : -1]
        force_map = _extract_npz_frame_forces(source, case_prefix, geometry["frames"])
        series = _build_series_from_force_map(force_map, geometry["frames"], geometry["nodes"])
        # A spectrum combination is two-sided: sum every other member of the
        # same group too, so the 2D profile draws all the signs rather than one.
        extras: list = []
        if both_sides:
            for member in _group_members(source, case_name, combinations, load_cases):
                if member == case_name:
                    continue
                member_map = _extract_npz_frame_forces(
                    source, f"static/{member}/", geometry["frames"]
                )
                extras.append(
                    (
                        _build_storey_series(geometry, member_map, source, cm_method, storey_mode),
                        _fork_legend(source, member, combinations, load_cases),
                    )
                )
        return ForceDiagramData(
            kind="static",
            quantity=quantity or "My",
            force_unit=_npz_unit(source, "force_unit", "kN"),
            length_unit=_npz_unit(source, "length_unit", "m"),
            nodes=geometry["nodes"],
            frames=geometry["frames"],
            force_map=force_map,
            series=series,
            storey_series=_build_storey_series(geometry, force_map, source, cm_method, storey_mode),
            storey_cm=cm_method,
            storey_mode=storey_mode,
            storey_extras=extras,
            storey_label=_fork_legend(source, case_name, combinations, load_cases),
        )

    # ── Builder / model + force_data ──────────────────────────────────
    geometry = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
    model = getattr(source, "model", None) or getattr(source, "mesh_model", None) or source
    units = getattr(model, "units", None) or getattr(source, "units", None)
    fu = force_unit_label(units) if units else "kN"
    lu = length_unit_label(units) if units else "m"

    # Builder + RS results
    if isinstance(force_data, list) or (
        isinstance(force_data, dict) and "element_results" in force_data
    ):
        records = force_data["element_results"] if isinstance(force_data, dict) else force_data
        if isinstance(force_data, dict) and force_data.get("units"):
            units = force_data["units"]
            fu = force_unit_label(units)
            lu = length_unit_label(units)
        series = _build_series_from_rs(records, quantity or "My")
        return ForceDiagramData(
            kind="rs",
            quantity=quantity or "My",
            force_unit=fu,
            length_unit=lu,
            nodes=geometry["nodes"],
            frames=geometry["frames"],
            series=series,
        )

    # Builder + static forces
    if not force_data:
        return ForceDiagramData(
            kind="static", quantity=quantity or "My", force_unit=fu, length_unit=lu
        )
    force_map = _build_static_force_map(source, geometry, force_data)
    series = _build_series_from_force_map(force_map, geometry["frames"], geometry["nodes"])
    return ForceDiagramData(
        kind="static",
        quantity=quantity or "My",
        force_unit=fu,
        length_unit=lu,
        nodes=geometry["nodes"],
        frames=geometry["frames"],
        force_map=force_map,
        series=series,
        storey_series=_build_storey_series(geometry, force_map, None, cm_method, storey_mode),
        storey_cm=cm_method,
        storey_mode=storey_mode,
    )


# ── 2D renderers (matplotlib) ────────────────────────────────────────


def _render_rs(
    series,
    quantity: str,
    force_unit: str,
    length_unit: str,
    both_ends: bool,
    title,
    figsize,
    **kwargs,
) -> Any:
    """Render the RS 2D line plot (quantity vs elevation)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  Install with: pip install matplotlib")
        return None
    if not series:
        print("No element results to plot.")
        return None

    q_upper = quantity.upper()
    sorted_res = sorted(series, key=lambda r: r["z_mid"])
    z = [r["z_mid"] for r in sorted_res]

    q = quantity.lower()
    unit = force_unit if q.startswith(("m", "v", "f")) else ""

    fig, ax = plt.subplots(figsize=figsize)
    if both_ends:
        for r in sorted_res:
            v_i = r["forces"].get(q_upper, 0.0)
            v_j = r["forces"].get(f"{q_upper}_j", v_i)
            ax.plot([v_i, v_j], [r["z_mid"], r["z_mid"]], "-o", **kwargs or {})
    else:
        vals = [r["forces"].get(q_upper, 0.0) for r in sorted_res]
        ax.plot(vals, z, "-o", **kwargs or {})

    ax.set_xlabel(f"{quantity} ({unit})")
    ax.set_ylabel(f"Elevation ({length_unit})")
    ax.set_title(title or f"{quantity} vs Elevation (CQC combined)")
    ax.grid(True, alpha=0.3)
    ax.axvline(0, color="grey", linewidth=0.5)

    fig.tight_layout()
    return fig


def _render_static_2d(
    series,
    quantity: str,
    force_unit: str,
    length_unit: str,
    use_local: bool,
    title,
    figsize,
    storey_series=None,
    cm_method: str = "bbox",
    storey_mode: str = "cut",
    storey_extras=None,
    storey_label: str = "",
) -> Any:
    """Render the static 2D diagram.

    With a *storey_series* the result is the **storey profile** — one line
    through the summed value at each distinct elevation (see
    :func:`_build_storey_series`), so a case reads as a single polyline
    instead of one segment per element.  Without one it falls back to the
    legacy per-element segments.

    Args:
        series: Per-element records from :func:`_build_series_from_force_map`.
        quantity: Canonical quantity key (``'My'``, ``'Fx'``, ...).
        force_unit, length_unit: Axis unit labels.
        use_local: Prefer the ``*_local`` keys (legacy per-element path).
        title: Optional axes title.
        figsize: Matplotlib figure size.
        storey_series: Per-level summed records; takes precedence when given.
        cm_method: Reference-point label shown for a storey profile.
        storey_mode: Level-summation rule, for the axis label.
        storey_extras: Other members of the same combination group, as
            ``(storey_series, label)`` pairs, each drawn as an **additional
            signed diagram** (never a filled band, which would swallow the zero
            crossings).
        storey_label: Legend entry for the primary curve.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  Install with: pip install matplotlib")
        return None
    if not series and not storey_series:
        print("No force data to plot.")
        return None

    q_upper = quantity.upper()

    fig, ax = plt.subplots(figsize=figsize)

    if storey_series:
        ax.plot(
            *_storey_xy(storey_series, q_upper),
            "-o",
            color="tab:blue",
            lw=1.4,
            ms=4,
            label=storey_label or None,
        )
        if storey_extras:
            # One signed diagram per remaining group member — a two-sided
            # combination is asymmetric, so one curve is only half the story.
            for i, (extra_series, extra_label) in enumerate(storey_extras):
                ax.plot(
                    *_storey_xy(extra_series, q_upper),
                    "-" + _GROUP_MARKERS[i % len(_GROUP_MARKERS)],
                    color=_GROUP_COLOURS[i % len(_GROUP_COLOURS)],
                    lw=1.4,
                    ms=4,
                    label=extra_label or None,
                )
    else:
        suffix_i = "_i_local" if use_local else ""
        suffix_j = "_j_local" if use_local else ""
        for ed in series:
            forces = ed["forces"]
            v_i = forces.get(f"{q_upper}{suffix_i}", forces.get(q_upper, np.nan))
            v_j = forces.get(f"{q_upper}{suffix_j}", forces.get(f"{q_upper}_j", np.nan))
            if np.isnan(v_i) or np.isnan(v_j):
                continue
            z_i = ed["z_i"]
            z_j = ed["z_j"]
            # Negate J-end for forces only (axial/shear satisfy F_j = -F_i)
            if not quantity.startswith("M"):
                v_j = -v_j
            ax.plot([v_i, v_j], [z_i, z_j], color="tab:blue", lw=1.0, alpha=0.7)

    ax.axvline(0, color="grey", lw=0.5, ls="--")
    kind = "Bending moment" if quantity.startswith("M") else "Force"
    if storey_series:
        phrase = {
            "cut": "storey sum across the cut",
            "end": "member-end sum, load path (base = reaction)",
        }.get(storey_mode, storey_mode)
        label = f"{kind} {quantity} [{force_unit}] — {phrase} · CM: {cm_method}"
    else:
        label = f"{kind} {quantity} [{force_unit}]" + (" (local)" if use_local else "")
    ax.set_xlabel(label)
    ax.set_ylabel(f"Elevation [{length_unit}]")
    ax.set_title(title or f"{kind} {quantity} vs elevation")
    ax.grid(True, alpha=0.3)
    if storey_extras:
        # Several signed diagrams — the response-spectrum fork / envelope pair.
        ax.legend(loc="best", fontsize="small", framealpha=0.9)

    fig.tight_layout()
    return fig


# ── 3D renderer (PyVista) ────────────────────────────────────────────


def _render_static_3d(
    data: ForceDiagramData,
    quantity: str,
    mode: str,
    moment_scale,
    show_original: bool,
    notebook: bool,
    title,
    window_title=None,
    **kwargs,
) -> Any:
    """Render the static 3D diagram via the shared frame renderer.

    Args:
        data: Resolved force-diagram data.
        quantity: Normalised quantity key (``My``, ``Fz``, ...).
        mode: ``'flag'`` or ``'tube'``.
        moment_scale: Extrusion length per unit quantity (``None`` = auto).
        show_original: Draw the undeformed centreline in grey.
        notebook: Return the plotter instead of showing it.
        title: Optional in-plot caption (drawn at the upper edge).
        window_title: Optional PyVista window (title-bar) caption.
        **kwargs: Passed to ``pyvista.Plotter()``.
    """
    from .viz_common import _set_isometric_view
    from .viz_forces import _render_frame_force_diagram

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista is required.  pip install pyvista")
        return None
    if not data.force_map:
        print(f"No {quantity} data to plot.")
        return None

    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, title=window_title, **kwargs)

    model_height, max_abs_val = _render_frame_force_diagram(
        plotter,
        None,  # `_compute_local_forces` does not consult the source object
        data.frames,
        data.nodes,
        data.force_map,
        quantity,
        mode,
        show_original=show_original,
        moment_scale=moment_scale,
    )

    if max_abs_val < 1e-15 and model_height < 1e-15:
        print(f"No {quantity} data to plot.")
        return None

    plotter.add_text(f"{quantity}  (red = +ve, blue = −ve)", position="lower_edge", font_size=10)
    if title:
        plotter.add_text(title, position="upper_edge", font_size=12)
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


# ── Unified entry point ──────────────────────────────────────────────


def plot_force_diagram(
    source,
    force_data=None,
    *,
    quantity="My",
    kind=None,
    dimension=None,
    combo=None,
    force_unit=None,
    length_unit=None,
    use_local=True,
    both_ends=False,
    collapse_to_parents=False,
    mode="flag",
    moment_scale=None,
    show_original=True,
    notebook=False,
    by_storey=True,
    cm_method="bbox",
    storey_mode="cut",
    both_sides=True,
    combinations=None,
    load_cases=None,
    title=None,
    window_title=None,
    figsize=None,
    **kwargs,
) -> Any:
    """Draw a force/moment diagram from any supported input.

    Unified, unit-aware dispatcher covering every input the toolkit
    supports — ``AnalysisBuilder`` + static/RS results, in-memory result
    dicts, and NPZ paths — and dispatching 2D-vs-3D and static-vs-RS from
    the input shape.

    Args:
        source: Builder instance, NPZ path, NPZ data dict, RS
            ``element_results`` list, or full RS result dict.
        force_data: Static force dict from ``extract_static_element_forces()``
            (Builder path) or RS results (Builder path).
        quantity: Quantity to plot, e.g. ``'My'``, ``'Mz'``, ``'Fx'``,
            ``'Vz'``.  Both the plain style (``'My'``) and the RS key style
            (``'My_i'``) are accepted.
        kind: ``'static'`` or ``'rs'``.  ``None`` infers from the input
            (RS when records carry ``z_mid``).  This selects *where the
            numbers come from* (and how they are keyed), not how they are
            drawn — rendering is controlled by ``dimension``.
        dimension: ``'2d'`` or ``'3d'`` — the rendering view, an axis
            independent of ``kind``.  The same ``quantity`` and ``combo``
            plot in either dimension; only the encoding changes.

            * ``'2d'`` (Matplotlib) — a **chart**, not a picture of the
              structure.  Quantity on the horizontal axis, elevation ``z``
              on the vertical axis, each frame element a line segment from
              ``(v_i, z_i)`` to ``(v_j, z_j)``.  A uniform axial force reads
              as a vertical line; a sign change along the member reads as a
              crossing of the zero axis, where the triangular fill switches
              side.  The RS path plots one point per element at ``z_mid``
              (``both_ends=True`` draws a horizontal ``[v_i, v_j]`` segment
              instead).  Always available — Matplotlib is a core dependency.
            * ``'3d'`` (PyVista) — a **spatial model**: the member
              centrelines in 3D, each carrying its quantity as a ribbon
              offset perpendicular to it (``mode="flag"``) or as a tube whose
              radius encodes the value (``mode="tube"``).  A sign change
              flips the ribbon to the other side of the member — the 3D
              counterpart of the 2D triangle.  Needs PyVista, which may be
              absent from an embedded interpreter such as Rhino 8.

            ``None`` infers: an RS source stays 2D unless ``dimension="3d"``
            is pinned *and* geometry is present; a static source resolves to
            3D when PyVista is importable and the source carries nodes,
            otherwise 2D.
        combo: Static case name for NPZ sources (``None`` = first case).
        force_unit: Force-unit label override (overrides source metadata).
        length_unit: Length-unit label override.
        use_local: Use local-coordinate forces (2D static / NPZ paths).
        both_ends: Plot both I- and J-end values (RS path).
        collapse_to_parents: Collapse split children to parents (3D).
        mode: ``'flag'`` or ``'tube'`` (3D static).
        moment_scale: Extrusion length per unit quantity (3D).
        show_original: Draw the centreline in grey (3D).
        notebook: Return the PyVista plotter (3D).
        by_storey: 2D only — draw the **storey profile** (one line through the
            value summed at each distinct elevation) instead of the legacy
            per-element segments.  Default ``True``.
        cm_method: 2D storey reference point — ``'bbox'`` (bounding-box
            midpoint of the nodes at each level, default) or ``'mass'``
            (mass-weighted centre of mass).  It sets the origin for the
            storey moments, so ``Mx``/``My`` (overturning) and ``Mz``
            (torsion) pick up the lever-arm of the axial / shear forces.
        storey_mode: 2D only — how the storey profile is summed.  ``'cut'``
            (default) credits **every member that reaches the level**, using
            its internal force at the cut, so a member spanning several levels
            without a node at one of them still contributes — this is what a
            storey-shear / overturning diagram reports.  ``'end'`` credits only
            member **ends** to their own node's level — the **load path** view:
            minus the applied nodal load, and the **reaction** at a support
            level (so a restrained base is not ~0), and blind to a member
            crossing a level without a node there.  See
            :func:`~fea_toolkit.model.storey_response.sum_storey_forces`.
        both_sides: 2D only — when the resolved case belongs to a combination
            group, draw **every** member of that group on one set of axes as
            separate signed diagrams (blue circles first, then an
            orange-square / green-triangle / … cycle) with a legend.  A
            combination that mixes a spectrum *magnitude* with signed
            gravity/wind is two-sided and asymmetric, so one curve is only half
            the result; a 2ⁿ fork (two independent spectra) has four corners and
            an envelope pair has two extremes — all are drawn.  Grouping comes
            from *combinations* (when given), else the archive's
            ``static_case_group`` / ``static_case_kind`` / ``static_case_coords``
            arrays, else the ``#1`` / ``#2`` label convention.  Default ``True``;
            pass ``False`` for a single curve.
        combinations: An **external combination definition set** — the canonical
            ``{name: {"type", "entries", ...}}`` dict (see
            :func:`fea_toolkit.io.combination_set.read_combination_set`) or a
            ready ``{name: LoadCombination}`` mapping.  Supplying it lets the
            renderer group and label the cases from the definition that
            generated them rather than from the archive's annotations.
        load_cases: Optional ``{name: LoadCase}`` mapping used only to expand
            *combinations* faithfully — without it a definition reference forks
            only where it is marked ``"magnitude": true``.
        title: Optional plot title, drawn **inside** the plot (the 2D axes
            title / the 3D upper-edge text).
        window_title: Optional PyVista render-window (title-bar) caption for
            the 3D paths — e.g. the source model's file name, so the open
            window identifies itself.  Ignored by the 2D Matplotlib paths,
            which own no such window.
        figsize: Matplotlib figure size (2D paths).
        **kwargs: Passed to ``pyvista.Plotter()`` (3D) or
            ``matplotlib.pyplot.plot()`` (RS 2D).

    Returns:
        ``pyvista.Plotter`` (3D, when *notebook*), a
        ``matplotlib.figure.Figure`` (2D), or ``None``.
    """
    q = _normalise_quantity(quantity)
    _is_shear_alias = q in ("Vz", "Vy")
    if kind != "rs" and (q is None or not (q.startswith(("M", "F")) or _is_shear_alias)):
        print(f"Unsupported quantity '{quantity}'.  Use 'M*', 'F*', 'Vz', or 'Vy'.")
        return None

    data = _resolve_source(
        source,
        force_data,
        combo,
        collapse_to_parents,
        kind,
        q,
        cm_method,
        storey_mode,
        both_sides,
        combinations,
        load_cases,
    )
    if data is None or data.empty:
        if data is not None and data.kind == "rs":
            print("No element results to plot.")
        else:
            print("No force data to plot.")
        return None

    eff_kind = kind or data.kind
    fu = force_unit or data.force_unit or "kN"
    lu = length_unit or data.length_unit or "m"

    # Shear aliases resolve to the local shear components used by the
    # geometry-based renderers: static 3D and RS 3D both key off "Fy"/"Fz".
    q_static = {"Vz": "Fz", "Vy": "Fy"}.get(q, q)

    if eff_kind == "rs":
        # 2D quantity-vs-elevation stays the default.  The per-element 3D view
        # is opt-in via ``dimension="3d"`` and needs geometry + a force map
        # (only available for builder/archive sources, not bare RS lists).
        if dimension == "3d" and data.force_map:
            return _render_static_3d(
                data,
                q_static,
                mode,
                moment_scale,
                show_original,
                notebook,
                title,
                window_title=window_title,
                **kwargs,
            )
        return _render_rs(data.series, q, fu, lu, both_ends, title, figsize, **kwargs)

    # Static — infer dimension unless pinned
    if dimension is None:
        try:
            import pyvista  # noqa: F401

            has_pv = True
        except ImportError:
            has_pv = False
        dimension = "3d" if has_pv and data.nodes else "2d"

    if dimension == "3d":
        return _render_static_3d(
            data,
            q_static,
            mode,
            moment_scale,
            show_original,
            notebook,
            title,
            window_title=window_title,
            **kwargs,
        )
    return _render_static_2d(
        data.series,
        q_static,
        fu,
        lu,
        use_local,
        title,
        figsize or (8, 6),
        storey_series=data.storey_series if by_storey else None,
        cm_method=data.storey_cm,
        storey_mode=data.storey_mode,
        storey_extras=data.storey_extras if by_storey else None,
        storey_label=data.storey_label,
    )
