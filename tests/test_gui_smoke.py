"""Milestone-1 smoke test: the Qt viewport embeds and renders a model.

The Qt-dependent test is gated by the ``needs_gui`` marker (registered in
``pyproject.toml``); ``tests/conftest.py`` skips it when the optional ``[gui]``
extra (PySide6 / pyvistaqt / qtpy) is not installed.
"""

import os

import pytest

# Belt-and-braces: tests/conftest.py already forces this before any QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_demo_model_is_valid():
    """The built-in ``fea-gui`` demo model builds a usable SAPModelData (no Qt)."""
    from fea_toolkit.gui.app import _demo_model
    from fea_toolkit.model.sap_data import SAPModelData

    md = _demo_model()
    assert isinstance(md, SAPModelData)
    assert md.nodes and md.frame_elements and md.sections and md.materials


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["pytest-fea-gui"])
    yield app


@pytest.mark.needs_gui
def test_main_window_renders_sample_model(qapp):
    """A ``MainWindow`` embeds a QtInteractor and renders model geometry."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.gui.main_window import MainWindow

    window = MainWindow(model=make_sample_model())
    try:
        assert window._interactor is not None
        assert window._viewer is not None
        # show_model added actors (frames + nodes) to the renderer.
        assert len(window._interactor.renderer.actors) > 0
    finally:
        window.close()
