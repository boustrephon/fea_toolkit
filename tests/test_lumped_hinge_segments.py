"""Builder emits one lumped spring per hinge-eligible point (``frame_segments``)."""

import openseespy.opensees as ops

from fea_toolkit.model.frame_segments import segment_members
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    FrameElement,
    ISection,
    Material,
    Node,
    Restraint,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder


def _node(nid, tag, x, y, z):
    return Node(node_id=nid, node_tag=tag, x=x, y=y, z=z)


def _girder_with_framing_point():
    """Girder A→C split at framing point M, plus a secondary beam M→S."""
    nodes = {
        "A": _node("A", 1, 0.0, 0.0, 0.0),
        "M": _node("M", 2, 4.0, 0.0, 0.0),
        "C": _node("C", 3, 8.0, 0.0, 0.0),
        "S": _node("S", 4, 4.0, 0.0, 3.0),
    }
    elems = {
        "G": FrameElement(
            elem_id="G",
            elem_tag=11,
            node_i="A",
            node_j="C",
            inactive=True,
            child_ids=["G-0", "G-1"],
        ),
        "G-0": FrameElement(elem_id="G-0", elem_tag=12, node_i="A", node_j="M", parent_id="G"),
        "G-1": FrameElement(elem_id="G-1", elem_tag=13, node_i="M", node_j="C", parent_id="G"),
        "B": FrameElement(elem_id="B", elem_tag=14, node_i="M", node_j="S"),
    }
    return nodes, elems


def _mesh():
    nodes, elems = _girder_with_framing_point()
    secs = {
        "BEAM": ISection(
            name="BEAM",
            shape="I/Wide Flange",
            material="Steel",
            depth=0.3,
            bf=0.15,
            tf=0.01,
            tw=0.006,
            A=8e-3,
            I33=1.2e-4,
            I22=4e-5,
            J=2e-6,
            Z33=1e-3,
            Z22=1e-3,
        ),
    }
    mm = MeshModel(
        nodes=nodes,
        frame_elements=elems,
        frame_assignments={"G-0": "BEAM", "G-1": "BEAM", "B": "BEAM"},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        materials={"Steel": Material("Steel", "Steel", E_mod=2e11, Fy=2.5e8)},
        sections=secs,
        restraints={
            "A": Restraint([1, 1, 1, 1, 1, 1]),
            "C": Restraint([1, 1, 1, 1, 1, 1]),
            "S": Restraint([1, 1, 1, 1, 1, 1]),
        },
        frame_element_types={"G-0": "beam", "G-1": "beam", "B": "beam"},
    )
    mm.frame_segments = segment_members(elems, nodes, frame_element_types=mm.frame_element_types)
    return mm


def test_framing_point_emits_one_spring_per_point():
    mm = _mesh()
    # The secondary beam framing into M cuts the girder into two segments.
    girder = mm.frame_segments["G"]
    assert [(s.node_i, s.node_j) for s in girder] == [("A", "M"), ("M", "C")]

    b = AnalysisBuilder(
        mm,
        {
            "element_type": "elasticBeamColumn",
            "hinge_model": "lumped",
            "hinge_backbone": "bilinear",
            "verbose": False,
        },
    )
    try:
        b.build_domain()
        # One spring per hinge-eligible point: girder A/M/C (3) + beam M/S (2).
        assert len(b._hinge_tags) == 5, f"expected 5 hinge springs, got {len(b._hinge_tags)}"

        # The girder's interior framing point M has a single hinge node (not a
        # back-to-back pair).  Hinge nodes and re-pointed connectivity live on
        # the builder (not the MeshModel): the upstream segment stays anchored
        # to the structural node while the downstream segment starts at the
        # builder-local hinge node.
        assert "G__hinge_1" in b._hinge_nodes
        assert "G__hinge_1a" not in b._hinge_nodes
        assert mm.frame_elements["G-0"].node_j == "M"  # mesh left un-mutated
        assert b._hinge_repoints["G-1"][0] == "G__hinge_1"
    finally:
        ops.wipe()


def test_hand_built_mesh_without_frame_segments_falls_back():
    """An empty ``frame_segments`` synthesises one segment per element."""
    mm = _mesh()
    mm.frame_segments = {}
    b = AnalysisBuilder(
        mm,
        {
            "element_type": "elasticBeamColumn",
            "hinge_model": "lumped",
            "hinge_backbone": "bilinear",
            "verbose": False,
        },
    )
    try:
        b.build_domain()
        # Fallback segments: girder (2 segments → 3 points) + beam (1 → 2) = 5.
        assert len(b._hinge_tags) == 5, f"expected 5 hinge springs, got {len(b._hinge_tags)}"
    finally:
        ops.wipe()
