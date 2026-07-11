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

from typing import Dict, List, Optional, Any, Tuple
import numpy as np


def find_disconnected_nodes(
    data: Dict[str, np.ndarray],
    top_n: int = 30,
    z_score_threshold: float = 3.0,
    max_modes: Optional[int] = None,
) -> List[Dict[str, Any]]:
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
    mag = np.sqrt(dx[:, :n_modes] ** 2 + dy[:, :n_modes] ** 2
                  + dz[:, :n_modes] ** 2)

    node_tags = data.get("node_tag", np.arange(n_nodes))
    coords = np.column_stack([
        data.get("node_x", np.zeros(n_nodes)),
        data.get("node_y", np.zeros(n_nodes)),
        data.get("node_z", np.zeros(n_nodes)),
    ])

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
            if std < 1e-12:
                z = 0.0
            else:
                z = (float(mag[ni, mi]) - mean) / std
            z_scores.append(z)
            if abs(z) > z_score_threshold:
                outlier_count += 1
                if abs(z) > abs(worst_z):
                    worst_z = z
                    worst_m = mi

        score = outlier_count / max(n_modes, 1)
        results.append({
            "node_tag": int(node_tags[ni]),
            "x": float(coords[ni, 0]),
            "y": float(coords[ni, 1]),
            "z": float(coords[ni, 2]),
            "score": score,
            "worst_mode": worst_m,
            "max_magnitude": float(np.max(mag[ni, :])),
            "z_scores": z_scores,
        })

    results.sort(key=lambda r: -r["score"])
    return results[:top_n]


def print_disconnect_report(report: List[Dict[str, Any]],
                              n_modes: int) -> None:
    """Print a formatted table of disconnected-node findings.

    Args:
        report: Output from :func:`find_disconnected_nodes`.
        n_modes: Total number of modes analysed (for column headings).
    """
    if not report:
        print("No disconnected nodes detected.")
        return

    print(f"\n{'=' * 70}")
    print("  MODAL DISCONNECT DETECTION")
    print(f"{'=' * 70}")
    print(f"  Analysed {n_modes} mode(s), Z > 3.0 = outlier")
    print()
    print(f"  {'Node':>8} {'Score':>7} {'Worst':>5} {'Coords (x, y, z)':>30}")
    print(f"  {'':>8} {'':>7} {'Mode':>5} {'':>30}")
    print(f"  {'-' * 54}")
    for r in report:
        print(f"  {r['node_tag']:>8} {r['score']:.2f} {r['worst_mode']:>5}  "
              f"({r['x']:8.2f}, {r['y']:8.2f}, {r['z']:8.2f})")
    print()


def plot_disconnected_nodes(
    data: Dict[str, np.ndarray],
    report: List[Dict[str, Any]],
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

    # Build frame wireframe
    ni = data.get("frame_node_i", [])
    nj = data.get("frame_node_j", [])
    nid = data.get("node_tag", [])
    coords = np.column_stack([
        data.get("node_x", []), data.get("node_y", []), data.get("node_z", []),
    ])
    tag_to_idx = {int(t): i for i, t in enumerate(nid)}

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
        wire = pv.PolyData(pts, lines=lines)
    else:
        wire = pv.PolyData()

    plotter = pv.Plotter()

    # Grey wireframe for context
    if wire.n_points > 0:
        plotter.add_mesh(wire, color="lightgrey", line_width=1, opacity=0.7)

    # Flagged nodes — red spheres ~0.5 m radius, slightly larger for
    # high-score nodes.
    max_score = max(r["score"] for r in report)
    for r in report:
        radius = 0.3 + 0.4 * (r["score"] / max(max_score, 1e-12))
        sphere = pv.Sphere(radius=radius, center=(r["x"], r["y"], r["z"]))
        plotter.add_mesh(sphere, color="red", opacity=0.7)
        if show_labels:
            label = f"Node {r['node_tag']} (score={r['score']:.2f})"
            plotter.add_point_labels(
                np.array([[r["x"], r["y"], r["z"]]]),
                [label],
                font_size=8, point_size=0, shape_opacity=0.6,
            )

    plotter.show_grid()
    # Isometric view with Z-up (keeps vertical lines vertical)
    bounds = plotter.bounds
    cx = (bounds[0] + bounds[1]) * 0.5
    cy = (bounds[2] + bounds[3]) * 0.5
    cz = (bounds[4] + bounds[5]) * 0.5
    horiz = max(bounds[1] - bounds[0], bounds[3] - bounds[2], 0.1)
    z_range = max(bounds[5] - bounds[4], 1.0)
    dist = max(horiz, z_range) * 1.5
    plotter.camera.position = (cx + dist, cy + dist, cz + dist * 0.4)
    plotter.camera.focal_point = (cx, cy, cz)
    plotter.camera.view_up = (0.0, 0.0, 1.0)
    return plotter
