"""Intermediate data model for SAP2000/ETABS models."""

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from ..utils import (
    DEFAULT_E_C_PA,
    DEFAULT_E_S_PA,
    DEFAULT_FC_PA,
    DEFAULT_FY_REBAR_PA,
    # Material-property defaults (SI units — single source of truth in utils.py)
    DEFAULT_FY_STEEL_PA,
    DEFAULT_G_C_PA,
    DEFAULT_G_MOD_FRAC,
    DEFAULT_RHO_MC_SI,
    DEFAULT_RHO_MS_SI,
    DEFAULT_RHO_WC_SI,
    DEFAULT_RHO_WS_SI,
    force_to_si_factor,
    length_to_si_factor,
    lineal_force_to_si_factor,
    mass_density_scale_factor,
    mass_density_to_si_factor,
    stress_scale_factor,
    stress_to_si_factor,
    weight_density_scale_factor,
    weight_density_to_si_factor,
)


@dataclass
class CoordSys:
    """Coordinate system."""

    name: str
    coord_type: str  # "Cartesian", "Cylindrical", "Spherical"
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

    node_id: str  # SAP2000 label (e.g., "1")
    node_tag: int  # numeric tag for OpenSees etc
    x: float
    y: float
    z: float
    is_special: bool = False


@dataclass
class Restraint:
    """Boundary conditions at a node."""

    dofs: list[int]  # [U1, U2, U3, R1, R2, R3] where 1 = fixed, 0 = free


@dataclass
class Constraint:
    """Boundary conditions at a node."""

    name: str
    constraint_type: str  # e.g. BODY
    coord_sys: str = "GLOBAL"
    constraint_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FrameEndOffset:
    """Rigid end offset (rigid zone) at each end of a frame element.

    Values are in model length units (typically m or mm).
    ``end_i`` / ``end_j`` are longitudinal offsets measured from the node
    toward the element interior (zero = elastic portion extends to the
    node).

    ``off_y_i`` / ``off_z_i`` / ``off_y_j`` / ``off_z_j`` are lateral
    offsets in the local y/z plane, typically derived from cardinal point
    (insertion point) settings.  These shift the section position relative
    to the reference line.  See :class:`FrameElement` for the cardinal
    point numbering scheme (1–11).
    """

    end_i: float = 0.0  # Longitudinal offset at I-end
    end_j: float = 0.0  # Longitudinal offset at J-end
    off_y_i: float = 0.0  # Lateral y-offset at I-end (from cardinal pt)
    off_z_i: float = 0.0  # Lateral z-offset at I-end (from cardinal pt)
    off_y_j: float = 0.0  # Lateral y-offset at J-end (from cardinal pt)
    off_z_j: float = 0.0  # Lateral z-offset at J-end (from cardinal pt)


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
class StressStrainCurve:
    """Hysteretic stress-strain curve parameters for uniaxial materials.

    These parameters appear across the SAP2000 MATERIAL PROPERTIES 03X
    family of tables (03A – Steel, 03B – Concrete, 03E – Rebar,
    03F – Tendon, 03G – Other).

    Not all fields apply to every material type; consult the relevant
    table for context-specific usage:

    ========== =========== ========== ============ ===========
    Field      03A Steel   03B Concr  03E Rebar    03F Tendon
    ========== =========== ========== ============ ===========
    ss_curve    Simple      Mander     Simple       "270 ksi"
    ss_hys      Kinematic   Takeda     Kinematic    Kinematic
    s_hard      0.015       —          0.01         —
    s_fc        —           0.002      —            —
    s_cap       —           0.005      0.09         —
    s_max       0.11        —          —            —
    s_rup       0.17        —          —            —
    final_slope -0.1        -0.1       -0.1         -0.1
    coup_mod    Von Mises   Mod. D-P   Von Mises    Von Mises
    f_angle     —           0.0        —            —
    d_angle     —           0.0        —            —
    use_ct_def  —           —          No           —
    ========== =========== ========== ============ ===========

    Args:
        ss_curve_opt: Stress-strain curve option (``"Simple"``,
            ``"Mander"``, or a custom label like ``"270 ksi"``).
        ss_hys_type: Hysteresis type (``"Kinematic"``, ``"Takeda"``,
            ``"Isotropic"``, etc.).
        s_hard: Strain at onset of hardening (steel/rebar).
        s_fc: Strain at peak compressive strength ``fc'`` (concrete).
        s_cap: Strain at crushing / cap point.
        s_max: Strain at maximum strength (steel).
        s_rup: Rupture strain (steel).
        final_slope: Normalised post-peak stiffness (fraction of initial
            elastic modulus, typically negative).
        coup_mod_type: Coupling / damage model type (e.g. ``"Von Mises"``,
            ``"Modified Darwin-Pecknold"``).
        f_angle: Flow angle (concrete).
        d_angle: Dilation angle (concrete).
        use_ct_def: Whether to use CT definition for confinement
            (rebar, ``"Yes"`` / ``"No"``).
    """

    ss_curve_opt: str = "Simple"
    ss_hys_type: str = "Kinematic"
    s_hard: Optional[float] = None
    s_fc: Optional[float] = None
    s_cap: Optional[float] = None
    s_max: Optional[float] = None
    s_rup: Optional[float] = None
    final_slope: Optional[float] = None
    coup_mod_type: str = "Von Mises"
    f_angle: Optional[float] = None
    d_angle: Optional[float] = None
    use_ct_def: bool = False


@dataclass
class Material:
    """Material properties from SAP2000, including all tables."""

    name: str
    type: str  # "Steel", "Concrete", "Rebar", "Tendon", etc.
    grade: Optional[str] = None
    E_mod: float = 0.0  # Young's modulus (Pa)
    G_mod: float = 0.0  # Shear modulus (Pa)
    nu: float = 0.0  # Poisson's ratio
    unit_weight: float = 0.0  # N/m³
    unit_mass: float = 0.0  # kg/m³
    Fy: Optional[float] = None  # Nominal yield strength (steel, rebar, tendon) – Pa
    Fu: Optional[float] = None  # Nominal ultimate strength – Pa
    Fc: Optional[float] = None  # Concrete unconfined compressive strength – Pa
    eFc: Optional[float] = None  # Confined compressive strength (fcc') – Pa (NOT strain)
    # Effective yield / ultimate from the 03A / 03E tables (used for design
    # or capacity curves — distinct from nominal Fy/Fu).
    eff_Fy: Optional[float] = None
    eff_Fu: Optional[float] = None
    # Hysteretic stress-strain curve parameters (from MATERIAL PROPERTIES 03X)
    ss_curve: Optional[StressStrainCurve] = None
    extra: dict[str, Any] = field(default_factory=dict)  # all other properties


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

    name: str  # Section name (SAP2000 label)
    shape: str  # Original SAP2000 shape name e.g. "I/Wide Flange"
    material: str  # Reference to Material.name
    A: float = 0.0  # Cross-sectional area
    I33: float = 0.0  # Major-axis moment of inertia
    I22: float = 0.0  # Minor-axis moment of inertia
    J: float = 0.0  # Torsional constant
    # Plastic moduli (from manufacturer DB where available)
    Z33: Optional[float] = None
    Z22: Optional[float] = None
    # Extra
    manufacturer: Optional[str] = None
    # Stiffness modifiers from FRAME SECTION PROPERTIES 01 - GENERAL
    # (AMod, A2Mod, A3Mod, JMod, I2Mod, I3Mod — 1.0 = no modification)
    modifiers: dict[str, float] = field(default_factory=dict)

    @property
    def shape_id(self) -> str:
        """Canonical shape identifier (see SHAPE_NAMES)."""
        return SHAPE_NAMES.get(self.shape, "GEN")

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
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
        raise NotImplementedError(f"Fiber conversion not implemented for {type(self).__name__}")


# --- Shape‑specific subclasses -------------------------------------------------


@dataclass
class ISection(Section):
    """I / Wide-flange section with equal flanges.

    OpenSees fiber representation: bottom flange → web → top flange,
    all as rectangular patches.
    """

    depth: float = 0.0  # Overall depth D
    bf: float = 0.0  # Flange width B
    tf: float = 0.0  # Flange thickness
    tw: float = 0.0  # Web thickness

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
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

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        raise NotImplementedError(
            "Fiber conversion requires a known shape type "
            "(I, Pipe, Box, etc.), not a General section"
        )


@dataclass
class PipeSection(Section):
    """Circular hollow section / pipe (CHS)."""

    od: float = 0.0  # Outer diameter
    t: float = 0.0  # Wall thickness

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Annular ring via ``patch circ``."""
        R = self.od / 2.0
        return [
            ("circ", mat_tag, nfy, nfz, 0.0, 0.0, max(0.0, R - self.t), R, 0.0, 360.0),
        ]


@dataclass
class BoxSection(Section):
    """Rectangular hollow section / box / tube (RHS)."""

    depth: float = 0.0  # D
    bf: float = 0.0  # B
    tf: float = 0.0  # Flange (top/bottom) thickness
    tw: float = 0.0  # Web (left/right) thickness

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
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

    depth: float = 0.0  # D
    bf: float = 0.0  # B

    def to_fiber_patches(self, mat_tag: int, nfy: int = 12, nfz: int = 6) -> list[tuple]:
        """Fiber patches: confined concrete core + steel reinforcement bars.

        Material tag convention:
            mat_tag     → unconfined concrete (Concrete01)
            mat_tag + 1 → confined concrete (Concrete01)
            mat_tag + 2 → steel rebar (Steel02)

        Steel reinforcement is set to 0.6 % of the gross cross-sectional
        area, distributed equally between top and bottom layers with a
        cover of 40 mm.
        """
        d, b = self.depth, self.bf
        cv = 0.04  # 40 mm clear cover
        half_d, half_b = d / 2.0, b / 2.0
        core_y1, core_y2 = -half_d + cv, half_d - cv
        core_z1, core_z2 = -half_b + cv, half_b - cv

        patches: list[tuple] = [
            ("rect", mat_tag + 1, nfy, nfz, core_y1, core_z1, core_y2, core_z2),
        ]
        # Unconfined cover patches (top, bottom, left, right)
        if cv > 0:
            patches.extend(
                [
                    ("rect", mat_tag, 1, nfz, -half_d, core_z1, core_y1, core_z2),
                    ("rect", mat_tag, 1, nfz, core_y2, core_z1, half_d, core_z2),
                    ("rect", mat_tag, nfy, 1, core_y1, -half_b, core_y2, core_z1),
                    ("rect", mat_tag, nfy, 1, core_y1, core_z2, core_y2, half_b),
                ]
            )

        # Steel reinforcement at 0.6 % of gross area
        total_steel = 0.006 * d * b
        n_bars = max(4, round(b * 2 / 0.15))  # ≈150 mm spacing
        bar_area = total_steel / n_bars if n_bars > 0 else 0.0
        if bar_area > 0:
            bar_dia = 2.0 * math.sqrt(bar_area / math.pi)
            y_top = -half_d + cv
            y_bot = half_d - cv
            top_bars = (n_bars + 1) // 2  # ceil for odd counts
            bot_bars = n_bars // 2  # floor for odd counts
            patches.append(
                (
                    "straight",
                    mat_tag + 2,
                    top_bars,
                    bar_dia,
                    y_top,
                    -half_b + cv,
                    y_top,
                    half_b - cv,
                )
            )
            patches.append(
                (
                    "straight",
                    mat_tag + 2,
                    bot_bars,
                    bar_dia,
                    y_bot,
                    -half_b + cv,
                    y_bot,
                    half_b - cv,
                )
            )

        return patches


@dataclass
class CircularSection(Section):
    """Solid circular section / rod."""

    diameter: float = 0.0

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Solid circle via ``patch circ`` with zero inner radius."""
        R = self.diameter / 2.0
        return [
            ("circ", mat_tag, nfy, nfz, 0.0, 0.0, 0.0, R, 0.0, 360.0),
        ]


@dataclass
class ConcreteRectangularSection(Section):
    """Reinforced concrete rectangular section.

    SAP2000 shape: ``Concrete Rectangular``

    Confinement fields (``tie_diameter``, ``tie_spacing``, ``tie_fy``,
    ``tie_config``) describe the transverse reinforcement and feed the
    Mander et al. (1988) confinement model via
    :meth:`fiber_confinement`.  When any required tie field is missing
    (``None`` / ``<= 0``), no confinement modelling is applied and a
    conventional heuristic core (1.25–1.3 × f'c) is used by the builders.
    """

    depth: float = 0.0  # D (local y direction)
    bf: float = 0.0  # B (local z direction)
    cover: float = 0.0  # Clear cover to rebar centreline
    top_bars: int = 0
    bot_bars: int = 0
    top_bar_dia: float = 0.0
    bot_bar_dia: float = 0.0
    # SAP2000 longitudinal rebar material name from
    # "FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN" / 03 - CONCRETE BEAM
    # (``RebarMatL``).  None → framework uses rebar-specific SI defaults
    # (DEFAULT_FY_REBAR_PA / DEFAULT_E_S_PA) scaled to model units.
    rebar_material: Optional[str] = None

    # ── Transverse (tie/hoop) reinforcement — Mander confinement ──
    # All geometric values are in model units; ``tie_fy`` is in model
    # stress units.  From "FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN"
    # (``TieSizeL`` / ``TieSpacingT`` / ``RebarMatT``) when available.
    tie_diameter: Optional[float] = None  # tie bar diameter
    tie_spacing: Optional[float] = None  # centre-to-centre spacing
    tie_fy: Optional[float] = None  # tie yield strength
    # SAP2000 transverse rebar material name (``RebarMatT``).  Used to
    # resolve ``tie_fy`` when not provided directly.  None → framework
    # falls back to the section's longitudinal ``rebar_material``.
    tie_rebar_mat: Optional[str] = None
    tie_config: str = "standard"  # "standard" | "cross_tie" | "spiral"
    ecu_max: float = 0.025  # cap on Mander spalling (ecu)
    # Longitudinal bar counts for the Mander effective-confinement
    # coefficient ``ke``.  ``long_count_x`` runs along the width (bf),
    # ``long_count_y`` along the depth (D).  0 = derive from top/bot bars.
    long_count_x: int = 0
    long_count_y: int = 0

    def fiber_confinement(self, fc: float, tie_fy: float) -> Optional[dict[str, float]]:
        """Compute Mander confined-core properties for this section.

        Uses the Mander et al. (1988) model implemented in
        :mod:`fea_toolkit.model.confinement`.  All quantities use the
        same (model) unit system — the Mander engine is unit-agnostic as
        long as stress, length and strain inputs are internally
        consistent.

        Args:
            fc: Unconfined concrete compressive strength (model units).
            tie_fy: Transverse (tie) rebar yield strength (model units).

        Returns:
            Dict with keys ``fcc`` (confined strength), ``ecc`` (strain at
            confined peak) and ``ecu`` (ultimate/spalling strain) when
            complete tie data is present and geometrically valid, else
            ``None`` to signal that the caller should fall back to a
            conventional heuristic core.
        """
        if not (
            self.tie_diameter
            and self.tie_diameter > 0
            and self.tie_spacing
            and self.tie_spacing > 0
            and tie_fy
            and tie_fy > 0
        ):
            return None
        # Core dimensions to the centreline of the perimeter hoop.
        # Mander uses centreline-to-centreline of the tie, i.e. clear
        # cover + one tie diameter.
        core_bc = self.bf - 2.0 * self.cover - self.tie_diameter
        core_dc = self.depth - 2.0 * self.cover - self.tie_diameter
        if core_bc <= 0 or core_dc <= 0:
            return None
        from .confinement import ConfinementData, mander_confined

        lcx = self.long_count_x or self.top_bars or 0
        # Depth-direction bar count.  An unset long_count_y defaults to 2
        # (a single pair of bars along the depth) rather than bot_bars,
        # which would over-count the bars running along the width face.
        lcy = self.long_count_y or 2
        try:
            data = ConfinementData(
                fc=fc,
                tie_diameter=self.tie_diameter,
                tie_spacing=self.tie_spacing,
                tie_fy=tie_fy,
                core_bc=core_bc,
                core_dc=core_dc,
                long_diameter=max(self.top_bar_dia or 0.0, self.bot_bar_dia or 0.0),
                long_count_x=max(lcx, 0),
                long_count_y=max(lcy, 0),
                tie_config=self.tie_config or "standard",
                ecu_max=self.ecu_max,
            )
            res = mander_confined(data)
        except ValueError:
            return None
        return {"fcc": res.fcc, "ecc": res.ecc, "ecu": res.ecu}

    def to_fiber_patches(self, mat_tag: int, nfy: int = 12, nfz: int = 6) -> list[tuple]:
        """Fiber patches: confined core + unconfined cover + rebar layers.

        Material tag convention:
            mat_tag     → unconfined concrete (Concrete01)
            mat_tag + 1 → confined concrete (Concrete01)
            mat_tag + 2 → steel rebar (Steel02)
        """
        d, b = self.depth, self.bf
        cv = self.cover
        if cv < 0:
            raise ValueError(f"Negative cover ({cv}) in section {self.name}")
        half_d, half_b = d / 2.0, b / 2.0
        if cv >= half_d or cv >= half_b:
            raise ValueError(
                f"Cover ({cv}) exceeds half-dimension in section {self.name}: "
                f"half_d={half_d}, half_b={half_b}"
            )
        core_y1, core_y2 = -half_d + cv, half_d - cv
        core_z1, core_z2 = -half_b + cv, half_b - cv

        patches: list[tuple] = [
            ("rect", mat_tag + 1, nfy, nfz, core_y1, core_z1, core_y2, core_z2),
        ]
        # Unconfined cover patches — only emit when cover > 0 to avoid
        # zero-area degenerate patches.
        if cv > 0:
            patches.append(
                ("rect", mat_tag, nfy, 1, core_y2, -half_b, half_d, half_b),
            )
            patches.append(
                ("rect", mat_tag, nfy, 1, -half_d, -half_b, core_y1, half_b),
            )
        if cv > 0 and core_y2 > core_y1:
            patches.append(
                ("rect", mat_tag, 1, max(1, nfz - 2), core_y1, -half_b, core_y2, core_z1)
            )
            patches.append(("rect", mat_tag, 1, max(1, nfz - 2), core_y1, core_z2, core_y2, half_b))
        # Rebar layers — convert diameter to cross-sectional area
        if self.top_bars and self.top_bar_dia > 0:
            area_bar = math.pi * (self.top_bar_dia / 2.0) ** 2
            patches.append(
                (
                    "straight",
                    mat_tag + 2,
                    self.top_bars,
                    area_bar,
                    half_d - cv,
                    -half_b + cv,
                    half_d - cv,
                    half_b - cv,
                )
            )
        if self.bot_bars and self.bot_bar_dia > 0:
            area_bar = math.pi * (self.bot_bar_dia / 2.0) ** 2
            patches.append(
                (
                    "straight",
                    mat_tag + 2,
                    self.bot_bars,
                    area_bar,
                    -half_d + cv,
                    -half_b + cv,
                    -half_d + cv,
                    half_b - cv,
                )
            )
        return patches


@dataclass
class ConcreteCircularSection(Section):
    """Reinforced concrete circular section.

    SAP2000 shape: ``Concrete Circular``

    Confinement fields (``tie_diameter``, ``tie_spacing``, ``tie_fy``,
    ``tie_config``) describe the spiral/circular hoop reinforcement and
    feed the Mander et al. (1988) confinement model via
    :meth:`fiber_confinement`.  When any required tie field is missing,
    no confinement modelling is applied and a conventional heuristic
    core (1.25–1.3 × f'c) is used by the builders.
    """

    diameter: float = 0.0
    cover: float = 0.0
    bar_count: int = 0
    bar_dia: float = 0.0
    # SAP2000 longitudinal rebar material name (``RebarMatL``) — see
    # :class:`ConcreteRectangularSection`.
    rebar_material: Optional[str] = None

    # ── Transverse (spiral/hoop) reinforcement — Mander confinement ──
    # Geometric values in model units; ``tie_fy`` in model stress units.
    tie_diameter: Optional[float] = None  # spiral/hoop bar diameter
    tie_spacing: Optional[float] = None  # centre-to-centre pitch
    tie_fy: Optional[float] = None  # transverse yield strength
    # SAP2000 transverse rebar material name (``RebarMatT``).  Used to
    # resolve ``tie_fy`` when not provided directly.  None → framework
    # falls back to the section's longitudinal ``rebar_material``.
    tie_rebar_mat: Optional[str] = None
    tie_config: str = "spiral"  # "spiral" | "standard" | "cross_tie"
    ecu_max: float = 0.025  # cap on Mander spalling (ecu)

    def fiber_confinement(self, fc: float, tie_fy: float) -> Optional[dict[str, float]]:
        """Compute Mander confined-core properties for this section.

        Uses the Mander et al. (1988) model implemented in
        :mod:`fea_toolkit.model.confinement`.  For circular sections the
        ``"spiral"`` configuration is used (perimeter spiral or circular
        hoops).

        Args:
            fc: Unconfined concrete compressive strength (model units).
            tie_fy: Transverse (spiral/hoop) rebar yield strength (model
                units).

        Returns:
            Dict with keys ``fcc``, ``ecc`` and ``ecu`` when complete tie
            data is present and geometrically valid, else ``None``.
        """
        if not (
            self.tie_diameter
            and self.tie_diameter > 0
            and self.tie_spacing
            and self.tie_spacing > 0
            and tie_fy
            and tie_fy > 0
        ):
            return None
        core_d = self.diameter - 2.0 * self.cover - self.tie_diameter
        if core_d <= 0:
            return None
        from .confinement import ConfinementData, mander_confined

        try:
            data = ConfinementData(
                fc=fc,
                tie_diameter=self.tie_diameter,
                tie_spacing=self.tie_spacing,
                tie_fy=tie_fy,
                core_bc=core_d,
                core_dc=core_d,
                long_diameter=self.bar_dia or 0.0,
                long_count_x=self.bar_count or 0,
                long_count_y=self.bar_count or 0,
                tie_config=self.tie_config or "spiral",
                ecu_max=self.ecu_max,
            )
            res = mander_confined(data)
        except ValueError:
            return None
        return {"fcc": res.fcc, "ecc": res.ecc, "ecu": res.ecu}

    def to_fiber_patches(self, mat_tag: int, nfy: int = 12, nfz: int = 6) -> list[tuple]:
        """Fiber patches: confined core ring + unconfined cover + rebar ring."""
        R = self.diameter / 2.0
        cv = self.cover
        if cv < 0:
            raise ValueError(f"Negative cover ({cv}) in section {self.name}")
        if cv >= R:
            raise ValueError(f"Cover ({cv}) exceeds radius ({R}) in section {self.name}")
        R_core = max(0.0, R - cv)
        patches: list[tuple] = [
            ("circ", mat_tag + 1, nfy, nfz, 0.0, 0.0, 0.0, R_core, 0.0, 360.0),
        ]
        # Cover ring — only emit when cover > 0 to avoid degenerate zero-area patch.
        if cv > 0:
            patches.append(
                ("circ", mat_tag, nfy, nfz, 0.0, 0.0, R_core, R, 0.0, 360.0),
            )
        if self.bar_count and self.bar_dia > 0:
            area_bar = math.pi * (self.bar_dia / 2.0) ** 2
            R_rebar = R - self.cover
            patches.append(
                ("circ_layer", mat_tag + 2, self.bar_count, area_bar, 0.0, 0.0, R_rebar, 0.0, 360.0)
            )
        return patches


@dataclass
class ChannelSection(Section):
    """Channel / C‑section."""

    depth: float = 0.0  # D
    bf: float = 0.0  # B
    tf: float = 0.0  # Flange thickness
    tw: float = 0.0  # Web thickness

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Placeholder — channel patches not yet implemented."""
        raise NotImplementedError("Fiber conversion for ChannelSection not yet implemented")


@dataclass
class AngleSection(Section):
    """Single angle section (L)."""

    depth: float = 0.0  # D
    bf: float = 0.0  # B
    tf: float = 0.0  # Flange thickness
    tw: float = 0.0  # Web thickness

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Placeholder — angle patches not yet implemented."""
        raise NotImplementedError("Fiber conversion for AngleSection not yet implemented")


@dataclass
class DoubleAngleSection(Section):
    """Double angle section (2L)."""

    depth: float = 0.0  # D
    bf: float = 0.0  # B (overall width including gap)
    tf: float = 0.0  # Flange thickness
    tw: float = 0.0  # Web thickness
    dis: float = 0.0  # Gap between angles

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Placeholder — double‑angle patches not yet implemented."""
        raise NotImplementedError("Fiber conversion for DoubleAngleSection not yet implemented")


@dataclass
class TeeSection(Section):
    """Tee section (T)."""

    depth: float = 0.0  # D
    bf: float = 0.0  # B
    tf: float = 0.0  # Flange thickness
    tw: float = 0.0  # Web (stem) thickness

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Placeholder — tee patches not yet implemented."""
        raise NotImplementedError("Fiber conversion for TeeSection not yet implemented")


@dataclass
class SDSection(Section):
    """Section Designer section — may be composite, with multiple materials.

    Each polygon is a closed loop of (y, z) coordinates associated with a
    material.  For composite sections the list holds contributions from
    steel, concrete, rebar etc.
    """

    polygons: list[tuple[str, list[tuple[float, float]]]] = field(default_factory=list)
    # Each tuple: (material_name, [(y1, z1), (y2, z2), ...])

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Placeholder — SD polygons require triangulation / meshing."""
        raise NotImplementedError(
            "Fiber conversion for SD sections requires polygon meshing — not yet implemented"
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

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        """Placeholder — encased sections need steel + concrete patches."""
        raise NotImplementedError("Fiber conversion for EncasedSection not yet implemented")


@dataclass
class ShellSection(Section):
    """Shell / area section (2‑D)."""

    thickness: float = 0.0
    # SAP2000 area-section rebar material name from
    # "AREA SECTION PROPERTY DESIGN PARAMETERS" (``RebarMat``).  None →
    # framework defaults apply for nonlinear wall/slab analysis.
    rebar_material: Optional[str] = None

    def to_fiber_patches(self, mat_tag: int, nfy: int = 8, nfz: int = 4) -> list[tuple]:
        raise NotImplementedError("Fiber conversion is not applicable to ShellSection")


# ── Element creation properties (resolved by Preprocessor) ─────────────


@dataclass
class FrameElementProperties:
    """Resolved element creation parameters for a single frame element.

    Populated by the Preprocessor from config (three-level resolution:
    per-ID override → selection group → role default), consumed by the
    AnalysisBuilder when creating OpenSees elements.

    Args:
        element_type: OpenSees element command name, e.g.
            ``"elasticBeamColumn"``, ``"nonlinearBeamColumn"``,
            ``"dispBeamColumn"``, ``"forceBeamColumn"``, ``"truss"``.
        material_strategy: Controls section/material creation.
            ``"elastic"``, ``"fiber_steel"``, ``"fiber_rc"``,
            ``"steel02"``.
        integration_type: Integration rule for beam-column elements.
            ``None`` / ``"Lobatto"``, ``"Legendre"``, ``"Radau"``,
            ``"NewtonCotes"``, ``"HingeRadau"``, ``"HingeMidpoint"``,
            ``"HingeRadauTwo"``, ``"UserHinge"``.
        num_integration_points: Number of integration points.
            0 = use element default (3 for elastic, 4-5 for fiber).
        hinge_params: Dict of hinge parameters, e.g.
            ``{"lpI": 0.1, "lpJ": 0.1}`` for hinge plasticity models.
    """

    element_type: str = "elasticBeamColumn"
    material_strategy: str = "elastic"
    integration_type: Optional[str] = None
    num_integration_points: int = 0
    hinge_params: Optional[dict[str, float]] = None


@dataclass
class AreaElementProperties:
    """Resolved element creation parameters for a single area element.

    Populated by the Preprocessor from config (three-level resolution:
    per-ID override → selection group → role default), consumed by the
    AnalysisBuilder when creating OpenSees shell elements.

    Args:
        element_type: OpenSees shell element command, or ``None`` for
            loads-only (no element created, mass still added).
            ``"ShellMITC4"``, ``"ShellDKGQ"``, ``"ShellNLDKGQ"``,
            ``None``.
        material_strategy: Controls section/material creation.
            ``"elastic"``, ``"layered_rc"``, ``"layered_steel"``.
        thickness: Override shell thickness (model units).
            ``None`` = use value from SAP section properties.
        nd_material_names: References into the ``nd_materials`` dict
            for this area's layered section (only used when
            ``material_strategy`` is ``"layered_rc"`` /
            ``"layered_steel"``).
        layer_stack: Direct list of :class:`ShellFiberLayer` objects,
            overriding ``nd_material_names`` if both are present.
        layered_section_group_key: Key used to look up the layered section
            group configuration (e.g. from the element property config or
            area ID). Only relevant when ``material_strategy`` is
            ``"layered_rc"`` / ``"layered_steel"``.
    """

    element_type: Optional[str] = "ShellMITC4"
    material_strategy: str = "elastic"
    thickness: Optional[float] = None
    nd_material_names: list[str] = field(default_factory=list)
    layer_stack: list["ShellFiberLayer"] = field(default_factory=list)
    layered_section_group_key: Optional[str] = None


@dataclass
class NDMaterial:
    """Multi‑axial (nD) material for nonlinear shell analysis.

    These are used with ``LayeredShellFiberSection`` in Xara/OpenSeesRT.

    Args:
        name: Unique material name.
        material_type: ``"ElasticIsotropic"``, ``"J2PlateFibre"``,
            ``"ConcreteS"``, or ``"PlateFromPlaneStress"``.
        E: Young's modulus (Pa).
        nu: Poisson's ratio.
        fy: Yield stress for J2PlateFibre (Pa).
        Hiso: Isotropic hardening modulus for J2PlateFibre.
        Hkin: Kinematic hardening modulus for J2PlateFibre.
        fc: Compressive strength for ConcreteS (Pa, positive).
        ft: Tensile strength for ConcreteS (Pa).
        Es: Steel rebar stiffness for ConcreteS (0 = plain concrete).
    """

    name: str
    material_type: str = "ElasticIsotropic"
    E: float = 200.0e9
    nu: float = 0.3
    fy: float = 400.0e6
    Hiso: float = 0.0
    Hkin: float = 0.0
    fc: float = 30.0e6
    ft: float = 3.0e6
    Es: float = 0.0

    def to_tcl(self, tag: int, wrapper_tag: int = 0) -> str:
        """Return the Tcl command to create this nD material in OpenSees.

        Args:
            tag: Integer tag for this material.
            wrapper_tag: For ``PlateFromPlaneStress``, the tag for the
                wrapper section (distinct from the plane-stress material
                tag).  Ignored for other types.

        Returns:
            Tcl command string.
        """
        t = self.material_type
        if t == "ElasticIsotropic":
            return f"nDMaterial ElasticIsotropic {tag} {self.E:g} {self.nu:g}"
        if t == "J2PlateFibre":
            return (
                f"nDMaterial J2PlateFibre {tag} {self.E:g} {self.nu:g}"
                f" {self.fy:g} {self.Hiso:g} {self.Hkin:g}"
            )
        if t == "ConcreteS":
            return (
                f"nDMaterial ConcreteS {tag} {self.E:g} {self.nu:g}"
                f" {self.fc:g} {self.ft:g} {self.Es:g}"
            )
        if t == "PlateFromPlaneStress":
            src = wrapper_tag or tag
            return f"nDMaterial PlateFromPlaneStress {tag} {src} 0.0"
        return f"nDMaterial {t} {tag} {self.E:g} {self.nu:g}"


@dataclass
class ShellFiberLayer:
    """A single layer in a ``LayeredShellFiberSection``.

    Args:
        thickness: Layer thickness (same units as model).
        nd_material: Name of the :class:`NDMaterial` for this layer.
        n_ip: Number of integration points through this layer (default 4).
    """

    thickness: float
    nd_material: str
    n_ip: int = 4


@dataclass
class LayeredShellSection:
    """Layered shell section for nonlinear shear wall analysis.

    Corresponds to ``LayeredShellFiberSection`` in Xara/OpenSeesRT.
    Each layer is a :class:`ShellFiberLayer` with an nD material reference.

    Usage::

        section = LayeredShellSection(
            name="WallSec",
            layers=[
                ShellFiberLayer(0.15, "Concrete"),
                ShellFiberLayer(0.01, "Rebar"),
                ShellFiberLayer(0.15, "Concrete"),
            ],
        )
    """

    name: str
    layers: list[ShellFiberLayer] = field(default_factory=list)

    def to_tcl(self, tag: int, mat_tags: dict[str, int]) -> str:
        """Return the Tcl command to create this layered shell section.

        Emits ``section LayeredShell <tag> <nLayers>`` followed by
        ``<matTag> <thickness>`` for each layer.

        .. note::

           The OpenSees ``LayeredShell`` section syntax accepts only
           ``(matTag, thickness)`` per layer — the ``nIP`` field on
           :class:`ShellFiberLayer` is metadata for display purposes only.

        Args:
            tag: Integer tag for the section.
            mat_tags: Dict mapping NDMaterial name → integer tag.

        Returns:
            Tcl ``section LayeredShell ...`` command string.

        Raises:
            KeyError: If any layer's ``nd_material`` is not in
                *mat_tags*.
        """
        parts = [f"section LayeredShell {tag} {len(self.layers)}"]
        for layer in self.layers:
            mt = mat_tags[layer.nd_material]  # fail hard on missing material
            parts.append(f"{mt} {layer.thickness:g}")
        return "   ".join(parts)


# --- Non‑section dataclasses ---------------------------------------------------


@dataclass
class FrameElement:
    """1D frame element connectivity.

    ``cardinal_point`` is the SAP2000/ETABS insertion point (1–11):

    =====  ===============
    Value  Position
    =====  ===============
    1      Bottom left
    2      Bottom centre
    3      Bottom right
    4      Middle left
    5      Middle centre
    6      Middle right
    7      Top left
    8      Top centre
    9      Top right
    10     Centroid (default)
    11     Shear centre
    =====  ===============
    """

    elem_id: str  # SAP2000 frame label
    elem_tag: int  # numeric tag for OpenSees etc
    node_i: str
    node_j: str
    angle: float = 0.0  # Rotation about local x‑axis (degrees)
    inactive: bool = False
    parent_id: Optional[str] = None
    child_ids: list[str] = field(default_factory=list)
    t_locations: list[float] = field(
        default_factory=list
    )  # parametric positions 0..1 where split occurs
    cardinal_point: int = (
        10  # Insertion point per SAP2000/ETABS (1-11; 10=centroid, 5=middle center)
    )


@dataclass
class AreaElement:
    """2D shell/area element connectivity.

    ``inactive`` is set to ``True`` when the super-element has been
    subdivided into mesh sub-elements.

    ``parent_id`` / ``child_ids`` track the subdivision hierarchy
    (mirroring :class:`FrameElement`).  The parent is the original
    super-element; children are the mesh sub-elements created by
    :func:`~fea_toolkit.model.geometry.mesh_area_elements`.
    """

    area_id: str
    area_tag: int
    node_ids: list[str]  # ordered corner nodes
    thickness: float = 0.0
    inactive: bool = False  # True when superseded by mesh sub‑elements
    parent_id: Optional[str] = None
    child_ids: list[str] = field(default_factory=list)


@dataclass
class Group:
    """Named group of objects."""

    name: str
    color: Optional[str] = None
    objects: list[str] = field(default_factory=list)  # "Frame:123", "Area:456", "Joint:1"


@dataclass
class LoadCase:
    """SAP2000 load case."""

    case_name: str
    case_type: str
    design_type_option: str  # "Prog Det"
    design_type: str  # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    design_action_option: str  # "Prog Det"
    design_action: str  # 'Non-Composite', 'Long-Term Composite', 'Short-Term Composite', etc.
    initial_condition: str = "Zero"
    modal_case: str = ""
    run_case: bool = False
    case_data: dict[str, Any] = field(
        default_factory=dict
    )  # "CASE - MODAL ..." or "CASE - RESPONSE SPECTRUM ..." etc


@dataclass
class LoadPattern:
    """SAP2000 load pattern."""

    name: str
    pattern_type: str  # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    self_weight_factor: float = 0.0
    auto_data: dict[str, Any] = field(default_factory=dict)  # data from AUTO* tables


@dataclass
class LoadCombination:
    """SAP2000 load combination."""

    name: str
    combo_type: str  # 'DEAD', 'LIVE', 'SUPERDEAD', 'WIND', 'QUAKE', etc.
    cases: dict[str, float] = field(default_factory=dict)
    design: dict[str, str] = field(default_factory=dict)


@dataclass
class MassSource:
    """SAP2000 mass source definition."""

    name: str
    elements: bool = False
    masses: bool = False
    loads: bool = False
    is_default: bool = False
    load_pattern: dict[str, float] = field(default_factory=dict)


@dataclass
class JointLoad:
    """ "JOINT LOADS - FORCE" : Concentrated load at a joint."""

    pattern: str  # name of the load pattern
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

    pattern: str  # load pattern name
    area_id: str  # area element ID
    coord_sys: str = "GLOBAL"  # 'GLOBAL' or 'Local'
    direction: str = "Gravity"  # 'Gravity', 'X', 'Y', 'Z'
    value: float = 0.0  # pressure (force/area)


@dataclass
class GravityLoad:
    """# "FRAME LOADS - GRAVITY"
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

    pattern: str  # name of the load pattern
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
    direction: str  # 'Gravity', 'Projected', 'LocalX', etc.
    load_type: str  # 'Force' or 'Moment'
    shape: str  # 'Uniform', 'Linear', 'Trapezoidal'
    val_a: float  # intensity at start (force/length)
    val_b: float  # intensity at end
    rdist_a: float  # relative distance from start
    rdist_b: float  # relative distance from start
    dist_a: float  # absolute distance from start (in model units)
    dist_b: float  # absolute distance from start
    coord_sys: str = "GLOBAL"


# ═══════════════════════════════════════════════════════════════════════
# Legacy unit-conversion helpers (deprecated)
# ═══════════════════════════════════════════════════════════════════════
# The canonical unit-conversion functions live in ``fea_toolkit.utils``
# (``*_scale_factor`` for SI→model, ``*_to_si_factor`` for model→SI).
# The aliases below accept a bare unit string for backward compatibility
# and emit a ``DeprecationWarning`` to guide callers to the unified API.


def _normalise_length_unit(lu: str) -> str:
    """[Deprecated] Normalise a length unit string to canonical short form.

    Use :func:`fea_toolkit.utils._normalise_unit` instead.
    """
    import warnings

    warnings.warn(
        "_normalise_length_unit is deprecated; use "
        "fea_toolkit.utils._normalise_unit(raw, 'm') instead",
        DeprecationWarning,
        stacklevel=2,
    )
    from ..utils import _normalise_unit

    return _normalise_unit(lu, "m")


def _normalise_force_unit(fu: str) -> str:
    """[Deprecated] Normalise a force unit string to canonical short form.

    Use :func:`fea_toolkit.utils._normalise_unit` instead.
    """
    import warnings

    warnings.warn(
        "_normalise_force_unit is deprecated; use "
        "fea_toolkit.utils._normalise_unit(raw, 'n') instead",
        DeprecationWarning,
        stacklevel=2,
    )
    from ..utils import _normalise_unit

    return _normalise_unit(fu, "n")


def _length_factor_from_units(lu: str) -> float:
    """[Deprecated] Factor to convert model length → metres.

    Use :func:`fea_toolkit.utils.length_to_si_factor` instead.
    """
    import warnings

    warnings.warn(
        "_length_factor_from_units is deprecated; use "
        "fea_toolkit.utils.length_to_si_factor(units) instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return length_to_si_factor({"L": lu})


def _force_factor_from_units(fu: str) -> float:
    """[Deprecated] Factor to convert model force → Newtons.

    Use :func:`fea_toolkit.utils.force_to_si_factor` instead.
    """
    import warnings

    warnings.warn(
        "_force_factor_from_units is deprecated; use "
        "fea_toolkit.utils.force_to_si_factor(units) instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return force_to_si_factor({"F": fu})


@dataclass
class SAPModelData:
    """Complete SAP2000 model data for export to OpenSees or Rhino."""

    nodes: dict[str, Node]
    restraints: dict[str, Restraint]
    materials: dict[str, Material]
    sections: dict[str, Section]
    frame_elements: dict[str, FrameElement]
    area_elements: dict[str, AreaElement]
    frame_assignments: dict[str, str]  # frame_id -> section_name
    area_assignments: dict[str, str]  # area_id -> section_name
    groups: dict[str, Group]
    frame_auto_mesh: dict[str, dict[str, Any]]  # frame_id -> auto mesh settings
    frame_end_offsets: dict[str, FrameEndOffset] = field(default_factory=dict)
    area_mesh: dict[str, AreaMesh] = field(default_factory=dict)
    area_edge_constraints: dict[str, list[AreaEdgeConstraint]] = field(default_factory=dict)
    # Loads (to be expanded later)
    load_cases: dict[str, LoadCase] = field(default_factory=dict)
    load_patterns: dict[str, LoadPattern] = field(default_factory=dict)
    joint_loads: list[JointLoad] = field(default_factory=list)
    frame_dist_loads: list[FrameDistributedLoad] = field(default_factory=list)
    area_uniform_loads: list[AreaUniformLoad] = field(default_factory=list)
    area_gravity_loads: list[AreaGravityLoad] = field(default_factory=list)
    frame_gravity_loads: list[GravityLoad] = field(default_factory=list)
    mass_sources: dict[str, MassSource] = field(default_factory=dict)
    # Multi-axial (nD) materials for nonlinear shell analysis
    nd_materials: dict[str, NDMaterial] = field(default_factory=dict)
    # Layered shell sections for nonlinear shear walls
    layered_shell_sections: dict[str, LayeredShellSection] = field(default_factory=dict)
    # Default units used for all coordinates and section properties
    units: dict[str, str] = field(default_factory=lambda: {"F": "N", "L": "m", "T": "C"})

    # ── Unit conversion factors ──────────────────────────────────────────
    # These are computed from self.units and provide a consistent way to
    # convert between model units and SI for code-based formulae.
    #
    # The properties are thin wrappers around the canonical conversion
    # functions in ``fea_toolkit.utils``:
    #
    #   value_in_SI = value_in_model_units * md.<factor>
    #   value_in_model_units = value_in_SI / md.<factor>
    #
    # e.g. ``md.length_factor == utils.length_to_si_factor(md.units)``,
    # and ``md.weight_density_factor == 1.0 / weight_density_scale_factor(md.units)``.

    @property
    def length_factor(self) -> float:
        """Factor to convert model length → metres.

        ``value_in_m = value_in_model_units * length_factor``

        Wraps :func:`fea_toolkit.utils.length_to_si_factor`.
        """
        return length_to_si_factor(self.units)

    @property
    def force_factor(self) -> float:
        """Factor to convert model force → Newtons.

        ``value_in_N = value_in_model_units * force_factor``

        Wraps :func:`fea_toolkit.utils.force_to_si_factor`.
        """
        return force_to_si_factor(self.units)

    @property
    def stress_factor(self) -> float:
        """Factor to convert model stress → Pascals.

        ``value_in_Pa = value_in_model_units * stress_factor``

        Wraps :func:`fea_toolkit.utils.stress_to_si_factor`.
        """
        return stress_to_si_factor(self.units)

    @property
    def weight_density_factor(self) -> float:
        """Factor to convert model weight density → N/m³.

        ``value_in_N_per_m3 = value_in_model_units * weight_density_factor``

        Wraps :func:`fea_toolkit.utils.weight_density_to_si_factor`.
        """
        return weight_density_to_si_factor(self.units)

    @property
    def mass_density_factor(self) -> float:
        """Factor to convert model mass density → kg/m³.

        ``value_in_kg_per_m3 = value_in_model_units * mass_density_factor``

        Wraps :func:`fea_toolkit.utils.mass_density_to_si_factor`.
        """
        return mass_density_to_si_factor(self.units)

    @property
    def lineal_force_factor(self) -> float:
        """Factor to convert model force-per-length → N/m.

        ``value_in_N_per_m = value_in_model_units * lineal_force_factor``

        Wraps :func:`fea_toolkit.utils.lineal_force_to_si_factor`.
        """
        return lineal_force_to_si_factor(self.units)

    # ── Unit conversion convenience methods ─────────────────────────────

    def model_length_to_m(self, value: float) -> float:
        """Convert a value from model length units to metres."""
        return value * self.length_factor

    def model_force_to_n(self, value: float) -> float:
        """Convert a value from model force units to Newtons."""
        return value * self.force_factor

    def model_stress_to_pa(self, value: float) -> float:
        """Convert a value from model stress units to Pascals."""
        return value * self.stress_factor

    def m_to_model_length(self, value: float) -> float:
        """Convert a value from metres to model length units."""
        Lf = self.length_factor
        return value / Lf if Lf != 0 else value

    def n_to_model_force(self, value: float) -> float:
        """Convert a value from Newtons to model force units."""
        Ff = self.force_factor
        return value / Ff if Ff != 0 else value

    # ── Material defaults ───────────────────────────────────────────────

    def apply_material_defaults(self) -> None:
        """Fill missing material properties with SI defaults scaled to model units.

        Material-property defaults are authored in SI (Pa, N/m³, kg/m³)
        and scaled to the model's unit system by the canonical
        ``utils`` factors:

            model_value = SI_default * scale_factor(units)

        After calling this, all materials are guaranteed to have non-zero
        values for E_mod, Fy, Fc, unit_weight, unit_mass, etc.  Consumers
        can read these values directly — no fallback logic needed.
        """
        ssf = stress_scale_factor(self.units)  # Pa → model stress
        wdsf = weight_density_scale_factor(self.units)  # N/m³ → model W-density
        mdsf = mass_density_scale_factor(self.units)  # kg/m³ → model M-density

        for mat in self.materials.values():
            is_concrete = mat.type and mat.type.lower() == "concrete"
            mat.type and mat.type.lower() in ("steel", "rebar", "tendon")

            # E_mod — use concrete modulus for concrete, steel modulus otherwise
            if not mat.E_mod or mat.E_mod <= 0:
                if is_concrete:
                    mat.E_mod = DEFAULT_E_C_PA * ssf
                else:
                    mat.E_mod = DEFAULT_E_S_PA * ssf

            # Fy — use rebar default for rebar/tendon, steel default otherwise
            if not mat.Fy or mat.Fy <= 0:
                if mat.type and mat.type.lower() in ("rebar", "tendon"):
                    mat.Fy = DEFAULT_FY_REBAR_PA * ssf
                else:
                    mat.Fy = DEFAULT_FY_STEEL_PA * ssf

            # Fc (concrete compressive strength) — only for concrete materials
            if is_concrete and (not mat.Fc or mat.Fc <= 0):
                mat.Fc = DEFAULT_FC_PA * ssf

            # G_mod — derive from E_mod via Poisson's ratio if missing
            if not mat.G_mod or mat.G_mod <= 0:
                if mat.nu and abs(mat.nu) > 1e-12:
                    mat.G_mod = mat.E_mod / (2.0 * (1.0 + abs(mat.nu)))
                elif is_concrete:
                    mat.G_mod = DEFAULT_G_C_PA * ssf
                else:
                    mat.G_mod = DEFAULT_G_MOD_FRAC * mat.E_mod

            # unit_weight (N/m³ → model units)
            if not mat.unit_weight or abs(mat.unit_weight) < 1e-12:
                if is_concrete:
                    mat.unit_weight = DEFAULT_RHO_WC_SI * wdsf
                else:
                    mat.unit_weight = DEFAULT_RHO_WS_SI * wdsf

            # unit_mass (kg/m³ → model units)
            if not mat.unit_mass or abs(mat.unit_mass) < 1e-12:
                if is_concrete:
                    mat.unit_mass = DEFAULT_RHO_MC_SI * mdsf
                else:
                    mat.unit_mass = DEFAULT_RHO_MS_SI * mdsf

    # ── Utility methods ──────────────────────────────────────────

    def max_node_tag(self) -> int:
        """Return the maximum ``node_tag`` in the model, or 0 if empty."""
        return max((n.node_tag for n in self.nodes.values()), default=0)

    def auto_detect_static_cases(self) -> list[str]:
        """Return names of static (LinStatic) load cases from the model."""
        if not self.load_cases:
            return []
        cases = []
        for lc in (
            self.load_cases.values() if isinstance(self.load_cases, dict) else self.load_cases
        ):
            if getattr(lc, "case_type", "").lower() in ("linstatic", "static"):
                cases.append(getattr(lc, "case_name", str(lc)))
        return cases

    def summary_dict(self) -> dict[str, Any]:
        """Return a one‑row dict of model statistics."""
        xs = [n.x for n in self.nodes.values()]
        ys = [n.y for n in self.nodes.values()]
        zs = [n.z for n in self.nodes.values()]
        return {
            "Nodes": len(self.nodes),
            "Frames": len(self.frame_elements),
            "Areas": len(self.area_elements),
            "Materials": len(self.materials),
            "Sections": len(self.sections),
            "X span (m)": max(xs) - min(xs) if xs else 0,
            "Y span (m)": max(ys) - min(ys) if ys else 0,
            "Z span (m)": max(zs) - min(zs) if zs else 0,
            "Units": str(self.units),
        }
