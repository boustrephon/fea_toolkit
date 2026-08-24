"""Geometric utilities for element orientation, splitting, and intersections.

The implementation lives in :mod:`fea_toolkit.model.geometry_core`,
:mod:`fea_toolkit.model.geometry_frames` and
:mod:`fea_toolkit.model.geometry_mesh`; this module re-exports every name
so ``from fea_toolkit.model.geometry import ...`` keeps working.
"""

from .geometry_core import (
    SpatialGrid,
    _segment_intersection_3d,
    compute_t_location,
    get_local_axes,
    get_SAP_vecxz,
    interp,
    list_interp,
    point_on_segment,
    polygon_area_3d,
    rotate_about_axis,
)
from .geometry_frames import (
    _point_uv_on_quad,
    _propagate_edge_restraints,
    apply_frame_end_offsets,
    beam_load_to_nodal_loads,
    child_length,
    convert_area_loads_to_edge_loads,
    derive_rigid_end_offsets,
    split_elements,
    split_elements_at_joints,
    split_elements_ss,
    subdivide_elements,
    trapezoidal_force_split,
)
from .geometry_mesh import (
    find_constraint_edges,
    find_wall_nodes_inside_slabs,
    mesh_area_elements,
    print_wall_inside_slab_report,
    remove_floating_nodes,
    split_areas_at_frame_edges,
    split_slabs_at_wall_intersections,
    subdivide_area_mesh,
    warn_frame_overlaps,
)

__all__ = [
    "SpatialGrid",
    "_point_uv_on_quad",
    "_propagate_edge_restraints",
    "_segment_intersection_3d",
    "apply_frame_end_offsets",
    "beam_load_to_nodal_loads",
    "child_length",
    "compute_t_location",
    "convert_area_loads_to_edge_loads",
    "derive_rigid_end_offsets",
    "find_constraint_edges",
    "find_wall_nodes_inside_slabs",
    "get_SAP_vecxz",
    "get_local_axes",
    "interp",
    "list_interp",
    "mesh_area_elements",
    "point_on_segment",
    "polygon_area_3d",
    "print_wall_inside_slab_report",
    "remove_floating_nodes",
    "rotate_about_axis",
    "split_areas_at_frame_edges",
    "split_elements",
    "split_elements_at_joints",
    "split_elements_ss",
    "split_slabs_at_wall_intersections",
    "subdivide_area_mesh",
    "subdivide_elements",
    "trapezoidal_force_split",
    "warn_frame_overlaps",
]
