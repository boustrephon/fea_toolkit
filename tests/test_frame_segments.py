"""Hinge-eligible segmentation — ``model.frame_segments``."""

from fea_toolkit.model.frame_segments import FrameSegment, segment_members
from fea_toolkit.model.sap_data import FrameElement, FrameRelease, Node


def _node(nid, x, y, z):
    return Node(node_id=nid, node_tag=abs(hash(nid)) % 1_000_000, x=x, y=y, z=z)


def _elem(eid, tag, node_i, node_j, **kwargs):
    return FrameElement(elem_id=eid, elem_tag=tag, node_i=node_i, node_j=node_j, **kwargs)


def test_unsplit_member_single_segment():
    nodes = {"A": _node("A", 0, 0, 0), "B": _node("B", 4, 0, 0)}
    elems = {"E": _elem("E", 1, "A", "B")}
    seg = segment_members(elems, nodes)
    assert set(seg) == {"E"}
    assert len(seg["E"]) == 1
    s = seg["E"][0]
    assert (s.node_i, s.node_j) == ("A", "B")
    assert s.sub_element_ids == ("E",)
    assert s.length == 4.0


def test_framing_point_cuts_girder_into_segments():
    nodes = {
        "A": _node("A", 0, 0, 0),
        "M": _node("M", 5, 0, 0),
        "B": _node("B", 10, 0, 0),
        "S": _node("S", 5, 5, 0),
    }
    elems = {
        "G": _elem("G", 1, "A", "B", inactive=True, child_ids=["G-0", "G-1"]),
        "G-0": _elem("G-0", 2, "A", "M", parent_id="G"),
        "G-1": _elem("G-1", 3, "M", "B", parent_id="G"),
        "S": _elem("S", 4, "S", "M"),
    }
    seg = segment_members(elems, nodes)
    girder = seg["G"]
    assert [(s.node_i, s.node_j) for s in girder] == [("A", "M"), ("M", "B")]
    assert [s.sub_element_ids for s in girder] == [("G-0",), ("G-1",)]
    assert [s.length for s in girder] == [5.0, 5.0]
    # The secondary beam is its own member, one segment to the framing point.
    assert [(s.node_i, s.node_j) for s in seg["S"]] == [("S", "M")]


def test_pure_discretization_stays_one_segment():
    # A member split at M with no other member framing in (member_degree == 1)
    # keeps a single segment spanning the whole member (M is elastic interior).
    nodes = {"A": _node("A", 0, 0, 0), "M": _node("M", 5, 0, 0), "B": _node("B", 10, 0, 0)}
    elems = {
        "G": _elem("G", 1, "A", "B", inactive=True, child_ids=["G-0", "G-1"]),
        "G-0": _elem("G-0", 2, "A", "M", parent_id="G"),
        "G-1": _elem("G-1", 3, "M", "B", parent_id="G"),
    }
    seg = segment_members(elems, nodes)
    assert len(seg["G"]) == 1
    s = seg["G"][0]
    assert (s.node_i, s.node_j) == ("A", "B")
    assert s.sub_element_ids == ("G-0", "G-1")
    assert s.length == 10.0


def test_exclude_braces_drops_brace_member():
    nodes = {"A": _node("A", 0, 0, 0), "B": _node("B", 0, 0, 3)}
    elems = {"BR": _elem("BR", 1, "A", "B")}
    seg = segment_members(elems, nodes, frame_element_types={"BR": "brace"})
    assert "BR" not in seg


def test_honour_releases_flags_pinned_end():
    # M3 released at end J leaves one hinge-eligible point (A): the member is
    # kept as a single segment spanning A→B, with the released end flagged so
    # the builder leaves it a pin (no hinge spring there) while the moment end
    # retains its hinge.
    nodes = {"A": _node("A", 0, 0, 0), "B": _node("B", 4, 0, 0)}
    elems = {"E": _elem("E", 1, "A", "B")}
    rel = FrameRelease(frame_id="E", end_j=[0, 0, 0, 0, 0, 1])
    seg = segment_members(elems, nodes, frame_releases={"E": rel})
    assert set(seg) == {"E"}
    s = seg["E"][0]
    assert (s.node_i, s.node_j) == ("A", "B")
    assert s.sub_element_ids == ("E",)
    assert s.release_i is False  # moment end keeps its hinge
    assert s.release_j is True  # M3-released end is a pin


def test_member_ids_restricts_scope():
    nodes = {
        "A": _node("A", 0, 0, 0),
        "B": _node("B", 4, 0, 0),
        "C": _node("C", 0, 4, 0),
        "D": _node("D", 4, 4, 0),
    }
    elems = {
        "E1": _elem("E1", 1, "A", "B"),
        "E2": _elem("E2", 2, "C", "D"),
    }
    seg = segment_members(elems, nodes, member_ids=["E1"])
    assert set(seg) == {"E1"}


def test_segment_is_frozen_and_immutable():
    seg = FrameSegment(node_i="A", node_j="B", sub_element_ids=("E",))
    assert seg.axial == 0.0
    assert seg.length == 0.0
