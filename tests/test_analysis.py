"""Tests for the Analysis framework (analysis/base.py, modal.py,
static.py, rs.py, pushover.py, manager.py).
"""

import pytest

from fea_toolkit.analysis.base import (
    _MODAL_DEFAULTS,
    _PUSHOVER_STEEL_DEFAULTS,
    _RESPONSE_SPECTRUM_DEFAULTS,
    _STATIC_LINEAR_DEFAULTS,
    Analysis,
    AnalysisCaseSpec,
    AnalysisResult,
)
from fea_toolkit.analysis.manager import AnalysisManager
from fea_toolkit.analysis.modal import ModalAnalysis
from fea_toolkit.analysis.pushover import PushoverAnalysis
from fea_toolkit.analysis.rs import ResponseSpectrumAnalysis
from fea_toolkit.analysis.static import StaticAnalysis
from fea_toolkit.model.mesh_model import MeshModel

# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeMeshModel(MeshModel):
    """Minimal MeshModel stand-in — all collections empty, no OpenSees state."""

    def __init__(self):
        super().__init__(
            nodes={},
            frame_elements={},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
        )


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
        self._modal_result = None  # set via _accept_dependency hook

    @classmethod
    def defaults(cls) -> dict:
        return {}

    def _accept_dependency(self, dep_result, dep_type):
        # Only accept results of the declared dependency type
        if dep_type is _FakeAnalysis and self._modal_result is None:
            self._modal_result = dep_result

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


# ── Analysis defaults (plain dicts) ────────────────────────────────────────


class TestAnalysisDefaults:
    def test_analysis_case_spec_to_dict(self):
        spec = AnalysisCaseSpec(name="test", analysis_type="pushover")
        data = spec.to_dict()
        assert data["name"] == "test"
        assert data["analysis_type"] == "pushover"
        assert data["config"] == {}
        assert data["kwargs"] == {}

    def test_static_defaults_exist(self):
        d = dict(_STATIC_LINEAR_DEFAULTS)
        assert "element_type" in d
        assert "solver_algorithm" in d

    def test_modal_defaults_exist(self):
        d = dict(_MODAL_DEFAULTS)
        assert "element_type" in d

    def test_rs_defaults_exist(self):
        d = dict(_RESPONSE_SPECTRUM_DEFAULTS)
        assert "element_type" in d

    def test_pushover_steel_defaults_exist(self):
        d = dict(_PUSHOVER_STEEL_DEFAULTS)
        assert "element_type" in d
        assert d["element_type"] == "nonlinearBeamColumn"


# ── Analysis ABC ────────────────────────────────────────────────────────────


class TestAnalysis:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Analysis(_FakeMeshModel())  # type: ignore[abstract]  # deliberate

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
        with pytest.raises(ValueError, match=r"requires.*_FakeAnalysis"):
            mgr.run_all()

    def test_circular_dependency_raises(self):
        class CircularA(Analysis):
            @classmethod
            def defaults(cls):
                return {}

            @property
            def requires(self):
                return [CircularB]

            @property
            def provides(self):
                return {"a"}

            def run(self):
                return AnalysisResult("ca", "A", {})

        class CircularB(Analysis):
            @classmethod
            def defaults(cls):
                return {}

            @property
            def requires(self):
                return [CircularA]

            @property
            def provides(self):
                return {"b"}

            def run(self):
                return AnalysisResult("cb", "B", {})

        mgr = AnalysisManager(_FakeMeshModel())
        mgr.add(CircularA(_FakeMeshModel(), name="ca"))
        mgr.add(CircularB(_FakeMeshModel(), name="cb"))
        with pytest.raises(ValueError, match=r"(?i)circular"):
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
        dummy = AnalysisResult(name="dummy", analysis_type="ModalAnalysis", data={})
        a = ResponseSpectrumAnalysis(
            _FakeMeshModel(),
            modal_result=dummy,
            direction="x",
            T_spec=[0.0, 1.0],
            Sa_spec=[1.0, 1.0],
        )
        assert a.requires == [ModalAnalysis]
        assert a._modal_result is dummy  # wired from constructor
        assert "rs_nodal_displacements" in a.provides

    def test_pushover_analysis_init(self):
        dummy = AnalysisResult(name="dummy", analysis_type="ModalAnalysis", data={})
        a = PushoverAnalysis(
            _FakeMeshModel(),
            modal_result=dummy,
            lateral_load_type="mode1",
            rs_modal_base_shear={"X": [1.0], "Y": [1.0]},
        )
        assert a.requires == [ModalAnalysis]
        assert a.lateral_load_type == "mode1"
        assert a._modal_result is dummy  # wired from constructor
        assert a.rs_modal_base_shear == {"X": [1.0], "Y": [1.0]}

    def test_pushover_defaults(self):
        d = PushoverAnalysis.defaults()
        assert isinstance(d, dict)
        assert "solver_algorithm" in d


# ── generate_report integration test (no OpenSeesPy) ──────────────────


class TestGenerateReportManagerPath:
    """Verify that generate_report always uses the AnalysisManager path."""

    def test_run_via_manager_param_removed(self):
        import inspect

        from fea_toolkit.report import generate_report

        sig = inspect.signature(generate_report)
        # run_via_manager param has been removed (always uses manager path)
        assert "run_via_manager" not in sig.parameters

    def test_inline_path_still_default(self):
        from fea_toolkit.report import _DEFAULT_CONFIG

        # The default config should not include run_via_manager
        # (it's a function parameter, not a config key)
        assert "run_via_manager" not in _DEFAULT_CONFIG
