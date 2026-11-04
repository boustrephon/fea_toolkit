"""Tests for the out-of-process analysis backend — driver and verb delegation.

The driver runs :func:`~fea_toolkit.analysis.linear.run_case_set` against a
stage file and writes ``results.npz`` + ``meta.json``; the ``run_static`` verb
delegates to it when ``openseespy`` is unavailable.  These tests exercise both
ends, and one full round-trip through a real subprocess (using this test
interpreter, which carries OpenSeesPy and an editable ``fea_toolkit``).
"""

from __future__ import annotations

import json
import sys

import pytest

from fea_toolkit.opensees import runtime
from fea_toolkit.workflow import Recipe, run_recipe


class TestSubprocessDriver:
    def teardown_method(self):
        import openseespy.opensees as ops

        ops.wipe()

    def test_static_driver_writes_results_and_meta(self, tmp_path):
        from examples.sample_model import make_sample_model
        from fea_toolkit.io.npz_reader import read_results_npz
        from fea_toolkit.io.stage_writer import write_model_file
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.opensees.subprocess_driver import main as driver_main

        md = make_sample_model()
        mesh = preprocess_model(md)
        stage = tmp_path / "model.npz"
        write_model_file(str(stage), sap=md, mesh=mesh, fmt="npz")

        out_dir = tmp_path / "out"
        code = driver_main(
            [
                "--model",
                str(stage),
                "--analysis",
                "static",
                "--cases",
                json.dumps({"DEAD": {"DEAD": 1.0}}),
                "--config",
                "{}",
                "--out-dir",
                str(out_dir),
            ]
        )
        assert code == 0

        arrays = read_results_npz(str(out_dir / "results.npz"))
        assert any(name.startswith("static/") for name in arrays)

        meta = json.loads((out_dir / "meta.json").read_text())
        assert meta["cases"] == ["DEAD"]
        assert meta["failed"] == []
        assert meta["cancelled"] is False
        assert "DEAD" in meta["raw"]
        assert len(meta["summaries"]) == 1

    def test_modal_driver_writes_analysis_result(self, tmp_path):
        from examples.sample_model import make_sample_model
        from fea_toolkit.analysis.base import AnalysisResult
        from fea_toolkit.io.stage_writer import write_model_file
        from fea_toolkit.opensees._exchange import analysis_result_from_jsonable
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.opensees.subprocess_driver import main as driver_main

        md = make_sample_model()
        mesh = preprocess_model(md)
        stage = tmp_path / "model.npz"
        write_model_file(str(stage), sap=md, mesh=mesh, fmt="npz")

        params_file = tmp_path / "params.json"
        params_file.write_text(json.dumps({"n_modes": 3, "name": "M", "config": {}}))

        out_dir = tmp_path / "out"
        code = driver_main(
            [
                "--model",
                str(stage),
                "--analysis",
                "modal",
                "--params-file",
                str(params_file),
                "--out-dir",
                str(out_dir),
            ]
        )
        assert code == 0

        meta = json.loads((out_dir / "meta.json").read_text())
        assert meta["kind"] == "analysis_result"
        result = analysis_result_from_jsonable(meta["result"])
        assert isinstance(result, AnalysisResult)
        assert result.name == "M"
        assert result.analysis_type == "ModalAnalysis"
        assert len(result.data["modal"]["periods"]) == 3
        # The mode-shape keys must survive the JSON round-trip as integers.
        assert all(isinstance(k, int) for k in result.data["shapes"])


class TestVerbDelegation:
    def teardown_method(self):
        import openseespy.opensees as ops

        ops.wipe()

    def test_run_static_delegates_when_openseespy_is_unavailable(self, monkeypatch):
        from examples.sample_model import make_sample_model

        monkeypatch.setattr(runtime, "opensees_available", lambda: False)

        recipe = Recipe(name="Cantilever")
        recipe.add("mesh")
        recipe.add("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}})
        run = run_recipe(recipe, make_sample_model(), opensees_python=sys.executable)

        assert [result.kind for result in run.results] == ["geometry", "cases"]
        arrays = run.results[-1].payload
        assert any(name.startswith("static/") for name in arrays)

    def test_delegation_failure_raises_with_the_subprocess_tail(self, monkeypatch):
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees import delegate
        from fea_toolkit.workflow.steps import StepError

        monkeypatch.setattr(runtime, "opensees_available", lambda: False)
        monkeypatch.setattr(
            delegate,
            "run_python",
            lambda *args, **kwargs: (1, "line one\nline two\nTraceback: boom"),
        )

        recipe = Recipe(name="Cantilever")
        recipe.add("mesh")
        recipe.add("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}})
        with pytest.raises(StepError) as info:
            run_recipe(recipe, make_sample_model())
        assert "out-of-process static analysis failed" in str(info.value.cause)
        assert "Traceback: boom" in str(info.value.cause)

    def test_run_modal_delegates_when_openseespy_is_unavailable(self, monkeypatch):
        from examples.sample_model import make_sample_model

        monkeypatch.setattr(runtime, "opensees_available", lambda: False)

        recipe = Recipe(name="Cantilever")
        recipe.add("mesh")
        recipe.add("modal", params={"n_modes": 3, "name": "M"})
        run = run_recipe(recipe, make_sample_model(), opensees_python=sys.executable)

        assert [result.kind for result in run.results] == ["geometry", "modal"]
        result = run.results[-1].payload
        assert len(result.data["modal"]["periods"]) == 3
        # The mode shapes crossed the subprocess boundary; their keys must be ints.
        assert all(isinstance(k, int) for k in result.data["shapes"])

    def test_run_response_spectrum_delegates_when_openseespy_is_unavailable(self, monkeypatch):
        from examples.sample_model import make_sample_model

        monkeypatch.setattr(runtime, "opensees_available", lambda: False)

        recipe = Recipe(name="Cantilever")
        recipe.add("mesh")
        recipe.add("modal", params={"n_modes": 3, "name": "M"})
        recipe.add(
            "response_spectrum",
            params={
                "direction": "X",
                "T_spec": [0.1, 0.2, 0.5, 1.0, 2.0],
                "Sa_spec": [0.4, 0.4, 0.3, 0.15, 0.05],
            },
        )
        run = run_recipe(recipe, make_sample_model(), opensees_python=sys.executable)

        assert [result.kind for result in run.results] == ["geometry", "modal", "response_spectrum"]
        result = run.results[-1].payload
        assert result.data["direction"] == "X"
        assert "rs_result" in result.data

    def test_run_pushover_delegates_when_openseespy_is_unavailable(self, monkeypatch):
        from examples.sample_model import make_sample_model

        monkeypatch.setattr(runtime, "opensees_available", lambda: False)

        recipe = Recipe(name="Cantilever")
        recipe.add("mesh")
        recipe.add("modal", params={"n_modes": 3, "name": "M"})
        recipe.add(
            "pushover",
            params={
                "material_type": "steel",
                "num_steps": 5,
                "config": {"hinge_model": "lumped"},
            },
        )
        run = run_recipe(recipe, make_sample_model(), opensees_python=sys.executable)

        assert [result.kind for result in run.results] == ["geometry", "modal", "pushover"]
        result = run.results[-1].payload
        assert set(result.data.keys()) == {"+X", "-X", "+Y", "-Y"}
