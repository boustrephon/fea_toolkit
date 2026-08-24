"""Pushover and nonlinear-response visualisation.

Plastic-hinge formation/heatmaps, shell damage maps, envelopes, step
animations, force evolution, and capacity-curve plots."""

import math
import warnings
from typing import Any, Optional

import numpy as np

from ..io.results_schema import make_pushover_key
from .viz_common import (
    _DEFAULT_HINGE_CMAP,
    _NPZ_TYPES,
    _add_animation_timer,
    _add_hinge_color_legend,
    _add_shell_color_legend,
    _ratio_to_color,
    _rgb_to_hex,
    _sample_cmap,
    _set_isometric_view,
)
from .viz_forces import _render_frame_force_diagram
from .viz_model import _render_scene, _resolve_mesh_data


def _resolve_pushover_data(
    data,
    direction="+X",
) -> "tuple[list, list, list, Optional[dict]]":
    """Normalise pushover data from any source into standard Python structures.

    Accepts three input types:

    * An ``AnalysisBuilder`` instance (reads from ``pushover_step_results``).
    * An NPZ data dict (from ``np.load()`` — reads ``pushover/{direction}/...`` arrays).
    * A ``list[dict]`` — raw step results from ``_record_step()``.

    Returns:
        ``(step_results, node_coords, node_tags, frame_eid_to_nodes)`` where:
        - *step_results* — list of dicts, each with ``"frame_forces"``,
            ``"shell_forces"``, and optionally ``"node_displacements"``.
        - *node_coords* — ``{node_tag: (x, y, z)}`` dict from geometry.
        - *frame_eid_to_nodes* — ``{sap_id: (ni_tag, nj_tag)}`` or ``None``
            if not available from the source type.
    """
    if isinstance(data, _NPZ_TYPES):
        # NPZ data path
        step_results = []
        step_arr = data.get(make_pushover_key(direction, "pushover/{direction}/step"), [])
        n_step = len(step_arr)
        if n_step == 0:
            return [], {}, [], {}

        # Node coords from geometry
        node_coords = {}
        n_tags = data.get("node_tag", [])
        n_x = data.get("node_x", [])
        n_y = data.get("node_y", [])
        n_z = data.get("node_z", [])
        for i in range(len(n_tags)):
            node_coords[int(n_tags[i])] = (float(n_x[i]), float(n_y[i]), float(n_z[i]))

        # Read node displacements if available
        node_disp_x = data.get(make_pushover_key(direction, "pushover/{direction}/node_disp_x"))
        node_disp_y = data.get(make_pushover_key(direction, "pushover/{direction}/node_disp_y"))
        node_disp_z = data.get(make_pushover_key(direction, "pushover/{direction}/node_disp_z"))
        node_tags_arr = data.get(make_pushover_key(direction, "pushover/{direction}/node_tag"))

        # Read frame SAP IDs
        frame_sap_id = data.get(
            make_pushover_key(direction, "pushover/{direction}/frame_sap_id"), []
        )
        n_frame = len(frame_sap_id)

        # Read frame force arrays
        comp_keys = [
            "fx_i",
            "fy_i",
            "fz_i",
            "mx_i",
            "my_i",
            "mz_i",
            "fx_j",
            "fy_j",
            "fz_j",
            "mx_j",
            "my_j",
            "mz_j",
        ]
        frame_arrays = {}
        for key in comp_keys:
            arr = data.get(make_pushover_key(direction, f"pushover/{{direction}}/frame_{key}"))
            if arr is not None:
                frame_arrays[key] = arr

        # Read shell SAP IDs
        shell_sap_id = data.get(
            make_pushover_key(direction, "pushover/{direction}/shell_sap_id"), []
        )
        n_shell = len(shell_sap_id)
        shell_comp_keys = ["Nx", "Ny", "Nxy", "Mx", "My", "Mxy"]
        shell_arrays = {}
        for key in shell_comp_keys:
            arr = data.get(make_pushover_key(direction, f"pushover/{{direction}}/shell_{key}"))
            if arr is not None:
                shell_arrays[key] = arr

        for si in range(n_step):
            sd = {"step": int(step_arr[si]), "frame_forces": {}, "shell_forces": {}}

            # Frame forces for this step — only iterate components that
            # were actually archived (missing ones are skipped).
            if n_frame > 0 and frame_arrays:
                for fi in range(n_frame):
                    eid = str(frame_sap_id[fi])
                    ff = {}
                    for key in comp_keys:
                        if key in frame_arrays:
                            ff[key] = float(frame_arrays[key][si, fi])
                    sd["frame_forces"][eid] = ff

            # Shell forces for this step — same guard against missing
            # archived components.
            if n_shell > 0 and shell_arrays:
                for si_shell in range(n_shell):
                    aid = str(shell_sap_id[si_shell])
                    sf = {}
                    for key in shell_comp_keys:
                        if key in shell_arrays:
                            sf[key] = float(shell_arrays[key][si, si_shell])
                    sd["shell_forces"][aid] = sf

            # Node displacements
            if (
                node_disp_x is not None
                and node_disp_y is not None
                and node_disp_z is not None
                and node_tags_arr is not None
            ):
                nd = {}
                for ni in range(len(node_tags_arr)):
                    tag = int(node_tags_arr[ni])
                    nd[tag] = (
                        float(node_disp_x[si, ni]),
                        float(node_disp_y[si, ni]),
                        float(node_disp_z[si, ni]),
                    )
                sd["node_displacements"] = nd

            step_results.append(sd)

        # Build frame_eid_to_nodes from geometry
        frame_eid_to_nodes = {}
        frame_ni = data.get("frame_node_i", [])
        frame_nj = data.get("frame_node_j", [])
        frame_sap = data.get("frame_sap_id", [])
        for i in range(len(frame_sap)):
            frame_eid_to_nodes[str(frame_sap[i])] = (int(frame_ni[i]), int(frame_nj[i]))

        return (
            step_results,
            node_coords,
            list(node_tags_arr) if node_tags_arr is not None else [],
            frame_eid_to_nodes,
        )

    if isinstance(data, list):
        # Raw step results list
        return data, {}, [], None

    # Builder/AnalysisBuilder path
    step_results = list(getattr(data, "pushover_step_results", []) or [])
    # Build node coords from mesh_model
    node_coords = {}
    mm = getattr(data, "mesh_model", None)
    if mm is not None:
        for nd in mm.nodes.values():
            node_coords[nd.node_tag] = (nd.x, nd.y, nd.z)

    # Build frame_eid_to_nodes
    frame_eid_to_nodes = {}
    if mm is not None:
        for eid, elem in mm.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is not None and nj is not None:
                frame_eid_to_nodes[eid] = (ni.node_tag, nj.node_tag)

    node_tags = sorted(node_coords.keys())
    return step_results, node_coords, node_tags, frame_eid_to_nodes


def _compute_hinge_ratios(
    frame_forces: dict[str, dict[str, float]],
    use_biaxial: bool = False,
) -> dict[str, tuple[float, float]]:
    """Compute demand/capacity hinge ratios for each frame element end.

    Uniaxial mode (default): ratio = |Mz| / max|Mz| across steps.
    Biaxial mode: ratio = sqrt((My/Mp_y)² + (Mz/Mp_z)²) where Mp_y and Mp_z
    are the peak observed My and Mz across all steps (same range-based
    normalization as the uniaxial case, applied to each axis separately).

    Returns ``{eid: (ratio_i, ratio_j)}``.
    """
    peak_mz: dict[str, float] = {}
    if use_biaxial:
        # Biaxial SRSS mode: track peak My and Mz separately per element
        peak_my: dict[str, float] = {}
        for eid, ff in frame_forces.items():
            my_i = abs(ff.get("my_i", 0.0))
            my_j = abs(ff.get("my_j", 0.0))
            mz_i = abs(ff.get("mz_i", 0.0))
            mz_j = abs(ff.get("mz_j", 0.0))
            peak_my[eid] = max(peak_my.get(eid, 0.0), my_i, my_j)
            peak_mz[eid] = max(peak_mz.get(eid, 0.0), mz_i, mz_j)

        ratios = {}
        for eid, ff in frame_forces.items():
            cap_y = peak_my.get(eid, 1e-12)
            cap_z = peak_mz.get(eid, 1e-12)
            # Guard against zero capacity in each axis separately
            safe_cap_y = max(1e-12, cap_y)
            safe_cap_z = max(1e-12, cap_z)
            my_i = abs(ff.get("my_i", 0.0))
            my_j = abs(ff.get("my_j", 0.0))
            mz_i = abs(ff.get("mz_i", 0.0))
            mz_j = abs(ff.get("mz_j", 0.0))
            r_i = math.sqrt((my_i / safe_cap_y) ** 2 + (mz_i / safe_cap_z) ** 2)
            r_j = math.sqrt((my_j / safe_cap_y) ** 2 + (mz_j / safe_cap_z) ** 2)
            ratios[eid] = (r_i, r_j)
        return ratios

    # Uniaxial mode (default): ratio = |Mz| / peak|Mz|
    for eid, ff in frame_forces.items():
        mz_i = abs(ff.get("mz_i", 0.0))
        mz_j = abs(ff.get("mz_j", 0.0))
        peak_mz[eid] = max(peak_mz.get(eid, 0.0), mz_i, mz_j)

    ratios = {}
    for eid, ff in frame_forces.items():
        peak = peak_mz.get(eid, 1e-12)
        if peak < 1e-12:
            ratios[eid] = (0.0, 0.0)
        else:
            ratios[eid] = (
                abs(ff.get("mz_i", 0.0)) / peak,
                abs(ff.get("mz_j", 0.0)) / peak,
            )
    return ratios


def _compute_hinge_ratios_all_steps(
    all_frame_forces: list[dict[str, dict[str, float]]],
    use_biaxial: bool = False,
) -> list[dict[str, tuple[float, float]]]:
    """Compute demand/capacity hinge ratios using peak capacities across all steps.

    For each element, the peak My and Mz are determined across *all*
    provided steps, then each step's forces are normalised against
    these fixed capacities.  This ensures that the colour mapping is
    consistent across the entire pushover (a ratio of 1.0 means the
    element reached its peak observed moment, regardless of which step
    that occurred at).

    Args:
        all_frame_forces: List of per-step ``{eid: {my_i, my_j, mz_i, mz_j}}``
            dicts, one entry per push step.
        use_biaxial: If True, compute SRSS of My/Mp_y and Mz/Mp_z.

    Returns:
        List of ``{eid: (ratio_i, ratio_j)}``, one per step, using
        globally-peak capacities for normalisation.
    """
    if not all_frame_forces:
        return []

    # Step 1: compute global peak capacities across all steps
    peak_my: dict[str, float] = {}
    peak_mz: dict[str, float] = {}
    for step_forces in all_frame_forces:
        for eid, ff in step_forces.items():
            my_i = abs(ff.get("my_i", 0.0))
            my_j = abs(ff.get("my_j", 0.0))
            mz_i = abs(ff.get("mz_i", 0.0))
            mz_j = abs(ff.get("mz_j", 0.0))
            peak_my[eid] = max(peak_my.get(eid, 0.0), my_i, my_j)
            peak_mz[eid] = max(peak_mz.get(eid, 0.0), mz_i, mz_j)

    # Step 2: normalise each step against the fixed capacities
    step_ratios: list[dict[str, tuple[float, float]]] = []
    for step_forces in all_frame_forces:
        ratios: dict[str, tuple[float, float]] = {}
        for eid, ff in step_forces.items():
            if use_biaxial:
                cap_y = peak_my.get(eid, 1e-12)
                cap_z = peak_mz.get(eid, 1e-12)
                safe_cap_y = max(1e-12, cap_y)
                safe_cap_z = max(1e-12, cap_z)
                my_i = abs(ff.get("my_i", 0.0))
                my_j = abs(ff.get("my_j", 0.0))
                mz_i = abs(ff.get("mz_i", 0.0))
                mz_j = abs(ff.get("mz_j", 0.0))
                r_i = math.sqrt((my_i / safe_cap_y) ** 2 + (mz_i / safe_cap_z) ** 2)
                r_j = math.sqrt((my_j / safe_cap_y) ** 2 + (mz_j / safe_cap_z) ** 2)
                ratios[eid] = (r_i, r_j)
            else:
                cap = peak_mz.get(eid, 1e-12)
                if cap < 1e-12:
                    ratios[eid] = (0.0, 0.0)
                else:
                    ratios[eid] = (
                        abs(ff.get("mz_i", 0.0)) / cap,
                        abs(ff.get("mz_j", 0.0)) / cap,
                    )
        step_ratios.append(ratios)

    return step_ratios


def plot_plastic_hinge_formation(
    data,
    direction: str = "+X",
    step: Optional[int] = None,
    plotter: Optional[Any] = None,
    hinge_scale: float = 1.0,
    displacement_scale: float = 50.0,
    show_deformed: bool = True,
    notebook: bool = False,
    use_biaxial: bool = False,
    colormap: str = _DEFAULT_HINGE_CMAP,
    **kwargs,
) -> Optional[Any]:
    """Visualise plastic hinge formation as 3D coloured blobs.

    Displays frame element end hinges as coloured spheres (rendered as
    GPU impostors via ``render_points_as_spheres=True`` for efficiency)
    at the element node locations.  Colour indicates yield state
    (threshold 0.5, sampled from the named matplotlib colormap):

    * **elastic** (ratio < 0.5) — sampled at cmap position 0.0.
    * **yielding** (0.5 ≤ ratio < 1.0) — sampled at cmap position 0.5.
    * **fully yielded** (ratio ≥ 1.0) — sampled at cmap position 1.0.

    When *step* is ``None`` (default), a slider widget is added to scrub
    through all push steps.  When *plotter* is provided, the hinge blobs
    are overlaid on an existing PyVista scene, enabling combination with
    ``plot_mesh()``, ``plot_pushover_envelope()``, etc.

    Accepts three input types:

    * An ``AnalysisBuilder`` instance (uses ``pushover_step_results``).
    * An NPZ data dict (from ``np.load()`` — reads ``pushover/+X/...`` arrays).
    * A ``list[dict]`` — raw step results.

    Args:
        data: Builder, NPZ dict, or list of step result dicts.
        direction: Push direction label (e.g. ``"+X"``).
        step: Step index to display.  ``None`` = slider widget.
        plotter: Existing ``pyvista.Plotter`` to overlay on.
            ``None`` = create a new plotter.
        hinge_scale: Point size multiplier for hinge blobs (default 1.0).
        displacement_scale: Displacement amplification when
            *show_deformed* is True (default 50.0).
        show_deformed: If True, place hinge blobs at deformed node
            positions.  Requires node displacement data.
        notebook: Return plotter for Jupyter embedding.
        colormap: Matplotlib colormap name for the demand/capacity
            hinge-ratio colour scale (default ``"plasma"`` — perceptually
            uniform and
            colour-blind safe).  Other accessible options include
            ``"viridis"``, ``"cividis"``, ``"turbo"``.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* or *plotter* was provided,
        otherwise ``None``.
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    pv.set_plot_theme("document")

    # ── Resolve data ─────────────────────────────────────────────
    step_results, node_coords, _node_tag_list, frame_eid_to_nodes = _resolve_pushover_data(
        data, direction=direction
    )

    if not step_results:
        print("No pushover step results found.")
        return None

    # Raw list data has no mesh geometry, so 3D rendering is not possible
    if isinstance(data, list):
        print(
            "Raw list data is not supported for 3D animation — use an NPZ dict or Builder instead."
        )
        return None

    n_step = len(step_results)

    # ── Pre-compute hinge ratios per step ───────────────────────
    # step_ratios[ei][eid] = (ratio_i, ratio_j)
    step_has_disp = []
    # Compute peak capacities across all steps for consistent normalization
    all_frame_forces = [sd.get("frame_forces", {}) for sd in step_results]
    step_ratios = _compute_hinge_ratios_all_steps(all_frame_forces, use_biaxial=use_biaxial)
    for sd in step_results:
        step_has_disp.append("node_displacements" in sd and bool(sd["node_displacements"]))

    # ── Build hinge point locations per frame end ───────────────
    # For each element that appears in frame_forces, identify its
    # i-end and j-end node coordinates (rest or deformed).
    # hinge_locs[step_idx] = [(x, y, z, ratio), ...]
    hinge_locs_per_step: list[list[tuple[float, float, float, float]]] = []

    for si, sd in enumerate(step_results):
        ff = sd.get("frame_forces", {})
        ratios = step_ratios[si]
        nd = sd.get("node_displacements", {})
        has_disp = bool(nd) and show_deformed

        hinge_pts: list[tuple[float, float, float, float]] = []

        for eid in ff:
            if eid not in frame_eid_to_nodes:
                continue
            ni_tag, nj_tag = frame_eid_to_nodes[eid]
            ri, rj = ratios.get(eid, (0.0, 0.0))

            # I-end
            if ni_tag in node_coords:
                cx, cy, cz = node_coords[ni_tag]
                if has_disp and ni_tag in nd:
                    dx, dy, dz = nd[ni_tag]
                    cx += dx * displacement_scale
                    cy += dy * displacement_scale
                    cz += dz * displacement_scale
                hinge_pts.append((cx, cy, cz, ri))

            # J-end
            if nj_tag in node_coords:
                cx, cy, cz = node_coords[nj_tag]
                if has_disp and nj_tag in nd:
                    dx, dy, dz = nd[nj_tag]
                    cx += dx * displacement_scale
                    cy += dy * displacement_scale
                    cz += dz * displacement_scale
                hinge_pts.append((cx, cy, cz, rj))

        hinge_locs_per_step.append(hinge_pts)

    # ── Compute global ratio bounds for consistent colour mapping ──
    all_ratios = []
    for pts_list in hinge_locs_per_step:
        for _, _, _, r in pts_list:
            all_ratios.append(r)
    max_ratio = max(all_ratios) if all_ratios else 1.0

    # ── Create or use existing plotter ───────────────────────────
    # Consume fea_toolkit-specific kwargs so they aren't forwarded to pv.Plotter
    _plotter_kwargs = {k: v for k, v in kwargs.items() if k != "use_biaxial"}
    own_plotter = plotter is None
    if own_plotter:
        plotter = pv.Plotter(notebook=notebook, **_plotter_kwargs)
        # Render the undeformed mesh as background
        _render_scene(plotter, _resolve_mesh_data(data), show_nodes=False)

    # ── Create hinge point cloud actor ───────────────────────────
    # We create a single PolyData with point scalars for colour.
    # The point positions and colours are updated per step.
    if hinge_locs_per_step and hinge_locs_per_step[0]:
        pts = np.array([(x, y, z) for x, y, z, _ in hinge_locs_per_step[0]], dtype=float)
        ratios_arr = np.array([r for _, _, _, r in hinge_locs_per_step[0]], dtype=float)

        hinge_cloud = pv.PolyData(pts)
        hinge_cloud["ratio"] = ratios_arr

        # Compute colours for initial state
        colors = np.array([_ratio_to_color(r, max_ratio, cmap_name=colormap) for r in ratios_arr])
        hinge_cloud["colors"] = colors

        # Render as points with GPU sphere impostors
        point_size = 15 * hinge_scale
        plotter.add_mesh(
            hinge_cloud,
            scalars="colors",
            rgb=True,
            point_size=point_size,
            render_points_as_spheres=True,
            style="points",
        )

        # ── Slider widget for multi-step navigation ──────────────
        if step is None and n_step > 1:

            def _update_hinges(step_val: float):
                """Callback for slider widget."""
                idx = min(int(round(step_val)), n_step - 1)
                pts_list = hinge_locs_per_step[idx]
                if not pts_list:
                    return
                pts_new = np.array([(x, y, z) for x, y, z, _ in pts_list], dtype=float)
                ratios_new = np.array([r for _, _, _, r in pts_list], dtype=float)
                colors_new = np.array(
                    [_ratio_to_color(r, max_ratio, cmap_name=colormap) for r in ratios_new]
                )

                hinge_cloud.points = pts_new
                hinge_cloud["ratio"] = ratios_new
                hinge_cloud["colors"] = colors_new
                plotter.render()

            plotter.add_slider_widget(
                _update_hinges,
                rng=[0, n_step - 1],
                value=0,
                title="Push Step",
                pointa=(0.1, 0.1),
                pointb=(0.4, 0.1),
                style="modern",
            )

        elif step is not None:
            # Static view at specific step
            idx = min(step, n_step - 1)
            pts_list = hinge_locs_per_step[idx]
            if pts_list:
                pts_new = np.array([(x, y, z) for x, y, z, _ in pts_list], dtype=float)
                ratios_new = np.array([r for _, _, _, r in pts_list], dtype=float)
                colors_new = np.array(
                    [_ratio_to_color(r, max_ratio, cmap_name=colormap) for r in ratios_new]
                )
                hinge_cloud.points = pts_new
                hinge_cloud["ratio"] = ratios_new
                hinge_cloud["colors"] = colors_new

    # ── Add colour legend ────────────────────────────────────────
    if own_plotter and hinge_locs_per_step and hinge_locs_per_step[0]:
        _add_hinge_color_legend(
            plotter, title="Relative Moment Demand (peak-normalized)", cmap_name=colormap
        )

    # ── Finalise ─────────────────────────────────────────────────
    if own_plotter:
        _set_isometric_view(plotter)
        if notebook:
            return plotter
        plotter.show()
        return None

    if notebook or plotter is not None:
        return plotter
    return None


def plot_plastic_hinge_heatmap(
    data,
    direction: str = "+X",
    title: str = "Plastic Hinge Formation — 2D Heatmap",
    xaxis: str = "step",
    drifts: Optional[list[float]] = None,
    figsize: tuple[float, float] = (10, 8),
    save_path: Optional[str] = None,
    colormap: str = _DEFAULT_HINGE_CMAP,
) -> Optional[Any]:
    """2D heatmap giving a birds‑eye overview of plastic hinge formation.

    Plots a colour grid where **each cell** represents one frame element
    (Y‑axis = mid‑height elevation) at one push step (X‑axis).  The colour
    shows the yield state (threshold 0.5, sampled from the named
    matplotlib colormap — same convention as
    :func:`plot_plastic_hinge_formation`):

    * **Gray** — no data (element not recorded at that step)
    * **Elastic** (ratio < 0.5) — sampled at cmap position 0.0
    * **Yielding** (0.5 ≤ ratio < 1.0) — sampled at cmap position 0.5
    * **Yielded** (ratio ≥ 1.0) — sampled at cmap position 1.0

    Parameters
    ----------
    data : AnalysisBuilder | dict | str
        Pushover results: a built ``AnalysisBuilder`` (after
        ``run_pushover_analysis()`` with ``record_pushover_steps=True``),
        an NPZ dict from ``write_pushover_results_npz()``, or a path to
        an NPZ file.
    direction : str
        Push direction — ``"+X"``, ``"-X"``, ``"+Y"``, ``"-Y"``.
        Only used for NPZ data (key namespace).
    title : str
        Figure title.
    xaxis : str
        X‑axis mode: ``"step"`` (default) for push‑step index, or
        ``"drift"`` for roof‑drift percentage (requires *drifts*).
    drifts : list of float, optional
        Roof‑drift percentage for each step.  Required when
        ``xaxis="drift"``.
    figsize : tuple
        Matplotlib figure size ``(width, height)`` in inches.
    save_path : str, optional
        If given, save the figure to this path (PNG/PDF).

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.colors import BoundaryNorm, ListedColormap
    except ImportError:
        print("Warning: matplotlib not available.  pip install matplotlib")
        return None
    import numpy as np

    # ── Step 1 – Resolve data ──────────────────────────────────────
    # Preserve node_coords / frame_eid_to_nodes so Method B can derive
    # frame mid-height elevations for NPZ inputs.
    push_data, node_coords, _, frame_eid_to_nodes = _resolve_pushover_data(data, direction)
    if not push_data:
        print("No pushover data found.")
        return None

    n_step = len(push_data)

    # ── Step 2 – Collect ratio matrix (N_elem × N_step) ─────────────
    # We build a dict of dicts:  ratio_matrix[eid][step_idx] = max(ri, rj)
    # so we can handle elements that appear/disappear between steps.
    all_eids: set = set()
    step_eids: list[set] = []
    per_step_ratios: list[dict[str, float]] = []

    all_frame_forces = [step_entry.get("frame_forces", {}) for step_entry in push_data]
    for step_entry in push_data:
        frame_forces = step_entry.get("frame_forces", {})
        step_eids.append(set(frame_forces.keys()))
        all_eids.update(frame_forces.keys())

    # Compute hinge ratios once using capacities fixed across all steps
    # so the colour mapping is consistent for the whole pushover.
    step_ratios_all = _compute_hinge_ratios_all_steps(all_frame_forces)
    for sidx in range(n_step):
        ratios = step_ratios_all[sidx] if sidx < len(step_ratios_all) else {}
        max_ratios = {}
        for eid, (ri, rj) in ratios.items():
            max_ratios[eid] = max(ri, rj)
        per_step_ratios.append(max_ratios)

    if not all_eids:
        print("No frame element data to plot in heatmap.")
        return None

    # ── Step 3 – Determine elevation per element ───────────────────
    # Try to get elevations from builder's mesh_model, or from NPZ raw data.
    elevations: dict[str, float] = {}
    # Method A: builder has mesh_model with node coords
    builder = getattr(data, "mesh_model", None) or (
        getattr(data, "model", None) if hasattr(data, "model") else None
    )
    if builder is not None:
        # Data is an AnalysisBuilder
        mm = builder
        if hasattr(data, "mesh_model"):
            mm = data.mesh_model
        for eid, elem in mm.frame_elements.items():
            if eid in all_eids:
                ni = mm.nodes.get(elem.node_i)
                nj = mm.nodes.get(elem.node_j)
                if ni and nj:
                    elevations[eid] = (ni.z + nj.z) / 2.0
    else:
        # Method B: NPZ raw data — derive each frame's mid-height
        # elevation from node_coords via frame_eid_to_nodes.
        if node_coords and frame_eid_to_nodes:
            for eid in all_eids:
                pair = frame_eid_to_nodes.get(eid)
                if pair is None:
                    continue
                ni_tag, nj_tag = pair
                ci = node_coords.get(ni_tag)
                cj = node_coords.get(nj_tag)
                if ci is not None and cj is not None:
                    elevations[eid] = (ci[2] + cj[2]) / 2.0
        # Retain legacy fallback for embedded mid_z / z_i / z_j keys.
        if not elevations:
            for eid in all_eids:
                for step_entry in push_data:
                    ff = step_entry.get("frame_forces", {}).get(eid)
                    if ff and "mid_z" in ff:
                        elevations[eid] = ff["mid_z"]
                        break
                    elif ff and "z_i" in ff and "z_j" in ff:
                        elevations[eid] = (ff["z_i"] + ff["z_j"]) / 2.0
                        break

    # Sort elements by elevation (or by ID if no elevations available)
    if elevations:
        sorted_eids = sorted(elevations, key=lambda e: elevations[e])
        sorted_elev = [elevations[e] for e in sorted_eids]
    else:
        sorted_eids = sorted(all_eids)
        sorted_elev = list(range(len(sorted_eids)))
        warnings.warn(
            "No element elevations available for heatmap Y-axis. "
            "Sorting elements by ID. Pass a built AnalysisBuilder or "
            "an NPZ dict with node coordinates for elevation sorting."
        )

    n_elem = len(sorted_eids)
    eid_to_row = {eid: idx for idx, eid in enumerate(sorted_eids)}

    # Build the matrix: initialise with NaN (→ gray in colormap)
    ratio_matrix = np.full((n_elem, n_step), np.nan, dtype=float)
    for sidx in range(n_step):
        for eid, ratio in per_step_ratios[sidx].items():
            row = eid_to_row.get(eid)
            if row is not None:
                ratio_matrix[row, sidx] = min(ratio, 2.0)  # clamp at 2.0

    # ── Step 4 – Prepare X‑axis values ─────────────────────────────
    if xaxis == "drift" and drifts is not None and len(drifts) >= n_step:
        x_values = np.array(drifts[:n_step], dtype=float)
        x_label = "Roof drift (%)"
    else:
        x_values = np.arange(n_step, dtype=float)
        x_label = "Push step"

    # ── Step 5 – Plot with pcolormesh ──────────────────────────────
    fig, ax = plt.subplots(figsize=figsize)

    # Colormap: gray = no data (NaN → sentinel -1), then 3 discrete bins
    # sampled from the named matplotlib colormap:
    #   elastic (ratio < 0.5), yielding (0.5 ≤ ratio < 1.0), yielded (≥ 1.0)
    # This matches the 0.5 threshold used by _ratio_to_color() in the 3D view.
    _c0, _c1, _c2 = _sample_cmap([0.0, 0.5, 1.0], colormap)
    cmap = ListedColormap(
        [
            "#999999",
            _rgb_to_hex(_c0),
            _rgb_to_hex(_c1),
            _rgb_to_hex(_c2),
        ]
    )
    # Boundaries: -1 (no data), 0–0.5 (elastic), 0.5–1.0 (yielding), ≥1.0 (yielded)
    bounds = [-0.5, 0.0, 0.5, 1.0, 2.0]
    norm = BoundaryNorm(bounds, cmap.N)

    # Fill NaN with -1 so BoundaryNorm maps them to gray
    plot_data = np.where(np.isnan(ratio_matrix), -1.0, ratio_matrix)
    # Clip to [0, 2] range for norm (values >= 1.0 → index 3 = red)
    plot_data = np.clip(plot_data, -1.0, 2.0)

    # pcolormesh expects shape (N_y, N_x); use edge grids
    x_edges = np.concatenate([x_values - 0.5, x_values[-1:] + 0.5])
    y_edges = np.arange(n_elem + 1) - 0.5

    mesh = ax.pcolormesh(
        x_edges, y_edges, plot_data, cmap=cmap, norm=norm, shading="flat", edgecolors="none"
    )

    # ── Step 6 – Colourbar with labels ────────────────────────────
    cbar = fig.colorbar(mesh, ax=ax, ticks=[-0.25, 0.35, 0.85, 1.5])
    cbar.ax.set_yticklabels(["No data", "Elastic", "Yielding", "Yielded"])
    cbar.ax.tick_params(labelsize=9)

    # ── Step 7 – Axes ──────────────────────────────────────────────
    ax.set_xlabel(x_label, fontsize=11)
    ax.set_ylabel("Element elevation", fontsize=11)
    ax.set_title(title, fontsize=12, fontweight="bold")

    # Y‑axis: show some elevation labels (avoid crowding)
    if elevations:
        y_ticks = np.arange(n_elem)
        # Show a subset of tick labels
        step_ylabels = max(1, n_elem // 20)
        ax.set_yticks(y_ticks[::step_ylabels])
        ax.set_yticklabels([f"{sorted_elev[i]:.1f}" for i in range(0, n_elem, step_ylabels)])
    else:
        y_ticks = np.arange(n_elem)
        step_ylabels = max(1, n_elem // 30)
        ax.set_yticks(y_ticks[::step_ylabels])
        ax.set_yticklabels([sorted_eids[i] for i in range(0, n_elem, step_ylabels)])

    fig.tight_layout()

    # ── Step 8 – Save / return ─────────────────────────────────────
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"  Heatmap saved: {save_path}")

    return fig


def _compute_shell_damage(
    shell_forces: dict[str, dict[str, float]],
    yield_stress: Optional[float] = None,
    thickness: Optional[float] = None,
) -> dict[str, tuple[float, float]]:
    """Compute a scalar damage index for each shell element.

    Uses a combined von Mises membrane stress proxy + bending moment
    magnitude, range‑normalised to [0, 1].

    When *yield_stress* and *thickness* are provided, the damage index is
    computed as a physical stress ratio: D = max(σ_vm / fy, σ_bend / fy),
    where σ_vm is the von Mises membrane stress and σ_bend is the extreme
    fibre bending stress.  When omitted (default), it uses a range-based
    normalisation where each element's peak D across all steps sets the
    scale — suitable for models where material properties are unknown.

    Returns ``{aid: (D, M_mag)}`` where *D* is a combined damage index
    and *M_mag* is the bending envelope (for debugging).
    """
    import math as _math

    indices: dict[str, tuple[float, float]] = {}
    for aid, sf in shell_forces.items():
        nx = sf.get("Nx", 0.0)
        ny = sf.get("Ny", 0.0)
        nxy = sf.get("Nxy", 0.0)
        mx = sf.get("Mx", 0.0)
        my = sf.get("My", 0.0)
        mxy = sf.get("Mxy", 0.0)

        if yield_stress is not None and thickness is not None and thickness > 0:
            # Physical stress-based damage index
            # Membrane stress: σ_vm = sqrt(Nx² + Ny² - Nx·Ny + 3·Nxy²) / t
            vm_stress = (
                _math.sqrt(max(0.0, nx * nx + ny * ny - nx * ny + 3.0 * nxy * nxy)) / thickness
            )
            # Bending stress at extreme fibre: σ_b = sqrt(Mx² + My² + Mxy²) / (t²/6)
            m_mag = _math.sqrt(mx * mx + my * my + mxy * mxy)
            bend_stress = m_mag / (thickness * thickness / 6.0) if thickness > 0 else 0.0
            D = max(vm_stress, bend_stress) / yield_stress if yield_stress > 0 else 0.0
            indices[aid] = (D, m_mag)
        else:
            # Range-based normalisation (legacy behaviour)
            vm_mem = _math.sqrt(max(0.0, nx * nx + ny * ny - nx * ny + 3.0 * nxy * nxy))
            m_mag = _math.sqrt(mx * mx + my * my + mxy * mxy)
            D = max(vm_mem, m_mag)
            indices[aid] = (D, m_mag)
    return indices


def _resolve_shell_data(
    source,
    shell_ids: set,
) -> tuple[dict[str, list[tuple[float, float, float]]], dict[str, list[int]]]:
    """Build shell vertex coordinates and node‑tag mapping for a subset of shell IDs.

    Uses ``_resolve_mesh_data()`` internally, avoiding duplicated NPZ/build‑path
    geometry extraction logic.  Accepts any type that ``_resolve_mesh_data`` supports
    (NPZ dict, Builder, AnalysisBuilder, SAPModelData, raw list).

    Returns ``(shell_verts, shell_node_tags)`` where:
    - *shell_verts* — ``{aid: [(x,y,z), ...]}`` for each shell's vertices
      (4 vertices per quad, right‑handed order).
    - *shell_node_tags* — ``{aid: [tag1, tag2, tag3, tag4]}`` for
      resolving displacements.
    """
    shell_verts: dict[str, list[tuple[float, float, float]]] = {}
    shell_node_tags: dict[str, list[int]] = {}

    # Raw list data — no geometry available, return empty
    if isinstance(source, list):
        return shell_verts, shell_node_tags

    # Delegate to _resolve_mesh_data for the heavy lifting
    resolved = _resolve_mesh_data(source)

    for sh in resolved["shells"]:
        aid = sh["id"]
        if aid not in shell_ids:
            continue
        refs = sh.get("node_ids") or sh.get("node_tags") or []
        verts: list[tuple[float, float, float]] = []
        tags: list[int] = []
        for ref in refs:
            nd = resolved["nodes"].get(ref) or resolved["nodes"].get(str(ref))
            if nd is None:
                # Fallback: search by tag value
                for n in resolved["nodes"].values():
                    if n.get("tag") == ref:
                        nd = n
                        break
            if nd is not None:
                verts.append((nd["x"], nd["y"], nd["z"]))
                tags.append(nd.get("tag", 0))
        if len(verts) == 4:
            shell_verts[aid] = verts
            shell_node_tags[aid] = tags

    return shell_verts, shell_node_tags


def _ratio_to_shell_color(ratio: float) -> tuple[float, float, float]:
    """Map a damage ratio to (green, yellow, red) RGB.

    * ratio < 0.7 → green
    * 0.7 ≤ ratio < 1.0 → yellow
    * ratio ≥ 1.0 → red
    * NaN → gray (no data)
    """
    import math as _math

    if _math.isnan(ratio) or ratio < -0.5:
        return (0.6, 0.6, 0.6)  # gray
    norm = min(ratio / 1.0, 1.0)
    if norm < 0.7 / 1.0:
        # Green to yellow
        t = norm / 0.7
        return (t, 1.0, 0.0)
    else:
        # Yellow to red
        t = (norm - 0.7) / 0.3
        return (1.0, 1.0 - t, 0.0)


def plot_shell_damage_map(
    data,
    direction: str = "+X",
    step: Optional[int] = None,
    plotter: Optional[Any] = None,
    displacement_scale: float = 50.0,
    show_deformed: bool = True,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Visualise shell element damage progression during a pushover.

    Displays shell faces coloured by a combined damage index derived
    from stress resultants (Nx, Ny, Nxy, Mx, My, Mxy):

    * **Green** — elastic (damage ratio < 0.7)
    * **Yellow** — yielding (0.7 ≤ ratio < 1.0)
    * **Red** — damaged / crushed (ratio ≥ 1.0)
    * **Gray** — no data (shell not recorded at that step)

    When *step* is ``None`` (default), a slider widget is added to scrub
    through all push steps.  When *plotter* is provided, the shell damage
    overlay is added to an existing PyVista scene.

    Accepts:
    * An ``AnalysisBuilder`` instance (uses ``pushover_step_results``).
    * An NPZ data dict (from ``np.load()``).

    Args:
        data: Builder or NPZ dict (raw list not supported — no geometry).
        direction: Push direction label (e.g. ``"+X"``).
        step: Step index to display.  ``None`` = slider widget.
        plotter: Existing ``pyvista.Plotter`` to overlay on.
        displacement_scale: Amplification for deformed positions.
        show_deformed: If True, displace vertices using recorded node
            displacements.
        notebook: Return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* or *plotter* was provided,
        otherwise ``None``.
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    pv.set_plot_theme("document")

    # ── Resolve data ─────────────────────────────────────────────
    step_results, _node_coords, _node_tag_list, _ = _resolve_pushover_data(
        data, direction=direction
    )

    if not step_results:
        print("No pushover step results found.")
        return None

    # Raw list data has no mesh geometry, so 3D rendering is not possible
    if isinstance(data, list):
        print(
            "Raw list data is not supported for 3D animation — use an NPZ dict or Builder instead."
        )
        return None

    n_step = len(step_results)

    # ── Collect all shell IDs across steps ────────────────────────
    all_shell_ids: set = set()
    per_step_indices: list[dict[str, float]] = []
    for sd in step_results:
        sf = sd.get("shell_forces", {})
        all_shell_ids.update(sf.keys())
        per_step_indices.append(_compute_shell_damage(sf))

    if not all_shell_ids:
        print(
            "No shell element data found. Use pushover_record_selection "
            "with element_types=['Area'] or include areas in the analysis."
        )
        return None

    # ── Build shell geometry ──────────────────────────────────────
    shell_verts, shell_node_tags = _resolve_shell_data(data, all_shell_ids)
    if not shell_verts:
        print("No shell geometry found in model source.")
        return None

    # Build a single PolyData for all shells (one face per shell)
    all_pts: list[float] = []
    all_faces: list[int] = []
    shell_aid_to_face_idx: dict[str, int] = {}
    offset = 0
    for aid, verts in shell_verts.items():
        for v in verts:
            all_pts.extend(v)
        all_faces.extend([4, offset, offset + 1, offset + 2, offset + 3])
        shell_aid_to_face_idx[aid] = offset // 4  # face index
        offset += 4

    if not all_pts:
        return None

    pts_arr = np.array(all_pts, dtype=float).reshape(-1, 3)
    faces_arr = np.array(all_faces, dtype=int)
    base_mesh = pv.PolyData(pts_arr, faces=faces_arr)

    # ── Pre-compute per-step colours ──────────────────────────────
    # Track peak D per element across all steps for range normalisation
    peak_D: dict[str, float] = {}
    for idx_dict in per_step_indices:
        for aid, (D, _) in idx_dict.items():
            peak_D[aid] = max(peak_D.get(aid, 0.0), D)

    # per_step_fcolors[step_idx] = {face_idx: (r,g,b)}
    per_step_fcolors: list[dict[int, tuple[float, float, float]]] = []

    # Also build deformed positions per step if available
    per_step_deformed_pts: list = []

    for si, sd in enumerate(step_results):
        idx_dict = per_step_indices[si]
        nd = sd.get("node_displacements", {})
        has_disp = bool(nd) and show_deformed

        fcolors: dict[int, tuple[float, float, float]] = {}
        def_pts = pts_arr.copy()

        for aid in all_shell_ids:
            face_idx = shell_aid_to_face_idx.get(aid, -1)
            if face_idx < 0:
                continue
            if aid in idx_dict:
                D, _ = idx_dict[aid]
                pk = peak_D.get(aid, 1e-12)
                ratio = D / pk if pk > 1e-12 else 0.0
                fcolors[face_idx] = _ratio_to_shell_color(min(ratio, 2.0))
            else:
                # No data for this shell at this step → gray
                fcolors[face_idx] = _ratio_to_shell_color(float("nan"))

            # Apply displacement to vertices
            if has_disp and aid in shell_node_tags:
                ntags = shell_node_tags[aid]
                for vi, tag in enumerate(ntags):
                    global_idx = face_idx * 4 + vi
                    if global_idx < len(def_pts) and tag in nd:
                        dx, dy, dz = nd[tag]
                        # Add to original (undeformed) position
                        def_pts[global_idx] = (
                            np.array(pts_arr[global_idx])
                            + np.array([dx, dy, dz]) * displacement_scale
                        )

        per_step_fcolors.append(fcolors)
        per_step_deformed_pts.append(def_pts)

    # Build cell RGB array for initial step
    n_faces = len(shell_verts)
    face_colors = np.zeros((n_faces, 3), dtype=float)
    initial_fcolors = per_step_fcolors[0]
    for fidx in range(n_faces):
        face_colors[fidx] = initial_fcolors.get(fidx, (0.6, 0.6, 0.6))

    mesh_shell = base_mesh.copy()
    mesh_shell.points = per_step_deformed_pts[0]
    mesh_shell.cell_data["RGB"] = face_colors

    # ── Create or use existing plotter ───────────────────────────
    own_plotter = plotter is None
    if own_plotter:
        plotter = pv.Plotter(notebook=notebook, **kwargs)
        # Add undeflected frames for context
        _render_scene(
            plotter, _resolve_mesh_data(data), show_nodes=False, show_shells=False, show_frames=True
        )

    # Add the shell damage mesh
    plotter.add_mesh(
        mesh_shell,
        scalars="RGB",
        rgb=True,
        show_edges=True,
        edge_color="black",
        line_width=1,
        opacity=0.85,
    )

    # ── Slider widget for step navigation ─────────────────────────
    if step is None and n_step > 1:

        def _update_shell_damage(step_val: float):
            """Callback for slider widget."""
            idx = min(int(round(step_val)), n_step - 1)
            fcolors = per_step_fcolors[idx]
            # Build new RGB array
            new_rgb = np.zeros((n_faces, 3), dtype=float)
            for fidx in range(n_faces):
                new_rgb[fidx] = fcolors.get(fidx, (0.6, 0.6, 0.6))
            mesh_shell.cell_data["RGB"] = new_rgb
            mesh_shell.points = per_step_deformed_pts[idx]
            plotter.render()

        plotter.add_slider_widget(
            _update_shell_damage,
            rng=[0, n_step - 1],
            value=0,
            title="Push Step",
            pointa=(0.1, 0.1),
            pointb=(0.4, 0.1),
            style="modern",
        )

    elif step is not None:
        idx = min(step, n_step - 1)
        fcolors = per_step_fcolors[idx]
        new_rgb = np.zeros((n_faces, 3), dtype=float)
        for fidx in range(n_faces):
            new_rgb[fidx] = fcolors.get(fidx, (0.6, 0.6, 0.6))
        mesh_shell.cell_data["RGB"] = new_rgb
        mesh_shell.points = per_step_deformed_pts[idx]

    # ── Finalise ─────────────────────────────────────────────────
    if own_plotter:
        _set_isometric_view(plotter)
        if notebook:
            return plotter
        plotter.show()
        return None

    if notebook or plotter is not None:
        return plotter
    return None


def plot_pushover_envelope(
    data,
    quantity: str = "Mz",
    direction: str = "+X",
    mode: str = "flag",
    show_frames: bool = True,
    show_shells: bool = False,
    notebook: bool = False,
    show_original: bool = True,
    moment_scale: Optional[float] = None,
    **kwargs,
) -> Optional[Any]:
    """3D force/moment envelope showing the extreme state across all push steps.

    For each frame element, finds the step where the chosen quantity
    is at its peak magnitude and renders that step's forces using the
    shared ``_render_frame_force_diagram()`` infrastructure.

    * **Frame elements**: moment flags or force tubes coloured by peak
      local quantity (red = +ve, blue = −ve).
    * **Shell elements**: coloured quads showing peak damage index
      across all steps (green = elastic, yellow = yielding, red = damaged).

    Accepts:
    * An ``AnalysisBuilder`` instance (uses ``pushover_step_results``).
    * An NPZ data dict (from ``np.load()``).

    Args:
        data: Builder or NPZ dict.
        quantity: Force quantity — ``"Mz"``, ``"My"``, ``"Mx"``,
            ``"Fx"``, ``"Fy"``, ``"Fz"``.
        direction: Push direction label (e.g. ``"+X"``).
        mode: ``"flag"`` (planar quadrilaterals) or ``"tube"``
            (coloured cylinders).
        show_frames: If True, render frame force envelope.
        show_shells: If True, render shell envelope with peak damage colours.
        notebook: Return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* else ``None``.
    """
    import numpy as np

    if not data:
        print("No pushover data provided.")
        return None

    # ── Resolve pushover data ───────────────────────────────────
    step_results, _node_coords, _node_tags, _frame_eid_to_nodes = _resolve_pushover_data(
        data, direction=direction
    )

    if not step_results:
        print("No pushover step results found.")
        return None

    # ── Build envelope: for each frame element, find peak force step ──
    envelope_global: dict[str, dict[str, float]] = {}

    for sd in step_results:
        ff = sd.get("frame_forces", {})
        for eid, entry in ff.items():
            q = quantity.lower()
            q_i = f"{q}_i"
            q_j = f"{q}_j"
            val_i = abs(entry.get(q_i, 0.0))
            val_j = abs(entry.get(q_j, 0.0))
            step_mag = max(val_i, val_j)

            existing = envelope_global.get(eid, {})
            prev_mag = existing.get("_peak_mag", -1.0)
            if step_mag > prev_mag:
                new_entry = {
                    "Fx": entry.get("fx_i", 0.0),
                    "Fy": entry.get("fy_i", 0.0),
                    "Fz": entry.get("fz_i", 0.0),
                    "Mx": entry.get("mx_i", 0.0),
                    "My": entry.get("my_i", 0.0),
                    "Mz": entry.get("mz_i", 0.0),
                    "Fx_j": entry.get("fx_j", 0.0),
                    "Fy_j": entry.get("fy_j", 0.0),
                    "Fz_j": entry.get("fz_j", 0.0),
                    "Mx_j": entry.get("mx_j", 0.0),
                    "My_j": entry.get("my_j", 0.0),
                    "Mz_j": entry.get("mz_j", 0.0),
                    "_peak_mag": step_mag,
                }
                envelope_global[eid] = new_entry

    # ── Compute shell envelope (peak damage per shell) ────────────
    shell_envelope: dict[str, float] = {}
    all_shell_ids: set = set()
    if show_shells:
        for sd in step_results:
            sf = sd.get("shell_forces", {})
            all_shell_ids.update(sf.keys())
            damages = _compute_shell_damage(sf)
            for aid, (D, _) in damages.items():
                prev = shell_envelope.get(aid, -1.0)
                if prev < D:
                    shell_envelope[aid] = D

    # ── Resolve mesh geometry for rendering ─────────────────────
    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    mesh_data = _resolve_mesh_data(data, collapse_to_parents=False)
    frames = mesh_data["frames"]
    nodes = mesh_data["nodes"]

    # ── Build force_map: {frame_index -> force_entry} ──────────
    force_map: dict[int, dict[str, float]] = {}
    for idx, fr in enumerate(frames):
        eid = fr.get("id")
        if eid and eid in envelope_global:
            f_entry = {k: v for k, v in envelope_global[eid].items() if not k.startswith("_")}
            force_map[idx] = f_entry

    # Consume fea_toolkit-specific kwargs so they aren't forwarded to pv.Plotter
    _plotter_kwargs = {
        k: v for k, v in kwargs.items() if k not in ("show_original", "moment_scale")
    }
    # ── Create shared plotter ──────────────────────────────────
    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **_plotter_kwargs)

    # ── Render frame force envelope via shared helper ──────────
    if show_frames and force_map:
        _render_frame_force_diagram(
            plotter,
            data,
            frames,
            nodes,
            force_map,
            quantity,
            mode,
            show_original=show_original,
            moment_scale=moment_scale,
        )

    # ── Render shell envelope (peak damage quads) ──────────────
    if show_shells and shell_envelope:
        shell_data = _resolve_shell_data(data, set(shell_envelope.keys()))
        shell_verts, _ = shell_data
        if shell_verts:
            for aid, verts in shell_verts.items():
                D = shell_envelope.get(aid, 0.0)
                color = _ratio_to_shell_color(D)
                pts = np.array(verts, dtype=float)
                quad = pv.PolyData(pts, faces=[4, 0, 1, 2, 3])
                plotter.add_mesh(
                    quad,
                    color=color,
                    show_edges=True,
                    edge_color="black",
                    line_width=1,
                    opacity=0.85,
                )

    # ── Finalise ─────────────────────────────────────────────────
    plotter.add_text(
        f"{quantity} (local)  (red = +ve, blue = −ve)", position="lower_edge", font_size=10
    )
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


def animate_pushover_deformation(
    data,
    direction: str = "+X",
    displacement_scale: float = 50.0,
    show_frames: bool = True,
    show_shells: bool = True,
    notebook: bool = False,
    save_html: Optional[str] = None,
    animation_interval_ms: int = 200,
    use_biaxial: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Animate the deformed shape through all pushover steps.

    Displays frame elements as coloured tubes (by demand/capacity hinge ratio) and
    shell elements as coloured quad faces (by damage index), deformed by
    recorded node displacements.  Includes a slider widget to scrub
    through steps and a timer callback for auto-play.

    Colour legends:
        * Frame tubes — blue (elastic), yellow (yielding), red (yielded).
        * Shell quads — green (elastic), yellow (yielding), red (damaged).
        * Grey — no data for that element at that step.

    Accepts two input types:
        * An ``AnalysisBuilder`` instance.
        * An NPZ data dict (from ``np.load()``).

    .. note::
        Raw ``list[dict]`` data (from ``_record_step()``) is **not** supported
        by 3D rendering — it lacks mesh topology. Use an NPZ dict or Builder
        instead.

    Args:
        data: Builder or NPZ dict.
        direction: Push direction label (e.g. ``"+X"``).
        displacement_scale: Amplification factor for node displacements
            (default 50.0).
        show_frames: If True, render frame elements as coloured tubes.
        show_shells: If True, render shell elements as coloured quads.
        notebook: If True, return plotter for Jupyter embedding.
        save_html: Optional file path to export an interactive HTML page
            via ``plotter.export_html()``.
        animation_interval_ms: Timer interval in milliseconds for
            auto-play animation (default 200).
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* is True, otherwise ``None``.
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    pv.set_plot_theme("document")

    # ── Resolve data ─────────────────────────────────────────────
    step_results, node_coords, _node_tag_list, frame_eid_to_nodes = _resolve_pushover_data(
        data, direction=direction
    )

    if not step_results:
        print("No pushover step results found.")
        return None

    # Raw list data has no mesh geometry, so 3D rendering is not possible
    if isinstance(data, list):
        print(
            "Raw list data is not supported for 3D animation — use an NPZ dict or Builder instead."
        )
        return None

    n_step = len(step_results)

    # ── Pre-compute hinge ratios per step (all steps for consistent capacities) ─
    all_frame_forces = [sd.get("frame_forces", {}) for sd in step_results]
    step_ratios = _compute_hinge_ratios_all_steps(all_frame_forces, use_biaxial=use_biaxial)

    # ── Pre-compute shell damage per step ────────────────────────
    per_step_indices: list[dict[str, float]] = []
    all_shell_ids: set = set()
    for sd in step_results:
        sf = sd.get("shell_forces", {})
        all_shell_ids.update(sf.keys())
        dmg = _compute_shell_damage(sf)
        per_step_indices.append(dmg)

    # Global peak ratios for consistent colour mapping
    all_ratios = []
    for rd in step_ratios:
        for eid, (ri, rj) in rd.items():
            all_ratios.extend([ri, rj])
    max_ratio = max(all_ratios) if all_ratios else 1.0

    # Global peak shell damage
    peak_D: dict[str, float] = {}
    for idx_dict in per_step_indices:
        for aid, (D, _) in idx_dict.items():
            peak_D[aid] = max(peak_D.get(aid, 0.0), D)

    # ── Build frame geometry ─────────────────────────────────────
    # frame_segs_per_step[step_idx] = list of (p_i, p_j, ratio_i, ratio_j)
    frame_segs_per_step: list[list[tuple]] = []
    for si, sd in enumerate(step_results):
        ff = sd.get("frame_forces", {})
        ratios = step_ratios[si]
        step_nd: dict = sd.get("node_displacements", {})
        step_has_disp = bool(step_nd)

        def _get_deformed_pos(tag_, nd_, has_disp_, scale_, coords_):
            """Local function to compute deformed position."""
            if tag_ in coords_:
                cx, cy, cz = coords_[tag_]
                if has_disp_ and tag_ in nd_:
                    dx, dy, dz = nd_[tag_]
                    cx += dx * scale_
                    cy += dy * scale_
                    cz += dz * scale_
                return np.array([cx, cy, cz])
            return None

        segs: list = []
        for eid in ff:
            if not frame_eid_to_nodes or eid not in frame_eid_to_nodes:
                continue
            ni_tag, nj_tag = frame_eid_to_nodes[eid]
            ri, rj = ratios.get(eid, (0.0, 0.0))

            p_i = _get_deformed_pos(ni_tag, step_nd, step_has_disp, displacement_scale, node_coords)
            p_j = _get_deformed_pos(nj_tag, step_nd, step_has_disp, displacement_scale, node_coords)
            if p_i is not None and p_j is not None:
                segs.append((p_i, p_j, ri, rj))
        frame_segs_per_step.append(segs)

    # ── Build shell geometry ─────────────────────────────────────
    # Build shell_verts + shell_node_tags once (geometry doesn't change)
    shell_verts, shell_node_tags = {}, {}
    if show_shells and all_shell_ids:
        shell_verts, shell_node_tags = _resolve_shell_data(data, all_shell_ids)

    # per_step_shell_pts[step_idx] = {aid: [(x,y,z), ...]} (deformed)
    per_step_shell_pts: list[dict[str, list]] = []
    per_step_shell_colors: list[dict[str, tuple[float, float, float]]] = []
    for si, sd in enumerate(step_results):
        idx_dict = per_step_indices[si]
        nd = sd.get("node_displacements", {})
        has_disp = bool(nd)
        step_shell_pts: dict[str, list] = {}
        step_shell_colors: dict[str, tuple] = {}

        for aid in all_shell_ids:
            if aid not in shell_verts:
                continue
            verts = shell_verts[aid]
            tags = shell_node_tags.get(aid, [])

            # Deform vertices — bind loop vars as defaults so the closure
            # is independent of subsequent loop iterations (B023).
            def _shell_disp(
                tag, vert, has_disp=has_disp, nd=nd, displacement_scale=displacement_scale
            ):
                if has_disp and tag in nd:
                    dx, dy, dz = nd[tag]
                    return (
                        vert[0] + dx * displacement_scale,
                        vert[1] + dy * displacement_scale,
                        vert[2] + dz * displacement_scale,
                    )
                return vert

            deformed = [_shell_disp(tags[k], verts[k]) for k in range(len(verts))]
            step_shell_pts[aid] = deformed

            # Colour
            if aid in idx_dict:
                D, _ = idx_dict[aid]
                peak = peak_D.get(aid, 1.0)
                norm_ratio = D / peak if peak > 1e-12 else 0.0
                step_shell_colors[aid] = _ratio_to_shell_color(norm_ratio)
            else:
                step_shell_colors[aid] = (0.6, 0.6, 0.6)  # grey

        per_step_shell_pts.append(step_shell_pts)
        per_step_shell_colors.append(step_shell_colors)

    # Consume fea_toolkit-specific kwargs so they aren't forwarded to pv.Plotter
    _plotter_kwargs = {k: v for k, v in kwargs.items() if k != "use_biaxial"}
    # ── Create plotter ──────────────────────────────────────────
    plotter = pv.Plotter(notebook=notebook, **_plotter_kwargs)

    # Render undeformed mesh as background
    _render_scene(plotter, _resolve_mesh_data(data), show_nodes=False)

    # ── Frame tube actor ────────────────────────────────────────
    frame_actor = None
    if show_frames and frame_segs_per_step and frame_segs_per_step[0]:
        segs0 = frame_segs_per_step[0]
        # Build initial frame tubes as a single PolyData
        pts_list = []
        for p_i, p_j, ri, rj in segs0:
            pts_list.extend(p_i.tolist())
            pts_list.extend(p_j.tolist())
        tube_pts = np.array(pts_list, dtype=float).reshape(-1, 3)
        # One colour per segment endpoint
        colors_list = []
        for p_i, p_j, ri, rj in segs0:
            c_i = _ratio_to_color(ri, max_ratio)
            c_j = _ratio_to_color(rj, max_ratio)
            colors_list.extend([c_i, c_j])
        tube_colors = np.array(colors_list, dtype=float)

        lines = []
        for i in range(len(segs0)):
            lines.append([2, 2 * i, 2 * i + 1])

        tube_mesh = pv.PolyData(tube_pts, lines=np.array(lines, dtype=int))
        tube_mesh["colors"] = tube_colors

        frame_actor = plotter.add_mesh(
            tube_mesh,
            scalars="colors",
            rgb=True,
            line_width=6,
            render_lines_as_tubes=True,
        )

    # ── Shell quad actor ────────────────────────────────────────
    shell_actor = None
    if show_shells and all_shell_ids and shell_verts:
        first_shell_pts = per_step_shell_pts[0]
        first_shell_colors = per_step_shell_colors[0]
        all_pts = []
        all_faces = []
        face_colors = []
        offset = 0
        for aid in all_shell_ids:
            if aid not in first_shell_pts:
                continue
            verts = first_shell_pts[aid]
            for v in verts:
                all_pts.extend(v)
            nv = len(verts)
            if nv >= 3:
                all_faces.extend(
                    [nv, offset, offset + 1, offset + 2, *(offset + i for i in range(3, nv))]
                )
                face_colors.append(first_shell_colors.get(aid, (0.6, 0.6, 0.6)))
            offset += nv

        if all_pts:
            pts_arr = np.array(all_pts, dtype=float).reshape(-1, 3)
            faces_arr = np.array(all_faces, dtype=int)
            shell_mesh = pv.PolyData(pts_arr, faces=faces_arr)
            shell_mesh.cell_data["colors"] = np.array(face_colors, dtype=float)

            shell_actor = plotter.add_mesh(
                shell_mesh,
                scalars="colors",
                rgb=True,
                opacity=0.7,
                show_edges=True,
                edge_color="darkgrey",
                lighting=False,
            )

    # ── Slider widget + timer for multi-step navigation ──────────
    if n_step > 1:

        def _make_update_fn(
            segs_per_step,
            shell_pts_per_step,
            shell_cols_per_step,
            tube_mesh,
            shell_mesh_data,
            n_step_,
        ):
            """Return a closure that updates geometry at a given step."""
            shell_faces_data = None
            if shell_mesh_data is not None:
                _shell_verts_data, shell_faces_data = shell_mesh_data

            def _update(step_val: float):
                idx = min(int(round(step_val)), n_step_ - 1)

                # ── Update frame tubes ──
                segs = segs_per_step[idx]
                if segs and tube_mesh is not None:
                    new_pts = []
                    new_colors = []
                    for p_i, p_j, ri, rj in segs:
                        new_pts.extend(p_i.tolist())
                        new_pts.extend(p_j.tolist())
                        new_colors.extend(
                            [_ratio_to_color(ri, max_ratio), _ratio_to_color(rj, max_ratio)]
                        )
                    if new_pts and len(new_pts) % 3 == 0:
                        pts_arr = np.array(new_pts, dtype=float).reshape(-1, 3)
                        if pts_arr.shape[0] == tube_mesh.n_points:
                            tube_mesh.points = pts_arr
                            tube_mesh["colors"] = np.array(new_colors, dtype=float)

                # ── Update shell quads ──
                if shell_actor is not None and shell_faces_data:
                    step_shell_pts = shell_pts_per_step[idx]
                    step_shell_cols = shell_cols_per_step[idx]
                    new_pts = []
                    new_fcolors = []
                    for aid in shell_faces_data:
                        if aid in step_shell_pts:
                            verts = step_shell_pts[aid]
                            for v in verts:
                                new_pts.extend(v)
                            new_fcolors.append(step_shell_cols.get(aid, (0.6, 0.6, 0.6)))
                    if new_pts and len(new_pts) % 3 == 0:
                        # Reshape the flat coordinate list into (N, 3)
                        # before assignment, matching the initial mesh
                        # construction.
                        shell_actor.mapper.dataset.points = np.array(new_pts, dtype=float).reshape(
                            -1, 3
                        )
                        shell_actor.mapper.dataset.cell_data["colors"] = np.array(
                            new_fcolors, dtype=float
                        )
                        shell_actor.mapper.Update()

                plotter.render()

            return _update

        # Pre-compute shell face data structure for efficient update
        shell_faces_data: list[str] = []
        if show_shells and all_shell_ids and shell_verts:
            shell_faces_data = list(all_shell_ids)

        update_fn = _make_update_fn(
            frame_segs_per_step,
            per_step_shell_pts,
            per_step_shell_colors,
            frame_actor.mapper.dataset if frame_actor is not None else None,
            (shell_verts, shell_faces_data) if shell_actor is not None else None,
            n_step,
        )

        plotter.add_slider_widget(
            update_fn,
            rng=[0, n_step - 1],
            value=0,
            title="Push Step",
            pointa=(0.1, 0.1),
            pointb=(0.4, 0.1),
            style="modern",
        )

        # ── Timer callback for auto-play ─────────────────────────
        timer_step = [0]  # mutable closure

        def _timer_callback():
            """Advance one step per timer tick."""
            timer_step[0] = (timer_step[0] + 1) % n_step
            update_fn(float(timer_step[0]))

        _add_animation_timer(
            plotter,
            _timer_callback,
            max_steps=n_step * 100,
            interval_ms=animation_interval_ms,
        )

    # ── HTML export ──────────────────────────────────────────────
    if save_html:
        try:
            plotter.export_html(save_html)
            print(f"  Interactive animation saved: {save_html}")
        except Exception as e:
            print(f"  Warning: could not export HTML — {e}")

    # ── Add colour legends ───────────────────────────────────────
    if show_frames and frame_segs_per_step and frame_segs_per_step[0]:
        _add_hinge_color_legend(
            plotter,
            title="Relative Moment Demand (peak-normalized)",
            position_x=0.82,
            position_y=0.1,
        )
    if show_shells and all_shell_ids and shell_verts:
        _add_shell_color_legend(plotter, title="Damage Index", position_x=0.82, position_y=0.1)

    # ── Finalise ─────────────────────────────────────────────────
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


def plot_frame_force_evolution(
    data,
    direction: str = "+X",
    quantity: str = "Mz",
    element_ids: Optional[list[str]] = None,
    yield_moment: Optional[dict[str, float]] = None,
    xaxis: str = "step",
    drifts: Optional[list[float]] = None,
    figsize: tuple[float, float] = (10, 8),
    **kwargs,
) -> Optional[Any]:
    """Plot the evolution of a force quantity for selected frame elements.

    Creates a 2D subplot grid (max 3×3), one subplot per element, showing
    how the chosen quantity changes with push step or roof drift.

    For moment quantities (``"Mz"``, ``"My"``, ``"Mx"``), the |Mz|
    magnitude at each element end is plotted.  For force quantities,
    ``"V"`` shows resultant shear at each end, ``"N"`` shows axial.

    Accepts:
    * An ``AnalysisBuilder`` instance (uses ``pushover_step_results``).
    * An NPZ data dict (from ``np.load()``).
    * A ``list[dict]`` — raw step results.

    Args:
        data: Builder, NPZ dict, or list of step result dicts.
        direction: Push direction label (only used for NPZ data).
        quantity: ``"Mz"``, ``"My"``, ``"Mx"`` (moment), ``"V"``
            (resultant shear), or ``"N"`` (axial force).
        element_ids: List of element SAP IDs to plot.  ``None`` = all.
        yield_moment: Optional dict ``{eid: yield_val}``.  When given,
            draws a dashed horizontal line at the yield value on each
            element's subplot.
        xaxis: ``"step"`` (default) for push-step index, or ``"drift"``
            for roof-drift percentage (requires *drifts*).
        drifts: Roof-drift percentage per step (required when
            ``xaxis="drift"``).
        figsize: Matplotlib figure size ``(width, height)``.
        **kwargs: Passed to ``matplotlib.axes.Axes.plot()``.

    Returns:
        ``matplotlib.figure.Figure`` or ``None``.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not available.  pip install matplotlib")
        return None
    import numpy as np

    # ── Resolve data ─────────────────────────────────────────────
    step_results, _, _, _ = _resolve_pushover_data(data, direction=direction)
    if not step_results:
        print("No pushover step results found.")
        return None

    n_step = len(step_results)

    # ── Collect per-element force history ────────────────────────
    # force_history[eid] = [(val_i, val_j), ...] one entry per step
    all_eids: set = set()
    force_history: dict[str, list[tuple[float, float]]] = {}
    for sd in step_results:
        ff = sd.get("frame_forces", {})
        for eid, entry in ff.items():
            all_eids.add(eid)
            if eid not in force_history:
                force_history[eid] = []
            if quantity == "Mz":
                v_i = abs(entry.get("mz_i", 0.0))
                v_j = abs(entry.get("mz_j", 0.0))
            elif quantity == "My":
                v_i = abs(entry.get("my_i", 0.0))
                v_j = abs(entry.get("my_j", 0.0))
            elif quantity == "Mx":
                v_i = abs(entry.get("mx_i", 0.0))
                v_j = abs(entry.get("mx_j", 0.0))
            elif quantity == "V":
                fz_i = entry.get("fz_i", 0.0)
                fy_i = entry.get("fy_i", 0.0)
                fz_j = entry.get("fz_j", 0.0)
                fy_j = entry.get("fy_j", 0.0)
                v_i = abs(fz_i) + abs(fy_i)
                v_j = abs(fz_j) + abs(fy_j)
            elif quantity == "N":
                v_i = abs(entry.get("fx_i", 0.0))
                v_j = abs(entry.get("fx_j", 0.0))
            else:
                v_i = abs(entry.get(f"{quantity.lower()}_i", 0.0))
                v_j = abs(entry.get(f"{quantity.lower()}_j", 0.0))
            force_history[eid].append((v_i, v_j))

    if not force_history:
        print("No frame element data found.")
        return None

    # Filter by element_ids if provided
    if element_ids is not None:
        selected = {eid: force_history[eid] for eid in element_ids if eid in force_history}
        if not selected:
            print(f"No data for requested element_ids: {element_ids}")
            return None
        force_history = selected

    n_elem = len(force_history)
    sorted_eids = sorted(force_history.keys())

    # ── Build X-axis values ──────────────────────────────────────
    if xaxis == "drift" and drifts is not None and len(drifts) >= n_step:
        x_vals = np.array(drifts[:n_step], dtype=float)
        x_label = "Roof drift (%)"
    else:
        x_vals = np.arange(n_step, dtype=float)
        x_label = "Push step"

    # ── Create subplot grid ──────────────────────────────────────
    ncols = min(n_elem, 3)
    nrows = max(1, (n_elem + 2) // 3)
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize, squeeze=False)

    for i, eid in enumerate(sorted_eids):
        row = i // 3
        col = i % 3
        ax = axes[row][col]

        history = force_history[eid]
        # Pad history to n_step (in case element appears mid-analysis)
        while len(history) < n_step:
            history.append((float("nan"), float("nan")))

        v_i_series = np.array([v[0] for v in history])
        v_j_series = np.array([v[1] for v in history])

        ax.plot(x_vals[: len(v_i_series)], v_i_series, "-o", markersize=3, label="I-end", **kwargs)
        ax.plot(x_vals[: len(v_j_series)], v_j_series, "-s", markersize=3, label="J-end", **kwargs)

        # Yield moment line
        if yield_moment is not None and eid in yield_moment:
            yv = yield_moment[eid]
            ax.axhline(
                yv, color="red", linestyle="--", linewidth=1, alpha=0.7, label=f"Yield ({yv:.1f})"
            )

        ax.set_title(f"Elem {eid}")
        ax.set_xlabel(x_label)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

    # Hide unused subplots
    for i in range(n_elem, nrows * ncols):
        row = i // 3
        col = i % 3
        fig.delaxes(axes[row][col])

    unit_map = {"Mz": "Moment", "My": "Moment", "Mx": "Moment", "V": "Shear", "N": "Axial"}
    ylabel = unit_map.get(quantity, quantity)
    # Add Y-label to the left-most column of each row
    for row in range(nrows):
        axes[row][0].set_ylabel(ylabel)

    fig.suptitle(f"Force evolution — {ylabel} ({quantity})", fontsize=12, fontweight="bold")
    fig.tight_layout()
    return fig


def plot_pushover_curve(
    pushover_results: dict[str, Any],
    title: str = None,
    figsize=(8, 6),
    **kwargs,
) -> Optional[Any]:
    """Plot the pushover capacity curve (base shear vs control displacement).

    Args:
        pushover_results: Output dict from
            :meth:`AnalysisBuilder.run_pushover_analysis`.
        title: Optional title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.
        **kwargs: Passed to ``matplotlib.pyplot.plot()``.

    Returns:
        The ``matplotlib.figure.Figure`` so the caller can ``.savefig()`` or
        ``.show()``.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  Install with: pip install matplotlib")
        return None

    disp = pushover_results.get("control_disp", [])
    shear = pushover_results.get("base_shear", [])

    if not disp or not shear:
        print("No pushover data to plot.")
        return None

    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(disp, shear, "-o", markersize=3, **kwargs or {})
    ax.set_xlabel("Control node displacement (m)")
    ax.set_ylabel("Base shear (kN)")
    ax.set_title(title or "Pushover Capacity Curve")
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color="grey", linewidth=0.5)
    ax.axvline(0, color="grey", linewidth=0.5)

    fig.tight_layout()
    return fig


def plot_pushover_curve_enhanced(
    pushover_results: dict[str, Any],
    title: str = None,
    figsize=(10, 6),
    design_disp: Optional[float] = None,
    unit_conversion: float = 1.0,
    **kwargs,
) -> Optional[Any]:
    """Enhanced pushover capacity curve with stiffness indicators.

    Plots base shear vs control displacement with:
    - **Initial and final tangent stiffness** (dashed lines)
    - **Area fill** under the curve
    - **Design drift marker** (optional vertical line)
    - Stiffness loss percentage annotation

    Args:
        pushover_results: Dict with ``'control_disp'`` and ``'base_shear'``
            keys (lists or arrays).  The :meth:`AnalysisBuilder.run_pushover_analysis`
            output uses kN; Tcl‑based output may use N — use *unit_conversion*.
        title: Plot title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.
        design_disp: Optional design drift displacement (m) to mark with a
            vertical dotted line.
        unit_conversion: Multiply base_shear by this factor (e.g. ``1/1000``
            if Tcl output is in N and desired display is kN).
        **kwargs: Passed to ``matplotlib.pyplot.plot()`` for the main curve.

    Returns:
        The ``matplotlib.figure.Figure``.
    """
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("Warning: matplotlib not installed.  Install with: pip install matplotlib")
        return None

    disp = np.asarray(pushover_results.get("control_disp", []), dtype=float)
    shear = np.asarray(pushover_results.get("base_shear", []), dtype=float)
    shear = shear * unit_conversion

    if len(disp) < 2 or len(shear) < 2 or len(disp) != len(shear):
        print("No pushover data to plot.")
        return None

    # Robust tangent stiffness via local regression over end segments
    n = len(disp)
    window = max(2, n // 20)  # 5 % of data points, at least 2
    # Initial stiffness: fit slope over first `window` points
    k0 = 0.0
    if disp[window - 1] > disp[0]:
        coeffs = np.polyfit(disp[:window], shear[:window], 1)
        k0 = coeffs[0]
    # Final stiffness: fit slope over last `window` points
    kf = 0.0
    if disp[-1] > disp[-window]:
        coeffs = np.polyfit(disp[-window:], shear[-window:], 1)
        kf = coeffs[0]
    loss_pct = (1 - kf / k0) * 100 if k0 > 0 else 0.0

    fig, ax = plt.subplots(figsize=figsize)

    # Main curve — merge caller kwargs with defaults
    plot_kw = {"label": "Pushover curve", "linewidth": 2}
    plot_kw.update(kwargs)
    ax.plot(disp, shear, "b-", **plot_kw)

    # Area fill
    ax.fill_between(disp, 0, shear, alpha=0.08, color="blue")

    # Initial stiffness line
    if k0 > 0:
        ax.plot(
            disp,
            k0 * disp,
            "r--",
            linewidth=1,
            alpha=0.5,
            label=f"Initial stiffness ({k0:.0f} kN/m)",
        )

    # Final stiffness line
    if kf > 0:
        ax.plot(
            disp, kf * disp, "g--", linewidth=1, alpha=0.5, label=f"Final stiffness ({kf:.0f} kN/m)"
        )

    # Design drift marker
    if design_disp is not None:
        ax.axvline(
            design_disp,
            color="orange",
            linestyle=":",
            linewidth=1.5,
            label=f"Design drift ({design_disp:.3f} m)",
        )

    # Labels & title
    ax.set_xlabel("Control node displacement (m)")
    ax.set_ylabel("Base shear (kN)")
    ax.set_title(title or "Pushover Capacity Curve")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color="grey", linewidth=0.5)
    ax.axvline(0, color="grey", linewidth=0.5)

    # Stiffness loss annotation
    ax.annotate(
        f"Stiffness loss: {loss_pct:.1f}%",
        xy=(0.97, 0.03),
        xycoords="axes fraction",
        ha="right",
        va="bottom",
        fontsize=10,
        bbox={"boxstyle": "round,pad=0.3", "fc": "lightyellow", "ec": "gray", "alpha": 0.8},
    )

    fig.tight_layout()
    return fig


def plot_capacity_spectrum(
    capacity_adrs: dict[str, list[float]],
    spectrum_periods: list[float],
    spectrum_accels: list[float],
    performance_point: dict[str, Any] = None,
    title: str = None,
    figsize=(8, 6),
) -> Optional[Any]:
    """Plot the capacity spectrum in ADRS format, overlaid on the demand
    response spectrum.

    Args:
        capacity_adrs: ADRS curve from
            :meth:`AnalysisBuilder.pushover_to_adrs` (dict with keys
            ``'S_a'`` and ``'S_d'``).
        spectrum_periods: Periods (s) defining the elastic demand spectrum.
        spectrum_accels: Spectral accelerations (m/s²) corresponding to
            *spectrum_periods*.
        performance_point: Optional result dict from
            :meth:`AnalysisBuilder.compute_performance_point`.  If provided
            the bilinear yield point and performance point are annotated.
        title: Optional title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.

    Returns:
        The ``matplotlib.figure.Figure``.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  Install with: pip install matplotlib")
        return None

    S_d = np.array(capacity_adrs.get("S_d", []))
    S_a = np.array(capacity_adrs.get("S_a", []))

    if len(S_d) < 2 or len(S_a) < 2 or len(S_d) != len(S_a):
        print("Insufficient or mismatched ADRS data to plot.")
        return None

    if len(spectrum_periods) != len(spectrum_accels):
        print("spectrum_periods and spectrum_accels have different lengths.")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    # --- Capacity spectrum ---
    ax.plot(S_d, S_a, "-o", markersize=3, label="Capacity (pushover)", color="tab:blue", zorder=3)

    # --- Demand spectrum (period lines + curve) ---
    T_spec = np.array(spectrum_periods)
    Sa_spec = np.array(spectrum_accels)
    Sd_spec = Sa_spec * (T_spec / (2.0 * math.pi)) ** 2
    ax.plot(Sd_spec, Sa_spec, "--", label="Demand (elastic)", color="tab:red", zorder=2)

    # --- Constant-period lines ---
    T_labels = [0.1, 0.2, 0.5, 1.0, 2.0, 4.0]
    S_d_max = max(S_d.max(), Sd_spec.max()) * 1.15
    S_a_max = max(S_a.max(), Sa_spec.max()) * 1.15
    for T in T_labels:
        sd_line = np.linspace(0, S_d_max, 50)
        sa_line = (2.0 * math.pi / T) ** 2 * sd_line
        ax.plot(sd_line, sa_line, ":", color="grey", linewidth=0.5, alpha=0.4)
        ax.text(
            sd_line[-1], sa_line[-1], f"T={T}s", fontsize=7, color="grey", alpha=0.6, va="bottom"
        )

    # --- Performance point ---
    if performance_point is not None:
        S_dp = performance_point.get("S_dp")
        S_ap = performance_point.get("S_ap")
        S_dy = performance_point.get("S_dy")
        S_ay = performance_point.get("S_ay")

        # Bilinear yield point
        if S_dy is not None and S_ay is not None and S_dy > 0:
            ax.plot(
                S_dy,
                S_ay,
                "s",
                color="tab:orange",
                markersize=8,
                zorder=5,
                label=f"Yield ({S_dy:.3f}, {S_ay:.1f})",
            )
            # Bilinear line
            sd_bilin = np.linspace(0, S_dy, 20)
            K_init = S_ay / S_dy
            ax.plot(sd_bilin, K_init * sd_bilin, "-", color="tab:orange", linewidth=1.5, alpha=0.7)
            # Post-yield line
            if S_dp is not None and S_dp > S_dy and S_dp > 0:
                sd_post = np.linspace(S_dy, max(S_dp * 1.2, S_d.max()), 20)
                K_post = (S_ap - S_ay) / (S_dp - S_dy) if S_dp != S_dy else 0
                ax.plot(
                    sd_post,
                    S_ay + K_post * (sd_post - S_dy),
                    "-",
                    color="tab:orange",
                    linewidth=1.5,
                    alpha=0.7,
                )

        # Performance point
        if S_dp is not None and S_ap is not None and S_dp > 0:
            ax.plot(
                S_dp,
                S_ap,
                "D",
                color="tab:green",
                markersize=10,
                zorder=6,
                label=f"Perf. Pt. ({S_dp:.3f}, {S_ap:.1f})",
            )
            # Vertical & horizontal dashed lines
            ax.axvline(S_dp, color="tab:green", linewidth=0.8, linestyle="--", alpha=0.5)
            ax.axhline(S_ap, color="tab:green", linewidth=0.8, linestyle="--", alpha=0.5)

    ax.set_xlabel("Spectral displacement S$_d$ (m)")
    ax.set_ylabel("Spectral acceleration S$_a$ (m/s²)")
    ax.set_title(title or "Capacity Spectrum Method – ADRS Format")
    ax.set_xlim(0, S_d_max)
    ax.set_ylim(0, S_a_max)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)

    fig.tight_layout()
    return fig
