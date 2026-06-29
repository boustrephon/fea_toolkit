# fea_toolkit/model/geometry.py

"""Geometric utilities for element orientation, splitting, and intersections."""

import math
import numpy as np
from typing import Sequence, Tuple, Dict, List, Any, Union, Optional
from collections import defaultdict

# from ..model.sap_data import FrameElement, FrameDistributedLoad
from ..model.sap_data import (
    SAPModelData, Node, Restraint, Material, Section,
    FrameElement, AreaElement, Group, LoadPattern, JointLoad,
    FrameDistributedLoad, AreaUniformLoad,
    FrameEndOffset, AreaMesh,
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


# ============================================================================
# Nodal load conversion (for geomTransf types that don't support eleLoad)
# ============================================================================

def beam_load_to_nodal_loads(
    load: FrameDistributedLoad,
    elem: FrameElement,
    node_coords: Dict[str, Tuple[float, float, float]],
    length: float,
) -> Dict[str, Dict[str, float]]:
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
    angle_rad = math.radians(elem.angle)

    # Build local axes
    vec_x_norm = vec_x
    vecxz = get_SAP_vecxz(vec_x_norm, elem.angle)
    vec_z = vecxz / np.linalg.norm(vecxz)
    vec_y = np.cross(vec_z, vec_x_norm)
    vec_y = vec_y / np.linalg.norm(vec_y)

    # Determine global direction of the load (SAP2000 convention)
    if load.direction == 'Gravity':
        global_dir = np.array([0.0, 0.0, -1.0])
    elif load.direction == 'X':
        global_dir = np.array([1.0, 0.0, 0.0])
    elif load.direction == 'Y':
        global_dir = np.array([0.0, 1.0, 0.0])
    elif load.direction == 'Z':
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
    span = bL - aL
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

    def fixed_end_forces(w_start: float, w_end: float,
                         a_frac: float, b_frac: float, L_total: float
                         ) -> Tuple[float, float, float, float]:
        """Return (V_i, V_j, M_i, M_j) for one load component.

        Decomposes a trapezoid into a uniform part (``w_min``) plus a
        triangular part (0 → ``w_tri``) and computes fixed-end forces
        using standard beam formulae.
        """
        s = b_frac - a_frac
        if s < 1e-12 or (abs(w_start) < 1e-12 and abs(w_end) < 1e-12):
            return (0.0, 0.0, 0.0, 0.0)

        sL = s * L_total          # loaded length
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
            F_tri = 0.5 * w_tri * sL   # total triangular force
            # Centroid of triangle from node i: (a_frac + 2*s/3) * L
            c_tri = (a_frac + 2.0 * s / 3.0)
            V_i_tri = F_tri * (1.0 - c_tri)
            V_j_tri = F_tri * c_tri
            # Fixed-end moment for triangular load on [0, sL]:
            #   M_i = w_tri * sL^2 / 30
            #   M_j = -w_tri * sL^2 / 20
            M_i_tri = w_tri * sL * sL / 30.0
            M_j_tri = -w_tri * sL * sL / 20.0
        else:
            V_i_tri = V_j_tri = M_i_tri = M_j_tri = 0.0

        return (V_i_uni + V_i_tri,
                V_j_uni + V_j_tri,
                M_i_uni + M_i_tri,
                M_j_uni + M_j_tri)

    # Compute local fixed-end forces for each direction.
    # wy (local y) → shear in y, moment about local z.
    # wz (local z) → shear in z, moment about local y.
    # wx (axial)   → axial force, no moment.
    Viy, Vjy, Miz, Mjz = fixed_end_forces(wy_a, wy_b, aL, bL, L)
    Viz, Vjz, Miy, Mjy = fixed_end_forces(wz_a, wz_b, aL, bL, L)
    Vix, Vjx, _, _     = fixed_end_forces(wx_a, wx_b, aL, bL, L)

    # Transform local forces back to global coordinates
    T = np.column_stack([vec_x, vec_y, vec_z])  # local-to-global transform

    f_i_local = np.array([Vix, Viy, Viz])
    m_i_local = np.array([0.0, Miy, Miz])   # wx (axial) → no moment
    f_j_local = np.array([Vjx, Vjy, Vjz])
    m_j_local = np.array([0.0, Mjy, Mjz])

    f_i_global = T @ f_i_local
    m_i_global = T @ m_i_local
    f_j_global = T @ f_j_local
    m_j_global = T @ m_j_local

    return {
        "i": {"fx": f_i_global[0], "fy": f_i_global[1], "fz": f_i_global[2],
              "mx": m_i_global[0], "my": m_i_global[1], "mz": m_i_global[2]},
        "j": {"fx": f_j_global[0], "fy": f_j_global[1], "fz": f_j_global[2],
              "mx": m_j_global[0], "my": m_j_global[1], "mz": m_j_global[2]},
    }


# ============================================================================
# Area load → frame edge load conversion
# ============================================================================

def convert_area_loads_to_edge_loads(
    nodes: Dict[str, 'Node'],
    area_elements: Dict[str, AreaElement],
    frame_elements: Dict[str, FrameElement],
    area_loads: List[AreaUniformLoad],
) -> List[FrameDistributedLoad]:
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
    from collections import defaultdict

    # Build lookup: pair of node IDs → frame element ID
    edge_map = {}  # (node_i, node_j) sorted → frame_id
    for eid, elem in frame_elements.items():
        if getattr(elem, 'inactive', False):
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

        # Compute area (shoelace formula)
        area_val = 0.0
        for k in range(len(nids)):
            i1, i2 = k, (k + 1) % len(nids)
            cross = np.cross(pts[i1], pts[i2])
            area_val += cross[2]
        area_val = abs(area_val) * 0.5

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
            if al.direction == 'Gravity':
                direction = 'Z'
            else:
                direction = al.direction

            # Create the edge load — uniform over the full span
            result_loads.append(FrameDistributedLoad(
                pattern=al.pattern,
                frame_id=frame_id,
                direction=direction,
                load_type='Force',
                shape='Uniform',
                val_a=w,
                val_b=w,
                rdist_a=0.0,
                rdist_b=1.0,
                dist_a=0.0,
                dist_b=edge_len,
                coord_sys=al.coord_sys,
            ))

    return result_loads


# ============================================================================
# Brace subdivision with initial imperfection (Approach A)
# ============================================================================

def subdivide_elements(
    elements: Dict[str, FrameElement],
    assignments: Dict[str, str],
    nodes: Dict[str, 'Node'],
    n_segments: int = 4,
    imperfection_ratio: float = 1.0 / 500.0,
    brace_ids: Optional[set] = None,
    end_offset: float = 0.0,
    next_tag: int = 1,
) -> Tuple[Dict[str, FrameElement], Dict[str, str], Dict[str, 'Node'], int, List[tuple]]:
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

    rigid_links: List[tuple] = []

    for eid in list(brace_ids):
        elem = elements.get(eid)
        # Skip already-inactive elements — prevents double subdivision when
        # the model is rebuilt (e.g., run_static_analysis with pattern_scales).
        if elem is None or getattr(elem, 'inactive', False):
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
                node_id=offset_i_id, node_tag=offset_i_tag,
                x=float(p_start[0]), y=float(p_start[1]), z=float(p_start[2]),
            )

            offset_j_id = f"{eid}_offset_j"
            offset_j_tag = next_tag
            next_tag += 1
            nodes[offset_j_id] = Node(
                node_id=offset_j_id, node_tag=offset_j_tag,
                x=float(p_end[0]), y=float(p_end[1]), z=float(p_end[2]),
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
                    node_id=new_node_id, node_tag=new_tag,
                    x=float(end_pt[0]), y=float(end_pt[1]), z=float(end_pt[2]),
                )
                j_node_id = new_node_id
            else:
                j_node_id = brace_end_id

            sub_elem_id = f"{eid}_sub_{seg}"
            sub_tag = next_tag
            next_tag += 1

            elements[sub_elem_id] = FrameElement(
                elem_id=sub_elem_id, elem_tag=sub_tag,
                node_i=prev_node_id, node_j=j_node_id, angle=elem.angle,
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
    elements: Dict[str, FrameElement],
    assignments: Dict[str, str],
    nodes: Dict[str, 'Node'],
    offsets: Dict[str, FrameEndOffset],
    next_tag: int = 1,
) -> Tuple[Dict[str, FrameElement], Dict[str, str], Dict[str, 'Node'], int, List[tuple]]:
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
    rigid_links: List[tuple] = []

    for eid, off in offsets.items():
        if off.end_i == 0.0 and off.end_j == 0.0:
            continue

        elem = elements.get(eid)
        if elem is None or getattr(elem, 'inactive', False):
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

        # I‑end offset node
        offset_i_id = f"{eid}_off_i"
        offset_i_tag = next_tag
        next_tag += 1
        p_start = p_i + u * d_i
        nodes[offset_i_id] = Node(
            node_id=offset_i_id, node_tag=offset_i_tag,
            x=float(p_start[0]), y=float(p_start[1]), z=float(p_start[2]),
        )

        # J‑end offset node
        offset_j_id = f"{eid}_off_j"
        offset_j_tag = next_tag
        next_tag += 1
        p_end = p_j - u * d_j
        nodes[offset_j_id] = Node(
            node_id=offset_j_id, node_tag=offset_j_tag,
            x=float(p_end[0]), y=float(p_end[1]), z=float(p_end[2]),
        )

        # Rigid link at I‑end (original node → offset node)
        if d_i > 0:
            rigid_i_id = f"{eid}_rigid_i"
            rigid_i_tag = next_tag
            next_tag += 1
            rigid_links.append((rigid_i_id, elem.node_i, offset_i_id, rigid_i_tag))

        # Rigid link at J‑end (offset node → original node)
        if d_j > 0:
            rigid_j_id = f"{eid}_rigid_j"
            rigid_j_tag = next_tag
            next_tag += 1
            rigid_links.append((rigid_j_id, offset_j_id, elem.node_j, rigid_j_tag))

        # Shorten the original element to the offset length
        elem.node_i = offset_i_id
        elem.node_j = offset_j_id

    return elements, assignments, nodes, next_tag, rigid_links


# ============================================================================
# Area meshing — subdivide area quads into a grid of smaller shell elements
# ============================================================================

def mesh_area_elements(
    area_elements: Dict[str, AreaElement],
    area_assignments: Dict[str, str],
    nodes: Dict[str, 'Node'],
    area_mesh: Dict[str, AreaMesh],
    next_tag: int = 1,
) -> Tuple[Dict[str, AreaElement], Dict[str, str], Dict[str, 'Node'], int]:
    """Subdivide area elements into a grid of smaller shell elements.

    Only areas with ``auto_mesh=True`` and a positive ``max_size`` are
    subdivided.  The subdivision count along each edge is calculated from
    ``max_size`` so that no sub-element exceeds that dimension.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        area_mesh: ``{area_id: AreaMesh}`` from parsed s2k data.
        next_tag: Next available numeric tag for new nodes and elements.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided areas added and original areas marked inactive.
    """
    for aid, mesh in area_mesh.items():
        if not mesh.auto_mesh or mesh.max_size <= 0.0:
            continue

        elem = area_elements.get(aid)
        if elem is None or len(elem.node_ids) != 4:
            continue  # only quad areas are meshed

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

        # Determine subdivision counts from max_size
        def _edge_length(a, b):
            return float(np.linalg.norm(b - a))

        l01 = _edge_length(corners[0], corners[1])
        l12 = _edge_length(corners[1], corners[2])
        l23 = _edge_length(corners[2], corners[3])
        l30 = _edge_length(corners[3], corners[0])

        # Average length along each parametric direction
        len_u = (l01 + l23) / 2.0   # I→J direction (edge 0-1, 2-3)
        len_v = (l12 + l30) / 2.0   # orthogonal direction (edge 1-2, 3-0)

        n_u = max(1, round(len_u / mesh.max_size))
        n_v = max(1, round(len_v / mesh.max_size))

        if n_u == 1 and n_v == 1:
            continue  # no subdivision needed

        # Bilinear interpolation to create grid points
        # Parametric coords (0..1) x (0..1) mapped to the quad
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        for j in range(n_v + 1):
            v = j / n_v
            for i in range(n_u + 1):
                u = i / n_u
                # Bilinear: blend corners
                top = corners[0] * (1 - u) + corners[1] * u
                bot = corners[3] * (1 - u) + corners[2] * u
                grid[j, i] = top * (1 - v) + bot * v

        # Create new nodes for interior grid points (skip corners)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                if (i == 0 and j == 0) or (i == n_u and j == 0) \
                   or (i == n_u and j == n_v) or (i == 0 and j == n_v):
                    # Corner — use original node ID
                    idx = (j * (n_u + 1) + i)
                    orig_corners = [0, 1, 3, 2]  # reorder to match grid
                    node_grid[j][i] = corner_ids[orig_corners[j * 2 + (i // max(1, n_u))]]
                    continue
                new_id = f"{aid}_mesh_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                pt = grid[j, i]
                nodes[new_id] = Node(
                    node_id=new_id, node_tag=new_tag,
                    x=float(pt[0]), y=float(pt[1]), z=float(pt[2]),
                )
                node_grid[j][i] = new_id

        # Mark original area as inactive
        elem.inactive = True

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
                    area_id=sub_id, area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                )
                if sec_name:
                    area_assignments[sub_id] = sec_name

    return area_elements, area_assignments, nodes, next_tag

