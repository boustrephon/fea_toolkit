"""
Unified NPZ writer — assembles model geometry + analysis results into a
single ``.npz`` file following the schema in ``results_schema.py``.

Usage::

    from fea_toolkit.io.npz_writer import write_results_npz

    write_results_npz(
        "results.npz",
        md=model_data,
        static_results={"DEAD": {...}, "SL_X": {...}},
        modal_result={"periods": [...], "mode_shapes": {...}, ...},
        rs_results={"rs_x": {...}, "rs_y": {...}},
    )
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .results_schema import make_static_key
from ..model.sap_data import SAPModelData


def _collect_geometry(md: SAPModelData) -> Dict[str, np.ndarray]:
    """Extract model geometry arrays from SAPModelData."""
    arrays: Dict[str, np.ndarray] = {}

    # Nodes
    node_tags = []
    node_sap_ids = []
    node_x, node_y, node_z = [], [], []
    for nid, nd in md.nodes.items():
        node_tags.append(nd.node_tag)
        node_sap_ids.append(str(nid))
        node_x.append(nd.x)
        node_y.append(nd.y)
        node_z.append(nd.z)
    arrays["node_tag"] = np.array(node_tags, dtype=int)
    arrays["node_sap_id"] = np.array(node_sap_ids, dtype=str)
    arrays["node_x"] = np.array(node_x, dtype=float)
    arrays["node_y"] = np.array(node_y, dtype=float)
    arrays["node_z"] = np.array(node_z, dtype=float)

    # Frame elements
    frame_eid, frame_sap_id, frame_sec_name = [], [], []
    frame_ni, frame_nj = [], []
    for eid, elem in md.frame_elements.items():
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        frame_eid.append(len(frame_eid))
        frame_sap_id.append(str(eid))
        sec = md.frame_assignments.get(eid, "")
        frame_sec_name.append(sec)
        frame_ni.append(ni.node_tag)
        frame_nj.append(nj.node_tag)

    arrays["frame_eid"] = np.array(frame_eid, dtype=int)
    arrays["frame_sap_id"] = np.array(frame_sap_id, dtype=str)
    arrays["frame_sec_name"] = np.array(frame_sec_name, dtype=str)
    arrays["frame_node_i"] = np.array(frame_ni, dtype=int)
    arrays["frame_node_j"] = np.array(frame_nj, dtype=int)

    # Shell elements (quad only)
    shell_eid, shell_sap_id, shell_sec_name = [], [], []
    s1, s2, s3, s4 = [], [], [], []
    for aid, area in md.area_elements.items():
        if getattr(area, 'inactive', False):
            continue
        if len(area.node_ids) < 3:
            continue
        nids = area.node_ids[:4]
        tags = []
        for nid in nids:
            nd = md.nodes.get(nid)
            if nd is None:
                break
            tags.append(nd.node_tag)
        if len(tags) < 3:
            continue
        # Pad to 4 (repeat last for triangles)
        while len(tags) < 4:
            tags.append(tags[-1])
        shell_eid.append(len(shell_eid))
        shell_sap_id.append(str(aid))
        sec2 = md.area_assignments.get(aid, "")
        shell_sec_name.append(sec2)
        s1.append(tags[0])
        s2.append(tags[1])
        s3.append(tags[2])
        s4.append(tags[3])

    arrays["shell_eid"] = np.array(shell_eid, dtype=int)
    arrays["shell_sap_id"] = np.array(shell_sap_id, dtype=str)
    arrays["shell_sec_name"] = np.array(shell_sec_name, dtype=str)
    arrays["shell_node_1"] = np.array(s1, dtype=int)
    arrays["shell_node_2"] = np.array(s2, dtype=int)
    arrays["shell_node_3"] = np.array(s3, dtype=int)
    arrays["shell_node_4"] = np.array(s4, dtype=int)

    return arrays


def _collect_static(static_results: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Extract static analysis arrays."""
    arrays: Dict[str, np.ndarray] = {}
    case_labels = list(static_results.keys())
    arrays["static_case_labels"] = np.array(case_labels, dtype=str)

    force_keys = [
        "fx_i", "fy_i", "fz_i", "mx_i", "my_i", "mz_i",
        "fx_j", "fy_j", "fz_j", "mx_j", "my_j", "mz_j",
    ]
    for case in case_labels:
        data = static_results[case]
        for key in force_keys:
            vals = data.get(key, data.get("element_forces", {}).get(key, []))
            arrays[make_static_key(case, key)] = np.asarray(vals, dtype=float)

        # Nodal displacements
        disp = data.get("nodal_displacements", {})
        if disp:
            # Convert dict to array ordered by node_tag
            tags = sorted(disp.keys(), key=int)
            for i, dof in enumerate(["dx", "dy", "dz"]):
                arr = np.array([disp[t][i] for t in tags], dtype=float)
                arrays[make_static_key(case, f"node_{dof}")] = arr

    return arrays


def _collect_modal(modal_result: Dict[str, Any],
                   mode_shapes: Optional[Dict] = None) -> Dict[str, np.ndarray]:
    """Extract modal analysis arrays."""
    arrays: Dict[str, np.ndarray] = {}
    mp = modal_result.get("modal_props", {})
    periods = list(modal_result.get("periods", []))
    n = len(periods)
    if n == 0:
        return arrays

    arrays["modal/period"] = np.array(periods, dtype=float)
    arrays["modal/frequency"] = np.array(
        [1.0 / p if p > 0 else 0.0 for p in periods], dtype=float)
    arrays["modal/omega"] = np.array(
        [2.0 * np.pi / p if p > 0 else 0.0 for p in periods], dtype=float)

    for key, npz_key in [
        ("partiMassRatiosMX", "modal/mx_ratio"),
        ("partiMassRatiosMY", "modal/my_ratio"),
        ("partiMassRatiosMZ", "modal/mz_ratio"),
        ("partiMassMX", "modal/mx_eff"),
        ("partiMassMY", "modal/my_eff"),
        ("partiMassMZ", "modal/mz_eff"),
    ]:
        vals = mp.get(key, [])
        padded = (list(vals) + [0.0] * n)[:n]
        arrays[npz_key] = np.array(padded, dtype=float)

    # Mode shapes (eigenvectors)
    if mode_shapes is not None and n > 0:
        # Build N_node × N_mode arrays
        node_tags = sorted(mode_shapes.get(0, {}).keys())
        if node_tags:
            n_nodes = len(node_tags)
            tag_to_idx = {t: i for i, t in enumerate(node_tags)}
            for dof_idx, npz_key in enumerate(["modal/mode_dx",
                                                "modal/mode_dy",
                                                "modal/mode_dz"]):
                arr = np.zeros((n_nodes, n))
                for midx in range(n):
                    node_vals = mode_shapes.get(midx, {})
                    for tag, disp in node_vals.items():
                        idx = tag_to_idx.get(tag)
                        if idx is not None:
                            arr[idx, midx] = disp[dof_idx]
                arrays[npz_key] = arr
            arrays["node_tag"] = np.array(node_tags, dtype=int)

    return arrays


def _collect_rs(rs_x: Optional[Dict] = None,
                rs_y: Optional[Dict] = None) -> Dict[str, np.ndarray]:
    """Extract response-spectrum arrays."""
    arrays: Dict[str, np.ndarray] = {}
    for direction, prefix in [("X", "rs/"), ("Y", "rs/")]:
        rs = rs_x if direction == "X" else rs_y
        if rs is None:
            continue
        p = prefix.rstrip("/")
        n = len(rs.get("modal_periods", []))
        arrays[f"{p}/period"] = np.array(
            rs.get("modal_periods", []), dtype=float)
        arrays[f"{p}/sa"] = np.array(
            rs.get("spectral_accels", []), dtype=float)
        arrays[f"{p}/eff_mass"] = np.array(
            rs.get("effective_masses", []), dtype=float)
        arrays[f"{p}/v_base"] = np.array(
            rs.get("modal_base_shear", []), dtype=float)
        arrays[f"{p}/v_cqc"] = np.array([rs.get("base_shear_cqc", 0.0)])
        arrays[f"{p}/v_total"] = np.array([rs.get("base_shear_total", 0.0)])
    return arrays


def write_results_npz(
    path: str,
    md: SAPModelData,
    static_results: Optional[Dict[str, Any]] = None,
    modal_result: Optional[Dict[str, Any]] = None,
    mode_shapes: Optional[Dict] = None,
    rs_results: Optional[Dict[str, Any]] = None,
    force_unit: str = "kN",
    length_unit: str = "m",
) -> str:
    """Write a unified NPZ file with model geometry + analysis results.

    Args:
        path: Output ``.npz`` file path.
        md: Parsed ``SAPModelData``.
        static_results: Dict from ``run_static_analysis()`` keyed by case.
        modal_result: Dict from ``run_modal_analysis()``.
        mode_shapes: Dict from ``extract_mode_shapes()``.
        rs_results: Dict with keys ``'rs_x'``, ``'rs_y'`` from ``run_rs()``.
        force_unit: Force unit string for metadata.
        length_unit: Length unit string for metadata.

    Returns:
        Absolute path to the saved file.
    """
    arrays: Dict[str, np.ndarray] = {}

    # Geometry
    arrays.update(_collect_geometry(md))

    # Analysis types present
    analysis_types = []
    if static_results:
        analysis_types.append("static")
        arrays.update(_collect_static(static_results))
    if modal_result:
        analysis_types.append("modal")
        arrays.update(_collect_modal(modal_result, mode_shapes))
    if rs_results:
        analysis_types.append("rs")
        if rs_results.get("rs_x") or rs_results.get("rs_y"):
            arrays.update(_collect_rs(
                rs_results.get("rs_x"), rs_results.get("rs_y")))

    arrays["analysis_types"] = np.array(analysis_types, dtype=str)
    arrays["force_unit"] = np.array([force_unit], dtype=str)
    arrays["length_unit"] = np.array([length_unit], dtype=str)
    arrays["created"] = np.array(
        [datetime.datetime.now().isoformat()], dtype=str)

    path = str(Path(path).resolve())
    np.savez_compressed(path, **arrays)
    return path
