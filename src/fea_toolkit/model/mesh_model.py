"""Frozen, serialisable topology after all model preparation.

``MeshModel`` is the output of the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`
and the input to the :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`.
It contains the fully prepared topology — split frames, meshed shells,
subdivided areas, detected constraints — with no OpenSees domain objects.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .sap_data import (
    AreaElement,
    AreaElementProperties,
    AreaGravityLoad,
    AreaUniformLoad,
    FrameDistributedLoad,
    FrameElement,
    FrameElementProperties,
    GravityLoad,
    Group,
    JointLoad,
    LayeredShellSection,
    LoadPattern,
    MassSource,
    Material,
    NDMaterial,
    Node,
    Restraint,
    Section,
)


@dataclass
class WallElement:
    """Prepared SFI_MVLEM_3D macro-element for a wall area.

    An SFI_MVLEM_3D element discretises an RC wall into ``m`` macro-
    fibers stacked along the wall width, each with its own FSAM nD
    material, thickness, and width.

    The four corner nodes use OpenSees ordering: i→j→k→l defines the
    quadrilateral with i→j along the bottom edge (L→R) and k→l along
    the top edge (L→R).

    Args:
        elem_id: Human-readable identifier (e.g., "W1").
        elem_tag: Integer OpenSees element tag.
        node_ids: Four node IDs in [i, j, k, l] order (bottom-left,
            bottom-right, top-left, top-right).
        m: Number of macro-fibers.
        thick: Per-fiber thickness values (length ``m``).
        width: Per-fiber width values (length ``m``, must sum to
            total wall width ``W``).
        fsam_material_names: Per-fiber FSAM nD material names (length ``m``).
        CoR: Centre of rotation parameter (0..1, default 0.4).
        ThickMod: Thickness modification factor (optional).
        Poisson: Poisson's ratio override (optional).
        Density: Density override (optional).
    """

    elem_id: str
    elem_tag: int
    node_ids: list[str]  # [i, j, k, l]
    m: int
    thick: list[float]  # length m
    width: list[float]  # length m
    fsam_material_names: list[str]  # FSAM nD material names, length m
    CoR: float = 0.4
    ThickMod: Optional[float] = None
    Poisson: Optional[float] = None
    Density: Optional[float] = None


@dataclass
class MeshModel:
    """Prepared model topology — the bridge between preprocessor and analysis.

    All geometry mutations (splitting, meshing, subdividing, constraint
    detection) have been applied.  No OpenSees domain objects exist.
    """

    # ── Nodes ────────────────────────────────────────────────────
    nodes: dict[str, Node]  # original · mesh · split

    # ── Frame elements (split + offset children) ──────────────────
    frame_elements: dict[str, FrameElement]
    frame_assignments: dict[str, str]

    # ── Area elements (meshed + subdivided) ───────────────────────
    area_elements: dict[str, AreaElement]
    area_assignments: dict[str, str]

    # ── Loads (redistributed to split children) ───────────────────
    frame_dist_loads: list[FrameDistributedLoad]
    edge_loads_from_areas: list = field(default_factory=list)
    # ── Wall elements (SFI_MVLEM_3D macro-elements) ───────────────
    # Populated by the Preprocessor from wall-classified areas when
    # ``element_strategies.wall.element_type == "SFI_MVLEM_3D"``.
    wall_elements: dict[str, WallElement] = field(default_factory=dict)
    #   elem_id → WallElement
    # (list of tuples — exact format matches builder's edge_loads_from_areas)
    joint_loads: list[JointLoad] = field(default_factory=list)
    frame_gravity_loads: list[GravityLoad] = field(default_factory=list)
    area_gravity_loads: list[AreaGravityLoad] = field(default_factory=list)
    area_uniform_loads: list[AreaUniformLoad] = field(default_factory=list)
    load_patterns: dict[str, "LoadPattern"] = field(default_factory=dict)
    mass_sources: dict[str, MassSource] = field(default_factory=dict)

    # ── Constraints (detected, not yet applied to OpenSees) ───────
    # ── Detected coarse‑fine node pairs (for visualisation only) ──
    # Populated by the Preprocessor's ``detect_constraint_edges`` option.
    # Each entry is a ``(coarse_node, fine_node)`` tuple — rendered in
    # PyVista as yellow lines.  NOT used for applying constraints at
    # analysis time — the AnalysisBuilder receives constraint arguments
    # directly from the caller and saves them in ``_saved_edge_constraints``.
    detected_edge_pairs: list[tuple] = field(default_factory=list)
    #   [(merged_nodes, master_chain, slave_nodes, type_a, type_b), ...]
    #   — from find_constraint_edges: merged node dict, master chain,
    #     slave nodes, and the two constraint type labels
    diaphragm_levels: list[float] = field(default_factory=list)

    # ── Rigid links from frame end offsets ────────────────────────
    offset_rigid_links: list[tuple] = field(default_factory=list)

    # ── Metadata for element creation ─────────────────────────────
    frame_element_types: dict[str, str] = field(default_factory=dict)
    #   elem_id → "beam" | "column" | "brace" | "wall" | "slab" | "unknown"
    area_element_types: dict[str, str] = field(default_factory=dict)

    # ── Materials, sections, groups, restraints ───────────────────
    materials: dict[str, Material] = field(default_factory=dict)
    sections: dict[str, Section] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    restraints: dict[str, Restraint] = field(default_factory=dict)
    base_z: Optional[float] = None

    # ── Tag maps (pre-computed for deterministic Ops recreation) ──
    frame_tag_map: dict[str, int] = field(default_factory=dict)
    #   elem_id → OpenSees element tag
    material_tags: dict[str, int] = field(default_factory=dict)
    #   material name → OpenSees material tag
    section_tags: dict[str, int] = field(default_factory=dict)
    #   section name → OpenSees section tag
    shell_sec_tags: dict[str, int] = field(default_factory=dict)
    shell_sec_variants: dict[str, int] = field(default_factory=dict)

    # ── Units ─────────────────────────────────────────────────────
    units: dict[str, str] = field(default_factory=lambda: {"F": "N", "L": "m", "T": "C"})

    # ── Model identification ─────────────────────────────────────
    model_name: str = ""

    # ── Saved constraint‑application arguments (for pushover re‑apply) ──
    # Each entry is a tuple of positional args for
    # ``AnalysisBuilder.apply_edge_constraints()``.  Currently always
    # empty — the Preprocessor stores detected node pairs in
    # ``detected_edge_pairs``, not here.  The AnalysisBuilder populates
    # its own ``_saved_edge_constraints`` when constraints are first
    # applied.  This field exists for future Preprocessor→AnalysisBuilder
    # transfer of pre-built constraint arguments.
    edge_constraint_args: list[tuple] = field(default_factory=list)

    # ── Resolved element creation properties ─────────────────────
    # Populated by the Preprocessor from config (three-level resolution:
    # per-ID override → selection group → role default).
    frame_element_properties: dict[str, FrameElementProperties] = field(default_factory=dict)
    #   elem_id → FrameElementProperties
    area_element_properties: dict[str, AreaElementProperties] = field(default_factory=dict)
    #   area_id → AreaElementProperties

    # ── nD materials for layered shell sections ──────────────────
    nd_materials: dict[str, NDMaterial] = field(default_factory=dict)
    #   material name → NDMaterial (resolved from config nd_materials dict)
    layered_shell_sections: dict[str, LayeredShellSection] = field(default_factory=dict)
    #   section name → LayeredShellSection (resolved from config shell_layers)

    # ── Diaphragm components (for rigidDiaphragm constraints) ────
    # Each entry is a (z_level, [node_id, ...]) tuple representing
    # one connected diaphragm component at a given storey.
    diaphragm_components: list[tuple[float, list[str]]] = field(default_factory=list)
    #   [(z_level, [master_node_id, slave_node_id, ...]), ...]

    # ── Diaphragm Z tolerance (per-elevation node matching) ───────
    # Tolerance (in model length units) used when grouping nodes near
    # a detected diaphragm elevation into a single rigidDiaphragm.
    # Configured via ``diaphragm_z_tolerance`` in the Preprocessor
    # config; defaults to 0.01 length units (matching the historical
    # hardcoded value).  Unit-agnostic: the model is built in the same
    # length units as the .s2k input.
    diaphragm_z_tolerance: float = 0.01

    # ── Loads-only area IDs (stiffness-free, mass-contributing) ──
    # Areas matching a loads-only selection are NOT created as shell
    # elements in OpenSees, but remain in the model for mass calc.
    loads_only_area_ids: set[str] = field(default_factory=set)

    # ── Orphan nodes (kept for visualisation only) ───────────────
    # Nodes that were removed from the main model because they are
    # only referenced by loads-only areas.  They exist purely for
    # rendering / visualisation and are NOT created in OpenSees.
    orphan_nodes: dict[str, Node] = field(default_factory=dict)

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def num_frames(self) -> int:
        return len(self.frame_elements)

    @property
    def num_areas(self) -> int:
        return len(self.area_elements)

    def summary(self) -> dict[str, Any]:
        """Return a dict of model statistics."""
        return {
            "Nodes": self.num_nodes,
            "Frames": self.num_frames,
            "Areas": self.num_areas,
            "Materials": len(self.materials),
            "Sections": len(self.sections),
            "Material tags": len(self.material_tags),
            "Constraint pairs": len(self.detected_edge_pairs),
            "Diaphragm levels": len(self.diaphragm_levels),
            "Distributed loads": len(self.frame_dist_loads),
            "Units": str(self.units),
        }
