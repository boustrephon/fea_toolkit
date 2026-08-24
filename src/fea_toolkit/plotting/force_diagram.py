"""
Unified force/moment diagram plotting.

The single unit-aware entry point :func:`plot_force_diagram` replaces the
four legacy force-diagram functions (``plot_force_diagram_3d``,
``plot_rs_force_diagram``, ``plot_npz_force_diagram``,
``plot_npz_moment_3d``), which are now thin wrappers over it (see
``plotting.viz``).

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

The RS ``element_results`` records are a de-facto contract of the response-
spectrum extraction pipeline: each carries ``z_mid`` and per-end quantity
keys such as ``My_i`` / ``My_j`` (CQC-combined).  ``plot_force_diagram``
normalises both the ``'My_i'`` and ``'My'`` key styles internally.
"""

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
    """

    kind: str
    quantity: str
    force_unit: str
    length_unit: str
    nodes: dict = field(default_factory=dict)
    frames: list = field(default_factory=list)
    force_map: dict = field(default_factory=dict)
    series: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """True when there is nothing to plot."""
        return not self.force_map and not self.series


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
    elements = getattr(source, "split_elements", None) or model.frame_elements

    elem_by_node_pair: dict[tuple[int, int], int] = {}
    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        eni = model.nodes.get(elem.node_i)
        enj = model.nodes.get(elem.node_j)
        if eni is None or enj is None:
            continue
        elem_by_node_pair[(eni.node_tag, enj.node_tag)] = elem.elem_tag

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


def _npz_unit(source: dict, key: str) -> str:
    """Read a length-1 string array from NPZ data (e.g. ``force_unit``)."""
    arr = source.get(key)
    if arr is not None and len(arr):
        return str(arr[0])
    return "?"


# ── Input resolution ─────────────────────────────────────────────────


def _resolve_source(
    source,
    force_data=None,
    combo=None,
    collapse_to_parents=False,
    kind=None,
    quantity=None,
) -> Optional[ForceDiagramData]:
    """Normalise any supported input into a :class:`ForceDiagramData`.

    Args:
        source: Builder, NPZ path, NPZ data dict, RS list, or RS dict.
        force_data: Static force dict or RS results (Builder path).
        combo: Static case name (NPZ sources).
        collapse_to_parents: Collapse split children to parents (3D).
        kind: Pinned kind (``'static'`` / ``'rs'``) or ``None`` to infer.
        quantity: Canonical quantity key (used for RS series extraction).

    Returns:
        :class:`ForceDiagramData`, or ``None`` when there is nothing to plot.

    Raises:
        ValueError: If an NPZ source has no static cases.
    """
    from ..utils import force_unit_label, length_unit_label
    from .viz import (
        _extract_npz_frame_forces,
        _resolve_mesh_data,
        _resolve_npz_static_case,
    )

    quantity = _normalise_quantity(quantity) or "My"

    # ── RS list / dict ────────────────────────────────────────────────
    if kind == "rs" or isinstance(source, list):
        units = None
        if isinstance(source, dict):
            records = source.get("element_results") or []
            units = source.get("units")
        else:
            records = list(source)
        if not records:
            return ForceDiagramData(
                kind="rs", quantity=quantity or "My", force_unit="kN", length_unit="m"
            )
        if units is None and isinstance(force_data, dict):
            units = force_data.get("units")
        fu = force_unit_label(units) if units else "kN"
        lu = length_unit_label(units) if units else "m"
        series = _build_series_from_rs(records, quantity or "My")
        return ForceDiagramData(
            kind="rs",
            quantity=quantity or "My",
            force_unit=fu,
            length_unit=lu,
            series=series,
        )

    # ── NPZ path ──────────────────────────────────────────────────────
    if isinstance(source, (str, Path)):
        from ..io.npz_reader import read_results

        source = read_results(str(source))

    # ── NPZ data dict ─────────────────────────────────────────────────
    if isinstance(source, (dict, np.lib.npyio.NpzFile)) and "element_results" not in source:
        geometry = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
        case_prefix = _resolve_npz_static_case(source, combo)
        force_map = _extract_npz_frame_forces(source, case_prefix, geometry["frames"])
        series = _build_series_from_force_map(force_map, geometry["frames"], geometry["nodes"])
        return ForceDiagramData(
            kind="static",
            quantity=quantity or "My",
            force_unit=_npz_unit(source, "force_unit"),
            length_unit=_npz_unit(source, "length_unit"),
            nodes=geometry["nodes"],
            frames=geometry["frames"],
            force_map=force_map,
            series=series,
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
    unit = force_unit if q.startswith(("m", "v")) else ""

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
) -> Any:
    """Render the static 2D diagram (per-element end values vs elevation)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  Install with: pip install matplotlib")
        return None
    if not series:
        print("No force data to plot.")
        return None

    q_upper = quantity.upper()
    suffix_i = "_i_local" if use_local else ""
    suffix_j = "_j_local" if use_local else ""

    fig, ax = plt.subplots(figsize=figsize)
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
    ax.set_xlabel(f"{kind} {quantity} [{force_unit}]" + (" (local)" if use_local else ""))
    ax.set_ylabel(f"Elevation [{length_unit}]")
    ax.set_title(title or f"{kind} {quantity} vs elevation")
    ax.grid(True, alpha=0.3)

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
    **kwargs,
) -> Any:
    """Render the static 3D diagram via the shared frame renderer."""
    from .viz import _render_frame_force_diagram, _set_isometric_view

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista is required.  pip install pyvista")
        return None
    if not data.force_map:
        print(f"No {quantity} data to plot.")
        return None

    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)

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
    title=None,
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
            (RS when records carry ``z_mid``).
        dimension: ``'2d'`` or ``'3d'``.  ``None`` infers from PyVista
            availability and geometry presence.
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
        title: Optional plot title.
        figsize: Matplotlib figure size (2D paths).
        **kwargs: Passed to ``pyvista.Plotter()`` (3D) or
            ``matplotlib.pyplot.plot()`` (RS 2D).

    Returns:
        ``pyvista.Plotter`` (3D, when *notebook*), a
        ``matplotlib.figure.Figure`` (2D), or ``None``.
    """
    q = _normalise_quantity(quantity)
    if kind != "rs" and (q is None or not q.startswith(("M", "F"))):
        print(f"Unsupported quantity '{quantity}'.  Use 'M*' or 'F*'.")
        return None

    data = _resolve_source(source, force_data, combo, collapse_to_parents, kind, q)
    if data is None or data.empty:
        if data is not None and data.kind == "rs":
            print("No element results to plot.")
        else:
            print("No force data to plot.")
        return None

    eff_kind = kind or data.kind
    fu = force_unit or data.force_unit or "kN"
    lu = length_unit or data.length_unit or "m"

    if eff_kind == "rs":
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
            data, q, mode, moment_scale, show_original, notebook, title, **kwargs
        )
    return _render_static_2d(data.series, q, fu, lu, use_local, title, figsize or (8, 6))
