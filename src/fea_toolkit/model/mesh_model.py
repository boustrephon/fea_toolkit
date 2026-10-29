"""Frozen, serialisable topology after all model preparation.

``MeshModel`` is the output of the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`
and the input to the :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`.
It contains the fully prepared topology — split frames, meshed shells,
subdivided areas, detected constraints — with no OpenSees domain objects.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .frame_segments import FrameSegment
from .sap_data import (
    AreaElement,
    AreaElementProperties,
    AreaGravityLoad,
    AreaUniformLoad,
    FrameDistributedLoad,
    FrameElement,
    FrameElementProperties,
    FrameEndOffset,
    FramePointLoad,
    FrameRelease,
    GravityLoad,
    Group,
    JointLoad,
    LayeredShellSection,
    LoadCase,
    LoadCombination,
    LoadPattern,
    MassSource,
    Material,
    NDMaterial,
    Node,
    ResponseSpectrumFunction,
    Restraint,
    Section,
    ShellUniformLoadSet,
    apply_material_defaults,
)


@dataclass
class WallElement:
    """Prepared wall macro-element for a wall area.

    Supports three wall macro-element families sharing the same
    open-quad corner layout [i, j, k, l]:

    * **SFI_MVLEM_3D** / **E_SFI_MVLEM_3D** — per-fibre FSAM nD
      materials (``fsam_material_names``).
    * **MVLEM_3D** — per-fibre uniaxial concrete + steel tags plus a
      single horizontal shear spring (``concrete_names`` /
      ``steel_names`` / ``shear_name``).

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
        fsam_material_names: Per-fiber FSAM nD material names (length
            ``m``); used by SFI_MVLEM_3D / E_SFI_MVLEM_3D.
        concrete_names: Per-fiber uniaxial concrete material names
            (length ``m``); used by MVLEM_3D when
            ``material_type == "uniaxial"``.
        steel_names: Per-fiber uniaxial steel material names (length
            ``m``); used by MVLEM_3D.
        shear_name: Single uniaxial shear-spring material name; used by
            MVLEM_3D.
        rho: Per-fiber mass density values (length ``m``); used by
            MVLEM_3D for the internal-node mass (must be non-zero).
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
    # ── MVLEM_3D (uniaxial) materials ──
    concrete_names: Optional[list[str]] = None  # length m
    steel_names: Optional[list[str]] = None  # length m
    shear_name: Optional[str] = None  # single shear tag
    dummy_name: Optional[str] = None  # interior steel (tiny-E Elastic)
    rho: Optional[list[float]] = None  # length m, non-zero
    # ── Element metadata ──
    material_type: str = "FSAM"  # "FSAM" | "uniaxial"
    element_type: Optional[str] = (
        None  # SFI_MVLEM_3D | E_SFI_MVLEM_3D | MVLEM_3D (None → derive from material_type)
    )
    CoR: float = 0.4
    ThickMod: Optional[float] = None
    Poisson: Optional[float] = None
    Density: Optional[float] = None

    def __post_init__(self) -> None:
        """Validate per-fibre list lengths before OpenSees command generation.

        Every per-fibre list supplied for this wall must contain exactly
        ``m`` entries so the builder can emit balanced ``-thick`` /
        ``-width`` / ``-mat`` / ``-matConcrete`` / ``-matSteel`` / ``-rho``
        argument lists.  ``fsam_material_names`` is validated only for the
        FSAM family (``material_type != "uniaxial"``): MVLEM_3D walls carry
        an intentionally empty list because their per-fibre materials live
        in ``concrete_names`` / ``steel_names`` instead.

        Raises:
            ValueError: If ``m`` is less than 1, or if ``m`` mismatches the
                length of ``thick``, ``width``, ``fsam_material_names``
                (FSAM family), or any supplied ``concrete_names`` /
                ``steel_names`` / ``rho``.
        """
        if self.m < 1:
            raise ValueError(f"WallElement '{self.elem_id}': m must be at least 1, got {self.m}.")
        per_fibre_fields: list[tuple[str, Optional[list]]] = [
            ("thick", self.thick),
            ("width", self.width),
            ("fsam_material_names", self.fsam_material_names),
            ("concrete_names", self.concrete_names),
            ("steel_names", self.steel_names),
            ("rho", self.rho),
        ]
        for field_name, values in per_fibre_fields:
            if values is None:
                continue
            if field_name == "fsam_material_names" and self.material_type == "uniaxial":
                continue
            if len(values) != self.m:
                raise ValueError(
                    f"WallElement '{self.elem_id}': per-fibre field "
                    f"'{field_name}' has length {len(values)} but m={self.m} "
                    f"— expected exactly {self.m} entries."
                )


@dataclass
class PanelZoneSpec:
    """The Krawinkler & Gupta panel-zone spring at one beam-column joint.

    Computed once by the
    :class:`~fea_toolkit.opensees.preprocessor.Preprocessor` and carried on
    the :class:`MeshModel` so the
    :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder` only has
    to emit the OpenSees commands.  The joint is split into a column node
    (the original joint, which the column passes through) and a coincident
    beam node (``*_pz``), with the beams re-pointed to the beam node and the
    two coupled by a zero-length bilinear rotational spring.

    Attributes:
        column_node_id: The original joint node (column passes through).
        beam_node_id: The generated coincident ``*_pz`` node.
        my: Krawinkler & Gupta yield moment (``0.55 · Fy · d_c · t_p · d_b``).
        k_theta: Krawinkler & Gupta rotational stiffness
            (``0.95 · G · t_p · d_c · d_b``).
        b: Post-yield hardening ratio of the bilinear ``Steel01`` spring.
    """

    column_node_id: str
    beam_node_id: str
    my: float
    k_theta: float
    b: float


@dataclass
class MeshModel:
    """Prepared model topology — the bridge between preprocessor and analysis.

    All geometry mutations (splitting, meshing, subdividing, constraint
    detection, panel-zone splitting) have been applied.  No OpenSees domain
    objects exist.

    ``MeshModel`` is frozen with respect to its preprocessed topology —
    :attr:`panel_zones`, :attr:`wall_elements` and
    :attr:`frame_element_types` are read-only to the ``AnalysisBuilder``.
    Lumped-hinge and member-release instrumentation are kept in builder-local
    state (generated nodes / re-points never written back to the mesh).  The
    remaining instrumentation (bond-slip springs, brace subdivision and
    limit-state columns) is applied by the builder at build time and restored
    via its ``_restore_*`` helpers — known debt, see ``docs/dev_notes.md``.
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
    # ── Wall elements (SFI_MVLEM_3D / E_SFI_MVLEM_3D / MVLEM_3D) ──
    # Populated by the Preprocessor from wall-classified areas when
    # ``element_strategies.wall.element_type`` is one of the supported
    # wall macro-element types (SFI_MVLEM_3D, E_SFI_MVLEM_3D, MVLEM_3D).
    wall_elements: dict[str, WallElement] = field(default_factory=dict)
    #   elem_id → WallElement
    # ── Panel zones (Krawinkler & Gupta joint springs) ───────────
    # Populated by the Preprocessor when ``panel_zone_model`` is
    # ``"krawinkler_gupta"``; keyed by the column (original joint) node ID.
    # The ``*_pz`` beam nodes and the re-pointed beams are already in
    # ``nodes`` / ``frame_elements`` — this dict only records the spring
    # parameters and the column↔beam node pairing for the builder to emit.
    panel_zones: dict[str, PanelZoneSpec] = field(default_factory=dict)
    #   column_node_id → PanelZoneSpec
    # (list of tuples — exact format matches builder's edge_loads_from_areas)
    joint_loads: list[JointLoad] = field(default_factory=list)
    added_masses: dict[str, float] = field(default_factory=dict)
    frame_point_loads: list[FramePointLoad] = field(default_factory=list)
    frame_gravity_loads: list[GravityLoad] = field(default_factory=list)
    area_gravity_loads: list[AreaGravityLoad] = field(default_factory=list)
    area_uniform_loads: list[AreaUniformLoad] = field(default_factory=list)
    load_patterns: dict[str, "LoadPattern"] = field(default_factory=dict)
    mass_sources: dict[str, MassSource] = field(default_factory=dict)
    # Load-case / combination / response-spectrum *definitions* — carried
    # through unchanged from the SAP model so the Model Tree (and the recipe
    # panel) can present them without the raw model at hand.
    load_cases: dict[str, LoadCase] = field(default_factory=dict)
    load_combinations: dict[str, LoadCombination] = field(default_factory=dict)
    response_spectrum_functions: dict[str, ResponseSpectrumFunction] = field(default_factory=dict)
    # ETABS shell uniform load sets — carried through unchanged so the Model
    # Tree can present them (definitions + per-area assignment) without the raw
    # model at hand.
    shell_uniform_load_sets: dict[str, ShellUniformLoadSet] = field(default_factory=dict)
    shell_uniform_load_set_assignments: dict[str, str] = field(default_factory=dict)

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
    # Frame end offsets carried through to the builder for native
    # ``-jntOffset`` emission (populated only when ``rigid_offset_method`` is
    # ``"jntOffset"`` — the default).  Empty when the Preprocessor applies
    # the offsets itself as stiff links / MPCs (``"beam"`` / ``"mpc"``).
    frame_end_offsets: dict[str, FrameEndOffset] = field(default_factory=dict)
    # Frame end releases / partial fixity keyed by (active) frame id.  When a
    # released frame is split by the Preprocessor the release is re-mapped
    # onto the first/last leaf element so the key remains an active element.
    frame_releases: dict[str, FrameRelease] = field(default_factory=dict)

    # ── Metadata for element creation ─────────────────────────────
    frame_element_types: dict[str, str] = field(default_factory=dict)
    #   elem_id → "beam" | "column" | "brace" | "wall" | "slab" | "unknown"
    area_element_types: dict[str, str] = field(default_factory=dict)

    # ── Hinge-eligible segmentation (route-agnostic topology) ─────
    # member_id → ordered [FrameSegment, ...] between consecutive
    # hinge-eligible points.  Computed by the Preprocessor; the builder emits
    # one spring per point only on the lumped-hinge route
    # (see docs/imk_member_classification.md (c)/(e)).
    frame_segments: dict[str, list[FrameSegment]] = field(default_factory=dict)

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

    # ── Rigid-body components (SAP2000 BODY constraints) ─────────
    # Each entry is a ``(constraint_name, [node_id, ...], [dof_flag, ...])``
    # tuple for one ``CONSTRAINT DEFINITIONS - BODY`` group — a rigid tie
    # between its assigned joints on the enabled DOFs.  ``dof_flag`` is the
    # six-element ``[UX, UY, UZ, RX, RY, RZ]`` enabled-DOF list (OpenSees
    # DOF order 1..6).  Emitted by the AnalysisBuilder as one
    # ``ops.rigidLink('beam', master, slave)`` MPC per slave node when all
    # six flags are set, or ``ops.equalDOF`` for a translation-only partial
    # set (a partial set that enables a rotational DOF is rejected).
    rigid_body_components: list[tuple[str, list[str], list[bool]]] = field(default_factory=list)

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

    # ── Opening (void) corner node ids moved to ``orphan_nodes`` ──
    # Unconnected opening-corner nodes are moved to ``orphan_nodes`` so no
    # OpenSees-domain consumer can accidentally create them; ``net_area``
    # resolves opening vertices from ``nodes`` + ``orphan_nodes``.  This set
    # records which orphan nodes are opening corners for tracing.
    opening_node_ids: set[str] = field(default_factory=set)

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
            "Distributed loads": sum(
                1 for ld in self.frame_dist_loads if not getattr(ld, "inactive", False)
            ),
            "Units": str(self.units),
        }

    def apply_material_defaults(self) -> None:
        """Fill missing material properties with SI defaults scaled to units.

        Delegates to :func:`~fea_toolkit.model.sap_data.apply_material_defaults`
        using this model's ``materials`` and ``units`` (idempotent).
        """
        apply_material_defaults(self.materials, self.units)
