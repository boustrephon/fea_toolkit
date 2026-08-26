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
