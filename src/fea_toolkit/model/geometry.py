# fea_toolkit/model/geometry.py

"""Geometric utilities for element orientation, splitting, and intersections."""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Optional, Union

import numpy as np

from ..model.sap_data import (
    AreaElement,
    AreaMesh,
    AreaUniformLoad,
    FrameDistributedLoad,
    FrameElement,
    FrameEndOffset,
    Group,
    JointLoad,
    Node,
    Restraint,
    SAPModelData,
)

# ============================================================================
# Vector and orientation functions (from SAP2OPS_v4.py)
# ============================================================================


def get_SAP_vecxz(vec_x: Union[Sequence[float], np.ndarray], angle: float = 0.0) -> np.ndarray:
    """Generate default vecxz vector for OpenSees geometric transformation.

    Args:
        vec_x: Vector from node I to node J (local x‑axis).
        angle: Rotation (degrees) about the local x‑axis from default.

    Returns:
        Unit vector in the local x‑z plane (vecxz).
    """
    if isinstance(vec_x, Sequence):
        vec_x = np.array(vec_x, dtype=float)
    v1 = vec_x
    length = np.linalg.norm(v1)
    if length < 1e-12:
        raise ValueError("Vector vec_x has zero length.")
    v1_norm = v1 / length

    globalY = np.array([0.0, 1.0, 0.0])
    globalZ = np.array([0.0, 0.0, 1.0])

    # Check if element is vertical (parallel to global Z)
    cos_sim = np.dot(v1_norm, globalZ)
    if abs(cos_sim) > 0.9999:
        return globalY if cos_sim > 0 else -globalY

    # Default vecxz = cross(local_x, global_Z) normalized
    v3 = np.cross(v1_norm, globalZ)
    v3_norm = v3 / np.linalg.norm(v3)

    if angle == 0.0:
        return v3_norm
    else:
        theta = math.radians(angle)
        return rotate_about_axis(v3_norm, v1_norm, theta)


def get_local_axes(
    axis: np.ndarray, angle: float = 0.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the full local coordinate system for a frame element.

    Given the element axis (I→J vector), returns the orthonormal
    ``(vx, vy, vz)`` triplet following SAP2000 conventions.

    * ``vx`` = unit vector along the element axis.
    * ``vz`` = vecxz (SAP2000 local z) normalised, perpendicular to *vx*.
    * ``vy`` = ``vz × vx``, completing the right-handed system.

    When *vx* is vertical (parallel to global Z), the default fallback
    uses global Y as the reference for vecxz.

    Args:
        axis: Element axis vector (I→J).  Does not need to be unit length.
        angle: Rotation (degrees) about the local x‑axis (SAP2000 convention).
            Defaults to 0.0.

    Returns:
        ``(vx, vy, vz)`` — each a **unit** ``np.ndarray`` of length 3 (all
        orthonormal to machine precision).
    """
    import numpy as np

    vx = np.asarray(axis, dtype=float)
    norm = np.linalg.norm(vx)
    if norm < 1e-12:
        raise ValueError("axis has zero length")
    vx = vx / norm

    vecxz = get_SAP_vecxz(vx, angle)
    vz = vecxz / np.linalg.norm(vecxz)
    vy = np.cross(vz, vx)
    if np.linalg.norm(vy) > 1e-12:
        vy = vy / np.linalg.norm(vy)
    else:
        # Fallback: vertical or near-vertical element
        vy = np.array([0.0, 1.0, 0.0])
        vz = np.cross(vx, vy)
        vz = vz / np.linalg.norm(vz)

    # Runtime sanity checks — all returned vectors must be unit length
    # (explicit checks, not assert, so they remain active under python -O)
    eps = 1e-10
    if abs(np.linalg.norm(vx) - 1.0) >= eps:
        raise ValueError(f"vx not unit: {np.linalg.norm(vx)}")
    if abs(np.linalg.norm(vy) - 1.0) >= eps:
        raise ValueError(f"vy not unit: {np.linalg.norm(vy)}")
    if abs(np.linalg.norm(vz) - 1.0) >= eps:
        raise ValueError(f"vz not unit: {np.linalg.norm(vz)}")
    if abs(np.dot(vx, vy)) >= eps:
        raise ValueError(f"vx·vy not zero: {np.dot(vx, vy)}")
    if abs(np.dot(vx, vz)) >= eps:
        raise ValueError(f"vx·vz not zero: {np.dot(vx, vz)}")
    if abs(np.dot(vy, vz)) >= eps:
        raise ValueError(f"vy·vz not zero: {np.dot(vy, vz)}")

    return vx, vy, vz


def rotate_about_axis(v: np.ndarray, axis: np.ndarray, theta_rad: float) -> np.ndarray:
    """Rotate a vector about an axis using Rodrigues' formula.

    Args:
        v: Vector to rotate.
        axis: Rotation axis (will be normalized).
        theta_rad: Rotation angle in radians.

    Returns:
        Rotated unit vector.
    """
    k = axis / np.linalg.norm(axis)
    v_rot = (
        v * math.cos(theta_rad)
        + np.cross(k, v) * math.sin(theta_rad)
        + k * np.dot(k, v) * (1 - math.cos(theta_rad))
    )
    return v_rot / np.linalg.norm(v_rot)


def point_on_segment(
    p: Union[Sequence[float], np.ndarray],
    a: Union[Sequence[float], np.ndarray],
    b: Union[Sequence[float], np.ndarray],
    tol: float = 1e-6,
) -> bool:
    """Check if point p lies on the closed line segment from a to b.

    Args:
        p: Point coordinates.
        a: Start point of segment.
        b: End point of segment.
        tol: Tolerance for collinearity and projection.

    Returns:
        True if p is within the segment (including endpoints).
    """
    p = np.asarray(p)
    a = np.asarray(a)
    b = np.asarray(b)
    ab = b - a
    ap = p - a
    bp = p - b

    # Collinearity check
    cross = np.cross(ab, ap)
    if np.linalg.norm(cross) > tol:
        return False

    # Check if projection lies between a and b
    return not (np.dot(ap, ab) < -tol or np.dot(bp, -ab) < -tol)


def compute_t_location(point, a, b) -> float:
    """Return parametric location t (0..1) of point on line segment a-b."""
    a = np.asarray(a)
    b = np.asarray(b)
    p = np.asarray(point)
    ab = b - a
    length = np.linalg.norm(ab)
    if length < 1e-12:
        return 0.0
    ap = p - a
    t = np.dot(ap, ab) / (length * length)
    return float(np.clip(t, 0.0, 1.0))


def global_to_local_distributed_load(ele_tag, global_force_vector):
    """Transform a global distributed load vector into OpenSees local coordinates
    and applies it to a 3D beam element.

    global_force_vector: list/array [Wx, Wy, Wz] (Force per unit true length)

    Apply to OpenSees 3D beam (Format: wy, wz, wx)
        ops.eleLoad('-ele', ele_tag, '-type', '-beamUniform', wy, wz, wx)
    """
    import openseespy.opensees as ops

    # 1. Fetch node coordinates for the element
    node_tags = ops.eleNodes(ele_tag)
    i_node, j_node = node_tags[0], node_tags[1]

    pos_i = np.array(ops.nodeCoord(i_node))
    pos_j = np.array(ops.nodeCoord(j_node))

    # 2. Get local X-axis (element vector)
    element_vector = pos_j - pos_i
    true_length = np.linalg.norm(element_vector)
    local_x = element_vector / true_length

    # 3. Retrieve the cross-product vector used in the element's geometric transformation
    # OpenSees stores geomTransf tags. Here, we fetch the defined local Y/Z or look it up.
    # Note: If OpenSees 'eleResponse' doesn't support 'yaxis' directly for your element type,
    # extract the vecxz vector used when you defined the geomTransf.
    try:
        local_y = np.array(ops.eleResponse(ele_tag, "yaxis"))
        local_z = np.array(ops.eleResponse(ele_tag, "zaxis"))
    except Exception:
        # Fallback manual calculation if eleResponse isn't available for the element type.
        # Note: OpenSees beam-column elements (elasticBeamColumn, forceBeamColumn, etc.)
        # delegate 'yaxis'/'zaxis' to CrdTransf::setResponse, which supports them.
        # This fallback exists for element types where the delegation may not apply.
        v = np.array([0.0, 0.0, 1.0]) if abs(local_x[1]) < 0.999 else np.array([1.0, 0.0, 0.0])
        local_z = np.cross(local_x, v)
        local_z = local_z / np.linalg.norm(local_z)
        local_y = np.cross(local_z, local_x)

    # 4. Project global load onto local axes via dot products
    W = np.array(global_force_vector)
    wx = np.dot(W, local_x)
    wy = np.dot(W, local_y)
    wz = np.dot(W, local_z)

    return wx, wy, wz


def interp(
    x: float, x1: float, x2: float, y1: Optional[float], y2: Optional[float]
) -> Optional[float]:
    """Returns an interpolated y-value for a line
    between two points (x1, y1) and (x2, y2)
    for a given x-value - i.e. linear interpolation
    along a line.

    Args:
        x (float): point at which interpolation is to occur
        x1 (float): start value of x
        x2 (float): end value of x
        y1 (float): start value of y
        y2 (float): end value of y

    Returns:
        float: interpolated y-value for a line at point x

    Example:
        >>> interp(1.5, 0.7, 1.9, 1.4, 2.3)
        2.0
        >>> round(interp(2.3, 0.7, 1.9, 1.4, 2.3), 6)
        2.6
        >>> interp(-0.1, 0.7, 1.9, 1.4, 2.3)
        0.8
    """
    if y1 is None or y2 is None:
        return None
    elif x1 == x2:
        return 0.5 * (y1 + y2)
    else:
        return y1 + (x - x1) / (x2 - x1) * (y2 - y1)


def list_interp(
    val: float,
    list_1: list[float],
    list_2: list[float],
    extend: bool = False,
    extrapolate: bool = False,
) -> float | None:
    """Returns interpolated values from list_2 based
    on values related to list_1, returning zero if
    `extend` is False and the values are outside the
    range of list_1 or returning the bookends
    if the provided values are outside the range of
    list_1 and if `extrapolate` is False. If any of
    the values are None, it will return `None`.

    Args:
        val (float): the lookup value
        list_1 (list[float]): the lookup list
        list_2 (list[float]): is the result list with values corresponding
                to those on list_1
        extrapolate (bool, optional): the option to extrapolate linearly
            outside the limits of list_1. Defaults to False.

    Returns:
        float: Linearly interpolated function

    Examples:
        >>> list_interp(0.5, [0.2, 0.8, 1.1], [1.1, 1.35, 1.4])
        1.225
        >>> list_interp(0.08, [0.2, 0.8], [1.1, 1.35], True, True)
        1.05
        >>> list_interp(0.08, [0.2, 0.8], [1.1, 1.35], True, False)
        1.1
    """
    i_list = [i for i, n in enumerate(list_1) if n == val]
    if i_list:
        # if lookup value matches a value in list_1
        return list_2[i_list[0]]
    elif val <= list_1[0]:
        # if lookup value is lower than all in list_1
        if not extend:
            # err_msg = f'Value ({val}) is outside range ({list_1[0]} to {list_1[-1]})'
            # raise ValueError(err_msg)
            return 0
        elif extrapolate:
            vals = list_1[:2] + list_2[:2]
            return interp(val, *vals)
        else:
            return list_2[0]
    elif val >= list_1[-1]:
        # if lookup value is higher than all in list_1
        if not extend:
            # err_msg = f'Value ({val}) is outside data range ({list_1[0]} to {list_1[-1]})'
            # raise ValueError(err_msg)
            return 0
        elif extrapolate:
            vals = list_1[-2:] + list_2[-2:]
            return interp(val, *vals)
        else:
            return list_2[-1]
    else:
        # carry out interpolation
        index_list = [
            i for i, (x1, x2) in enumerate(zip(list_1[:-1], list_1[1:])) if val >= x1 and val <= x2
        ]
        if len(index_list) == 1:
            j = index_list[0]
            vals = (list_1[j], list_1[j + 1], list_2[j], list_2[j + 1])
            if any(v is None for v in vals):
                return None
            else:
                return interp(val, *vals)
        else:
            return None


# ============================================================================
# Spatial grid for efficient nearest‑neighbour search
# ============================================================================


def polygon_area_3d(pts):
    """Compute the 3D area of a polygon via cross-product summation.

    Uses the vector-area formula :math:`\\mathbf{A} = \\frac12 \\sum \\mathbf{r}_i \\times \\mathbf{r}_{i+1}`
    which works for any orientation (horizontal, vertical, or inclined).

    Parameters
    ----------
    pts : list of (x, y, z) arrays or tuples
        Polygon vertices in order (at least 3).

    Returns
    -------
    float
        Polygon area (always \u2265 0).  Returns 0.0 if fewer than 3 vertices.
    """
    if len(pts) < 3:
        return 0.0
    area_vec = np.zeros(3)
    for k in range(len(pts)):
        i1, i2 = k, (k + 1) % len(pts)
        area_vec += np.cross(np.asarray(pts[i1]), np.asarray(pts[i2]))
    return 0.5 * float(np.linalg.norm(area_vec))


def _segment_intersection_3d(a, b, c, d, tol=1e-6):
    """Find intersection point of line segments AB and CD in 3D.

    Returns (intersect_point, s, t) or (None, None, None) if no intersection.
    s = parametric position along AB (0=a, 1=b)
    t = parametric position along CD (0=c, 1=d)
    """
    ab = b - a
    cd = d - c
    ac = c - a

    # Evaluate all three coordinate-pair projections and choose the one
    # with the largest absolute determinant for best numerical stability.
    best = None  # (det, i, j)
    for i, j in [(0, 1), (0, 2), (1, 2)]:
        det = ab[i] * (-cd[j]) - ab[j] * (-cd[i])
        if abs(det) > tol and (best is None or abs(det) > abs(best[0])):
            best = (det, i, j)
    if best is None:
        return None, None, None
    det, i, j = best
    s = (ac[i] * (-cd[j]) - ac[j] * (-cd[i])) / det
    t = (ab[i] * ac[j] - ab[j] * ac[i]) / det
    k = 3 - i - j
    residual = (a[k] + s * ab[k]) - (c[k] + t * cd[k])
    if abs(residual) > tol * max(1.0, abs(a[k]), abs(b[k]), abs(c[k]), abs(d[k])):
        return None, None, None
    if -tol <= s <= 1 + tol and -tol <= t <= 1 + tol:
        p = a + s * ab
        return p, s, t
    return None, None, None


class SpatialGrid:
    """Simple 3D uniform grid for spatial indexing of points.

    Partitions 3D space into cubic cells of size *cell_size*.
    Points are mapped to cells via :meth:`add_point`; later,
    :meth:`points_in_bbox` returns only points in cells that
    overlap the query bounding box.  This provides a fast
    broad-phase pre-filter for proximity and point-on-segment
    tests.

    The class has **no external dependencies** (pure Python +
    ``math``) and is used in three places:

    * ``split_elements_at_joints`` — find joints on frame segments
    * ``split_elements`` (AtFrames) — find frame-frame intersections
    * ``_split_frames_at_shell_subdiv`` — find shell mesh nodes
      on frame segments

    Parameters
    ----------
    cell_size : float, optional
        Side length of each cubic cell (default 1.0).  Should be
        roughly the same order of magnitude as typical node spacing
        or query extent.  Auto-sized to about 1 percent of model extent in most
        callers.
    """

    def __init__(self, cell_size: float = 1.0):
        self.cell_size = cell_size
        self.grid: dict[tuple[int, int, int], list[tuple[Any, tuple[float, float, float]]]] = (
            defaultdict(list)
        )

    def _cell(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        """Return the (i, j, k) cell index for a given coordinate."""
        return (
            int(math.floor(x / self.cell_size)),
            int(math.floor(y / self.cell_size)),
            int(math.floor(z / self.cell_size)),
        )

    def add_point(self, point_id: Any, coords: tuple[float, float, float]) -> None:
        """Store *point_id* at *coords* in the grid.

        Parameters
        ----------
        point_id : Any
            Identifier for the point (e.g. node ID string).
        coords : tuple of float
            (x, y, z) coordinate.
        """
        self.grid[self._cell(*coords)].append((point_id, coords))

    def points_in_bbox(
        self, mins: tuple[float, float, float], maxs: tuple[float, float, float]
    ) -> list[tuple[Any, tuple[float, float, float]]]:
        """Return all points whose cell overlaps the axis-aligned bounding box.

        Parameters
        ----------
        mins : tuple of float
            (x_min, y_min, z_min) of the query box.
        maxs : tuple of float
            (x_max, y_max, z_max) of the query box.

        Returns
        -------
        list of (point_id, (x, y, z))
            Points in cells that intersect the bounding box.
            The caller should filter further (e.g. with
            point-on-segment) since the grid only provides
            cell-level precision.
        """
        min_cell = self._cell(mins[0], mins[1], mins[2])
        max_cell = self._cell(maxs[0], maxs[1], maxs[2])
        result = []
        for i in range(min_cell[0], max_cell[0] + 1):
            for j in range(min_cell[1], max_cell[1] + 1):
                for k in range(min_cell[2], max_cell[2] + 1):
                    result.extend(self.grid[(i, j, k)])
        return result


# ============================================================================
# Element splitting at joints (respecting auto‑mesh)
# ============================================================================


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


def split_elements_ss(
    nodes: dict[str, dict[str, float]],
    elements: dict[str, dict[str, Any]],
    assignments: dict[str, Any],
    dist_loads: dict[str, Any],
    auto_mesh: dict[str, dict[str, Any]],
    frame_dist_loads: dict[str, Any],
    tol: float = 1e-6,
    verbose: bool = False,
) -> tuple[dict[str, dict], dict[str, Any], dict[str, Any]]:
    """Main entry point for element splitting (currently only at joints).

    .. deprecated::
       Use :func:`split_elements` instead, which handles both AtJoints
       and AtFrames splitting.  This wrapper will be removed in a future
       version.
    """
    return split_elements_at_joints(
        nodes, elements, assignments, dist_loads, auto_mesh, frame_dist_loads, tol, verbose
    )


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


# ============================================================================
# Nodal load conversion (for geomTransf types that don't support eleLoad)
# ============================================================================


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
    # Decompose into: uniform(w_avg) + antisymmetric(w_var)
    #   w_avg = (w_a + w_b) / 2      — constant part
    #   w_var = (w_b - w_a) / 2      — linearly varying part (triangular)
    #
    # For uniform load w_avg over [aL, bL]:
    #   V_i += w_avg * span * L * (1 - (aL + bL) / 2)
    #   V_j += w_avg * span * L * (aL + bL) / 2
    #   M_i += w_avg * (span * L)² / 12
    #   M_j -= w_avg * (span * L)² / 12
    #
    # For triangular load w_var (0 at aL, w_var at bL):
    #   V_i += w_var * span * L / 2 * (1 - (2*aL + bL) / 3)
    #   V_j += w_var * span * L / 2 * (2*aL + bL) / 3
    #   M_i += w_var * (span * L)² / 30
    #   M_j -= w_var * (span * L)² / 20

    def fixed_end_forces(
        w_start: float, w_end: float, a_frac: float, b_frac: float, L_total: float
    ) -> tuple[float, float, float, float]:
        """Return (V_i, V_j, M_i, M_j) for one load component.

        Decomposes a trapezoid into a uniform part (``w_min``) plus a
        triangular part (0 → ``w_tri``) and computes fixed-end forces
        using standard beam formulae.
        """
        s = b_frac - a_frac
        if s < 1e-12 or (abs(w_start) < 1e-12 and abs(w_end) < 1e-12):
            return (0.0, 0.0, 0.0, 0.0)

        sL = s * L_total  # loaded length
        centre = (a_frac + b_frac) * 0.5  # mid-point of loaded region

        # --- Uniform part (value closer to zero over full loaded span) ---
        w_min = w_start if abs(w_start) < abs(w_end) else w_end
        V_i_uni = w_min * sL * (1.0 - centre)
        V_j_uni = w_min * sL * centre
        M_i_uni = w_min * sL * sL / 12.0
        M_j_uni = -w_min * sL * sL / 12.0

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


# ============================================================================
# Area load → frame edge load conversion
# ============================================================================


def convert_area_loads_to_edge_loads(
    nodes: dict[str, Node],
    area_elements: dict[str, AreaElement],
    frame_elements: dict[str, FrameElement],
    area_loads: list[AreaUniformLoad],
) -> list[FrameDistributedLoad]:
    """Convert uniform area loads to equivalent frame edge loads.

    For each area element with a uniform pressure load, the total force
    is distributed to the frame elements forming its edges using the
    tributary‑width method (force on each edge = pressure × distance
    from edge to centroid × edge length).

    The resulting distributed loads are returned as
    :class:`FrameDistributedLoad` instances that can be appended to
    the existing frame load list.

    Args:
        nodes: Node dict from ``SAPModelData.nodes``.
        area_elements: Area element dict from ``SAPModelData.area_elements``.
        frame_elements: Frame element dict from ``SAPModelData.frame_elements``.
        area_loads: List of area uniform loads.

    Returns:
        List of ``FrameDistributedLoad`` objects for the edge frame elements.
    """
    # Build lookup: pair of node IDs → frame element ID
    edge_map = {}  # (node_i, node_j) sorted → frame_id
    for eid, elem in frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        key = tuple(sorted((elem.node_i, elem.node_j)))
        edge_map[key] = eid

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

        # Compute area centroid
        pts = np.array([node_coords[nid] for nid in nids])
        centroid = pts.mean(axis=0)

        # Compute area via shared helper
        area_val = polygon_area_3d([pts[i] for i in range(len(nids))])

        if area_val < 1e-12:
            continue

        P = al.value  # pressure

        # For each edge of the area, find the matching frame element
        for k in range(len(nids)):
            n_a = nids[k]
            n_b = nids[(k + 1) % len(nids)]
            key = tuple(sorted((n_a, n_b)))
            frame_id = edge_map.get(key)
            if frame_id is None:
                continue

            # Midpoint of this edge
            p_a = node_coords[n_a]
            p_b = node_coords[n_b]
            mid = (p_a + p_b) * 0.5

            # Perpendicular distance from centroid to the edge line
            edge_vec = p_b - p_a
            edge_len = np.linalg.norm(edge_vec)
            if edge_len < 1e-12:
                continue
            edge_dir = edge_vec / edge_len

            # Vector from midpoint to centroid
            to_cent = centroid - mid
            # Perpendicular distance (remove component parallel to edge)
            perp_vec = to_cent - np.dot(to_cent, edge_dir) * edge_dir
            perp_dist = np.linalg.norm(perp_vec)

            # Tributary load intensity: w = P × perp_dist (kN/m)
            w = P * perp_dist
            if abs(w) < 1e-12:
                continue

            # Determine load direction from the area load
            direction = "Z" if al.direction == "Gravity" else al.direction

            # Create the edge load — uniform over the full span
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


# ============================================================================
# Brace subdivision with initial imperfection (Approach A)
# ============================================================================


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


# ============================================================================
# Frame end offsets (rigid zones at joints)
# ============================================================================


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


# ============================================================================
# Area meshing — subdivide area quads into a grid of smaller shell elements
# ============================================================================


def _point_uv_on_quad(
    pt: np.ndarray,
    corners: list[np.ndarray],
) -> Optional[tuple[float, float]]:
    """Estimate parametric (u, v) of *pt* on a bilinear quad.

    Uses Newton iteration on the bilinear surface
    ``p(u,v) = (1-v)[(1-u)c0 + u*c1] + v[(1-u)c3 + u*c2]``.

    Returns:
        ``(u, v)`` clamped to ``[0, 1]``, or ``None`` if the point is
        not on the quad (residual exceeds 1 % of the quad diagonal).
    """
    c0, c1, c2, c3 = corners
    diag = float(np.linalg.norm(c2 - c0))
    tol = max(1e-8, 0.01 * diag)
    u, v = 0.5, 0.5
    for _ in range(20):
        top = c0 * (1.0 - u) + c1 * u
        bot = c3 * (1.0 - u) + c2 * u
        p = top * (1.0 - v) + bot * v
        dp_du = (c1 - c0) * (1.0 - v) + (c2 - c3) * v
        dp_dv = bot - top
        r = p - pt
        J = np.column_stack([dp_du, dp_dv])  # (3, 2)
        JTJ = J.T @ J
        try:
            delta = np.linalg.solve(JTJ, -J.T @ r)
        except np.linalg.LinAlgError:
            break
        u = float(np.clip(u + delta[0], 0.0, 1.0))
        v = float(np.clip(v + delta[1], 0.0, 1.0))
        if float(np.linalg.norm(delta)) < 1e-8:
            break
    # Residual check: reject points that aren't actually on the quad
    top = c0 * (1.0 - u) + c1 * u
    bot = c3 * (1.0 - u) + c2 * u
    p_final = top * (1.0 - v) + bot * v
    if float(np.linalg.norm(p_final - pt)) > tol:
        return None
    return u, v


def _propagate_edge_restraints(
    node_grid: Sequence[Sequence[Optional[str]]],
    n_u: int,
    n_v: int,
    restraints: dict[str, Restraint],
) -> None:
    """Propagate edge-node restraints via bitwise-AND of adjacent corners.

    For each intermediate node on the four edges of an ``n_u×n_v`` mesh
    grid, looks up the two corner nodes at the ends of that edge in
    *restraints*.  If both corners are restrained, the intermediate node
    receives the bitwise AND of the two ``dofs`` lists.  Interior nodes
    receive any DOF bit that is common to **all four** corners (the
    bitwise AND across the four corner ``dofs`` lists).  Corners are
    untouched.  Nodes that already carry an explicit restraint (for
    example, pre-existing nodes reused via coordinate deduplication) are
    never overwritten — only newly created nodes inherit the propagated
    restraints.

    Example: corner A has ``dofs=[1,0,1,0,0,1]`` and corner B has
    ``dofs=[1,1,1,1,0,0]`` — a node created between them receives
    ``[1,0,1,0,0,0]``.

    Args:
        node_grid: ``(n_v+1)×(n_u+1)`` grid of node IDs (str or None).
        n_u: Subdivision count in the u-direction (grid columns = n_u+1).
        n_v: Subdivision count in the v-direction (grid rows = n_v+1).
        restraints: Model restraints dict, modified in place.
    """
    if not restraints or n_u < 2 or n_v < 2:
        return

    def _and_dofs(r1: Restraint, r2: Restraint) -> list[int]:
        return [a & b for a, b in zip(r1.dofs, r2.dofs)]

    def _apply(c1_id: Optional[str], c2_id: Optional[str], nid: Optional[str]) -> None:
        if nid is None or c1_id is None or c2_id is None:
            return
        if nid in (c1_id, c2_id):
            return
        # Nodes that already carry an explicit restraint (e.g. existing
        # nodes reused via coordinate deduplication) keep it unchanged.
        if nid in restraints:
            return
        r1 = restraints.get(c1_id)
        r2 = restraints.get(c2_id)
        if r1 is not None and r2 is not None:
            restraints[nid] = Restraint(dofs=_and_dofs(r1, r2))

    c_bl = node_grid[0][0]  # bottom-left  (u=0, v=0)
    c_br = node_grid[0][n_u]  # bottom-right (u=n_u, v=0)
    c_tr = node_grid[n_v][n_u]  # top-right    (u=n_u, v=n_v)
    c_tl = node_grid[n_v][0]  # top-left     (u=0, v=n_v)

    # bottom edge (j=0): c_bl -- c_br
    for i in range(1, n_u):
        _apply(c_bl, c_br, node_grid[0][i])
    # top edge (j=n_v): c_tl -- c_tr
    for i in range(1, n_u):
        _apply(c_tl, c_tr, node_grid[n_v][i])
    # left edge (i=0): c_bl -- c_tl
    for j in range(1, n_v):
        _apply(c_bl, c_tl, node_grid[j][0])
    # right edge (i=n_u): c_br -- c_tr
    for j in range(1, n_v):
        _apply(c_br, c_tr, node_grid[j][n_u])

    # ── Interior nodes: DOFs common to all four corners ──────────
    r_bl = restraints.get(c_bl)
    r_br = restraints.get(c_br)
    r_tr = restraints.get(c_tr)
    r_tl = restraints.get(c_tl)
    if all(r is not None for r in (r_bl, r_br, r_tr, r_tl)):
        common = [r_bl.dofs[k] & r_br.dofs[k] & r_tr.dofs[k] & r_tl.dofs[k] for k in range(6)]
        if any(common):
            for j in range(1, n_v):
                for i in range(1, n_u):
                    nid = node_grid[j][i]
                    if nid is not None and nid not in restraints:
                        restraints[nid] = Restraint(dofs=list(common))


def mesh_area_elements(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    area_mesh: dict[str, AreaMesh],
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
    restraints: Optional[dict[str, Restraint]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide area elements into a grid of smaller shell elements.

    Only areas with ``auto_mesh=True`` and a positive ``max_size`` are
    subdivided.  The subdivision count along each edge is calculated from
    ``max_size`` so that no sub-element exceeds that dimension.

    Sub-elements inherit:
      * Section assignment from the parent area.
      * Thickness from the parent area.
      * Group membership (if *groups* is provided) — each sub-element is
        added to every group that contained the parent area ID.

    New nodes created on the perimeter edges inherit a restraint equal to
    the bitwise AND of the two corner-node restraints at the ends of that
    edge (see :func:`_propagate_edge_restraints`).  Interior nodes inherit
    any DOF bit that is common to all four corner restraints.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        area_mesh: ``{area_id: AreaMesh}`` from parsed s2k data.
        next_tag: Next available numeric tag for new nodes and elements.
        groups: Optional ``{group_name: Group}`` — group memberships are
            propagated to sub-elements.
        restraints: Optional ``{node_id: Restraint}`` — model restraints
            dict.  Edge-node restraints are propagated here (modified in
            place).

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided areas added and original areas marked inactive.
    """

    # Coordinate-based node registry for subdividing areas.
    # Populated per-area from corner nodes only, so adjacent areas'
    # shared edges are still deduplicated without collapsing
    # intentionally separate nodes at the same coordinate.
    def _coord_key(x, y, z):
        return (round(x, 6), round(y, 6), round(z, 6))

    _coord_to_id: dict[tuple, str] = {}

    # ── Pre-build vectorised cache of real SAP2000 nodes ──────────
    # Avoids a full nodes.items() scan inside each area's mesh loop.
    _cached_ids: list[str] = []
    _cached_pos: list[np.ndarray] = []
    for nid, nd in nodes.items():
        if nd.node_tag > 999999:
            continue  # internal tag, not a real SAP2000 node
        _cached_ids.append(nid)
        _cached_pos.append(np.array([nd.x, nd.y, nd.z], dtype=float))
    _cached_pos_arr = np.array(_cached_pos)  # shape (N, 3)

    for aid, mesh in area_mesh.items():
        if not mesh.auto_mesh or mesh.max_size <= 0.0:
            continue

        elem = area_elements.get(aid)
        if elem is None or len(elem.node_ids) != 4:
            continue  # only quad areas are meshed
        if getattr(elem, "inactive", False):
            continue  # already subdivided in a previous build

        # Seed registry from this area's corner nodes
        for nid in elem.node_ids:
            nd = nodes.get(nid)
            if nd is not None:
                _coord_to_id[_coord_key(nd.x, nd.y, nd.z)] = nid

        # Compute area bounding box + plane normal for seeding
        # existing interior / edge nodes.
        box_corners = []
        for nid in elem.node_ids:
            nd = nodes.get(nid)
            if nd is not None:
                box_corners.append(np.array([nd.x, nd.y, nd.z]))
        if len(box_corners) >= 3:
            box_pts = np.array(box_corners)
            # Scale-aware tolerance: fraction of max_size, with a floor
            # to keep shared-edge detection consistent across unit systems.
            _tol = max(mesh.max_size * 0.001, 0.001)
            bbox_min = box_pts.min(axis=0) - _tol
            bbox_max = box_pts.max(axis=0) + _tol
            # Cross product of two edges gives plane normal
            v1 = box_pts[1] - box_pts[0]
            v2 = box_pts[-1] - box_pts[0]
            n_plane = np.cross(v1, v2)
            n_len = np.linalg.norm(n_plane)
            if n_len > 1e-12:
                n_plane /= n_len
                # Vectorised bounding-box + plane-distance mask
                in_bbox = np.all(
                    (_cached_pos_arr >= bbox_min) & (_cached_pos_arr <= bbox_max),
                    axis=1,
                )
                near_plane = np.abs(np.dot(_cached_pos_arr - box_pts[0], n_plane)) < _tol
                for idx in np.flatnonzero(in_bbox & near_plane):
                    nid = _cached_ids[idx]
                    if nid in _coord_to_id:
                        continue  # already seeded
                    nd = nodes.get(nid)
                    if nd is not None:
                        _coord_to_id[_coord_key(nd.x, nd.y, nd.z)] = nid

        # Gather corner nodes, ensuring we have unique corners (4-node quad)
        corner_ids = [str(nid) for nid in elem.node_ids]
        if len(corner_ids) != 4 or len(set(corner_ids)) != 4:
            continue

        corners = []
        for nid in corner_ids:
            nd = nodes.get(nid)
            if nd is None:
                break
            corners.append(np.array([nd.x, nd.y, nd.z], dtype=float))
        if len(corners) != 4:
            continue

        # Normalise to CCW winding so all sub-cells have consistent
        # shell normals (OpenSees ShellMITC4 expects CCW ordering).
        # Compute the face normal using the cross product of the two
        # diagonals, then project the quad onto the plane perpendicular
        # to the dominant normal axis for a reliable 2D winding test.
        diag1 = corners[2] - corners[0]
        diag2 = corners[3] - corners[1]
        n_face = np.cross(diag1, diag2)
        abs_n = np.abs(n_face)
        dom = int(np.argmax(abs_n))  # 0=X, 1=Y, 2=Z
        # 2D signed area on the projection plane perpendicular to the
        # dominant normal axis.  Negative → clockwise → reverse.
        if dom == 0:  # project onto YZ  (ey × ez = +ex, right-handed)
            u = [c[1] for c in corners]
            v = [c[2] for c in corners]
        elif dom == 1:  # project onto ZX  (ez × ex = +ey, right-handed)
            u = [c[2] for c in corners]
            v = [c[0] for c in corners]
        else:  # project onto XY  (ex × ey = +ez, right-handed)
            u = [c[0] for c in corners]
            v = [c[1] for c in corners]
        # 2D signed area (shoelace) on the projection plane.
        # Negative → clockwise → reverse the vertex order.
        signed_2d = (
            (u[0] * v[1] - u[1] * v[0])
            + (u[1] * v[2] - u[2] * v[1])
            + (u[2] * v[3] - u[3] * v[2])
            + (u[3] * v[0] - u[0] * v[3])
        ) * 0.5
        if signed_2d < 0:  # clockwise → reverse
            corner_ids = [corner_ids[0], corner_ids[3], corner_ids[2], corner_ids[1]]
            corners = [corners[0], corners[3], corners[2], corners[1]]

        # Determine subdivision counts from max_size
        def _edge_length(a, b):
            return float(np.linalg.norm(b - a))

        l01 = _edge_length(corners[0], corners[1])
        l12 = _edge_length(corners[1], corners[2])
        l23 = _edge_length(corners[2], corners[3])
        l30 = _edge_length(corners[3], corners[0])

        # Use the longest edge in each parametric direction so that
        # no sub-element exceeds max_size, even on tapered faces.
        len_u = max(l01, l23)  # I→J direction (edge 0-1, 2-3)
        len_v = max(l12, l30)  # orthogonal direction (edge 1-2, 3-0)

        n_u = max(1, math.ceil(len_u / mesh.max_size))
        n_v = max(1, math.ceil(len_v / mesh.max_size))

        if n_u == 1 and n_v == 1:
            continue  # no subdivision needed

        # Cap subdivision to prevent memory explosion from tiny max_size.
        MAX_SUBDIVIDE = 100
        if n_u > MAX_SUBDIVIDE or n_v > MAX_SUBDIVIDE:
            raise ValueError(
                f"Area {aid}: subdivision {n_u}×{n_v} exceeds maximum "
                f"{MAX_SUBDIVIDE} (len_u={len_u:.2f}, len_v={len_v:.2f}, "
                f"max_size={mesh.max_size}). Increase max_size."
            )

        # ── Check for interior seed nodes (e.g. wall edge nodes that ──
        # ── lie inside this slab area).  When found, switch to an    ──
        # ── irregular subdivision so the mesh passes through them.   ──
        interior_seeds: list[tuple[float, float]] = []  # (u, v)
        _corner_set = set(corner_ids)
        # Reverse map from _coord_to_id to check seeded nodes
        seeded_ids = set(_coord_to_id.values())
        for nid in seeded_ids:
            if nid in _corner_set:
                continue
            nd_ref = nodes.get(nid)
            if nd_ref is None:
                continue
            pos = np.array([nd_ref.x, nd_ref.y, nd_ref.z], dtype=float)
            uv = _point_uv_on_quad(pos, corners)
            if uv is None:
                continue
            u, v = uv
            # Classify position: corner / perimeter / interior
            at_corner = (u <= 1e-6 or u >= 1.0 - 1e-6) and (v <= 1e-6 or v >= 1.0 - 1e-6)
            if at_corner:
                continue
            interior_seeds.append((u, v))

        # Fold interior seeds into the division lists
        if interior_seeds:
            # Merge near-duplicates and sort
            _tol_uv = 1e-6
            u_vals = sorted(
                set(
                    [0.0, 1.0]
                    + [round(i / n_u, 8) for i in range(n_u + 1)]
                    + [s[0] for s in interior_seeds]
                )
            )
            v_vals = sorted(
                set(
                    [0.0, 1.0]
                    + [round(j / n_v, 8) for j in range(n_v + 1)]
                    + [s[1] for s in interior_seeds]
                )
            )
            # Deduplicate near-equal values
            u_vals = [u_vals[0]] + [
                u for u in u_vals[1:] if u - u_vals[u_vals.index(u) - 1] > _tol_uv
            ]
            v_vals = [v_vals[0]] + [
                v for v in v_vals[1:] if v - v_vals[v_vals.index(v) - 1] > _tol_uv
            ]
            n_u = len(u_vals) - 1
            n_v = len(v_vals) - 1
            # Cap check
            if n_u > MAX_SUBDIVIDE or n_v > MAX_SUBDIVIDE:
                raise ValueError(
                    f"Area {aid}: irregular subdivision {n_u}×{n_v} exceeds "
                    f"maximum {MAX_SUBDIVIDE}. Reduce max_size or remove "
                    f"interior seed nodes."
                )
            use_irregular = True
        else:
            u_vals = [i / n_u for i in range(n_u + 1)]
            v_vals = [j / n_v for j in range(n_v + 1)]
            use_irregular = False

        # Bilinear interpolation to create grid points
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        if use_irregular:
            for j, v in enumerate(v_vals):
                for i, u in enumerate(u_vals):
                    top = corners[0] * (1.0 - u) + corners[1] * u
                    bot = corners[3] * (1.0 - u) + corners[2] * u
                    grid[j, i] = top * (1.0 - v) + bot * v
        else:
            for j in range(n_v + 1):
                v = j / n_v
                for i in range(n_u + 1):
                    u = i / n_u
                    top = corners[0] * (1.0 - u) + corners[1] * u
                    bot = corners[3] * (1.0 - u) + corners[2] * u
                    grid[j, i] = top * (1.0 - v) + bot * v

        # Create new nodes for interior grid points (skip corners)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                if i == 0 and j == 0:
                    node_grid[j][i] = corner_ids[0]
                    continue
                if i == n_u and j == 0:
                    node_grid[j][i] = corner_ids[1]
                    continue
                if i == n_u and j == n_v:
                    node_grid[j][i] = corner_ids[2]
                    continue
                if i == 0 and j == n_v:
                    node_grid[j][i] = corner_ids[3]
                    continue
                new_id = f"{aid}_mesh_{j}_{i}"
                pt = grid[j, i]
                # Reuse existing node at the same coordinates so
                # adjacent meshed areas share edge/interior nodes.
                ck = _coord_key(float(pt[0]), float(pt[1]), float(pt[2]))
                existing = _coord_to_id.get(ck)
                if existing is not None:
                    node_grid[j][i] = existing
                    continue
                new_tag = next_tag
                next_tag += 1
                nodes[new_id] = Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                _coord_to_id[ck] = new_id
                node_grid[j][i] = new_id

        # Mark original area as inactive
        elem.inactive = True

        # Determine which groups contain the parent area
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                ref = f"Area:{aid}"
                if ref in g.objects:
                    parent_groups.append(gname)

        # Create sub-area elements (CCW ordering: 0→1→2→3 per sub-quad)
        sec_name = area_assignments.get(aid, "")
        for j in range(n_v):
            for i in range(n_u):
                sub_id = f"{aid}_sub_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                # Quad corners in CCW order
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                area_elements[sub_id] = AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                    thickness=elem.thickness,
                    parent_id=aid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                # Propagate group membership
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

        # Propagate edge-node restraints using bitwise-AND of adjacent
        # corners (only if both corners are restrained).
        if restraints is not None:
            _propagate_edge_restraints(node_grid, n_u, n_v, restraints)

    return area_elements, area_assignments, nodes, next_tag


# ═══════════════════════════════════════════════════════════════════════
# N×N shell subdivision — subdivide each coarse quad into an N×N
# grid of smaller shell elements at the model-data level.
# ═══════════════════════════════════════════════════════════════════════


def subdivide_area_mesh(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    n: int,
    selection: Optional[set[str]] = None,
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
    restraints: Optional[dict[str, Restraint]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide each coarse shell quad into an N×N grid of sub-elements.

    Operates on model data (``AreaElement`` / ``Node`` dataclasses) so the
    refined mesh is visible to NPZ export, PyVista, Rhino, and diagnostics.

    Each parent area is marked ``inactive=True`` and linked to its children
    via ``parent_id`` / ``child_ids``.  Sub-elements inherit the parent's
    section assignment, thickness, and group membership.

    New interior nodes are created with coordinate-based deduplication so
    adjacent subdivided areas share edge nodes.

    New nodes created on the perimeter edges inherit a restraint equal to
    the bitwise AND of the two corner-node restraints at the ends of that
    edge (see :func:`_propagate_edge_restraints`).  Interior nodes inherit
    any DOF bit that is common to all four corner restraints.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        n: Subdivision count (2 = 2×2 grid, 3 = 3×3, etc.).  Must be ≥ 2.
        selection: Optional set of area IDs to subdivide.  ``None`` = all.
        next_tag: Next available numeric tag for new nodes and elements.
        groups: Optional ``{group_name: Group}`` — group memberships are
            propagated to sub-elements.
        restraints: Optional ``{node_id: Restraint}`` — model restraints
            dict.  Edge-node restraints are propagated here (modified in
            place).

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)``.
    """
    if n < 2:
        return area_elements, area_assignments, nodes, next_tag

    def _coord_key(x, y, z):
        return (round(x, 6), round(y, 6), round(z, 6))

    # Seed coordinate registry from active quadrilateral area corner
    # nodes only — not from every node in the model — so that
    # coincident offset/release/disconnected nodes are not spuriously
    # reused during deduplication.
    _coord_to_id: dict[tuple, str] = {}
    for aid, elem in area_elements.items():
        if getattr(elem, "inactive", False):
            continue
        if len(elem.node_ids) != 4:
            continue
        for nid in elem.node_ids:
            nd = nodes.get(nid)
            if nd is not None:
                _coord_to_id[_coord_key(nd.x, nd.y, nd.z)] = nid

    for aid, elem in list(area_elements.items()):
        if selection is not None and aid not in selection:
            continue
        if getattr(elem, "inactive", False):
            continue
        if len(elem.node_ids) != 4:
            continue

        # Gather corner coordinates
        corners = []
        corner_ids = list(elem.node_ids)
        for nid in corner_ids:
            nd = nodes.get(nid)
            if nd is None:
                break
            corners.append(np.array([nd.x, nd.y, nd.z], dtype=float))
        if len(corners) != 4:
            continue

        # Normalise winding to counter-clockwise using projected
        # signed area so child shells have consistent normals.
        e3 = corners[1] - corners[0]
        e4 = corners[3] - corners[0]
        normal = np.cross(e3, e4)
        plane_z = np.array([0.0, 0.0, 1.0])
        if np.dot(normal, plane_z) < 0.0:
            corners[2], corners[3] = corners[3], corners[2]

        # Bilinear interpolation to create (n+1)² grid points
        grid = np.zeros((n + 1, n + 1, 3))
        for j in range(n + 1):
            v = j / n
            for i in range(n + 1):
                u = i / n
                top = corners[0] * (1.0 - u) + corners[1] * u
                bot = corners[3] * (1.0 - u) + corners[2] * u
                grid[j, i] = top * (1.0 - v) + bot * v

        # Create node grid — reuse existing nodes at corners, create new
        # interior nodes with coordinate dedup
        node_grid = [[None] * (n + 1) for _ in range(n + 1)]
        for j in range(n + 1):
            for i in range(n + 1):
                if i == 0 and j == 0:
                    node_grid[j][i] = corner_ids[0]
                    continue
                if i == n and j == 0:
                    node_grid[j][i] = corner_ids[1]
                    continue
                if i == n and j == n:
                    node_grid[j][i] = corner_ids[2]
                    continue
                if i == 0 and j == n:
                    node_grid[j][i] = corner_ids[3]
                    continue
                pt = grid[j, i]
                ck = _coord_key(float(pt[0]), float(pt[1]), float(pt[2]))
                existing = _coord_to_id.get(ck)
                if existing is not None:
                    node_grid[j][i] = existing
                    continue
                new_id = f"{aid}_sub_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                nodes[new_id] = Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                _coord_to_id[ck] = new_id
                node_grid[j][i] = new_id

        # Determine which groups contain the parent area
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                ref = f"Area:{aid}"
                if ref in g.objects:
                    parent_groups.append(gname)

        # Mark parent inactive
        elem.inactive = True

        # Create n² sub-elements
        sec_name = area_assignments.get(aid, "")
        for j in range(n):
            for i in range(n):
                sub_id = f"{aid}_sub_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                area_elements[sub_id] = AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                    thickness=elem.thickness,
                    parent_id=aid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                # Propagate group membership
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

        # Propagate edge-node restraints using bitwise-AND of adjacent
        # corners (only if both corners are restrained).
        if restraints is not None:
            _propagate_edge_restraints(node_grid, n, n, restraints)

    return area_elements, area_assignments, nodes, next_tag


# ═══════════════════════════════════════════════════════════════════════
# AtFrames for areas — subdivide shell elements at frame edge nodes
# ═══════════════════════════════════════════════════════════════════════


def split_areas_at_frame_edges(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    frame_elements: dict[str, FrameElement],
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide areas at frame-element edge nodes not at area corners.

    For each area that is not already subdivided (``inactive=False``),
    finds all frame element nodes that lie on the area's perimeter edges.
    The area is then subdivided into a grid that passes through those
    intermediate edge nodes, creating sub-elements whose corner nodes
    include the frame connection points.

    This mirrors the ``AtFrames`` splitting applied to frame elements \u2014
    it ensures that frame elements terminating on an area edge are
    connected to the shell mesh.

    Sub-elements inherit:
      * Section assignment from the parent area.
      * Thickness from the parent area.
      * Parent-child relationship (``parent_id`` / ``child_ids``).

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` \u2014 new interior nodes are added here.
        frame_elements: ``{frame_id: FrameElement}`` \u2014 used to find frame
            node locations that should connect to area edges.
        next_tag: Next available numeric tag for new nodes and elements.
        groups: Optional ``{group_name: Group}`` \u2014 group memberships are
            propagated to sub-elements.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided areas added and original areas marked inactive.
    """
    from .sap_data import AreaElement as _AreaElement
    from .sap_data import Node as _Node

    # Collect all frame node IDs
    frame_node_ids: set = set()
    for fe in frame_elements.values():
        frame_node_ids.add(fe.node_i)
        frame_node_ids.add(fe.node_j)

    # Coordinates of frame nodes (for collinearity tests)
    frame_node_coords: dict[str, np.ndarray] = {}
    # \u2500\u2500 Spatial indices for frame nodes \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # Z-band index \u2014 groups nodes by integer Z-band (efficient for
    # slabs).  Uses int(z / tol) for robust bucket keys \u2014 avoids
    # round() pitfalls where nearby values can land in different buckets.
    z_band_tol = 0.1  # 100\u202fmm band width
    frame_nodes_by_z: dict[int, list[str]] = {}
    # XY spatial grid \u2014 groups nodes by integer XY cell (efficient
    # for walls).  Cell size should be large enough that a wall\u2019s
    # bounding box overlaps at least one cell, small enough that the
    # number of candidate nodes per cell stays manageable.
    xy_grid_size = 0.5  # 500\u202fmm cell size
    frame_nodes_xy: dict[tuple[int, int], list[str]] = {}
    for nid in frame_node_ids:
        nd = nodes.get(nid)
        if nd is not None:
            arr = np.array([nd.x, nd.y, nd.z], dtype=float)
            frame_node_coords[nid] = arr
            z_key = int(nd.z / z_band_tol)
            frame_nodes_by_z.setdefault(z_key, []).append(nid)
            x_key = int(round(nd.x / xy_grid_size))
            y_key = int(round(nd.y / xy_grid_size))
            frame_nodes_xy.setdefault((x_key, y_key), []).append(nid)

    if not frame_node_coords:
        return area_elements, area_assignments, nodes, next_tag

    for aid, elem in list(area_elements.items()):
        if getattr(elem, "inactive", False):
            continue
        if elem is None or len(elem.node_ids) != 4:
            continue

        corners = list(elem.node_ids)
        corner_coords = [
            np.array([nodes[c].x, nodes[c].y, nodes[c].z], dtype=float) if c in nodes else None
            for c in corners
        ]
        if any(c is None for c in corner_coords):
            continue

        # Ensure CCW ordering (same convention as mesh_area_elements)
        c0, c1, c2, c3 = corner_coords
        u = c1 - c0
        v = c3 - c0
        normal = np.cross(u, v)
        signed = float(np.dot(normal, np.cross(c2 - c0, c3 - c0)))
        if signed < 0:
            corners = [corners[0], corners[3], corners[2], corners[1]]
            corner_coords = [corner_coords[0], corner_coords[3], corner_coords[2], corner_coords[1]]

        # Four edges: (0\u21921), (1\u21922), (2\u21923), (3\u21920)
        edges = [
            (0, 1, "u"),  # edge 0\u21921, u-direction
            (1, 2, "v"),  # edge 1\u21922, v-direction
            (2, 3, "u"),  # edge 2\u21923, u-direction (reverse)
            (3, 0, "v"),  # edge 3\u21920, v-direction (reverse)
        ]

        # Choose spatial filter based on orientation
        normal_len = float(np.linalg.norm(normal))
        nz_abs = abs(normal[2]) / normal_len if normal_len > 0 else 0.0
        # Warn if a vertical area (|nz|\u22480) has a slab-like section name
        sec_name = area_assignments.get(aid, "")
        if nz_abs < 0.1 and "slab" in sec_name.lower():
            warnings.warn(
                f'Area {aid}: section "{sec_name}" assigned to a vertical '
                f"element (|nz|={nz_abs:.3f}) \u2014 likely mis-classified in "
                f"the source model.",
                UserWarning,
                stacklevel=2,
            )
        local_frame_ids: set = set()

        if nz_abs > 0.707:
            # \u2500\u2500 Slab (near-horizontal): Z-band filter \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
            # Slabs span a single Z level, so only 1\u20132 bands need
            # checking \u2014 very efficient.
            az_min = float(min(c[2] for c in corner_coords))
            az_max = float(max(c[2] for c in corner_coords))
            z0 = int(az_min / z_band_tol)
            z1 = int(az_max / z_band_tol)
            for zb in range(z0, z1 + 1):
                local_frame_ids.update(frame_nodes_by_z.get(zb, []))
        else:
            # \u2500\u2500 Wall (near-vertical): XY spatial grid filter \u2500\u2500\u2500\u2500
            # Walls span multiple Z levels but occupy a narrow XY
            # footprint.  The XY grid limits candidate nodes to those
            # in the area\u2019s XY bounding box.
            xs = [float(c[0]) for c in corner_coords]
            ys = [float(c[1]) for c in corner_coords]
            margin = max(xy_grid_size * 1.5, 0.5)  # at least 500\u202fmm margin
            ix_min = int(round((min(xs) - margin) / xy_grid_size))
            ix_max = int(round((max(xs) + margin) / xy_grid_size))
            iy_min = int(round((min(ys) - margin) / xy_grid_size))
            iy_max = int(round((max(ys) + margin) / xy_grid_size))
            for ix in range(ix_min, ix_max + 1):
                for iy in range(iy_min, iy_max + 1):
                    local_frame_ids.update(frame_nodes_xy.get((ix, iy), []))

        edge_node_lists: dict[str, list] = {"u": [], "v": []}
        for ei, ej, direction in edges:
            p_i = corner_coords[ei]
            p_j = corner_coords[ej]
            edge_vec = p_j - p_i
            edge_len_sq = float(np.dot(edge_vec, edge_vec))
            if edge_len_sq < 1e-12:
                continue
            edge_len = float(np.sqrt(edge_len_sq))
            edge_dir = edge_vec / edge_len

            for nid in local_frame_ids:
                if nid in corners:
                    continue  # skip area corner nodes
                npos = frame_node_coords[nid]
                vec_ip = npos - p_i
                # Collinearity: cross product near zero
                cross_len = float(np.linalg.norm(np.cross(edge_vec, vec_ip)))
                if cross_len / edge_len > 1e-4:
                    continue
                # Within segment: projection t between -tol and 1+tol
                t = float(np.dot(vec_ip, edge_dir)) / edge_len
                if t < -1e-6 or t > 1 + 1e-6:
                    continue
                # Store with adjustment for reversed edges
                if direction == "u":
                    if ei == 0:  # edge 0\u21921: t as-is
                        edge_node_lists["u"].append(t)
                    else:  # edge 2\u21923: reverse \u2192 1-t
                        edge_node_lists["u"].append(1.0 - t)
                elif ei == 1:  # edge 1\u21922: t as-is
                    edge_node_lists["v"].append(t)
                else:  # edge 3\u21920: reverse \u2192 1-t
                    edge_node_lists["v"].append(1.0 - t)

        # Deduplicate and sort t-values (add 0 and 1 as implicit boundaries)
        u_vals = sorted(set([0.0, 1.0] + edge_node_lists["u"]))
        v_vals = sorted(set([0.0, 1.0] + edge_node_lists["v"]))

        # Only subdivide if there\u2019s at least one intermediate node
        if len(u_vals) <= 2 and len(v_vals) <= 2:
            continue

        # Ensure minimum spacing between t-values (merge near-duplicates)
        _tol = 1e-6
        u_vals = [u_vals[0]] + [u for u in u_vals[1:] if u - u_vals[u_vals.index(u) - 1] > _tol]
        v_vals = [v_vals[0]] + [v for v in v_vals[1:] if v - v_vals[v_vals.index(v) - 1] > _tol]

        n_u = len(u_vals) - 1  # number of sub-divisions in u-direction
        n_v = len(v_vals) - 1

        # Ensure reasonable aspect ratio \u2014 if one direction has far fewer
        # divisions than the other, subdivide the coarser direction so
        # element aspect ratios stay below max_aspect_ratio.
        max_aspect = 4.0
        if n_u > 0 and n_v > 0:
            # Estimate element sizes from edge lengths
            len_u = max(
                float(np.linalg.norm(corner_coords[1] - corner_coords[0])),
                float(np.linalg.norm(corner_coords[2] - corner_coords[3])),
            )
            len_v = max(
                float(np.linalg.norm(corner_coords[2] - corner_coords[1])),
                float(np.linalg.norm(corner_coords[3] - corner_coords[0])),
            )
            elem_u = len_u / n_u
            elem_v = len_v / n_v
            if elem_u > elem_v * max_aspect and elem_u > 0:
                # u-direction too coarse \u2014 subdivide u_vals more
                extra = int(math.ceil(elem_u / (elem_v * max_aspect))) - 1
                for k in range(1, extra + 1):
                    u_vals.append(k / (extra + 1))
                u_vals = sorted(set(u_vals))
            elif elem_v > elem_u * max_aspect and elem_v > 0:
                # v-direction too coarse \u2014 subdivide v_vals more
                extra = int(math.ceil(elem_v / (elem_u * max_aspect))) - 1
                for k in range(1, extra + 1):
                    v_vals.append(k / (extra + 1))
                v_vals = sorted(set(v_vals))
            n_u = len(u_vals) - 1
            n_v = len(v_vals) - 1

        # Bilinear grid at parametric positions
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        for j, v in enumerate(v_vals):
            for i, u in enumerate(u_vals):
                top = corner_coords[0] * (1 - u) + corner_coords[1] * u
                bot = corner_coords[3] * (1 - u) + corner_coords[2] * u
                grid[j, i] = top * (1 - v) + bot * v

        # Build spatial coordinate cache once (reuse across grid points)
        _pos_cache_np: dict[str, np.ndarray] = {
            nid: np.array([nd.x, nd.y, nd.z], dtype=float) for nid, nd in nodes.items()
        }
        # Merge frame node coords into cache
        for nid, npos in frame_node_coords.items():
            _pos_cache_np[nid] = npos
        # Create nodes for grid points (reusing frame nodes at same coords)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                pt = grid[j, i]
                # Check spatial cache for existing node at this position
                found = None
                for nid, npos in _pos_cache_np.items():
                    if np.linalg.norm(npos - pt) < 1e-4:
                        found = nid
                        break
                if found is not None:
                    node_grid[j][i] = found
                    continue
                # Create new node
                new_id = f"{aid}_af_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                nd = _Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                nodes[new_id] = nd
                _pos_cache_np[new_id] = np.array([pt[0], pt[1], pt[2]], dtype=float)
                node_grid[j][i] = new_id

        # Mark original as inactive and record parent-child
        elem.inactive = True
        elem.child_ids = []

        # Determine parent groups
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                ref = f"Area:{aid}"
                if ref in g.objects:
                    parent_groups.append(gname)

        sec_name = area_assignments.get(aid, "")
        for j in range(n_v):
            for i in range(n_u):
                sub_id = f"{aid}_af_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                # Deduplicate collinear collapse
                deduped = []
                for n in (n0, n1, n2, n3):
                    if not deduped or n != deduped[-1]:
                        deduped.append(n)
                if len(deduped) < 3:
                    continue
                area_elements[sub_id] = _AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=deduped,
                    thickness=getattr(elem, "thickness", 0.0),
                    inactive=False,
                    parent_id=aid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

    return area_elements, area_assignments, nodes, next_tag


# ═══════════════════════════════════════════════════════════════════════
# Constraint-edge detection for slab-wall and misaligned slab interfaces
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Constraint-edge detection — finds mesh nodes that are on only one
# side of an edge (these need constraints to "heal" incompatible meshes).
# Uses coordinate-based matching: a node on a slab perimeter that has
# no twin node at the same coordinate on the adjacent area's perimeter
# is a free node that needs tying to the other side.
# ═══════════════════════════════════════════════════════════════════════

# ── Frame-overlap detection ───────────────────────────────────────────


def warn_frame_overlaps(
    frame_elements: dict[str, FrameElement],
    frame_assignments: Optional[dict[str, str]],
    nodes: dict[str, Node],
    *,
    prefix: str = "",
    exclude_types: frozenset = frozenset({"brick wall"}),
) -> None:
    """Warn about overlapping or collinear frame elements.

    Builds a frame-only edge registry and checks for:
    * identical overlaps (same end nodes),
    * partial overlaps (collinear edges sharing a node).

    Overlapping duplicate members should be fixed in the source model.

    Note:
        This uses a lightweight check rather than the full sweep-line
        algorithm from :func:`find_constraint_edges` because simple
        sub-segment overlaps (e.g. a beam spanning half a longer beam)
        don't create chain junctions for the sweep to detect.

    Args:
        frame_elements: ``{frame_id: FrameElement}``.
        frame_assignments: ``{frame_id: section_name}`` or ``None``.
        nodes: ``{node_id: Node}``.
        prefix: Optional prefix for warning messages.
        exclude_types: Section name patterns to skip (case-insensitive
            substring match).
    """
    from collections import defaultdict

    import numpy as np

    COSINE_TOL = 0.9999

    assign = frame_assignments or {}
    _node_arr = {nid: np.array([nd.x, nd.y, nd.z], dtype=float) for nid, nd in nodes.items()}

    def _is_excluded(name: str) -> bool:
        name_lower = name.lower()
        return any(excl.lower() in name_lower for excl in exclude_types)

    def _pos_key(nid: str) -> tuple:
        nd = nodes[nid]
        return (nd.x, nd.y, nd.z, nid)

    def _cos_match(d1, d2):
        return float(np.dot(d1, d2)) > COSINE_TOL

    def _chain_dir(key, from_node):
        """Unit vector from from_node toward the other end of key."""
        other = key[1] if key[0] == from_node else key[0]
        vec = _node_arr[other] - _node_arr[from_node]
        nrm = float(np.linalg.norm(vec))
        return vec / nrm if nrm > 1e-12 else None

    # Build frame-only edge registry
    frame_reg: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fid, felem in frame_elements.items():
        if getattr(felem, "inactive", False) or felem is None:
            continue
        if _is_excluded(assign.get(fid, "")):
            continue
        nA, nB = felem.node_i, felem.node_j
        if nA == nB:
            continue
        key = (nA, nB) if _pos_key(nA) <= _pos_key(nB) else (nB, nA)
        frame_reg[key].append(fid)

    # ── 1. Identical overlaps (same key, 2+ frame elements) ────
    for key, fids in frame_reg.items():
        if len(fids) >= 2:
            names = {fid: assign.get(fid, "?") for fid in fids}
            warnings.warn(
                f"{prefix}Overlapping frame elements on edge "
                f"{key[0]}\u2192{key[1]}: {names}.  "
                f"Consolidate in the source model.",
                stacklevel=2,
            )

    # ── 2. Partial overlaps (different keys, collinear, share
    #      a node, different lengths) ────────────────────────────
    node_edges: dict[str, list[tuple]] = defaultdict(list)
    for (nA, nB), fids in frame_reg.items():
        fid = fids[0]
        ndA, ndB = _node_arr[nA], _node_arr[nB]
        length = float(np.linalg.norm(ndB - ndA))
        node_edges[nA].append(((nA, nB), fid, length))
        node_edges[nB].append(((nA, nB), fid, length))

    checked: set = set()
    for _node, incident in node_edges.items():
        if len(incident) < 2:
            continue
        for i in range(len(incident)):
            ki, fidi, leni = incident[i]
            for j in range(i + 1, len(incident)):
                kj, fidj, lenj = incident[j]
                pair = frozenset({(ki, fidi), (kj, fidj)})
                if pair in checked:
                    continue
                checked.add(pair)
                d_i = _chain_dir(ki, _node)
                d_j = _chain_dir(kj, _node)
                if d_i is None or d_j is None:
                    continue
                if not _cos_match(d_i, d_j):
                    continue
                if abs(leni - lenj) > 1e-9:
                    ni = assign.get(fidi, "?")
                    nj = assign.get(fidj, "?")
                    warnings.warn(
                        f"{prefix}Overlapping collinear frame elements: "
                        f"{fidi} ({ni}) on {ki[0]}\u2192{ki[1]} "
                        f"and {fidj} ({nj}) on {kj[0]}\u2192{kj[1]}.  "
                        f"Consolidate in the source model.",
                        stacklevel=2,
                    )


def find_constraint_edges(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    frame_elements: Optional[dict[str, FrameElement]] = None,
    frame_assignments: Optional[dict[str, str]] = None,
    exclude_types: frozenset = frozenset({"brick"}),
    verbose: bool = True,
) -> list[tuple[list[str], list[tuple], list[tuple], str, str]]:
    """Find tears in the final mesh via sweep-line chain following.

    Detects locations where two (or more) adjacent elements share a
    geometric edge but have incompatible meshes — one chain has intermediate
    nodes the other doesn't.  These need line/edge constraints in OpenSees.

    Supports both area elements (2D shells) and frame elements (1D beams).
    A beam running along a meshed slab edge will be detected: the slab's
    sub-elements create intermediate mesh nodes on the shared edge while
    the single beam element spans the full length.

    .. note::
       Frame-element edges are only compared against area-element edges.
       Frame-vs-frame overlaps are **skipped** — they indicate modelling
       errors (duplicate/collinear members) and should be resolved in the
       source model rather than patched with constraints.  Use
       :func:`warn_frame_overlaps` to detect them separately.

    Uses a sorted-tuple edge registry where each key ``(nA, nB)`` is
    ordered by node position (X → Y → Z), making the direction implicit:
    nA → nB is the positive direction.  Compatible edges (same key with
    2+ elements) are identified automatically because they produce the
    identical sorted tuple.

    Non-structural element types (e.g. brick infill walls used as load
    panels) are excluded via ``exclude_types`` using case-insensitive
    substring matching (e.g. ``{'brick'}`` matches ``'Brick'``,
    ``'Brick Wall'``, ``'BRICK WALL'``, ``'BrickInfill'``, etc.).

    Args:
        area_elements: ``{area_id: AreaElement}`` — all area elements.
        area_assignments: ``{area_id: section_name}``.
        nodes: ``{node_id: Node}`` — all nodes.
        frame_elements: Optional ``{frame_id: FrameElement}`` — frame
            elements.  Pass post-split elements (e.g. ``builder.split_elements``)
            for best results.
        frame_assignments: Optional ``{frame_id: section_name}``.
        exclude_types: Set of section name patterns to skip.  Each entry
            is matched as a case-insensitive substring.

    Returns:
        List of tuples.  Each tuple has:
        - ``merged_nodes`` — all unique nodes along the shared edge,
          sorted by position.
        - ``master_chain`` — ``[(node_id, t_param), ...]`` — the nodes
          belonging to the coarsest element along the edge.  These
          define the master edge for constraint application.
        - ``slave_nodes`` — ``[(node_id, t_param), ...]`` — all
          remaining nodes from finer-meshed elements, sorted by
          t-parameter.  These need to be constrained to the master
          edge.
        - ``type_a``, ``type_b`` — the two most common section names
          (for backward compatibility).

        The t-parameters are in the range [0, 1] along the edge from
        the first merged node to the last.
    """
    from collections import defaultdict

    import numpy as np

    COSINE_TOL = 0.9999

    # ── Helper: case-insensitive substring matching ─────────────
    # SAP2000 section names vary in case (e.g. "Brick Wall",
    # "BRICK WALL", "BrickInfill").  Each entry in exclude_types is
    # matched as a case-insensitive substring of the section name.
    def _is_excluded(name: str) -> bool:
        name_lower = name.lower()
        return any(excl.lower() in name_lower for excl in exclude_types)

    # ── 0. Node arrays & position sort key ──────────────────────
    _node_arr: dict[str, np.ndarray] = {}
    for nid, nd in nodes.items():
        _node_arr[nid] = np.array([nd.x, nd.y, nd.z], dtype=float)

    def _pos_key(nid: str) -> tuple:
        nd = nodes[nid]
        return (nd.x, nd.y, nd.z, nid)

    # ── 1. Build sorted-tuple edge registry ─────────────────────
    # {(nA, nB): [elem_id, ...]}  where nA ≤ nB by position (X→Y→Z).
    # Direction from nA→nB is the positive direction.
    # Separate sets track which keys came from area vs frame elements
    # so the sweep can skip frame-vs-frame comparisons (those should
    # be resolved as modeling errors, not constraints).
    edge_reg: dict[tuple[str, str], list[str]] = defaultdict(list)
    area_keys: set = set()  # keys contributed by area elements
    frame_keys: set = set()  # keys contributed by frame elements

    for aid, elem in area_elements.items():
        if getattr(elem, "inactive", False) or elem is None or len(elem.node_ids) < 3:
            continue
        if _is_excluded(area_assignments.get(aid, "")):
            continue
        nids = list(elem.node_ids)
        n = len(nids)
        for i in range(n):
            j = (i + 1) % n
            nA, nB = nids[i], nids[j]
            if nA == nB:
                continue
            key = (nA, nB) if _pos_key(nA) <= _pos_key(nB) else (nB, nA)
            edge_reg[key].append(aid)
            area_keys.add(key)

    # ── 1b. Add frame element edges to the registry ─────────────
    if frame_elements is not None:
        assign = frame_assignments or {}
        for fid, felem in frame_elements.items():
            if getattr(felem, "inactive", False) or felem is None:
                continue
            if _is_excluded(assign.get(fid, "")):
                continue
            nA, nB = felem.node_i, felem.node_j
            if nA == nB:
                continue
            key = (nA, nB) if _pos_key(nA) <= _pos_key(nB) else (nB, nA)
            edge_reg[key].append(fid)
            frame_keys.add(key)

    # ── 2. Build node→keys index for chain following ────────────
    # Derived directly from the registry — not a separate data model.
    node_keys: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for nA, nB in edge_reg:
        node_keys[nA].append((nA, nB))
        node_keys[nB].append((nA, nB))

    # ── Helpers ─────────────────────────────────────────────────
    def _cos_match(d1: np.ndarray, d2: np.ndarray) -> bool:
        return float(np.dot(d1, d2)) > COSINE_TOL

    def _chain_dir(key: tuple, from_node: str) -> np.ndarray:
        """Unit vector from from_node to the other end of the key."""
        other = key[1] if key[0] == from_node else key[0]
        vec = _node_arr[other] - _node_arr[from_node]
        nrm = float(np.linalg.norm(vec))
        return vec / nrm if nrm > 1e-12 else None

    def _follow(start_node: str, key: tuple) -> list[str]:
        """Follow a single chain from start_node through key."""
        other = key[1] if key[0] == start_node else key[0]
        chain = [start_node, other]
        cur = other
        acc_dir = _node_arr[cur] - _node_arr[start_node]
        nrm = float(np.linalg.norm(acc_dir))
        if nrm > 1e-12:
            acc_dir /= nrm
        else:
            return chain
        visited = {start_node, cur}
        while cur in node_keys:
            found = None
            for k in node_keys[cur]:
                d = _chain_dir(k, cur)
                if d is not None and _cos_match(d, acc_dir):
                    found = k
                    break
            if found is None:
                break
            nxt = found[1] if found[0] == cur else found[0]
            if nxt in visited:
                break
            visited.add(nxt)
            chain.append(nxt)
            vec = _node_arr[nxt] - _node_arr[start_node]
            nrm = float(np.linalg.norm(vec))
            if nrm > 1e-12:
                acc_dir = vec / nrm
            cur = nxt
        return chain

    def _truncate_at_junction(c1: list[str], c2: list[str]):
        """Truncate both chains at the first common node after start.
        Returns (truncated_c1, truncated_c2) or None if no junction."""
        set2 = set(c2[1:])
        for i, nid in enumerate(c1[1:], 1):
            if nid in set2:
                j = c2.index(nid)
                return c1[: i + 1], c2[: j + 1]
        return None

    def _collect(chains: list[list[str]], d: np.ndarray) -> list[str]:
        nodes_set: set = set()
        for ch in chains:
            nodes_set.update(ch)
        ref = _node_arr[chains[0][0]]
        return sorted(
            nodes_set,
            key=lambda nid: float(np.dot(_node_arr[nid] - ref, d)),
        )

    # ── 3. Single pass: horizontal edges (by Z-band) ────────────
    # All edges in this model are at constant Z — there are no true
    # vertical (Z-changing) tears.  A single Z-band sweep suffices.
    z_groups: dict[float, list[tuple]] = defaultdict(list)
    for nA, nB in edge_reg:
        zk = round((_node_arr[nA][2] + _node_arr[nB][2]) * 0.5, 1)
        z_groups[zk].append((nA, nB))

    tears: list[tuple] = []  # each: (merged_nodes, [(chain, elem_id), ...])

    for zk, group in z_groups.items():
        if len(group) < 2:
            continue
        # Sort by min-X then min-Y of the two nodes
        group.sort(
            key=lambda k: (
                min(_node_arr[k[0]][0], _node_arr[k[1]][0]),
                min(_node_arr[k[0]][1], _node_arr[k[1]][1]),
            )
        )
        active: list[tuple] = []  # list of (nA, nB)

        for nA, nB in group:
            x_min = min(_node_arr[nA][0], _node_arr[nB][0])

            # Retire keys whose max X < sweep position
            active = [
                k for k in active if max(_node_arr[k[0]][0], _node_arr[k[1]][0]) >= x_min - 1e-6
            ]

            # Check both nA and nB against the active set
            for k in active:
                if k == (nA, nB):
                    continue  # same key → compatible edge, skip
                shared = {nA, nB} & set(k)
                if not shared:
                    continue
                sn = next(iter(shared))
                d1 = _chain_dir((nA, nB), sn)
                d2 = _chain_dir(k, sn)
                if d1 is None or d2 is None:
                    continue
                if not _cos_match(d1, d2):
                    continue
                # Skip frame-vs-frame comparisons — overlapping frame
                # elements should be fixed in the source model, not
                # patched with constraints.  Frame-vs-area IS wanted.
                if (nA, nB) not in area_keys and k not in area_keys:
                    continue
                c1 = _follow(sn, (nA, nB))
                c2 = _follow(sn, k)
                trunc = _truncate_at_junction(c1, c2)
                if trunc is not None:
                    ordered = _collect([trunc[0], trunc[1]], d1)
                    if len(ordered) >= 3:
                        # Store the element IDs from the two starting keys
                        ea = edge_reg[(nA, nB)][0]
                        eb = edge_reg[k][0]
                        tears.append((ordered, [(trunc[0], ea), (trunc[1], eb)]))

            active.append((nA, nB))

    # ── Helper: resolve element ID to type name ─────────────────
    # Checks area_assignments first, then frame_assignments.
    fa = frame_assignments or {}

    def _elem_type(eid: str) -> str:
        return area_assignments.get(eid) or fa.get(eid) or "unknown"

    # ── 4. Merge overlapping tears ──────────────────────────────
    # Two tears overlap if they share ≥1 node and are colinear
    # (absolute cosine of endpoint vectors > COSINE_TOL).
    # Each tear carries the per-element node chains for ALL elements
    # sharing the edge — there can be 2, 3, or more.
    merged: list[tuple[list[str], list[tuple], str, str]] = []
    # (merged_nodes, [(chain_with_t, type), ...], type_a, type_b)
    used = [False] * len(tears)
    from collections import defaultdict

    for i in range(len(tears)):
        if used[i]:
            continue
        nids_i, chains_i = tears[i]
        group_nids = [nids_i]
        # elem_id → set_of_nodes for each participating element
        elem_chains: dict[str, set] = {}
        for chain, eid in chains_i:
            elem_chains[eid] = set(chain)
        types_by_elem: dict[str, str] = {eid: _elem_type(eid) for chain, eid in chains_i}
        used[i] = True
        # Direction from first tear's endpoints
        fa_i = _node_arr[nids_i[0]]
        la_i = _node_arr[nids_i[-1]]
        d_i = la_i - fa_i
        dni = float(np.linalg.norm(d_i))
        if dni > 1e-12:
            d_i /= dni
        else:
            d_i = np.array([1.0, 0.0, 0.0])

        for j in range(i + 1, len(tears)):
            if used[j]:
                continue
            nids_j, chains_j = tears[j]
            shared = set(nids_i) & set(nids_j)
            if not shared:
                continue
            fa_j = _node_arr[nids_j[0]]
            la_j = _node_arr[nids_j[-1]]
            d_j = la_j - fa_j
            dnj = float(np.linalg.norm(d_j))
            if dnj > 1e-12:
                d_j /= dnj
            else:
                d_j = np.array([1.0, 0.0, 0.0])
            if abs(float(np.dot(d_i, d_j))) <= COSINE_TOL:
                continue
            group_nids.append(nids_j)
            for chain, eid in chains_j:
                if eid in elem_chains:
                    elem_chains[eid].update(chain)
                else:
                    elem_chains[eid] = set(chain)
                    types_by_elem[eid] = _elem_type(eid)
            used[j] = True

        all_nodes: set = set()
        ref_node = group_nids[0][0]
        for t in group_nids:
            all_nodes.update(t)
        ordered = sorted(
            all_nodes,
            key=lambda nid: float(np.dot(_node_arr[nid] - _node_arr[ref_node], d_i)),
        )
        if len(ordered) >= 3:
            # Compute t-parameters along the edge
            span_vec = _node_arr[ordered[-1]] - _node_arr[ordered[0]]
            span_len = float(np.linalg.norm(span_vec))
            span_dir = span_vec / span_len if span_len > 1e-12 else np.array([1.0, 0.0, 0.0])
            ref_pt = _node_arr[ordered[0]]

            def _chain_with_t(
                nodes: set, ref_pt=ref_pt, span_dir=span_dir, span_len=span_len
            ) -> list[tuple]:
                items = [
                    (nid, float(np.dot(_node_arr[nid] - ref_pt, span_dir)) / span_len)
                    for nid in nodes
                    if nid in _node_arr
                ]
                items.sort(key=lambda x: x[1])
                return items

            # Build per-element chains with t-values, WITHOUT merging by type
            # (merging by type mixes coarse and fine nodes from different
            # elements of the same section — we need to keep them separate
            # to correctly identify which chain is the coarsest master).
            all_chains: list[tuple] = []  # [(chain_with_t, type, elem_id), ...]
            for eid, elem_nodes in elem_chains.items():
                t = types_by_elem[eid]
                all_chains.append((_chain_with_t(elem_nodes), t, eid))

            # Sort by chain length (fewest nodes = coarsest mesh = master)
            all_chains.sort(key=lambda x: len(x[0]))

            # The shortest chain defines the master edge
            master_chain = all_chains[0][0] if all_chains else []

            # Warn if master chain doesn't span the full tear (t=0 to t=1)
            if verbose and len(master_chain) >= 2:
                t_start = master_chain[0][1]
                t_end = master_chain[-1][1]
                if t_start > 0.01 or t_end < 0.99:
                    print(
                        f"  ⚠ Master chain t-range [{t_start:.3f}, {t_end:.3f}] "
                        f"does not span full tear — slave nodes outside this "
                        f"range will use extrapolated interpolation"
                    )

            # All other chains are slaves
            slave_chains = [c for c, _, _ in all_chains[1:]] if len(all_chains) > 1 else []

            # Collect all slave nodes (excluding master nodes)
            master_nodes = {nid for nid, _ in master_chain}
            slave_nodes: list[tuple] = []
            for sc in slave_chains:
                for nid, tval in sc:
                    if nid not in master_nodes:
                        slave_nodes.append((nid, tval))
            slave_nodes.sort(key=lambda x: x[1])

            # Verify master chain integrity: all master nodes should be
            # colinear and in order along the span direction
            if len(master_chain) >= 2:
                m0 = _node_arr[master_chain[0][0]]
                m1 = _node_arr[master_chain[-1][0]]
                span_dir = m1 - m0
                span_len = float(np.linalg.norm(span_dir))
                if span_len > 1e-12:
                    span_dir /= span_len
                    # Check each intermediate master node is on the span line
                    for nid, tval in master_chain[1:-1]:
                        pt = _node_arr[nid]
                        proj = float(np.dot(pt - m0, span_dir))
                        off = float(np.linalg.norm(pt - (m0 + proj * span_dir)))
                        if off > 0.01 * span_len:
                            # Master node is off the span line — add to slaves
                            slave_nodes.append((nid, tval))
                            master_chain = [
                                (m0_n, m0_t) for m0_n, m0_t in master_chain if m0_n != nid
                            ]

            type_names = sorted({t for _, t, _ in all_chains})
            type_a = type_names[0] if type_names else "unknown"
            type_b = type_names[-1] if len(type_names) > 1 else type_a
            merged.append((ordered, master_chain, slave_nodes, type_a, type_b))

    # ── 5. Format results ───────────────────────────────────────
    # Returns: [(merged_nodes, master_chain, slave_nodes, type_a, type_b)]
    # master_chain: [(node_id, t), ...] — coarsest element's nodes (master edge)
    # slave_nodes:  [(node_id, t), ...] — all non-master nodes in order
    results: list[tuple[list[str], list[tuple], list[tuple], str, str]] = []
    for nids, master, slaves, ta, tb in merged:
        results.append((nids, master, slaves, ta, tb))

    return results


# ═══════════════════════════════════════════════════════════════════════
# Wall-slab intersection diagnostics and splitting
# ═══════════════════════════════════════════════════════════════════════


def find_wall_nodes_inside_slabs(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    z_tol: float = 0.5,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Identify wall-area nodes that lie inside slab areas.

    A *wall* is any area whose corners span more than *z_tol* in Z
    (vertical orientation).  A *slab* is any area whose corners are all
    within *z_tol* (horizontal orientation).

    For each slab, this function finds all wall nodes that are on the
    slab's Z plane and within the slab's XY bounding box but NOT at a
    slab corner.  These are wall-to-slab connections that the regular
    meshing pipeline may miss.

    Args:
        area_elements: ``{area_id: AreaElement}``.
        area_assignments: ``{area_id: section_name}``.
        nodes: ``{node_id: Node}``.
        z_tol: Z-span threshold (same units as model) — areas with
            Z-span ≤ *z_tol* are considered horizontal (slabs), those
            with Z-span > *z_tol* are vertical (walls).

    Returns:
        List of dicts, one per wall-in-slab finding::

            {
                "slab_id": str,         # slab area ID
                "wall_id": str,         # wall area ID
                "nodes": [              # wall nodes inside the slab
                    {
                        "node_id": str,
                        "node_tag": int,
                        "x": float, "y": float, "z": float,
                    }
                ],
                "slab_X": (float, float),   # slab X range
                "slab_Y": (float, float),   # slab Y range
                "slab_Z": float,             # slab Z level
                "section": str,              # wall section name
            }
    """
    # ── Classify areas ──────────────────────────────────────────
    slab_ids: set = set()
    wall_ids: set = set()
    for aid, ae in area_elements.items():
        if getattr(ae, "inactive", False):
            continue
        nds = [nodes.get(n) for n in ae.node_ids]
        nds = [n for n in nds if n is not None]
        if len(nds) < 4:
            continue
        zs = [n.z for n in nds]
        z_span = max(zs) - min(zs)
        if z_span <= z_tol:
            slab_ids.add(aid)
        else:
            wall_ids.add(aid)

    if not slab_ids or not wall_ids:
        return []

    # ── Gather wall node coordinates ────────────────────────────
    # Wall nodes that are at each wall's bottom Z (typically a slab level)
    wall_nodes_at_z: dict[float, list[dict]] = defaultdict(list)
    for wid in wall_ids:
        ae = area_elements[wid]
        for nid in ae.node_ids:
            nd = nodes.get(nid)
            if nd is None:
                continue
            wall_nodes_at_z[round(nd.z, 4)].append(
                {
                    "node_id": nid,
                    "node_tag": nd.node_tag,
                    "x": nd.x,
                    "y": nd.y,
                    "z": nd.z,
                    "wall_id": wid,
                    "section": area_assignments.get(wid, ""),
                }
            )

    if not wall_nodes_at_z:
        return []

    # ── Check each slab for interior wall nodes ─────────────────
    findings: list[dict[str, Any]] = []
    for sid in slab_ids:
        ae = area_elements[sid]
        corner_nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        corner_nds = [n for n in corner_nds if n is not None]
        if len(corner_nds) < 4:
            continue

        xs = [n.x for n in corner_nds]
        ys = [n.y for n in corner_nds]
        zs = [n.z for n in corner_nds]
        slab_z = round(sum(zs) / len(zs), 4)

        # Warn if slab is rotated off axis — bounding-box containment
        # may produce false positives for non-orthogonal slabs.
        _slab_rotated = False
        for k in range(len(corner_nds)):
            n1 = corner_nds[k]
            n2 = corner_nds[(k + 1) % len(corner_nds)]
            if abs(n1.x - n2.x) > 1e-6 and abs(n1.y - n2.y) > 1e-6:
                _slab_rotated = True
                break
        if _slab_rotated and verbose:
            print(
                f"  ⚠ Slab {sid} is rotated — wall-node containment "
                f"uses axis-aligned bounding box, may have false "
                f"positives"
            )

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        margin = max((x_max - x_min), (y_max - y_min)) * 0.001

        wall_node_set = wall_nodes_at_z.get(slab_z, [])
        if not wall_node_set:
            continue

        interior: list[dict] = []
        seen_walls: set = set()
        for wn in wall_node_set:
            # Inside slab XY projection (not at corners)
            if not (x_min - margin <= wn["x"] <= x_max + margin):
                continue
            if not (y_min - margin <= wn["y"] <= y_max + margin):
                continue
            # Skip slab corner nodes
            is_corner = any(
                abs(wn["x"] - cn.x) < margin and abs(wn["y"] - cn.y) < margin for cn in corner_nds
            )
            if is_corner:
                continue
            interior.append(wn)
            seen_walls.add(wn["wall_id"])

        if not interior:
            continue

        for wid in sorted(seen_walls):
            wall_nodes = [wn for wn in interior if wn["wall_id"] == wid]
            findings.append(
                {
                    "slab_id": sid,
                    "wall_id": wid,
                    "nodes": wall_nodes,
                    "slab_X": (x_min, x_max),
                    "slab_Y": (y_min, y_max),
                    "slab_Z": float(np.mean(zs)),
                    "section": wall_nodes[0]["section"],
                }
            )

    return findings


def print_wall_inside_slab_report(
    findings: list[dict[str, Any]],
    file=None,
    verbose: bool = False,
) -> None:
    """Print a summary report of wall-in-slab findings.

    Args:
        findings: Output from :func:`find_wall_nodes_inside_slabs`.
        file: Output stream (default ``sys.stdout``).
        verbose: If True, also print individual wall–slab details
            (node coordinates, slab bounds).  Default is False.
    """
    n = len(findings)
    if n == 0:
        print("  No wall nodes found inside slab areas.", file=file)
        return
    print(f"  ⚠ {n} wall–slab intersection(s) detected", file=file)
    if verbose:
        for f in findings:
            sec = f["section"]
            coords = "; ".join(
                f"{wn['node_id']}({wn['x']:.1f},{wn['y']:.1f},{wn['z']:.2f})" for wn in f["nodes"]
            )
            print(
                f"    Wall {f['wall_id']:>4} ({sec}) inside slab {f['slab_id']:>4}\n"
                f"      Nodes: {coords}\n"
                f"      Slab bounds: X∈{f['slab_X']} Y∈{f['slab_Y']} Z={f['slab_Z']:.2f}",
                file=file,
            )


def split_slabs_at_wall_intersections(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
    z_tol: float = 0.5,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide slab areas at wall-edge intersection lines.

    Before the regular max_size-based meshing, this function detects
    wall areas whose edge nodes fall inside slab areas and splits the
    slab along those intersection lines.  The resulting sub-areas then
    mesh naturally to share nodes with the wall at the interface.

    This mirrors the pattern of :func:`split_areas_at_frame_edges` but
    for wall-slab pairs instead of frame-slab pairs.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        next_tag: Next available numeric tag for new nodes.
        groups: Optional ``{group_name: Group}`` — group memberships
            propagated to sub-elements.
        z_tol: Z-span threshold for wall vs slab classification.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided slab areas added.
    """
    from .sap_data import AreaElement as _AreaElement
    from .sap_data import Node as _Node

    # ── 1. Find intersections ───────────────────────────────────
    findings = find_wall_nodes_inside_slabs(
        area_elements,
        area_assignments,
        nodes,
        z_tol=z_tol,
    )
    if not findings:
        return area_elements, area_assignments, nodes, next_tag

    # ── 2. Group findings by slab ───────────────────────────────
    # Each group: (slab_id, [(wall_id, wall_nodes), ...])
    slab_groups: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for f in findings:
        slab_groups[f["slab_id"]].append((f["wall_id"], f["nodes"]))

    # ── 3. For each slab, collect parametric split positions ────
    for sid, wall_list in slab_groups.items():
        elem = area_elements.get(sid)
        if elem is None or getattr(elem, "inactive", False):
            continue
        if len(elem.node_ids) != 4:
            continue

        # Slab corner coordinates (CCW)
        corners_list = [nodes.get(n) for n in elem.node_ids if n in nodes]
        if len(corners_list) != 4:
            continue
        # Ensure CCW ordering matching mesh_area_elements convention
        c0 = np.array([corners_list[0].x, corners_list[0].y, corners_list[0].z])
        c1 = np.array([corners_list[1].x, corners_list[1].y, corners_list[1].z])
        c2 = np.array([corners_list[2].x, corners_list[2].y, corners_list[2].z])
        c3 = np.array([corners_list[3].x, corners_list[3].y, corners_list[3].z])
        corners_arr = [c0, c1, c2, c3]

        # Get wall interior nodes as numpy arrays
        interior_pts: list[np.ndarray] = []
        for wid, wall_nodes in wall_list:
            for wn in wall_nodes:
                interior_pts.append(np.array([wn["x"], wn["y"], wn["z"]]))

        if not interior_pts:
            continue

        # Compute parametric (u, v) for each interior point
        u_vals: set = {0.0, 1.0}
        v_vals: set = {0.0, 1.0}
        for pt in interior_pts:
            uv = _point_uv_on_quad(pt, corners_arr)
            if uv is None:
                continue
            u, v = uv
            if 1e-6 < u < 1.0 - 1e-6:
                u_vals.add(round(u, 8))
            if 1e-6 < v < 1.0 - 1e-6:
                v_vals.add(round(v, 8))

        if len(u_vals) <= 2 and len(v_vals) <= 2:
            continue  # no interior splits needed

        # Sort and deduplicate
        u_list = sorted(u_vals)
        v_list = sorted(v_vals)
        _tol = 1e-6
        u_list = [u_list[0]] + [u for u in u_list[1:] if u - u_list[u_list.index(u) - 1] > _tol]
        v_list = [v_list[0]] + [v for v in v_list[1:] if v - v_list[v_list.index(v) - 1] > _tol]

        n_u = len(u_list) - 1
        n_v = len(v_list) - 1

        # Bilinear grid at parametric positions
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        for j, v in enumerate(v_list):
            for i, u in enumerate(u_list):
                top = c0 * (1.0 - u) + c1 * u
                bot = c3 * (1.0 - u) + c2 * u
                grid[j, i] = top * (1.0 - v) + bot * v

        # Build coordinate cache
        _pos_cache: dict[str, np.ndarray] = {}
        for nid, nd in nodes.items():
            _pos_cache[nid] = np.array([nd.x, nd.y, nd.z])
        for pt in interior_pts:
            pass  # already in nodes

        # Create grid nodes (reuse existing)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        corner_ids = list(elem.node_ids)
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                if i == 0 and j == 0:
                    node_grid[j][i] = corner_ids[0]
                    continue
                if i == n_u and j == 0:
                    node_grid[j][i] = corner_ids[1]
                    continue
                if i == n_u and j == n_v:
                    node_grid[j][i] = corner_ids[2]
                    continue
                if i == 0 and j == n_v:
                    node_grid[j][i] = corner_ids[3]
                    continue
                pt = grid[j, i]
                # Reuse existing node within tolerance
                found = None
                for nid, npos in _pos_cache.items():
                    if np.linalg.norm(npos - pt) < 1e-4:
                        found = nid
                        break
                if found is not None:
                    node_grid[j][i] = found
                    continue
                new_id = f"{sid}_wi_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                nodes[new_id] = _Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                _pos_cache[new_id] = np.array([pt[0], pt[1], pt[2]])
                node_grid[j][i] = new_id

        # Mark original slab inactive
        elem.inactive = True

        # Determine parent groups
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                if f"Area:{sid}" in g.objects:
                    parent_groups.append(gname)

        # Create sub-areas (CCW ordering)
        sec_name = area_assignments.get(sid, "")
        for j in range(n_v):
            for i in range(n_u):
                sub_id = f"{sid}_wi_sub_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                area_elements[sub_id] = _AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                    thickness=elem.thickness,
                    parent_id=sid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

    return area_elements, area_assignments, nodes, next_tag


# ═══════════════════════════════════════════════════════════════════
# Floating node removal
# ═══════════════════════════════════════════════════════════════════


def remove_floating_nodes(
    md: SAPModelData,
    z_tolerance: float = 0.5,
) -> list[dict[str, Any]]:
    """Remove nodes not connected to any element, redistributing loads and mass.

    SAP2000 can define nodes that are not referenced by any frame or
    area element but still carry mass (from mass source) or loads
    (joint loads).  These cause singularities in OpenSees.

    For each floating node:
      1. Accumulate its mass and joint loads.
      2. Find the nearest connected node at the same elevation band.
      3. Add mass/loads to that node.
      4. Remove the floating node from ``md.nodes``.

    Modifies *md* in‑place (removes nodes, transfers restraints and
    joint loads to the nearest connected neighbour).

    Args:
        md: Model data to scan and clean.
        z_tolerance: Elevation tolerance for matching nodes (same units
            as model coordinates).

    Returns:
        List of dicts documenting each removed node, with keys:
        ``node_id``, ``x``, ``y``, ``z``, ``mass_source``, ``loads``,
        ``restrained``, ``nearest_node``, ``distance``.
    """
    connected: set = set()
    for fe in md.frame_elements.values():
        if not getattr(fe, "inactive", False):
            connected.add(fe.node_i)
            connected.add(fe.node_j)
    for ae in md.area_elements.values():
        if not getattr(ae, "inactive", False):
            connected.update(ae.node_ids)

    joint_loads: dict[str, list[float]] = {}
    for jl in getattr(md, "joint_loads", []):
        nid = getattr(jl, "node_id", "") or getattr(jl, "node", "")
        if not nid:
            continue
        if nid not in joint_loads:
            joint_loads[nid] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        joint_loads[nid][0] += getattr(jl, "fx", 0.0) or 0.0
        joint_loads[nid][1] += getattr(jl, "fy", 0.0) or 0.0
        joint_loads[nid][2] += getattr(jl, "fz", 0.0) or 0.0
        joint_loads[nid][3] += getattr(jl, "mx", 0.0) or 0.0
        joint_loads[nid][4] += getattr(jl, "my", 0.0) or 0.0
        joint_loads[nid][5] += getattr(jl, "mz", 0.0) or 0.0

    ms_has_masses = any(
        getattr(src, "masses", False) for src in getattr(md, "mass_sources", {}).values()
    )

    restraints = getattr(md, "restraints", {})

    floating: list[str] = [nid for nid in md.nodes if nid not in connected]
    if not floating:
        return []

    rows: list[dict[str, Any]] = []
    removed_nodes: list[str] = []

    for nid in floating:
        nd = md.nodes[nid]
        loads = joint_loads.get(nid, [0.0] * 6)
        has_loads = any(abs(v) > 1e-12 for v in loads)
        has_restraint = nid in restraints
        has_mass = ms_has_masses

        if not (has_mass or has_loads or has_restraint):
            removed_nodes.append(nid)
            continue

        best_dist = float("inf")
        best_nid = None
        for cnid in connected:
            cnd = md.nodes[cnid]
            if abs(cnd.z - nd.z) > z_tolerance:
                continue
            d = np.linalg.norm([cnd.x - nd.x, cnd.y - nd.y, cnd.z - nd.z])
            if d < best_dist:
                best_dist = d
                best_nid = cnid

        if best_nid is None:
            for cnid in connected:
                cnd = md.nodes[cnid]
                d = np.linalg.norm([cnd.x - nd.x, cnd.y - nd.y, cnd.z - nd.z])
                if d < best_dist:
                    best_dist = d
                    best_nid = cnid

        if has_loads and best_nid:
            if not hasattr(md, "joint_loads") or md.joint_loads is None:
                md.joint_loads = []
            md.joint_loads.append(
                JointLoad(
                    node=best_nid,
                    fx=loads[0],
                    fy=loads[1],
                    fz=loads[2],
                    mx=loads[3],
                    my=loads[4],
                    mz=loads[5],
                )
            )

        if has_restraint and best_nid and best_nid not in restraints:
            restraints[best_nid] = restraints.pop(nid)

        rows.append(
            {
                "node_id": nid,
                "x": nd.x,
                "y": nd.y,
                "z": nd.z,
                "mass_source": ms_has_masses,
                "loads": (
                    f"({loads[0]:.1f}, {loads[1]:.1f}, {loads[2]:.1f}, "
                    f"{loads[3]:.1f}, {loads[4]:.1f}, {loads[5]:.1f})"
                ),
                "restrained": has_restraint,
                "nearest_node": best_nid,
                "distance": best_dist,
            }
        )
        removed_nodes.append(nid)

    for nid in removed_nodes:
        md.nodes.pop(nid, None)
        restraints.pop(nid, None)

    return rows
