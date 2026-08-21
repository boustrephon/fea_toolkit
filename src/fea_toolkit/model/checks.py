"""Pre-build connectivity and model checks on parsed SAP2000 data.

These functions work on ``SAPModelData`` directly and do NOT require
an OpenSees domain or builder.  They are used by the report pipeline
to detect model issues before running any analysis.
"""

import math
from collections import defaultdict
from typing import Any, Optional

from fea_toolkit.model.sap_data import (
    SAPModelData,
    ShellSection,
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


def compute_asce41_hinge_length(
    md: "SAPModelData",
    sec_name: str,
    elem_length: float,
) -> float:
    """Plastic hinge length ``L_p`` per ASCE 41-17 §10.8 (model length units).

    Legacy alias — the canonical implementation now lives in
    :func:`fea_toolkit.capacity.asce41.hinge_length` (moved verbatim from
    this module).  This wrapper delegates to it unchanged, so behaviour is
    preserved for existing callers.
    """
    from ..capacity.asce41 import hinge_length

    return hinge_length(md, sec_name, elem_length)
