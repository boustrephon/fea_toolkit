"""Milestone-1 smoke test: the Qt viewport embeds and renders a model.

The Qt-dependent test is gated by the ``needs_gui`` marker (registered in
``pyproject.toml``).  ``tests/conftest.py`` owns the head-less Qt policy: it
probes the optional ``[gui]`` extra (PySide6 / pyvistaqt / qtpy), skips the
marker when it is absent, and forces ``QT_QPA_PLATFORM=offscreen`` before any
``QApplication`` is created -- so no per-file override is needed here.
"""

import pytest


def test_demo_model_is_valid():
    """The built-in ``fea-gui`` demo model builds a usable SAPModelData (no Qt)."""
    from fea_toolkit.gui.app import _demo_model
    from fea_toolkit.model.sap_data import SAPModelData

    md = _demo_model()
    assert isinstance(md, SAPModelData)
    assert md.nodes and md.frame_elements and md.sections and md.materials


@pytest.mark.needs_gui
def test_main_window_renders_sample_model(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` embeds a QtInteractor and renders model geometry."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    window = MainWindow(model=make_sample_model())
    try:
        assert window._interactor is not None
        assert window._viewer is not None
        # show_model added actors (frames + nodes) to the renderer.
        assert len(window._interactor.renderer.actors) > 0
    finally:
        window.close()
