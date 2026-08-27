"""Frame-element geometry helpers.

Element splitting, load redistribution to nodes/edges, rigid end offsets,
and their private helpers.  Re-exported by :mod:`fea_toolkit.model.geometry`."""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from .geometry_core import (
    SpatialGrid,
    _segment_intersection_3d,
    compute_t_location,
    get_SAP_vecxz,
    list_interp,
    point_on_segment,
    polygon_area_3d,
)
from .sap_data import (
    AreaElement,
    AreaUniformLoad,
    FrameDistributedLoad,
    FrameElement,
    FrameEndOffset,
    Node,
)


def split_elements_at_joints(
    nodes: dict[str, dict[str, float]],
    elements: dict[str, dict[str, Any]],
    assignments: dict[str, Any],
    dist_loads: dict[str, Any],
    auto_mesh: dict[str, dict[str, Any]],
    frame_dist_loads: dict[str, Any],
    tol: float = 1e-6,
    verbose: bool = False,
) -> tuple[dict[str, dict], dict[str, Any], dict[str, Any]]:
    """Split frame elements at nodes that lie on them, using spatial grid.

    Only splits if ``auto_mesh[eid].get('AtJoints')`` is True.
    Uses a :class:`SpatialGrid` with cell size auto-sized to about 1 %
    of model extent for fast bounding-box pre-filtering.

    Parameters
    ----------
    nodes : dict
        ``{node_id: {x, y, z}}``.
    elements : dict
        ``{elem_id: {i, j, id, ...}}``.
    assignments : dict
        ``{elem_id: section_name}`` — propagated to children.
    dist_loads : dict
        Distributed loads — split proportionally across children.
    auto_mesh : dict
        ``{elem_id: {AtJoints: bool}}``.
    frame_dist_loads : dict
        Legacy parameter (unused, kept for signature compat).
    tol : float
        Geometric tolerance (default 1e-6).
    verbose : bool
        Print progress if True.

    Returns
    -------
    tuple
        ``(new_elements, new_assignments, new_dist_loads)`` with
        parent-child tracking and redistributed loads.
    """
    if not elements:
        return elements, assignments, dist_loads

    # Build spatial grid of all nodes
    node_coords = {nid: (nd["x"], nd["y"], nd["z"]) for nid, nd in nodes.items()}
    # Estimate grid cell size as 1% of model extent
    all_coords = np.array(list(node_coords.values()))
    extent = np.max(all_coords, axis=0) - np.min(all_coords, axis=0)
    cell_size = max(1.0, np.mean(extent) / 100.0)
    grid = SpatialGrid(cell_size)
    for nid, coord in node_coords.items():
        grid.add_point(nid, coord)

    new_elements = {}
    new_assignments = {}
    new_dist_loads = {}
    # Determine next element ID (assuming numeric IDs)
    existing_ids = [
        int(e.get("id", 0)) for e in elements.values() if str(e.get("id", "0")).isdigit()
    ]
    next_id = max(existing_ids) + 1 if existing_ids else 1

    for eid, el in elements.items():
        mesh_flag = auto_mesh.get(eid, {}).get("AtJoints", False)
        if not mesh_flag:
            # Keep as is
            new_elements[eid] = el
            if eid in assignments:
                new_assignments[eid] = assignments[eid]
            if eid in dist_loads:
                new_dist_loads[eid] = dist_loads[eid]
            continue

        a = np.array(node_coords[el["i"]])
        b = np.array(node_coords[el["j"]])
        # Bounding box enlarged by tol
        mins = np.minimum(a, b) - tol
        maxs = np.maximum(a, b) + tol
        candidates = grid.points_in_bbox(tuple(mins), tuple(maxs))

        intermediate = []
        for nid, coord in candidates:
            if nid == el["i"] or nid == el["j"]:
                continue
            if point_on_segment(coord, a, b, tol):
                intermediate.append((nid, coord))

        if not intermediate:
            new_elements[eid] = el
            if eid in assignments:
                new_assignments[eid] = assignments[eid]
            if eid in dist_loads:
                new_dist_loads[eid] = dist_loads[eid]
            continue

        # Sort by distance from a
        def dist_from_a(item, a=a):
            coord = item[1]
            return math.hypot(coord[0] - a[0], coord[1] - a[1], coord[2] - a[2])

        intermediate.sort(key=dist_from_a)
        ordered_nodes = [el["i"]] + [nid for nid, _ in intermediate] + [el["j"]]

        for k in range(len(ordered_nodes) - 1):
            new_eid = f"{eid}-{k}"
            new_el_id = next_id
            next_id += 1
            new_el = el.copy()
            new_el["id"] = new_el_id
            new_el["i"] = ordered_nodes[k]
            new_el["j"] = ordered_nodes[k + 1]
            new_elements[new_eid] = new_el
            # Propagate assignments and loads
            if eid in assignments:
                new_assignments[new_eid] = assignments[eid]
            if eid in dist_loads:
                new_dist_loads[new_eid] = dist_loads[eid]

    if verbose:
        print(f"split_elements_at_joints: {len(elements)} → {len(new_elements)} elements")
    return new_elements, new_assignments, new_dist_loads


def trapezoidal_force_split(
    f_data: tuple[tuple[float, float], tuple[float, float]], t_values: list[float]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Splits TRAPF data based on t-parameters -
    returns (n + 1) values for a set of n t-parameters

    Args:
        f_data (_type_): ((RDSTART, FSTART), (RDEND, FEND))
        t_values (_type_): t-parameters

    Returns:
        _type_: Collections of tuples ((RDSTART, FSTART), (RDEND, FEND))
        based on the t-parameters

    Examples:
        >> f_data = ((0.2, 1.2), (0.8, 5.1))
        >> t_values = [0.1, 0.5, 0.75, 0.95]
        >> trapf_split(f_data, t_values)
        [((0, 0), (1, 0)), ((0.25, 1.2), (1, 3.1499999999999995)), ((0, 3.1499999999999995), (1, 4.7749999999999995)), ((0, 4.7749999999999995), (0.2500000000000003, 5.1)), ((0, 0), (1, 0))]
    """
    tt, ff = zip(*f_data)
    # print(tt,ff)
    t_values = sorted({0, *t_values, 1.0})
    f_values = [list_interp(t, tt, ff) for t in t_values]
    # print(f_values, '**')
    data_list = []
    for t1, t2, f1, f2 in zip(t_values[:-1], t_values[1:], f_values[:-1], f_values[1:]):
        # print(t1, t2, f1, f2)
        if f1 == 0 and f2 != 0:  # left transition
            dt = (tt[0] - t1) / (t2 - t1)
            data_list.append(((dt, ff[0]), (1, f2)))
        elif f1 != 0 and f2 == 0:  # right transition
            dt = (tt[1] - t1) / (t2 - t1)
            data_list.append(((0, f1), (dt, ff[1])))
        elif f1 != 0 and f2 != 0:
            data_list.append(((0, f1), (1, f2)))
        elif f1 == 0 and f2 == 0 and t1 < tt[0] and t2 > tt[1]:
            dt1 = (tt[0] - t1) / (t2 - t1)
            dt2 = (tt[1] - t1) / (t2 - t1)
            data_list.append(((dt1, ff[0]), (dt2, ff[1])))
        elif f1 == 0 and f2 == 0:
            data_list.append(((0, 0), (1, 0)))
    return data_list


def split_elements(
    nodes: dict[str, Node],
    elements: dict[str, FrameElement],
    assignments: dict[str, str],
    dist_loads: list[FrameDistributedLoad],
    auto_mesh: dict[str, dict[str, Any]],
    tol: float = 1e-6,
    verbose: bool = False,
) -> tuple[dict[str, FrameElement], dict[str, str], list[FrameDistributedLoad]]:
    """Split elements at joints (if AtJoints=True) and/or frame-frame
    intersections (if AtFrames=True), then redistribute distributed loads.

    Uses a :class:`SpatialGrid` (default cell_size=1.0) for fast
    broad-phase filtering of candidate nodes during the AtJoints pass.

    The two flags are **independent** — see the truth table:

    ========== ========== ==============================================
    AtJoints   AtFrames   Splits at
    ========== ========== ==============================================
    ``True``   ``False``  Existing joint nodes on the element
    ``False``  ``True``   Frame-frame intersections (new or reused
                          ``split_n_*`` / joint nodes)
    ``True``   ``True``   Both — joints AND frame intersections
    ``False``  ``False``  No splitting
    ========== ========== ==============================================

    When *AtJoints* is ``False`` and *AtFrames* is ``True``, only nodes
    whose ``node_id`` is in the tracked AtFrames set (either newly created
    ``split_n_*`` nodes **or** existing joint nodes whose coordinates
    coincide with an AtFrames intersection) are accepted as split points.

    New ``Node`` objects created at frame-frame intersections use unique
    ``node_id`` (``split_n_N``) and ``node_tag`` values.  These are added
    to the *nodes* dict in-place.  Callers must ensure corresponding
    ``ops.node()`` calls are made in the OpenSees model.

    Storey-level splitting (splitting at identified storey elevations)
    is logically separate and would be controlled by a distinct config
    option such as ``split_at_storeys``.

    Args:
        nodes: ``{node_id: Node}`` — may be extended with new
            ``split_n_*`` nodes created at frame-frame intersections.
        elements: ``{elem_id: FrameElement}`` — inactive parents and
            new children returned.
        assignments: ``{elem_id: section_name}``.
        dist_loads: Distributed loads to redistribute across children.
        auto_mesh: ``{elem_id: {AtJoints: bool, AtFrames: bool}}``.
        tol: Geometric tolerance for intersection and proximity checks.
        verbose: Print progress.

    Returns:
        ``(new_elements, new_assignments, new_dist_loads)`` with parent-
        child tracking and redistributed loads.
    """
    # Build node coords dict

    node_coords = {nid: (node.x, node.y, node.z) for nid, node in nodes.items()}
    # Create spatial grid (default cell_size=1.0) for fast broad-phase
    # pre-filtering of candidate nodes.  Auto-sizing based on model
    # extent could be added here for very large models (see
    # split_elements_at_joints for the pattern).
    grid = SpatialGrid()
    for nid, coord in node_coords.items():
        grid.add_point(nid, coord)

    # ---- AtFrames: find frame-frame intersections and create new nodes ----
    # Collect elements that want frame-frame splitting
    at_frames_ids = [
        eid for eid, el in elements.items() if auto_mesh.get(eid, {}).get("AtFrames", False)
    ]
    # Track all node IDs involved in AtFrames (created or reused)
    at_frames_nodes: set = set()

    if at_frames_ids:
        # Determine next node ID and tag for new split nodes
        existing_ids = set(nodes.keys())
        next_node_tag = max((nd.node_tag for nd in nodes.values()), default=0) + 1
        next_node_num = 1
        while f"split_n_{next_node_num}" in existing_ids:
            next_node_num += 1

        # Build array of endpoints for all AtFrames elements
        at_frames_elems = [(eid, el) for eid, el in elements.items() if eid in at_frames_ids]

        # Precompute 3D bounding boxes for broad-phase filtering
        _elem_bbox: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for eid, el in at_frames_elems:
            pa = node_coords[el.node_i]
            pb = node_coords[el.node_j]
            _elem_bbox[eid] = (
                np.array([min(pa[0], pb[0]), min(pa[1], pb[1]), min(pa[2], pb[2])]),
                np.array([max(pa[0], pb[0]), max(pa[1], pb[1]), max(pa[2], pb[2])]),
            )

        _bbox_tol = tol  # expand bounding boxes slightly for robustness

        for i in range(len(at_frames_elems)):
            eid_a, el_a = at_frames_elems[i]
            a = np.array(node_coords[el_a.node_i])
            b = np.array(node_coords[el_a.node_j])
            if np.linalg.norm(b - a) < 1e-12:
                continue
            bbox_a_min, bbox_a_max = _elem_bbox[eid_a]
            for j in range(i + 1, len(at_frames_elems)):
                eid_b, el_b = at_frames_elems[j]
                # Broad-phase: skip if bounding boxes do not overlap
                bbox_b_min, bbox_b_max = _elem_bbox[eid_b]
                if np.any(bbox_a_max + _bbox_tol < bbox_b_min - _bbox_tol) or np.any(
                    bbox_b_max + _bbox_tol < bbox_a_min - _bbox_tol
                ):
                    continue
                c = np.array(node_coords[el_b.node_i])
                d = np.array(node_coords[el_b.node_j])
                if np.linalg.norm(d - c) < 1e-12:
                    continue
                # Skip if they share a node (already joined)
                node_ids_a = {el_a.node_i, el_a.node_j}
                node_ids_b = {el_b.node_i, el_b.node_j}
                if node_ids_a & node_ids_b:
                    continue
                p, s, t = _segment_intersection_3d(a, b, c, d, tol=tol)
                if p is None:
                    continue
                # Convert parametric tol to absolute distance for the longer
                # segment, so endpoint and reuse checks have consistent scale
                len_a = float(np.linalg.norm(b - a))
                len_b = float(np.linalg.norm(d - c))
                abs_tol = max(tol, tol * max(len_a, len_b))

                # Parametric tolerance (dimensionless fraction) for s/t
                # endpoint checks — distinct from abs_tol (physical distance)
                # used later for coordinate-based node reuse.
                param_tol = tol
                split_a = param_tol < s < 1 - param_tol
                split_b = param_tol < t < 1 - param_tol
                if not split_a and not split_b:
                    continue

                # Reuse any existing node at this location (joint or split_n_)
                used_nid = None
                mins = (float(p[0]) - abs_tol, float(p[1]) - abs_tol, float(p[2]) - abs_tol)
                maxs = (float(p[0]) + abs_tol, float(p[1]) + abs_tol, float(p[2]) + abs_tol)
                for nid_check, coord in grid.points_in_bbox(mins, maxs):
                    dist = math.hypot(
                        coord[0] - float(p[0]),
                        coord[1] - float(p[1]),
                        coord[2] - float(p[2]),
                    )
                    if dist <= abs_tol:
                        used_nid = nid_check
                        break
                if used_nid is None:
                    # Create a new node at the intersection
                    used_nid = f"split_n_{next_node_num}"
                    next_node_num += 1
                    new_node = Node(
                        node_id=used_nid,
                        node_tag=next_node_tag,
                        x=float(p[0]),
                        y=float(p[1]),
                        z=float(p[2]),
                    )
                    next_node_tag += 1
                    nodes[used_nid] = new_node
                    node_coords[used_nid] = (float(p[0]), float(p[1]), float(p[2]))
                    grid.add_point(used_nid, (float(p[0]), float(p[1]), float(p[2])))
                at_frames_nodes.add(used_nid)

                # If element A needs splitting, record the intermediate node
                # Record t-location with tolerance-based dedup
                # (set() only catches exact duplicates)
                if split_a:
                    merged = [*list(el_a.t_locations), s]
                    merged.sort()
                    deduped = []
                    prev = None
                    for val in merged:
                        if prev is None or abs(val - prev) > tol:
                            deduped.append(val)
                        prev = val
                    el_a.t_locations = deduped
                if split_b:
                    merged = [*list(el_b.t_locations), t]
                    merged.sort()
                    deduped = []
                    prev = None
                    for val in merged:
                        if prev is None or abs(val - prev) > tol:
                            deduped.append(val)
                        prev = val
                    el_b.t_locations = deduped

    new_elements = {}
    new_assignments = {}
    new_dist_loads = []  # will hold new loads for child elements
    next_tag = max((elem.elem_tag for elem in elements.values()), default=0) + 1

    for eid, el in elements.items():
        # Check if auto-mesh and AtJoints is True, or has AtFrames t_locations
        mesh_flag = auto_mesh.get(eid, {}).get("AtJoints", False) or bool(el.t_locations)
        if not mesh_flag:
            # No splitting
            new_elements[eid] = el
            if eid in assignments:
                new_assignments[eid] = assignments[eid]
            # Keep original loads unchanged
            for ld in dist_loads:
                if ld.frame_id == eid:
                    new_dist_loads.append(ld)
            continue

        a = np.array(node_coords[el.node_i])
        b = np.array(node_coords[el.node_j])
        length = float(np.linalg.norm(b - a))

        if length < 1e-12:
            # Zero‑length element – keep as is
            new_elements[eid] = el
            if eid in assignments:
                new_assignments[eid] = assignments[eid]
            continue

        # Find intermediate nodes
        mins = np.minimum(a, b) - tol
        maxs = np.maximum(a, b) + tol
        candidates = grid.points_in_bbox(tuple(mins), tuple(maxs))
        intermediate = []
        at_joints = auto_mesh.get(eid, {}).get("AtJoints", False)
        for nid, coord in candidates:
            if nid in (el.node_i, el.node_j):
                continue
            if point_on_segment(coord, a, b, tol):
                # If AtJoints is False, only split at AtFrames-tracked nodes
                if not at_joints and nid not in at_frames_nodes:
                    continue
                t = compute_t_location(coord, a, b)
                intermediate.append((nid, t))
        if not intermediate:
            # no split
            new_elements[eid] = el
            if eid in assignments:
                new_assignments[eid] = assignments[eid]
            for ld in dist_loads:
                if ld.frame_id == eid:
                    new_dist_loads.append(ld)
            continue

        # Sort intermediate by t, then deduplicate by t within tolerance
        intermediate.sort(key=lambda x: x[1])
        deduped = []
        prev_t = None
        for nid, t in intermediate:
            if prev_t is not None and abs(t - prev_t) <= tol:
                continue  # skip — already have a node at this t
            deduped.append((nid, t))
            prev_t = t
        intermediate = deduped

        t_locs = [t for _, t in intermediate]
        node_list = [el.node_i] + [nid for nid, _ in intermediate] + [el.node_j]

        # Mark original as inactive
        el.inactive = True
        el.t_locations = t_locs
        el.child_ids = []
        new_elements[eid] = el
        # Keep assignment on parent (for possible later use)
        if eid in assignments:
            new_assignments[eid] = assignments[eid]

        # Create child elements
        child_elements = []
        for k in range(len(node_list) - 1):
            child_id = f"{eid}-{k}"
            child_tag = next_tag
            next_tag += 1
            child = FrameElement(
                elem_id=child_id,
                elem_tag=child_tag,
                node_i=node_list[k],
                node_j=node_list[k + 1],
                angle=el.angle,
                parent_id=eid,
                inactive=False,
            )
            new_elements[child_id] = child
            new_assignments[child_id] = assignments.get(eid)
            el.child_ids.append(child_id)
            child_elements.append(child)

        # Now split distributed loads on this element
        for ld in dist_loads:
            if ld.frame_id != eid:
                continue
            if abs(ld.val_a) < 1e-12 and abs(ld.val_b) < 1e-12:
                continue

            # Compute global fractions for load start/end
            t_a = ld.dist_a / length if length > 0 else 0.0
            t_b = ld.dist_b / length if length > 0 else 1.0
            t_a = max(0.0, min(1.0, t_a))
            t_b = max(0.0, min(1.0, t_b))

            f_data = ((t_a, ld.val_a), (t_b, ld.val_b))
            segments = trapezoidal_force_split(f_data, t_locs)

            for seg_idx, child in enumerate(child_elements):
                if seg_idx >= len(segments):
                    break
                seg = segments[seg_idx]
                ((t_start_local, f_start), (t_end_local, f_end)) = seg
                if abs(f_start) < 1e-12 and abs(f_end) < 1e-12:
                    continue

                child_len = float(
                    np.linalg.norm(
                        np.array(node_coords[child.node_j]) - np.array(node_coords[child.node_i])
                    )
                )
                child_dist_a = t_start_local * child_len
                child_dist_b = t_end_local * child_len
                shape = "Uniform" if abs(f_start - f_end) < 1e-6 else "Linear"

                child_load = FrameDistributedLoad(
                    pattern=ld.pattern,
                    frame_id=child.elem_id,
                    direction=ld.direction,
                    load_type=ld.load_type,
                    shape=shape,
                    val_a=f_start,
                    val_b=f_end,
                    rdist_a=t_start_local,
                    rdist_b=t_end_local,
                    dist_a=child_dist_a,
                    dist_b=child_dist_b,
                )
                new_dist_loads.append(child_load)

    if verbose:
        print(f"split_elements: {len(elements)} elements → {len(new_elements)} elements")
        print(f"  {len(dist_loads)} loads → {len(new_dist_loads)} loads")
    return new_elements, new_assignments, new_dist_loads


def child_length(child, node_coords):
    a = np.array(node_coords[child.node_i])
    b = np.array(node_coords[child.node_j])
    return np.linalg.norm(b - a)


def beam_load_to_nodal_loads(
    load: FrameDistributedLoad,
    elem: FrameElement,
    node_coords: dict[str, tuple[float, float, float]],
    length: float,
) -> dict[str, dict[str, float]]:
    """Convert a distributed beam load into statically equivalent nodal loads.

    This is a fallback for geometric transformations that do **not** support
    ``eleLoad`` (notably ``Corotational`` in 3D, per the OpenSees documentation).
    The load is projected onto the element's local axes and the fixed-end forces
    are computed assuming a prismatic beam.

    Args:
        load: The distributed load definition.
        elem: The frame element the load acts on.
        node_coords: ``{node_id: (x, y, z)}`` dict for both end nodes.
        length: Element length (in model length units).

    Returns:
        A dict ``{"i": {fx, fy, fz, mx, my, mz}, "j": {fx, fy, fz, mx, my, mz}}``
        with the equivalent nodal forces at each end, expressed in **global**
        coordinates so they can be applied via :func:`openseespy.opensees.load`.
    """
    a = np.array(node_coords[elem.node_i])
    b = np.array(node_coords[elem.node_j])
    vec_x = (b - a) / length
    # Build local axes
    vec_x_norm = vec_x
    vecxz = get_SAP_vecxz(vec_x_norm, elem.angle)
    vec_z = vecxz / np.linalg.norm(vecxz)
    vec_y = np.cross(vec_z, vec_x_norm)
    vec_y = vec_y / np.linalg.norm(vec_y)

    # Determine global direction of the load (SAP2000 convention)
    if load.direction == "Gravity":
        global_dir = np.array([0.0, 0.0, -1.0])
    elif load.direction == "X":
        global_dir = np.array([1.0, 0.0, 0.0])
    elif load.direction == "Y":
        global_dir = np.array([0.0, 1.0, 0.0])
    elif load.direction == "Z":
        global_dir = np.array([0.0, 0.0, 1.0])
    else:
        global_dir = np.array([0.0, 0.0, -1.0])

    # Project intensities onto local axes
    wx_a = load.val_a * float(np.dot(global_dir, vec_x))
    wy_a = load.val_a * float(np.dot(global_dir, vec_y))
    wz_a = load.val_a * float(np.dot(global_dir, vec_z))
    wx_b = load.val_b * float(np.dot(global_dir, vec_x))
    wy_b = load.val_b * float(np.dot(global_dir, vec_y))
    wz_b = load.val_b * float(np.dot(global_dir, vec_z))

    # Partial-span parameters (clamped to [0, 1])
    aL = max(0.0, min(1.0, load.rdist_a))
    bL = max(0.0, min(1.0, load.rdist_b))
    L = length

    # --- Fixed-end forces for a trapezoidal load on a prismatic beam ---
    # Reference:  Gere & Timoshenko, "Mechanics of Materials"
    #
    # Decompose into: uniform(w_start) + triangular(w_tri)
    #   w_tri = w_end - w_start       — linearly varying part (triangular)
    #
    # For uniform load w_start over [aL, bL]:
    #   V_i += w_start * span * L * (1 - (aL + bL) / 2)
    #   V_j += w_start * span * L * (aL + bL) / 2
    #   M_i += w_start * (span * L)² / 12
    #   M_j -= w_start * (span * L)² / 12
    #
    # For triangular load w_tri (0 at aL, w_tri at bL):
    #   V_i += w_tri * span * L / 2 * (1 - (2*aL + bL) / 3)
    #   V_j += w_tri * span * L / 2 * (2*aL + bL) / 3
    #   M_i += w_tri * (span * L)² / 30
    #   M_j -= w_tri * (span * L)² / 20

    def fixed_end_forces(
        w_start: float, w_end: float, a_frac: float, b_frac: float, L_total: float
    ) -> tuple[float, float, float, float]:
        """Return (V_i, V_j, M_i, M_j) for one load component.

        Decomposes a trapezoid into a uniform part (``w_start``) plus a
        triangular part (0 → ``w_tri``) and computes fixed-end forces
        using standard beam formulae.
        """
        s = b_frac - a_frac
        if s < 1e-12 or (abs(w_start) < 1e-12 and abs(w_end) < 1e-12):
            return (0.0, 0.0, 0.0, 0.0)

        sL = s * L_total  # loaded length
        centre = (a_frac + b_frac) * 0.5  # mid-point of loaded region

        # --- Uniform part (w_start over the full loaded span) ---
        # Using w_start (not the value closer to zero) is required so the
        # decomposition reconstructs the trapezoid exactly:
        #   w(a_frac) = w_start + 0      = w_start
        #   w(b_frac) = w_start + w_tri  = w_end
        # The "closer to zero" choice breaks this for decreasing loads
        # (e.g. 10 -> 4 would decompose to 4 + (-6) and reconstruct
        # 10 -> -2).
        V_i_uni = w_start * sL * (1.0 - centre)
        V_j_uni = w_start * sL * centre
        M_i_uni = w_start * sL * sL / 12.0
        M_j_uni = -w_start * sL * sL / 12.0

        # --- Triangular part (0 at a_frac, w_tri at b_frac) ---
        w_tri = w_end - w_start
        if abs(w_tri) > 1e-12:
            F_tri = 0.5 * w_tri * sL  # total triangular force
            # Centroid of triangle from node i: (a_frac + 2*s/3) * L
            c_tri = a_frac + 2.0 * s / 3.0
            V_i_tri = F_tri * (1.0 - c_tri)
            V_j_tri = F_tri * c_tri
            # Fixed-end moment for triangular load on [0, sL]:
            #   M_i = w_tri * sL^2 / 30
            #   M_j = -w_tri * sL^2 / 20
            M_i_tri = w_tri * sL * sL / 30.0
            M_j_tri = -w_tri * sL * sL / 20.0
        else:
            V_i_tri = V_j_tri = M_i_tri = M_j_tri = 0.0

        return (V_i_uni + V_i_tri, V_j_uni + V_j_tri, M_i_uni + M_i_tri, M_j_uni + M_j_tri)

    # Compute local fixed-end forces for each direction.
    # wy (local y) → shear in y, moment about local z.
    # wz (local z) → shear in z, moment about local y.
    # wx (axial)   → axial force, no moment.
    Viy, Vjy, Miz, Mjz = fixed_end_forces(wy_a, wy_b, aL, bL, L)
    Viz, Vjz, Miy, Mjy = fixed_end_forces(wz_a, wz_b, aL, bL, L)
    Vix, Vjx, _, _ = fixed_end_forces(wx_a, wx_b, aL, bL, L)

    # Transform local forces back to global coordinates
    T = np.column_stack([vec_x, vec_y, vec_z])  # local-to-global transform

    f_i_local = np.array([Vix, Viy, Viz])
    m_i_local = np.array([0.0, Miy, Miz])  # wx (axial) → no moment
    f_j_local = np.array([Vjx, Vjy, Vjz])
    m_j_local = np.array([0.0, Mjy, Mjz])

    f_i_global = T @ f_i_local
    m_i_global = T @ m_i_local
    f_j_global = T @ f_j_local
    m_j_global = T @ m_j_local

    return {
        "i": {
            "fx": f_i_global[0],
            "fy": f_i_global[1],
            "fz": f_i_global[2],
            "mx": m_i_global[0],
            "my": m_i_global[1],
            "mz": m_i_global[2],
        },
        "j": {
            "fx": f_j_global[0],
            "fy": f_j_global[1],
            "fz": f_j_global[2],
            "mx": m_j_global[0],
            "my": m_j_global[1],
            "mz": m_j_global[2],
        },
    }


def convert_area_loads_to_edge_loads(
    nodes: dict[str, Node],
    area_elements: dict[str, AreaElement],
    frame_elements: dict[str, FrameElement],
    area_loads: list[AreaUniformLoad],
) -> list[FrameDistributedLoad]:
    """Convert uniform area loads to equivalent frame edge loads.

    The pressure on each panel is split among the frame elements matching
    the panel's edges using the **nearest-supported-edge** partition -- a
    generalisation of the classic 45 degree yield-line / tributary method:

    * every point of the panel is assigned to the *supported* edge it is
      closest to -- for a fully-supported rectangle this reproduces the
      standard two-way pattern (trapezoids on the long edges, triangles
      on the short edges);
    * a panel with ``distribution == "OneWay"`` spans between its two
      opposite supported edges, each carrying half the panel load
      (matching SAP2000's ``OneWay`` flag);
    * edges without a matching frame contribute nothing, and their share
      is redistributed to the remaining supported edges -- the transferred
      force always sums to exactly ``pressure x panel_area`` (sum of
      tributary areas is the panel area), so load is neither silently
      lost nor doubled.

    If a panel has *no* supported edge, a ``RuntimeWarning`` is raised and
    the load is dropped -- the model should mesh the panel as a shell
    element or add a frame along its edge.

    Args:
        nodes: Node dict from ``SAPModelData.nodes``.
        area_elements: Area element dict from ``SAPModelData.area_elements``.
        frame_elements: Frame element dict from ``SAPModelData.frame_elements``.
        area_loads: List of area uniform loads.

    Returns:
        List of ``FrameDistributedLoad`` objects for the edge frame elements.
    """
    # Build lookup: pair of node IDs -> frame element ID
    edge_map = {}  # (node_i, node_j) sorted -> frame_id
    for eid, elem in frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        key = tuple(sorted((elem.node_i, elem.node_j)))
        edge_map.setdefault(key, eid)

    # Also need node coords
    node_coords = {nid: np.array([n.x, n.y, n.z]) for nid, n in nodes.items()}

    result_loads = []

    for al in area_loads:
        area = area_elements.get(al.area_id)
        if area is None:
            continue
        nids = area.node_ids
        if len(nids) < 3:
            continue
        pts = np.array([node_coords[nid] for nid in nids])
        area_val = polygon_area_3d([pts[i] for i in range(len(nids))])
        if area_val < 1e-12:
            continue

        P = al.value  # pressure

        # Supported edges (those with a matching frame element)
        supported: list = []
        for k in range(len(nids)):
            n_a = nids[k]
            n_b = nids[(k + 1) % len(nids)]
            key = tuple(sorted((n_a, n_b)))
            frame_id = edge_map.get(key)
            if frame_id is None:
                continue
            p_a = pts[k]
            p_b = pts[(k + 1) % len(nids)]
            edge_len = float(np.linalg.norm(p_b - p_a))
            if edge_len < 1e-12:
                continue
            supported.append((k, frame_id, p_a, p_b, edge_len))

        if not supported:
            warnings.warn(
                f"Area load on panel {al.area_id} has no supported edge -- "
                "load dropped; mesh the panel as a shell element or add a "
                "frame along its edge.",
                RuntimeWarning,
            )
            continue

        # Tributary area per supported edge (sum is the panel area)
        distribution = str(getattr(al, "distribution", "TwoWay") or "TwoWay")
        if distribution.lower() == "oneway":
            trib = _one_way_tributary(pts, supported, area_val)
        else:
            trib = _nearest_edge_tributary(pts, supported, area_val)

        direction = "Z" if al.direction == "Gravity" else al.direction

        by_k = {
            k: (frame_id, p_a, p_b, edge_len) for (k, frame_id, p_a, p_b, edge_len) in supported
        }
        for kidx, tarea in trib.items():
            if tarea < 1e-12:
                continue
            frame_id, p_a, p_b, edge_len = by_k[kidx]
            w = P * tarea / edge_len

            # Create the edge load -- uniform over the full span
            result_loads.append(
                FrameDistributedLoad(
                    pattern=al.pattern,
                    frame_id=frame_id,
                    direction=direction,
                    load_type="Force",
                    shape="Uniform",
                    val_a=w,
                    val_b=w,
                    rdist_a=0.0,
                    rdist_b=1.0,
                    dist_a=0.0,
                    dist_b=edge_len,
                    coord_sys=al.coord_sys,
                )
            )

    return result_loads


def _nearest_edge_tributary(
    pts: np.ndarray,
    supported: list,
    area_val: float,
) -> dict:
    """Partition a convex panel among its supported edges by nearest-edge.

    Points in the panel are assigned to the closest *supported* edge
    segment (a Voronoi partition that reproduces the 45 degree yield-line
    pattern for rectangles).  Returns ``{edge_index_in_supported:
    tributary_area}`` summing to exactly ``area_val``.

    Sampling is vectorised: a uniform lattice is placed over the
    centroid-fan triangulation and each lattice point is assigned to the
    nearest supported edge segment, then the counts are normalised so the
    tributary areas sum to exactly the panel area.
    """
    e0 = pts[1] - pts[0]
    nrm = np.cross(e0, pts[2] - pts[0])
    nrm = nrm / np.linalg.norm(nrm)
    x_axis = e0 / np.linalg.norm(e0)
    y_axis = np.cross(nrm, x_axis)

    p2 = np.column_stack([pts @ x_axis, pts @ y_axis])  # (n, 2)
    c = p2.mean(axis=0)

    segs = [(p2[k], p2[(k + 1) % len(p2)]) for (k, *_rest) in supported]

    max_dim = float(max(np.ptp(p2[:, 0]), np.ptp(p2[:, 1])))
    n = int(np.clip(np.ceil(max_dim / 0.05), 24, 200))

    cells = []
    for k in range(len(p2)):
        a = p2[k]
        b = p2[(k + 1) % len(p2)]
        tri_area = 0.5 * abs((a[0] - c[0]) * (b[1] - c[1]) - (a[1] - c[1]) * (b[0] - c[0]))
        if tri_area < 1e-12:
            continue
        ii, jj = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        mask = (ii + jj) < n
        i = ii[mask].astype(float)
        j = jj[mask].astype(float)
        u = (i + 0.5) / n
        v = (j + 0.5) / n
        w = 1.0 - u - v
        pts_t = u[:, None] * a[None, :] + v[:, None] * b[None, :] + w[:, None] * c[None, :]
        cells.append(pts_t)

    if not cells:
        raise ValueError("panel could not be sampled for tributary partition")

    P = np.concatenate(cells, axis=0)  # (m, 2)

    def _seg_dists(P):
        cols = []
        for (ax, ay), (bx, by) in segs:
            a = np.array([ax, ay])
            b = np.array([bx, by])
            ab = b - a
            denom = float(np.dot(ab, ab))
            if denom < 1e-18:
                cols.append(np.linalg.norm(P - a, axis=1))
                continue
            t = np.clip((P - a) @ ab / denom, 0.0, 1.0)
            proj = a[None, :] + t[:, None] * ab[None, :]
            cols.append(np.linalg.norm(P - proj, axis=1))
        return np.column_stack(cols)

    idx = _seg_dists(P).argmin(axis=1)
    counts = np.bincount(idx, minlength=len(segs))
    n_samples = int(counts.sum())
    if n_samples == 0:
        raise ValueError("panel could not be sampled for tributary partition")

    trib = {supported[i][0]: float(cnt) / n_samples * area_val for i, cnt in enumerate(counts)}
    return trib


def _one_way_tributary(
    pts: np.ndarray,
    supported: list,
    area_val: float,
) -> dict:
    """SAP ``OneWay`` distribution: span between two opposite supported edges.

    The panel spans across its short direction onto the two opposite
    supported edges with the *smallest* midpoint separation (for a
    rectangle: the two long edges); each carries half the panel load.
    """
    mids = [(k, (p_a + p_b) * 0.5) for (k, _fid, p_a, p_b, _len) in supported]

    if len(mids) == 1:
        return {mids[0][0]: area_val}

    best_pair = None
    best_d = np.inf
    for i in range(len(mids)):
        for j in range(i + 1, len(mids)):
            ki, kj = mids[i][0], mids[j][0]
            if (ki + 1) % len(pts) == kj or (kj + 1) % len(pts) == ki:
                continue  # adjacent edges
            d = float(np.linalg.norm(mids[i][1] - mids[j][1]))
            if d < best_d:
                best_d = d
                best_pair = (i, j)

    if best_pair is None:
        return _nearest_edge_tributary(pts, supported, area_val)

    i, j = best_pair
    return {mids[i][0]: area_val / 2.0, mids[j][0]: area_val / 2.0}


def subdivide_elements(
    elements: dict[str, FrameElement],
    assignments: dict[str, str],
    nodes: dict[str, Node],
    n_segments: int = 4,
    imperfection_ratio: float = 1.0 / 500.0,
    brace_ids: Optional[set] = None,
    end_offset: float = 0.0,
    next_tag: int = 1,
) -> tuple[dict[str, FrameElement], dict[str, str], dict[str, Node], int, list[tuple]]:
    """Subdivide selected frame elements into *n_segments* sub‑elements
    with a small initial imperfection to trigger buckling under compression.

    This implements **Approach A** for brace buckling modelling — subdivided
    element with ``Corotational`` geometric transformation.  The imperfection
    is applied as a lateral offset at internal nodes, perpendicular to the
    element local axis.

    .. note::
       Approach A is **experimental**.  ``Corotational`` geometry with
       imperfect subdivided elements does **not** converge under gravity
       loads (known OpenSees limitation).  A two-stage rebuild approach
       (``Linear`` for gravity → ``Corotational`` for push) would be
       needed to make this work.  See ``docs/pushover_analysis.md`` for
       the current status.

    Bug fixes applied:

    *   **Missing ``set_brace_selection()``** — subdivision was never
        triggered.  Now called in ``run_pushover_4dir``.
    *   **Double subdivision** — ``run_static_analysis`` rebuilds the
        model; now skips already-inactive elements.
    *   **``split_elements`` conflict** — split children overlapped with
        subdivided elements; ``split_elements=False`` now used.
    *   **``forceBeamColumn`` element-level failure** — switched to
        ``dispBeamColumn`` which has no element-level iteration.

    When *end_offset* > 0 (for steel gusset plates), the brace is trimmed
    at both ends and **rigid link** elements are created between the original
    working points and the offset brace ends.

    Args:
        elements: ``{elem_id: FrameElement}`` of **all** frame elements
            (modified in place).
        assignments: ``{elem_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new nodes are added here.
        n_segments: Number of sub‑elements to create (default 4).
        imperfection_ratio: Lateral offset as a fraction of element length
            (default ``L/500``, per ASCE 41 imperfection recommendations).
        brace_ids: Set of element IDs to subdivide.  If ``None``, no elements
            are subdivided (caller must provide a selection).
        end_offset: Distance from each working point to the gusset plate
            face (model length units).  Default 0.0 (no offset).  Set to
            typical gusset plate dimension for braced steel frames.
        next_tag: Next available numeric tag for new nodes and elements.

    Returns:
        ``(elements, assignments, nodes, next_tag, rigid_links)`` with the
        subdivided elements added and original elements preserved (inactive).
        ``rigid_links`` is a list of ``(link_id, node_i, node_j, link_tag)``
        tuples describing the rigid offset segments.
    """
    if brace_ids is None:
        brace_ids = set()

    rigid_links: list[tuple] = []

    for eid in list(brace_ids):
        elem = elements.get(eid)
        # Skip already-inactive elements — prevents double subdivision when
        # the model is rebuilt (e.g., run_static_analysis with pattern_scales).
        if elem is None or getattr(elem, "inactive", False):
            continue

        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue

        p_i = np.array([ni.x, ni.y, ni.z])
        p_j = np.array([nj.x, nj.y, nj.z])
        vec = p_j - p_i
        length = np.linalg.norm(vec)
        if length < 1e-12:
            continue

        # Unit vector along the element
        u = vec / length

        # Clamp offset so it doesn't consume the whole element
        half = length * 0.45
        d = min(end_offset, half)

        if d > 0:
            # Create offset nodes at each end (working point → gusset face)
            p_start = p_i + u * d
            p_end = p_j - u * d

            offset_i_id = f"{eid}_offset_i"
            offset_i_tag = next_tag
            next_tag += 1
            nodes[offset_i_id] = Node(
                node_id=offset_i_id,
                node_tag=offset_i_tag,
                x=float(p_start[0]),
                y=float(p_start[1]),
                z=float(p_start[2]),
            )

            offset_j_id = f"{eid}_offset_j"
            offset_j_tag = next_tag
            next_tag += 1
            nodes[offset_j_id] = Node(
                node_id=offset_j_id,
                node_tag=offset_j_tag,
                x=float(p_end[0]),
                y=float(p_end[1]),
                z=float(p_end[2]),
            )

            # Rigid link at I‑end
            link_i_id = f"{eid}_rigid_i"
            link_i_tag = next_tag
            next_tag += 1
            rigid_links.append((link_i_id, elem.node_i, offset_i_id, link_i_tag))

            # Rigid link at J‑end
            link_j_id = f"{eid}_rigid_j"
            link_j_tag = next_tag
            next_tag += 1
            rigid_links.append((link_j_id, offset_j_id, elem.node_j, link_j_tag))

            brace_start_id = offset_i_id
            brace_end_id = offset_j_id
            p_start_arr = p_start
            p_end_arr = p_end
        else:
            brace_start_id = elem.node_i
            brace_end_id = elem.node_j
            p_start_arr = p_i
            p_end_arr = p_j

        effective_vec = p_end_arr - p_start_arr
        effective_len = np.linalg.norm(effective_vec)
        if effective_len < 1e-12:
            continue

        u_eff = effective_vec / effective_len
        # Perpendicular direction for imperfection
        ref = np.array([0.0, 0.0, 1.0])
        if abs(np.dot(u_eff, ref)) > 0.99:
            ref = np.array([1.0, 0.0, 0.0])
        perp = np.cross(u_eff, ref)
        perp = perp / np.linalg.norm(perp)
        imperfection = effective_len * imperfection_ratio

        # Mark original element as inactive
        elem.inactive = True

        prev_node_id = brace_start_id
        seg_tags = []

        for seg in range(n_segments):
            # Node at the END boundary of this segment.
            # The sinusoidal imperfection is evaluated at each node position,
            # giving a smooth half-sine shape along the brace length.
            t_end = (seg + 1) / n_segments
            imp_amp = imperfection * math.sin(t_end * math.pi)
            end_pt = p_start_arr + effective_vec * t_end + perp * imp_amp

            if seg < n_segments - 1:
                new_node_id = f"{eid}_sub_{seg}_end"
                new_tag = next_tag
                next_tag += 1
                nodes[new_node_id] = Node(
                    node_id=new_node_id,
                    node_tag=new_tag,
                    x=float(end_pt[0]),
                    y=float(end_pt[1]),
                    z=float(end_pt[2]),
                )
                j_node_id = new_node_id
            else:
                j_node_id = brace_end_id

            sub_elem_id = f"{eid}_sub_{seg}"
            sub_tag = next_tag
            next_tag += 1

            elements[sub_elem_id] = FrameElement(
                elem_id=sub_elem_id,
                elem_tag=sub_tag,
                node_i=prev_node_id,
                node_j=j_node_id,
                angle=elem.angle,
                parent_id=eid,
            )
            seg_tags.append(sub_elem_id)
            if eid in assignments:
                assignments[sub_elem_id] = assignments[eid]
            prev_node_id = j_node_id

        # Track child elements on the original brace
        elem.child_ids = seg_tags

    return elements, assignments, nodes, next_tag, rigid_links


def apply_frame_end_offsets(
    elements: dict[str, FrameElement],
    assignments: dict[str, str],
    nodes: dict[str, Node],
    offsets: dict[str, FrameEndOffset],
    next_tag: int = 1,
) -> tuple[dict[str, FrameElement], dict[str, str], dict[str, Node], int, list[tuple]]:
    """Apply rigid end offsets to frame elements.

    For each frame with a non-zero offset, the elastic portion is shortened
    and stiff beam elements (rigid links) bridge the gap between the original
    node and the offset elastic end.

    Args:
        elements: ``{elem_id: FrameElement}`` (modified in place).
        assignments: ``{elem_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new offset nodes are added here.
        offsets: ``{elem_id: FrameEndOffset}`` from parsed s2k data.
        next_tag: Next available numeric tag for new nodes and elements.

    Returns:
        ``(elements, assignments, nodes, next_tag, rigid_links)``.
        ``rigid_links`` is a list of ``(link_id, node_i, node_j, link_tag)``
        tuples.
    """
    rigid_links: list[tuple] = []

    for eid, off in offsets.items():
        if off.end_i == 0.0 and off.end_j == 0.0:
            continue

        elem = elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            continue

        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue

        p_i = np.array([ni.x, ni.y, ni.z], dtype=float)
        p_j = np.array([nj.x, nj.y, nj.z], dtype=float)
        vec = p_j - p_i
        length = float(np.linalg.norm(vec))
        if length < 1e-12:
            continue

        u = vec / length

        # Clamp offsets so the elastic portion doesn't vanish
        half = length * 0.45
        d_i = min(off.end_i, half)
        d_j = min(off.end_j, half)

        # I‑end offset: only create a new node if there is a non‑zero offset.
        if d_i > 0:
            offset_i_id = f"{eid}_off_i"
            offset_i_tag = next_tag
            next_tag += 1
            p_start = p_i + u * d_i
            nodes[offset_i_id] = Node(
                node_id=offset_i_id,
                node_tag=offset_i_tag,
                x=float(p_start[0]),
                y=float(p_start[1]),
                z=float(p_start[2]),
            )
            rigid_i_id = f"{eid}_rigid_i"
            rigid_i_tag = next_tag
            next_tag += 1
            rigid_links.append((rigid_i_id, elem.node_i, offset_i_id, rigid_i_tag))
        else:
            offset_i_id = elem.node_i  # keep original node

        # J‑end offset: only create a new node if there is a non‑zero offset.
        if d_j > 0:
            offset_j_id = f"{eid}_off_j"
            offset_j_tag = next_tag
            next_tag += 1
            p_end = p_j - u * d_j
            nodes[offset_j_id] = Node(
                node_id=offset_j_id,
                node_tag=offset_j_tag,
                x=float(p_end[0]),
                y=float(p_end[1]),
                z=float(p_end[2]),
            )
            rigid_j_id = f"{eid}_rigid_j"
            rigid_j_tag = next_tag
            next_tag += 1
            rigid_links.append((rigid_j_id, offset_j_id, elem.node_j, rigid_j_tag))
        else:
            offset_j_id = elem.node_j  # keep original node

        # Shorten the original element to the offset length
        elem.node_i = offset_i_id
        elem.node_j = offset_j_id

    return elements, assignments, nodes, next_tag, rigid_links


def derive_rigid_end_offsets(
    elements: dict[str, FrameElement],
    assignments: dict[str, str],
    nodes: dict[str, Node],
    sections: dict,
    factor: float = 0.5,
    absolute: Optional[float] = None,
    joint_extents: Optional[dict[str, float]] = None,
    verbose: bool = False,
) -> dict[str, FrameEndOffset]:
    """Auto-derive rigid end offsets from intersecting-member dimensions.

    Models the rigid beam-column joint zone (Level 1 of the joint-fidelity
    taxonomy): at each end of a frame element the elastic portion is
    shortened by ``factor x D``, where ``D`` is the largest section depth
    of the *non-collinear* members meeting at that node - i.e. the
    intersecting member's dimension in the plane of flexure.  A beam end
    is offset by (``factor x``) the column depth and vice versa; collinear
    continuations (e.g. the adjacent beam) are ignored.  The default
    ``factor = 0.5`` moves flexure out to the joint face (half the
    intersecting member's depth).

    Args:
        elements: ``{elem_id: FrameElement}`` - post-split children are
            fine; each end is considered independently, so only the
            outermost children (which meet a non-collinear member) receive
            an offset.
        assignments: ``{elem_id: section_name}``.
        nodes: ``{node_id: Node}``.
        sections: ``{section_name: Section}`` - the ``depth`` (falling
            back to ``bf`` then ``diameter``) gives the intersecting
            member's flexural-plane dimension.
        factor: Fraction of the intersecting member's **full** depth used
            as the offset (default 0.5 -> flexure starts at the joint face).
        absolute: Optional fixed offset length; overrides the derived
            ``factor x D`` when not ``None``.
        joint_extents: Optional ``{node_id: dimension}`` of an explicit
            joint-element panel.  Subtracted from the derived offset (never
            below zero) so a rigid link does not double-count an explicit
            joint element.
        verbose: Print each derived offset.

    Returns:
        ``{elem_id: FrameEndOffset}`` for members with at least one
        non-zero derived longitudinal offset.
    """
    # node -> connected element ids (inactive elements skipped).
    by_node: dict[str, list[str]] = defaultdict(list)
    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        by_node[elem.node_i].append(eid)
        by_node[elem.node_j].append(eid)

    result: dict[str, FrameEndOffset] = {}
    for eid, elem in elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        axis = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z], dtype=float)
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            continue
        u_e = axis / norm

        derived = {"end_i": 0.0, "end_j": 0.0}
        for end_key, node_id in (("end_i", elem.node_i), ("end_j", elem.node_j)):
            d_max = 0.0
            for cid in by_node.get(node_id, ()):
                if cid == eid:
                    continue
                other = elements.get(cid)
                if other is None or getattr(other, "inactive", False):
                    continue
                ci = nodes.get(other.node_i)
                cj = nodes.get(other.node_j)
                if ci is None or cj is None:
                    continue
                axis_c = np.array([cj.x - ci.x, cj.y - ci.y, cj.z - ci.z], dtype=float)
                norm_c = float(np.linalg.norm(axis_c))
                if norm_c < 1e-12:
                    continue
                u_c = axis_c / norm_c
                # Collinear continuation - not an intersecting member.
                if abs(float(np.dot(u_e, u_c))) > 0.9999:
                    continue
                sec = sections.get(assignments.get(cid))
                d = (
                    float(getattr(sec, "depth", 0.0) or 0.0)
                    or float(getattr(sec, "bf", 0.0) or 0.0)
                    or float(getattr(sec, "diameter", 0.0) or 0.0)
                )
                d_max = max(d_max, d)
            if d_max <= 0.0:
                continue
            length = float(absolute) if absolute is not None else factor * d_max
            if joint_extents and node_id in joint_extents:
                length = max(0.0, length - float(joint_extents[node_id]))
            if length > 0.0:
                derived[end_key] = length

        if derived["end_i"] > 0.0 or derived["end_j"] > 0.0:
            result[eid] = FrameEndOffset(end_i=derived["end_i"], end_j=derived["end_j"])
            if verbose:
                print(
                    f"  rigid end zones: {eid} offset_i={derived['end_i']:.4g} "
                    f"offset_j={derived['end_j']:.4g}"
                )

    return result
