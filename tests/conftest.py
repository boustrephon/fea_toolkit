"""Shared pytest configuration for the fea_toolkit test suite.

Centralises the two pieces of *global* visualisation-test infrastructure so
that no individual test module has to declare them (and, more importantly,
cannot forget them) — see ``.clinerules`` §5.5.

matplotlib ``Agg`` backend
    Forced through the ``MPLBACKEND`` environment variable at conftest
    import time — before pytest collects any test module — so the policy is
    suite-wide and a forgotten ``Agg`` switch is impossible.  A missing
    ``Agg`` switch is a *silent* failure on a display-less CI runner rather
    than a clean error, so the policy lives in exactly one place.

PyVista
    PyVista is a core dependency but cannot be installed inside Rhino 8's
    embedded interpreter, so its availability is probed once here:

    * when present, ``pyvista`` is put into ``OFF_SCREEN`` mode so that no
      test ever opens a render window (suite-wide head-less policy);
    * the ``needs_pyvista`` marker (registered in ``pyproject.toml`` next to
      ``slow``) skips the tests that need it when the package is missing.

    A test that needs the PyVista *module object* inline can instead call
    ``pytest.importorskip("pyvista")`` — the idiom already used for ``h5py``,
    ``scipy``, ``rhino3dm`` and friends elsewhere in the suite.
"""

import importlib.util
import os

import pytest

# Force the non-interactive Agg backend at conftest import time, *before*
# pytest collects any test module: a module-level ``import matplotlib.pyplot``
# during collection would otherwise latch onto a display backend and fail
# silently on a head-less CI runner.  ``_matplotlib_agg`` below therefore
# only releases figures — it no longer selects a backend.
os.environ["MPLBACKEND"] = "Agg"

try:  # pyvista is core, but unavailable inside Rhino 8's embedded interpreter
    import pyvista as _pyvista
except ImportError:  # pragma: no cover - depends on the interpreter
    _HAVE_PYVISTA = False
else:
    _HAVE_PYVISTA = True
    # Suite-wide head-less policy: no test should pop a render window.
    _pyvista.OFF_SCREEN = True

# Force the head-less Qt platform plugin at conftest import time -- *before*
# any ``QApplication`` is created -- so GUI tests never open a window.  Qt is
# optional (the ``[gui]`` extra), so this is harmless when it is absent.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The optional ``[gui]`` extra: pyvistaqt + qtpy + a Qt binding.  Probed
# cheaply with ``find_spec`` (no heavy import) so the ``needs_gui`` marker
# can skip cleanly when the extra is missing.
_HAVE_GUI = (
    importlib.util.find_spec("pyvistaqt") is not None
    and importlib.util.find_spec("qtpy") is not None
    and (
        importlib.util.find_spec("PySide6") is not None
        or importlib.util.find_spec("PyQt6") is not None
    )
)


@pytest.fixture(autouse=True, scope="module")
def _matplotlib_agg():
    """Release matplotlib figures between test modules.

    The non-interactive ``Agg`` backend is forced via ``MPLBACKEND`` at
    conftest import time (see above), so this fixture only drops the figure
    registry.  Module-scoped rather than session-scoped so every plotting
    module starts from a clean figure registry, matching the per-file
    fixtures this replaces.
    """
    yield
    import matplotlib.pyplot as plt

    plt.close("all")


def pytest_collection_modifyitems(config, items):
    """Skip marker-gated tests when their optional backend is missing."""
    if not _HAVE_PYVISTA:
        skip_pv = pytest.mark.skip(reason="pyvista not installed")
        for item in items:
            if "needs_pyvista" in item.keywords:
                item.add_marker(skip_pv)
    if not _HAVE_GUI:
        skip_gui = pytest.mark.skip(reason="[gui] extra not installed (PySide6/pyvistaqt)")
        for item in items:
            if "needs_gui" in item.keywords:
                item.add_marker(skip_gui)
