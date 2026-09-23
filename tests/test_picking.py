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
