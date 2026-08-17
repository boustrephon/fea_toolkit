"""Data model — SAP2000 model data, geometry utilities, selection, and analysis helpers.

The model subpackage is **OpenSees-free** — no ``ops.*`` imports are allowed.

Modules
-------
sap_data — All dataclass types: SAPModelData, Node, FrameElement, Section, etc.
selection — Composable element filtering (:class:`Selection`).
mesh_model — Frozen topology after preprocessing (:class:`MeshModel`).
geometry — Element splitting, area meshing, load redistribution, local axes.
sections — Manufacturer section library (:class:`SectionLibrary`).
stories — Storey identification and summarisation (:func:`identify_stories`).
storey_response — Storey displacements, drifts, shears, modal drifts.
units — Unit-system conversion (:func:`convert_mesh_units`), kip-in enabler.
checks — Connectivity diagnostics, brace buckling check, self-weight check.
csm — Capacity Spectrum Method (ADRS conversion, performance point).
confinement — Mander confined concrete model.
tree_utils — Parent-child tree traversal for split elements.
"""

from .checks import (
    check_brace_buckling,
    check_model_connectivity,
    check_self_weight_consistency,
    compute_asce41_hinge_length,
    compute_hinge_length,
)
from .confinement import (
    ConfinementData,
    ConfinementResult,
    mander_confined,
)
from .csm import (
    bilinearize_composite,
    bilinearize_equal_energy,
    bilinearize_rc,
    bilinearize_stiffness_change,
    compute_performance_point,
    pushover_to_adrs,
)
from .geometry import (
    SpatialGrid,
    apply_frame_end_offsets,
    beam_load_to_nodal_loads,
    convert_area_loads_to_edge_loads,
    find_constraint_edges,
    find_wall_nodes_inside_slabs,
    get_local_axes,
    get_SAP_vecxz,
    mesh_area_elements,
    polygon_area_3d,
    print_wall_inside_slab_report,
    remove_floating_nodes,
    split_areas_at_frame_edges,
    split_elements,
    split_slabs_at_wall_intersections,
    subdivide_elements,
    trapezoidal_force_split,
    warn_frame_overlaps,
)
from .mesh_model import MeshModel
from .sap_data import (
    AngleSection,
    AreaEdgeConstraint,
    AreaElement,
    AreaElementProperties,
    AreaGravityLoad,
    AreaMesh,
    AreaUniformLoad,
    BoxSection,
    ChannelSection,
    CircularSection,
    ConcreteCircularSection,
    ConcreteRectangularSection,
    DoubleAngleSection,
    EncasedSection,
    FrameDistributedLoad,
    FrameElement,
    FrameElementProperties,
    FrameEndOffset,
    FramePointLoad,
    GeneralSection,
    GravityLoad,
    Group,
    ISection,
    JointLoad,
    LayeredShellSection,
    LoadCase,
    LoadCombination,
    LoadPattern,
    MassSource,
    Material,
    NDMaterial,
    Node,
    PipeSection,
    RectangularSection,
    Restraint,
    SAPModelData,
    SDSection,
    Section,
    ShellFiberLayer,
    ShellSection,
    TeeSection,
)
from .sections import SectionLibrary
from .selection import Selection
from .storey_response import (
    StoreyRigidBody,
    build_storey_table,
    compute_linear_storey_responses,
    group_shell_forces_by_section,
    modal_storey_drifts,
    peak_displacement,
    rigid_body_fit,
    storey_displacements,
    storey_drifts,
    storey_shears,
)
from .stories import (
    StoryLevel,
    identify_stories,
    plot_stories,
    stories_dataframe,
)
from .tree_utils import (
    collect_descendants,
    frame_split_summary,
    get_element_chain,
    get_root_parent,
)
from .units import (
    KIP_IN_UNITS,
    convert_mesh_units,
    unit_multipliers,
)

__all__ = [
    "KIP_IN_UNITS",
    "AngleSection",
    "AreaEdgeConstraint",
    "AreaElement",
    "AreaElementProperties",
    "AreaGravityLoad",
    "AreaMesh",
    "AreaUniformLoad",
    "BoxSection",
    "ChannelSection",
    "CircularSection",
    "ConcreteCircularSection",
    "ConcreteRectangularSection",
    "ConfinementData",
    "ConfinementResult",
    "DoubleAngleSection",
    "EncasedSection",
    "FrameDistributedLoad",
    "FrameElement",
    "FrameElementProperties",
    # Element properties
    "FrameEndOffset",
    "FramePointLoad",
    "GeneralSection",
    "GravityLoad",
    "Group",
    "ISection",
    "JointLoad",
    "LayeredShellSection",
    # Loads
    "LoadCase",
    "LoadCombination",
    "LoadPattern",
    "MassSource",
    # Materials & shells
    "Material",
    # Mesh model
    "MeshModel",
    "NDMaterial",
    "Node",
    "PipeSection",
    "RectangularSection",
    "Restraint",
    # Core model
    "SAPModelData",
    "SDSection",
    # Section types
    "Section",
    # Section library
    "SectionLibrary",
    # Selection
    "Selection",
    "ShellFiberLayer",
    "ShellSection",
    "SpatialGrid",
    "StoreyRigidBody",
    "StoryLevel",
    "TeeSection",
    "apply_frame_end_offsets",
    "beam_load_to_nodal_loads",
    "bilinearize_composite",
    "bilinearize_equal_energy",
    "bilinearize_rc",
    "bilinearize_stiffness_change",
    "build_storey_table",
    "check_brace_buckling",
    # Checks
    "check_model_connectivity",
    "check_self_weight_consistency",
    # Tree utils
    "collect_descendants",
    "compute_asce41_hinge_length",
    "compute_hinge_length",
    "compute_linear_storey_responses",
    "compute_performance_point",
    "convert_area_loads_to_edge_loads",
    "convert_mesh_units",
    "find_constraint_edges",
    "find_wall_nodes_inside_slabs",
    "frame_split_summary",
    "get_SAP_vecxz",
    "get_element_chain",
    "get_local_axes",
    "get_root_parent",
    "group_shell_forces_by_section",
    # Stories
    "identify_stories",
    # Confinement
    "mander_confined",
    "mesh_area_elements",
    "modal_storey_drifts",
    "peak_displacement",
    "plot_stories",
    "polygon_area_3d",
    "print_wall_inside_slab_report",
    # CSM
    "pushover_to_adrs",
    "remove_floating_nodes",
    "rigid_body_fit",
    "split_areas_at_frame_edges",
    # Geometry
    "split_elements",
    "split_slabs_at_wall_intersections",
    # Storey response
    "storey_displacements",
    "storey_drifts",
    "storey_shears",
    "stories_dataframe",
    "subdivide_elements",
    "trapezoidal_force_split",
    # Units
    "unit_multipliers",
    "warn_frame_overlaps",
]
