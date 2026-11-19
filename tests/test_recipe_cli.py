"""The recipe-runner CLI and its ``save_results`` / ``report`` verbs.

Covers the headless batch path: a model file + a recipe JSON, run through
``python -m fea_toolkit run``, emitting an NPZ results archive and/or a
plain-text report.  The pure text renderer is tested without OpenSees; the verb
and CLI tests solve the tiny sample model in-process (as ``test_recipe.py``'s
``TestEndToEnd`` does).
"""

import json

import pytest

from fea_toolkit.workflow import Recipe, Step, run_recipe


@pytest.fixture(autouse=True)
def _wipe_opensees():
    yield
    import openseespy.opensees as ops

    ops.wipe()


def test_render_static_text_report_is_pure():
    from examples.sample_model import make_sample_model
    from fea_toolkit.analysis.report_text import render_static_text_report

    md = make_sample_model()
    text = render_static_text_report(
        md,
        None,
        {"DEAD": {"nodal_displacements": {"1": [0.001, 0.0, 0.002, 0.0, 0.0, 0.0]}}},
        [
            {
                "analysis": "DEAD",
                "applied": {"fx": 10.0, "fy": 0.0, "fz": 0.0},
                "summed": {"fx": -10.0, "fy": 0.0, "fz": 0.0},
            }
        ],
    )

    assert "Case DEAD" in text
    assert "applied" in text and "reacted" in text
    assert "ux=+0.001" in text
    assert "Δfx" in text


def test_save_results_verb_without_cases_is_a_no_op():
    from examples.sample_model import make_sample_model
    from fea_toolkit.workflow.steps import StepContext
    from fea_toolkit.workflow.verbs.save_results import run_save_results

    logged: list = []
    context = StepContext(model_data=make_sample_model(), log=logged.append)
    result = run_save_results(context, Step("save_results", params={"path": "unused.npz"}))

    assert result == []
    assert any("no solved cases" in message for message in logged)


def test_save_results_writes_an_npz_the_repository_reads_back(tmp_path):
    from examples.sample_model import make_sample_model

    out = tmp_path / "results.npz"
    recipe = Recipe(
        steps=[
            Step("mesh"),
            Step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}}),
            Step("save_results", params={"path": str(out)}),
        ]
    )
    run = run_recipe(recipe, make_sample_model())

    assert [result.kind for result in run.results] == ["geometry", "cases", "session"]
    assert out.exists()

    from fea_toolkit.io.results_repository import NpzResultsRepository

    assert "DEAD" in NpzResultsRepository(str(out)).cases()


def test_report_step_renders_the_solved_case():
    from examples.sample_model import make_sample_model

    logged: list = []
    recipe = Recipe(
        steps=[
            Step("mesh"),
            Step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}}),
            Step("report"),
        ]
    )
    run = run_recipe(recipe, make_sample_model(), log=logged.append)

    assert run.results[-1].kind == "session"
    assert "Case DEAD" in run.results[-1].payload
    assert any("Case DEAD" in message for message in logged)


def test_run_subcommand_solves_and_writes_npz(tmp_path):
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_codec import model_to_json

    model = tmp_path / "model.json"
    model.write_text(model_to_json(make_sample_model()), encoding="utf-8")
    out_npz = tmp_path / "out.npz"
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps(
            {
                "name": "solve",
                "steps": [
                    {"verb": "mesh", "params": {}},
                    {"verb": "run_static", "params": {"cases": {"DEAD": {"DEAD": 1.0}}}},
                    {"verb": "save_results", "params": {"path": str(out_npz)}},
                ],
            }
        ),
        encoding="utf-8",
    )

    from fea_toolkit.__main__ import main

    assert main(["run", str(recipe), "--model", str(model)]) == 0
    assert out_npz.exists()


def test_run_subcommand_missing_model_is_a_usage_error(tmp_path):
    from fea_toolkit.__main__ import main

    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps({"steps": [{"verb": "save", "params": {"path": "model.json"}}]}),
        encoding="utf-8",
    )
    assert main(["run", str(recipe)]) == 2


def test_shipped_preset_recipes_load():
    from pathlib import Path

    from fea_toolkit.workflow.recipe import Recipe

    recipes_dir = Path(__file__).resolve().parent.parent / "examples" / "recipes"
    for name in ("solve.json", "solve_report.json", "convert.json"):
        recipe = Recipe.from_json(str(recipes_dir / name))
        assert recipe.steps, f"{name} should declare steps"


def test_run_subcommand_output_overrides_save_results_path(tmp_path):
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.model_codec import model_to_json

    model = tmp_path / "model.json"
    model.write_text(model_to_json(make_sample_model()), encoding="utf-8")
    default_npz = tmp_path / "default.npz"
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps(
            {
                "name": "solve",
                "steps": [
                    {"verb": "mesh", "params": {}},
                    {"verb": "run_static", "params": {"cases": {"DEAD": {"DEAD": 1.0}}}},
                    {"verb": "save_results", "params": {"path": str(default_npz)}},
                ],
            }
        ),
        encoding="utf-8",
    )
    out_npz = tmp_path / "custom.npz"

    from fea_toolkit.__main__ import main

    assert main(["run", str(recipe), "--model", str(model), "--output", str(out_npz)]) == 0
    assert out_npz.exists()
    assert not default_npz.exists()


def test_open_results_feeds_report_and_save_results(tmp_path):
    from examples.sample_model import make_sample_model

    solved = tmp_path / "solved.npz"
    solve = Recipe(
        steps=[
            Step("mesh"),
            Step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}}),
            Step("save_results", params={"path": str(solved)}),
        ]
    )
    run_recipe(solve, make_sample_model())
    assert solved.exists()

    # Report the archive without a source model.
    logged: list = []
    report = Recipe(steps=[Step("open_results", params={"path": str(solved)}), Step("report")])
    run = run_recipe(report, make_sample_model(), log=logged.append)
    assert run.results[-1].kind == "session"
    assert "Case DEAD" in run.results[-1].payload

    # Re-save the archive through the verb — a second, distinct archive.
    resaved = tmp_path / "resaved.npz"
    resave = Recipe(
        steps=[
            Step("open_results", params={"path": str(solved)}),
            Step("save_results", params={"path": str(resaved)}),
        ]
    )
    run_recipe(resave, make_sample_model())
    assert resaved.exists()

    from fea_toolkit.io.results_repository import NpzResultsRepository

    assert "DEAD" in NpzResultsRepository(str(resaved)).cases()
