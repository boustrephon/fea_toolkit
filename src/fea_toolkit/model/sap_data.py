"""Intermediate data model for SAP2000/ETABS models."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

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


@dataclass
class Section:
    """Frame or shell section properties."""
    name: str
    shape: str                  # "I/Wide Flange", "Pipe", "Box/Tube", "SD Section", "Shell"
    material: str               # material name (reference to Material)
    A: float                    # Cross-sectional area (m²)
    I33: float                  # Major axis moment of inertia (m⁴)
    I22: float                  # Minor axis moment of inertia (m⁴)
    J: float                    # Torsional constant (m⁴)
    # Shape-specific (optional)
    depth: float = 0.0          # Overall depth (m)
    width: float = 0.0          # Flange width (m)
    tw: float = 0.0             # Web thickness (m)
    tf: float = 0.0             # Flange thickness (m)
    # Plastic moduli (from manufacturer DB)
    Z33: Optional[float] = None
    Z22: Optional[float] = None
    # For shells
    thickness: float = 0.0
    # Extra
    manufacturer: Optional[str] = None

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

@dataclass
class LoadCombination:
    """SAP2000 load combination."""
    name: str
    combo_type: str   # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    cases: Dict[str, float] = field(default_factory=dict) 
    design: Dict[str, str] = field(default_factory=dict) 

@dataclass
class MassSource:
    """SAP2000 mass source."""
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
    # Loads (to be expanded later)
    load_cases: Dict[str,LoadCase] = field(default_factory=dict)
    load_patterns: Dict[str,LoadPattern] = field(default_factory=dict)
    joint_loads: List[JointLoad] = field(default_factory=list)
    frame_dist_loads: List[FrameDistributedLoad] = field(default_factory=list)    # dist_loads: Dict[str, DistributedLoad]
    # conc_loads: Dict[str, ConcentratedLoad]
    # Default units used for all coordinates and section properties
    units: Dict[str, str] = field(default_factory=lambda: {'F': "N", 'L': "m", 'T': "C"})   
    

