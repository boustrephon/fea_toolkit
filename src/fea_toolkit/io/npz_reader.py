"""
Unified NPZ reader — loads ``.npz`` files written by ``npz_writer.py``
and returns numpy arrays compatible with Rhino, PyVista, and opstool.

Usage::

    from fea_toolkit.io.npz_reader import read_results_npz

    data = read_results_npz("results.npz")
    print(data["node_tag"])       # array of node tags
    print(data["static/DEAD/fx_i"])  # element forces for DEAD case

    # PyVista helper
    mesh = npz_to_pyvista_mesh("results.npz", deformed_case="DEAD")

    # Rhino helper
    colour_data = npz_to_rhino_colour("results.npz", quantity="fx_i", case="DEAD")
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def read_results_npz(path: str) -> Dict[str, Any]:
    """Load a unified NPZ file and return a dict of numpy arrays.

    The dict preserves the schema key names so consumers can access
    results by ``data["static/DEAD/fx_i"]``, ``data["modal/period"]``, etc.

    String arrays are loaded safely with ``allow_pickle=False``.
    """
    path = str(Path(path).resolve())
    return dict(np.load(path, allow_pickle=False))


def _get_static_cases(data: Dict[str, Any]) -> List[str]:
    """Return list of static case names present in the data."""
    labels = data.get("static_case_labels")
    if labels is not None:
        return [str(l) for l in labels]
    return []


def _get_analysis_types(data: Dict[str, Any]) -> List[str]:
    types = data.get("analysis_types")
    if types is not None:
        return [str(t) for t in types]
    return []


# ── PyVista adapter ───────────────────────────────────────────────────────

def npz_to_pyvista_frame_mesh(
    data: Dict[str, Any],
    deformed_case: Optional[str] = None,
    scale: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build frame line vertices and connectivity for PyVista.

    Returns ``(points, lines, displacements, sap_ids)`` where:

    * ``points`` — ``(N_frame*2, 3)`` float array of node I/J coordinates.
    * ``lines`` — ``(N_frame, 3)`` int array ``[2, i, j]`` for PyVista.
    * ``displacements`` — ``(N_frame*2, 3)`` float array, zero if undeformed.
    * ``sap_ids`` — ``(N_frame,)`` str array of SAP2000 FrameIDs.

    When *deformed_case* is provided, ``points`` are shifted by the
    nodal displacements for that case times *scale*.
    """
    nid = data.get("node_tag")
    if nid is None:
        return np.empty((0, 3)), np.empty((0, 3)), np.empty((0, 3)), np.empty((0,), dtype=str)

    coords = np.column_stack([
        data.get("node_x", []),
        data.get("node_y", []),
        data.get("node_z", []),
    ])
    ni = data.get("frame_node_i", [])
    nj = data.get("frame_node_j", [])
    n_frame = len(ni)

    if n_frame == 0:
        return np.empty((0, 3)), np.empty((0, 3)), np.empty((0, 3)), np.empty((0,), dtype=str)

    tag_to_idx = {int(t): i for i, t in enumerate(nid)}

    points = np.zeros((n_frame * 2, 3))
    lines = np.zeros((n_frame, 3), dtype=int)
    disp = np.zeros((n_frame * 2, 3))
    sap_ids = np.array(data.get("frame_sap_id", []), dtype=str)

    for ei in range(n_frame):
        i_idx = tag_to_idx.get(int(ni[ei]))
        j_idx = tag_to_idx.get(int(nj[ei]))
        if i_idx is None or j_idx is None:
            continue
        points[ei * 2] = coords[i_idx]
        points[ei * 2 + 1] = coords[j_idx]
        lines[ei] = [2, ei * 2, ei * 2 + 1]

        if deformed_case:
            key_prefix = f"static/{deformed_case}/node_d"
            dx = data.get(key_prefix + "x", np.zeros(len(nid)))
            dy = data.get(key_prefix + "y", np.zeros(len(nid)))
            dz = data.get(key_prefix + "z", np.zeros(len(nid)))
            disp[ei * 2] = [dx[i_idx] * scale, dy[i_idx] * scale,
                            dz[i_idx] * scale]
            disp[ei * 2 + 1] = [dx[j_idx] * scale, dy[j_idx] * scale,
                                dz[j_idx] * scale]

    return points, lines, disp, sap_ids


def npz_to_pyvista_shell_mesh(
    data: Dict[str, Any],
    deformed_case: Optional[str] = None,
    scale: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build shell quad vertices and face connectivity for PyVista.

    Returns ``(points, faces, displacements)``.
    """
    s1 = data.get("shell_node_1", [])
    n_shell = len(s1)
    if n_shell == 0:
        return np.empty((0, 3)), np.empty((0, 3)), np.empty((0, 3))

    nid = data.get("node_tag")
    coords = np.column_stack([
        data.get("node_x", []), data.get("node_y", []), data.get("node_z", []),
    ])
    tag_to_idx = {int(t): i for i, t in enumerate(nid)}

    points = np.zeros((n_shell * 4, 3))
    faces = np.zeros((n_shell, 5), dtype=int)
    disp = np.zeros((n_shell * 4, 3))
    sn = [s1, data.get("shell_node_2", []),
          data.get("shell_node_3", []), data.get("shell_node_4", [])]

    for ei in range(n_shell):
        for k in range(4):
            idx = tag_to_idx.get(int(sn[k][ei]))
            if idx is None:
                continue
            points[ei * 4 + k] = coords[idx]
            faces[ei] = [4, ei * 4, ei * 4 + 1, ei * 4 + 2, ei * 4 + 3]
            if deformed_case:
                key_prefix = f"static/{deformed_case}/node_d"
                dx = data.get(key_prefix + "x", np.zeros(len(nid)))
                dy = data.get(key_prefix + "y", np.zeros(len(nid)))
                dz = data.get(key_prefix + "z", np.zeros(len(nid)))
                disp[ei * 4 + k] = [dx[idx] * scale, dy[idx] * scale,
                                    dz[idx] * scale]

    return points, faces, disp


def npz_to_pyvista_modal_mesh(
    data: Dict[str, Any], mode_idx: int, scale: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build a deformed mesh for a given mode index.

    Returns ``(frame_points, frame_lines, shell_points, shell_faces)``
    where the node positions are offset by the eigenvector displacement.
    """
    nid = data.get("node_tag")
    coords = np.column_stack([
        data.get("node_x", []), data.get("node_y", []), data.get("node_z", []),
    ])
    tag_to_idx = {int(t): i for i, t in enumerate(nid)}

    dx = data.get("modal/mode_dx", np.zeros((len(nid), 1)))[:, mode_idx]
    dy = data.get("modal/mode_dy", np.zeros((len(nid), 1)))[:, mode_idx]
    dz = data.get("modal/mode_dz", np.zeros((len(nid), 1)))[:, mode_idx]

    def _shift_frame(pts, node_i, node_j):
        """Shift frame line endpoints by modal displacement.

        ``pts`` is ``(N_frame * 2, 3)`` — consecutive I/J endpoint pairs.
        ``node_i`` / ``node_j`` are ``(N_frame,)`` node tags.
        """
        shifted = pts.copy()
        n_frames = len(node_i)
        for ei in range(n_frames):
            i_idx = tag_to_idx.get(int(node_i[ei]))
            j_idx = tag_to_idx.get(int(node_j[ei]))
            if i_idx is not None:
                shifted[ei * 2, 0] += dx[i_idx] * scale
                shifted[ei * 2, 1] += dy[i_idx] * scale
                shifted[ei * 2, 2] += dz[i_idx] * scale
            if j_idx is not None:
                shifted[ei * 2 + 1, 0] += dx[j_idx] * scale
                shifted[ei * 2 + 1, 1] += dy[j_idx] * scale
                shifted[ei * 2 + 1, 2] += dz[j_idx] * scale
        return shifted

    def _shift_shell(pts, n1, n2, n3, n4):
        """Shift shell corner points by modal displacement.

        ``pts`` is ``(N_shell * 4, 3)`` — consecutive corner triples.
        """
        shifted = pts.copy()
        n_shells = len(n1)
        for si in range(n_shells):
            for k, arr in enumerate([n1, n2, n3, n4]):
                idx = tag_to_idx.get(int(arr[si]))
                if idx is not None:
                    shifted[si * 4 + k, 0] += dx[idx] * scale
                    shifted[si * 4 + k, 1] += dy[idx] * scale
                    shifted[si * 4 + k, 2] += dz[idx] * scale
        return shifted

    f_n1 = data.get("frame_node_i", np.array([]))
    f_n2 = data.get("frame_node_j", np.array([]))
    f_pts, f_lines, _, _ = npz_to_pyvista_frame_mesh(data)
    if len(f_pts) > 0 and len(f_n1) > 0:
        f_pts = _shift_frame(f_pts, f_n1, f_n2)

    s_n1 = data.get("shell_node_1", np.array([]))
    s_n2 = data.get("shell_node_2", np.array([]))
    s_n3 = data.get("shell_node_3", np.array([]))
    s_n4 = data.get("shell_node_4", np.array([]))
    s_pts, s_faces, _ = npz_to_pyvista_shell_mesh(data)
    if len(s_pts) > 0 and len(s_n1) > 0:
        s_pts = _shift_shell(s_pts, s_n1, s_n2, s_n3, s_n4)

    return f_pts, f_lines, s_pts, s_faces

    return f_pts, f_lines, s_pts, s_faces


# ── Rhino adapter ─────────────────────────────────────────────────────────

def npz_to_rhino_colour_data(
    data: Dict[str, Any],
    quantity: str = "fx_i",
    case: Optional[str] = None,
) -> Dict[str, float]:
    """Extract per-element scalar data for Rhino colouring.

    Returns ``{sap_frame_id: value}`` dict matching SAP_FrameID UserStrings.

    Args:
        data: Loaded NPZ dict from ``read_results_npz()``.
        quantity: Force quantity, e.g. ``"fx_i"``, ``"my_j"``.
        case: Static case name.  ``None`` = first available case.

    Returns:
        Dict mapping SAP frame ID (string) → scalar value.
    """
    cases = _get_static_cases(data)
    if not cases:
        return {}
    case_name = case if case is not None else cases[0]
    key = f"static/{case_name}/{quantity}"
    vals = data.get(key)
    sap_ids = data.get("frame_sap_id")
    if vals is None or sap_ids is None:
        return {}
    return {str(sap_ids[i]): float(vals[i]) for i in range(len(vals))}


def npz_build_id_tag_map(data: Dict[str, Any]) -> Dict[str, int]:
    """Build a mapping from SAP2000 string node ID → OpenSees node tag.

    Returns {sap_node_id: node_tag}, e.g. ``{"1": 1, "2": 2, ...}``.
    """
    sap_ids = data.get("node_sap_id")
    tags = data.get("node_tag")
    if sap_ids is None or tags is None:
        return {}
    return {str(sap_ids[i]): int(tags[i]) for i in range(len(sap_ids))}


def npz_build_child_map(data: Dict[str, Any]) -> Dict[str, list]:
    """Build a mapping from parent SAP ID → list of child SAP IDs.

    Useful when the Rhino model has original (un-split) geometry and
    results come from the meshed model.  For each parent, the map gives
    all children that replaced it after splitting.

    Returns {parent_sap_id: [child_sap_id, ...]}, e.g.
    ``{"1": ["1-0", "1-1"], "2": ["2-0"], ...}``.
    """
    child_ids = data.get("frame_sap_id")
    parent_ids = data.get("frame_parent_sap_id")
    if child_ids is None or parent_ids is None:
        return {}
    result: Dict[str, list] = {}
    for i in range(len(child_ids)):
        pid = str(parent_ids[i])
        if pid:
            result.setdefault(pid, []).append(str(child_ids[i]))
    return result


def npz_build_parent_map(data: Dict[str, Any]) -> Dict[str, str]:
    """Build a reverse mapping from child SAP ID → parent SAP ID.

    Returns {child_sap_id: parent_sap_id}, e.g.
    ``{"1-0": "1", "1-1": "1", "2-0": "2", ...}``.
    """
    child_ids = data.get("frame_sap_id")
    parent_ids = data.get("frame_parent_sap_id")
    if child_ids is None or parent_ids is None:
        return {}
    return {str(child_ids[i]): str(parent_ids[i])
            for i in range(len(child_ids)) if str(parent_ids[i])}
