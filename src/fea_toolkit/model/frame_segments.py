"""Hinge-eligible segmentation of frame members.

For each frame *member* (the top of a ``parent_id`` split chain) compute the
segments between its *hinge-eligible* points — the member's two ends plus any
interior node where another member frames in (``member_degree >= 2``).  A pure
discretization node (e.g. a shell-mesh matching point) is interior to a single
member and stays inside a segment's elastic interior.

The result is route-agnostic: it records *where a hinge could go*, not whether
one is emitted.  The ``AnalysisBuilder`` emits one spring per hinge-eligible
point only on the lumped-hinge route (``hinge_model="lumped"``); elastic / modal
/ fiber routes ignore this field.

No OpenSees imports — model-layer topology only.
"""

import math
from dataclasses import dataclass
from typing import Optional

from .sap_data import FrameElement, FrameRelease, Node


@dataclass(frozen=True)
class FrameSegment:
    """One member segment between two consecutive hinge-eligible points.

    The elastic interior is the chain of ``sub_element_ids`` (rigidly connected
    at interior discretization nodes); the two ends carry the member's flexural
    hinges.  ``axial`` is the column's raw gravity axial ``P_g`` (model force
    units, compression positive) — ``0.0`` until the gravity-axial derivation
    fills it (a follow-on; see the Elwood ``_stack_gravity_axial`` path).

    Attributes:
        node_i: Start hinge-eligible node id.
        node_j: End hinge-eligible node id.
        sub_element_ids: Frozen chain of elastic sub-elements, in order.
        axial: Raw column gravity axial ``P_g`` (``0.0`` = none yet).
        length: Straight-line length (sum of the sub-element lengths).
    """

    node_i: str
    node_j: str
    sub_element_ids: tuple[str, ...]
    axial: float = 0.0
    length: float = 0.0


#: Default hinge-eligibility rules, in application order.  ``honour_releases``
#: drops a pinned (M3-released) end — a member with only one remaining point
#: (fixed-pin) produces no segment; the builder's release mechanism handles that
#: end (known A3/A4 refinement).  ``include_leaning_columns`` is deliberately
#: absent (leaning columns stay elastic by default) and needs a leaning-column
#: signal that is currently benchmark-specific, so it is deferred.
DEFAULT_HINGE_RULES: tuple[str, ...] = (
    "member_ends",
    "framing_points",
    "exclude_braces",
    "honour_releases",
)

#: Local-DOF index of M3 (strong-axis moment) in the ``FrameRelease`` order
#: ``[P, V2, V3, T, M2, M3]`` — the pin indicator ``honour_releases`` reads.
_M3_INDEX = 5


def _top_member(eid: str, frame_elements: dict[str, FrameElement]) -> str:
    """Resolve *eid* to the top of its ``parent_id`` chain (its member id)."""
    mid = eid
    while True:
        elem = frame_elements.get(mid)
        if elem is None or elem.parent_id is None:
            return mid
        mid = elem.parent_id


def _order_chain(
    eids: list[str], frame_elements: dict[str, FrameElement]
) -> tuple[list[str], list[str]]:
    """Order a member's sub-elements into a chain.

    Returns ``(ordered_sub_elements, ordered_nodes)``; ``ordered_nodes`` has one
    more entry than ``ordered_sub_elements`` and each adjacent pair is connected
    by the corresponding sub-element.  Falls back to insertion order for a
    non-linear chain (defensive — members are always linear).
    """
    if not eids:
        return [], []
    if len(eids) == 1:
        elem = frame_elements[eids[0]]
        return [eids[0]], [elem.node_i, elem.node_j]

    incidence: dict[str, list[str]] = {}
    for eid in eids:
        elem = frame_elements[eid]
        incidence.setdefault(elem.node_i, []).append(eid)
        incidence.setdefault(elem.node_j, []).append(eid)

    endpoints = [nid for nid, es in incidence.items() if len(es) == 1]
    if len(endpoints) == 2:
        node = endpoints[0]
        ordered: list[str] = []
        prev: Optional[str] = None
        while True:
            candidates = [eid for eid in incidence.get(node, []) if eid != prev]
            if not candidates:
                break
            eid = candidates[0]
            ordered.append(eid)
            prev = eid
            elem = frame_elements[eid]
            node = elem.node_j if elem.node_i == node else elem.node_i
    else:
        ordered = list(eids)  # defensive: not a simple chain

    chain_nodes: list[str] = []
    for eid in ordered:
        elem = frame_elements[eid]
        if not chain_nodes:
            chain_nodes = [elem.node_i, elem.node_j]
        else:
            last = chain_nodes[-1]
            if elem.node_i == last:
                chain_nodes.append(elem.node_j)
            elif elem.node_j == last:
                chain_nodes.append(elem.node_i)
            else:
                chain_nodes.extend([elem.node_i, elem.node_j])
    return ordered, chain_nodes


def _released_m3(release: FrameRelease, end: str) -> bool:
    """Whether *release* releases the strong-axis moment (M3) at *end*."""
    return _M3_INDEX in release.released_indices(end)


def _veto_released_ends(
    flags: list[bool],
    ordered: list[str],
    chain_nodes: list[str],
    frame_elements: dict[str, FrameElement],
    frame_releases: dict[str, FrameRelease],
) -> None:
    """Drop a member end whose strong-axis moment (M3) is released (pinned)."""
    if not ordered or not chain_nodes:
        return
    first_id = ordered[0]
    rel = frame_releases.get(first_id)
    if rel is not None:
        end = "I" if frame_elements[first_id].node_i == chain_nodes[0] else "J"
        if _released_m3(rel, end):
            flags[0] = False
    last_id = ordered[-1]
    rel = frame_releases.get(last_id)
    if rel is not None:
        end = "I" if frame_elements[last_id].node_i == chain_nodes[-1] else "J"
        if _released_m3(rel, end):
            flags[-1] = False


def segment_members(
    frame_elements: dict[str, FrameElement],
    nodes: dict[str, Node],
    *,
    frame_element_types: Optional[dict[str, str]] = None,
    frame_releases: Optional[dict[str, FrameRelease]] = None,
    member_ids: Optional[list[str]] = None,
    rules: Optional[list[str]] = None,
) -> dict[str, list[FrameSegment]]:
    """Cut each frame member into hinge-eligible segments.

    Args:
        frame_elements: All frame elements (active leaves and inactive parents).
        nodes: Node dict (for segment length).
        frame_element_types: ``{elem_id: role}`` (``beam``/``column``/``brace``)
            for the active leaves — consumed by ``exclude_braces``.
        frame_releases: ``{active_elem_id: FrameRelease}`` — consumed by
            ``honour_releases``.
        member_ids: Optional member ids to restrict to; ``None`` means every
            member with an active element.
        rules: Rule names to apply; ``None`` means :data:`DEFAULT_HINGE_RULES`.

    Returns:
        ``{member_id: [FrameSegment, ...]}`` ordered along the member.  A member
        with fewer than two hinge-eligible points (a brace, or a fixed-pin
        member) is omitted — the builder treats an absent member as elastic.
    """
    rules = list(rules) if rules is not None else list(DEFAULT_HINGE_RULES)
    frame_element_types = frame_element_types or {}
    frame_releases = frame_releases or {}

    active = {
        eid: elem for eid, elem in frame_elements.items() if not getattr(elem, "inactive", False)
    }

    # ── Member identity (top of the parent chain) ──
    members: dict[str, list[str]] = {}
    for eid in active:
        members.setdefault(_top_member(eid, frame_elements), []).append(eid)

    if member_ids is not None:
        members = {mid: members[mid] for mid in member_ids if mid in members}

    # ── member_degree per node (distinct top-level members incident) ──
    node_members: dict[str, set[str]] = {}
    for eid in active:
        mid = _top_member(eid, frame_elements)
        elem = frame_elements[eid]
        node_members.setdefault(elem.node_i, set()).add(mid)
        node_members.setdefault(elem.node_j, set()).add(mid)
    member_degree = {nid: len(mids) for nid, mids in node_members.items()}

    result: dict[str, list[FrameSegment]] = {}
    for mid, eids in members.items():
        ordered, chain_nodes = _order_chain(eids, frame_elements)
        if not chain_nodes:
            continue

        n = len(chain_nodes)
        flags = [False] * n

        # ── Detectors — candidates ──
        if "member_ends" in rules:
            flags[0] = True
            flags[-1] = True
        if "framing_points" in rules:
            for i in range(1, n - 1):
                if member_degree.get(chain_nodes[i], 0) >= 2:
                    flags[i] = True

        # ── Filters — vetoes ──
        if "exclude_braces" in rules and ordered and frame_element_types.get(ordered[0]) == "brace":
            flags = [False] * n
        if "honour_releases" in rules:
            _veto_released_ends(flags, ordered, chain_nodes, frame_elements, frame_releases)

        hinge_nodes = [chain_nodes[i] for i in range(n) if flags[i]]
        if len(hinge_nodes) < 2:
            continue

        segments: list[FrameSegment] = []
        for a, b in zip(hinge_nodes, hinge_nodes[1:]):
            ia = chain_nodes.index(a)
            ib = chain_nodes.index(b)
            na = nodes.get(a)
            nb = nodes.get(b)
            length = (
                math.hypot(nb.x - na.x, nb.y - na.y, nb.z - na.z)
                if na is not None and nb is not None
                else 0.0
            )
            segments.append(
                FrameSegment(
                    node_i=a,
                    node_j=b,
                    sub_element_ids=tuple(ordered[ia:ib]),
                    length=length,
                )
            )
        result[mid] = segments

    return result
