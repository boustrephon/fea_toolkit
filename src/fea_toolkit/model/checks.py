"""Pre-build connectivity and model checks on parsed SAP2000 data.

These functions work on ``SAPModelData`` directly and do NOT require
an OpenSees domain or builder.  They are used by the report pipeline
to detect model issues before running any analysis.
"""

import math
from collections import defaultdict
from typing import Any, Optional

from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    SAPModelData,
    ShellSection,
)
from fea_toolkit.utils import (
    DEFAULT_FC_PA,
    DEFAULT_FY_STEEL_PA,
    length_to_si_factor,
    stress_to_si_factor,
)


def check_model_connectivity(
    md: SAPModelData,
    tol: float = 1e-6,
) -> dict[str, Any]:
    """Pre-build connectivity check on parsed SAP2000 data.

    Call **before** creating a builder to detect model issues that
    will cause a singular stiffness matrix.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data from ``parser.get_model_data()``.
    tol : float
        Coordinate tolerance for duplicate-node detection.

    Returns
    -------
    dict
        Keys ``orphan_nodes``, ``shell_only_base_nodes``,
        ``duplicate_coords``, ``zero_area_sections``, ``summary``.
    """
    report: dict[str, Any] = {}

    # ── Orphan nodes ───────────────────────────────────────────────
    frame_nodes: set = set()
    area_nodes: set = set()
    for e in md.frame_elements.values():
        frame_nodes.add(e.node_i)
        frame_nodes.add(e.node_j)
    for a in md.area_elements.values():
        area_nodes.update(a.node_ids)
    all_connected = frame_nodes | area_nodes
    orphans = [
        {"node_id": nid, "coord": (nd.x, nd.y, nd.z)}
        for nid, nd in md.nodes.items()
        if nid not in all_connected
    ]
    report["orphan_nodes"] = orphans

    # ── Shell-only base nodes ─────────────────────────────────────
    shell_only: list = []
    if md.nodes:
        min_z = min(nd.z for nd in md.nodes.values())
        base_ids = {nid for nid, nd in md.nodes.items() if abs(nd.z - min_z) < 0.01}
        base_frame_conn = set()
        for e in md.frame_elements.values():
            if e.node_i in base_ids:
                base_frame_conn.add(e.node_i)
            if e.node_j in base_ids:
                base_frame_conn.add(e.node_j)
        shell_only = [
            {
                "node_id": nid,
                "restraint": str(md.restraints.get(nid, "none")),
                "coord": (md.nodes[nid].x, md.nodes[nid].y, md.nodes[nid].z),
            }
            for nid in sorted(base_ids - base_frame_conn)
            if nid in area_nodes and nid not in base_frame_conn
        ]
        report["shell_only_base_nodes"] = shell_only
    else:
        report["shell_only_base_nodes"] = []

    # ── Duplicate coordinate nodes ─────────────────────────────────
    coord_map: dict[tuple, list] = defaultdict(list)
    for nid, nd in md.nodes.items():
        key = (round(nd.x / tol), round(nd.y / tol), round(nd.z / tol))
        coord_map[key].append(nid)
    dupes = [
        {"coord": (k[0] * tol, k[1] * tol, k[2] * tol), "node_ids": v}
        for k, v in coord_map.items()
        if len(v) > 1
    ]
    report["duplicate_coords"] = dupes

    # ── Zero-area sections ─────────────────────────────────────────
    zero_secs = []
    for sn, s in md.sections.items():
        if isinstance(s, ShellSection):
            if s.thickness <= 0:
                zero_secs.append(
                    {
                        "name": sn,
                        "type": "ShellSection",
                        "thickness": s.thickness,
                    }
                )
        elif (getattr(s, "A", None) or 0) == 0.0:
            zero_secs.append(
                {
                    "name": sn,
                    "type": type(s).__name__,
                    "thickness": getattr(s, "thickness", 0),
                }
            )
    report["zero_area_sections"] = zero_secs

    # ── Summary ────────────────────────────────────────────────────
    report["summary"] = (
        f"Nodes: {len(md.nodes)} | Frames: {len(md.frame_elements)} | "
        f"Areas: {len(md.area_elements)} | "
        f"Orphans: {len(orphans)} | "
        f"Shell-only base: {len(shell_only)} | "
        f"Duplicate coords: {len(dupes)} | "
        f"Zero-area sections: {len(zero_secs)}"
    )
    return report


# ═══════════════════════════════════════════════════════════════════
# Brace buckling check
# ═══════════════════════════════════════════════════════════════════


def check_brace_buckling(
    md: "SAPModelData",
    brace_ids: Optional[set] = None,
    K: float = 1.0,
    axial_demand: Optional[dict[str, float]] = None,
    print_results: bool = True,
) -> dict[str, dict[str, float]]:
    """Check selected braces against Euler buckling.

    Computes P_cr = π²EI₂₂/(KL)² for each brace and optionally
    compares against provided axial demand.

    Parameters
    ----------
    md : SAPModelData
        The parsed model data.
    brace_ids : set, optional
        Set of element IDs to check.  Defaults to empty set.
    K : float
        Effective length factor (default 1.0 — pinned-pinned).
    axial_demand : dict, optional
        ``{elem_id: axial_force_N}`` dict with estimated compressive
        demand (e.g. from a prior linear static analysis).  If provided,
        the demand/capacity ratio is reported.
    print_results : bool
        If True, print a summary table.

    Returns
    -------
    dict
        ``{elem_id: {'P_cr': ..., 'P_demand': ..., 'ratio': ...,
                     'slenderness': ..., 'length': ..., 'section': ...}}``
    """
    if not brace_ids:
        if print_results:
            print("No brace IDs provided.")
        return {}

    elements = md.frame_elements
    assignments = md.frame_assignments

    results: dict[str, dict[str, float]] = {}
    for eid in brace_ids:
        elem = elements.get(eid)
        if elem is None:
            continue
        sec_name = assignments.get(eid) if assignments else None
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        mat = md.materials.get(sec.material)
        if mat is None:
            continue

        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        if L < 1e-12:
            continue

        # E_mod is guaranteed non-None by SAPModelData.apply_material_defaults(),
        # but manually-constructed fixtures may still have it as None.
        from ..utils import DEFAULT_E_S_PA

        E = mat.E_mod if (mat.E_mod or 0) > 0 else DEFAULT_E_S_PA
        I22 = (sec.I22 or 0) if (sec.I22 or 0) > 0 else (sec.I33 or 0)
        A = (sec.A or 0) if (sec.A or 0) > 0 else 1e-4

        # Skip elements with no positive moment of inertia — fabricating
        # a zero-based buckling result is misleading (division by zero
        # later in slenderness and ratio).  A is clamped to 1e-4 above,
        # so we only need to guard I22.
        if I22 <= 0:
            continue

        P_cr = (math.pi**2 * E * I22) / ((K * L) ** 2)
        r = math.sqrt(I22 / A)
        slenderness = (K * L) / r if r > 0 else float("inf")

        demand = axial_demand.get(eid, 0.0) if axial_demand else 0.0
        ratio = demand / P_cr if P_cr > 0 else float("inf")

        results[eid] = {
            "P_cr": P_cr,
            "P_demand": demand,
            "ratio": ratio,
            "slenderness": slenderness,
            "length": L,
            "section": sec_name,
        }

    if print_results and results:
        force_unit = md.units.get("F", "N")
        print(f"\n── Euler buckling check (K={K}) ──")
        header = (
            f"  {'ID':>12} {'Section':>20} {'L (m)':>8} {'λ':>8} {'P_cr (' + force_unit + ')':>14}"
        )
        if axial_demand:
            header += f" {'P_dem (' + force_unit + ')':>14} {'Ratio':>8}"
        print(header)
        print("  " + "-" * len(header))
        for eid, r in sorted(results.items()):
            line = (
                f"  {eid:>12} {r['section']:>20} {r['length']:8.3f} "
                f"{r['slenderness']:8.1f} {r['P_cr']:10.1f}"
            )
            if axial_demand:
                line += f" {r['P_demand']:10.1f} {r['ratio']:8.3f}"
            print(line)
        if axial_demand:
            n_critical = sum(1 for r in results.values() if r["ratio"] > 0.5)
            if n_critical:
                print(f"\n  ⚠ {n_critical} brace(s) with demand > 50% of P_cr")
            else:
                print("\n  ✅ All braces with demand < 50% of P_cr")
    return results


# ═══════════════════════════════════════════════════════════════════
# Self-weight consistency check
# ═══════════════════════════════════════════════════════════════════


def check_self_weight_consistency(
    md: "SAPModelData",
    load_totals: Optional[dict[str, dict[str, float]]] = None,
    atol: Optional[float] = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Compare applied self-weight loads against expected values.

    Parameters
    ----------
    md : SAPModelData
        The parsed model data (used for frame/area geometry).
    load_totals : dict, optional
        Dict of ``{pattern_name: {fx, fy, fz, mx, my, mz}}`` from a
        builder's ``load_totals`` attribute.  If None, only the
        geometry-based expected total is computed.
    atol : float, optional
        Absolute tolerance for the check.  Defaults to 1% of expected.
    verbose : bool
        If True, print a summary.

    Returns
    -------
    dict
        Keys ``expected``, ``applied``, ``discrepancy``, ``passed``,
        ``tolerance``, ``by_section``.
    """
    from fea_toolkit.model.sap_data import ShellSection

    # ── Compute expected self-weight from geometry ──
    expected = 0.0
    by_section: dict[str, float] = {}

    for eid, elem in md.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sec_name = md.frame_assignments.get(eid)
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        mat = md.materials.get(sec.material)
        if mat is None or abs(mat.unit_weight) < 1e-12:
            continue
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        w = sec.A * mat.unit_weight * L
        expected += w
        by_section[sec_name] = by_section.get(sec_name, 0.0) + w

    for aid, area in md.area_elements.items():
        if getattr(area, "inactive", False):
            continue
        sec_name = md.area_assignments.get(aid)
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        if not isinstance(sec, ShellSection):
            continue
        mat = md.materials.get(sec.material)
        if mat is None or abs(mat.unit_weight) < 1e-12:
            continue
        thickness = sec.thickness
        if thickness < 1e-12:
            continue
        # Polygon area via shoelace formula
        verts = [md.nodes[nid] for nid in area.node_ids if nid in md.nodes]
        if len(verts) < 3:
            continue
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        area_val = 0.5 * abs(
            sum(
                xs[i] * ys[(i + 1) % len(verts)] - xs[(i + 1) % len(verts)] * ys[i]
                for i in range(len(verts))
            )
        )
        w = area_val * thickness * mat.unit_weight
        expected += w
        by_section[sec_name] = by_section.get(sec_name, 0.0) + w

    # ── Applied load from load_totals ──
    applied = 0.0
    if load_totals:
        lt_fz = sum(abs(v.get("fz", 0)) for v in load_totals.values())
        applied = lt_fz

    if atol is None:
        atol = 0.01 * max(expected, 1.0)

    discrepancy = abs(expected - applied)
    passed = discrepancy < atol

    if verbose:
        fu = md.units.get("F", "N")
        print(f"  Expected self-weight: {expected:.0f} {fu}")
        print(f"  Applied self-weight:  {applied:.0f} {fu}")
        print(f"  Discrepancy:          {discrepancy:.0f} {fu} (tol={atol:.0f})")
        print(f"  Status:               {'✓ PASS' if passed else '✗ FAIL'}")

    return {
        "expected": expected,
        "applied": applied,
        "discrepancy": discrepancy,
        "passed": passed,
        "tolerance": atol,
        "by_section": by_section,
    }


# ═══════════════════════════════════════════════════════════════════
# Plastic hinge length calculations
# ═══════════════════════════════════════════════════════════════════


def compute_hinge_length(
    section: Any,
    elem_length: float,
) -> float:
    """Compute plastic hinge length *Lp* based on section type.

    Follows the same rules as AnalysisBuilder._compute_hinge_length:

    - I/Wide Flange (ISection): Lp = 0.5 * depth
    - Pipe: Lp = 0.5 * outer_diameter
    - Other / unknown: Lp = 0.1 * L

    Parameters
    ----------
    section : Section
        The section object (must have ``shape`` attribute).
    elem_length : float
        Element length in consistent units.

    Returns
    -------
    float
        Plastic hinge length.
    """
    from fea_toolkit.model.sap_data import ISection, PipeSection

    if isinstance(section, ISection):
        return 0.5 * section.depth
    elif isinstance(section, PipeSection):
        return 0.5 * section.od
    else:
        return 0.1 * elem_length


def _get_conversion_factors(md: Any) -> tuple:
    """Extract stress_factor and length_factor from any model-like object.

    Works with both ``SAPModelData`` (has ``stress_factor`` /
    ``length_factor`` properties) and ``MeshModel`` (needs units dict).

    Returns
    -------
    tuple
        ``(stress_factor, length_factor)`` as floats (SI → model units).
    """
    if hasattr(md, "stress_factor") and hasattr(md, "length_factor"):
        return md.stress_factor, md.length_factor
    # Fallback: compute from units dict using the canonical utils factors.
    units = getattr(md, "units", {}) or {}
    sf = stress_to_si_factor(units)
    lf = length_to_si_factor(units)
    return sf, lf


def compute_asce41_hinge_length(
    md: "SAPModelData",
    sec_name: str,
    elem_length: float,
) -> float:
    """Plastic hinge length Lp per ASCE 41-17 §10.8.

    Computes Lp based on material and section properties.  Falls back
    to 0.1 * L when section data is unavailable.

    All material values are guaranteed non-None by
    SAPModelData.apply_material_defaults() but manually-constructed
    fixtures may still have None values — these are handled by
    falling back to hardcoded SI defaults within the formula.

    Code-based formulae convert to SI internally using the model's
    conversion factors, then return results in model length units.

    Parameters
    ----------
    md : SAPModelData
        The parsed model data (for section/material lookup).
    sec_name : str
        Section name for material and geometry lookup.
    elem_length : float
        Element length in model units.

    Returns
    -------
    float
        Plastic hinge length in model length units.
    """
    sec = md.sections.get(sec_name)
    if sec is None:
        return max(0.05, elem_length * 0.1)

    mat = md.materials.get(sec.material)
    if mat is None:
        return max(0.05, elem_length * 0.1)

    # ── Get conversion factors (works with SAPModelData and MeshModel) ──
    sf, lf = _get_conversion_factors(md)

    # ── Convert material strengths from model stress units → Pa → MPa ──
    # Model-provided values are in model stress units and need sf;
    # fallback constants are already in Pa and bypass sf.
    fy_pa = mat.Fy * sf if (mat.Fy or 0) > 0 else DEFAULT_FY_STEEL_PA
    fc_pa = mat.Fc * sf if (mat.Fc or 0) > 0 else DEFAULT_FC_PA
    fy_mpa = fy_pa / 1e6
    fc_mpa = fc_pa / 1e6

    # ── Convert section dimensions from model length units → mm ──
    # Use explicit ConcreteRectangularSection check rather than generic
    # attribute-based detection which can misidentify sections.
    is_concrete = isinstance(sec, ConcreteRectangularSection)
    is_brace = hasattr(sec, "od") or hasattr(sec, "t")

    def _to_mm(val: float) -> float:
        """Convert a value in model length units to mm."""
        return val * lf * 1000.0

    if is_concrete:
        # ASCE 41-17 d_b = longitudinal rebar diameter (mm)
        if (getattr(sec, "top_bar_dia", None) or 0) > 0:
            db = _to_mm(sec.top_bar_dia)
        elif (getattr(sec, "bar_dia", None) or 0) > 0:
            db = _to_mm(sec.bar_dia)
        else:
            db = 20.0  # fallback rebar diameter in mm
    # Steel: db = section depth in the loading direction (mm)
    # ASCE 41-17 §9.3.3.2 uses overall section depth or OD for steel
    # members — flange thickness (tf) and wall thickness (t) are not
    # valid d_b terms.
    elif (getattr(sec, "depth", None) or 0) > 0:
        db = _to_mm(sec.depth)
    elif (getattr(sec, "od", None) or 0) > 0:
        db = _to_mm(sec.od)
    else:
        db = 20.0  # fallback in mm

    # ── ASCE 41-17 formula §10.8 ──
    # Convert elem_length to metres for the formula
    L_m = elem_length * lf

    if is_concrete:
        Lp = 0.05 * L_m + 0.1 * db * fy_mpa / max(fc_mpa, 1.0) ** 0.5 / 1000.0
    elif is_brace:
        Lp = 0.08 * L_m + 0.015 * db * fy_mpa / 1000.0
    else:
        Lp = 0.08 * L_m + 0.022 * db * fy_mpa / 1000.0

    # Lp from formula is in metres — clamp and convert back to model units
    Lp_m = min(Lp, 0.33 * L_m)
    return Lp_m / lf if lf != 0 else Lp_m
