"""
Utility functions for configuration merging and load-pattern inference.

These are used primarily by ``run_all()`` to auto-detect load patterns
from raw SAP2000 table data and merge user config with defaults.
"""

import math
from typing import Dict, List, Optional

# Gravitational acceleration in m/s²  (SI default)
_G_SI = 9.80665

# ── Material-property defaults (SI Pa units) ──────────────────────────
# These are used as fallback values when SAP2000 data is missing.
# They must be scaled to the model's unit system via 
# length_scale_factor(), force_scale_factor() and stress_scale_factor().

DEFAULT_FY_STEEL_PA = 250.0e6       # Steel yield stress (Pa)
DEFAULT_FY_REBAR_PA = 400.0e6       # Rebar / RC steel yield stress (Pa)
DEFAULT_FC_PA = 30.0e6              # Concrete compressive strength (Pa)
DEFAULT_E_S_PA = 200.0e9            # Young's modulus (Pa)
DEFAULT_NU_S = 0.3                  # Poisson's ratio
DEFAULT_G_S_PA = 76.9e9             # Shear modulus — computed from E & nu (E / (2 * (1 + nu)))
DEFAULT_G_MOD_FRAC = 0.385          # Default G = G_MOD_FRAC × E when G is missing
DEFAULT_E_C_PA = 30.0e9             # Concrete Young's modulus
DEFAULT_NU_C = 0.2                  # Poisson's ratio
DEFAULT_G_C_PA = 12.5e9             # Shear modulus — computed from E & nu (E / (2 * (1 + nu)))
DEFAULT_EPS_C = 0.002               # Strain at peak Fc (concrete)
DEFAULT_EPS_CC = 0.005              # Crushing strain (confined concrete)
DEFAULT_RHO_WC_SI = 24000.0         # Default concrete unit weight (N/m³)
DEFAULT_RHO_MC_SI = 2450.0          # Default concrete unit mass (kg/m³)
DEFAULT_RHO_WS_SI = 77000.0         # Default steel unit weight (N/m³)
DEFAULT_RHO_MS_SI = 7850.0          # Default steel unit mass (kg/m³)
DEFAULT_GRAVITY_MS2 = _G_SI         # Gravitational acceleration in m/s²

def g_from_units(units: dict) -> float:
    """Return gravitational acceleration matching the model length unit.

    SAP2000 analysis always assumes time in seconds.  This function
    scales g from the SI value (9.80665 m/s²) to the model's length
    unit.  Falls back to 9.81 if the length unit is unrecognised.

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'KN', 'T': 'C'}``.

    Returns:
        Gravitational acceleration in the model's length-unit / s².
    """
    lu = (units or {}).get('L', 'm')
    if not lu or not isinstance(lu, str):
        lu = 'm'
    # Normalise aliases before scaling
    _alias = {
        'meter': 'm', 'meters': 'm', 'metre': 'm', 'metres': 'm',
        'centimeter': 'cm', 'centimeters': 'cm', 'centimetre': 'cm',
        'millimeter': 'mm', 'millimeters': 'mm', 'millimetre': 'mm',
        'foot': 'ft', 'feet': 'ft',
        'inch': 'in', 'inches': 'in',
    }
    lu = _alias.get(lu.lower(), lu.lower())
    # Scale factor relative to 1 m
    scale = {
        'm': 1.0,
        'cm': 100.0,
        'mm': 1000.0,
        'ft': 3.28084,
        'in': 39.3701,
    }.get(lu, 1.0)
    return _G_SI * scale

def length_scale_factor(units: dict) -> float:
    """Compute the length-unit scaling factor from model units.

    Length-like SI quantities (m) are converted from SI to
    the model's unit system by multiplying by this factor:

        value_in_model_units = value_in_m * length_scale_factor(units)

    Example: to convert 1.0 m to model units:
        >> length_scale_factor({'L': 'mm'}) -> 1000.0  (1 m = 1000 mm)

    The factor is ``L_factor`` where:

    =======  =========
    Unit     L_factor
    =======  =========
    m        1.0
    cm       100.0
    mm       1000.0
    in       39.3701 = 1 / 0.0254
    ft       3.28084 = 1 / 0.3048
    =======  =========

    Args:
        units: target (model) units dict, e.g. ``{'L': 'm', 'F': 'N', 'T': 'C'}``.

    Returns:
        Scaling factor to convert SI (m) to the model's length unit.
    """
    u = units or {}
    lu = u.get('L', 'm')

    # Normalise
    _alias_L = {
        'meter': 'm', 'meters': 'm', 'metre': 'm', 'metres': 'm',
        'centimeter': 'cm', 'centimeters': 'cm', 'centimetre': 'cm',
        'millimeter': 'mm', 'millimeters': 'mm', 'millimetre': 'mm',
        'foot': 'ft', 'feet': 'ft',
        'inch': 'in', 'inches': 'in',
    }

    lu_norm = _alias_L.get(lu.lower(), lu.lower()) if isinstance(lu, str) else 'm'

    L_factor = {
        'm':  1.0,
        'cm': 100.0,
        'mm': 1000.0,
        'in': 1 / 0.0254,
        'ft': 1 / 0.3048,
    }.get(lu_norm, 1.0)

    return L_factor

def force_scale_factor(units: dict) -> float:
    """Compute the force-unit scaling factor from model units.

    Force-like SI quantities (N) are converted to the model's unit system
    by multiplying by this factor::

        value_in_model_units = value_in_SI_N * force_scale_factor(units)

    The factor is ``F_factor`` where:


    =======  ==========
    Unit     F_factor
    =======  ==========
    N        1.0
    kN       0.001
    MN       0.000001
    kgf      0.101972 = 1 / 9.80665
    lbf      0.224809 = 1 / 4.44822
    kipf     0.000224809 = 1 / 4448.22
    =======  ==========

    For example:
    - N, m   → 1.0² ÷ 1.0    = 1.0     (Pa → Pa)
    - N, mm  → 0.001² ÷ 1.0  = 1e-6    (Pa → MPa = N/mm²)
    - kN, mm → 0.001² ÷ 1000 = 1e-9    (Pa → kN/mm²)

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'N', 'T': 'C'}``.

    Returns:
        Scaling factor to convert N to the model's force unit.
    """
    u = units or {}
    fu = u.get('F', 'N')

    # Normalise
    _alias_F = {
        'newton': 'N', 'newtons': 'N',
        'kilonewton': 'kN', 'kilonewtons': 'kN',
        'meganewton': 'MN', 'meganewtons': 'MN',
        'kilogramforce': 'kgf', 'kg': 'kgf',
        'pound': 'lb', 'pounds': 'lb', 'lbf': 'lb',
        'kip': 'kip', 'kips': 'kip', 'kipf': 'kip',
        'tonneforce': 'tonf', 'tonf': 'tonf', 'ton': 'tonf',
    }
    fu_norm = _alias_F.get(fu.lower(), fu.lower()).lower() if isinstance(fu, str) else 'n'

    F_factor = {
        'n': 1.0,
        'kn': 0.001,
        'mn': 0.000001,
        'kgf': 1 / 9.80665,
        'tonf': 1 / 9806.65,
        'lb': 1 / 4.448,
        'kip': 1 / 4448.0,
    }.get(fu_norm, 1.0)

    return F_factor

def mass_scale_factor(units: dict) -> float:
    """Compute the mass-unit scaling factor from model units.

    Mass units are derived from the force-length-time (FLT) basis using
    Newton's second law (F = m · a).  Since time is always seconds in
    SAP2000/OpenSees, the acceleration factor equals the length factor::

        M_factor = F_factor / L_factor

    where:

        F_factor = force_scale_factor(units)   (SI N → model force unit)
        L_factor = length_scale_factor(units)  (SI m → model length unit)

    This derivation ensures mass units are **consistent** with the
    model's force, length, and time basis — the same system used by
    SAP2000 internally.  For example:

    =========== ======== =========== ==========
    Force unit  Length   F_factor    M_factor
    =========== ======== =========== ==========
    N           m        1.0         1.0       (kg)
    N           mm       1.0         0.001     (tonne)
    kN          m        0.001       0.001     (tonne)
    kN          mm       0.001       1e-6      (kg/tonne mix)
    kgf         m        1/9.80665   1/9.80665 (hyl)
    lb          in       1/4.448     1/39.37   (blob ≈ 5.71e-3)
    Kip         ft       1/4448      1/3.2808  (kiloslug ≈ 6.85e-5)
    =========== ======== =========== ==========

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'N', 'T': 'C'}``.

    Returns:
        Scaling factor to convert SI mass (kg) to model mass units:
        ``model_mass = SI_kg * mass_scale_factor(units)``.
    """
    return force_scale_factor(units) / length_scale_factor(units)

def stress_scale_factor(units: dict) -> float:
    """Compute the stress-unit scaling factor from model units.

    Stress-like SI quantities (Pa) are converted to the model's unit system
    by multiplying by this factor::

        value_in_model_units = value_in_Pa * stress_scale_factor(units)

    The factor is ``F_factor ÷ L_factor²`` where:

    F_factor = force_scale_factor(units)
    L_factor = length_scale_factor(units)

    For example:
    - N, m   → 1.0 ÷ 1.0²    = 1.0     (Pa → Pa)
    - N, mm  → 1.0 ÷ 1000.0²  = 1e-6    (Pa → MPa = N/mm²)
    - kN, mm → 0.001 ÷ 1000.0² = 1e-9    (Pa → kN/mm²)

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'N', 'T': 'C'}``.

    Returns:
        Scaling factor to convert Pa to the model's stress unit.
    """

    L_factor = length_scale_factor(units)
    F_factor = force_scale_factor(units)

    return F_factor / L_factor ** 2

def mass_density_scale_factor(units: dict) -> float:
    """Compute the unit-mass-unit scaling factor from model units.

    Unit-mass-like quantities (kg/m3) [M]/[L]^3 are converted to the model's unit system
    by multiplying by this factor::

        value_in_model_units = value_in_SI_kg_m3 * mass_density_scale_factor(units)

    The factor is ``M_factor ÷ L_factor^3 `` where:

    L_factor = length_scale_factor(units)
    M_factor = mass_scale_factor(units)

    For example:
    - kg, m   → 1.0^3 ÷ 1.0    = 1.0     (kg/m^3 → kg/m^3)
    - kg, mm  → 1.0 ÷ 1.0E3^3  = 1e-9    (kg/m^3 → kg/mm^3)
    - tonne, mm → 0.001 ÷ 1.0E3^3 = 1e-12    (kg/m^3 → tonne/mm^3)

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'N', 'T': 'C'}``.

    Returns:
        Scaling factor to convert kg/m3 to the model's mass density unit.
    """

    M_factor = mass_scale_factor(units)
    L_factor = length_scale_factor(units)

    return M_factor / L_factor ** 3 

def weight_density_scale_factor(units: dict) -> float:
    """Compute the SI unit-weight unit scaling factor from model units.

    Unit-mass-like quantities (N/m3) [F]/[L]^3 are converted to the model's unit system
    by multiplying by this factor::

        value_in_model_units = value_in_N_m3 * weight_density_scale_factor(units)

    The factor is ``F_factor ÷ L_factor^3`` where:

    F_factor = force_scale_factor(units)
    L_factor = length_scale_factor(units)

    For example:
    - N, m   → 1.0 ÷ 1.0^3    = 1.0     (N/m^3 → N/m^3)
    - N, mm  → 1.0 ÷ 1000.0^3  = 1e-9    (N/m^3 → N/mm^3)
    - kN, mm → 0.001 ÷ 1000.0^3 = 1e-12    (N/m^3 → kN/mm^3)

    Args:
        units: Model units dict, e.g. ``{'L': 'm', 'F': 'N', 'T': 'C'}``.

    Returns:
        Scaling factor to convert N/m3 to the model's weight density unit.
    """

    F_factor = force_scale_factor(units)
    L_factor = length_scale_factor(units)

    return F_factor / L_factor ** 3

# ── Known stress-valued material property keys (SI canonical values → model units) ──
_STRESS_KEYS: frozenset = frozenset({
    'E', 'Es', 'Ec', 'G',            # elastic moduli
    'fc', 'ft', 'fcu',                # concrete strengths
    'fy', 'fyh', 'fu',                # steel strengths
    'Hiso', 'Hkin',                   # hardening moduli
})


def scale_material_dict(
    mat_dict: dict,
    units: dict,
    stress_scale: Optional[float] = None,
) -> dict:
    """Scale stress-valued fields in a material dict from SI Pa to model units.

    Returns a new dict with stress fields multiplied by
    ``stress_scale_factor(units)``.  Non-stress fields (Poisson's ratio,
    density, strain values, string flags) are passed through unchanged.

    Args:
        mat_dict: Material property dict, e.g. ``{"E": 200e9, "nu": 0.3, "fy": 400e6}``.
        units: Model units dict, e.g. ``{"F": "kN", "L": "m"}``.
        stress_scale: Pre-computed factor (optional); computed from
            ``stress_scale_factor(units)`` if ``None``.

    Returns:
        New dict with stress fields scaled to model units.
    """
    ssf = stress_scale if stress_scale is not None else stress_scale_factor(units)
    if abs(ssf - 1.0) < 1e-15:
        return dict(mat_dict)  # SI → SI, no scaling needed
    result = {}
    for k, v in mat_dict.items():
        if isinstance(v, (int, float)) and k in _STRESS_KEYS:
            result[k] = v * ssf
        else:
            result[k] = v
    return result


def deep_merge(base: dict, override: dict) -> dict:
    """Merge *override* into *base*.

    *   Scalar values in *override* replace *base*.
    *   Dicts are merged recursively.
    *   ``None`` in *override* removes the key from *base* (opt-out).
    """
    result = dict(base)
    for k, v in override.items():
        if v is None:
            result.pop(k, None)
        elif k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def infer_loads(raw_tables: dict) -> dict:
    """Auto-detect load patterns by DesignType from the raw s2k tables.

    Returns ``{"dead": [names], "live": [names], "wind": [names],
    "quake": [names]}``.
    """
    result = {"dead": [], "live": [], "wind": [], "quake": []}
    for tname, records in raw_tables.items():
        if "LOAD PATTERN DEFINITIONS" not in tname:
            continue
        for rec in records:
            name = rec.get("LoadPat", "")
            dtype = rec.get("DesignType", "")
            if dtype == "Dead":
                result["dead"].append(name)
            elif dtype == "Live":
                result["live"].append(name)
            elif dtype == "Wind":
                result["wind"].append(name)
            elif dtype == "Quake":
                result["quake"].append(name)
    return result


def build_gravity_patterns(inferred: dict) -> dict:
    """Build the gravity load combination dict from auto-detected loads.

    Uses 1.0 for Dead, 0.5 for Live (GB 50011 seismic combos).
    """
    patterns = {}
    for name in inferred.get("dead", []):
        patterns[name] = 1.0
    for name in inferred.get("live", []):
        patterns[name] = 0.5
    return patterns


def pick_wind(inferred: dict, direction: str) -> dict:
    """Pick the wind pattern matching *direction* (e.g. '+X')."""
    sign, axis = direction[0], direction[1]
    for name in inferred.get("wind", []):
        if axis in name and sign in name:
            return {name: 1.0}
    # Fallback: first wind pattern with the right axis
    for name in inferred.get("wind", []):
        if axis in name:
            return {name: 1.0}
    return {}


# ── Legacy aliases with underscore prefixes (for backward compat) ──────

def _deep_merge(base: dict, override: dict) -> dict:
    return deep_merge(base, override)


def _infer_loads(raw_tables: dict) -> dict:
    return infer_loads(raw_tables)


def _build_gravity_patterns(inferred: dict) -> dict:
    return build_gravity_patterns(inferred)


def _pick_wind(inferred: dict, direction: str) -> dict:
    return pick_wind(inferred, direction)


# ── Flag diagram geometry (pure NumPy, no renderer dependency) ────────

def compute_flag_parts(pt1, pt2, vn, Fi, Fj, scale):
    """Yield ``(vertices, col_val)`` for each part of a flag diagram element.

    Parameters
    ----------
    pt1, pt2 : array-like of length 3
        I-end and J-end node coordinates.
    vn : array-like of length 3
        Unit vector for positive flag offset direction.
    Fi, Fj : float
        Force/moment values (original, un-negated).
    scale : float
        Scale factor (display units per force/moment unit).

    Yields
    ------
    vertices : list of ndarray
        Corner points in perimeter order (4 for a quad, 3 for a triangle).
    col_val : float
        Signed value for colour mapping (positive → red, negative → blue).
    """
    import numpy as np

    pt1 = np.asarray(pt1, dtype=float)
    pt2 = np.asarray(pt2, dtype=float)
    vn = np.asarray(vn, dtype=float)

    if abs(Fi) < 1e-12 and abs(Fj) < 1e-12:
        return

    off_i = vn * Fi * scale       # I-end: +vn for positive Fi
    off_j = -vn * Fj * scale      # J-end: -vn for positive Fj (baked-in negation)

    if Fi * Fj < 0.0:
        # Trapezoid: [pt1, pt2, pt2+off_j, pt1+off_i]
        col_val = Fi if abs(Fi) >= abs(Fj) else Fj
        yield [pt1, pt2, pt2 + off_j, pt1 + off_i], col_val
    else:
        # Zero-crossing: split at vcp = vx · Fi / (Fi + Fj)
        if abs(Fi + Fj) < 1e-15:
            return
        ratio = Fi / (Fi + Fj)
        p_zero = pt1 + (pt2 - pt1) * ratio
        if abs(Fi) > 1e-12:
            yield [pt1, p_zero, pt1 + off_i], Fi
        if abs(Fj) > 1e-12:
            yield [p_zero, pt2, pt2 + off_j], Fj


def cqc_combine(modal_values: List[float],
                omega: List[float],
                damp_ratios: List[float]) -> float:
    """Complete Quadratic Combination of modal results (Der Kiureghian 1980).

    Uses the standard CQC correlation coefficient formula:

    .. math::

        \\rho_{ij} = \\frac{8 \\sqrt{\\zeta_i \\zeta_j} (\\zeta_i + r \\zeta_j) r^{3/2}}
                           {(1 - r^2)^2 + 4 \\zeta_i \\zeta_j r (1 + r^2) + 4 (\\zeta_i^2 + \\zeta_j^2) r^2}

    where :math:`r = \\omega_i / \\omega_j` and :math:`\\zeta` is the damping ratio.

    Args:
        modal_values: Per-mode response quantities (shear, moment, etc.).
        omega: Circular frequencies of each mode (rad/s).
        damp_ratios: Damping ratio for each mode.

    Returns:
        CQC-combined scalar value.
    """
    n = len(modal_values)
    if n == 0:
        return 0.0
    if n == 1:
        return abs(modal_values[0])
    total = 0.0
    for i in range(n):
        for j in range(n):
            di = damp_ratios[i] if i < len(damp_ratios) else 0.05
            dj = damp_ratios[j] if j < len(damp_ratios) else 0.05
            om_i = omega[i] if i < len(omega) else 1.0
            om_j = omega[j] if j < len(omega) else 1.0
            bij = om_i / om_j if om_j > 0 else 1.0
            rho = (
                8.0 * math.sqrt(di * dj) * (di + bij * dj) * (bij ** 1.5)
            ) / (
                (1.0 - bij ** 2.0) ** 2.0
                + 4.0 * di * dj * bij * (1.0 + bij ** 2.0)
                + 4.0 * (di ** 2.0 + dj ** 2.0) * bij ** 2.0
            )
            total += modal_values[i] * modal_values[j] * rho
    return math.sqrt(max(total, 0.0))


def sum_reactions_with_overturning(
    reactions: dict,
    nodes: dict,
) -> Dict[str, float]:
    """Sum per‑node reaction forces and moments, adding overturning
    moments from force × lever‑arm about the plan centroid at base level.

    **What it does**
    ``ops.nodeReaction(tag, dof)`` returns the reaction at each restrained
    DOF.  For pinned-base columns the rotational DOFs (Mx, My) are free
    and ``nodeReaction`` returns **zero** for those components, even
    though the element carries bending moment.  This function reconstructs
    the full overturning moment by adding the force × lever‑arm
    contribution of each reaction component about a fixed reference point:

    ``mx += fz·dy − fy·dz``,  ``my += fx·dz − fz·dx``,  ``mz += fy·dx − fx·dy``

    The reference point is the **bounding‑box midpoint** ``(min+max)/2``
    of the **base (support) nodes only** — the centre of the base
    footprint.  This fixed reference is used consistently for all load
    cases (static and RS) so that moments share a common origin for
    comparison and combination.

    **Usage**
    - **Static lateral loads** (Wind, Quake): called from
      :func:`pumphouse_report_v2.run_linear_cases` and
      :meth:`AnalysisBuilder.run_static_analysis
      <fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_static_analysis>`
    - **Response‑spectrum analysis**: the same lever-arm logic is applied
      per-mode in
      :meth:`AnalysisBuilder.run_response_spectrum_analysis
      <fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_response_spectrum_analysis>`,
      but the source is ``ops.eleResponse(eid, 'forces')`` (global
      element‑end forces) rather than ``nodeReaction``.  The element‑end
      forces include the column bending moment directly, but the axial
      force lever‑arm (Fz from one column × distance to another) is a
      structural‑level effect that must still be added — this function's
      approach is replicated there.

    Args:
        reactions: ``{node_key: {fx, fy, fz, mx, my, mz}}``.
            Keys may be ``node_tag`` (int) or ``node_id`` (str);
            the function tries both lookups.
        nodes: Node lookup dict — each value must have ``.x``, ``.y``,
            ``.z`` attributes.

    Returns:
        ``{fx, fy, fz, mx, my, mz}`` summed vector with overturning
        moment included.
    """
    if not nodes:
        return {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
                'mx': 0.0, 'my': 0.0, 'mz': 0.0}

    # Build a one-time tag-to-node index for efficient lookups.
    # Reaction keys may be string IDs or integer node_tags; build both.
    tag_to_node: Dict = {}
    for nd in nodes.values():
        t = getattr(nd, 'node_tag', None)
        if t is not None:
            tag_to_node[t] = nd

    def _resolve_node(nid):
        """Look up a node by string key or integer tag."""
        if isinstance(nid, str):
            nd = nodes.get(nid)
            if nd is not None:
                return nd
        return tag_to_node.get(nid)

    # Identify the base (support) nodes — those that appear in reactions.
    # The centroid is computed from these nodes only, so that the
    # overturning moment reference is at the centre of the base footprint.
    _base_nds = []
    for nid in reactions:
        nd = _resolve_node(nid)
        if nd is not None:
            _base_nds.append(nd)

    if _base_nds:
        xs = [n.x for n in _base_nds]
        ys = [n.y for n in _base_nds]
        cx = (min(xs) + max(xs)) * 0.5  # bounding‑box midpoint (v1 match)
        cy = (min(ys) + max(ys)) * 0.5
        z_base = sum(n.z for n in _base_nds) / len(_base_nds)  # avg of support nodes
    else:
        # Fallback: all nodes
        xs = [n.x for n in nodes.values()]
        ys = [n.y for n in nodes.values()]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        z_base = min(n.z for n in nodes.values())

    summed = {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
              'mx': 0.0, 'my': 0.0, 'mz': 0.0}
    for nid, r in reactions.items():
        node = _resolve_node(nid)
        if node is None:
            continue
        fx = r.get('fx', 0.0); fy = r.get('fy', 0.0); fz = r.get('fz', 0.0)
        mx = r.get('mx', 0.0); my = r.get('my', 0.0); mz = r.get('mz', 0.0)
        dx = node.x - cx; dy = node.y - cy; dz = node.z - z_base
        summed['fx'] += fx; summed['fy'] += fy; summed['fz'] += fz
        summed['mx'] += mx + fz * dy - fy * dz
        summed['my'] += my + fx * dz - fz * dx
        summed['mz'] += mz + fy * dx - fx * dy
    return summed