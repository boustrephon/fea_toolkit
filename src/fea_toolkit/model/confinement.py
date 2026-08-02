"""
Confined concrete properties using the Mander et al. (1988) model.

Computes the confined compressive strength ``f'cc`` and corresponding
strain ``εcc`` from transverse reinforcement parameters (stirrup/hoop
spacing, diameter, yield strength, and core geometry).

References
----------
- Mander, J. B., Priestley, M. J. N., & Park, R. (1988).
  "Theoretical stress-strain model for confined concrete."
  *ASCE Journal of Structural Engineering*, 114(8), 1804–1826.
- Mander, J. B., Priestley, M. J. N., & Park, R. (1988).
  "Observed stress-strain behavior of confined concrete."
  *ASCE Journal of Structural Engineering*, 114(8), 1827–1849.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ConfinementData:
    """Input parameters for the Mander confinement model.

    Parameters
    ----------
    fc : float
        Unconfined concrete compressive strength (Pa).
    tie_diameter : float
        Diameter of transverse reinforcement (m).
    tie_spacing : float
        Centre-to-centre spacing of transverse reinforcement (m).
    tie_fy : float
        Yield strength of transverse reinforcement (Pa).
    core_bc : float
        Core dimension perpendicular to the x-axis (m),
        measured to the centreline of the perimeter hoop.
    core_dc : float
        Core dimension perpendicular to the y-axis (m),
        measured to the centreline of the perimeter hoop.
    long_diameter : float, optional
        Diameter of longitudinal bars (m).  Used to compute
        clear spacing between tied bars.
    long_count_x : int, optional
        Number of longitudinal bars along the x-axis.
    long_count_y : int, optional
        Number of longitudinal bars along the y-axis.
    tie_config : str
        ``"standard"`` (perimeter hoop only), ``"cross_tie"``
        (perimeter + cross-ties), or ``"spiral"`` (circular spiral
        or circular hoops).  Determines the effective confinement
        factor ``ke``.
    cover : float, optional
        Clear cover to the perimeter hoop (m).  Used to compute
        the centreline core dimensions from overall section
        dimensions when not provided directly.
    overall_b : float, optional
        Overall section width (m).  Used with *cover* to derive
        ``core_bc``.
    overall_h : float, optional
        Overall section depth (m).  Used with *cover* to derive
        ``core_dc``.
    cross_tie_count_x : int, optional
        Number of cross-tie legs in the x-direction (perimeter hoop
        excluded).  Used to compute ``Ash_x`` and ``rho_x``.
    cross_tie_count_y : int, optional
        Number of cross-tie legs in the y-direction (perimeter hoop
        excluded).  Used to compute ``Ash_y`` and ``rho_y``.
    eps_su : float, optional
        Ultimate strain of the transverse steel (default 0.1 for
        ASTM A706).  Used in the spalling/ultimate strain formula.
    ecu_max : float, optional
        Upper bound for the confined ultimate (spalling) strain
        (default 0.025).  The Priestley (1996) spalling formula can
        predict very large strains; NZSEE C5 uses 0.05.  The value
        is configurable.
    """

    fc: float
    tie_diameter: float
    tie_spacing: float
    tie_fy: float
    core_bc: float = 0.0
    core_dc: float = 0.0
    long_diameter: float = 0.0
    long_count_x: int = 0
    long_count_y: int = 0
    cross_tie_count_x: int = 0
    cross_tie_count_y: int = 0
    tie_config: str = "standard"
    cover: float = 0.0
    overall_b: float = 0.0
    overall_h: float = 0.0
    eps_su: float = 0.1
    """Ultimate strain of transverse steel (default 0.1 for ASTM A706)."""
    ecu_max: float = 0.025
    """Upper bound for the confined ultimate strain (default 0.025)."""

    def __post_init__(self) -> None:
        # Validate fundamental material/geometry parameters
        if self.fc <= 0:
            raise ValueError(f"fc must be > 0, got {self.fc}")
        if self.tie_fy <= 0:
            raise ValueError(f"tie_fy must be > 0, got {self.tie_fy}")
        if self.tie_diameter <= 0:
            raise ValueError(f"tie_diameter must be > 0, got {self.tie_diameter}")
        if self.tie_spacing < 0:
            raise ValueError(f"tie_spacing must be >= 0, got {self.tie_spacing}")
        if 0 < self.tie_spacing < self.tie_diameter:
            raise ValueError(
                f"tie_spacing ({self.tie_spacing:.4f}) is positive but smaller "
                f"than tie_diameter ({self.tie_diameter:.4f})"
            )
        # Validate tie_config
        valid_configs = {"standard", "cross_tie", "spiral"}
        if self.tie_config not in valid_configs:
            raise ValueError(
                f"Unsupported tie_config={self.tie_config!r}. "
                f"Must be one of {sorted(valid_configs)}"
            )
        # Validate cross-tie counts
        if self.cross_tie_count_x < 0:
            raise ValueError(f"cross_tie_count_x must be >= 0, got {self.cross_tie_count_x}")
        if self.cross_tie_count_y < 0:
            raise ValueError(f"cross_tie_count_y must be >= 0, got {self.cross_tie_count_y}")
        # Validate ecu_max
        if self.ecu_max <= 0:
            raise ValueError(f"ecu_max must be greater than 0, got {self.ecu_max}")
        # Validate eps_su
        if self.eps_su <= 0:
            raise ValueError(f"eps_su must be > 0, got {self.eps_su}")
        # For spiral, require compatible core dimensions
        if self.tie_config == "spiral" and (self.core_bc <= 0 or self.core_dc <= 0):
            raise ValueError(
                f"Spiral tie_config requires positive core_bc and core_dc; "
                f"got core_bc={self.core_bc}, core_dc={self.core_dc}"
            )
        # Derive core dimensions from overall + cover if not given directly
        if self.core_bc <= 0 and self.overall_b > 0:
            self.core_bc = self.overall_b - 2 * self.cover - self.tie_diameter
        if self.core_dc <= 0 and self.overall_h > 0:
            self.core_dc = self.overall_h - 2 * self.cover - self.tie_diameter
        # Validate derived dimensions
        if self.core_bc <= 0:
            raise ValueError(
                f"core_bc ≤ 0 ({self.core_bc:.3f}) after derivation — "
                f"check cover ({self.cover:.3f}), tie_diameter "
                f"({self.tie_diameter:.3f}), and overall_b "
                f"({self.overall_b:.3f})"
            )
        if self.core_dc <= 0:
            raise ValueError(
                f"core_dc ≤ 0 ({self.core_dc:.3f}) after derivation — "
                f"check cover ({self.cover:.3f}), tie_diameter "
                f"({self.tie_diameter:.3f}), and overall_h "
                f"({self.overall_h:.3f})"
            )


@dataclass
class ConfinementResult:
    """Output of the Mander confinement model.

    Attributes
    ----------
    fcc : float
        Confined compressive strength (Pa).
    ecc : float
        Strain at confined peak stress.
    ecu : float
        Ultimate (spalling) strain of confined concrete.
        Typically 0.004 for unconfined cover, 0.012–0.025
        for well-confined core.
    ke : float
        Effective confinement coefficient.
    rho_s : float
        Volumetric ratio of transverse reinforcement.
    f_l : float
        Effective lateral confining stress (Pa).
    """

    fcc: float
    ecc: float
    ecu: float = 0.02
    ke: float = 0.0
    rho_s: float = 0.0
    f_l: float = 0.0


def mander_confined(data: ConfinementData) -> ConfinementResult:
    """Compute confined concrete properties using Mander et al. (1988).

    Parameters
    ----------
    data : ConfinementData
        Confinement input parameters.

    Returns
    -------
    ConfinementResult
        Confined strength ``fcc``, strain ``ecc``, and intermediate
        quantities (``ke``, ``rho_s``, ``f_l``).
    """
    fc = data.fc
    fyh = data.tie_fy
    s = data.tie_spacing
    db = data.tie_diameter
    bc = data.core_bc
    dc = data.core_dc

    if s <= 0 or bc <= 0 or dc <= 0:
        # No confinement data — return unconfined properties
        return ConfinementResult(
            fcc=fc,
            ecc=0.002,
            ecu=0.004,
            ke=0.0,
            rho_s=0.0,
            f_l=0.0,
        )

    # Area of one transverse bar
    Ab = math.pi * db**2 / 4.0

    if data.tie_config == "spiral":
        # Circular section: spiral or circular hoops
        # Core diameter (to hoop centreline)
        Ds = bc  # core diameter
        # Volumetric ratio for spirals: rho_s = 4 * Ab / (s * Ds)
        rho_s = 4.0 * Ab / (s * Ds)
        # Effective confinement coefficient for circular
        # Mander Eq. 5-8: ke = (1 - s'/(2*Ds))^2 / (1 - rho_cc)
        # where rho_cc = Al / Ac (longitudinal / core area)
        #
        # For circular sections the caller stores the total number of
        # longitudinal bars around the ring in *both* long_count_x and
        # long_count_y (see ConcreteCircularSection.fiber_confinement).
        # The ring count is therefore the value of either field — taking
        # the product would count each bar twice (or 64 bars for an 8-bar
        # ring), inflating rho_cc and driving ke above 1.0.
        s_prime = s - db  # clear spacing
        rho_cc = 0.0
        if data.long_diameter > 0 and data.long_count_x > 0:
            Al = math.pi * data.long_diameter**2 / 4.0
            n_longs = data.long_count_x
            Ac = math.pi * Ds**2 / 4.0
            rho_cc = (n_longs * Al) / Ac if Ac > 0 else 0.0
        # Clamp ke to the physically admissible range (0, 1] — a value
        # above 1.0 would indicate an over-counted longitudinal ratio.
        _ke_raw = (
            ((1.0 - s_prime / (2.0 * Ds)) ** 2 / (1.0 - rho_cc)) if Ds > 0 and rho_cc < 1.0 else 0.0
        )
        ke = min(max(_ke_raw, 0.0), 1.0)
        # Effective lateral confining stress (factored by ke)
        f_l = ke * 0.5 * rho_s * fyh
    else:
        # Rectangular section: perimeter hoop ± cross-ties
        # Volumetric ratio of transverse steel
        # Total tie area in each direction
        Ash_x = 2.0 * Ab  # two legs in x-direction
        Ash_y = 2.0 * Ab  # two legs in y-direction
        # Add cross-ties if specified
        if data.tie_config == "cross_tie":
            Ash_x += data.cross_tie_count_x * Ab
            Ash_y += data.cross_tie_count_y * Ab
        # Volumetric ratios per direction
        rho_x = Ash_x / (s * dc) if dc > 0 else 0.0
        rho_y = Ash_y / (s * bc) if bc > 0 else 0.0
        rho_s = rho_x + rho_y

        # Lateral confining stress per direction
        f_lx = rho_x * fyh
        f_ly = rho_y * fyh

        # Effective confinement coefficient ke (Mander Eq. 5–8)
        s_prime = s - db  # clear spacing between hoops
        wi_x_sum = 0.0
        if data.long_count_x > 1 and data.long_diameter > 0:
            # Sum of (wi')² where wi' is clear spacing between
            # adjacent longitudinal bars along x-direction
            n_gaps_x = data.long_count_x - 1
            gap_x = (bc - data.long_diameter * data.long_count_x) / n_gaps_x
            wi_x_sum = n_gaps_x * gap_x**2
        wi_y_sum = 0.0
        if data.long_count_y > 1 and data.long_diameter > 0:
            n_gaps_y = data.long_count_y - 1
            gap_y = (dc - data.long_diameter * data.long_count_y) / n_gaps_y
            wi_y_sum = n_gaps_y * gap_y**2

        # Mander Eq. 5: ke = (1 - sum(wi²)/(6*bc*dc)) * (1 - s'/(2*bc)) * (1 - s'/(2*dc)) / (1 - rho_cc)
        # where rho_cc is the ratio of longitudinal reinforcement area to core area
        rho_cc = 0.0
        if data.long_diameter > 0 and data.long_count_x > 0 and data.long_count_y > 0:
            Al = math.pi * data.long_diameter**2 / 4.0
            n_longs = data.long_count_x * data.long_count_y
            rho_cc = (n_longs * Al) / (bc * dc) if bc * dc > 0 else 0.0

        term1 = 1.0 - wi_x_sum / (6.0 * bc * dc) - wi_y_sum / (6.0 * bc * dc)
        term2 = 1.0 - s_prime / (2.0 * bc) if bc > 0 else 0.0
        term3 = 1.0 - s_prime / (2.0 * dc) if dc > 0 else 0.0
        ke = term1 * term2 * term3 / (1.0 - rho_cc)

        # Effective lateral confining stress
        # For rectangular sections with unequal confinement in x and y,
        # Mander's full solution requires iterating on the triaxial failure
        # surface (Mander Eq. 2 with the biaxial interaction).  A common
        # conservative simplification (used by Priestley, Calvi & Kowalsky,
        # "Displacement-Based Seismic Design of Structures", 2007) takes
        # the minimum of the two direction stresses, since the weaker
        # direction governs the confined strength:
        f_l = ke * min(f_lx, f_ly) if ke > 0 else 0.0

    if ke <= 0:
        ke = 0.0
        f_l = 0.0

    # Mander Eq. 3: f'cc = f'c * (2.254 * sqrt(1 + 7.94*f'l/f'c) - 2*f'l/f'c - 1.254)
    if f_l > 0 and fc > 0:
        ratio = f_l / fc
        sqrt_term = math.sqrt(1.0 + 7.94 * ratio)
        fcc = fc * (2.254 * sqrt_term - 2.0 * ratio - 1.254)
    else:
        fcc = fc

    # Strain at confined peak stress (Mander Eq. 4)
    # εcc = εc * (1 + 5 * (f'cc/f'c - 1))
    ecc = 0.002 * (1.0 + 5.0 * (fcc / fc - 1.0)) if fc > 0 else 0.002

    # Ultimate confined strain (spalling)
    # Priestley et al. (1996) simplified form using confined strength:
    #   ecu = 0.004 + 1.4 * rho_s * fyh * eps_su / f'cc
    ecu = 0.004 + 1.4 * rho_s * fyh * data.eps_su / fcc if fcc > 0 else 0.004
    ecu = min(ecu, data.ecu_max)  # configurable cap on spalling strain

    return ConfinementResult(
        fcc=fcc,
        ecc=ecc,
        ecu=ecu,
        ke=ke,
        rho_s=rho_s,
        f_l=f_l,
    )
