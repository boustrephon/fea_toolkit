"""Standalone review and integrity checks for parsed SAP2000 (.s2k) models.

This module provides a **solver-free first-pass review** of a
:class:`~fea_toolkit.model.sap_data.SAPModelData` instance: an inventory
of the model contents, connectivity diagnostics (loose nodes, duplicate
coordinates, independent/floating sub-structures) and a collection of
data-integrity checks that catch the issues which typically precede a
singular stiffness matrix or a silently wrong analysis.

The review never builds an OpenSees domain.  An optional second phase
(``include_analysis=True``) runs a modal and a linear-static analysis via
the normal Preprocessor → AnalysisBuilder pipeline to confirm static and
dynamic behaviour (periods, mass participation, reactions and
singularity detection).

Public API
----------
review_model
    Run the full review on a :class:`SAPModelData` instance.
review_s2k_file
    Parse a ``.s2k`` path and review it in one call.
print_review_report
    Print a human-readable console summary.
format_review_markdown
    Render the review as a Markdown document.
main
    Command-line entry point — ``python -m fea_toolkit.model.review <path>``.

The core review has **no pandas dependency** (it returns plain dicts and
lists) so it stays importable in dependency-light environments.
"""

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from .._unit_scaling import force_unit_label, length_unit_label
from .checks import (
    check_brace_buckling,
    check_model_connectivity,
    check_self_weight_consistency,
)
from .sap_data import FRAME_RELEASE_DOF_LABELS, SAPModelData, patterns_from_case

__all__ = [
    "format_review_markdown",
    "format_review_report",
    "main",
    "print_review_report",
    "review_model",
    "review_s2k_file",
]


# ═══════════════════════════════════════════════════════════════════
# Disjoint-set union (union-find) for connectivity analysis
# ═══════════════════════════════════════════════════════════════════


class _DisjointSet:
    """Minimal union-find with path compression (iterative)."""

    def __init__(self, items):
        self._parent: dict[Any, Any] = {item: item for item in items}

    def find(self, item: Any) -> Any:
        """Return the representative (root) of *item*'s set."""
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: Any, b: Any) -> None:
        """Merge the sets containing *a* and *b*."""
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a


def _analyse_components(md: SAPModelData) -> list[dict[str, Any]]:
    """Group nodes into connected components and characterise each one.

    Nodes are connected through (i) active frame/area elements and
    (ii) joint constraints (rigid bodies / diaphragms tie joints
    together).  Each returned component records its node/element counts
    and whether it reaches a support (restraint).

    Args:
        md: Parsed model data.

    Returns:
        Component dicts sorted largest-first, each with keys ``n_nodes``,
        ``n_frames``, ``n_areas``, ``n_restrained_nodes``, ``is_supported``,
        ``nodes`` and ``frames``.
    """
    node_ids = list(md.nodes.keys())
    dsu = _DisjointSet(node_ids)

    for elem in md.frame_elements.values():
        if getattr(elem, "inactive", False):
            continue
        if elem.node_i in dsu._parent and elem.node_j in dsu._parent:
            dsu.union(elem.node_i, elem.node_j)

    for area in md.area_elements.values():
        if getattr(area, "inactive", False):
            continue
        present = [nid for nid in area.node_ids if nid in dsu._parent]
        for nid in present[1:]:
            dsu.union(present[0], nid)

    constraint_groups: dict[str, list[str]] = defaultdict(list)
    for nid, cname in md.constraint_assignments.items():
        if nid in dsu._parent:
            constraint_groups[cname].append(nid)
    for group in constraint_groups.values():
        for nid in group[1:]:
            dsu.union(group[0], nid)

    buckets: dict[Any, list[str]] = defaultdict(list)
    for nid in node_ids:
        buckets[dsu.find(nid)].append(nid)

    components: list[dict[str, Any]] = []
    for nids in buckets.values():
        nset = set(nids)
        frames = [
            eid
            for eid, elem in md.frame_elements.items()
            if not getattr(elem, "inactive", False) and elem.node_i in nset
        ]
        areas = [
            aid
            for aid, area in md.area_elements.items()
            if not getattr(area, "inactive", False) and all(n in nset for n in area.node_ids)
        ]
        restrained = [nid for nid in nids if nid in md.restraints]
        components.append(
            {
                "n_nodes": len(nids),
                "n_frames": len(frames),
                "n_areas": len(areas),
                "n_restrained_nodes": len(restrained),
                "is_supported": bool(restrained),
                "nodes": nids,
                "frames": frames,
            }
        )

    components.sort(key=lambda c: (c["n_nodes"], c["n_frames"]), reverse=True)
    return components


# ═══════════════════════════════════════════════════════════════════
# Inventory / breakdown
# ═══════════════════════════════════════════════════════════════════


def _count_by(values) -> dict[str, int]:
    """Count a stream of hashable values into a frequency-sorted dict."""
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[str(value)] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _inventory(md: SAPModelData) -> dict[str, int]:
    """Return a flat count of every model object category."""
    return {
        "nodes": len(md.nodes),
        "restraints": len(md.restraints),
        "frame_elements": len(md.frame_elements),
        "area_elements": len(md.area_elements),
        "materials": len(md.materials),
        "sections": len(md.sections),
        "frame_section_assignments": len(md.frame_assignments),
        "area_section_assignments": len(md.area_assignments),
        "frame_releases": len(md.frame_releases),
        "groups": len(md.groups),
        "constraints": len(md.constraints),
        "constraint_assignments": len(md.constraint_assignments),
        "load_cases": len(md.load_cases),
        "load_patterns": len(md.load_patterns),
        "joint_loads": len(md.joint_loads),
        "frame_distributed_loads": len(md.frame_dist_loads),
        "frame_gravity_loads": len(md.frame_gravity_loads),
        "area_uniform_loads": len(md.area_uniform_loads),
        "area_gravity_loads": len(md.area_gravity_loads),
        "mass_sources": len(md.mass_sources),
        "frame_auto_mesh": len(md.frame_auto_mesh),
        "frame_end_offsets": len(md.frame_end_offsets),
    }


def _breakdown(md: SAPModelData) -> dict[str, dict[str, int]]:
    """Return per-type breakdowns for restraints, materials and loads."""
    return {
        "restraint_dof_patterns": _count_by(
            "".join(str(d) for d in r.dofs) for r in md.restraints.values()
        ),
        "material_types": _count_by(m.type for m in md.materials.values()),
        "section_types": _count_by(type(s).__name__ for s in md.sections.values()),
        "load_case_types": _count_by(lc.case_type for lc in md.load_cases.values()),
        "load_pattern_types": _count_by(lp.pattern_type for lp in md.load_patterns.values()),
    }


def _bounds(md: SAPModelData) -> Optional[dict[str, float]]:
    """Return the model bounding box, or ``None`` for an empty model."""
    if not md.nodes:
        return None
    xs = [n.x for n in md.nodes.values()]
    ys = [n.y for n in md.nodes.values()]
    zs = [n.z for n in md.nodes.values()]
    return {
        "x_min": min(xs),
        "x_max": max(xs),
        "x_span": max(xs) - min(xs),
        "y_min": min(ys),
        "y_max": max(ys),
        "y_span": max(ys) - min(ys),
        "z_min": min(zs),
        "z_max": max(zs),
        "z_span": max(zs) - min(zs),
    }


def _natural_key(value: str):
    """Sort key that orders numeric-looking IDs naturally (``"2"`` < ``"10"``)."""
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in str(value).split("-"))


def _release_summary(md: SAPModelData) -> dict[str, Any]:
    """Summarise frame end releases parsed from the FRAME RELEASE table(s)."""
    by_dof: dict[str, int] = dict.fromkeys(FRAME_RELEASE_DOF_LABELS, 0)
    rows: list[dict[str, Any]] = []
    for frame_id, release in md.frame_releases.items():
        end_i = release.released_labels("I")
        end_j = release.released_labels("J")
        for label in set(end_i) | set(end_j):
            by_dof[label] += 1
        rows.append({"frame_id": frame_id, "end_i": end_i, "end_j": end_j})
    rows.sort(key=lambda r: _natural_key(r["frame_id"]))
    return {"n_frames_with_releases": len(rows), "by_dof": by_dof, "releases": rows}


# ═══════════════════════════════════════════════════════════════════
# Integrity checks
# ═══════════════════════════════════════════════════════════════════


def _integrity(md: SAPModelData, tol: float) -> dict[str, Any]:
    """Run data-integrity checks on the parsed model.

    Args:
        md: Parsed model data.
        tol: Length tolerance for degenerate-element detection.

    Returns:
        Dict of issue lists plus a ``counts`` summary mapping each issue
        category to the number of offenders found.
    """
    # ── Elements referencing missing nodes ──
    missing_node_refs = []
    for eid, elem in md.frame_elements.items():
        missing = [n for n in (elem.node_i, elem.node_j) if n not in md.nodes]
        if missing:
            missing_node_refs.append({"element": eid, "kind": "Frame", "missing_nodes": missing})
    for aid, area in md.area_elements.items():
        missing = [n for n in area.node_ids if n not in md.nodes]
        if missing:
            missing_node_refs.append({"element": aid, "kind": "Area", "missing_nodes": missing})

    # ── Unassigned (active) elements ──
    unassigned_frames = [
        eid
        for eid, elem in md.frame_elements.items()
        if not getattr(elem, "inactive", False) and eid not in md.frame_assignments
    ]
    unassigned_areas = [
        aid
        for aid, area in md.area_elements.items()
        if not getattr(area, "inactive", False) and aid not in md.area_assignments
    ]

    # ── Active elements assigned to undefined sections ──
    # Every assignment value must resolve to a section defined in
    # ``md.sections``; a dangling reference would abort section creation
    # (or silently drop the element) downstream, so it is blocking.
    undefined_section_refs = []
    for eid, elem in md.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sec_name = md.frame_assignments.get(eid)
        if sec_name is not None and sec_name not in md.sections:
            undefined_section_refs.append({"element": eid, "kind": "Frame", "section": sec_name})
    for aid, area in md.area_elements.items():
        if getattr(area, "inactive", False):
            continue
        sec_name = md.area_assignments.get(aid)
        if sec_name is not None and sec_name not in md.sections:
            undefined_section_refs.append({"element": aid, "kind": "Area", "section": sec_name})

    # ── Sections referencing missing materials ──
    missing_material_refs = [
        {"section": name, "material": sec.material}
        for name, sec in md.sections.items()
        if sec.material and sec.material not in md.materials
    ]

    # ── Degenerate (zero / near-zero length) frames ──
    zero_length_elements = []
    for eid, elem in md.frame_elements.items():
        ni, nj = md.nodes.get(elem.node_i), md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        length = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        if length <= tol:
            zero_length_elements.append({"element": eid, "length": length})

    # ── Duplicate / overlapping frames (same unordered node pair) ──
    pair_map: dict[frozenset, list[str]] = defaultdict(list)
    for eid, elem in md.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        pair_map[frozenset((elem.node_i, elem.node_j))].append(eid)
    duplicate_elements = [
        {"nodes": sorted(pair), "elements": elems}
        for pair, elems in pair_map.items()
        if len(elems) > 1
    ]

    # ── Unreferenced assets ──
    used_sections = set(md.frame_assignments.values()) | set(md.area_assignments.values())
    unreferenced_sections = [name for name in md.sections if name not in used_sections]
    used_materials = {sec.material for sec in md.sections.values() if sec.material}
    unreferenced_materials = [name for name in md.materials if name not in used_materials]

    # ── Loads pointing at missing targets / undefined patterns ──
    dangling = {"joints": [], "frames": [], "areas": []}
    used_patterns: set[str] = set()
    for jl in md.joint_loads:
        used_patterns.add(jl.pattern)
        if jl.node_id not in md.nodes:
            dangling["joints"].append(jl.node_id)
    for dl in md.frame_dist_loads:
        used_patterns.add(dl.pattern)
        if dl.frame_id not in md.frame_elements:
            dangling["frames"].append(dl.frame_id)
    for gl in md.frame_gravity_loads:
        used_patterns.add(gl.pattern)
        if gl.frame_id not in md.frame_elements:
            dangling["frames"].append(gl.frame_id)
    for al in md.area_uniform_loads:
        used_patterns.add(al.pattern)
        if al.area_id not in md.area_elements:
            dangling["areas"].append(al.area_id)
    for agl in md.area_gravity_loads:
        used_patterns.add(agl.pattern)
        if agl.area_id not in md.area_elements:
            dangling["areas"].append(agl.area_id)

    referenced_in_cases: set[str] = set()
    for lc in md.load_cases.values():
        referenced_in_cases |= set(patterns_from_case(lc).keys())
    active_patterns = used_patterns | referenced_in_cases
    unknown_patterns = sorted(p for p in active_patterns if p not in md.load_patterns)
    unreferenced_patterns = [name for name in md.load_patterns if name not in active_patterns]

    result: dict[str, Any] = {
        "missing_node_refs": missing_node_refs,
        "unassigned_frames": unassigned_frames,
        "unassigned_areas": unassigned_areas,
        "undefined_section_refs": undefined_section_refs,
        "missing_material_refs": missing_material_refs,
        "zero_length_elements": zero_length_elements,
        "duplicate_elements": duplicate_elements,
        "unreferenced_sections": unreferenced_sections,
        "unreferenced_materials": unreferenced_materials,
        "unreferenced_patterns": unreferenced_patterns,
        "dangling_loads": dangling,
        "unknown_patterns": unknown_patterns,
    }
    result["counts"] = {
        key: len(value) if isinstance(value, list) else sum(len(v) for v in value.values())
        for key, value in result.items()
    }
    return result


# ═══════════════════════════════════════════════════════════════════
# Engineering observations
# ═══════════════════════════════════════════════════════════════════


def _observations(md: SAPModelData) -> dict[str, Any]:
    """Derive engineering-review observations from the model data.

    These are not pass/fail checks — they surface modelling choices a
    reviewer should consciously confirm (support fixity, insertion
    points, mass source and auto-mesh usage).
    """
    restraint_patterns: dict[tuple, int] = defaultdict(int)
    for restraint in md.restraints.values():
        restraint_patterns[tuple(restraint.dofs)] += 1

    translation_only = bool(restraint_patterns) and all(
        pattern[:3] == (1, 1, 1) and pattern[3:] == (0, 0, 0) for pattern in restraint_patterns
    )
    fully_fixed = bool(restraint_patterns) and all(all(pattern) for pattern in restraint_patterns)

    non_default_cardinal = [
        {"element": eid, "cardinal_point": elem.cardinal_point}
        for eid, elem in md.frame_elements.items()
        if elem.cardinal_point != 10
    ]

    def _mass_source_entry(name, ms) -> dict[str, Any]:
        return {
            "name": name,
            "is_default": ms.is_default,
            "from_elements": ms.elements,
            "from_masses": ms.masses,
            "from_loads": ms.loads,
            "n_patterns": len(ms.load_pattern),
        }

    # Prefer the entry flagged as the model default — a model may list
    # several sources in arbitrary dictionary order, so stopping at the
    # first item can surface a non-default source.  When no source is
    # flagged default, fall back to the first entry (reported with
    # ``is_default=False``) so a source is still surfaced.
    mass_source = None
    for name, ms in md.mass_sources.items():
        if ms.is_default:
            mass_source = _mass_source_entry(name, ms)
            break
    if mass_source is None and md.mass_sources:
        name, ms = next(iter(md.mass_sources.items()))
        mass_source = _mass_source_entry(name, ms)

    return {
        "restraint_patterns": {
            "".join(str(d) for d in pattern): count
            for pattern, count in sorted(restraint_patterns.items(), key=lambda kv: -kv[1])
        },
        "all_translation_only": translation_only,
        "all_fully_fixed": fully_fixed,
        "non_default_cardinal_points": non_default_cardinal,
        "auto_mesh_assigned": len(md.frame_auto_mesh),
        "mass_source": mass_source,
    }


# ═══════════════════════════════════════════════════════════════════
# Optional solver-free checks (self-weight / brace buckling)
# ═══════════════════════════════════════════════════════════════════


def _self_weight(md: SAPModelData) -> dict[str, Any]:
    """Compute the model's analytical self-weight (solver-free).

    Delegates to
    :func:`~fea_toolkit.model.checks.check_self_weight_consistency`, which
    derives the expected weight from element geometry and material unit
    weights and returns it broken down by section.

    Confirming the *applied* load against the support reactions is the
    analysis phase's job (the ``load_verification`` block of the OpenSees
    pass), so the ``applied`` / ``discrepancy`` / ``passed`` keys are left
    as ``None`` here rather than reporting a misleading zero-applied
    comparison.

    Args:
        md: Parsed model data.

    Returns:
        Dict with ``expected``, ``by_section``, ``applied``,
        ``discrepancy`` and ``passed``.
    """
    sw = check_self_weight_consistency(md, verbose=False)
    return {
        "expected": sw["expected"],
        "by_section": sw["by_section"],
        "applied": None,
        "discrepancy": None,
        "passed": None,
    }


def _brace_buckling(md: SAPModelData, k_factor: float = 1.0) -> dict[str, Any]:
    """Run the Euler brace-buckling check when the model has braces.

    Braces are auto-detected by section shape (Pipe / Angle / Double
    Angle / Tee / Channel) through
    :meth:`~fea_toolkit.model.selection.Selection.from_brace_sections`.
    When the model contains no brace sections the check is skipped and
    ``detected`` is ``False`` — no Euler capacity is fabricated for
    non-brace members.

    Args:
        md: Parsed model data.
        k_factor: Effective length factor ``K`` (default 1.0 —
            pinned-pinned).

    Returns:
        Dict with ``detected`` (bool), ``k_factor`` (float) and
        ``members`` (``{elem_id: {P_cr, P_demand, ratio, slenderness,
        length, section, A, I22}}`` — empty when no braces are present).
    """
    from .selection import Selection

    brace_ids = set(Selection.from_brace_sections(md).get_frame_ids(md))
    if not brace_ids:
        return {"detected": False, "k_factor": k_factor, "members": {}}
    members = check_brace_buckling(md, brace_ids=brace_ids, K=k_factor, print_results=False)
    if not members:
        return {"detected": False, "k_factor": k_factor, "members": {}}
    return {"detected": True, "k_factor": k_factor, "members": members}


# ═══════════════════════════════════════════════════════════════════
# Optional OpenSees analysis phase
# ═══════════════════════════════════════════════════════════════════


def _run_analysis(md: SAPModelData, config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run the optional OpenSees modal + linear-static phase.

    Thin adapter delegating to
    :func:`~fea_toolkit.opensees.analysis_builder.run_review_analysis`,
    which owns the OpenSees domain construction and analysis execution.
    The import is lazy so this module stays importable without an OpenSees
    runtime.

    Args:
        md: Parsed model data.
        config: Optional builder config dict.

    Returns:
        Dict with ``ok``, ``periods``, ``mass_participation``, ``static``
        and ``error`` keys.
    """
    try:
        from ..opensees.analysis_builder import run_review_analysis
    except Exception as exc:  # pragma: no cover - optional environment
        return {
            "ok": False,
            "periods": [],
            "mass_participation": [],
            "static": None,
            "error": f"OpenSees not available: {exc}",
        }
    return run_review_analysis(md, config)


def _write_geometry_npz(md: SAPModelData, path: Any) -> tuple[Optional[str], Optional[str]]:
    """Write a geometry-only NPZ (no OpenSees required).

    Used when ``export_npz`` is requested **without** the analysis phase.
    The canonical :func:`~fea_toolkit.io.npz_writer.write_results_npz`
    writer is imported lazily so the review module stays importable
    without the I/O stack loaded.

    Args:
        md: Parsed model data.
        path: Output ``.npz`` file path.

    Returns:
        ``(npz_path, error)`` — the resolved path (or ``None``) and an
        ``"ExcType: message"`` string (or ``None``).  Failures are captured
        rather than raised so a review never aborts on an export error.
    """
    try:
        from ..io.npz_writer import write_results_npz

        return write_results_npz(str(path), md), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# ═══════════════════════════════════════════════════════════════════
# Public review API
# ═══════════════════════════════════════════════════════════════════

_BLOCKING_INTEGRITY = (
    "missing_node_refs",
    "unassigned_frames",
    "unassigned_areas",
    "undefined_section_refs",
    "missing_material_refs",
    "zero_length_elements",
    "unknown_patterns",
    "dangling_loads",
)


def _is_clean(result: dict[str, Any]) -> bool:
    """Return True when the review found no blocking issues."""
    conn = result["connectivity"]
    if conn["orphan_nodes"] or conn["duplicate_coords"] or conn["floating_components"]:
        return False
    counts = result["integrity"]["counts"]
    if any(counts.get(key) for key in _BLOCKING_INTEGRITY):
        return False
    analysis = result.get("analysis")
    return analysis is None or bool(analysis.get("ok"))


def review_model(
    md: SAPModelData,
    *,
    file: Optional[Any] = None,
    tol: float = 1e-6,
    include_analysis: bool = False,
    analysis_config: Optional[dict[str, Any]] = None,
    self_weight: bool = False,
    brace_buckling: bool = False,
    brace_k: float = 1.0,
    export_npz: Optional[Any] = None,
) -> dict[str, Any]:
    """Run the full model review on parsed SAP2000 data.

    Args:
        md: Parsed model data (:class:`SAPModelData`).
        file: Optional source path recorded in the result.
        tol: Coordinate / length tolerance for duplicate-coordinate and
            degenerate-element detection.
        include_analysis: When True, run the optional OpenSees
            modal + linear-static phase (requires openseespy; slow).
        analysis_config: Optional builder config for the analysis phase.
            Additional keys ``load_verify`` and ``wind_check`` (bool)
            enable the applied-vs-reaction equilibrium table and the
            wind-load sanity check respectively.
        self_weight: When True, add the solver-free analytical
            self-weight block (element weight by section) under
            ``result["self_weight"]``.
        brace_buckling: When True, run the Euler brace-buckling check
            under ``result["brace_buckling"]``.  The check is skipped
            (``detected=False``) when the model has no brace sections.
        brace_k: Effective length factor ``K`` for the brace-buckling
            check (default 1.0).
        export_npz: Optional output ``.npz`` path.  When given, a unified
            NPZ archive (geometry, per :func:`~fea_toolkit.io.npz_writer.
            write_results_npz`) is written under ``result["npz"]``.
            With ``include_analysis=True`` the archive also carries the
            meshed geometry plus the modal and static results; without it
            only the raw model geometry is written.

    Returns:
        A nested dict with keys ``file``, ``units``, ``inventory``,
        ``breakdown``, ``bounds``, ``connectivity``, ``releases``,
        ``integrity``, ``observations``, ``self_weight``,
        ``brace_buckling``, ``analysis``, ``npz``, ``npz_error`` and
        ``ok``.
    """
    if not math.isfinite(tol) or tol < 0:
        raise ValueError(f"tol must be a finite, non-negative number, got {tol!r}")
    connectivity_report = check_model_connectivity(md, tol=tol)
    components = _analyse_components(md)
    floating = [
        comp
        for comp in components
        if not comp["is_supported"] and (comp["n_frames"] + comp["n_areas"]) > 0
    ]

    result: dict[str, Any] = {
        "file": str(file) if file is not None else None,
        "units": dict(md.units),
        "inventory": _inventory(md),
        "breakdown": _breakdown(md),
        "bounds": _bounds(md),
        "connectivity": {
            "orphan_nodes": connectivity_report["orphan_nodes"],
            "duplicate_coords": connectivity_report["duplicate_coords"],
            "shell_only_base_nodes": connectivity_report["shell_only_base_nodes"],
            "zero_area_sections": connectivity_report["zero_area_sections"],
            "n_components": len(components),
            "components": components,
            "floating_components": floating,
        },
        "releases": _release_summary(md),
        "integrity": _integrity(md, tol),
        "observations": _observations(md),
        "self_weight": _self_weight(md) if self_weight else None,
        "brace_buckling": _brace_buckling(md, brace_k) if brace_buckling else None,
        "analysis": None,
        "npz": None,
        "npz_error": None,
    }

    if include_analysis:
        analysis_cfg = dict(analysis_config or {})
        if export_npz and not analysis_cfg.get("export_npz"):
            analysis_cfg["export_npz"] = str(export_npz)
        result["analysis"] = _run_analysis(md, analysis_cfg or None)
        # Surface the export result at the top level for both paths.
        result["npz"] = result["analysis"].get("npz")
        result["npz_error"] = result["analysis"].get("npz_error")
    elif export_npz:
        # Geometry-only export — no OpenSees domain required.
        result["npz"], result["npz_error"] = _write_geometry_npz(md, export_npz)

    result["ok"] = _is_clean(result)
    return result


def review_s2k_file(
    path: Any,
    *,
    tol: float = 1e-6,
    include_analysis: bool = False,
    analysis_config: Optional[dict[str, Any]] = None,
    self_weight: bool = False,
    brace_buckling: bool = False,
    brace_k: float = 1.0,
    export_npz: Optional[Any] = None,
) -> dict[str, Any]:
    """Parse a ``.s2k`` file and run the full review on it.

    Args:
        path: Path to the ``.s2k`` / ``.$2k`` file (ETABS ``.e2k`` / ``.$et``
            is planned, not yet supported).
        tol: Forwarded to :func:`review_model`.
        include_analysis: Forwarded to :func:`review_model`.
        analysis_config: Forwarded to :func:`review_model`.
        self_weight: Forwarded to :func:`review_model`.
        brace_buckling: Forwarded to :func:`review_model`.
        brace_k: Forwarded to :func:`review_model`.
        export_npz: Forwarded to :func:`review_model`.

    Returns:
        The review result dict (see :func:`review_model`).
    """
    from ..io.s2k_parser import SAP2000Parser

    source = Path(path)
    parser = SAP2000Parser(source)
    parser.parse()
    md = parser.get_model_data()
    return review_model(
        md,
        file=source,
        tol=tol,
        include_analysis=include_analysis,
        analysis_config=analysis_config,
        self_weight=self_weight,
        brace_buckling=brace_buckling,
        brace_k=brace_k,
        export_npz=export_npz,
    )


# ═══════════════════════════════════════════════════════════════════
# Reporting
# ═══════════════════════════════════════════════════════════════════


def _display_modes(
    analysis: dict[str, Any],
    max_modes: int = 0,
    min_participation: float = 0.0,
) -> tuple[list[dict[str, Any]], int]:
    """Select the modal rows a report should display.

    Args:
        analysis: The ``analysis`` sub-dict of a review result.
        max_modes: Cap on the number of rows displayed; ``0`` (or ``None``)
            shows every mode.
        min_participation: Drop modes whose largest translational mass
            participation (the max of ``MX`` / ``MY`` / ``MZ``, in percent)
            falls below this value.  ``0.0`` keeps every mode.

    Returns:
        ``(rows, hidden)`` — the rows to display, in mode order, and the
        number of rows suppressed by the two filters combined.
    """
    rows = list(analysis.get("mass_participation") or [])
    total = len(rows)
    if min_participation and min_participation > 0.0:
        rows = [
            row
            for row in rows
            if max(
                abs(float(row.get("mx", 0.0))),
                abs(float(row.get("my", 0.0))),
                abs(float(row.get("mz", 0.0))),
            )
            >= min_participation
        ]
    if max_modes and max_modes > 0:
        rows = rows[:max_modes]
    return rows, total - len(rows)


def _format_table(rows: list[dict[str, Any]], tablefmt: str = "grid") -> str:
    """Render a list of row dicts as a table string.

    Uses :mod:`tabulate` — an *optional* dependency (``pip install -e
    ".[report]"``) — when it is importable, so the plain-text report gains
    bordered/gridded tables without making the solver-free review depend on
    a third-party library.  When ``tabulate`` is absent the function falls
    back to a dependency-free fixed-width layout (or, for Markdown formats,
    a native pipe table), keeping the review importable everywhere.

    Values are rendered verbatim, so callers should pre-format numbers
    (e.g. ``f"{value:.1f}"``) to control precision and unit suffixes.

    Args:
        rows: Row dicts.  The keys of the first row define the columns
            and their order.
        tablefmt: ``tabulate`` table format (default ``"grid"``).  The
            Markdown formats ``"github"`` / ``"pipe"`` / ``"markdown"``
            also select a pipe table in the dependency-free fallback.

    Returns:
        The rendered table, or an empty string when *rows* is empty.
    """
    if not rows:
        return ""
    cols = list(rows[0].keys())

    try:
        from tabulate import tabulate as _tabulate
    except ImportError:
        _tabulate = None
    if _tabulate is not None:
        # ``disable_numparse`` keeps caller-formatted values verbatim (e.g.
        # "0.1250" is not collapsed to 0.125) and left-aligns them, so the
        # rendered table matches the strings the formatters built.
        return _tabulate(rows, headers="keys", tablefmt=tablefmt, disable_numparse=True)

    # ── Dependency-free fallback ──────────────────────────────────
    if tablefmt in ("github", "pipe", "markdown"):
        header = "| " + " | ".join(str(c) for c in cols) + " |"
        separator = "|" + "|".join("---" for _ in cols) + "|"
        body = ["| " + " | ".join(str(row.get(c, "")) for c in cols) + " |" for row in rows]
        return "\n".join([header, separator, *body])

    widths = {c: len(str(c)) for c in cols}
    for row in rows:
        for c in cols:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    lines = ["  ".join(str(c).ljust(widths[c]) for c in cols)]
    lines.append("  ".join("-" * widths[c] for c in cols))
    lines.extend("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols) for row in rows)
    return "\n".join(lines)


def _apply_indent(text: str, prefix: str = "  ") -> str:
    """Indent every non-empty line of *text* by *prefix*."""
    return "\n".join((prefix + line) if line else line for line in text.splitlines())


def _limit_rows(rows: list[Any], limit: int = 0) -> tuple[list[Any], int]:
    """Apply an optional display cap, mirroring ``_display_modes``.

    Args:
        rows: Rows to display.
        limit: Maximum rows to keep; ``0`` (or ``None``) keeps every row.

    Returns:
        ``(shown, hidden)`` — the capped rows and the number suppressed.
    """
    if limit and limit > 0 and len(rows) > limit:
        return rows[:limit], len(rows) - limit
    return rows, 0


# (result key, column label) for the six mass-participation ratios.
_MODAL_RATIO_COLS = (
    ("mx", "Mx"),
    ("my", "My"),
    ("mz", "Mz"),
    ("rx", "Rx"),
    ("ry", "Ry"),
    ("rz", "Rz"),
)


def _modal_table_rows(
    rows: list[dict[str, Any]],
    totals: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Build table rows for modal participation entries.

    Renders the rotational (Rx/Ry/Rz) as well as the translational
    (Mx/My/Mz) mass-participation ratios — the 6-DOF presentation used by
    the report pipeline's ``modal_table_enhanced()`` — and, when *totals*
    is supplied, appends a final ``SUM`` row.

    Args:
        rows: ``mass_participation`` entries from a review result.
        totals: Optional per-ratio sums (see :func:`_modal_totals`), added
            as a trailing ``SUM`` row.

    Returns:
        Row dicts with pre-formatted string values (see
        :func:`_format_table`).
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "Mode": str(row.get("mode", "")),
            "Period (s)": f"{float(row.get('period', 0.0)):.4f}",
        }
        for key, label in _MODAL_RATIO_COLS:
            entry[f"{label} (%)"] = f"{float(row.get(key, 0.0)):.2f}"
        out.append(entry)
    if totals:
        entry = {"Mode": "SUM", "Period (s)": "\u2014"}
        for key, label in _MODAL_RATIO_COLS:
            entry[f"{label} (%)"] = f"{float(totals.get(key, 0.0)):.2f}"
        out.append(entry)
    return out


def _modal_totals(analysis: dict[str, Any]) -> dict[str, float]:
    """Sum mass participation across **all** modes.

    Mirrors the SUM row of ``modal_table_enhanced()`` but sums every mode
    (not just the displayed subset), so a filtered table still reports the
    full cumulative participation.

    Args:
        analysis: The ``analysis`` sub-dict of a review result.

    Returns:
        Dict of per-ratio sums keyed ``mx``/``my``/``mz``/``rx``/``ry``/
        ``rz``; empty when there are no modal rows.
    """
    rows = analysis.get("mass_participation") or []
    if not rows:
        return {}
    return {key: sum(float(row.get(key, 0.0)) for row in rows) for key, _ in _MODAL_RATIO_COLS}


def _reaction_table_rows(
    reactions: dict[str, float],
    force_unit: str,
    length_unit: str,
) -> list[dict[str, Any]]:
    """Build a single-row table for the summed support reactions.

    Args:
        reactions: ``{"fx": ..., ..., "mz": ...}`` summed reactions.
        force_unit: Force-unit label (e.g. ``"kN"``).
        length_unit: Length-unit label (e.g. ``"m"``).

    Returns:
        A one-row list of pre-formatted values (see :func:`_format_table`).
    """
    row: dict[str, Any] = {"Reaction": "Summed (all supports)"}
    for comp, label in (("fx", "Fx"), ("fy", "Fy"), ("fz", "Fz")):
        row[f"{label} ({force_unit})"] = f"{float(reactions.get(comp, 0.0)):,.3g}"
    for comp, label in (("mx", "Mx"), ("my", "My"), ("mz", "Mz")):
        row[f"{label} ({force_unit}\u00b7{length_unit})"] = (
            f"{float(reactions.get(comp, 0.0)):,.3g}"
        )
    return [row]


def _mass_unit_label(units: dict[str, Any]) -> str:
    """Return a display label for the model's consistent mass unit.

    Mass is force·time²/length; SAP2000 analyses always use seconds, so the
    label derives from the force/length pair (``kN``‑``m`` → tonnes,
    ``N``‑``m`` → kg, ``lbf``‑``ft`` → slugs).  Unrecognised pairs fall back
    to the explicit ``F·s²/L`` form.

    Args:
        units: Model units dict, e.g. ``{"F": "KN", "L": "m"}``.

    Returns:
        Conventional mass-unit label (e.g. ``"t"``).
    """
    fu = force_unit_label(units)
    lu = length_unit_label(units)
    return {
        ("kN", "m"): "t",
        ("N", "m"): "kg",
        ("lbf", "ft"): "slug",
        ("lb", "ft"): "slug",
    }.get((fu, lu), f"{fu}\u00b7s\u00b2/{lu}")


def format_review_report(
    result: dict[str, Any],
    max_modes: int = 0,
    min_participation: float = 0.0,
    num_braces: int = 0,
) -> str:
    """Render a review result as a plain-text report.

    Args:
        result: Review dict returned by :func:`review_model`.
        max_modes: Cap on the number of modal rows displayed; ``0`` shows
            every computed mode (the default).
        min_participation: Hide modes whose largest translational mass
            participation is below this percentage (``0.0`` = show all).
        num_braces: Cap on the number of brace rows displayed in the
            brace-buckling section; ``0`` shows every brace.

    Returns:
        A multi-line plain-text report.
    """
    inv = result["inventory"]
    breakdown = result["breakdown"]
    conn = result["connectivity"]
    releases = result["releases"]
    integrity = result["integrity"]
    observations = result["observations"]
    units = result["units"]
    lu = units.get("L", "m")

    lines: list[str] = []
    add = lines.append

    add("=" * 70)
    add("SAP2000 MODEL REVIEW")
    if result.get("file"):
        add(f"  File : {result['file']}")
    add(f"  Units: {units.get('F')}, {units.get('L')}, {units.get('T')}")
    add(f"  Status: {'PASS - no blocking issues' if result['ok'] else 'ISSUES FOUND'}")
    if result.get("npz"):
        add(f"  NPZ  : {result['npz']}")
    if result.get("npz_error"):
        add(f"  NPZ  : FAILED - {result['npz_error']}")
    add("=" * 70)

    add("")
    add("-- Inventory " + "-" * 57)
    for key, value in inv.items():
        add(f"  {key:<34}{value:>10}")

    add("")
    add("-- Breakdown " + "-" * 57)
    for name, counts in breakdown.items():
        if not counts:
            continue
        add(f"  {name}:")
        for key, value in counts.items():
            add(f"      {key:<26}{value:>8}")

    bounds = result.get("bounds")
    if bounds:
        add("")
        add("-- Bounding box " + "-" * 54)
        add(
            f"  X: {bounds['x_min']:.3f} .. {bounds['x_max']:.3f}  (span {bounds['x_span']:.3f} {lu})"
        )
        add(
            f"  Y: {bounds['y_min']:.3f} .. {bounds['y_max']:.3f}  (span {bounds['y_span']:.3f} {lu})"
        )
        add(
            f"  Z: {bounds['z_min']:.3f} .. {bounds['z_max']:.3f}  (span {bounds['z_span']:.3f} {lu})"
        )

    add("")
    add("-- Connectivity " + "-" * 54)
    add(f"  Connected components:                {conn['n_components']:>8}")
    add(f"  Orphan (loose) nodes:                {len(conn['orphan_nodes']):>8}")
    add(f"  Duplicate coordinates:               {len(conn['duplicate_coords']):>8}")
    add(f"  Floating sub-structures (unsupported):{len(conn['floating_components']):>7}")
    for comp in conn["floating_components"]:
        add(f"      - nodes={comp['n_nodes']} frames={comp['n_frames']} areas={comp['n_areas']}")

    add("")
    add("-- Element releases " + "-" * 50)
    add(f"  Frames with releases: {releases['n_frames_with_releases']}")
    if releases["n_frames_with_releases"]:
        for key, value in releases["by_dof"].items():
            if value:
                add(f"      {key:<6}{value:>8}")

    add("")
    add("-- Integrity " + "-" * 57)
    for key, value in integrity["counts"].items():
        add(f"  {key:<34}{value:>7}  [{'OK' if value == 0 else 'REVIEW'}]")

    add("")
    add("-- Observations " + "-" * 54)
    add(f"  Support DOF patterns: {observations['restraint_patterns']}")
    if observations["all_translation_only"]:
        add("  All supports are translation-only (pinned / simply supported bases).")
    if observations["all_fully_fixed"]:
        add("  All supports are fully fixed.")
    add(
        f"  Non-default insertion (cardinal) points: {len(observations['non_default_cardinal_points'])}"
    )
    add(f"  Frames with auto-mesh assignment: {observations['auto_mesh_assigned']}")
    ms = observations["mass_source"]
    if ms is None:
        add("  Mass source: NONE DEFINED")
    else:
        add(
            f"  Mass source: '{ms['name']}' (default={ms['is_default']}, "
            f"elements={ms['from_elements']}, masses={ms['from_masses']}, "
            f"loads={ms['from_loads']}, patterns={ms['n_patterns']})"
        )

    force_unit = units.get("F", "N")

    self_weight = result.get("self_weight")
    if self_weight is not None:
        add("")
        add("-- Self-weight (analytical) " + "-" * 42)
        add(f"  Expected self-weight: {self_weight['expected']:.1f} {force_unit}")
        if self_weight.get("passed") is not None:
            status = "PASS" if self_weight["passed"] else "FAIL"
            add(
                f"  Applied {self_weight.get('applied')} {force_unit} / "
                f"discrepancy {self_weight.get('discrepancy')}  [{status}]"
            )
        by_section = self_weight.get("by_section") or {}
        if by_section:
            rows = [
                {"Section": name, f"Weight ({force_unit})": f"{weight:.1f}"}
                for name, weight in sorted(by_section.items(), key=lambda kv: -kv[1])
            ]
            rows.append(
                {"Section": "Total", f"Weight ({force_unit})": f"{self_weight['expected']:.1f}"}
            )
            add(_apply_indent(_format_table(rows)))

    brace = result.get("brace_buckling")
    if brace is not None:
        add("")
        add("-- Brace buckling " + "-" * 51)
        if not brace.get("detected"):
            add("  No brace sections found in model.")
        else:
            members = brace["members"]
            rows = [
                {
                    "Element": eid,
                    "Section": r["section"],
                    f"Length ({lu})": f"{r['length']:.3f}",
                    "Slenderness": f"{r['slenderness']:.1f}",
                    f"P_cr ({force_unit})": f"{r['P_cr']:.0f}",
                }
                for eid, r in sorted(members.items(), key=lambda kv: -kv[1]["length"])
            ]
            shown, hidden = _limit_rows(rows, num_braces)
            add(f"  Effective length factor K = {brace['k_factor']}   braces = {len(members)}")
            add(_apply_indent(_format_table(shown)))
            if hidden:
                add(f"      ... {hidden} further brace(s) not shown")

    analysis = result.get("analysis")
    if analysis is not None:
        add("")
        add("-- Analysis (OpenSees) " + "-" * 47)
        if analysis["ok"]:
            add(f"  Static + modal: OK ({len(analysis['periods'])} modes)")
            rows, hidden = _display_modes(analysis, max_modes, min_participation)
            add(
                _apply_indent(
                    _format_table(_modal_table_rows(rows, _modal_totals(analysis) or None))
                )
            )
            if hidden:
                add(f"      ... {hidden} further mode(s) not shown")
            static = analysis.get("static") or {}
            add(f"  Patterns applied: {static.get('patterns_applied')}")
            add(f"  Supports with reactions: {static.get('n_supports')}")
            reactions = static.get("summed_reactions")
            if reactions:
                add(_apply_indent(_format_table(_reaction_table_rows(reactions, force_unit, lu))))
            mass = analysis.get("mass_source")
            if mass:
                add(
                    f"  Seismic mass (mass source): {mass['total_mass']:,.3f} "
                    f"{_mass_unit_label(units)}  =  weight "
                    f"{mass['total_weight']:,.1f} {force_unit}"
                )
        else:
            add(f"  FAILED: {analysis['error']}")

        lv = analysis.get("load_verification")
        if lv:
            add("")
            add("  -- Load verification (applied vs reactions) --")
            add(_apply_indent(_format_table(lv)))
        elif analysis.get("load_verification_error"):
            add(f"  Load verification FAILED: {analysis['load_verification_error']}")

        wind = analysis.get("wind")
        if wind:
            add("")
            add("  -- Wind sanity check --")
            if isinstance(wind, dict):
                add(_apply_indent(_format_table(wind.get("rows") or [])))
                add(
                    "  Basis: pressure = |base reaction| / projected face area "
                    "(+X wind \u2192 Y\u00b7Z face, +Y wind \u2192 X\u00b7Z face), "
                    "from the Wind+X / Wind+Y load cases."
                )
                if wind.get("within_10pct"):
                    add("  Pressures are within 10 % of each other.")
            else:
                add(_apply_indent(str(wind)))
        elif analysis.get("wind_error"):
            add(f"  Wind check FAILED: {analysis['wind_error']}")

    add("")
    add("=" * 70)
    return "\n".join(lines)


def print_review_report(
    result: dict[str, Any],
    max_modes: int = 0,
    min_participation: float = 0.0,
    num_braces: int = 0,
) -> None:
    """Print the plain-text review report (see :func:`format_review_report`).

    Args:
        result: Review dict returned by :func:`review_model`.
        max_modes: Cap on the number of modal rows displayed (``0`` = all).
        min_participation: Hide modes below this mass-participation
            percentage (``0.0`` = show all).
        num_braces: Cap on the number of brace rows displayed (``0`` = all).
    """
    print(
        format_review_report(
            result,
            max_modes=max_modes,
            min_participation=min_participation,
            num_braces=num_braces,
        )
    )


def format_review_markdown(
    result: dict[str, Any],
    max_modes: int = 0,
    min_participation: float = 0.0,
    num_braces: int = 0,
) -> str:
    """Render a review result as a Markdown document.

    Args:
        result: Review dict returned by :func:`review_model`.
        max_modes: Cap on the number of modal rows displayed; ``0`` shows
            every computed mode (the default).
        min_participation: Hide modes whose largest translational mass
            participation is below this percentage (``0.0`` = show all).
        num_braces: Cap on the number of brace rows displayed in the
            brace-buckling section; ``0`` shows every brace.

    Returns:
        A Markdown string with inventory, connectivity, releases,
        integrity, observation, self-weight, brace-buckling and analysis
        sections.
    """
    inv = result["inventory"]
    units = result["units"]
    lu = units.get("L", "m")
    conn = result["connectivity"]
    releases = result["releases"]
    integrity = result["integrity"]
    observations = result["observations"]

    md: list[str] = []
    add = md.append
    add("# SAP2000 Model Review")
    add("")
    if result.get("file"):
        add(f"**File:** `{result['file']}`  ")
    add(f"**Units:** {units.get('F')}, {units.get('L')}, {units.get('T')}  ")
    add(f"**Status:** {'PASS - no blocking issues' if result['ok'] else 'ISSUES FOUND'}  ")
    if result.get("npz"):
        add(f"**NPZ:** `{result['npz']}`  ")
    if result.get("npz_error"):
        add(f"**NPZ export failed:** {result['npz_error']}  ")
    add("")

    add("## Inventory")
    add("")
    add("| Object | Count |")
    add("|---|---:|")
    for key, value in inv.items():
        add(f"| {key.replace('_', ' ')} | {value} |")
    add("")

    add("## Breakdown")
    add("")
    for name, counts in result["breakdown"].items():
        if not counts:
            continue
        joined = ", ".join(f"`{key}`={value}" for key, value in counts.items())
        add(f"**{name.replace('_', ' ')}:** {joined}")
        add("")

    add("## Connectivity")
    add("")
    add(f"- Connected components: **{conn['n_components']}**")
    add(f"- Orphan (loose) nodes: **{len(conn['orphan_nodes'])}**")
    add(f"- Duplicate coordinates: **{len(conn['duplicate_coords'])}**")
    add(f"- Floating sub-structures (no support): **{len(conn['floating_components'])}**")
    add("")

    add("## Element releases")
    add("")
    add(f"Frames with releases: **{releases['n_frames_with_releases']}**")
    add("")
    if releases["n_frames_with_releases"]:
        add("| Frame | End I | End J |")
        add("|---|---|---|")
        for row in releases["releases"][:100]:
            end_i = ", ".join(row["end_i"]) or "—"
            end_j = ", ".join(row["end_j"]) or "—"
            add(f"| {row['frame_id']} | {end_i} | {end_j} |")
        add("")

    add("## Integrity")
    add("")
    add("| Check | Count | Status |")
    add("|---|---:|---|")
    for key, value in integrity["counts"].items():
        add(f"| {key.replace('_', ' ')} | {value} | {'OK' if value == 0 else 'REVIEW'} |")
    add("")

    add("## Observations")
    add("")
    add(f"- Support DOF patterns (U1U2U3R1R2R3): `{observations['restraint_patterns']}`")
    add(f"- All supports translation-only: **{observations['all_translation_only']}**")
    add(f"- All supports fully fixed: **{observations['all_fully_fixed']}**")
    add(f"- Non-default insertion points: **{len(observations['non_default_cardinal_points'])}**")
    add(f"- Frames with auto-mesh: **{observations['auto_mesh_assigned']}**")
    ms = observations["mass_source"]
    add(f"- Mass source: **{ms['name'] if ms else 'NONE'}**")
    add("")

    force_unit = units.get("F", "N")

    self_weight = result.get("self_weight")
    if self_weight is not None:
        add("## Self-weight (analytical)")
        add("")
        add(f"Expected self-weight: **{self_weight['expected']:.1f} {force_unit}**")
        add("")
        if self_weight.get("passed") is not None:
            status = "PASS" if self_weight["passed"] else "FAIL"
            add(
                f"Applied: `{self_weight.get('applied')}` {force_unit} — "
                f"discrepancy `{self_weight.get('discrepancy')}` — **{status}**"
            )
            add("")
        by_section = self_weight.get("by_section") or {}
        if by_section:
            rows = [
                {"Section": name, f"Weight ({force_unit})": f"{weight:.1f}"}
                for name, weight in sorted(by_section.items(), key=lambda kv: -kv[1])
            ]
            rows.append(
                {"Section": "Total", f"Weight ({force_unit})": f"{self_weight['expected']:.1f}"}
            )
            add(_format_table(rows, tablefmt="github"))
            add("")

    brace = result.get("brace_buckling")
    if brace is not None:
        add("## Brace buckling")
        add("")
        if not brace.get("detected"):
            add("_No brace sections found in model._")
            add("")
        else:
            members = brace["members"]
            add(
                f"Effective length factor K = {brace['k_factor']}. "
                f"Braces checked: **{len(members)}**."
            )
            add("")
            rows = [
                {
                    "Element": eid,
                    "Section": r["section"],
                    f"Length ({lu})": f"{r['length']:.3f}",
                    "Slenderness": f"{r['slenderness']:.1f}",
                    f"P_cr ({force_unit})": f"{r['P_cr']:.0f}",
                }
                for eid, r in sorted(members.items(), key=lambda kv: -kv[1]["length"])
            ]
            shown, hidden = _limit_rows(rows, num_braces)
            add(_format_table(shown, tablefmt="github"))
            add("")
            if hidden:
                add(f"_{hidden} further brace(s) not shown._")
                add("")

    analysis = result.get("analysis")
    if analysis is not None:
        add("## Analysis (OpenSees)")
        add("")
        if analysis["ok"]:
            add(f"Static + modal completed ({len(analysis['periods'])} modes).")
            add("")
            rows, hidden = _display_modes(analysis, max_modes, min_participation)
            add(
                _format_table(
                    _modal_table_rows(rows, _modal_totals(analysis) or None),
                    tablefmt="github",
                )
            )
            add("")
            if hidden:
                add(f"_{hidden} further mode(s) not shown._")
                add("")
            static = analysis.get("static") or {}
            add(
                f"Patterns applied: **{static.get('patterns_applied')}** \u00b7 "
                f"supports with reactions: **{static.get('n_supports')}**"
            )
            add("")
            reactions = static.get("summed_reactions")
            if reactions:
                add(
                    _format_table(
                        _reaction_table_rows(reactions, force_unit, lu), tablefmt="github"
                    )
                )
                add("")
            mass = analysis.get("mass_source")
            if mass:
                add(
                    f"Seismic mass (mass source): **{mass['total_mass']:,.3f} "
                    f"{_mass_unit_label(units)}** \u2014 weight "
                    f"**{mass['total_weight']:,.1f} {force_unit}**."
                )
                add("")
        else:
            add(f"**FAILED:** {analysis['error']}")
        add("")

        lv = analysis.get("load_verification")
        if lv:
            add("### Load verification (applied vs reactions)")
            add("")
            add(_format_table(lv, tablefmt="github"))
            add("")
        elif analysis.get("load_verification_error"):
            add(f"**Load verification FAILED:** {analysis['load_verification_error']}")
            add("")

        wind = analysis.get("wind")
        if wind:
            add("### Wind sanity check")
            add("")
            if isinstance(wind, dict):
                add(_format_table(wind.get("rows") or [], tablefmt="github"))
                add("")
                add(
                    "_Basis: pressure = |base reaction| / projected face area "
                    "(+X wind \u2192 Y\u00b7Z face, +Y wind \u2192 X\u00b7Z face), "
                    "from the Wind+X / Wind+Y load cases._"
                )
                add("")
                if wind.get("within_10pct"):
                    add("_Pressures are within 10 % of each other._")
                    add("")
            else:
                add(str(wind))
                add("")
        elif analysis.get("wind_error"):
            add(f"**Wind check FAILED:** {analysis['wind_error']}")
            add("")

    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════════
# Command-line interface
# ═══════════════════════════════════════════════════════════════════


def main(argv: Optional[list[str]] = None) -> int:
    """Command-line entry point for the SAP2000 model review.

    The model file is always supplied by the caller — nothing is
    hard-coded to a project directory.

    Usage::

        python -m fea_toolkit.model.review path/to/model.s2k
        python -m fea_toolkit.model.review model.s2k --analysis
        python -m fea_toolkit.model.review model.s2k --format markdown --out review.md
        python -m fea_toolkit.model.review model.s2k --analysis --min-participation 1
        python -m fea_toolkit.model.review model.s2k --analysis --max-modes 3
        python -m fea_toolkit.model.review model.s2k --self-weight --brace-buckling
        python -m fea_toolkit.model.review model.s2k --load-verify --wind-check
        python -m fea_toolkit.model.review model.s2k --analysis --npz results.npz

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` when the review is clean, ``1`` when
        issues were found, ``2`` on a usage/file error.
    """
    parser = argparse.ArgumentParser(
        prog="fea_toolkit.model.review",
        description="Review and check a SAP2000 .s2k model file.",
    )
    parser.add_argument(
        "path",
        help="Path to the .s2k / .$2k file to review (ETABS .e2k / .$et planned, not yet supported).",
    )
    parser.add_argument(
        "--analysis",
        action="store_true",
        help="Run the optional OpenSees modal + linear-static pass.",
    )
    parser.add_argument(
        "--tol",
        type=float,
        default=1e-6,
        help="Coordinate / length tolerance (default: 1e-6).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "markdown"),
        default="text",
        help="Report format (default: text).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the report to this file instead of stdout.",
    )
    parser.add_argument(
        "--num-modes",
        type=int,
        default=12,
        help="Number of modes to compute in the analysis pass (default: 12).",
    )
    parser.add_argument(
        "--max-modes",
        type=int,
        default=0,
        help="Cap the number of modal rows shown in the report (default: 0 = all).",
    )
    parser.add_argument(
        "--min-participation",
        type=float,
        default=0.0,
        metavar="PCT",
        help=(
            "Hide modes whose largest translational mass participation "
            "(max of MX/MY/MZ) is below PCT percent (default: 0 = show all)."
        ),
    )
    parser.add_argument(
        "--self-weight",
        action="store_true",
        help="Report the analytical self-weight (element weight by section).",
    )
    parser.add_argument(
        "--brace-buckling",
        action="store_true",
        help=(
            "Run the Euler brace-buckling check.  Skipped automatically when "
            "the model contains no brace sections."
        ),
    )
    parser.add_argument(
        "--k-factor",
        type=float,
        default=1.0,
        help="Effective length factor K for --brace-buckling (default: 1.0).",
    )
    parser.add_argument(
        "--num-braces",
        type=int,
        default=0,
        help="Cap the number of brace rows shown in the report (default: 0 = all).",
    )
    parser.add_argument(
        "--load-verify",
        action="store_true",
        help="Run applied-vs-reaction load verification (implies --analysis).",
    )
    parser.add_argument(
        "--wind-check",
        action="store_true",
        help="Run the wind load sanity check (implies --analysis).",
    )
    parser.add_argument(
        "--npz",
        default=None,
        metavar="PATH",
        help=(
            "Write a unified NPZ archive to PATH.  Without --analysis the "
            "archive contains the model geometry only; with --analysis it "
            "also contains the meshed geometry plus the modal and static "
            "results."
        ),
    )
    args = parser.parse_args(argv)

    source = Path(args.path)
    if not source.is_file():
        print(f"error: file not found: {source}", file=sys.stderr)
        return 2

    run_analysis = args.analysis or args.load_verify or args.wind_check
    analysis_config = (
        {
            "num_modes": args.num_modes,
            "load_verify": args.load_verify,
            "wind_check": args.wind_check,
        }
        if run_analysis
        else None
    )
    try:
        result = review_s2k_file(
            source,
            tol=args.tol,
            include_analysis=run_analysis,
            analysis_config=analysis_config,
            self_weight=args.self_weight,
            brace_buckling=args.brace_buckling,
            brace_k=args.k_factor,
            export_npz=args.npz,
        )
    except (OSError, ValueError) as exc:
        print(f"error: cannot review {source}: {exc}", file=sys.stderr)
        return 2
    report = (
        format_review_markdown(
            result,
            max_modes=args.max_modes,
            min_participation=args.min_participation,
            num_braces=args.num_braces,
        )
        if args.format == "markdown"
        else format_review_report(
            result,
            max_modes=args.max_modes,
            min_participation=args.min_participation,
            num_braces=args.num_braces,
        )
    )

    if args.out:
        try:
            Path(args.out).write_text(report, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"Report written to {args.out}")
    else:
        print(report)

    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
