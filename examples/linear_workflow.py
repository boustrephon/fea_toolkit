#!/usr/bin/env python
"""
Building linear analysis workflow.

Generates model images, runs meshing/splitting/constraints, detects
orphan mass, executes static/modal/response-spectrum analyses, and
produces summary tables and plots.

The model path is always user-supplied: pass a .s2k file, or use
``--sample`` for the built-in cantilever model.

Usage::

    python examples/linear_workflow.py /path/to/model.s2k
    python examples/linear_workflow.py --sample                # built-in sample
    python examples/linear_workflow.py --no-analysis           # parse + enrich only
"""

import sys
import argparse
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))  # project root (for `examples.sample_model`)
sys.path.insert(0, str(_REPO_ROOT / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.preprocessor import preprocess_model as ft_preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.model.geometry import (
    mesh_area_elements,
    split_elements,
    split_areas_at_frame_edges,
    split_slabs_at_wall_intersections,
    find_constraint_edges,
    warn_frame_overlaps,
    remove_floating_nodes,
)
from fea_toolkit.io.report import (
    summarise_mass_sources,
    summarise_load_cases,
    summarise_load_patterns,
    load_pattern_totals,
    material_summary,
    section_summary,
    area_section_summary,
    modal_table_enhanced,
    format_linear_table,
)
from fea_toolkit.model.sap_data import (
    SAPModelData,
    Node,
    Restraint,
    Material,
    Section,
    FrameElement,
    AreaElement,
    ShellSection,
)
from fea_toolkit.spectrum import _build_spectrum

# ═══════════════════════════════════════════════════════════════════
# 1. Model loading
# ═══════════════════════════════════════════════════════════════════


def load_model(s2k_path: str) -> Tuple[SAPModelData, Dict[str, Any]]:
    """Parse a SAP2000 .s2k file and return model data + raw tables."""
    p = Path(s2k_path)
    if not p.exists():
        raise FileNotFoundError(f"Model file not found: {p}")
    parser = SAP2000Parser(p)
    parser.parse()
    md = parser.get_model_data()
    raw = parser.raw_tables
    return md, raw


# ═══════════════════════════════════════════════════════════════════
# 2. Building views
# ═══════════════════════════════════════════════════════════════════


def plot_building_views(
    md: SAPModelData,
    window_size: Tuple[int, int] = (800, 600),
) -> Any:
    """Return a 2×2 matplotlib figure with plan, two elevations, isometric.

    Delegates to :func:`fea_toolkit.plotting.viz.plot_building_views`.
    """
    from fea_toolkit.plotting.viz import plot_building_views as _lib_views

    return _lib_views(md, window_size=window_size)


# ═══════════════════════════════════════════════════════════════════
# 3. Model pre-processing (mesh, split, constraints)
# ═══════════════════════════════════════════════════════════════════


def preprocess_model(md: SAPModelData) -> Dict[str, Any]:
    """Apply meshing, splitting, and constraint detection.

    Returns a dict of pre-processing stats.
    """
    import openseespy.opensees as ops

    stats: Dict[str, Any] = {}

    # ── Mesh area elements (use tags past max existing) ───────────
    n_area_before = len(md.area_elements)
    next_tag = md.max_node_tag() + 1
    md.area_elements, md.area_assignments, md.nodes, next_tag = mesh_area_elements(
        md.area_elements,
        md.area_assignments,
        md.nodes,
        md.area_mesh,
        next_tag=next_tag,
    )
    n_area_after = len(md.area_elements)
    stats["area_elements"] = {"before": n_area_before, "after": n_area_after}

    # ── Split elements at joints ──────────────────────────────────
    n_frame_before = len(md.frame_elements)
    dist_loads = getattr(md, "frame_dist_loads", [])
    frame_auto_mesh = getattr(md, "frame_auto_mesh", {})
    md.frame_elements, md.frame_assignments, md.frame_dist_loads = split_elements(
        md.nodes,
        md.frame_elements,
        md.frame_assignments,
        dist_loads,
        frame_auto_mesh,
    )
    n_frame_after = len(md.frame_elements)
    stats["frame_elements"] = {"before": n_frame_before, "after": n_frame_after}

    # ── Split areas at frame edges ────────────────────────────────
    md.area_elements, md.area_assignments, md.nodes, next_tag = split_areas_at_frame_edges(
        md.area_elements,
        md.area_assignments,
        md.nodes,
        md.frame_elements,
        next_tag=next_tag,
    )
    stats["areas_split_at_frames"] = len(md.area_elements) - n_area_after

    # ── Check for frame overlaps (modelling errors) ───────────────
    if md.frame_elements:
        warnings.filterwarnings("once")
        warn_frame_overlaps(
            md.frame_elements,
            md.frame_assignments,
            md.nodes,
            prefix="[Preprocess] ",
        )

    # ── Split slabs at wall intersections ──────────────────────────
    # Splits slab areas along wall edge lines so wall and slab mesh
    # nodes coincide at the interface.  Currently disabled — the
    # builder-side split_slabs_at_walls option was removed because
    # it created duplicate meshing.  The correct place is here,
    # between area-splitting and constraint detection.
    # if False:
    #     md.area_elements, md.area_assignments, md.nodes, next_tag = (
    #         split_slabs_at_wall_intersections(
    #             md.area_elements, md.area_assignments, md.nodes,
    #             next_tag=next_tag,
    #         )
    #     )
    #     stats["split_slabs_at_walls"] = True

    # ── Split frames at frame intersections ────────────────────────
    # Splits frame elements where they cross other frame elements
    # without sharing a node.  The split_elements() function already
    # supports this via the AtFrames per-element flag in auto_mesh.
    # Currently disabled — enable by setting AtFrames=True on the
    # relevant frame elements in md.frame_auto_mesh.
    # if False:
    #     md.frame_elements, md.frame_assignments, md.frame_dist_loads = (
    #         split_elements(
    #             md.nodes, md.frame_elements, md.frame_assignments,
    #             md.frame_dist_loads, md.frame_auto_mesh,
    #             # Requires AtFrames=True set per-element in
    #             # md.frame_auto_mesh — modify before calling:
    #             # for v in md.frame_auto_mesh.values():
    #             #     v['AtFrames'] = True
    #         )
    #     )
    #     stats["split_frames_at_frames"] = True

    # ── Constraint edges — detect edges from post-split data ─────
    # No builder/build needed — find_constraint_edges operates on the
    # model data directly.  The actual build + constraint application
    # happens per analysis call in _build_and_constrain.
    raw_edges = find_constraint_edges(
        md.area_elements,
        md.area_assignments,
        md.nodes,
        frame_elements=md.frame_elements,
        frame_assignments=md.frame_assignments,
    )
    stats["constraint_edges"] = len(raw_edges)
    if raw_edges:
        print(f"  → {len(raw_edges)} constraint edge(s) detected")
    else:
        print("  (no constraint edges detected)")

    # Clear area_mesh so builder's _mesh_areas() doesn't re-mesh
    # already-subdivided areas.
    md.area_mesh = {}

    return stats


# ═══════════════════════════════════════════════════════════════════
# 4. Orphan mass detection
# ═══════════════════════════════════════════════════════════════════


def remove_floating_nodes(
    md: SAPModelData,
    z_tolerance: float = 0.5,
) -> pd.DataFrame:
    """Remove nodes not connected to any element, redistributing loads and mass.

    Delegates to :func:`fea_toolkit.model.geometry.remove_floating_nodes`
    and wraps the result in a DataFrame for backward compatibility.

    Returns a DataFrame documenting what was moved.
    """
    from fea_toolkit.model.geometry import remove_floating_nodes as _lib_remove

    rows = _lib_remove(md, z_tolerance=z_tolerance)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════
# 5. Linear analysis
# ═══════════════════════════════════════════════════════════════════


def _auto_detect_cases(md: SAPModelData) -> List[str]:
    """Auto-detect static load cases from the SAP2000 model.

    Delegates to :meth:`SAPModelData.auto_detect_static_cases`.
    """
    return md.auto_detect_static_cases()


def _build_and_constrain(md: SAPModelData) -> AnalysisBuilder:
    """Build model once, detect + apply edge constraints."""
    import openseespy.opensees as ops

    ops.wipe()
    mm = ft_preprocess_model(
        md,
        {
            "verbose": False,
            "create_shells": True,
            "split_elements": False,  # already split in preprocess
            "element_type": "elasticBeamColumn",
            "use_elastic_sections": True,
            # Detect wall nodes inside slab areas (warns during _mesh_areas)
            "detect_wall_slab_intersections": True,
            # Slab-wall splitting is handled in preprocess_model() (as a
            # commented-out option at position 4) — don't re-split here.
            # "split_slabs_at_walls": True,
            "solver_test_max_iter": 30,
            "gravity_num_substeps": 5,
            # Per-type stiffness factors (ACI 318-19 cracked-section)
            "stiffness_factors": {
                "beam": 0.35,
                "column": 0.70,
                "brace": 0.50,
                "wall": 0.70,
                "slab": 0.25,
            },
        },
    )
    b = AnalysisBuilder(
        mm,
        {
            "verbose": False,
            "create_shells": True,
            "split_elements": False,
            "element_type": "elasticBeamColumn",
            "use_elastic_sections": True,
            "detect_wall_slab_intersections": True,
            "solver_test_max_iter": 30,
            "gravity_num_substeps": 5,
            "stiffness_factors": {
                "beam": 0.35,
                "column": 0.70,
                "brace": 0.50,
                "wall": 0.70,
                "slab": 0.25,
            },
        },
    )
    b.build_domain()

    raw_edges = find_constraint_edges(
        b.mesh_model.area_elements,
        b.mesh_model.area_assignments,
        b.mesh_model.nodes,
        frame_elements=b.mesh_model.frame_elements,
        frame_assignments=b.mesh_model.frame_assignments,
    )
    if raw_edges:
        coarse_pairs = []
        fine_nodes = []
        for nids, master_chain, slave_nodes, *_ in raw_edges:
            # Master edge = first and last node of the coarsest chain
            if len(master_chain) >= 2:
                m1 = b.mesh_model.nodes[master_chain[0][0]].node_tag
                m2 = b.mesh_model.nodes[master_chain[-1][0]].node_tag
                if m1 is not None and m2 is not None and m1 != m2:
                    coarse_pairs.append((m1, m2))
            # Slave nodes = all non-master nodes
            for nid, _ in slave_nodes:
                tag = b.mesh_model.nodes[nid].node_tag
                if tag is not None:
                    fine_nodes.append(tag)
        b.apply_edge_constraints(coarse_edges=coarse_pairs, fine_nodes=fine_nodes or None)
    return b


def run_linear_cases(
    md: SAPModelData,
    cases: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run static analysis for each load case individually via the builder.

    Returns a dict keyed by case name, each with displacements,
    reactions, element_forces, and load_totals.
    """
    if cases is None:
        cases = _auto_detect_cases(md)
    if not cases:
        print("  (no static load cases found)")
        return {}

    results: Dict[str, Any] = {}
    for case in cases:
        b = _build_and_constrain(md)
        try:
            # Derive pattern→scale mapping from case_data if available
            pattern_scales = {"DEAD": 1.0}
            if hasattr(md, "load_cases") and md.load_cases:
                lc = md.load_cases.get(case) if isinstance(md.load_cases, dict) else None
                if lc and hasattr(lc, "case_data") and lc.case_data:
                    # Only include entries whose values are directly
                    # convertible to float — structured data (modal/RS
                    # case definitions with nested dicts) is skipped.
                    pattern_scales = (
                        {
                            k: float(v)
                            for k, v in lc.case_data.items()
                            if isinstance(v, (str, int, float))
                        }
                        if isinstance(lc.case_data, dict)
                        else {"DEAD": 1.0}
                    )
            results[case] = b.run_static_analysis(pattern_scales=pattern_scales)
        finally:
            import openseespy.opensees as ops

            ops.wipe()
    return results


def run_modal(
    md: SAPModelData,
    num_modes: int = 12,
    extract_shapes: bool = False,
    eigen_solver: str = "genBandArpack",
) -> Dict[str, Any]:
    """Run eigenvalue modal analysis via the builder.

    Parameters
    ----------
    md : SAPModelData
    num_modes : int
        Number of eigenvalues to solve for.
    extract_shapes : bool
        If True, also extract nodal eigenvectors (mode shapes) by keeping
        the builder alive.  The returned dict includes keys ``'builder'``
        and ``'mode_shapes'`` for later visualisation with
        :func:`~fea_toolkit.plotting.plot_mode_animation`.
    eigen_solver : str
        Solver passed to ``builder.run_modal_analysis()``.
        ``"genBandArpack"`` (default) — generalised banded ARPACK with
        a Ritz gravity pre-step for a non-zero starting vector.
        ``"symmBandLapack"`` — symmetric banded Lapack.
        ``"default"`` — plain ARPACK, fallback to fullGenLapack.

    Returns
    -------
    dict with keys: eigenvalues, periods, frequencies, modal_props, num_modes,
    and (if *extract_shapes*) builder, mode_shapes.
    """
    b = _build_and_constrain(md)
    try:
        result = b.run_modal_analysis(num_modes=num_modes, eigen_solver=eigen_solver)
        if extract_shapes:
            shapes = b.extract_mode_shapes(num_modes)
            result["builder"] = b
            result["mode_shapes"] = shapes
        return result
    finally:
        if not extract_shapes:
            import openseespy.opensees as ops

            ops.wipe()


def run_rs(
    md: SAPModelData,
    modal_result: Dict[str, Any],
    direction: str = "X",
    T_rigid: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Compute CQC-combined response spectrum base shear from modal results.

    Post-processes modal participation factors against a GB 50011 spectrum
    via :func:`fea_toolkit.spectrum.cqc_combine`.  No OpenSees analysis
    is needed — this is purely arithmetic on the modal properties returned
    by :meth:`AnalysisBuilder.run_modal_analysis`.
    """
    from fea_toolkit.spectrum import cqc_combine as _cqc

    mp = modal_result.get("modal_props", {})
    if not mp:
        return None

    periods = list(modal_result.get("periods", []))
    n_modes = len(periods)
    if n_modes == 0:
        return None

    # Build spectrum and interpolation helper
    spec_cfg = {
        "intensity": 7,
        "acceleration": 0.10,
        "site_class": "I1",
        "level": "rare",
        "damping": 0.05,
    }
    T_spec, Sa_spec, _, _, _, _ = _build_spectrum(spec_cfg)

    def _sa_fn(T: float) -> float:
        return float(np.interp([T], T_spec, Sa_spec, left=Sa_spec[0], right=Sa_spec[-1])[0])

    mass_key = f"partiMassM{direction}"
    eff_masses = mp.get(mass_key, [0.0] * n_modes)[:n_modes]
    total_mass = mp.get("totalFreeMass", [0.0])[0]

    result = _cqc(
        eff_masses=eff_masses,
        periods=periods,
        spectrum_fn=_sa_fn,
        damping=0.05,
        T_rigid=T_rigid,
        total_mass=total_mass,
    )
    if result:
        result["direction"] = direction
        v = result
        print(
            f"  RS-{direction}: V_cqc={v['base_shear_cqc']:.1f}  "
            f"V_rigid_cutoff={v['base_shear_rigid_cutoff']:.1f}  "
            f"V_missing={v['base_shear_missing_mass']:.1f}  "
            f"V_total={v['base_shear_total']:.1f} kN  "
            f"(ΣM_eff/M_total={v['captured_mass']:.0f}/"
            f"{v['total_mass']:.0f} t = {v['participation_ratio']:.1%})"
        )
    return result


# ═══════════════════════════════════════════════════════════════════
# 7. Mode-shape visualisation (PyVista)
# ═══════════════════════════════════════════════════════════════════


def visualize_mode_shapes(
    builder,
    mode_shapes: Dict[int, Dict[int, tuple]],
    modal_result: Dict[str, Any],
    mode_indices: Optional[List[int]] = None,
    scale: float = 50.0,
    out_dir: str = "examples/output",
    save_gif: bool = False,
) -> None:
    """Show or animate mode shapes via PyVista.

    When called from a terminal (non‑notebook), opens an interactive
    PyVista window per mode.  Each window oscillates the mode shape
    sinusoidally.

    Parameters
    ----------
    builder : AnalysisBuilder
        Built builder (from ``run_modal(..., extract_shapes=True)``).
    mode_shapes : dict
        ``{mode_index: {node_tag: (dx, dy, dz)}}``.
    modal_result : dict
        Output of ``run_modal`` (for periods list).
    mode_indices : list of int, optional
        0‑based mode indices to show.  ``None`` = all modes.
    scale : float
        Displacement magnification factor.
    out_dir : str
        Output directory for saved files.
    save_gif : bool
        If True, save an animated GIF per mode instead of showing
        an interactive window.
    """
    try:
        from fea_toolkit.plotting import plot_mode_animation
    except ImportError:
        print("PyVista not available.  Install with: pip install pyvista")
        return

    periods = modal_result.get("periods", [])
    n_modes = len(mode_shapes)
    indices = mode_indices if mode_indices is not None else list(range(n_modes))

    if save_gif:
        import pyvista as pv
        import math as _math

        pv.set_plot_theme("document")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

    for idx in indices:
        if idx not in mode_shapes:
            print(f"  Skipping mode {idx} (no shape data)")
            continue

        if save_gif:
            _save_mode_gif(builder, mode_shapes, idx, periods, scale, out)
        else:
            plot_mode_animation(
                builder,
                mode_shapes,
                mode=idx,
                scale=scale,
                animate=True,
                periods=periods,
            )


def _save_mode_gif(builder, mode_shapes, mode_idx, periods, scale, out_dir):
    """Record a single mode animation as a GIF file (frames + shells)."""
    import pyvista as pv
    import math as _math
    import imageio

    disp = mode_shapes[mode_idx]
    pv.set_plot_theme("document")

    # ── Collect shell quads ───────────────────────────────────────
    shell_quads = []  # (p1, p2, p3, p4, d1, d2, d3, d4)
    for aid, area in builder.mesh_model.area_elements.items():
        if getattr(area, "inactive", False):
            continue
        if len(area.node_ids) < 3:
            continue
        nids = area.node_ids[:4]  # at most 4 for a quad
        pts = []
        ds = []
        for nid in nids:
            nd = builder.mesh_model.nodes.get(nid)
            if nd is None:
                break
            tag = nd.node_tag
            pts.append(np.array([nd.x, nd.y, nd.z]))
            ds.append(np.array(disp.get(tag, (0, 0, 0))))
        if len(pts) == 4:
            shell_quads.append(pts + ds)

    # ── Collect frame segments ────────────────────────────────────
    elements = builder.mesh_model.frame_elements
    segments = []
    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = builder.mesh_model.nodes.get(elem.node_i)
        nj = builder.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        di = disp.get(ni.node_tag, (0, 0, 0))
        dj = disp.get(nj.node_tag, (0, 0, 0))
        segments.append(
            (
                np.array([ni.x, ni.y, ni.z]),
                np.array([nj.x, nj.y, nj.z]),
                np.array(di),
                np.array(dj),
            )
        )

    # Pre-compute stable frame point counts from undeformed length
    _seg_npoints = [max(2, int(np.linalg.norm(p2 - p1) * 2)) for p1, p2, _, _ in segments]

    # Pre-compute triangulation for each shell quad
    _shell_tris = [([3, 0, 1, 2], [3, 0, 2, 3]) for _ in shell_quads]
    _shell_sec_idxs = [0] * len(shell_quads)  # uniform — no section coloring

    from fea_toolkit.plotting.viz import _build_deformed_mesh, _set_isometric_view

    plotter = pv.Plotter(off_screen=True, window_size=[1200, 800])

    # Undeformed shells (light grey, translucent)
    if shell_quads:
        _, undeformed_shells = _build_deformed_mesh(
            segments,
            _seg_npoints,
            shell_quads,
            _shell_tris,
            _shell_sec_idxs,
            scale,
            0.0,
        )
        if undeformed_shells is not None and undeformed_shells.n_points:
            plotter.add_mesh(
                undeformed_shells, color="lightgrey", opacity=0.3, show_edges=True, line_width=1
            )

    # Undeformed frames (grey lines) — individual segments
    for p1, p2, _, _ in segments:
        n = max(2, int(np.linalg.norm(p2 - p1) * 2))
        poly = pv.lines_from_points(np.linspace(p1, p2, n))
        plotter.add_mesh(poly, color="#999999", line_width=1, opacity=0.5)

    # Deformed shell (red, translucent)
    shell_mesh = pv.PolyData()
    if shell_quads:
        _, shell_mesh = _build_deformed_mesh(
            segments,
            _seg_npoints,
            shell_quads,
            _shell_tris,
            _shell_sec_idxs,
            scale,
            0.0,
        )
        if shell_mesh.n_points:
            plotter.add_mesh(
                shell_mesh, color="#c44e52", opacity=0.5, show_edges=True, line_width=1
            )

    # Deformed frames (red lines)
    frame_mesh, _ = _build_deformed_mesh(
        segments,
        _seg_npoints,
        shell_quads,
        _shell_tris,
        _shell_sec_idxs,
        scale,
        0.0,
    )
    if frame_mesh.n_points:
        plotter.add_mesh(frame_mesh, color="#c44e52", line_width=2)

    period_str = f"T = {periods[mode_idx]:.4f} s" if mode_idx < len(periods) else ""
    plotter.add_text(f"Mode {mode_idx + 1}  {period_str}", position="upper_edge", font_size=16)
    plotter.show_grid()
    _set_isometric_view(plotter)
    plotter.render()

    n_frames = 60
    gif_path = out_dir / f"mode_{mode_idx + 1}.gif"
    with imageio.get_writer(gif_path, mode="I", duration=1000 / 15, loop=0) as writer:
        for i in range(n_frames):
            amp = _math.sin(2.0 * _math.pi * i / n_frames)
            nfm, nsm = _build_deformed_mesh(
                segments,
                _seg_npoints,
                shell_quads,
                _shell_tris,
                _shell_sec_idxs,
                scale,
                amp,
            )
            if nfm is not None and nfm.n_points:
                frame_mesh.points = nfm.points
            if shell_quads and nsm is not None and nsm.n_points:
                shell_mesh.points = nsm.points
            plotter.render()
            img = plotter.screenshot(return_img=True)
            writer.append_data(img)
    plotter.close()
    print(f"  Saved {gif_path}")


# ═══════════════════════════════════════════════════════════════════
# 8. Cache — save/load modal results for fast re‑visualisation
# ═══════════════════════════════════════════════════════════════════

CACHE_DIR = Path("examples/output")
CACHE_NPZ = CACHE_DIR / "results.npz"


def _build_cache_data(modal_result: Dict[str, Any], md: SAPModelData) -> Dict[str, Any]:
    """Extract data needed for visualisation into a cacheable dict.

    Strips out the builder (OpenSees state) and keeps only what
    ``_visualize_from_cache`` needs.
    """
    shapes = modal_result.get("mode_shapes", {})
    # Convert mode_shapes to flat arrays for NPZ compatibility:
    #   shapes_arr[mode, node_idx, dof] = displacement
    node_tags = sorted(
        modal_result.get("builder", object).mesh_model.nodes if modal_result.get("builder") else []
    )
    node_coords = {}
    if node_tags and modal_result.get("builder"):
        for nid_str in node_tags:
            nd = modal_result["builder"].mesh_model.nodes.get(nid_str)
            if nd:
                node_coords[nd.node_tag] = (nd.x, nd.y, nd.z)

    # Save frame + shell connectivity for mesh reconstruction
    b = modal_result.get("builder")
    frame_conn = []
    if b:
        elems = b.mesh_model.frame_elements
        for eid, elem in elems.items():
            if getattr(elem, "inactive", False):
                continue
            ni = b.mesh_model.nodes.get(elem.node_i)
            nj = b.mesh_model.nodes.get(elem.node_j)
            if ni and nj:
                frame_conn.append((ni.node_tag, nj.node_tag))

    shell_quads = []
    shell_sections = []
    for aid, area in md.area_elements.items():
        if getattr(area, "inactive", False):
            continue
        if len(area.node_ids) < 4:
            continue
        tags = []
        for nid in area.node_ids[:4]:
            nd = md.nodes.get(nid)
            if nd is None:
                break
            tags.append(nd.node_tag)
        if len(tags) == 4:
            shell_quads.append(tuple(tags))
            sec_name = md.area_assignments.get(aid, "unknown")
            shell_sections.append(sec_name)

    return {
        "periods": modal_result.get("periods", []),
        "frequencies": modal_result.get("frequencies", []),
        "eigenvalues": modal_result.get("eigenvalues", []),
        "modal_props": modal_result.get("modal_props", {}),
        "num_modes": modal_result.get("num_modes", 0),
        "node_coords": node_coords,
        "frame_conn": frame_conn,
        "shell_quads": shell_quads,
        "shell_sections": shell_sections,
        "mode_shapes": shapes,
    }


def save_cache(
    modal_result: Dict[str, Any],
    md: SAPModelData,
    static_results: Optional[Dict] = None,
    rs_x: Optional[Dict] = None,
    rs_y: Optional[Dict] = None,
) -> None:
    """Save modal results + mode shapes + mesh data to a unified NPZ cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    from fea_toolkit.io.npz_writer import write_results_npz

    rs_results = {"rs_x": rs_x, "rs_y": rs_y} if rs_x or rs_y else None
    path = write_results_npz(
        str(CACHE_NPZ),
        md=md,
        static_results=static_results,
        modal_result=modal_result,
        mode_shapes=modal_result.get("mode_shapes"),
        rs_results=rs_results,
    )
    print(f"  Cached to {path}")


def load_cache() -> Optional[Dict[str, Any]]:
    """Load cached results for visualisation from unified NPZ file."""
    if not CACHE_NPZ.exists():
        # Fallback: old pickle format
        old_pkl = CACHE_DIR / "modal_results.pkl"
        if old_pkl.exists():
            import pickle as _pickle

            with open(old_pkl, "rb") as f:
                data = _pickle.load(f)
            old_npz = CACHE_DIR / "mode_shapes.npz"
            if old_npz.exists():
                npz_obj = np.load(old_npz)
                shapes = {}
                for key in npz_obj:
                    if key.endswith("_tags"):
                        continue
                    midx = int(key.replace("mode_", ""))
                    t_key = f"{key}_tags"
                    tags = npz_obj[t_key]
                    arr = npz_obj[key]
                    sv = {int(t): tuple(arr[j]) for j, t in enumerate(tags)}
                    shapes[midx] = sv
                data["mode_shapes"] = shapes
                npz_obj.close()
            return data
        print("  No cache found — run with --cache first")
        return None

    from fea_toolkit.io.npz_reader import read_results_npz

    data = read_results_npz(str(CACHE_NPZ))

    n_modes = len(data.get("modal/period", []))
    n_nodes = len(data.get("node_tag", []))
    n_frames = len(data.get("frame_eid", []))
    n_shells = len(data.get("shell_eid", []))
    print(
        f"  Loaded cache: {n_modes} modes"
        f"  |  {n_nodes} nodes  |  {n_frames} frames"
        f"  |  {n_shells} shells"
    )

    # Backward-compat keys for visualize_from_cache etc.
    nid = data.get("node_tag", np.array([])).tolist()
    mode_dx = data.get("modal/mode_dx")
    mode_shapes = {}
    if mode_dx is not None and hasattr(mode_dx, "shape") and mode_dx.ndim > 1:
        for midx in range(mode_dx.shape[1]):
            node_vals = {}
            for j, tag in enumerate(nid):
                node_vals[int(tag)] = (
                    float(mode_dx[j, midx]),
                    float(data["modal/mode_dy"][j, midx]),
                    float(data["modal/mode_dz"][j, midx]),
                )
            mode_shapes[midx] = node_vals
    data["mode_shapes"] = mode_shapes

    data["node_coords"] = {
        int(tag): (float(data["node_x"][j]), float(data["node_y"][j]), float(data["node_z"][j]))
        for j, tag in enumerate(nid)
    }
    fi = data.get("frame_node_i", [])
    fj = data.get("frame_node_j", [])
    data["frame_conn"] = [(int(fi[j]), int(fj[j])) for j in range(len(fi))]

    shell_quads = []
    for j in range(len(data.get("shell_eid", []))):
        shell_quads.append(
            (
                int(data["shell_node_1"][j]),
                int(data["shell_node_2"][j]),
                int(data["shell_node_3"][j]),
                int(data["shell_node_4"][j]),
            )
        )
    data["shell_quads"] = shell_quads

    data["periods"] = data.get("modal/period", []).tolist()
    data["frequencies"] = data.get("modal/frequency", []).tolist()
    data["num_modes"] = n_modes

    mp = {}
    for key, npz_key in [
        ("partiMassRatiosMX", "modal/mx_ratio"),
        ("partiMassRatiosMY", "modal/my_ratio"),
        ("partiMassRatiosMZ", "modal/mz_ratio"),
        ("partiMassMX", "modal/mx_eff"),
        ("partiMassMY", "modal/my_eff"),
        ("partiMassMZ", "modal/mz_eff"),
    ]:
        arr = data.get(npz_key)
        if arr is not None:
            mp[key] = arr.tolist()
    data["modal_props"] = mp

    return data


# Colour palette for shell section groups (matches viz.py)
_SECTION_COLORS_CACHE = [
    "#4c72b0",
    "#dd8452",
    "#55a868",
    "#c44e52",
    "#8172b3",
    "#937860",
    "#da8bc3",
    "#8c8c8c",
    "#ccb974",
    "#64b5cd",
]


def _make_plotter_from_cache(data: Dict[str, Any], mode_idx: int, scale: float):
    """Build a PyVista plotter from cached mesh/shape data (no builder needed)."""
    import pyvista as pv

    pv.set_plot_theme("document")

    shapes = data.get("mode_shapes", {})
    disp = shapes.get(mode_idx, {})
    coords = data.get("node_coords", {})
    frame_conn = data.get("frame_conn", [])
    shell_quads = data.get("shell_quads", [])
    shell_sections = data.get("shell_sections", [])

    periods = data.get("periods", [])
    period_str = f"T = {periods[mode_idx]:.4f} s" if mode_idx < len(periods) else ""

    # Frame segments
    segments = []
    for ni_tag, nj_tag in frame_conn:
        pi = coords.get(ni_tag)
        pj = coords.get(nj_tag)
        if pi is None or pj is None:
            continue
        di = disp.get(ni_tag, (0, 0, 0))
        dj = disp.get(nj_tag, (0, 0, 0))
        segments.append(
            (
                np.array(pi),
                np.array(pj),
                np.array(di),
                np.array(dj),
            )
        )

    # Group shell quads by section name
    # quads_by_sec[sec_name] = [(p1,p2,p3,p4,d1,d2,d3,d4), ...]
    quads_by_sec: Dict[str, list] = {}
    for idx, tags in enumerate(shell_quads):
        pts = []
        ds = []
        ok = True
        for tag in tags:
            c = coords.get(tag)
            if c is None:
                ok = False
                break
            pts.append(np.array(c))
            ds.append(np.array(disp.get(tag, (0, 0, 0))))
        if ok and len(pts) == 4:
            sec = shell_sections[idx] if idx < len(shell_sections) else "unknown"
            quads_by_sec.setdefault(sec, []).append(pts + ds)
    _sec_names = sorted(quads_by_sec.keys())
    _sec_colors = {
        name: _SECTION_COLORS_CACHE[i % len(_SECTION_COLORS_CACHE)]
        for i, name in enumerate(_sec_names)
    }

    # Pre-compute stable frame line point counts from undeformed length
    _seg_npoints = []
    for p1, p2, _, _ in segments:
        n = max(2, int(np.linalg.norm(p2 - p1) * 2))
        _seg_npoints.append(n)

    # Per-section shell mesh builders (used for animation)
    _shell_fn_by_sec: Dict[str, callable] = {}
    _shell_mesh_by_sec: Dict[str, pv.PolyData] = {}
    for sec_name, quads in quads_by_sec.items():

        def _make_sec_shell(amp, qs=quads):
            all_pts, all_faces = [], []
            offset = 0
            for q in qs:
                p1, p2, p3, p4, d1, d2, d3, d4 = q
                a1 = p1 + d1 * scale * amp
                a2 = p2 + d2 * scale * amp
                a3 = p3 + d3 * scale * amp
                a4 = p4 + d4 * scale * amp
                all_pts.extend([a1, a2, a3, a4])
                all_faces.append([3, offset, offset + 1, offset + 2])
                all_faces.append([3, offset, offset + 2, offset + 3])
                offset += 4
            if not all_pts:
                return pv.PolyData()
            return pv.PolyData(np.vstack(all_pts), np.array(all_faces, dtype=int))

        _shell_fn_by_sec[sec_name] = _make_sec_shell
        _shell_mesh_by_sec[sec_name] = _make_sec_shell(0.0)

    from fea_toolkit.plotting.viz import _build_deformed_mesh, _set_isometric_view

    plotter = pv.Plotter(off_screen=False, window_size=[1200, 800])

    # Undeformed shells (light grey, translucent)
    for sec_name, quads in quads_by_sec.items():
        undeformed = _shell_fn_by_sec[sec_name](0.0)
        plotter.add_mesh(undeformed, color="lightgrey", opacity=0.3, show_edges=True, line_width=1)

    # Undeformed frames (grey lines)
    for p1, p2, _, _ in segments:
        n = max(2, int(np.linalg.norm(p2 - p1) * 2))
        poly = pv.lines_from_points(np.linspace(p1, p2, n))
        plotter.add_mesh(poly, color="#999999", line_width=1, opacity=0.5)

    # Deformed shells — per-section colour
    has_shells = bool(quads_by_sec)
    for sec_name in _sec_names:
        color = _sec_colors[sec_name]
        sm = _shell_mesh_by_sec[sec_name]
        plotter.add_mesh(sm, color=color, opacity=0.5, show_edges=True, line_width=1)

    # Deformed frames (red lines) — use shared helper
    fm, _ = _build_deformed_mesh(
        segments,
        _seg_npoints,
        [],
        [],
        [],
        scale,
        0.0,
    )
    if fm is not None and fm.n_points:
        plotter.add_mesh(fm, color="#c44e52", line_width=2)

    # Legend for shell section colours
    if len(_sec_names) > 1:
        from pyvista import Color as pvColor

        legend_entries = [(name, pvColor(_sec_colors[name])) for name in _sec_names]
        plotter.add_legend(
            legend_entries,
            border=True,
            size=[0.2, 0.12],
            loc="lower_right",
            face="white",
        )

    plotter.add_text(f"Mode {mode_idx + 1}  {period_str}", position="upper_edge", font_size=16)
    plotter.show_grid()
    _set_isometric_view(plotter)

    # Closure for frame mesh animation (invariant point count)
    _segments = segments
    __seg_npoints = _seg_npoints

    def _frame_fn(amp, _s=scale, _seg=_segments, _sn=__seg_npoints):
        fm, _ = _build_deformed_mesh(_seg, _sn, [], [], [], _s, amp)
        return fm

    return plotter, fm, _shell_mesh_by_sec, _frame_fn, _shell_fn_by_sec, has_shells


def visualize_from_cache(
    mode_indices: Optional[List[int]] = None,
    scale: float = 50.0,
    save_gif: bool = False,
    out_dir: str = "examples/output",
) -> None:
    """Load cached modal results and animate mode shapes.

    No OpenSees analysis needed — uses the NPZ/PKL cache saved by
    ``run_all(..., cache=True)``.
    """
    data = load_cache()
    if data is None:
        return

    shapes = data.get("mode_shapes", {})
    if not shapes:
        print("  No mode shapes in cache — re-run with --cache")
        return

    import pyvista as pv

    n_modes = len(shapes)
    indices = mode_indices if mode_indices is not None else sorted(shapes.keys())

    for idx in indices:
        if idx not in shapes:
            print(f"  Skipping mode {idx} (not in cache)")
            continue

        if save_gif:
            from fea_toolkit.plotting.viz import _build_deformed_mesh, _set_isometric_view
            import math as _math, imageio

            pv.set_plot_theme("document")

            coords = data.get("node_coords", {})
            disp = shapes[idx]
            frame_conn = data.get("frame_conn", [])
            shell_quads_in = data.get("shell_quads", [])
            periods = data.get("periods", [])
            period_str = f"T = {periods[idx]:.4f} s" if idx < len(periods) else ""
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            gif_path = out / f"mode_{idx + 1}.gif"

            segments = []
            for ni_tag, nj_tag in frame_conn:
                pi = coords.get(ni_tag)
                pj = coords.get(nj_tag)
                if pi is None or pj is None:
                    continue
                di = disp.get(ni_tag, (0, 0, 0))
                dj = disp.get(nj_tag, (0, 0, 0))
                segments.append(
                    (
                        np.array(pi),
                        np.array(pj),
                        np.array(di),
                        np.array(dj),
                    )
                )

            quads = []
            for tags in shell_quads_in:
                pts, ds = [], []
                ok = True
                for tag in tags:
                    c = coords.get(tag)
                    if c is None:
                        ok = False
                        break
                    pts.append(np.array(c))
                    ds.append(np.array(disp.get(tag, (0, 0, 0))))
                if ok and len(pts) == 4:
                    quads.append(pts + ds)

            # Pre-compute stable geometry
            _seg_npoints = [max(2, int(np.linalg.norm(p2 - p1) * 2)) for p1, p2, _, _ in segments]
            _shell_tris = [([3, 0, 1, 2], [3, 0, 2, 3]) for _ in quads]
            _shell_sec_idxs = [0] * len(quads)

            if not segments and not quads:
                print(f"  Mode {idx + 1}: no matching geometry to render")
                continue

            p = pv.Plotter(off_screen=True, window_size=[1200, 800])
            if quads:
                _, us = _build_deformed_mesh(
                    segments,
                    _seg_npoints,
                    quads,
                    _shell_tris,
                    _shell_sec_idxs,
                    scale,
                    0.0,
                )
                if us is not None and us.n_points:
                    p.add_mesh(us, color="lightgrey", opacity=0.3, show_edges=True, line_width=1)
            for p1, p2, _, _ in segments:
                n = max(2, int(np.linalg.norm(p2 - p1) * 2))
                p.add_mesh(
                    pv.lines_from_points(np.linspace(p1, p2, n)),
                    color="#999999",
                    line_width=1,
                    opacity=0.5,
                )

            fm, sm = _build_deformed_mesh(
                segments,
                _seg_npoints,
                quads,
                _shell_tris,
                _shell_sec_idxs,
                scale,
                0.0,
            )
            if sm is not None and sm.n_points:
                p.add_mesh(sm, color="#c44e52", opacity=0.5, show_edges=True, line_width=1)
            if fm is not None and fm.n_points:
                p.add_mesh(fm, color="#c44e52", line_width=2)

            p.add_text(f"Mode {idx + 1}  {period_str}", position="upper_edge", font_size=16)
            p.show_grid()
            _set_isometric_view(p)
            p.render()

            n_frames = 60
            with imageio.get_writer(gif_path, mode="I", duration=1000 / 15, loop=0) as w:
                for i in range(n_frames):
                    amp = _math.sin(2.0 * _math.pi * i / n_frames)
                    nfm, nsm = _build_deformed_mesh(
                        segments,
                        _seg_npoints,
                        quads,
                        _shell_tris,
                        _shell_sec_idxs,
                        scale,
                        amp,
                    )
                    if nfm is not None and nfm.n_points:
                        fm.points = nfm.points
                    if nsm is not None and nsm.n_points:
                        sm.points = nsm.points
                    p.render()
                    w.append_data(p.screenshot(return_img=True))
            p.close()
            print(f"  Saved {gif_path}")
        else:
            plotter, fm, sm_by_sec, fm_fn, sm_fn_by_sec, has_shells = _make_plotter_from_cache(
                data, idx, scale
            )

            import math as _math

            step_counter = [0]

            def callback(step):
                step_counter[0] = step
                amp = _math.sin(2.0 * _math.pi * step / 60.0)
                fm.points = fm_fn(amp).points
                if has_shells:
                    for sec_name, sm in sm_by_sec.items():
                        sm.points = sm_fn_by_sec[sec_name](amp).points
                plotter.render()

            plotter.add_timer_event(600, 30, callback)
            plotter.show()


# ═══════════════════════════════════════════════════════════════════
# 9. Plotting (static figures)
# ═══════════════════════════════════════════════════════════════════

SPECTRUM_CFG = {
    "intensity": 7,
    "acceleration": 0.10,
    "site_class": "I1",
    "level": "rare",
    "damping": 0.05,
}


def _df_modal_from_results(modal_result: Dict[str, Any]) -> Optional[pd.DataFrame]:
    """Build a modal participation DataFrame from existing analysis results.

    Delegates to :func:`fea_toolkit.io.report.modal_participation_df`.
    """
    from fea_toolkit.io.report import modal_participation_df as _lib_df

    return _lib_df(modal_result)


def save_modal_table(
    modal_result: Dict[str, Any],
    out_dir: str = "examples/output",
) -> Optional[str]:
    """Build an HTML modal-participation table from existing results and save it.

    Delegates to :func:`fea_toolkit.io.report.save_modal_participation_html`.
    """
    from fea_toolkit.io.report import save_modal_participation_html as _lib_save

    return _lib_save(modal_result, out_dir=out_dir)


def plot_results(
    modal_result: Dict[str, Any],
    rs_x: Optional[Dict[str, Any]] = None,
    rs_y: Optional[Dict[str, Any]] = None,
    out_dir: str = "examples/output",
) -> Dict[str, str]:
    """Generate modal participation, spectrum, and RS plots.

    Saves PNG files to *out_dir* and returns a dict mapping plot name
    to file path.
    """
    from fea_toolkit.plotting.report import (
        plot_modal_participation,
        plot_rs_modal_analysis,
    )
    from fea_toolkit.spectrum import plot_seismic_spectrum
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    saved = {}

    # ── 1. Modal participation bar chart ──────────────────────────
    df_modal = _df_modal_from_results(modal_result)
    if df_modal is not None:
        fig = plot_modal_participation(df_modal)
        if fig:
            path = out / "modal_participation.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            saved["modal_participation"] = str(path)
            print(f"  Saved {path}")

    # ── 2. Seismic spectrum ───────────────────────────────────────
    fig = plot_seismic_spectrum(SPECTRUM_CFG, modal_result)
    if fig:
        path = out / "seismic_spectrum.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        saved["seismic_spectrum"] = str(path)
        print(f"  Saved {path}")

    # ── 3. RS modal base shear ────────────────────────────────────
    sx = rs_x.get("modal_base_shear", []) if rs_x else []
    sy = rs_y.get("modal_base_shear", []) if rs_y else []
    fig = plot_rs_modal_analysis(
        modal_result.get("modal_props", {}),
        sx,
        sy,
        periods=modal_result.get("periods"),
        base_shear_rigid_x=rs_x.get("base_shear_rigid", 0) if rs_x else 0,
        base_shear_rigid_y=rs_y.get("base_shear_rigid", 0) if rs_y else 0,
        T_rigid=rs_x.get("T_rigid") if rs_x else None,
    )
    if fig:
        path = out / "rs_modal_analysis.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        saved["rs_modal_analysis"] = str(path)
        print(f"  Saved {path}")

    return saved


# ═══════════════════════════════════════════════════════════════════
# 8. Orchestrator
# ═══════════════════════════════════════════════════════════════════


def model_summary(md: SAPModelData) -> pd.DataFrame:
    """Return a one-row summary DataFrame of the model.

    Delegates to :meth:`SAPModelData.summary_dict`.
    """
    return pd.DataFrame([md.summary_dict()])


def _safe_summary(func, md):
    """Run a summary function, returning an empty DataFrame if the table is absent."""
    try:
        return func(md)
    except (KeyError, ValueError):
        return pd.DataFrame()


def run_all(
    s2k_path: str,
    md: Optional[SAPModelData] = None,
    num_modes: int = 32,
    skip_analysis: bool = False,
    extract_shapes: bool = False,
    visualize_modes: Union[bool, List[int]] = False,
    save_gif: bool = False,
    eigen_solver: str = "genBandArpack",
    cache: bool = False,
) -> Dict[str, Any]:
    """Run all analyses and return compiled results dict.

    *s2k_path* may be ``None`` when a pre-parsed model is supplied via *md*
    (e.g. the ``--sample`` path).

    Returns
    -------
    dict with keys: md, raw, model_summary, sections, area_sections,
    materials, mass_sources, load_patterns, load_cases,
    load_totals, orphan_mass, preprocessing, static, modal, rs_x, rs_y
    """
    print(f"FEA Toolkit v{__version__}  |  OpenSees {ops_version()}")
    if md is None:
        print(f"Loading model: {s2k_path}\n")
        md, raw = load_model(s2k_path)
    else:
        raw = {}
        print("Using provided model (no .s2k file loaded)\n")
    print(
        f"  Nodes: {len(md.nodes)}  |  Frames: {len(md.frame_elements)}"
        f"  |  Areas: {len(md.area_elements)}"
    )
    print(f"  Materials: {len(md.materials)}  |  Sections: {len(md.sections)}")
    print(f"  Units: {md.units}\n")

    results: Dict[str, Any] = {
        "md": md,
        "raw": raw,
        "model_summary": model_summary(md),
    }

    # ── Summaries ─────────────────────────────────────────────────
    results["sections"] = section_summary(md)
    results["area_sections"] = area_section_summary(md)
    results["materials"] = material_summary(md)
    results["mass_sources"] = summarise_mass_sources(md)
    results["load_patterns"] = summarise_load_patterns(md)
    results["load_cases"] = _safe_summary(summarise_load_cases, md)
    results["load_totals"] = _safe_summary(load_pattern_totals, md)

    print("--- Model summaries ---")
    print(results["model_summary"].to_string(index=False))
    print(
        f"  Sections: {len(results['sections'])}"
        f"  |  Area sections: {len(results['area_sections'])}"
        f"  |  Materials: {len(results['materials'])}"
    )

    if results["mass_sources"] is not None and not results["mass_sources"].empty:
        print(f"\n  Mass sources: {len(results['mass_sources'])} rows")

    # ── Remove floating nodes (redistribute mass & loads) ─────────
    print("\n--- Floating node check ---")
    floated = remove_floating_nodes(md)
    results["floating_nodes"] = floated
    if len(floated) > 0:
        print(f"  ⚠ {len(floated)} floating node(s) removed with redistribution:")
        for _, row in floated.iterrows():
            details = []
            if row.get("mass_source"):
                details.append("mass source active")
            if row.get("loads"):
                details.append(f"loads={row['loads']}")
            if row.get("restrained"):
                details.append("restrained")
            print(
                f"    Node {row['node_id']} ({', '.join(details)})"
                f"  → {row['nearest_node']}  (d={row['distance']:.3f})"
            )
    else:
        print("  ✓ No floating nodes found")

    if skip_analysis:
        results["static"] = pd.DataFrame()
        results["modal"] = {}
        results["rs_x"] = None
        results["rs_y"] = None
        return results

    # ── Pre-processing ────────────────────────────────────────────
    print("\n--- Pre-processing (mesh, split, constraints) ---")
    pp = preprocess_model(md)

    # Re-check for floating nodes — meshing marks original areas as
    # inactive, leaving their corner nodes orphaned.  These nodes have
    # mass (from mass source) but no stiffness, causing a singular
    # stiffness matrix.
    floated2 = remove_floating_nodes(md)
    if len(floated2) > 0:
        results.setdefault("floating_nodes", pd.DataFrame())
        results["floating_nodes"] = pd.concat(
            [results["floating_nodes"], floated2], ignore_index=True
        )
        print(f"  ⚠ {len(floated2)} post-mesh floating node(s) removed:")
        for _, row in floated2.iterrows():
            print(f"    Node {row['node_id']} → {row['nearest_node']}")
    results["preprocessing"] = pp
    print(f"  Areas: {pp['area_elements']['before']} → {pp['area_elements']['after']}")
    print(f"  Frames: {pp['frame_elements']['before']} → {pp['frame_elements']['after']}")
    print(f"  Constraint edges: {pp['constraint_edges']}")

    # ── Static analysis ───────────────────────────────────────────
    print("\n--- Static analysis ---")
    static_cases = _auto_detect_cases(md)
    print(f"  Auto-detected cases: {static_cases}")
    static_results = run_linear_cases(md, cases=static_cases)
    results["static"] = static_results
    if static_results:
        print(f"  {len(static_results)} case(s) run")
        for case_name, case_data in static_results.items():
            rx = case_data.get("reactions", {})
            print(
                f"    {case_name}: Rx={rx.get('fx', 0):.1f}  "
                f"Ry={rx.get('fy', 0):.1f}  Rz={rx.get('fz', 0):.1f}"
            )
    else:
        print("  (no results)")

    # ── Modal analysis ────────────────────────────────────────────
    print(f"\n--- Modal analysis ({num_modes} modes) ---")
    modal = run_modal(
        md, num_modes=num_modes, extract_shapes=extract_shapes, eigen_solver=eigen_solver
    )
    results["modal"] = modal
    if modal and "periods" in modal:
        periods = modal["periods"]
        print(f"  T₁ = {periods[0]:.4f} s" if len(periods) > 0 else "")

    # ── Response spectrum ─────────────────────────────────────────
    if modal and "periods" in modal and len(modal["periods"]) > 0:
        print(f"\n--- Response spectrum analysis ---")
        for d in ("X", "Y"):
            try:
                rs = run_rs(md, modal, direction=d)
                results[f"rs_{d.lower()}"] = rs
            except Exception as exc:
                warnings.warn(f"RS-{d} failed: {exc}")
                results[f"rs_{d.lower()}"] = None

    # ── Plots & tables ────────────────────────────────────────────
    try:
        save_modal_table(modal, out_dir="examples/output")
        plots = plot_results(
            modal,
            rs_x=results.get("rs_x"),
            rs_y=results.get("rs_y"),
            out_dir="examples/output",
        )
        results["plots"] = plots
    except Exception as exc:
        warnings.warn(f"Plotting failed: {exc}")
        results["plots"] = {}

    # ── Mode-shape visualisation ──────────────────────────────────
    if visualize_modes and extract_shapes:
        b = modal.get("builder")
        shapes = modal.get("mode_shapes")
        if b is not None and shapes is not None:
            try:
                print("\n--- Mode shape visualisation ---")
                visualize_mode_shapes(
                    b,
                    shapes,
                    modal,
                    mode_indices=visualize_modes if isinstance(visualize_modes, list) else None,
                    scale=50.0,
                    out_dir="examples/output",
                    save_gif=save_gif,
                )
            except Exception as exc:
                warnings.warn(f"Mode viz failed: {exc}")
        # Clean up the builder that was kept alive for shape extraction
        import openseespy.opensees as ops

        ops.wipe()

    # ── Cache results for fast reload ─────────────────────────────
    if cache:
        try:
            save_cache(
                modal,
                md,
                static_results=results.get("static"),
                rs_x=results.get("rs_x"),
                rs_y=results.get("rs_y"),
            )
        except Exception as exc:
            warnings.warn(f"Cache save failed: {exc}")

    return results


# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Building linear analysis workflow.",
    )
    parser.add_argument(
        "s2k_file",
        nargs="?",
        help="Path to the SAP2000 .s2k file.",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Use the built‑in sample model (no external file needed).",
    )
    parser.add_argument(
        "--no-analysis",
        action="store_true",
        help="Parse and summarise only — skip OpenSees analyses.",
    )
    parser.add_argument(
        "--num-modes",
        type=int,
        default=32,
        help="Number of modes for eigenvalue analysis (default: 32).",
    )
    parser.add_argument(
        "--shapes",
        action="store_true",
        help="Extract mode shapes for visualisation.",
    )
    parser.add_argument(
        "--animate",
        action="store_true",
        help="Open interactive PyVista windows for mode shape animation.",
    )
    parser.add_argument(
        "--gif",
        action="store_true",
        help="Save mode shape animations as GIFs (requires imageio).",
    )
    parser.add_argument(
        "--mode-index",
        type=int,
        nargs="*",
        default=None,
        help="0‑based mode indices to visualise (default: all).",
    )
    parser.add_argument(
        "--solver",
        choices=["default", "genBandArpack", "symmBandLapack", "fullGenLapack", "ritz"],
        default="genBandArpack",
        help="Eigenvalue solver (default: genBandArpack with Ritz pre-step).",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Save modal results to examples/output/results.npz "
        "(mode shapes are included only when --shapes/--animate/--gif "
        "request their extraction) for fast reload with --from-cache.",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Skip analysis — load cached results and run visualisation only.",
    )
    args = parser.parse_args()

    if args.from_cache:
        # No analysis — load from cache and run visualisation
        visualize_from_cache(
            mode_indices=args.mode_index,
            save_gif=args.gif,
        )
        return

    if args.sample:
        from examples.sample_model import make_sample_model

        md = make_sample_model()
        print("Using built‑in sample model (no .s2k file needed)")
        results = run_all(
            s2k_path=None,
            md=md,
            skip_analysis=args.no_analysis,
            num_modes=args.num_modes,
            extract_shapes=False,
            eigen_solver=args.solver,
            cache=False,
        )
        return
    elif args.s2k_file:
        s2k_path = args.s2k_file
    else:
        # No hardcoded default: the model path is always user-supplied.
        parser.error("a .s2k file path (or --sample) is required")

    # Auto-enable cache when animation is requested, so subsequent
    # --from-cache calls can re-animate without re-running the analysis.
    auto_cache = args.cache or args.animate or args.gif

    results = run_all(
        s2k_path,
        num_modes=args.num_modes,
        skip_analysis=args.no_analysis,
        extract_shapes=args.shapes or args.animate or args.gif,
        visualize_modes=args.mode_index
        if args.mode_index is not None
        else (True if args.animate or args.gif else False),
        save_gif=args.gif,
        eigen_solver=args.solver,
        cache=auto_cache,
    )

    # ── Summary printout ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)

    if results.get("floating_nodes") is not None and len(results["floating_nodes"]) > 0:
        print(
            f"\n⚠ {len(results['floating_nodes'])} floating node(s) removed "
            f"— mass/loads redistributed"
        )

    if results.get("static") and len(results["static"]) > 0:
        print(f"\nStatic: {len(results['static'])} case(s) run")

    if results.get("modal") and "periods" in results["modal"]:
        print(f"Modal: {len(results['modal']['periods'])} modes")

    for d in ("x", "y"):
        rs = results.get(f"rs_{d}")
        if rs:
            print(f"RS-{d.upper()}: completed")


if __name__ == "__main__":
    main()
