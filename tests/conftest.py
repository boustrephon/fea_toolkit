"""Shared pytest configuration for the fea_toolkit test suite.

Centralises the two pieces of *global* visualisation-test infrastructure so
that no individual test module has to declare them (and, more importantly,
cannot forget them) — see ``.clinerules`` §5.5.

matplotlib ``Agg`` backend
    Set once per test module for the whole suite.  A forgotten ``Agg``
    switch is a *silent* failure on a display-less CI runner rather than a
    clean error, so the policy lives in exactly one place.  ``matplotlib``
    is imported lazily, so non-plotting modules pay nothing.

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

import pytest

try:  # pyvista is core, but unavailable inside Rhino 8's embedded interpreter
    import pyvista as _pyvista
except ImportError:  # pragma: no cover - depends on the interpreter
    _HAVE_PYVISTA = False
else:
    _HAVE_PYVISTA = True
    # Suite-wide head-less policy: no test should pop a render window.
    _pyvista.OFF_SCREEN = True


@pytest.fixture(autouse=True, scope="module")
def _matplotlib_agg():
    """Use the non-interactive matplotlib backend and release figures.

    Module-scoped rather than session-scoped so every plotting module starts
    from a clean figure registry, matching the per-file fixtures this
    replaces.
    """
    import matplotlib

    matplotlib.use("Agg")
    yield
    import matplotlib.pyplot as plt

    plt.close("all")


def pytest_collection_modifyitems(config, items):
    """Skip every ``needs_pyvista`` test when PyVista is not importable."""
    if _HAVE_PYVISTA:
        return
    skip = pytest.mark.skip(reason="pyvista not installed")
    for item in items:
        if "needs_pyvista" in item.keywords:
            item.add_marker(skip)
