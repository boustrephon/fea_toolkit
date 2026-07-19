"""Unified results writer — export MeshModel + analysis results to NPZ or HDF5.

Builds on the existing NPZ schema (``results_schema.md``) and adds a
format-agnostic writer that works with both ``SAPModelData`` and
``MeshModel``.  HDF5 output uses h5py (optional dependency).

Usage::

    from fea_toolkit.io.unified_writer import write_results

    # NPZ (default)
    write_results("results.npz", mesh_model=mesh,
                   static_results=static, modal_result=modal)

    # HDF5 (requires h5py)
    write_results("results.h5", mesh_model=mesh,
                   static_results=static, modal_result=modal,
                   fmt="h5")
"""

from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
import datetime
import json
import numpy as np

from ..model.mesh_model import MeshModel
from ..model.sap_data import SAPModelData
from .results_schema import make_static_key


# ═══════════════════════════════════════════════════════════════════
# Geometry collection (works with MeshModel or SAPModelData)
# ═══════════════════════════════════════════════════════════════════


def _get_nodes(model) -> Tuple[List, List, List, List, List]:
    """Extract node arrays from a MeshModel or SAPModelData."""
    tags, sap_ids, xs, ys, zs = [], [], [], [], []
    for nid, nd in model.nodes.items():
        tags.append(nd.node_tag)
        sap_ids.append(str(nid))
        xs.append(nd.x)
        ys.append(nd.y)
        zs.append(nd.z)
    return tags, sap_ids, xs, ys, zs


def _get_frames(model) -> Tuple:
    """Extract frame element arrays."""
    from collections import defaultdict

    frame_eid, frame_sap_id, frame_parent_sap_id, frame_sec_name = [], [], [], []
    frame_ni, frame_nj = [], []
    frame_t_start, frame_t_end = [], []

    # Build parent lookup for t_start/t_end
    parent_lookup: Dict[str, tuple] = {}
    for eid, elem in model.frame_elements.items():
        if elem.t_locations and elem.child_ids:
            parent_lookup[eid] = (elem.t_locations, elem.child_ids)

    for eid, elem in model.frame_elements.items():
        if getattr(elem, 'inactive', False):
            continue
        ni = model.nodes.get(elem.node_i)
        nj = model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        frame_eid.append(len(frame_eid))
        frame_sap_id.append(str(eid))
        frame_parent_sap_id.append(str(elem.parent_id) if elem.parent_id else "")
        sec = model.frame_assignments.get(eid, "")
        frame_sec_name.append(sec)
        frame_ni.append(ni.node_tag)
        frame_nj.append(nj.node_tag)

        if elem.parent_id and elem.parent_id in parent_lookup:
            t_locs, children = parent_lookup[elem.parent_id]
            try:
                idx = children.index(eid)
                ts = t_locs[idx - 1] if idx > 0 else 0.0
                te = t_locs[idx] if idx < len(t_locs) else 1.0
                frame_t_start.append(ts)
                frame_t_end.append(te)
            except ValueError:
                frame_t_start.append(0.0)
                frame_t_end.append(1.0)
        else:
            frame_t_start.append(0.0)
            frame_t_end.append(1.0)

    return (frame_eid, frame_sap_id, frame_parent_sap_id, frame_sec_name,
            frame_ni, frame_nj, frame_t_start, frame_t_end)


def _get_shells(model) -> Tuple:
    """Extract shell element arrays."""
    shell_eid, shell_sap_id, shell_sec_name = [], [], []
    s1, s2, s3, s4 = [], [], [], []

    for aid, area in model.area_elements.items():
        if getattr(area, 'inactive', False):
            continue
        if len(area.node_ids) < 3:
            continue
        nids = area.node_ids[:4]
        tags = []
        for nid in nids:
            nd = model.nodes.get(nid)
            if nd is None:
                break
            tags.append(nd.node_tag)
        if len(tags) < 3:
            continue
        while len(tags) < 4:
            tags.append(tags[-1])
        shell_eid.append(len(shell_eid))
        shell_sap_id.append(str(aid))
        sec = model.area_assignments.get(aid, "")
        shell_sec_name.append(sec)
        s1.append(tags[0])
        s2.append(tags[1])
        s3.append(tags[2])
        s4.append(tags[3])

    return shell_eid, shell_sap_id, shell_sec_name, s1, s2, s3, s4


def collect_geometry_arrays(model) -> Dict[str, np.ndarray]:
    """Extract all geometry arrays from a MeshModel or SAPModelData.

    The returned dict can be written to NPZ or HDF5.
    """
    arrays: Dict[str, np.ndarray] = {}

    # Nodes
    tags, sids, xs, ys, zs = _get_nodes(model)
    arrays["node_tag"] = np.array(tags, dtype=int)
    arrays["node_sap_id"] = np.array(sids, dtype=str)
    arrays["node_x"] = np.array(xs, dtype=float)
    arrays["node_y"] = np.array(ys, dtype=float)
    arrays["node_z"] = np.array(zs, dtype=float)

    # Frames
    (feid, fsid, fpsid, fsec,
     fni, fnj, fts, fte) = _get_frames(model)
    arrays["frame_eid"] = np.array(feid, dtype=int)
    arrays["frame_sap_id"] = np.array(fsid, dtype=str)
    arrays["frame_parent_sap_id"] = np.array(fpsid, dtype=str)
    arrays["frame_sec_name"] = np.array(fsec, dtype=str)
    arrays["frame_node_i"] = np.array(fni, dtype=int)
    arrays["frame_node_j"] = np.array(fnj, dtype=int)
    arrays["frame_t_start"] = np.array(fts, dtype=float)
    arrays["frame_t_end"] = np.array(fte, dtype=float)

    # Shells
    (seid, ssid, ssec,
     sn1, sn2, sn3, sn4) = _get_shells(model)
    arrays["shell_eid"] = np.array(seid, dtype=int)
    arrays["shell_sap_id"] = np.array(ssid, dtype=str)
    arrays["shell_sec_name"] = np.array(ssec, dtype=str)
    arrays["shell_node_1"] = np.array(sn1, dtype=int)
    arrays["shell_node_2"] = np.array(sn2, dtype=int)
    arrays["shell_node_3"] = np.array(sn3, dtype=int)
    arrays["shell_node_4"] = np.array(sn4, dtype=int)

    return arrays


# ═══════════════════════════════════════════════════════════════════
# Results collection
# ═══════════════════════════════════════════════════════════════════


def collect_static_arrays(static_results: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Extract static analysis arrays.

    Accepts both formats:

    * **Case‑nested** (legacy builder): ``{"DEAD": {"nodal_displacements": ...,
      "element_forces": ...}}``
    * **Flat** (AnalysisBuilder): ``{"nodal_displacements": ...,
      "reactions": ...}`` (stored under case ``"1"``)
    """
    arrays: Dict[str, np.ndarray] = {}

    # Detect format
    has_nested_cases = any(
        isinstance(v, dict) and ("nodal_displacements" in v or "element_forces" in v)
        for v in static_results.values()
    )

    if has_nested_cases:
        case_labels = list(static_results.keys())
        arrays["static_case_labels"] = np.array(case_labels, dtype=str)
        for case in case_labels:
            data = static_results[case]
            _collect_case_forces(arrays, case, data)
            _collect_case_displacements(arrays, case, data)
    else:
        # Flat format — treat as single unnamed case
        arrays["static_case_labels"] = np.array(["1"], dtype=str)
        _collect_case_forces(arrays, "1", static_results)
        _collect_case_displacements(arrays, "1", static_results)

    return arrays


def _collect_case_forces(arrays: Dict[str, np.ndarray], case: str,
                         data: Dict[str, Any]) -> None:
    """Collect element force arrays for one static case."""
    force_keys = [
        "fx_i", "fy_i", "fz_i", "mx_i", "my_i", "mz_i",
        "fx_j", "fy_j", "fz_j", "mx_j", "my_j", "mz_j",
    ]
    for key in force_keys:
        vals = data.get(key, data.get("element_forces", {}).get(key, []))
        arrays[make_static_key(case, key)] = np.asarray(vals, dtype=float)


def _collect_case_displacements(arrays: Dict[str, np.ndarray], case: str,
                                data: Dict[str, Any]) -> None:
    """Collect nodal displacement arrays for one static case."""
    disp = data.get("nodal_displacements", {})
    if not disp:
        return
    tags = sorted(disp.keys(), key=int)
    for i, dof in enumerate(["dx", "dy", "dz"]):
        arr = np.array([disp[t][i] for t in tags], dtype=float)
        arrays[make_static_key(case, f"node_{dof}")] = arr


def collect_modal_arrays(modal_result: Dict[str, Any],
                          mode_shapes: Optional[Dict] = None,
                          ) -> Dict[str, np.ndarray]:
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

    if mode_shapes is not None and n > 0:
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

    return arrays


def collect_rs_arrays(rs_x: Optional[Dict] = None,
                       rs_y: Optional[Dict] = None) -> Dict[str, np.ndarray]:
    """Extract response-spectrum arrays."""
    arrays: Dict[str, np.ndarray] = {}
    for direction, d_key in [("X", "x"), ("Y", "y")]:
        rs = rs_x if direction == "X" else rs_y
        if rs is None:
            continue
        arrays[f"rs/v_base_{d_key}"] = np.array(
            rs.get("modal_base_shear", []), dtype=float)
        arrays[f"rs/v_cqc_{d_key}"] = np.array([rs.get("base_shear_cqc", 0.0)])
        arrays[f"rs/v_srss_{d_key}"] = np.array([rs.get("base_shear_srss", 0.0)])
    return arrays


# ═══════════════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════════════


def _build_metadata(model, static_results=None, modal_result=None,
                     config=None) -> str:
    """Build JSON metadata string."""
    meta = {
        "created": datetime.datetime.now().isoformat(),
        "model_name": getattr(model, 'model_name', ''),
        "num_nodes": len(model.nodes),
        "num_frames": len([e for e in model.frame_elements.values()
                           if not getattr(e, 'inactive', False)]),
        "num_areas": len([a for a in model.area_elements.values()
                          if not getattr(a, 'inactive', False)]),
    }
    if static_results:
        # Use same nested/flat detection as collect_static_arrays
        has_nested_cases = any(
            isinstance(v, dict) and ("nodal_displacements" in v or "element_forces" in v)
            for v in static_results.values()
        )
        if has_nested_cases:
            meta["static_cases"] = list(static_results.keys())
            meta["has_local_forces"] = any(
                "fx_i" in r for r in static_results.values())
        else:
            meta["static_cases"] = ["1"]
            meta["has_local_forces"] = (
                "fx_i" in static_results or
                any("fx_i" in v for v in static_results.values()
                    if isinstance(v, dict))
            )
    if modal_result:
        meta["num_modes"] = len(modal_result.get("periods", []))
    if config:
        meta["config"] = {k: v for k, v in config.items()
                          if isinstance(v, (str, int, float, bool))}
    return json.dumps(meta)


# ═══════════════════════════════════════════════════════════════════
# NPZ writer
# ═══════════════════════════════════════════════════════════════════


def _write_npz(path: str, arrays: Dict[str, np.ndarray]) -> str:
    """Write arrays to NPZ file."""
    path = str(path)
    np.savez_compressed(path, **arrays)
    return path


# ═══════════════════════════════════════════════════════════════════
# HDF5 writer (optional — requires h5py)
# ═══════════════════════════════════════════════════════════════════


def _write_h5(path: str, arrays: Dict[str, np.ndarray]) -> str:
    """Write arrays to HDF5 file using h5py.

    Organises data into groups matching the NPZ key hierarchy.
    String arrays are stored as variable-length UTF-8.
    """
    try:
        import h5py
    except ImportError:
        raise ImportError(
            "HDF5 output requires h5py. Install with: pip install h5py")

    path = str(path)
    with h5py.File(path, 'w') as f:
        for key, arr in arrays.items():
            parts = key.split('/')
            dataset_name = parts[-1]
            group_path = '/'.join(parts[:-1]) if len(parts) > 1 else ''

            # Handle string arrays — h5py can't write NumPy fixed-width string dtype
            if arr.dtype.kind == 'U' or arr.dtype.kind == 'S':
                dt = h5py.string_dtype()
                arr_obj = arr.astype(object)
                if arr.ndim == 0:
                    ds = f.create_dataset(key, shape=(), dtype=dt)
                    ds[()] = str(arr.item())
                    continue
                else:
                    if group_path:
                        g = f.require_group(group_path)
                        g.create_dataset(dataset_name, data=arr_obj, dtype=dt)
                    else:
                        f.create_dataset(dataset_name, data=arr_obj, dtype=dt)
            else:
                if group_path:
                    g = f.require_group(group_path)
                    g.create_dataset(dataset_name, data=arr)
                else:
                    f.create_dataset(key, data=arr)
    return path


# ═══════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════


def write_results(
    path: str,
    model=None,
    mesh_model: Optional[MeshModel] = None,
    static_results: Optional[Dict[str, Any]] = None,
    modal_result: Optional[Dict[str, Any]] = None,
    mode_shapes: Optional[Dict] = None,
    rs_results: Optional[Dict[str, Dict]] = None,
    fmt: str = "npz",
    config: Optional[Dict] = None,
) -> str:
    """Write model geometry + analysis results to a unified output file.

    Accepts either a ``MeshModel`` or a ``SAPModelData`` (via the
    *model* parameter).  Supports NPZ (default) and HDF5 formats.

    Args:
        path: Output file path (``.npz`` or ``.h5``).
        model: ``SAPModelData`` or ``MeshModel`` (alternative to *mesh_model*).
        mesh_model: ``MeshModel`` (preferred — from Preprocessor).
        static_results: Dict of static analysis results keyed by case name.
        modal_result: Dict from ``run_modal_analysis()``.
        mode_shapes: Dict of mode shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``.
        rs_results: Dict with keys ``rs_x``, ``rs_y`` from ``run_rs()``.
        fmt: ``"npz"`` (default) or ``"h5"``.
        config: Builder config dict (included in metadata).

    Returns:
        Absolute path to the written file.
    """
    # Resolve model source (MeshModel or SAPModelData — both have .nodes)
    src = mesh_model or model
    if src is None:
        raise ValueError("Either mesh_model or model must be provided")

    # Collect all arrays
    arrays: Dict[str, np.ndarray] = {}

    # Geometry
    if hasattr(src, 'nodes'):
        arrays.update(collect_geometry_arrays(src))

    # Static results
    if static_results:
        arrays.update(collect_static_arrays(static_results))

    # Modal results
    if modal_result:
        arrays.update(collect_modal_arrays(modal_result, mode_shapes=mode_shapes))

    # RS results
    if rs_results:
        arrays.update(collect_rs_arrays(
            rs_x=rs_results.get("rs_x"),
            rs_y=rs_results.get("rs_y"),
        ))

    # Metadata
    arrays["metadata_json"] = np.array([
        _build_metadata(src, static_results, modal_result, config)
    ])

    # Write — validate fmt explicitly
    if fmt == "h5":
        _write_h5(path, arrays)
    elif fmt == "npz":
        _write_npz(path, arrays)
    else:
        raise ValueError(f"Unsupported format '{fmt}'; expected 'npz' or 'h5'")

    return str(Path(path).resolve())
