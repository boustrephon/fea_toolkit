"""Per-column gravity axial (raw ``P_g``) and transfer-column detection.

Derives the operating gravity axial ``P_g`` carried by each column segment from
the model's seismic weight (``added_masses`` — the FEMA 355C convention the SAC
benchmark uses, where seismic mass and gravity load share one origin).  A column
whose support chain is broken by a beam (a *transfer*, including a cantilever's
single support) is flagged so the caller can warn and fall back to a static
solve — the closed-form tributary stack is not valid there.

Assumes per-storey column segmentation (each column segment carries the mass
above its top) — the SAC convention.

No OpenSees imports — model-layer topology only.
"""

from dataclasses import replace
from typing import Optional

from .frame_segments import FrameSegment
from .sap_data import FrameElement, Node


def _line_key(node: Node) -> tuple[float, float]:
    """The column-line key: ``(x, y)`` rounded to 6 places."""
    return (round(node.x, 6), round(node.y, 6))


def _has_column_support(
    bottom_node_id: str,
    col_incidence: dict[str, list[str]],
    frame_elements: dict[str, FrameElement],
    nodes: dict[str, Node],
    restraints: dict,
) -> bool:
    """Whether a node lies on a column chain that reaches a restraint."""
    visited: set[str] = set()
    node_id = bottom_node_id
    while node_id not in visited:
        visited.add(node_id)
        if node_id in restraints:
            return True
        below: Optional[str] = None
        for eid in col_incidence.get(node_id, []):
            elem = frame_elements[eid]
            other = elem.node_j if elem.node_i == node_id else elem.node_i
            if nodes[other].z < nodes[node_id].z - 1e-9:
                below = other
                break
        if below is None:
            return False
        node_id = below
    return False


def _member_bottom_node(segments: list[FrameSegment], nodes: dict[str, Node]) -> Optional[str]:
    """The lowest-``z`` node among a member's segment endpoints."""
    lowest: Optional[str] = None
    lowest_z: Optional[float] = None
    for seg in segments:
        for nid in (seg.node_i, seg.node_j):
            nd = nodes.get(nid)
            if nd is None:
                continue
            if lowest_z is None or nd.z < lowest_z:
                lowest = nid
                lowest_z = nd.z
    return lowest


def fill_column_gravity_axial(
    frame_segments: dict[str, list[FrameSegment]],
    frame_elements: dict[str, FrameElement],
    nodes: dict[str, Node],
    frame_element_types: dict[str, str],
    *,
    added_masses: Optional[dict[str, float]] = None,
    restraints: Optional[dict] = None,
    g: float,
) -> tuple[dict[str, list[FrameSegment]], list[str]]:
    """Fill raw column gravity axial ``P_g`` onto each column segment.

    Args:
        frame_segments: ``{member_id: [FrameSegment, ...]}`` from
            :func:`~fea_toolkit.model.frame_segments.segment_members`.
        frame_elements: All frame elements (active leaves and inactive parents).
        nodes: Node dict.
        frame_element_types: ``{elem_id: role}`` — columns are
            ``role == "column"``.
        added_masses: ``{node_id: mass}`` (FEMA 355C seismic weight).
        restraints: ``{node_id: Restraint}``.
        g: Gravitational acceleration in model units (required — supply
            ``g_from_units(units)``; there is no hardcoded default).

    Returns:
        ``(frame_segments, transfer_ids)`` — ``frame_segments`` rebuilt with
        ``axial`` set on column segments, and the list of transfer-column member
        ids (bottom on a beam, no column below) for the caller to warn about.
    """
    added_masses = added_masses or {}
    restraints = restraints or {}

    # ── (x, y) line → [(z, mass), ...] ──
    line_masses: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for nid, mass in added_masses.items():
        nd = nodes.get(nid)
        if nd is None:
            continue
        line_masses.setdefault(_line_key(nd), []).append((nd.z, float(mass)))

    # ── Column incidence (for the support-chain walk) ──
    col_incidence: dict[str, list[str]] = {}
    for eid, elem in frame_elements.items():
        if getattr(elem, "inactive", False) or frame_element_types.get(eid) != "column":
            continue
        col_incidence.setdefault(elem.node_i, []).append(eid)
        col_incidence.setdefault(elem.node_j, []).append(eid)

    transfers: list[str] = []
    updated: dict[str, list[FrameSegment]] = {}
    for mid, segments in frame_segments.items():
        first_sub = (
            segments[0].sub_element_ids[0] if segments and segments[0].sub_element_ids else None
        )
        if first_sub is None or frame_element_types.get(first_sub) != "column":
            updated[mid] = segments  # non-column — axial stays 0.0
            continue

        bottom = _member_bottom_node(segments, nodes)
        if bottom is not None and not _has_column_support(
            bottom, col_incidence, frame_elements, nodes, restraints
        ):
            transfers.append(mid)

        new_segments: list[FrameSegment] = []
        for seg in segments:
            na = nodes[seg.node_i]
            nb = nodes[seg.node_j]
            top = nb if nb.z > na.z else na
            total_mass = sum(m for z, m in line_masses.get(_line_key(top), []) if z >= top.z - 1e-9)
            new_segments.append(replace(seg, axial=g * total_mass))
        updated[mid] = new_segments

    return updated, transfers
