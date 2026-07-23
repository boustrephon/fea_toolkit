"""Tests for the Analysis framework (analysis/base.py, modal.py,
static.py, rs.py, pushover.py, manager.py).
"""

import numpy as np
import pytest

from fea_toolkit.analysis.base import (
    Analysis,
    AnalysisDefaults,
    AnalysisResult,
    STATIC_LINEAR_DEFAULTS,
    MODAL_DEFAULTS,
    RESPONSE_SPECTRUM_DEFAULTS,
    PUSHOVER_STEEL_DEFAULTS,
)
from fea_toolkit.analysis.modal import ModalAnalysis
from fea_toolkit.analysis.static import StaticAnalysis
from fea_toolkit.analysis.rs import ResponseSpectrumAnalysis
from fea_toolkit.analysis.pushover import PushoverAnalysis
from fea_toolkit.analysis.manager import AnalysisManager


# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeMeshModel:
    """Minimal stand-in for MeshModel — just enough to construct analyses."""
    pass


class _FakeAnalysis(Analysis):
    """Minimal concrete Analysis subclass for testing the base class."""

    @classmethod
    def defaults(cls) -> dict:
        return {}

    @property
    def requires(self) -> list:
        return []

    @property
    def provides(self) -> set:
        return {"dummy"}

    def run(self) -> AnalysisResult:
        return AnalysisResult(
            name=self.name,
            analysis_type="_FakeAnalysis",
            data={"answer": 42},
        )


class _DepAnalysis(Analysis):
    """Analysis that requires _FakeAnalysis."""

    def __init__(self, mesh_model, name=None, config=None):
        super().__init__(mesh_model, name, config)
        self._modal_result = None  # manager.inject_dependencies sets this

    @classmethod
    def defaults(cls) -> dict:
        return {}

    @property
    def requires(self) -> list:
        return [_FakeAnalysis]

    @property
    def provides(self) -> set:
        return {"dependent"}

    def run(self) -> AnalysisResult:
        return AnalysisResult(
            name=self.name,
            analysis_type="_DepAnalysis",
            data={"injected": self._modal_result is not None},
        )


# ── AnalysisResult ──────────────────────────────────────────────────────────


class TestAnalysisResult:
    def test_basic_construction(self):
        r = AnalysisResult(
            name="test",
            analysis_type="ModalAnalysis",
            data={"periods": [1.0, 0.5]},
        )
        assert r.name == "test"
        assert r.analysis_type == "ModalAnalysis"
        assert r.data == {"periods": [1.0, 0.5]}
        assert r.metadata == {}

    def test_with_metadata(self):
        r = AnalysisResult(
            name="modal_12",
            analysis_type="ModalAnalysis",
            data={"periods": [1.0]},
            metadata={"n_modes": 12},
        )
        assert r.metadata == {"n_modes": 12}

    def test_repr(self):
        r = AnalysisResult(name="x", analysis_type="T", data={})
        assert "AnalysisResult" in repr(r)
        assert "x" in repr(r)


# ── AnalysisDefaults ────────────────────────────────────────────────────────


class TestAnalysisDefaults:
    def test_to_dict(self):
        d = AnalysisDefaults(element_type="elastic", num_int_pts=5)
        data = d.to_dict()
        assert data["element_type"] == "elastic"
        assert data["num_int_pts"] == 5
        assert "solver_algorithm" in data  # should have defaults for missing

    def test_presets_exist(self):
        for preset in [
            STATIC_LINEAR_DEFAULTS,
            MODAL_DEFAULTS,
            RESPONSE_SPECTRUM_DEFAULTS,
            PUSHOVER_STEEL_DEFAULTS,
        ]:
            assert isinstance(preset, AnalysisDefaults)
            d = preset.to_dict()
            assert "element_type" in d
            assert "num_int_pts" in d


# ── Analysis ABC ────────────────────────────────────────────────────────────


class TestAnalysis:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Analysis(_FakeMeshModel())  # type: ignore

    def test_concrete_subclass(self):
        a = _FakeAnalysis(_FakeMeshModel(), name="test", config={"key": 1})
        assert a.name == "test"
        assert a.config == {"key": 1}
        assert a.mesh_model is not None

    def test_auto_name(self):
        a = _FakeAnalysis(_FakeMeshModel())
        assert a.name == "_FakeAnalysis"

    def test_run(self):
        a = _FakeAnalysis(_FakeMeshModel())
        r = a.run()
        assert r.data["answer"] == 42
        assert r.analysis_type == "_FakeAnalysis"


# ── AnalysisManager ─────────────────────────────────────────────────────────


class TestAnalysisManager:
    def test_add_and_run(self):
        mgr = AnalysisManager(_FakeMeshModel())
        mgr.add(_FakeAnalysis(_FakeMeshModel(), name="a1"))
        results = mgr.run_all()
        assert "a1" in results
        assert results["a1"].data["answer"] == 42

    def test_dependency_resolution(self):
        mgr = AnalysisManager(_FakeMeshModel())
        dep = _DepAnalysis(_FakeMeshModel(), name="dep")
        base = _FakeAnalysis(_FakeMeshModel(), name="base")
        mgr.add(base)
        mgr.add(dep)
        results = mgr.run_all()
        assert results["dep"].data["injected"] is True

    def test_unmet_dependency_raises(self):
        mgr = AnalysisManager(_FakeMeshModel())
        dep = _DepAnalysis(_FakeMeshModel(), name="dep")
        mgr.add(dep)
        with pytest.raises(ValueError, match="requires.*_FakeAnalysis"):
            mgr.run_all()

    def test_circular_dependency_raises(self):
        class CircularA(Analysis):
            @classmethod
            def defaults(cls): return {}
            @property
            def requires(self): return [CircularB]
            @property
            def provides(self): return {"a"}
            def run(self): return AnalysisResult("ca", "A", {})

        class CircularB(Analysis):
            @classmethod
            def defaults(cls): return {}
            @property
            def requires(self): return [CircularA]
            @property
            def provides(self): return {"b"}
            def run(self): return AnalysisResult("cb", "B", {})

        mgr = AnalysisManager(_FakeMeshModel())
        mgr.add(CircularA(_FakeMeshModel(), name="ca"))
        mgr.add(CircularB(_FakeMeshModel(), name="cb"))
        with pytest.raises(ValueError, match="(?i)circular"):
            mgr.run_all()

    def test_run_one(self):
        mgr = AnalysisManager(_FakeMeshModel())
        a = _FakeAnalysis(_FakeMeshModel(), name="solo")
        r = mgr.run_one(a)
        assert r.data["answer"] == 42
        assert mgr.results["solo"].data["answer"] == 42

    def test_chaining(self):
        mgr = AnalysisManager(_FakeMeshModel())
        mgr.add(_FakeAnalysis(_FakeMeshModel(), name="a")).add(
            _FakeAnalysis(_FakeMeshModel(), name="b")
        )
        assert len(mgr._analyses) == 2


# ── Import & signature tests (no OpenSeesPy required) ──────────────────────


class TestAnalysisSignatures:
    def test_modal_analysis_init(self):
        """ModalAnalysis can be instantiated with a mock mesh_model."""
        a = ModalAnalysis(_FakeMeshModel(), n_modes=6)
        assert a.n_modes == 6
        assert a.requires == []
        assert "periods" in a.provides

    def test_modal_defaults(self):
        d = ModalAnalysis.defaults()
        assert isinstance(d, dict)

    def test_static_analysis_init(self):
        a = StaticAnalysis(_FakeMeshModel())
        assert a.requires == []
        assert "df_linear" in a.provides

    def test_rs_analysis_init(self):
        a = ResponseSpectrumAnalysis(
            _FakeMeshModel(),
            modal_result=None,
            direction="x",
            T_spec=np.array([0.0, 1.0]),
            Sa_spec=np.array([1.0, 1.0]),
        )
        assert a.requires == [ModalAnalysis]
        assert a._modal_result is None  # not injected yet
        assert "rs_nodal_displacements" in a.provides

    def test_pushover_analysis_init(self):
        a = PushoverAnalysis(
            _FakeMeshModel(),
            modal_result=None,
            lateral_load_type="mode1",
        )
        assert a.requires == [ModalAnalysis]
        assert a.lateral_load_type == "mode1"
        assert a._modal_result is None

    def test_pushover_defaults(self):
        d = PushoverAnalysis.defaults()
        assert isinstance(d, dict)
        assert "solver_algorithm" in d


# ── generate_report integration test (no OpenSeesPy) ──────────────────


class TestGenerateReportManagerPath:
    """Verify that generate_report accepts run_via_manager without error."""

    def test_run_via_manager_parameter_accepted(self):
        from fea_toolkit.report import generate_report
        import inspect
        sig = inspect.signature(generate_report)
        assert "run_via_manager" in sig.parameters
        assert sig.parameters["run_via_manager"].default is False

    def test_inline_path_still_default(self):
        from fea_toolkit.report import generate_report
        from fea_toolkit.report import _DEFAULT_CONFIG
        # The default config should not include run_via_manager
        # (it's a function parameter, not a config key)
        assert "run_via_manager" not in _DEFAULT_CONFIG
