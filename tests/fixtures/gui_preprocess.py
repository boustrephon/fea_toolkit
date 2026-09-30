"""Shared GUI-preprocessing helpers for the ``needs_gui`` test modules.

``PRESETS`` maps each Model-menu action to the Preprocessor config dict it
applies plus the human label the Message Log echoes.  ``run_preprocess`` drives
a preset through ``MainWindow._mesh_preset`` and spins the Qt event loop until
the worker finishes, so ``test_gui_preprocess`` and ``test_gui_views`` do not
each re-implement the same pump.
"""

import time

PRESETS = {
    "model.split": (
        {"split_elements": True, "create_shells": False},
        "Splitting elements at joints",
    ),
    "model.mesh": (
        {"split_elements": True, "create_shells": True},
        "Splitting elements at joints and meshing areas",
    ),
}


def run_preprocess(window, action_key="model.split", timeout=30.0):
    """Run a Model-menu preset and spin the GUI until its worker ends."""
    from qtpy.QtCore import QCoreApplication

    config, label = PRESETS[action_key]
    window._mesh_preset(config, label)
    deadline = time.monotonic() + timeout
    while window._worker is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert window._worker is None, "the preprocessing worker never finished"
