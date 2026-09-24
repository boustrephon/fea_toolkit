"""Tests for lazy package imports — the toolkit must import cleanly
inside Rhino 8 (CPython 3.9), where ``openseespy`` cannot be installed."""

import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


class TestLazyImports:
    def test_import_rhino_without_openseespy(self):
        """`import fea_toolkit.rhino` must not load openseespy or pyvista."""
        code = (
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "import fea_toolkit.rhino; "
            "import fea_toolkit.io.stage_reader; "
            "assert 'openseespy' not in sys.modules, 'openseespy loaded'; "
            "assert 'pyvista' not in sys.modules, 'pyvista loaded'; "
            "assert 'opsvis' not in sys.modules, 'opsvis loaded'; "
            "print('clean')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout

    def test_import_the_picking_adapter_without_qt(self):
        """``gui.views.interactor`` must stay Qt-free.

        The Qt-less CI matrix is what tests the picking adapter, and the ``gui``
        packages expose their Qt widgets lazily for exactly this reason -- an
        eager re-export in ``gui/views/__init__.py`` broke the 3.10/3.12 matrix
        once (``ModuleNotFoundError: No module named 'qtpy'``).
        """
        code = (
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "import fea_toolkit.gui.views.interactor as m; "
            "loaded = sorted(k for k in sys.modules if 'qtpy' in k or k.startswith('PySide6')); "
            "assert not loaded, 'Qt got imported: ' + repr(loaded); "
            "assert m.ViewportInteraction is not None; "
            "print('clean')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout

    def test_import_opensees_package_without_solver(self):
        """`import fea_toolkit.opensees` must not load openseespy either."""
        code = (
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "import fea_toolkit.opensees; "
            "assert 'openseespy' not in sys.modules, 'openseespy loaded'; "
            "print('clean')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout

    def test_lazy_attr_resolution(self):
        """`from fea_toolkit import X` still works for solver-bound names."""
        from fea_toolkit import (
            AnalysisBuilder,
            MeshModel,
            SAP2000Parser,
            preprocess_model,
        )

        assert AnalysisBuilder is not None
        assert MeshModel is not None
        assert SAP2000Parser is not None
        assert preprocess_model is not None

    def test_lazy_opensees_attr_resolution(self):
        from fea_toolkit.opensees import (
            AnalysisBuilder,
            RecordingOpenSees,
            export_model_to_tcl,
            global_to_local_distributed_load,
        )

        assert AnalysisBuilder is not None
        assert RecordingOpenSees is not None
        assert callable(export_model_to_tcl)
        assert callable(global_to_local_distributed_load)

    def test_preprocessor_runs_without_openseespy(self):
        """preprocess_model is ops-free and must run without the solver."""
        code = (
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "from examples.sample_model import make_sample_model; "
            "from fea_toolkit import preprocess_model; "
            "md = make_sample_model(); "
            "mesh = preprocess_model(md, {'element_type': 'elasticBeamColumn'}); "
            "assert len(mesh.nodes) > 0; "
            "assert 'openseespy' not in sys.modules, 'openseespy loaded'; "
            "print('ok')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "ok" in out.stdout

    def test_missing_attr_raises(self):
        import fea_toolkit

        try:
            _ = fea_toolkit.definitely_not_a_real_name
        except AttributeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected AttributeError")


class TestOptionalPandasImports:
    """Modules that guard pandas must import cleanly when it is absent.

    ``sys.modules['pandas'] = None`` makes ``import pandas`` raise, which
    exercises the ``_MissingPandas`` fallback; their module-level ``pd.*``
    annotations must therefore not be evaluated at import time.
    """

    MODULES = (
        "fea_toolkit.plotting",
        "fea_toolkit.plotting.report",
        "fea_toolkit.analysis.linear",
        "fea_toolkit.report",
    )

    # ``fea_toolkit.gui.*`` is deliberately absent: it belongs to the
    # optional ``[gui]`` extra, so importing it needs Qt, not just the
    # absence of pandas.  The GUI import path is covered by the gui-test
    # CI job, which installs that extra.

    def test_guarded_modules_import_without_pandas(self):
        for module in self.MODULES:
            code = (
                f"import sys; sys.path.insert(0, {SRC!r}); "
                "sys.modules['pandas'] = None; "
                f"import {module}; "
                "print('clean')"
            )
            out = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, check=False
            )
            assert out.returncode == 0, f"{module} failed to import: {out.stderr}"
