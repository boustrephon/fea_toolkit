"""Forward selection index: ``(render category, cell) -> SAP label``.

Qt-free, so this module carries **no** marker -- the viewport->tree counterpart
of ``test_gui_tree_index.py``.  The order the index assumes is the order the
backends batch geometry in, which ``test_gui_pick.py`` checks against a real
pick.
"""

import numpy as np

from fea_toolkit.gui.controllers.selection import CATEGORY_GROUPS, SelectionIndex
from fea_toolkit.plotting.renderers.base import FrameGeom, NodeGeom, ShellGeom


def _frame(elem_id: str) -> FrameGeom:
    return FrameGeom(
        elem_id=elem_id,
        section="S",
        node_i="1",
        node_j="2",
        start=np.zeros(3),
        end=np.array([1.0, 0.0, 0.0]),
    )


def _shell(area_id: str, n_vertices: int) -> ShellGeom:
    vertices = np.array([[float(i), 0.0, 0.0] for i in range(n_vertices)])
    return ShellGeom(area_id=area_id, section="S", vertices=vertices)


def _node(node_id: str) -> NodeGeom:
    return NodeGeom(node_id=node_id, position=np.array([1.0, 2.0, 3.0]))


def test_frames_resolve_by_cell_order():
    index = SelectionIndex(frames=[_frame("7"), _frame("9")])
    assert index.label("frames", 0) == "7"
    assert index.label("frames", 1) == "9"


def test_nodes_resolve_by_cell_order():
    index = SelectionIndex(nodes=[_node("3"), _node("4")])
    assert index.label("nodes", 0) == "3"
    assert index.label("nodes", 1) == "4"


def test_shell_cells_walk_the_fan_triangulation():
    """A triangle is 1 cell, a quad 2, a hexagon 4 -- in render order."""
    index = SelectionIndex(shells=[_shell("A", 3), _shell("B", 4), _shell("C", 6)])
    assert [index.label("shells", cell) for cell in range(7)] == [
        "A",
        "B",
        "B",
        "C",
        "C",
        "C",
        "C",
    ]


def test_out_of_range_and_unknown_categories_return_none():
    index = SelectionIndex(frames=[_frame("7")], nodes=[_node("1")])
    assert index.label("frames", 1) is None
    assert index.label("frames", -1) is None
    assert index.label("nodes", 5) is None
    assert index.label("deformed", 0) is None  # not a selectable batch
    assert index.label("shells", 0) is None  # no shells were rendered


def test_blank_labels_are_not_selectable():
    index = SelectionIndex(frames=[_frame("")])
    assert index.label("frames", 0) is None


def test_group_keys_are_the_model_tree_group_names():
    index = SelectionIndex()
    assert index.group_key("frames") == "frame_elements"
    assert index.group_key("shells") == "area_elements"
    assert index.group_key("nodes") == "nodes"
    assert index.group_key("force_flags") is None
    assert set(CATEGORY_GROUPS.values()) == {"frame_elements", "area_elements", "nodes"}
