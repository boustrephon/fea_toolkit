"""Shared NPZ/HDF5 serialisation helpers for the model-stage and results writers.

Consolidates the geometry-array extraction and the flat NPZ/HDF5 writers
that were previously duplicated across :mod:`fea_toolkit.io.stage_writer`
and :mod:`fea_toolkit.io.unified_writer`:

* :func:`collect_geometry_arrays` — one implementation that serves both the
  fixed-quad shell schema (``shell_node_1..4``, used by the results pipeline
  and validated by :mod:`fea_toolkit.io.results_schema`) and the ragged
  shell schema (``shell_node_ids_flat`` + ``shell_node_offsets``, used by
  the model-stage files and the source resolver).
* :func:`_write_npz` / :func:`_write_h5` — the format writers.  Both return
  the written path (as :mod:`fea_toolkit.io.unified_writer` historically
  did); callers that ignored the return value are unaffected.
"""

from __future__ import annotations

import typing as t

import numpy as np

#: Allowed stage names, in logical pipeline order.
STAGE_NAMES = ("sap", "mesh")


def collect_geometry_arrays(
    model: t.Any,
    *,
    ragged: bool = False,
) -> dict[str, np.ndarray]:
    """Extract lightweight geometry arrays from a ``SAPModelData`` or
    ``MeshModel``.

    Inactive (split/meshed parent) elements are skipped, matching
    :func:`fea_toolkit.io.npz_writer._collect_geometry`.

    Args:
        model: A ``SAPModelData`` or ``MeshModel`` instance.
        ragged: When True, shell connectivity is written as ragged arrays
            (``shell_node_ids_flat`` + ``shell_node_offsets``) so triangles,
            quads and N-gons survive without padding.  When False (default),
            the fixed-quad schema (``shell_node_1`` .. ``shell_node_4``,
            last corner repeated for triangles) is produced — this is the
            canonical results-schema layout.

    Returns:
        Dict with ``node_*``, ``frame_*`` and ``shell_*`` arrays.
    """
    nodes = getattr(model, "nodes", {})
    arrays: dict[str, np.ndarray] = {}

    # ── Nodes ──────────────────────────────────────────────────────
    n_tags, n_ids, n_x, n_y, n_z, n_special = [], [], [], [], [], []
    for nid, nd in nodes.items():
        n_tags.append(nd.node_tag)
        n_ids.append(str(nid))
        n_x.append(nd.x)
        n_y.append(nd.y)
        n_z.append(nd.z)
        n_special.append(int(bool(getattr(nd, "is_special", False))))
    arrays["node_tag"] = np.array(n_tags, dtype=int)
    arrays["node_sap_id"] = np.array(n_ids, dtype=str)
    arrays["node_x"] = np.array(n_x, dtype=float)
    arrays["node_y"] = np.array(n_y, dtype=float)
    arrays["node_z"] = np.array(n_z, dtype=float)
    arrays["node_is_special"] = np.array(n_special, dtype=int)

    # ── Frames ─────────────────────────────────────────────────────
    # Build parent lookup for t_start/t_end (child i of parent P spans
    # t_locations[i-1] .. t_locations[i]).
    frame_elements = getattr(model, "frame_elements", {})
    parent_lookup: dict[str, tuple] = {}
    for eid, elem in frame_elements.items():
        if elem.t_locations and elem.child_ids:
            parent_lookup[eid] = (elem.t_locations, elem.child_ids)

    f_ids, f_sap, f_parent, f_sec, f_ni, f_nj = [], [], [], [], [], []
    f_t0, f_t1, f_angle, f_card, f_tag = [], [], [], [], []
    frame_assignments = getattr(model, "frame_assignments", {})
    for eid, elem in frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        f_ids.append(len(f_ids))
        f_sap.append(str(eid))
        f_parent.append(str(elem.parent_id) if elem.parent_id else "")
        f_sec.append(frame_assignments.get(eid, ""))
        f_ni.append(ni.node_tag)
        f_nj.append(nj.node_tag)
        if elem.parent_id and elem.parent_id in parent_lookup:
            t_locs, children = parent_lookup[elem.parent_id]
            try:
                idx = children.index(eid)
                f_t0.append(float(t_locs[idx - 1]) if idx > 0 else 0.0)
                f_t1.append(float(t_locs[idx]) if idx < len(t_locs) else 1.0)
            except ValueError:
                f_t0.append(0.0)
                f_t1.append(1.0)
        else:
            f_t0.append(float(elem.t_locations[0]) if elem.t_locations else 0.0)
            f_t1.append(float(elem.t_locations[-1]) if len(elem.t_locations) > 1 else 1.0)
        f_angle.append(float(getattr(elem, "angle", 0.0)))
        f_card.append(int(getattr(elem, "cardinal_point", 10)))
        f_tag.append(int(getattr(elem, "elem_tag", 0)))
    arrays["frame_eid"] = np.array(f_ids, dtype=int)
    arrays["frame_sap_id"] = np.array(f_sap, dtype=str)
    arrays["frame_parent_sap_id"] = np.array(f_parent, dtype=str)
    arrays["frame_sec_name"] = np.array(f_sec, dtype=str)
    arrays["frame_node_i"] = np.array(f_ni, dtype=int)
    arrays["frame_node_j"] = np.array(f_nj, dtype=int)
    arrays["frame_t_start"] = np.array(f_t0, dtype=float)
    arrays["frame_t_end"] = np.array(f_t1, dtype=float)
    arrays["frame_angle"] = np.array(f_angle, dtype=float)
    arrays["frame_cardinal_point"] = np.array(f_card, dtype=int)
    arrays["frame_elem_tag"] = np.array(f_tag, dtype=int)
    # ── Shells ─────────────────────────────────────────────────────
    s_ids, s_sap, s_parent, s_sec, s_tag, s_thick = [], [], [], [], [], []
    area_assignments = getattr(model, "area_assignments", {})
    if ragged:
        flat: list[int] = []
        offsets: list[int] = []
        for aid, area in getattr(model, "area_elements", {}).items():
            if getattr(area, "inactive", False):
                continue
            tags = _node_tags(nodes, list(area.node_ids))
            if len(tags) < 3:
                continue
            s_ids.append(len(s_ids))
            s_sap.append(str(aid))
            s_parent.append(str(area.parent_id) if getattr(area, "parent_id", None) else "")
            s_sec.append(area_assignments.get(aid, ""))
            s_tag.append(int(getattr(area, "area_tag", 0)))
            s_thick.append(float(getattr(area, "thickness", 0.0)))
            offsets.append(len(flat))
            flat.extend(tags)
        offsets.append(len(flat))
        arrays["shell_eid"] = np.array(s_ids, dtype=int)
        arrays["shell_sap_id"] = np.array(s_sap, dtype=str)
        arrays["shell_parent_sap_id"] = np.array(s_parent, dtype=str)
        arrays["shell_sec_name"] = np.array(s_sec, dtype=str)
        arrays["shell_elem_tag"] = np.array(s_tag, dtype=int)
        arrays["shell_thickness"] = np.array(s_thick, dtype=float)
        arrays["shell_node_ids_flat"] = np.array(flat, dtype=int)
        arrays["shell_node_offsets"] = np.array(offsets, dtype=int)
    else:
        s1, s2, s3, s4 = [], [], [], []
        for aid, area in getattr(model, "area_elements", {}).items():
            if getattr(area, "inactive", False):
                continue
            tags = _node_tags(nodes, list(area.node_ids)[:4])
            if len(tags) < 3:
                continue
            while len(tags) < 4:
                tags.append(tags[-1])
            s_ids.append(len(s_ids))
            s_sap.append(str(aid))
            s_parent.append(str(area.parent_id) if getattr(area, "parent_id", None) else "")
            s_sec.append(area_assignments.get(aid, ""))
            s_tag.append(int(getattr(area, "area_tag", 0)))
            s_thick.append(float(getattr(area, "thickness", 0.0)))
            s1.append(tags[0])
            s2.append(tags[1])
            s3.append(tags[2])
            s4.append(tags[3])
        arrays["shell_eid"] = np.array(s_ids, dtype=int)
        arrays["shell_sap_id"] = np.array(s_sap, dtype=str)
        arrays["shell_parent_sap_id"] = np.array(s_parent, dtype=str)
        arrays["shell_sec_name"] = np.array(s_sec, dtype=str)
        arrays["shell_elem_tag"] = np.array(s_tag, dtype=int)
        arrays["shell_thickness"] = np.array(s_thick, dtype=float)
        arrays["shell_node_1"] = np.array(s1, dtype=int)
        arrays["shell_node_2"] = np.array(s2, dtype=int)
        arrays["shell_node_3"] = np.array(s3, dtype=int)
        arrays["shell_node_4"] = np.array(s4, dtype=int)

    return arrays


def _node_tags(nodes: t.Any, node_ids: list[str]) -> list[int]:
    """Map SAP node IDs to OpenSees tags, stopping at the first missing node."""
    tags: list[int] = []
    for nid in node_ids:
        nd = nodes.get(nid)
        if nd is None:
            break
        tags.append(nd.node_tag)
    return tags


# ═══════════════════════════════════════════════════════════════════
# Format writers
# ═══════════════════════════════════════════════════════════════════


def _write_npz(path: str, arrays: dict[str, np.ndarray]) -> str:
    """Write arrays to a compressed NPZ file, returning the written path."""
    path = str(path)
    np.savez_compressed(path, **arrays)
    return path


def _write_h5(path: str, arrays: dict[str, np.ndarray]) -> str:
    """Write arrays to an HDF5 file using h5py, returning the written path.

    Organises data into groups matching the NPZ key hierarchy (``/`` in a
    key becomes a group).  String arrays are stored as variable-length UTF-8.
    """
    try:
        import h5py
    except ImportError:
        raise ImportError("HDF5 output requires h5py. Install with: pip install h5py") from None

    path = str(path)
    with h5py.File(path, "w") as f:
        for key, arr in arrays.items():
            parts = key.split("/")
            dataset_name = parts[-1]
            group_path = "/".join(parts[:-1]) if len(parts) > 1 else ""

            # Handle string arrays — h5py can't write NumPy fixed-width string dtype
            if arr.dtype.kind in {"U", "S"}:
                dt = h5py.string_dtype()
                arr_obj = arr.astype(object)
                if arr.ndim == 0:
                    ds = f.create_dataset(key, shape=(), dtype=dt)
                    ds[()] = str(arr.item())
                elif group_path:
                    g = f.require_group(group_path)
                    g.create_dataset(dataset_name, data=arr_obj, dtype=dt)
                else:
                    f.create_dataset(dataset_name, data=arr_obj, dtype=dt)
            elif group_path:
                g = f.require_group(group_path)
                g.create_dataset(dataset_name, data=arr)
            else:
                f.create_dataset(key, data=arr)
    return path


__all__ = [
    "STAGE_NAMES",
    "_write_h5",
    "_write_npz",
    "collect_geometry_arrays",
]
