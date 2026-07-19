"""Frozen, serialisable topology after all model preparation.

``MeshModel`` is the output of the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`
and the input to the :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`.
It contains the fully prepared topology — split frames, meshed shells,
subdivided areas, detected constraints — with no OpenSees domain objects.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Set

from .sap_data import (
    Node, Restraint, Material, Section,
    FrameElement, AreaElement, Group,
    FrameDistributedLoad, JointLoad, GravityLoad,
    AreaGravityLoad, AreaUniformLoad,
)


@dataclass
class MeshModel:
    """Prepared model topology — the bridge between preprocessor and analysis.

    All geometry mutations (splitting, meshing, subdividing, constraint
    detection) have been applied.  No OpenSees domain objects exist.
    """

    # ── Nodes ────────────────────────────────────────────────────
    nodes: Dict[str, Node]                     # original · mesh · split

    # ── Frame elements (split + offset children) ──────────────────
    frame_elements: Dict[str, FrameElement]
    frame_assignments: Dict[str, str]

    # ── Area elements (meshed + subdivided) ───────────────────────
    area_elements: Dict[str, AreaElement]
    area_assignments: Dict[str, str]

    # ── Loads (redistributed to split children) ───────────────────
    frame_dist_loads: List[FrameDistributedLoad]
    edge_loads_from_areas: List = field(default_factory=list)
    # (list of tuples — exact format matches builder's edge_loads_from_areas)
    joint_loads: List[JointLoad] = field(default_factory=list)
    frame_gravity_loads: List[GravityLoad] = field(default_factory=list)
    area_gravity_loads: List[AreaGravityLoad] = field(default_factory=list)
    area_uniform_loads: List[AreaUniformLoad] = field(default_factory=list)

    # ── Constraints (detected, not yet applied to OpenSees) ───────
    edge_constraint_pairs: List[tuple] = field(default_factory=list)
    #   [(master_tag, slave_tag), ...]  — coarse→fine constraints
    diaphragm_levels: List[float] = field(default_factory=list)

    # ── Rigid links from frame end offsets ────────────────────────
    offset_rigid_links: List[tuple] = field(default_factory=list)

    # ── Metadata for element creation ─────────────────────────────
    frame_element_types: Dict[str, str] = field(default_factory=dict)
    #   elem_id → "beam" | "column" | "brace" | "wall" | "slab" | "unknown"
    area_element_types: Dict[str, str] = field(default_factory=dict)

    # ── Materials, sections, groups, restraints ───────────────────
    materials: Dict[str, Material] = field(default_factory=dict)
    sections: Dict[str, Section] = field(default_factory=dict)
    groups: Dict[str, Group] = field(default_factory=dict)
    restraints: Dict[str, Restraint] = field(default_factory=dict)
    base_z: Optional[float] = None

    # ── Tag maps (pre-computed for deterministic Ops recreation) ──
    frame_tag_map: Dict[str, int] = field(default_factory=dict)
    #   elem_id → OpenSees element tag
    material_tags: Dict[str, int] = field(default_factory=dict)
    #   material name → OpenSees material tag
    section_tags: Dict[str, int] = field(default_factory=dict)
    #   section name → OpenSees section tag
    shell_sec_tags: Dict[str, int] = field(default_factory=dict)
    shell_sec_variants: Dict[str, int] = field(default_factory=dict)

    # ── Units ─────────────────────────────────────────────────────
    units: Dict[str, str] = field(default_factory=lambda: {"F": "N", "L": "m", "T": "C"})

    # ── Model identification ─────────────────────────────────────
    model_name: str = ""

    # ── Saved edge constraint arguments (for pushover re-apply) ──
    # Each entry is a tuple of positional args for apply_edge_constraints
    saved_edge_constraints: List[tuple] = field(default_factory=list)

    # ── Loads-only area IDs (stiffness-free, mass-contributing) ──
    # Areas matching a loads-only selection are NOT created as shell
    # elements in OpenSees, but remain in the model for mass calc.
    loads_only_area_ids: Set[str] = field(default_factory=set)

    # ── Orphan nodes (kept for visualisation only) ───────────────
    # Nodes that were removed from the main model because they are
    # only referenced by loads-only areas.  They exist purely for
    # rendering / visualisation and are NOT created in OpenSees.
    orphan_nodes: Dict[str, Node] = field(default_factory=dict)

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def num_frames(self) -> int:
        return len(self.frame_elements)

    @property
    def num_areas(self) -> int:
        return len(self.area_elements)

    def summary(self) -> Dict[str, Any]:
        """Return a dict of model statistics."""
        return {
            "Nodes": self.num_nodes,
            "Frames": self.num_frames,
            "Areas": self.num_areas,
            "Materials": len(self.materials),
            "Sections": len(self.sections),
            "Material tags": len(self.material_tags),
            "Constraint pairs": len(self.edge_constraint_pairs),
            "Diaphragm levels": len(self.diaphragm_levels),
            "Distributed loads": len(self.frame_dist_loads),
            "Units": str(self.units),
        }
