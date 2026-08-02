"""Modal disconnect diagnostics — identify nodes that are disconnected
from the main structural system using mode shape analysis.

A node that is poorly connected will exhibit anomalously large
eigenvector components even in low-frequency modes, because there is
no stiffness path restraining it.

Usage from cached NPZ::

    from fea_toolkit.io.npz_reader import read_results_npz
    from fea_toolkit.plotting.diagnostics import (
        find_disconnected_nodes,
        plot_disconnected_nodes,
    )

    data = read_results_npz("output/results.npz")
    report = find_disconnected_nodes(data, top_n=20)
    for item in report:
        print(f"Node {item['node_tag']}: score={item['score']:.2f}  "
              f"max_mode={item['worst_mode']}")

    # 3D view
    plot_disconnected_nodes(data, report)
"""

from typing import Any, Optional

import numpy as np

from .viz import _set_isometric_view


def find_disconnected_nodes(
    data: dict[str, np.ndarray],
    top_n: int = 30,
    z_score_threshold: float = 3.0,
    max_modes: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Identify disconnected nodes from mode shape data using outlier
    detection.

    For each mode, the per-node displacement magnitude is computed and
    Z‑normalised.  Nodes with |Z| > *z_score_threshold* in a given mode
    are flagged as outliers.  The final **suspicion score** is the number
    of modes (as a fraction of all modes) in which the node appears as
    an outlier.

    Args:
        data: NPZ dict from ``read_results_npz()`` containing
            ``modal/mode_dx``, ``modal/mode_dy``, ``modal/mode_dz``
            (all ``(N_node, N_mode)``).
        top_n: Maximum number of outliers to report.
        z_score_threshold: Z‑score above which a node is considered
            an outlier in a mode (default 3.0).
        max_modes: Analyse only the first *max_modes* modes.  ``None``
            = all available modes.

    Returns:
        List of dicts sorted by descending suspicion score::

            {
                "node_tag": int,
                "x": float, "y": float, "z": float,
                "score": float,        # fraction of modes where flagged
                "worst_mode": int,     # 0‑based mode with highest |Z|
                "max_magnitude": float,  # peak eigenvector displacement
                "z_scores": List[float],  # Z per mode
            }
    """
    dx = data.get("modal/mode_dx")
    dy = data.get("modal/mode_dy")
    dz = data.get("modal/mode_dz")
    if dx is None or dy is None or dz is None:
        print("No mode shape data found (keys 'modal/mode_dx/y/z').")
        return []

    n_modes = dx.shape[1] if dx.ndim > 1 else 1
    if max_modes is not None:
        n_modes = min(n_modes, max_modes)

    n_nodes = len(dx)
    # Displacement magnitude per node per mode
    mag = np.sqrt(dx[:, :n_modes] ** 2 + dy[:, :n_modes] ** 2 + dz[:, :n_modes] ** 2)

    node_tags = data.get("node_tag", np.arange(n_nodes))
    coords = np.column_stack(
        [
            data.get("node_x", np.zeros(n_nodes)),
            data.get("node_y", np.zeros(n_nodes)),
            data.get("node_z", np.zeros(n_nodes)),
        ]
    )

    # Z-score per mode — within each mode, how many stds from mean
    results = []
    for ni in range(n_nodes):
        z_scores = []
        outlier_count = 0
        worst_z = 0.0
        worst_m = 0
        for mi in range(n_modes):
            col = mag[:, mi]
            mean = float(np.mean(col))
            std = float(np.std(col))
            z = 0.0 if std < 1e-12 else (float(mag[ni, mi]) - mean) / std
            z_scores.append(z)
            if abs(z) > z_score_threshold:
                outlier_count += 1
                if abs(z) > abs(worst_z):
                    worst_z = z
                    worst_m = mi

        score = outlier_count / max(n_modes, 1)
        if score == 0:
            continue
        results.append(
            {
                "node_tag": int(node_tags[ni]),
                "x": float(coords[ni, 0]),
                "y": float(coords[ni, 1]),
                "z": float(coords[ni, 2]),
                "score": score,
                "worst_mode": worst_m,
                "max_magnitude": float(np.max(mag[ni, :])),
                "z_scores": z_scores,
            }
        )

    results.sort(key=lambda r: -r["score"])
    return results[:top_n]


def print_disconnect_report(
    report: list[dict[str, Any]], n_modes: int, z_score_threshold: float = 3.0
) -> None:
    """Print a formatted table of disconnected-node findings.

    Args:
        report: Output from :func:`find_disconnected_nodes`.
        n_modes: Total number of modes analysed (for column headings).
        z_score_threshold: Z-score threshold used for detection.
    """
    if not report:
        print("No disconnected nodes detected.")
        return

    print(f"\n{'=' * 70}")
    print("  MODAL DISCONNECT DETECTION")
    print(f"{'=' * 70}")
    print(f"  Analysed {n_modes} mode(s), Z > {z_score_threshold} = outlier")
    print()
    print(f"  {'Node':>8} {'Score':>7} {'Worst':>5} {'Coords (x, y, z)':>30}")
    print(f"  {'':>8} {'':>7} {'Mode':>5} {'':>30}")
    print(f"  {'-' * 54}")
    for r in report:
        print(
            f"  {r['node_tag']:>8} {r['score']:.2f} {r['worst_mode']:>5}  "
            f"({r['x']:8.2f}, {r['y']:8.2f}, {r['z']:8.2f})"
        )
    print()


def plot_disconnected_nodes(
    data: dict[str, np.ndarray],
    report: list[dict[str, Any]],
    show_labels: bool = True,
) -> Optional[Any]:
    """PyVista 3D scatter plot of the model with disconnected nodes
    highlighted in red, sized by suspicion score.

    Args:
        data: NPZ dict (needs ``frame_node_i``, ``frame_node_j``, etc.
            for the wireframe model).
        report: Output from :func:`find_disconnected_nodes`.
        show_labels: If True, label each flagged node with its tag.

    Returns:
        ``pyvista.Plotter`` or ``None`` if PyVista is not installed.

    .. todo::
        Add ``plotter.add_plane_clipper(widget_color=\"red\", normal=\"+z\")``
        for an interactive clipping plane.  Requires PyVista ≥ 0.50.
    """
    if not report:
        print("No disconnected nodes to plot.")
        return None

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed.")
        return None

    pv.set_plot_theme("document")

    nid = data.get("node_tag", [])
    coords = np.column_stack(
        [
            data.get("node_x", []),
            data.get("node_y", []),
            data.get("node_z", []),
        ]
    )
    tag_to_idx = {int(t): i for i, t in enumerate(nid)}

    # ── Frame wireframe ──
    ni = data.get("frame_node_i", [])
    nj = data.get("frame_node_j", [])
    n_frame = len(ni)
    if n_frame > 0:
        pts = np.zeros((n_frame * 2, 3))
        lines = np.zeros((n_frame, 3), dtype=int)
        for ei in range(n_frame):
            i_idx = tag_to_idx.get(int(ni[ei]))
            j_idx = tag_to_idx.get(int(nj[ei]))
            if i_idx is None or j_idx is None:
                continue
            pts[ei * 2] = coords[i_idx]
            pts[ei * 2 + 1] = coords[j_idx]
            lines[ei] = [2, ei * 2, ei * 2 + 1]
        frame_mesh = pv.PolyData(pts, lines=lines)
    else:
        frame_mesh = pv.PolyData()

    # ── Shell quads ──
    s1 = data.get("shell_node_1", [])
    s2 = data.get("shell_node_2", [])
    s3 = data.get("shell_node_3", [])
    s4 = data.get("shell_node_4", [])
    n_shell = len(s1)
    if n_shell > 0:
        quad_pts = np.zeros((n_shell * 4, 3))
        faces = np.zeros((n_shell, 5), dtype=int)
        for ei in range(n_shell):
            for k, sn in enumerate([s1, s2, s3, s4]):
                idx = tag_to_idx.get(int(sn[ei]))
                if idx is not None:
                    quad_pts[ei * 4 + k] = coords[idx]
            faces[ei] = [4, ei * 4, ei * 4 + 1, ei * 4 + 2, ei * 4 + 3]
        shell_mesh = pv.PolyData(quad_pts, faces=faces)
    else:
        shell_mesh = pv.PolyData()

    plotter = pv.Plotter()

    # Semi-transparent shell surfaces
    if shell_mesh.n_points > 0:
        plotter.add_mesh(
            shell_mesh, color="lightblue", opacity=0.15, show_edges=False, lighting=False
        )

    # Frame wireframe (slightly darker than shells)
    if frame_mesh.n_points > 0:
        plotter.add_mesh(frame_mesh, color="#555555", line_width=2, opacity=0.8)

    # Flagged nodes — red spheres ~0.15-0.35 m radius
    max_score = max(r["score"] for r in report)
    for r in report:
        radius = 0.15 + 0.2 * (r["score"] / max(max_score, 1e-12))
        sphere = pv.Sphere(radius=radius, center=(r["x"], r["y"], r["z"]))
        plotter.add_mesh(sphere, color="red", opacity=0.7)
        if show_labels:
            label = f"Node {r['node_tag']} (score={r['score']:.2f})"
            plotter.add_point_labels(
                np.array([[r["x"], r["y"], r["z"]]]),
                [label],
                font_size=12,
                point_size=0,
                shape_opacity=0.6,
            )

    plotter.show_grid()
    # Use the same isometric view as all other visualisations in this
    # codebase.  Terrain interaction style keeps Z up during rotation.
    plotter.enable_terrain_style()
    _set_isometric_view(plotter)
    return plotter


# ═══════════════════════════════════════════════════════════════════════
# Wall-in-slab intersection visualisation
# ═══════════════════════════════════════════════════════════════════════


def plot_wall_slab_intersections(
    area_elements: dict[str, Any],
    area_assignments: dict[str, str],
    nodes: dict[str, Any],
    findings: list[dict[str, Any]],
    slab_id: Optional[str] = None,
    context_radius: float = 4.0,
    show_labels: bool = True,
) -> Optional[Any]:
    """PyVista 3D view of wall-edge nodes inside slab areas.

    For each wall‑slab intersection (or a specific slab), renders:

    * The slab area as a translucent blue quad with labelled corner nodes.
    * The intersecting wall node(s) as red spheres with labels.
    * The intersecting wall element outline(s) as thick red edge lines.
    * Adjacent wall elements that share an edge with the slab (continue
      above or below) as orange wireframes.
    * Nearby slab areas within *context_radius* for spatial reference.

    Args:
        area_elements: ``{area_id: AreaElement}`` from the model.
        area_assignments: ``{area_id: section_name}``.
        nodes: ``{node_id: Node}`` with ``.x``, ``.y``, ``.z``, ``.node_tag``.
        findings: Output from :func:`~fea_toolkit.model.geometry.find_wall_nodes_inside_slabs`.
        slab_id: If given, only render intersections for this slab.
            If ``None``, render all findings.
        context_radius: Include other slab areas within this distance
            of the target slab's bounding box (same units as model).
        show_labels: If True, label slab corner nodes and wall nodes.

    Returns:
        ``pyvista.Plotter`` or ``None`` if PyVista is not installed.

    Usage::

        from fea_toolkit.model.geometry import find_wall_nodes_inside_slabs
        from fea_toolkit.plotting.diagnostics import plot_wall_slab_intersections

        findings = find_wall_nodes_inside_slabs(
            md.area_elements, md.area_assignments, md.nodes)
        plotter = plot_wall_slab_intersections(
            md.area_elements, md.area_assignments, md.nodes, findings,
            slab_id="335",  # optional — show one slab
        )
        plotter.show()
    """
    if not findings:
        print("No wall‑slab intersections to plot.")
        return None

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed.")
        return None

    pv.set_plot_theme("document")
    plotter = pv.Plotter()

    # ── Filter findings ──────────────────────────────────────────
    if slab_id is not None:
        relevant = [f for f in findings if f["slab_id"] == slab_id]
        if not relevant:
            print(f"Slab {slab_id} not found in findings.")
            return None
    else:
        relevant = findings

    # Collect all unique slab IDs involved
    target_slab_ids: set = {f["slab_id"] for f in relevant}

    # ── Determine the bounding box to zoom to ────────────────────
    all_x, all_y, all_z = [], [], []
    for f in relevant:
        all_x.extend(f["slab_X"])
        all_y.extend(f["slab_Y"])
        all_z.append(f["slab_Z"])
        for wn in f["nodes"]:
            all_x.append(wn["x"])
            all_y.append(wn["y"])
            all_z.append(wn["z"])

    if not all_x:
        return None

    zoom_x = (min(all_x), max(all_x))
    zoom_y = (min(all_y), max(all_y))
    zoom_z = (min(all_z), max(all_z))

    # ── Context slabs — other slab areas near the target ─────────
    # Extend bbox by context_radius for context query
    ctx_x_min = zoom_x[0] - context_radius
    ctx_x_max = zoom_x[1] + context_radius
    ctx_y_min = zoom_y[0] - context_radius
    ctx_y_max = zoom_y[1] + context_radius
    ctx_z_min = zoom_z[0] - context_radius
    ctx_z_max = zoom_z[1] + context_radius

    context_slabs = set()
    for aid, ae in area_elements.items():
        if getattr(ae, "inactive", False):
            continue
        nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        nds = [n for n in nds if n is not None]
        if len(nds) < 4:
            continue
        xs = [n.x for n in nds]
        ys = [n.y for n in nds]
        zs = [n.z for n in nds]
        z_span = max(zs) - min(zs)
        if z_span > 0.5:
            continue  # vertical area, not slab
        avg_z = sum(zs) / len(zs)
        if not (ctx_z_min <= avg_z <= ctx_z_max):
            continue
        ax_min, ax_max = min(xs), max(xs)
        ay_min, ay_max = min(ys), max(ys)
        # Check overlap with extended bbox
        if ax_max < ctx_x_min or ax_min > ctx_x_max or ay_max < ctx_y_min or ay_min > ctx_y_max:
            continue
        context_slabs.add(aid)

    # Also include the wall areas themselves
    wall_ids: set = {f["wall_id"] for f in relevant}

    # ── Helper: build area quad ──────────────────────────────────
    def _area_quad(
        aid: str, color: str, opacity: float, edge_color: str, label: bool = False
    ) -> None:
        ae = area_elements.get(aid)
        if ae is None:
            return
        nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        nds = [n for n in nds if n is not None]
        if len(nds) < 4:
            return
        pts = np.array([[n.x, n.y, n.z] for n in nds[:4]])
        face = np.array([[4, 0, 1, 2, 3]])
        mesh = pv.PolyData(pts, faces=face)
        plotter.add_mesh(
            mesh,
            color=color,
            opacity=opacity,
            show_edges=True,
            edge_color=edge_color,
            line_width=1,
            lighting=False,
        )
        if label:
            labels = [f"{aid}\\n{n.node_id}(t={n.node_tag})" for n in nds[:4]]
            plotter.add_point_labels(pts, labels, font_size=8, point_size=4, shape_opacity=0.5)

    # ── 1. Context slabs (light grey, low opacity) ───────────────
    for aid in context_slabs - target_slab_ids:
        _area_quad(aid, "lightgrey", 0.1, "grey")

    # ── 2. Target slab(s) (blue, labelled corners) ────────────────
    for sid in target_slab_ids:
        _area_quad(sid, "lightblue", 0.3, "blue", label=show_labels)

    # ── 3. Walls with edges on this slab (orange, below/above) ──
    # Find all wall areas that share a Z-level edge with the target
    # slab — these are the walls that continue above or below.
    adjacent_wall_ids: set = set()
    for sid in target_slab_ids:
        ae = area_elements.get(sid)
        if ae is None:
            continue
        slab_nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        slab_nds = [n for n in slab_nds if n is not None]
        if len(slab_nds) < 4:
            continue
        sx_min = min(n.x for n in slab_nds)
        sx_max = max(n.x for n in slab_nds)
        sy_min = min(n.y for n in slab_nds)
        sy_max = max(n.y for n in slab_nds)
        slab_z = round(sum(n.z for n in slab_nds) / len(slab_nds), 4)

        for wid, wae in area_elements.items():
            if getattr(wae, "inactive", False):
                continue
            if wid in wall_ids:
                continue  # already shown as intersecting wall
            if wid in target_slab_ids:
                continue  # not a wall
            wnds = [nodes.get(n) for n in wae.node_ids if n in nodes]
            wnds = [n for n in wnds if n is not None]
            if len(wnds) < 4:
                continue
            # Check if this wall has any node at the slab's Z level
            # AND within the slab's XY bounds
            for wn in wnds:
                if abs(wn.z - slab_z) > 0.01:
                    continue
                if not (sx_min - 0.01 <= wn.x <= sx_max + 0.01):
                    continue
                if not (sy_min - 0.01 <= wn.y <= sy_max + 0.01):
                    continue
                adjacent_wall_ids.add(wid)
                break

    for wid in adjacent_wall_ids:
        ae = area_elements.get(wid)
        if ae is None:
            continue
        nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        nds = [n for n in nds if n is not None]
        if len(nds) < 4:
            continue
        pts = np.array([[n.x, n.y, n.z] for n in nds[:4]])
        line_pts = np.vstack([pts, pts[0]])
        n_seg = len(line_pts) - 1
        lines = np.array([[2, i, i + 1] for i in range(n_seg)], dtype=int)
        wall_mesh = pv.PolyData(line_pts, lines=lines)
        assign = area_assignments.get(wid, "")
        plotter.add_mesh(
            wall_mesh,
            color="orange",
            line_width=2,
            opacity=0.5,
            label=f"Adj. wall {wid} ({assign})",
        )

    # ── 4. Intersecting wall outlines (red edges) ────────────────
    for wid in wall_ids:
        ae = area_elements.get(wid)
        if ae is None:
            continue
        nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        nds = [n for n in nds if n is not None]
        if len(nds) < 4:
            continue
        pts = np.array([[n.x, n.y, n.z] for n in nds[:4]])
        line_pts = np.vstack([pts, pts[0]])  # close the loop
        n_seg = len(line_pts) - 1
        lines = np.array([[2, i, i + 1] for i in range(n_seg)], dtype=int)
        wall_mesh = pv.PolyData(line_pts, lines=lines)
        assign = area_assignments.get(wid, "")
        plotter.add_mesh(
            wall_mesh, color="red", line_width=3, opacity=0.9, label=f"Wall {wid} ({assign})"
        )

    # ── 5. Wall nodes inside slab (red spheres, labelled) ─────────
    for f in relevant:
        for wn in f["nodes"]:
            center = (wn["x"], wn["y"], wn["z"])
            sphere = pv.Sphere(radius=0.25, center=center)
            plotter.add_mesh(sphere, color="red", opacity=0.9)
            if show_labels:
                label = f"{wn['node_id']}(t={wn['node_tag']})"
                plotter.add_point_labels(
                    np.array([center]),
                    [label],
                    font_size=10,
                    point_size=0,
                    shape_opacity=0.6,
                    text_color="red",
                )

    # ── 6. Legend ───────────────────────────────────────────────
    plotter.add_legend(border=True, size=(0.28, 0.18))

    # ── Camera ──────────────────────────────────────────────────
    plotter.show_grid()
    plotter.enable_terrain_style()
    cx = (zoom_x[0] + zoom_x[1]) * 0.5
    cy = (zoom_y[0] + zoom_y[1]) * 0.5
    cz = (zoom_z[0] + zoom_z[1]) * 0.5
    d = max(zoom_x[1] - zoom_x[0], zoom_y[1] - zoom_y[0], zoom_z[1] - zoom_z[0], 1.0) * 1.8
    plotter.camera.position = (cx + d, cy + d * 0.6, cz + d * 0.4)
    plotter.camera.focal_point = (cx, cy, cz)
    plotter.camera.up = (0, 0, 1)

    return plotter
