# fea_toolkit/model/geometry.py

"""Geometric utilities for element orientation, splitting, and intersections."""

import math
import numpy as np
from typing import Sequence, Tuple, Dict, List, Any, Union, Optional
from collections import defaultdict

# from ..model.sap_data import FrameElement, FrameDistributedLoad
from ..model.sap_data import (
    SAPModelData, Node, Restraint, Material, Section,
    FrameElement, AreaElement, Group, LoadPattern, JointLoad, FrameDistributedLoad
)

# ============================================================================
# Vector and orientation functions (from SAP2OPS_v4.py)
# ============================================================================

def get_SAP_vecxz(vec_x: Union[Sequence[float], np.ndarray],
                  angle: float = 0.0) -> np.ndarray:
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
    v_rot = (v * math.cos(theta_rad) +
             np.cross(k, v) * math.sin(theta_rad) +
             k * np.dot(k, v) * (1 - math.cos(theta_rad)))
    return v_rot / np.linalg.norm(v_rot)


def point_on_segment(p: Union[Sequence[float], np.ndarray],
                     a: Union[Sequence[float], np.ndarray],
                     b: Union[Sequence[float], np.ndarray],
                     tol: float = 1e-6) -> bool:
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
    if np.dot(ap, ab) < -tol or np.dot(bp, -ab) < -tol:
        return False
    return True

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

import numpy as np
import openseespy.opensees as ops

def global_to_local_distributed_load(ele_tag, global_force_vector):
    """
    Transforms a global distributed load vector into OpenSees local coordinates
    and applies it to a 3D beam element.
    
    global_force_vector: list/array [Wx, Wy, Wz] (Force per unit true length)

    Apply to OpenSees 3D beam (Format: wy, wz, wx)
        ops.eleLoad('-ele', ele_tag, '-type', '-beamUniform', wy, wz, wx)
    """
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
        local_y = np.array(ops.eleResponse(ele_tag, 'yaxis'))
        local_z = np.array(ops.eleResponse(ele_tag, 'zaxis'))
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
    
    return wx, wy,wz

def interp(x:float, x1: float, x2: float, y1: Optional[float], y2: Optional[float]) -> Optional[float]:
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


def list_interp(val: float, list_1: list[float], list_2: list[float], 
                extend:bool = False, extrapolate:bool=False)-> float|None:
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
        index_list = [i for i, (x1, x2) 
                      in enumerate(zip(list_1[:-1], list_1[1:])) 
                      if val >= x1 and val <= x2]
        if len(index_list) == 1:
            j = index_list[0]
            vals = (list_1[j], list_1[j+1], 
                    list_2[j], list_2[j+1])
            if any([v is None for v in vals]):
                return None
            else:
                return interp(val, *vals)
        else:
            return None



# ============================================================================
# Spatial grid for efficient nearest‑neighbour search
# ============================================================================

class SpatialGrid:
    """Simple 3D grid for spatial indexing of points and line segments."""
    def __init__(self, cell_size: float = 1.0):
        self.cell_size = cell_size
        self.grid: Dict[Tuple[int, int, int], List[Tuple[Any, Tuple[float, float, float]]]] = defaultdict(list)

    def _cell(self, x: float, y: float, z: float) -> Tuple[int, int, int]:
        return (int(math.floor(x / self.cell_size)),
                int(math.floor(y / self.cell_size)),
                int(math.floor(z / self.cell_size)))

    def add_point(self, point_id: Any, coords: Tuple[float, float, float]) -> None:
        self.grid[self._cell(*coords)].append((point_id, coords))

    def points_in_bbox(self, mins: Tuple[float, float, float],
                       maxs: Tuple[float, float, float]) -> List[Tuple[Any, Tuple[float, float, float]]]:
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

def split_elements_at_joints(nodes: Dict[str, Dict[str, float]],
                             elements: Dict[str, Dict[str, Any]],
                             assignments: Dict[str, Any],
                             dist_loads: Dict[str, Any],
                             auto_mesh: Dict[str, Dict[str, Any]],
                             frame_dist_loads: Dict[str, Any],
                             tol: float = 1e-6,
                             verbose: bool = False) -> Tuple[Dict[str, Dict], Dict[str, Any], Dict[str, Any]]:
    """Split frame elements at nodes that lie on them, using spatial grid.
    Only splits if auto_mesh[eid].get('AtJoints') is True.
    Returns new elements, assignments, and dist_loads. (??)
    """
    if not elements:
        return elements, assignments, dist_loads

    # Build spatial grid of all nodes
    node_coords = {nid: (nd['x'], nd['y'], nd['z']) for nid, nd in nodes.items()}
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
    existing_ids = [int(e.get('id', 0)) for e in elements.values() if str(e.get('id', '0')).isdigit()]
    next_id = max(existing_ids) + 1 if existing_ids else 1

    for eid, el in elements.items():
        mesh_flag = auto_mesh.get(eid, {}).get('AtJoints', False)
        if not mesh_flag:
            # Keep as is
            new_elements[eid] = el
            if eid in assignments:
                new_assignments[eid] = assignments[eid]
            if eid in dist_loads:
                new_dist_loads[eid] = dist_loads[eid]
            continue

        a = np.array(node_coords[el['i']])
        b = np.array(node_coords[el['j']])
        # Bounding box enlarged by tol
        mins = np.minimum(a, b) - tol
        maxs = np.maximum(a, b) + tol
        candidates = grid.points_in_bbox(tuple(mins), tuple(maxs))

        intermediate = []
        for nid, coord in candidates:
            if nid == el['i'] or nid == el['j']:
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
        def dist_from_a(item):
            coord = item[1]
            return math.hypot(coord[0]-a[0], coord[1]-a[1], coord[2]-a[2])
        intermediate.sort(key=dist_from_a)
        ordered_nodes = [el['i']] + [nid for nid, _ in intermediate] + [el['j']]

        for k in range(len(ordered_nodes) - 1):
            new_eid = f"{eid}-{k}"
            new_el_id = next_id
            next_id += 1
            new_el = el.copy()
            new_el['id'] = new_el_id
            new_el['i'] = ordered_nodes[k]
            new_el['j'] = ordered_nodes[k+1]
            new_elements[new_eid] = new_el
            # Propagate assignments and loads
            if eid in assignments:
                new_assignments[new_eid] = assignments[eid]
            if eid in dist_loads:
                new_dist_loads[new_eid] = dist_loads[eid]

    if verbose:
        print(f"split_elements_at_joints: {len(elements)} → {len(new_elements)} elements")
    return new_elements, new_assignments, new_dist_loads


def trapezoidal_force_split(f_data: Tuple[Tuple[float, float], Tuple[float, float]],
                            t_values: List[float]) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
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
    t_values = sorted(set([0] + t_values + [1.0]))
    f_values = [list_interp(t, tt, ff) for t in t_values]
    # print(f_values, '**')
    data_list  = []
    for t1, t2, f1, f2 in zip(t_values[:-1], t_values[1:], f_values[:-1], f_values[1:]):
        # print(t1, t2, f1, f2)
        if f1 == 0 and f2 != 0:   # left transition
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

def split_elements_ss(nodes: Dict[str, Dict[str, float]],
                   elements: Dict[str, Dict[str, Any]],
                   assignments: Dict[str, Any],
                   dist_loads: Dict[str, Any],
                   auto_mesh: Dict[str, Dict[str, Any]],
                   frame_dist_loads: Dict[str, Any],
                   tol: float = 1e-6,
                   verbose: bool = False) -> Tuple[Dict[str, Dict], Dict[str, Any], Dict[str, Any]]:
    """Main entry point for element splitting (currently only at joints)."""
    return split_elements_at_joints(nodes, elements, assignments, dist_loads,
                        auto_mesh, frame_dist_loads, tol, verbose)

def split_elements(
        nodes: Dict[str, 'Node'],
        elements: Dict[str, FrameElement],
        assignments: Dict[str, str],
        dist_loads: List[FrameDistributedLoad],
        auto_mesh: Dict[str, Dict[str, Any]],
        tol: float = 1e-6,
        verbose: bool = False
        )-> Tuple[Dict[str, FrameElement], Dict[str, str], List[FrameDistributedLoad]]:
    """Split elements at joints (if AtJoints=True) and redistribute distributed loads.
    Returns updated elements (with parent-child tracking) and updated distributed loads.

    nodes = {nid: {'tag': node.node_tag, 'x': node.x, 'y': node.y, 'z': node.z}
                        for nid, node in self.model.nodes.items()}
    elements = {
        fid: {'tag': fe.elem_tag, 'i': fe.node_i, 'j': fe.node_j, 'angle': fe.angle} 
                for fid, fe in self.model.frame_elements.items()}:
    Args:
        nodes (_type_): _description_
        elements (_type_): _description_
        assignments (_type_): _description_
        dist_loads (_type_): _description_
        auto_mesh (_type_): _description_
        tol (_type_): _description_
        verbose (_type_): _description_

    Returns:
        _type_: _description_
    """
    # Build node coords dict


    node_coords = {nid: (node.x, node.y, node.z) for nid, node in nodes.items()}
    # Create spatial grid (not shown for brevity, use previous implementation)
    grid = SpatialGrid()
    for nid, coord in node_coords.items():
        grid.add_point(nid, coord)

    new_elements = {}
    new_assignments = {}
    new_dist_loads = []   # will hold new loads for child elements
    next_tag = max((elem.elem_tag for elem in elements.values()), default=0) + 1

    for eid, el in elements.items():
        # Check if auto-mesh and AtJoints is True
        mesh_flag = auto_mesh.get(eid, {}).get('AtJoints', False)
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
        for nid, coord in candidates:
            if nid == el.node_i or nid == el.node_j:
                continue
            if point_on_segment(coord, a, b, tol):
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

        # Sort intermediate by t
        intermediate.sort(key=lambda x: x[1])
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
        for k in range(len(node_list)-1):
            child_id = f"{eid}-{k}"
            child_tag = next_tag
            next_tag += 1
            child = FrameElement(
                elem_id=child_id,
                elem_tag=child_tag,
                node_i=node_list[k],
                node_j=node_list[k+1],
                angle=el.angle,
                parent_id=eid,
                inactive=False
            )
            new_elements[child_id] = child
            new_assignments[child_id] = assignments.get(eid, None)
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

                child_len = float(np.linalg.norm(
                    np.array(node_coords[child.node_j]) -
                    np.array(node_coords[child.node_i])
                ))
                child_dist_a = t_start_local * child_len
                child_dist_b = t_end_local * child_len
                shape = 'Uniform' if abs(f_start - f_end) < 1e-6 else 'Linear'

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
                    dist_b=child_dist_b
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

