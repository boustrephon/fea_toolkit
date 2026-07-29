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
checks — Connectivity diagnostics, brace buckling check, self-weight check.
csm — Capacity Spectrum Method (ADRS conversion, performance point).
confinement — Mander confined concrete model.
tree_utils — Parent-child tree traversal for split elements.
"""

from .sap_data import (
    SAPModelData,
    Node,
    Restraint,
    FrameElement,
    AreaElement,
    Section,
    ISection,
    PipeSection,
    BoxSection,
    RectangularSection,
    CircularSection,
    ConcreteRectangularSection,
    ConcreteCircularSection,
    ChannelSection,
    AngleSection,
    DoubleAngleSection,
    TeeSection,
    GeneralSection,
    ShellSection,
    SDSection,
    EncasedSection,
    FrameEndOffset,
    AreaMesh,
    AreaEdgeConstraint,
    Material,
    Group,
    LoadCase,
    LoadPattern,
    LoadCombination,
    MassSource,
    JointLoad,
    AreaUniformLoad,
    GravityLoad,
    AreaGravityLoad,
    FramePointLoad,
    FrameDistributedLoad,
    NDMaterial,
    ShellFiberLayer,
    LayeredShellSection,
    FrameElementProperties,
    AreaElementProperties,
)

from .selection import Selection

from .mesh_model import MeshModel

from .sections import SectionLibrary

from .geometry import (
    split_elements,
    trapezoidal_force_split,
    mesh_area_elements,
    split_areas_at_frame_edges,
    convert_area_loads_to_edge_loads,
    beam_load_to_nodal_loads,
    subdivide_elements,
    apply_frame_end_offsets,
    get_local_axes,
    get_SAP_vecxz,
    find_constraint_edges,
    warn_frame_overlaps,
    find_wall_nodes_inside_slabs,
    split_slabs_at_wall_intersections,
    print_wall_inside_slab_report,
    remove_floating_nodes,
    polygon_area_3d,
    SpatialGrid,
)

from .stories import (
    identify_stories,
    StoryLevel,
    stories_dataframe,
    plot_stories,
)

from .storey_response import (
    storey_displacements,
    storey_drifts,
    storey_shears,
    modal_storey_drifts,
    StoreyRigidBody,
    rigid_body_fit,
    peak_displacement,
    build_storey_table,
    compute_linear_storey_responses,
)

from .checks import (
    check_model_connectivity,
    check_brace_buckling,
    check_self_weight_consistency,
    compute_hinge_length,
    compute_asce41_hinge_length,
)

from .csm import (
    pushover_to_adrs,
    compute_performance_point,
    bilinearize_stiffness_change,
    bilinearize_equal_energy,
    bilinearize_composite,
)

from .confinement import (
    mander_confined,
    ConfinementData,
    ConfinementResult,
)

from .tree_utils import (
    collect_descendants,
    get_root_parent,
    get_element_chain,
    frame_split_summary,
)

__all__ = [
    # Core model
    "SAPModelData",
    "Node",
    "Restraint",
    "FrameElement",
    "AreaElement",
    # Section types
    "Section",
    "ISection",
    "PipeSection",
    "BoxSection",
    "RectangularSection",
    "CircularSection",
    "ConcreteRectangularSection",
    "ConcreteCircularSection",
    "ChannelSection",
    "AngleSection",
    "DoubleAngleSection",
    "TeeSection",
    "GeneralSection",
    "ShellSection",
    "SDSection",
    "EncasedSection",
    # Element properties
    "FrameEndOffset",
    "AreaMesh",
    "AreaEdgeConstraint",
    "FrameElementProperties",
    "AreaElementProperties",
    # Materials & shells
    "Material",
    "NDMaterial",
    "ShellFiberLayer",
    "LayeredShellSection",
    "Group",
    # Loads
    "LoadCase",
    "LoadPattern",
    "LoadCombination",
    "MassSource",
    "JointLoad",
    "AreaUniformLoad",
    "GravityLoad",
    "AreaGravityLoad",
    "FramePointLoad",
    "FrameDistributedLoad",
    # Selection
    "Selection",
    # Mesh model
    "MeshModel",
    # Section library
    "SectionLibrary",
    # Geometry
    "split_elements",
    "trapezoidal_force_split",
    "mesh_area_elements",
    "split_areas_at_frame_edges",
    "convert_area_loads_to_edge_loads",
    "beam_load_to_nodal_loads",
    "subdivide_elements",
    "apply_frame_end_offsets",
    "get_local_axes",
    "get_SAP_vecxz",
    "find_constraint_edges",
    "warn_frame_overlaps",
    "find_wall_nodes_inside_slabs",
    "split_slabs_at_wall_intersections",
    "print_wall_inside_slab_report",
    "remove_floating_nodes",
    "polygon_area_3d",
    "SpatialGrid",
    # Stories
    "identify_stories",
    "StoryLevel",
    "stories_dataframe",
    "plot_stories",
    # Storey response
    "storey_displacements",
    "storey_drifts",
    "storey_shears",
    "modal_storey_drifts",
    "StoreyRigidBody",
    "rigid_body_fit",
    "peak_displacement",
    "build_storey_table",
    "compute_linear_storey_responses",
    # Checks
    "check_model_connectivity",
    "check_brace_buckling",
    "check_self_weight_consistency",
    "compute_hinge_length",
    "compute_asce41_hinge_length",
    # CSM
    "pushover_to_adrs",
    "compute_performance_point",
    "bilinearize_stiffness_change",
    "bilinearize_equal_energy",
    "bilinearize_composite",
    # Confinement
    "mander_confined",
    "ConfinementData",
    "ConfinementResult",
    # Tree utils
    "collect_descendants",
    "get_root_parent",
    "get_element_chain",
    "frame_split_summary",
]