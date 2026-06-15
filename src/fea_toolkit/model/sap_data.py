"""Intermediate data model for SAP2000/ETABS models."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class Node:
    """Finite element node."""
    id: str                     # SAP2000 label (e.g., "1")
    x: float
    y: float
    z: float
    is_special: bool = False


@dataclass
class Restraint:
    """Boundary conditions at a node."""
    dofs: List[int]             # [U1, U2, U3, R1, R2, R3] where 1 = fixed, 0 = free


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
    id: str                     # SAP2000 frame label
    node_i: str
    node_j: str
    angle: float = 0.0          # Rotation about local x‑axis (degrees)


@dataclass
class AreaElement:
    """2D shell/area element connectivity."""
    id: str
    node_ids: List[str]         # ordered corner nodes
    thickness: float = 0.0


@dataclass
class Group:
    """Named group of objects."""
    name: str
    color: Optional[str] = None
    objects: List[str] = field(default_factory=list)  # "Frame:123", "Area:456", "Joint:1"


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
    frame_auto_mesh: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # frame_id -> auto mesh settings
    # Loads (to be expanded later)
    # dist_loads: Dict[str, DistributedLoad]
    # conc_loads: Dict[str, ConcentratedLoad]
    units: str = "mm"   # length unit used for all coordinates and section properties
    

