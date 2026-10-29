"""Per-column gravity axial and transfer detection — ``model.gravity_axial``."""

from fea_toolkit.model.frame_segments import segment_members
from fea_toolkit.model.gravity_axial import fill_column_gravity_axial
from fea_toolkit.model.sap_data import FrameElement, Node, Restraint


def _node(nid, x, y, z):
    return Node(node_id=nid, node_tag=abs(hash(nid)) % 1_000_000, x=x, y=y, z=z)


def _elem(eid, tag, node_i, node_j, **kwargs):
    return FrameElement(elem_id=eid, elem_tag=tag, node_i=node_i, node_j=node_j, **kwargs)


def _axial(elems, nodes, types, added_masses, restraints=None, g=10.0):
    seg = segment_members(elems, nodes, frame_element_types=types)
    return fill_column_gravity_axial(
        seg,
        elems,
        nodes,
        types,
        added_masses=added_masses,
        restraints=restraints,
        g=g,
    )


def test_column_axial_decreases_up_the_building():
    nodes = {
        "B": _node("B", 0, 0, 0),
        "L1": _node("L1", 0, 0, 3),
        "L2": _node("L2", 0, 0, 6),
        "L3": _node("L3", 0, 0, 9),
    }
    elems = {
        "C1": _elem("C1", 1, "B", "L1"),
        "C2": _elem("C2", 2, "L1", "L2"),
        "C3": _elem("C3", 3, "L2", "L3"),
    }
    types = {"C1": "column", "C2": "column", "C3": "column"}
    masses = {"L1": 100.0, "L2": 100.0, "L3": 50.0}
    restraints = {"B": Restraint([1, 1, 1, 1, 1, 1])}
    filled, transfers = _axial(elems, nodes, types, masses, restraints=restraints)
    assert transfers == []
    ax = {mid: segs[0].axial for mid, segs in filled.items()}
    assert ax["C1"] == 2500.0  # 10 * (100 + 100 + 50)
    assert ax["C2"] == 1500.0  # 10 * (100 + 50)
    assert ax["C3"] == 500.0  # 10 * 50


def test_transfer_column_is_flagged():
    nodes = {"A": _node("A", 0, 0, 3), "M": _node("M", 3, 0, 3), "T": _node("T", 3, 0, 6)}
    elems = {"B": _elem("B", 1, "A", "M"), "C": _elem("C", 2, "M", "T")}
    types = {"B": "beam", "C": "column"}
    _, transfers = _axial(elems, nodes, types, {"T": 50.0})
    assert transfers == ["C"]


def test_beam_axial_stays_zero():
    nodes = {"A": _node("A", 0, 0, 3), "B": _node("B", 4, 0, 3)}
    elems = {"B": _elem("B", 1, "A", "B")}
    types = {"B": "beam"}
    filled, transfers = _axial(elems, nodes, types, {"B": 100.0})
    assert transfers == []
    assert filled["B"][0].axial == 0.0
