# fea_toolkit/model/geometry.py

import math
import numpy as np
from typing import Dict, List, Tuple, Any, Optional
from collections import defaultdict

# ... (keep existing get_SAP_vecxz, rotate_about_axis, point_on_segment) ...

class SpatialGrid:
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

def split_elements_at_joints(nodes: Dict[str, Dict[str, float]],
                             elements: Dict[str, Dict[str, Any]],
                             assignments: Dict[str, Any],
                             dist_loads: Dict[str, Any],
                             auto_mesh: Dict[str, Dict[str, Any]],
                             tol: float = 1e-6,
                             verbose: bool = False) -> Tuple[Dict[str, Dict], Dict[str, Any], Dict[str, Any]]:
    """Split frame elements at nodes that lie on them, using spatial grid.
    Only splits if auto_mesh[eid].get('AtJoints') is True.
    Returns new elements, assignments, and dist_loads.
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
    # Determine next element ID (assuming string IDs may contain numbers)
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

def split_elements(nodes: Dict, elements: Dict, assignments: Dict,
                   dist_loads: Dict, auto_mesh: Dict,
                   tol: float = 1e-6, verbose: bool = False) -> Tuple[Dict, Dict, Dict]:
    """Main splitting entry point."""
    # Currently only splits at joints. Add frame intersections later.
    return split_elements_at_joints(nodes, elements, assignments, dist_loads, auto_mesh, tol, verbose)

