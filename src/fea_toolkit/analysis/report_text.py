"""Plain-text static-analysis report — pure formatting, no OpenSees, no Qt.

Renders the cases a static solve produced (nodal displacements, member end
forces) plus their applied/reaction summaries into one readable string, so the
same renderer serves the ``report`` verb, a batch CLI and a notebook alike.  It
reads the already-collected payloads — never re-queries OpenSees — and derives
its unit labels from ``md.units``, so it is unit-agnostic.
"""

from typing import Any, Optional

from ..model.sap_data import SAPModelData

__all__ = ["render_static_text_report"]


def _fmt(value: Any, digits: int = 4) -> str:
    """Format a numeric value, tolerating a missing or non-finite entry."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:+.{digits}g}"


def _at(values: Any, index: int) -> str:
    """Format ``values[index]``, or ``"-"`` when absent/short."""
    if values is None or index >= len(values):
        return "-"
    return _fmt(values[index])


def render_static_text_report(
    md: SAPModelData,
    mesh_model: Any,
    case_results: dict[str, dict[str, Any]],
    summaries: Optional[list[dict]] = None,
) -> str:
    """Render solved static cases as a plain-text report.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData` — supplies
            the unit labels (never a hardcoded unit).
        mesh_model: The prepared topology.  Its active ``frame_elements`` are
            the order the ``element_forces`` component arrays line up with.
        case_results: ``{case_name: {"nodal_displacements": {...},
            "element_forces": {...}}}`` — the raw payloads a solve fills.
        summaries: Optional per-case ``{"analysis", "applied", "summed"}`` list
            — the input
            :func:`~fea_toolkit.analysis.linear.render_load_reaction_rows`
            formats; supplies the applied/reaction section.

    Returns:
        The report as one newline-joined string.
    """
    fu = md.units.get("F", "?")
    lu = md.units.get("L", "?")

    lines: list[str] = [
        "Static analysis report",
        f"Units: force = {fu}, length = {lu}",
        f"Cases: {len(case_results)}",
    ]

    summary_by_case = {str(s.get("analysis")): s for s in (summaries or [])}

    for case, payload in case_results.items():
        lines.append("")
        lines.append(f"── Case {case} ──")

        # ── Applied vs reacted loads ──
        summary = summary_by_case.get(str(case))
        if summary:
            applied = summary.get("applied", {}) or {}
            summed = summary.get("summed", {}) or {}
            lines.append(
                f"  applied  fx={_fmt(applied.get('fx'))}  fy={_fmt(applied.get('fy'))}  "
                f"fz={_fmt(applied.get('fz'))}"
            )
            lines.append(
                f"  reacted  fx={_fmt(summed.get('fx'))}  fy={_fmt(summed.get('fy'))}  "
                f"fz={_fmt(summed.get('fz'))}"
            )
            for axis in ("fx", "fy", "fz"):
                delta = _fmt((applied.get(axis, 0.0) or 0.0) + (summed.get(axis, 0.0) or 0.0))
                lines.append(f"  Δ{axis} = {delta}")

        # ── Nodal displacements ──
        disp = payload.get("nodal_displacements", {}) or {}
        if disp:
            lines.append(f"  Nodal displacements (ux, uy, uz in {lu}):")
            for node_id, d in disp.items():
                lines.append(
                    f"    {node_id!s:>8}  ux={_fmt(d[0])}  uy={_fmt(d[1])}  uz={_fmt(d[2])}"
                )

        # ── Member end forces ──
        forces = payload.get("element_forces", {}) or {}
        frame_elements = getattr(mesh_model, "frame_elements", {}) or {} if mesh_model else {}
        if forces and frame_elements:
            lines.append(f"  Member end forces (local; force in {fu}, moment in {fu}·{lu}):")
            idx = 0
            for elem_id, elem in frame_elements.items():
                if getattr(elem, "inactive", False):
                    continue
                lines.append(
                    f"    {elem_id!s:>8}  "
                    f"i: N={_at(forces.get('fx_i'), idx)} Vy={_at(forces.get('fy_i'), idx)} "
                    f"Vz={_at(forces.get('fz_i'), idx)} Mx={_at(forces.get('mx_i'), idx)} "
                    f"My={_at(forces.get('my_i'), idx)} Mz={_at(forces.get('mz_i'), idx)}  "
                    f"j: N={_at(forces.get('fx_j'), idx)} Vy={_at(forces.get('fy_j'), idx)} "
                    f"Vz={_at(forces.get('fz_j'), idx)} Mx={_at(forces.get('mx_j'), idx)} "
                    f"My={_at(forces.get('my_j'), idx)} Mz={_at(forces.get('mz_j'), idx)}"
                )
                idx += 1

    return "\n".join(lines)
