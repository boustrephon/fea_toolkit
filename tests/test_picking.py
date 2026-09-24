"""The verified PyVista picking contract behind viewport -> tree selection.

PyVista's ``enable_mesh_picking`` callback receives the picked **actor** and
nothing else (pyvista 0.48.1), so the cell index has to be read from the picker
it installs -- ``plotter.iren.picker.GetCellId()``.  The GUI's
``MainWindow._picked_cell_id`` reads exactly that.  These tests pin the
contract against the real library, so a pyvista upgrade that changes the
callback or the picker wiring fails loudly instead of leaving selection
silently dead.

Runs on a plain off-screen ``pv.Plotter``: the embedded ``QtInteractor`` cannot
render (and therefore cannot pick) under the ``offscreen`` Qt platform, which
has no GL context.
"""

import numpy as np
import pytest
import vtk

from fea_toolkit.gui.controllers.selection import SelectionIndex
from fea_toolkit.plotting.viewer import ModelViewer

pytestmark = pytest.mark.needs_pyvista


@pytest.fixture()
def viewer():
    """An off-screen ``ModelViewer`` over the sample model, rendered once."""
    from examples.sample_model import make_sample_model

    v = ModelViewer(model_data=make_sample_model(), backend="pyvista", off_screen=True)
    v.show_model(show_nodes=True)
    v._backend.plotter.show(auto_close=False)
    yield v
    v._backend.clear()
    v._backend.plotter.close()


def _display_coords(renderer, point):
    """Display (pixel) coordinates of a world *point*."""
    coord = vtk.vtkCoordinate()
    coord.SetCoordinateSystemToWorld()
    coord.SetValue(*[float(value) for value in point])
    return coord.GetComputedDisplayValue(renderer)


def _pick_at(plotter, point):
    """Pick the geometry under a world *point*; ``True`` when something was hit."""
    x, y = _display_coords(plotter.renderer, point)
    return plotter.iren.picker.Pick(int(round(x)), int(round(y)), 0, plotter.renderer) == 1


def test_members_pick_to_their_own_cell_and_category(viewer):
    """Actor -> ``"frames"``, cell index -> that member's SAP label."""
    frames = viewer.geometry()[0]
    selection = SelectionIndex.from_viewer(viewer)
    plotter = viewer._backend.plotter
    plotter.enable_mesh_picking(callback=lambda *args: None, use_actor=True, show=False)

    for expected_cell, frame in enumerate(frames):
        midpoint = (np.asarray(frame.start) + np.asarray(frame.end)) / 2.0
        assert _pick_at(plotter, midpoint), f"pick missed member {frame.elem_id}"

        category = viewer._backend.category_of_actor(plotter.iren.picker.GetActor())
        assert category == "frames"
        assert plotter.iren.picker.GetCellId() == expected_cell
        assert selection.label(category, plotter.iren.picker.GetCellId()) == frame.elem_id


def test_clicking_at_joints_resolves_to_a_real_entity(viewer):
    """Joint positions hit a member and/or the node cloud -- never a dead end.

    Which batch wins depends on the ray, so only the *outcome* is asserted:
    a known category (never ``None``) resolving to a real SAP label.
    """
    _frames, _shells, nodes = viewer.geometry()
    selection = SelectionIndex.from_viewer(viewer)
    plotter = viewer._backend.plotter
    plotter.enable_mesh_picking(callback=lambda *args: None, use_actor=True, show=False)

    seen = set()
    for node in nodes:
        if not _pick_at(plotter, node.position):
            continue
        category = viewer._backend.category_of_actor(plotter.iren.picker.GetActor())
        seen.add(category)
        assert selection.label(category, plotter.iren.picker.GetCellId()) is not None

    assert seen, "no geometry was picked at the joint positions"
    assert seen <= {"frames", "shells", "nodes"}


def test_unknown_actors_have_no_category(viewer):
    """The reverse lookup is identity-based, so foreign actors resolve to None."""
    assert viewer._backend.category_of_actor(object()) is None


# ── The interaction policy's picking behaviour ──────────────────────
#
# ``ViewportInteraction`` is what the GUI installs; these drive its pickers
# directly on the same off-screen render (no Qt, no observers).


def _interaction(viewer, **policy_overrides):
    """A ``ViewportInteraction`` over the viewer's plotter."""
    from dataclasses import replace

    from fea_toolkit.gui.controllers.interaction import PRESETS
    from fea_toolkit.gui.views.interactor import ViewportInteraction

    policy = replace(PRESETS["click_drag"], **policy_overrides)
    return ViewportInteraction(
        viewer._backend.plotter,
        policy=policy,
        on_pick=lambda result: None,
        node_actors=lambda: viewer._backend.actors("nodes"),
    )


def test_a_tighter_tolerance_shrinks_the_picking_region(viewer):
    """The knob behind "shrink the picking area": 8 px off the member.

    VTK's default tolerance (0.025 of the viewport diagonal) still selects the
    member from there; the policy's tight default does not.
    """
    frame = viewer.geometry()[0][0]
    renderer = viewer._backend.plotter.renderer
    midpoint = (np.asarray(frame.start) + np.asarray(frame.end)) / 2.0
    x, y = _display_coords(renderer, midpoint)

    loose = _interaction(viewer, pick_tolerance=0.05, node_priority=False)
    tight = _interaction(viewer, pick_tolerance=0.001, node_priority=False)

    assert loose.pick_at(x + 8, y).hit is True
    assert tight.pick_at(x + 8, y).hit is False


def test_the_default_tolerance_is_clickable_but_not_fat(viewer):
    """The default region is a calibrated compromise, not a guess.

    Measured on macOS with a 1026x860 render window (device pixels): the policy
    default (0.010 -> ~13 px) selects a click 6 px to the side of the member,
    while 30 px away -- well inside VTK's 0.025 default -- no longer does.
    """
    frame = viewer.geometry()[0][0]
    renderer = viewer._backend.plotter.renderer
    midpoint = (np.asarray(frame.start) + np.asarray(frame.end)) / 2.0
    x, y = _display_coords(renderer, midpoint)

    default = _interaction(viewer, node_priority=False)

    assert default.pick_at(x + 6, y).hit is True
    assert default.pick_at(x + 30, y).hit is False


def test_node_priority_wins_at_a_joint(viewer):
    """A joint is a node *and* the end of a member: the policy prefers the node."""
    nodes = viewer.geometry()[2]
    renderer = viewer._backend.plotter.renderer
    x, y = _display_coords(renderer, nodes[1].position)

    result = _interaction(viewer, node_priority=True).pick_at(x, y)

    assert result.hit is True
    assert result.node is True
    assert viewer._backend.category_of_actor(result.actor) == "nodes"
    index = SelectionIndex.from_viewer(viewer)
    assert index.label("nodes", result.index) == nodes[1].node_id


def test_without_node_priority_the_cell_pick_still_works(viewer):
    """The knob is a preference, not a requirement."""
    frame = viewer.geometry()[0][0]
    renderer = viewer._backend.plotter.renderer
    midpoint = (np.asarray(frame.start) + np.asarray(frame.end)) / 2.0
    x, y = _display_coords(renderer, midpoint)

    result = _interaction(viewer, node_priority=False).pick_at(x, y)

    assert viewer._backend.category_of_actor(result.actor) == "frames"
    assert SelectionIndex.from_viewer(viewer).label("frames", result.index) == frame.elem_id


def test_a_click_on_nothing_resolves_to_no_entity(viewer):
    """A corner click must not resolve to a model entity, so the UI can clear."""
    result = _interaction(viewer, node_priority=False).pick_at(5, 5)
    category = viewer._backend.category_of_actor(result.actor) if result.hit else None
    assert category is None


def test_overlays_are_not_pickable(viewer):
    """A selection highlight sits *on* its element; it must not steal the click."""
    frame = viewer.geometry()[0][0]
    viewer.highlight_elements(frame_ids=[frame.elem_id])
    highlight = viewer._backend.actors("highlights")[-1]

    assert highlight.GetPickable() == 0


def test_node_markers_are_visible_enough_to_click():
    """Regression: ``radius * 20`` drew 0.4 px markers nobody could see or hit."""
    from fea_toolkit.plotting.renderers.pyvista import node_point_size

    assert node_point_size(0.02) >= 6.0  # the show_model default
    assert node_point_size(0.0001) >= 6.0  # floored for tiny models
