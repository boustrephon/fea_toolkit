"""The workflow layer — the verb manifest, recipes, and the steps that run.

``fea_toolkit.workflow`` is Qt-free, and importing it must not load OpenSees
either (a verb's heavy import lives inside its function).  Both properties are
asserted here rather than assumed, because they are what lets the GUI render a
verb's parameter form before a model is open.

The end-to-end tests at the foot of the file do solve a model, so they wipe the
OpenSees domain in teardown (guardrails §5.6).
"""

import ast
import subprocess
import sys
import tempfile
from typing import ClassVar

import pytest

from fea_toolkit.model.sap_data import (
    AreaElement,
    Material,
    Node,
    Restraint,
    SAPModelData,
    ShellSection,
)
from fea_toolkit.model.selection import Selection
from fea_toolkit.workflow import (
    STEP_SPECS,
    ParamSpec,
    Recipe,
    Step,
    StepError,
    list_verbs,
    run_recipe,
    validate_params,
)

# ── Model helpers ─────────────────────────────────────────────────────


def _section(**extra) -> ShellSection:
    """A slab section, with non-zero frame properties unless overridden.

    ``A``/``I33``/``I22``/``J`` are inherited from the ``Section`` base and
    default to zero; a non-zero value is what makes a scale observable.
    """
    fields = {"A": 5.0, "I33": 2.0, "I22": 1.0, "J": 0.5}
    fields.update(extra)
    return ShellSection(name="SLAB", shape="Shell", material="C30", thickness=0.2, **fields)


def _slab_model() -> SAPModelData:
    """One four-node slab area in the XY plane at z = 0."""
    return SAPModelData(
        nodes={
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.0),
            "4": Node("4", 4, 0.0, 4.0, 0.0),
        },
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        materials={"C30": Material(name="C30", type="Concrete", E_mod=3.0e10)},
        sections={"SLAB": _section()},
        frame_elements={},
        area_elements={"A1": AreaElement("A1", 1, ["1", "2", "3", "4"])},
        frame_assignments={},
        area_assignments={"A1": "SLAB"},
        groups={},
        frame_auto_mesh={},
    )


def _model_with_a_combination() -> SAPModelData:
    """The sample cantilever plus one linear combination of its ``DEAD`` case.

    ``make_sample_model`` defines load cases but no combinations, so a reduction
    step has nothing to reduce on it.  This adds the smallest combination that
    still exercises the path: a single-leaf ``Linear Add``.
    """
    from examples.sample_model import make_sample_model
    from fea_toolkit.model.sap_data import LoadCombination, LoadCombinationEntry

    model = make_sample_model()
    model.load_combinations = {
        "GRAV": LoadCombination(
            name="GRAV",
            combo_type="Linear Add",
            entries=[LoadCombinationEntry(name="DEAD", factor=1.0, kind="case")],
        )
    }
    return model


def _wall_over_slab_model() -> SAPModelData:
    """A slab plus a vertical wall whose base nodes pass through the slab.

    The wall's two base nodes sit *inside* the slab's outline and on its plane,
    which is exactly the case ``split_slabs_at_walls`` exists for: the slab is
    otherwise meshed with no node at the wall line.
    """
    model = _slab_model()
    model.nodes.update(
        {
            "11": Node("11", 11, 1.0, 2.0, 0.0),
            "12": Node("12", 12, 3.0, 2.0, 0.0),
            "13": Node("13", 13, 3.0, 2.0, 3.0),
            "14": Node("14", 14, 1.0, 2.0, 3.0),
        }
    )
    model.area_elements["A2"] = AreaElement("A2", 2, ["11", "12", "13", "14"])
    model.area_assignments["A2"] = "SLAB"
    return model


def _masonry_model() -> SAPModelData:
    """A model whose section carries *only* a thickness — a masonry wall, in effect.

    Its ``A``/``I33``/``I22``/``J`` are the ``Section`` defaults (zero), because a
    shell's stiffness comes from :attr:`ShellSection.thickness`.  This is the
    section a "soften the masonry" step has to reach, and the one that shows why
    the two non-structural strategies need separate verbs.
    """
    model = _slab_model()
    model.sections["SLAB"] = ShellSection(name="SLAB", shape="Shell", material="C30", thickness=0.2)
    return model


# ── The manifest ──────────────────────────────────────────────────────


class TestManifest:
    """The verb manifest is pure data, and truthful about its verbs."""

    def test_verbs_are_listed_in_the_order_they_are_meant_to_run(self):
        assert list_verbs() == [
            "check_connectivity",
            "check_self_weight",
            "check_brace_buckling",
            "scale_sections",
            "mesh",
            "run_static",
            "combine",
            "chart",
        ]

    def test_every_spec_is_self_consistent(self):
        """A spec's key, its verb name and its parameter types must agree."""
        for verb, spec in STEP_SPECS.items():
            assert spec.verb == verb
            assert callable(spec.run)
            assert spec.kind in ("geometry", "model", "cases", "table", "figure")
            assert spec.help, f"{verb} has no help text"
            for name, param in spec.params.items():
                assert isinstance(param, ParamSpec), f"{verb}.{name}"
                assert param.help, f"{verb}.{name} has no help text"

    def test_importing_the_manifest_does_not_load_opensees(self):
        """A parameter form must be renderable without OpenSees."""
        code = "import sys, fea_toolkit.workflow; print('openseespy' in sys.modules)"
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert done.stdout.strip() == "False"

    def test_defaults_are_independent_copies(self):
        """A mutable default is copied per call, as ``validate_params`` does."""
        spec = STEP_SPECS["run_static"]
        first = spec.defaults()
        first["cases"]["DEAD"] = {"DEAD": 1.0}
        assert spec.defaults()["cases"] == {}

    def test_the_builder_config_keys_are_a_valid_manifest(self):
        """The config editor's manifest is data the editor can render."""
        from fea_toolkit.workflow import BUILDER_CONFIG_KEYS

        assert BUILDER_CONFIG_KEYS
        assert all(isinstance(spec, ParamSpec) for spec in BUILDER_CONFIG_KEYS.values())
        assert {spec.type for spec in BUILDER_CONFIG_KEYS.values()} <= {bool, int, float, str}
        assert all(spec.help for spec in BUILDER_CONFIG_KEYS.values())

    def test_run_static_declares_its_config_manifest(self):
        """The ``config`` dict param carries the manifest the editor renders from."""
        from fea_toolkit.workflow import BUILDER_CONFIG_KEYS, STEP_SPECS

        config = STEP_SPECS["run_static"].params["config"]
        assert config.type is dict
        assert config.manifest is BUILDER_CONFIG_KEYS


# ── Recipes as data ───────────────────────────────────────────────────


class TestRecipeSerialisation:
    """A recipe is data: it round-trips, and it refuses what it cannot mean."""

    def _recipe(self) -> Recipe:
        recipe = Recipe(name="Masonry building")
        recipe.add("scale_sections", Selection(sections=["brick wall"]), {"factor": 0.01})
        recipe.add("mesh", Selection(sections=["brick wall"]), {"split_slabs_at_walls": True})
        recipe.add("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}}, optional=True)
        return recipe

    def test_round_trip_through_a_dict_is_faithful(self):
        recipe = self._recipe()
        assert Recipe.from_dict(recipe.to_dict()).to_dict() == recipe.to_dict()

    def test_round_trip_through_a_json_file_is_faithful(self):
        recipe = self._recipe()
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/recipe.json"
            recipe.to_json(path)
            assert Recipe.from_json(path).to_dict() == recipe.to_dict()

    def test_the_name_and_selection_survive(self):
        rebuilt = Recipe.from_dict(self._recipe().to_dict())
        assert rebuilt.name == "Masonry building"
        assert rebuilt.steps[0].selection == Selection(sections=["brick wall"])

    def test_an_absent_selection_stays_absent(self):
        recipe = Recipe(steps=[Step("mesh")])
        assert Recipe.from_dict(recipe.to_dict()).steps[0].selection is None

    def test_an_empty_selection_is_not_read_as_an_absent_one(self):
        recipe = Recipe(steps=[Step("mesh", selection=Selection())])
        assert Recipe.from_dict(recipe.to_dict()).steps[0].selection is not None

    def test_an_unknown_verb_is_rejected_by_name(self):
        with pytest.raises(ValueError, match="unknown verb 'squeeze'"):
            Recipe.from_dict({"steps": [{"verb": "squeeze"}]})

    def test_a_step_that_is_not_a_mapping_is_rejected(self):
        with pytest.raises(ValueError, match="recipe step 0: expected a mapping, got str"):
            Recipe.from_dict({"steps": ["mesh"]})

    def test_an_unknown_parameter_is_rejected_rather_than_ignored(self):
        data = {"steps": [{"verb": "mesh", "params": {"split_wals": True}}]}
        with pytest.raises(ValueError, match="unknown parameter 'split_wals'"):
            Recipe.from_dict(data)

    def test_a_wrongly_typed_parameter_is_rejected(self):
        data = {"steps": [{"verb": "scale_sections", "params": {"factor": "tiny"}}]}
        with pytest.raises(ValueError, match="must be a number"):
            Recipe.from_dict(data)

    def test_add_rejects_a_bad_parameter_where_the_mistake_was_made(self):
        with pytest.raises(ValueError, match="unknown parameter"):
            Recipe().add("mesh", params={"nope": 1})

    def test_only_the_parameters_a_step_sets_are_written(self):
        """Defaults are applied at run time, not baked into the file."""
        recipe = Recipe()
        recipe.add("mesh", params={"split_slabs_at_walls": True})
        assert recipe.to_dict()["steps"][0]["params"] == {"split_slabs_at_walls": True}

    def test_the_python_export_compiles(self):
        compile(self._recipe().to_python(), "<recipe>", "exec")

    def test_the_python_export_carries_the_recipe_unchanged(self):
        recipe = self._recipe()
        tree = ast.parse(recipe.to_python())
        literal = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(getattr(target, "id", "") == "RECIPE" for target in node.targets)
        )
        assert Recipe.from_dict(ast.literal_eval(literal)).to_dict() == recipe.to_dict()


class _FakeMesh:
    """The smallest duck-typed stand-in for a ``MeshModel``."""

    def __init__(self) -> None:
        self.frame_elements: dict = {}
        self.area_elements: dict = {}


class _RecordingPreprocessor:
    """A Preprocessor stand-in that records how the ``mesh`` verb configured it."""

    calls: ClassVar[list] = []

    def __init__(self, config):
        self.config = dict(config)

    def run(self, md, load_shell_selection=None):
        type(self).calls.append((self.config, load_shell_selection))
        return _FakeMesh()


class TestMeshVerb:
    """The ``mesh`` verb is the Preprocessor's front door — nothing else."""

    def test_split_slabs_at_walls_false_leaves_the_slab_whole(self):
        recipe = Recipe(steps=[Step("mesh", params={"split_slabs_at_walls": False})])
        mesh = run_recipe(recipe, _wall_over_slab_model()).results[0].payload
        assert len(mesh.area_elements) == 2  # the slab and the wall, untouched

    def test_split_slabs_at_walls_true_cuts_the_slab_along_the_wall(self):
        """The option the Admin Building script relies on, demonstrated."""
        recipe = Recipe(steps=[Step("mesh", params={"split_slabs_at_walls": True})])
        mesh = run_recipe(recipe, _wall_over_slab_model()).results[0].payload
        assert len(mesh.area_elements) > 2  # the slab was subdivided

    def test_create_shells_false_reports_a_split_not_a_mesh(self):
        recipe = Recipe(steps=[Step("mesh", params={"create_shells": False})])
        assert run_recipe(recipe, _slab_model()).results[0].label == "Split"

    def test_the_selection_and_config_reach_the_preprocessor(self, monkeypatch):
        """Both approved options — the loads-only set and the wall split — arrive."""
        import fea_toolkit.opensees.preprocessor as preprocessor_module

        _RecordingPreprocessor.calls = []
        monkeypatch.setattr(preprocessor_module, "Preprocessor", _RecordingPreprocessor)
        selection = Selection(sections=["SLAB"])
        recipe = Recipe(steps=[Step("mesh", selection, {"split_slabs_at_walls": True})])
        run = run_recipe(recipe, _slab_model())

        config, passed = _RecordingPreprocessor.calls[0]
        assert config["split_slabs_at_walls"] is True
        assert config["create_shells"] is True  # the default was filled in
        assert passed == selection
        assert isinstance(run.results[0].payload, _FakeMesh)


class TestPerStepState:
    """A step's parameters belong to that step, never to the shared spec."""

    def test_two_steps_do_not_share_a_mutable_default(self):
        first = validate_params("run_static", STEP_SPECS["run_static"].params, {})
        second = validate_params("run_static", STEP_SPECS["run_static"].params, {})
        first["cases"]["X"] = {"DEAD": 1.0}
        assert second["cases"] == {}


class TestScaleSectionsVerb:
    """Softening a selection's sections — the other half of the script's setup."""

    def _step(self, selection=None, factor=0.01):
        return Step("scale_sections", selection, {"factor": factor})

    def test_the_matching_sections_properties_are_scaled(self):
        selection = Selection(sections=["SLAB"])
        run = run_recipe(Recipe(steps=[self._step(selection)]), _slab_model())
        scaled = run.results[0].payload.sections["SLAB"]
        got = {name: getattr(scaled, name) for name in ("A", "I33", "I22", "J")}
        assert got == pytest.approx({"A": 0.05, "I33": 0.02, "I22": 0.01, "J": 0.005})

    def test_a_masonry_wall_is_softened_through_its_thickness(self):
        """Option 1 reaches a shell: with A/I/J zero, thickness is the knob."""
        step = Step("scale_sections", Selection(sections=["SLAB"]), {"factor": 0.01})
        sections = run_recipe(Recipe(steps=[step]), _masonry_model()).results[0].payload.sections
        assert sections["SLAB"].thickness == pytest.approx(0.2 * 0.01)

    def test_naming_properties_a_section_lacks_changes_nothing(self):
        """A frame-shaped attribute list cannot soften a shell — and says so."""
        messages: list = []
        step = Step(
            "scale_sections",
            Selection(sections=["SLAB"]),
            {"factor": 0.01, "attributes": "A,I33,I22,J"},
        )
        run_recipe(Recipe(steps=[step]), _masonry_model(), log=messages.append)
        assert any("unchanged (every named property is zero)" in m for m in messages)

    def test_the_callers_model_is_never_mutated(self):
        model = _slab_model()
        run_recipe(Recipe(steps=[self._step(Selection(sections=["SLAB"]))]), model)
        assert model.sections["SLAB"].A == 5.0

    def test_a_selection_that_matches_nothing_leaves_every_section_alone(self):
        run = run_recipe(Recipe(steps=[self._step(Selection(sections=["NOPE"]))]), _slab_model())
        assert run.results[0].payload.sections["SLAB"].A == 5.0

    def test_a_factor_of_one_reports_that_nothing_changed(self):
        messages: list = []
        run_recipe(Recipe(steps=[self._step(factor=1.0)]), _slab_model(), log=messages.append)
        assert any("no property changed" in message for message in messages)

    def test_the_attributes_scaled_can_be_narrowed(self):
        step = Step("scale_sections", None, {"factor": 0.5, "attributes": "A"})
        scaled = run_recipe(Recipe(steps=[step]), _slab_model()).results[0].payload
        section = scaled.sections["SLAB"]
        got = {name: getattr(section, name) for name in ("A", "J")}
        assert got == pytest.approx({"A": 2.5, "J": 0.5})  # J was not named


class TestCheckVerbs:
    """The model checks, as ``table`` steps (P30 phase C, increment 1).

    A check runs on the parsed model alone — no OpenSees domain, no results —
    which is why these are the first ``table`` steps and why this file can test
    them without a solve.
    """

    def test_connectivity_returns_a_table_of_preformatted_cells(self):
        run = run_recipe(Recipe(steps=[Step("check_connectivity")]), _slab_model())
        (result,) = run.results
        assert result.kind == "table"
        assert result.payload.title == "Connectivity"
        assert result.payload.columns == ("check", "value")
        assert all(isinstance(cell, str) for row in result.payload.rows for cell in row)

    def test_an_unreferenced_node_is_counted_as_orphan(self):
        model = _slab_model()
        model.nodes["99"] = Node("99", 99, 8.0, 8.0, 0.0)  # referenced by nothing
        run = run_recipe(Recipe(steps=[Step("check_connectivity")]), model)
        rows = dict(run.results[0].payload.rows)
        assert int(rows["orphan nodes"]) == 1

    def test_self_weight_reports_its_verdict(self):
        run = run_recipe(Recipe(steps=[Step("check_self_weight")]), _slab_model())
        rows = dict(run.results[0].payload.rows)
        assert set(rows) == {"expected", "applied", "discrepancy", "tolerance", "passed"}

    def test_brace_buckling_is_scoped_by_the_steps_selection(self):
        """The checked elements are exactly the selected frames — no more, no fewer."""
        from examples.sample_model import make_rc_frame_model

        model = make_rc_frame_model()
        selection = Selection(sections=["COL"])  # the columns only; beams are excluded
        step = Step("check_brace_buckling", selection)
        run = run_recipe(Recipe(steps=[step]), model)

        table = run.results[0].payload
        assert table.title == "Brace buckling"
        assert table.columns[0] == "element"

        selected = selection.get_frame_ids(model)
        assert selected, "the selection must match a frame for this test to mean anything"
        assert len(selected) < len(model.frame_elements), "the selection must exclude a frame"
        assert {row[0] for row in table.rows} == set(selected)

    def test_a_check_names_its_own_parameters_only(self):
        """An undeclared parameter is rejected, exactly as for every other verb."""
        with pytest.raises(ValueError, match=r"unknown parameter\(s\) \['tol'\]"):
            Recipe().add("check_self_weight", params={"tol": 1e-3})


class TestFailuresAndCancellation:
    """A step that fails is either fatal or tolerated — never silent."""

    def test_a_non_optional_failure_raises_carrying_the_step_index(self):
        recipe = Recipe(steps=[Step("run_static")])  # no mesh step preceded it
        with pytest.raises(StepError) as info:
            run_recipe(recipe, _slab_model())
        assert info.value.index == 0
        assert info.value.verb == "run_static"
        assert "prepared topology" in str(info.value.cause)

    def test_an_optional_failure_is_recorded_and_the_run_continues(self):
        recipe = Recipe(
            steps=[
                Step("run_static", optional=True),  # fails: no topology yet
                Step("mesh"),  # still runs
            ]
        )
        run = run_recipe(recipe, _slab_model())
        assert [failure[1] for failure in run.failures] == ["run_static"]
        assert run.results[0].label == "Meshed"

    def test_cancellation_stops_the_run_at_a_step_boundary(self):
        recipe = Recipe(steps=[Step("mesh"), Step("mesh")])
        run = run_recipe(recipe, _slab_model(), cancel=lambda: True)
        assert run.cancelled is True
        assert run.results == []

    def test_a_cancellation_after_one_step_keeps_that_step(self):
        calls = {"n": 0}

        def cancel() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        recipe = Recipe(steps=[Step("mesh"), Step("mesh")])
        run = run_recipe(recipe, _slab_model(), cancel=cancel)
        assert run.cancelled is True
        assert len(run.results) == 1


class TestEndToEnd:
    """The chain a recipe exists to express: prepare, then solve."""

    def teardown_method(self):
        import openseespy.opensees as ops

        ops.wipe()

    def test_a_mesh_step_then_a_run_static_step_solves_the_sample_model(self):
        from examples.sample_model import make_sample_model

        recipe = Recipe(name="Cantilever")
        recipe.add("mesh")
        recipe.add("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}})
        run = run_recipe(recipe, make_sample_model())

        assert [result.kind for result in run.results] == ["geometry", "cases"]
        arrays = run.results[-1].payload
        assert any(name.startswith("static/") for name in arrays)

    def test_combine_without_solved_cases_fails_with_a_clear_message(self):
        with pytest.raises(StepError) as info:
            run_recipe(Recipe(steps=[Step("combine")]), _slab_model())
        assert "run_static" in str(info.value.cause)

    def test_a_cancelled_solve_marks_the_run_cancelled(self):
        """A run_static stopped mid-case marks the run cancelled, final step or not."""
        from examples.sample_model import make_sample_model

        calls = {"n": 0}

        def cancel() -> bool:
            calls["n"] += 1
            # False before 'mesh', False before 'run_static', False before the
            # first case, True before the second — so DEAD solves, WIND is skipped.
            return calls["n"] > 3

        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step(
                    "run_static",
                    params={"cases": {"DEAD": {"DEAD": 1.0}, "WIND": {"WIND": 1.0}}},
                ),
            ]
        )
        run = run_recipe(recipe, make_sample_model(), cancel=cancel)

        assert run.cancelled is True
        # The case solved before the cancellation is kept in the partial result.
        arrays = run.results[-1].payload
        assert any(name.startswith("static/DEAD") for name in arrays)

    def test_combine_returns_the_archive_a_solve_returns(self):
        """Both ``cases`` verbs hand a viewer one shape, so neither is a special case."""
        from fea_toolkit.io.results_repository import NpzResultsRepository

        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}}),
                Step("combine", params={"combinations": ["GRAV"]}),
            ]
        )
        run = run_recipe(recipe, _model_with_a_combination())

        assert [result.kind for result in run.results] == ["geometry", "cases", "cases"]
        arrays = run.results[-1].payload

        # The composite is keyed exactly like a solved case, so one repository
        # serves both and a view needs no per-verb branch.
        assert any(name.startswith("static/GRAV/") for name in arrays)
        repository = NpzResultsRepository(arrays)
        assert repository.cases() == ["GRAV"]
        assert repository.case_meta("GRAV").get("group") == "GRAV"

    def test_a_chart_step_renders_the_storey_profile_from_solved_cases(self):
        """A ``chart`` step turns a solved lateral case into a figure, no re-solve."""
        from matplotlib.figure import Figure

        from examples.sample_model import make_sample_model

        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("run_static", params={"cases": {"WIND": {"WIND": 1.0}}}),
                Step("chart"),
            ]
        )
        run = run_recipe(recipe, make_sample_model())

        assert [result.kind for result in run.results] == ["geometry", "cases", "figure"]
        figure = run.results[-1].payload
        assert figure is not None, "a lateral case should yield a storey profile"
        assert isinstance(figure, Figure)

    def test_a_chart_step_without_solved_cases_fails_clearly(self):
        with pytest.raises(StepError) as info:
            run_recipe(Recipe(steps=[Step("chart")]), _slab_model())
        assert "run_static" in str(info.value.cause)
