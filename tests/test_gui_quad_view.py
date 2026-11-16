"""Milestone-10 quad-view tests: four viewports sharing one scene.

Qt-dependent, so the whole module is gated by the ``needs_gui`` marker;
``tests/conftest.py`` skips it when the optional ``[gui]`` extra is absent.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.needs_gui


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the sample model in a quad view."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=make_sample_model())
    yield win
    win.close()


def test_quad_view_has_four_viewports_and_a_primary(window):
    """Four interactors exist; the primary keeps the ``_interactor`` name."""
    quad = window._quad
    assert len(quad.interactors) == 4
    assert window._interactor is quad.interactors[0]
    assert window._interactor is quad.primary_interactor
    assert window._backend is quad.backend


def test_default_orientations_are_iso_front_top_side(window):
    """The four views default to iso / front (XZ) / top (XY) / side (YZ)."""
    quad = window._quad
    assert [quad.orientation(i) for i in range(4)] == ["iso", "xz", "xy", "yz"]


def test_the_scene_is_mirrored_to_every_viewport(window):
    """One rendered scene — the same actor is present in all four renderers."""
    frame_actors = window._backend.actors("frames")
    assert frame_actors
    for interactor in window._quad.interactors[1:]:
        renderer_actors = list(interactor.renderer.actors.values())
        assert frame_actors[0] in renderer_actors


def test_camera_action_targets_the_active_viewport(window):
    """View ▸ Camera ▸ Top reorients the active viewport (the primary, initially)."""
    quad = window._quad
    assert quad.active_index == 0
    before = window._interactor.camera_position
    window._actions["view.xy"].trigger()
    assert quad.orientation(0) == "xy"
    assert window._interactor.camera_position != before


def test_set_active_tracks_the_active_interactor(window):
    """Clicking a viewport (here, directly) makes it the active one."""
    quad = window._quad
    quad.set_active(2)
    assert quad.active_index == 2
    assert quad.active_interactor is quad.interactors[2]


def test_cycle_orientation_advances_the_caption(window):
    """The per-view button cycles orientation and updates the caption."""
    quad = window._quad
    quad.cycle_orientation(0)
    assert quad.orientation(0) == "xz"
    assert quad._captions[0].text() == "Front"


def test_sync_focus_reaims_every_other_view(window):
    """Re-aiming one view re-targets the others at the same focal point."""
    quad = window._quad
    quad.apply_orientations()
    custom_focal = [10.0, 20.0, 30.0]
    pos, _focal, up = quad.interactors[0].camera_position
    quad.interactors[0].camera_position = (pos, custom_focal, up)
    quad.sync_focus(0)
    for interactor in quad.interactors[1:]:
        _pos, focal, _up = interactor.camera_position
        assert np.allclose(np.asarray(focal, dtype=float), custom_focal)


def test_fit_reorients_every_viewport(window):
    """View ▸ Zoom to fit reorients all four views without raising."""
    window._actions["view.fit"].trigger()
    for interactor in window._quad.interactors:
        assert interactor.camera_position is not None
