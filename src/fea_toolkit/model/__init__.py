"""Data model — SAP2000 model data, geometry utilities, selection, and analysis helpers.

The model subpackage is **OpenSees-free** — no ``ops.*`` imports are allowed.

Modules
-------
sap_data — All dataclass types: SAPModelData, Node, FrameElement, Section, etc.
selection — Composable element filtering (:class:`Selection`).
mesh_model — Frozen topology after preprocessing (:class:`MeshModel`).
geometry — Re-export facade for the geometry helpers:
    geometry_core — Vector/orientation math (local axes, interpolation,
        :class:`SpatialGrid`, polygon area).
    geometry_frames — Frame-element splitting, load redistribution, rigid
        end offsets.
    geometry_mesh — Area meshing, overlap/constraint-edge detection,
        wall/slab intersection.
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
    brace_buckling_check,
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
    BILINEARIZE_METHODS,
    bilinearize_composite,
    bilinearize_equal_energy,
    bilinearize_rc,
    bilinearize_stiffness_change,
    compute_performance_point,
    get_bilinearize_method,
    pushover_to_adrs,
    register_bilinearize_method,
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
from .source_resolver import ResolvedSource, resolve_model_source
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

# ── Lazy re-exports (PEP 562) ─────────────────────────────────────
# ``storey_response`` imports pandas, which is NOT a required dependency
# (see ``pyproject.toml`` core deps) and is absent from Rhino 8's bundled
# CPython.  ``import fea_toolkit`` must work without pandas, so the
# storey-response names are resolved lazily here instead of eagerly.
# ``from fea_toolkit.model import storey_displacements`` and
# ``fea_toolkit.model.storey_displacements`` work exactly as before.
_STOREY_RESPONSE_NAMES = frozenset(
    {
        "StoreyRigidBody",
        "build_storey_table",
        "compute_linear_storey_responses",
        "group_shell_forces_by_section",
        "modal_storey_drifts",
        "peak_displacement",
        "rigid_body_fit",
        "storey_displacements",
        "storey_drifts",
        "storey_shears",
    }
)


def __getattr__(name: str):
    """PEP 562 lazy resolution for the pandas-dependent storey-response API."""
    if name in _STOREY_RESPONSE_NAMES:
        from . import storey_response

        return getattr(storey_response, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_STOREY_RESPONSE_NAMES))


__all__ = [
    "BILINEARIZE_METHODS",
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
    # Source resolution
    "ResolvedSource",
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
    "brace_buckling_check",
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
    "get_bilinearize_method",
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
    "register_bilinearize_method",
    "remove_floating_nodes",
    # Source resolution
    "resolve_model_source",
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
