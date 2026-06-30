"""Intermediate data model for SAP2000/ETABS models."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple


@dataclass
class CoordSys:
    """Coordinate system."""
    name: str
    coord_type: str    # "Cartesian", "Cylindrical", "Spherical"
    x: float = 0
    y: float = 0
    z: float = 0
    xx: float = 0
    yy: float = 0
    zz: float = 0

default_coord_sys = CoordSys(name="GLOBAL", coord_type="Cartesian")

@dataclass
class Node:
    """Finite element node."""
    node_id: str                     # SAP2000 label (e.g., "1")
    node_tag: int                    # numeric tag for OpenSees etc
    x: float
    y: float
    z: float
    is_special: bool = False


@dataclass
class Restraint:
    """Boundary conditions at a node."""
    dofs: List[int]             # [U1, U2, U3, R1, R2, R3] where 1 = fixed, 0 = free

@dataclass
class Constraint:
    """Boundary conditions at a node."""
    name: str
    constraint_type: str  # e.g. BODY
    coord_sys: str = "GLOBAL"
    constraint_data: Dict[str, Any] = field(default_factory=dict) #

@dataclass
class FrameEndOffset:
    """Rigid end offset (rigid zone) at each end of a frame element.

    Values are in model length units (typically m or mm), measured from
    the node toward the element interior.  Zero means no offset (the
    elastic portion extends all the way to the node).
    """
    end_i: float = 0.0    # Offset at I-end
    end_j: float = 0.0    # Offset at J-end


@dataclass
class AreaMesh:
    """Auto-mesh settings for an area element (from AREA MESH ASSIGNMENTS).

    Controls how SAP2000 subdivides the area into smaller shell elements
    for analysis.
    """
    auto_mesh: bool = False
    no_auto_mesh_at_edges: bool = False
    no_sub_mesh: bool = False
    min_size: float = 0.0
    max_size: float = 0.0


@dataclass
class AreaEdgeConstraint:
    """Edge constraint assignment for a single edge of an area element.

    SAP2000 uses these to enforce connectivity between coarse and fine
    meshes along shared edges.
    """
    area_id: str = ""
    edge: int = 0
    constraint: str = "Default"


@dataclass
class Material:
    """Material properties from SAP2000, including all tables."""
    name: str
    type: str                     # "Steel", "Concrete", "Rebar", "Tendon", etc.
    grade: Optional[str] = None
    E_mod: float = 0.0                # Young's modulus (Pa)
    G_mod: float = 0.0                # Shear modulus (Pa)
    nu: float = 0.0               # Poisson's ratio
    unit_weight: float = 0.0      # N/m³
    unit_mass: float = 0.0        # kg/m³
    Fy: Optional[float] = None    # Yield strength (steel, rebar, tendon) – Pa
    Fu: Optional[float] = None    # Ultimate strength – Pa
    Fc: Optional[float] = None    # Concrete compressive strength – Pa
    eFc: Optional[float] = None   # Strain at Fc
    extra: Dict[str, Any] = field(default_factory=dict)   # all other properties


# ============================================================================
# Section type hierarchy
# ============================================================================

# Mapping of SAP2000/ETABS shape names to canonical internal identifiers.
# New names should be added here as needed.
SHAPE_NAMES = {
    # I / Wide flange
    "I/Wide Flange": "I",
    "WIDE FLANGE": "I",
    "Steel I/Wide Flange": "I",
    # Channel
    "Channel": "CH",
    "CHANNEL": "CH",
    "Steel Channel": "CH",
    "Concrete Channel": "CH",
    # Single angle
    "Angle": "A",
    "Steel Angle": "A",
    "Concrete Angle": "A",
    # Double angle
    "Double Angle": "AA",
    "Steel Double Angle": "AA",
    "Concrete Double Angle": "AA",
    # Tee
    "Tee": "T",
    # Rectangular solid
    "Rectangular": "R",
    "Rectangle": "R",
    "RECTANGLE": "R",
    "Steel Plate": "R",
    "Concrete Rectangular": "R",
    "Concrete Circular": "C",
    # Circular solid
    "Circle": "C",
    "CIRCLE": "C",
    "Steel Rod": "C",
    "Steel Circle": "C",
    "Concrete Circle": "C",
    # Pipe / CHS
    "Pipe": "CHS",
    "PIPE": "CHS",
    "Steel Pipe": "CHS",
    "Concrete Pipe": "CHS",
    # Box / RHS
    "Box/Tube": "RHS",
    "Tube": "RHS",
    "TUBE": "RHS",
    "Steel Tube": "RHS",
    "Concrete Tube": "RHS",
    # General / catalogue
    "General": "GEN",
    "GENERAL": "GEN",
    "NA": "GEN",
    # SD Section
    "SD Section": "SD",
    # Nonprismatic
    "Nonprismatic": "NP",
    # Encased
    "Concrete Encasement Rectangle": "ECR",
    "Concrete Encasement Circle": "ECC",
    # Deck
    "Steel Deck": "DK",
}


@dataclass
class Section:
    """Base class for all frame section types.

    Stores the derived section properties common to all shapes (area, inertias,
    torsional constant, plastic moduli) plus shape‑specific dimensions in
    subclasses.

    Subclasses should override :meth:`to_fiber_patches` to generate OpenSees
    fiber patch definitions for nonlinear analysis.
    """

    name: str                     # Section name (SAP2000 label)
    shape: str                    # Original SAP2000 shape name e.g. "I/Wide Flange"
    material: str                 # Reference to Material.name
    A: float = 0.0                # Cross-sectional area
    I33: float = 0.0              # Major-axis moment of inertia
    I22: float = 0.0              # Minor-axis moment of inertia
    J: float = 0.0                # Torsional constant
    # Plastic moduli (from manufacturer DB where available)
    Z33: Optional[float] = None
    Z22: Optional[float] = None
    # Extra
    manufacturer: Optional[str] = None

    @property
    def shape_id(self) -> str:
        """Canonical shape identifier (see SHAPE_NAMES)."""
        return SHAPE_NAMES.get(self.shape, "GEN")

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Generate OpenSees ``patch`` definitions for fiber sections.

        Args:
            mat_tag: OpenSees material tag.
            nfy: Number of fibres along the local y direction.
            nz: Number of fibres along the local z direction.

        Returns:
            List of ``('rect', mat_tag, nfy, nfz, y1, z1, y2, z2)`` tuples.

        Raises:
            NotImplementedError: If the section type does not support fiber
                conversion (e.g. general catalogue sections).
        """
        raise NotImplementedError(
            f"Fiber conversion not implemented for {type(self).__name__}"
        )


# --- Shape‑specific subclasses -------------------------------------------------


@dataclass
class ISection(Section):
    """I / Wide-flange section with equal flanges.

    OpenSees fiber representation: bottom flange → web → top flange,
    all as rectangular patches.
    """
    depth: float = 0.0    # Overall depth D
    bf: float = 0.0       # Flange width B
    tf: float = 0.0       # Flange thickness
    tw: float = 0.0       # Web thickness

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        y1 = -self.depth / 2.0
        y2 = -self.depth / 2.0 + self.tf
        y3 = self.depth / 2.0 - self.tf
        y4 = self.depth / 2.0
        return [
            ("rect", mat_tag, nfy, nfz, y1, -self.bf / 2, y2, self.bf / 2),
            ("rect", mat_tag, nfy, nfz, y2, -self.tw / 2, y3, self.tw / 2),
            ("rect", mat_tag, nfy, nfz, y3, -self.bf / 2, y4, self.bf / 2),
        ]


@dataclass
class GeneralSection(Section):
    """Generic section from catalogue or with directly specified properties.

    No shape‑specific dimensions are stored — all derived properties
    (A, I33, I22, J, etc.) are provided by SAP2000 / the catalogue.
    """
    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        raise NotImplementedError(
            "Fiber conversion requires a known shape type "
            "(I, Pipe, Box, etc.), not a General section"
        )


@dataclass
class PipeSection(Section):
    """Circular hollow section / pipe (CHS)."""
    od: float = 0.0       # Outer diameter
    t: float = 0.0        # Wall thickness

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Annular ring via ``patch circ``."""
        R = self.od / 2.0
        return [
            ("circ", mat_tag, nfy, nfz, 0.0, 0.0,
             max(0.0, R - self.t), R, 0.0, 360.0),
        ]


@dataclass
class BoxSection(Section):
    """Rectangular hollow section / box / tube (RHS)."""
    depth: float = 0.0    # D
    bf: float = 0.0       # B
    tf: float = 0.0       # Flange (top/bottom) thickness
    tw: float = 0.0       # Web (left/right) thickness

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Four rectangular patches for the flanges and webs."""
        D, B = self.depth, self.bf
        tf, tw = self.tf, self.tw
        half = D / 2.0
        hb = B / 2.0
        return [
            # Top flange
            ("rect", mat_tag, nfy, nfz, half - tf, -hb, half, hb),
            # Bottom flange
            ("rect", mat_tag, nfy, nfz, -half, -hb, -half + tf, hb),
            # Left web
            ("rect", mat_tag, nfy, nfz, -half + tf, -hb, half - tf, -hb + tw),
            # Right web
            ("rect", mat_tag, nfy, nfz, -half + tf, hb - tw, half - tf, hb),
        ]


@dataclass
class RectangularSection(Section):
    """Solid rectangular section."""
    depth: float = 0.0    # D
    bf: float = 0.0       # B

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        y1, y2 = -self.depth / 2, self.depth / 2
        z1, z2 = -self.bf / 2, self.bf / 2
        return [("rect", mat_tag, nfy, nfz, y1, z1, y2, z2)]


@dataclass
class CircularSection(Section):
    """Solid circular section / rod."""
    diameter: float = 0.0

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Solid circle via ``patch circ`` with zero inner radius."""
        R = self.diameter / 2.0
        return [
            ("circ", mat_tag, nfy, nfz, 0.0, 0.0, 0.0, R, 0.0, 360.0),
        ]


@dataclass
class ConcreteRectangularSection(Section):
    """Reinforced concrete rectangular section.

    SAP2000 shape: ``Concrete Rectangular``
    """
    depth: float = 0.0       # D (local y direction)
    bf: float = 0.0          # B (local z direction)
    cover: float = 0.0       # Clear cover to rebar centreline
    top_bars: int = 0
    bot_bars: int = 0
    top_bar_dia: float = 0.0
    bot_bar_dia: float = 0.0

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 12, nfz: int = 6
    ) -> List[Tuple]:
        """Fiber patches: confined core + unconfined cover + rebar layers.

        Material tag convention:
            mat_tag     → unconfined concrete (Concrete01)
            mat_tag + 1 → confined concrete (Concrete01)
            mat_tag + 2 → steel rebar (Steel02)
        """
        d, b = self.depth, self.bf
        cv = self.cover
        half_d, half_b = d / 2.0, b / 2.0
        core_y1, core_y2 = -half_d + cv, half_d - cv
        core_z1, core_z2 = -half_b + cv, half_b - cv

        patches: List[Tuple] = [
            ("rect", mat_tag + 1, nfy, nfz, core_y1, core_z1, core_y2, core_z2),
            ("rect", mat_tag, nfy, 1, core_y2, -half_b, half_d, half_b),
            ("rect", mat_tag, nfy, 1, -half_d, -half_b, core_y1, half_b),
        ]
        if core_y2 > core_y1:
            patches.append(
                ("rect", mat_tag, 1, max(1, nfz - 2), core_y1, -half_b, core_y2, core_z1)
            )
            patches.append(
                ("rect", mat_tag, 1, max(1, nfz - 2), core_y1, core_z2, core_y2, half_b)
            )
        # Rebar layers
        if self.top_bars and self.top_bar_dia > 0:
            patches.append(
                ("straight", mat_tag + 2, self.top_bars, self.top_bar_dia,
                 half_d - cv, -half_b + cv, half_d - cv, half_b - cv)
            )
        if self.bot_bars and self.bot_bar_dia > 0:
            patches.append(
                ("straight", mat_tag + 2, self.bot_bars, self.bot_bar_dia,
                 -half_d + cv, -half_b + cv, -half_d + cv, half_b - cv)
            )
        return patches


@dataclass
class ConcreteCircularSection(Section):
    """Reinforced concrete circular section.

    SAP2000 shape: ``Concrete Circular``
    """
    diameter: float = 0.0
    cover: float = 0.0
    bar_count: int = 0
    bar_dia: float = 0.0

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 12, nfz: int = 6
    ) -> List[Tuple]:
        """Fiber patches: confined core ring + unconfined cover + rebar ring."""
        R = self.diameter / 2.0
        R_core = max(0.0, R - self.cover)
        patches: List[Tuple] = [
            ("circ", mat_tag + 1, nfy, nfz, 0.0, 0.0, 0.0, R_core, 0.0, 360.0),
            ("circ", mat_tag, nfy, nfz, 0.0, 0.0, R_core, R, 0.0, 360.0),
        ]
        if self.bar_count and self.bar_dia > 0:
            R_rebar = R - self.cover
            patches.append(
                ("circ", mat_tag + 2, self.bar_count, 1,
                 0.0, 0.0, R_rebar - self.bar_dia / 2.0,
                 R_rebar + self.bar_dia / 2.0, 0.0, 360.0)
            )
        return patches


@dataclass
class ChannelSection(Section):
    """Channel / C‑section."""
    depth: float = 0.0    # D
    bf: float = 0.0       # B
    tf: float = 0.0       # Flange thickness
    tw: float = 0.0       # Web thickness

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Placeholder — channel patches not yet implemented."""
        raise NotImplementedError(
            "Fiber conversion for ChannelSection not yet implemented"
        )


@dataclass
class AngleSection(Section):
    """Single angle section (L)."""
    depth: float = 0.0    # D
    bf: float = 0.0       # B
    tf: float = 0.0       # Flange thickness
    tw: float = 0.0       # Web thickness

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Placeholder — angle patches not yet implemented."""
        raise NotImplementedError(
            "Fiber conversion for AngleSection not yet implemented"
        )


@dataclass
class DoubleAngleSection(Section):
    """Double angle section (2L)."""
    depth: float = 0.0    # D
    bf: float = 0.0       # B (overall width including gap)
    tf: float = 0.0       # Flange thickness
    tw: float = 0.0       # Web thickness
    dis: float = 0.0      # Gap between angles

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Placeholder — double‑angle patches not yet implemented."""
        raise NotImplementedError(
            "Fiber conversion for DoubleAngleSection not yet implemented"
        )


@dataclass
class TeeSection(Section):
    """Tee section (T)."""
    depth: float = 0.0    # D
    bf: float = 0.0       # B
    tf: float = 0.0       # Flange thickness
    tw: float = 0.0       # Web (stem) thickness

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Placeholder — tee patches not yet implemented."""
        raise NotImplementedError(
            "Fiber conversion for TeeSection not yet implemented"
        )


@dataclass
class SDSection(Section):
    """Section Designer section — may be composite, with multiple materials.

    Each polygon is a closed loop of (y, z) coordinates associated with a
    material.  For composite sections the list holds contributions from
    steel, concrete, rebar etc.
    """
    polygons: List[Tuple[str, List[Tuple[float, float]]]] = field(
        default_factory=list
    )
    # Each tuple: (material_name, [(y1, z1), (y2, z2), ...])

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Placeholder — SD polygons require triangulation / meshing."""
        raise NotImplementedError(
            "Fiber conversion for SD sections requires polygon meshing — "
            "not yet implemented"
        )


@dataclass
class EncasedSection(Section):
    """Composite encased section (e.g. concrete‑encased steel).

    Stores the embedded (steel) section plus the encasement geometry and
    material.
    """
    embedded_section: Optional["Section"] = None
    encasement_material: str = ""
    encasement_depth: float = 0.0
    encasement_bf: float = 0.0

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        """Placeholder — encased sections need steel + concrete patches."""
        raise NotImplementedError(
            "Fiber conversion for EncasedSection not yet implemented"
        )


@dataclass
class ShellSection(Section):
    """Shell / area section (2‑D)."""
    thickness: float = 0.0

    def to_fiber_patches(
        self, mat_tag: int, nfy: int = 8, nfz: int = 4
    ) -> List[Tuple]:
        raise NotImplementedError(
            "Fiber conversion is not applicable to ShellSection"
        )


# --- Non‑section dataclasses ---------------------------------------------------

@dataclass
class FrameElement:
    """1D frame element connectivity."""
    elem_id: str                     # SAP2000 frame label
    elem_tag: int                    # numeric tag for OpenSees etc
    node_i: str
    node_j: str
    angle: float = 0.0          # Rotation about local x‑axis (degrees)
    inactive: bool = False
    parent_id: Optional[str] = None
    child_ids: List[str] = field(default_factory=list)
    t_locations: List[float] = field(default_factory=list)   # parametric positions 0..1 where split occurs

@dataclass
class AreaElement:
    """2D shell/area element connectivity."""
    area_id: str
    area_tag: int
    node_ids: List[str]         # ordered corner nodes
    thickness: float = 0.0
    inactive: bool = False      # True when superseded by mesh sub‑elements


@dataclass
class Group:
    """Named group of objects."""
    name: str
    color: Optional[str] = None
    objects: List[str] = field(default_factory=list)  # "Frame:123", "Area:456", "Joint:1"

@dataclass
class LoadCase:
    """SAP2000 load case."""
    case_name: str
    case_type: str
    design_type_option: str   # "Prog Det"
    design_type: str          # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    design_action_option: str # "Prog Det"
    design_action: str        # 'Non-Composite', 'Long-Term Composite', 'Short-Term Composite', etc.
    initial_condition: str = 'Zero'
    modal_case: str = ''
    run_case: bool = False
    case_data: Dict[str, Any] = field(default_factory=dict) # "CASE - MODAL ..." or "CASE - RESPONSE SPECTRUM ..." etc

@dataclass
class LoadPattern:
    """SAP2000 load pattern."""
    name: str
    pattern_type: str          # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    self_weight_factor: float = 0.0
    auto_data: Dict[str, Any] = field(default_factory=dict)   # data from AUTO* tables

@dataclass
class LoadCombination:
    """SAP2000 load combination."""
    name: str
    combo_type: str   # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    cases: Dict[str, float] = field(default_factory=dict) 
    design: Dict[str, str] = field(default_factory=dict) 

@dataclass
class MassSource:
    """SAP2000 mass source definition."""
    name: str
    elements: bool = False
    masses: bool = False
    loads: bool = False
    is_default: bool = False
    load_pattern: Dict[str, float] = field(default_factory=dict)

@dataclass
class JointLoad:
    """ "JOINT LOADS - FORCE" : Concentrated load at a joint."""
    pattern: str               # name of the load pattern
    # coord_sys: CoordSys
    node_id: str
    # node_tag: int
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 0.0
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0
    coord_sys: str = "GLOBAL"


@dataclass
class AreaUniformLoad:
    """Uniform pressure load on an area element."""
    pattern: str               # load pattern name
    area_id: str               # area element ID
    coord_sys: str = "GLOBAL"  # 'GLOBAL' or 'Local'
    direction: str = "Gravity" # 'Gravity', 'X', 'Y', 'Z'
    value: float = 0.0         # pressure (force/area)


@dataclass
class GravityLoad:
    """ # "FRAME LOADS - GRAVITY" 
       Frame=5   LoadPat="leg stiffener_1_t=20"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0   MultiplierZ=-1.05
       """
    pattern: str
    # coord_sys: CoordSys
    frame_id: str
    # frame_tag: int
    multiplier_x: float = 0.0
    multiplier_y: float = 0.0
    multiplier_z: float = 0.0
    coord_sys: str = "GLOBAL"

@dataclass
class AreaGravityLoad:
    """AREA LOADS - GRAVITY table entry.

       Area=1   LoadPat="DEAD"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0   MultiplierZ=-1
    """
    pattern: str
    area_id: str
    multiplier_x: float = 0.0
    multiplier_y: float = 0.0
    multiplier_z: float = 0.0
    coord_sys: str = "GLOBAL"

@dataclass
class FramePointLoad:
    """Concentrated load on a frame element."""
    pattern: str               # name of the load pattern
    # coord_sys: CoordSys
    node_id: str
    # node_tag: int
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 0.0
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0
    coord_sys: str = "GLOBAL"

@dataclass
class FrameDistributedLoad:
    """ "FRAME LOADS - DISTRIBUTED" : Distributed load on a frame element.
    Frame=5   LoadPat="wind +X"   CoordSys=GLOBAL   Type=Force   Dir=X   DistType=RelDist  RelDistA=0   RelDistB=1   AbsDistA=0   AbsDistB=5.08   FOverLA=1.65   FOverLB=1.65
    """
    pattern: str
    # coord_sys: CoordSys
    frame_id: str
    # frame_tag: int
    direction: str             # 'Gravity', 'Projected', 'LocalX', etc.
    load_type: str             # 'Force' or 'Moment'
    shape: str                 # 'Uniform', 'Linear', 'Trapezoidal'
    val_a: float               # intensity at start (force/length)
    val_b: float               # intensity at end
    rdist_a: float              # relative distance from start
    rdist_b: float              # relative distance from start
    dist_a: float              # absolute distance from start (in model units)
    dist_b: float              # absolute distance from start
    coord_sys: str = "GLOBAL"

@dataclass
class SAPModelData:
    """Complete SAP2000 model data for export to OpenSees or Rhino."""
    nodes: Dict[str, Node]
    restraints: Dict[str, Restraint]
    materials: Dict[str, Material]
    sections: Dict[str, Section]
    frame_elements: Dict[str, FrameElement]
    area_elements: Dict[str, AreaElement]
    frame_assignments: Dict[str, str]      # frame_id -> section_name
    area_assignments: Dict[str, str]       # area_id -> section_name
    groups: Dict[str, Group]
    frame_auto_mesh: Dict[str, Dict[str, Any]]   # frame_id -> auto mesh settings
    frame_end_offsets: Dict[str, FrameEndOffset] = field(default_factory=dict)
    area_mesh: Dict[str, AreaMesh] = field(default_factory=dict)
    area_edge_constraints: Dict[str, List[AreaEdgeConstraint]] = field(default_factory=dict)
    # Loads (to be expanded later)
    load_cases: Dict[str,LoadCase] = field(default_factory=dict)
    load_patterns: Dict[str,LoadPattern] = field(default_factory=dict)
    joint_loads: List[JointLoad] = field(default_factory=list)
    frame_dist_loads: List[FrameDistributedLoad] = field(default_factory=list)
    area_uniform_loads: List[AreaUniformLoad] = field(default_factory=list)
    area_gravity_loads: List[AreaGravityLoad] = field(default_factory=list)
    frame_gravity_loads: List[GravityLoad] = field(default_factory=list)
    mass_sources: Dict[str, MassSource] = field(default_factory=dict)
    # Default units used for all coordinates and section properties
    units: Dict[str, str] = field(default_factory=lambda: {'F': "N", 'L': "m", 'T': "C"})   
    

