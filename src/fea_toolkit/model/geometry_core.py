"""Core vector and orientation math.

Local axes, segment/interpolation helpers, polygon area, and
:class:`SpatialGrid` — the low-level primitives shared by the frame and
area-mesh helpers.  Re-exported by :mod:`fea_toolkit.model.geometry`."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Optional, Union

import numpy as np


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
