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
import pathlib

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
#
# Unconditional on purpose: CI showed the ``offscreen`` platform hosts the
# embedded viewport on Linux, so no test depends on a display (see
# docs/gui_roadmap.md section 9.7).
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


# Ground-motion mirror: records fetched into ``data/ground_motion/<public|local>``
# by the fetch scripts under ``scripts/``.  Absent on a fresh checkout (and in
# CI).  The ``needs_ground_motion`` marker is *record-scoped*: a test marks the
# specific record(s) it needs (``@pytest.mark.needs_ground_motion("LA21")``) and
# is skipped only when those records are absent; a bare marker
# (``@pytest.mark.needs_ground_motion``) requires the whole committed registry
# to be present.  The gate is *record-based* — a record must exist at the path
# its ``redistributable`` flag and ``source_url`` extension imply — not
# directory-based, so an empty mirror does not enable the gated tests.
def _manifest_registry() -> list[dict]:
    import json

    registry = pathlib.Path("tests") / "fixtures" / "ground_motion_manifests.json"
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _record_path(record_id: str) -> pathlib.Path | None:
    """Resolve the expected mirror path for one manifest record, or ``None``."""
    from fea_toolkit.io.ground_motion import source_extension

    for item in _manifest_registry():
        if not isinstance(item, dict):
            continue
        if str(item.get("record_id", "")) == record_id:
            sub = "public" if item.get("redistributable") else "local"
            ext = source_extension(str(item.get("source_url", "") or ""))
            return pathlib.Path("data") / "ground_motion" / sub / f"{record_id}{ext}"
    return None


def _record_present(record_id: str) -> bool:
    p = _record_path(record_id)
    return p is not None and p.exists()


def _all_record_ids() -> list[str]:
    return [
        str(item["record_id"])
        for item in _manifest_registry()
        if isinstance(item, dict) and item.get("record_id")
    ]


@pytest.fixture(scope="session")
def ground_motion_dir():
    """Path to the non-redistributable ground-motion mirror, or ``None`` when
    the mirror directory is absent."""
    d = pathlib.Path("data") / "ground_motion" / "local"
    return str(d) if d.exists() else None


@pytest.fixture(scope="session")
def resolve_ground_motion_record():
    """Return a callable mapping a ``record_id`` to its on-disk path.

    Raises ``FileNotFoundError`` when the record is absent; pair it with
    ``@pytest.mark.needs_ground_motion("<record_id>")`` so the test is skipped
    before the fixture runs.
    """

    def _resolve(record_id: str) -> str:
        p = _record_path(record_id)
        if p is None or not p.exists():
            raise FileNotFoundError(f"ground-motion record {record_id!r} not in the mirror")
        return str(p)

    return _resolve


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


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires.

    Defined once here rather than re-declared per GUI test module (it was
    previously duplicated across ~14 files).  The import stays inside the
    fixture so a Qt-less interpreter -- Rhino 8's embedded CPython, a minimal
    ``pip install -e .`` -- never touches ``qtpy`` during collection.
    """
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


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
    for item in items:
        marker = item.get_closest_marker("needs_ground_motion")
        if marker is None:
            continue
        record_ids = [str(a) for a in marker.args if a]
        if record_ids:
            missing = [r for r in record_ids if not _record_present(r)]
            if missing:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"ground-motion record(s) not fetched: {', '.join(missing)}"
                    )
                )
        else:
            ids = _all_record_ids()
            missing = [r for r in ids if not _record_present(r)]
            if not ids or missing:
                item.add_marker(
                    pytest.mark.skip(reason="ground-motion mirror not fetched (data/ground_motion)")
                )
