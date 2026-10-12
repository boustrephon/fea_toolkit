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
            "mesh_checks",
            "hinge_length",
            "member_shear_capacity",
            "wall_shear_check",
            "scale_sections",
            "fix_shell_only_base",
            "mesh",
            "save_model",
            "run_static",
            "load_verification",
            "modal",
            "response_spectrum",
            "pushover",
            "combine",
            "chart",
        ]

    def test_every_spec_is_self_consistent(self):
        """A spec's key, its verb name and its parameter types must agree."""
        for verb, spec in STEP_SPECS.items():
            assert spec.verb == verb
            assert callable(spec.run)
            assert spec.kind in (
                "geometry",
                "model",
                "cases",
                "modal",
                "response_spectrum",
                "pushover",
                "table",
                "figure",
            )
            assert spec.help, f"{verb} has no help text"
            for name, param in spec.params.items():
                assert isinstance(param, ParamSpec), f"{verb}.{name}"
                assert param.help, f"{verb}.{name} has no help text"

    def test_analysis_results_that_are_not_cases_have_their_own_kind(self):
        """RS and pushover return an ``AnalysisResult``, not solved case payloads —
        so they must not claim the ``cases`` kind that ``combine`` / ``chart`` read."""
        assert STEP_SPECS["response_spectrum"].kind == "response_spectrum"
        assert STEP_SPECS["pushover"].kind == "pushover"
        assert STEP_SPECS["response_spectrum"].kind != "cases"
        assert STEP_SPECS["pushover"].kind != "cases"

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

    def test_modal_declares_its_config_manifest(self):
        """The ``modal`` step exposes the builder config, including the shell-only fix."""
        from fea_toolkit.workflow import BUILDER_CONFIG_KEYS, STEP_SPECS

        config = STEP_SPECS["modal"].params["config"]
        assert config.type is dict
        assert config.manifest is BUILDER_CONFIG_KEYS
        assert config.default == {}

    def test_the_shell_only_base_fix_is_a_config_key_off_by_default(self):
        from fea_toolkit.workflow import BUILDER_CONFIG_KEYS

        key = BUILDER_CONFIG_KEYS["fix_shell_only_base"]
        assert key.type is bool
        assert key.default is False

    def test_response_spectrum_n_modes_defaults_to_all(self):
        from fea_toolkit.workflow import STEP_SPECS

        n_modes = STEP_SPECS["response_spectrum"].params["n_modes"]
        assert n_modes.type is int
        assert n_modes.default is None
        assert n_modes.allow_none is True


# ── Recipes as data ───────────────────────────────────────────────────


class TestAnalysisVerbs:
    """The analysis verbs are thin wrappers — what the report passes must arrive."""

    def test_pushover_declares_the_report_s_parameters(self):
        """``generate_report`` passes ``gravity_patterns`` / ``brace_type`` /
        ``brace_sections`` to the analysis; the verb must declare them so a
        recipe step can carry the same information."""
        params = STEP_SPECS["pushover"].params
        assert params["gravity_patterns"].type is dict
        assert params["brace_type"].choices == ("truss", "beam")
        assert params["brace_sections"].type is list
        assert params["return_builders"].type is bool

    def test_pushover_passes_gravity_and_brace_through(self, monkeypatch):
        """Every declared parameter reaches ``run_pushover_analysis`` unchanged."""
        import fea_toolkit.analysis.pushover as pushover_module
        from fea_toolkit.workflow.steps import StepContext

        captured: dict = {}

        def fake_run_pushover_analysis(_mesh, _modal, **kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(pushover_module, "run_pushover_analysis", fake_run_pushover_analysis)
        context = StepContext(model_data=_slab_model(), model=object(), results={"modal": object()})
        step = Step(
            "pushover",
            params={
                "gravity_patterns": {"DEAD": 1.0, "LIVE": 0.5},
                "brace_type": "beam",
                "brace_sections": ["BRACE-1"],
                "return_builders": True,
            },
        )
        STEP_SPECS["pushover"].run(context, step)

        assert captured["gravity_patterns"] == {"DEAD": 1.0, "LIVE": 0.5}
        assert captured["brace_type"] == "beam"
        assert captured["brace_sections"] == ["BRACE-1"]
        assert captured["return_builders"] is True

    def test_pushover_declares_the_retention_parameters(self):
        """The retention knobs are declared with large-model-friendly defaults."""
        params = STEP_SPECS["pushover"].params
        assert params["step_stride"].default == 1
        assert params["store_node_disp"].default is True
        assert params["store_frame_forces"].default is False
        assert params["store_shell_forces"].default is False

    def test_pushover_retention_knobs_reach_the_builder_config(self, monkeypatch):
        """The retention params become the flat config keys the runner reads."""
        import fea_toolkit.analysis.pushover as pushover_module
        from fea_toolkit.workflow.steps import StepContext

        captured: dict = {}

        def fake_run_pushover_analysis(_mesh, _modal, **kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(pushover_module, "run_pushover_analysis", fake_run_pushover_analysis)
        context = StepContext(model_data=_slab_model(), model=object(), results={"modal": object()})
        step = Step(
            "pushover",
            params={
                "step_stride": 2,
                "store_node_disp": True,
                "store_frame_forces": True,
                "store_shell_forces": False,
                "store_section_deformations": True,
            },
        )
        STEP_SPECS["pushover"].run(context, step)

        config = captured["config"]
        assert config["record_pushover_steps"] is True
        assert config["pushover_step_stride"] == 2
        assert config["pushover_record_node_disp"] is True
        assert config["pushover_record_frame_forces"] is True
        assert config["pushover_record_shell_forces"] is False
        assert config["pushover_record_section_deformations"] is True

    def test_response_spectrum_resolves_each_source(self):
        """``resolve_spectrum`` maps source → (T, Sa) from the right place."""
        from fea_toolkit.model.sap_data import LoadPattern, ResponseSpectrumFunction
        from fea_toolkit.workflow.steps import StepContext
        from fea_toolkit.workflow.verbs.response_spectrum import resolve_spectrum

        md = _slab_model()
        md.response_spectrum_functions = {
            "UNIFRS": ResponseSpectrumFunction("UNIFRS", [0.0, 1.0], [1.0, 2.0])
        }
        md.load_patterns = {
            "SLX": LoadPattern(
                "SLX",
                "Quake",
                auto_data={
                    "AUTO SEISMIC - CHINESE 2010": {
                        "AlphaMax": 0.16,
                        "Tg": 0.4,
                        "DampRatio": 0.05,
                    }
                },
            )
        }
        context = StepContext(model_data=md, model=object(), results={})

        T, Sa = resolve_spectrum(context, source="model", name="UNIFRS", T_spec=[], Sa_spec=[])
        assert T == [0.0, 1.0]
        assert Sa == [1.0, 2.0]

        T, Sa = resolve_spectrum(context, source="auto_seismic", name="", T_spec=[], Sa_spec=[])
        assert T[0] == 0.0
        assert len(T) == len(Sa)

        with pytest.raises(ValueError):
            resolve_spectrum(context, source="model", name="NOPE", T_spec=[], Sa_spec=[])
        with pytest.raises(ValueError):
            resolve_spectrum(context, source="explicit", name="", T_spec=[], Sa_spec=[])

    def test_response_spectrum_auto_seismic_requires_a_table(self):
        """A model without an AUTO SEISMIC table cannot supply that source."""
        from fea_toolkit.workflow.steps import StepContext
        from fea_toolkit.workflow.verbs.response_spectrum import resolve_spectrum

        context = StepContext(model_data=_slab_model(), model=object(), results={})
        with pytest.raises(ValueError):
            resolve_spectrum(context, source="auto_seismic", name="", T_spec=[], Sa_spec=[])


class TestModalCaseSelection:
    """Multiple modal analyses, and the RS/pushover ``modal_case`` selection."""

    @staticmethod
    def _load_case(name, case_type, modal_case="", case_data=None):
        from fea_toolkit.model.sap_data import LoadCase

        return LoadCase(
            case_name=name,
            case_type=case_type,
            design_type_option="Prog Det",
            design_type="Other",
            design_action_option="Prog Det",
            design_action="Other",
            modal_case=modal_case,
            case_data=case_data or {},
        )

    def test_spectrum_and_pushover_declare_modal_case(self):
        from fea_toolkit.workflow.verbs.pushover import PUSHOVER_PARAMS
        from fea_toolkit.workflow.verbs.response_spectrum import RS_PARAMS

        assert "modal_case" in RS_PARAMS
        assert "modal_case" in PUSHOVER_PARAMS
        assert RS_PARAMS["modal_case"].default == ""
        assert PUSHOVER_PARAMS["modal_case"].default == ""

    def test_response_spectrum_records_forces_by_default(self):
        """RS force recording is opt-out (default ON), unlike pushover's."""

        from fea_toolkit.workflow.verbs.response_spectrum import RS_PARAMS

        assert "record_frame_forces" in RS_PARAMS
        assert "record_shell_forces" in RS_PARAMS
        assert RS_PARAMS["record_frame_forces"].default is True
        assert RS_PARAMS["record_shell_forces"].default is True

    def test_resolve_modal_result_returns_the_sole_result(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_modal_result

        result = object()
        context = SimpleNamespace(results={"modal": {"MODAL": result}})
        assert resolve_modal_result(context) is result
        assert resolve_modal_result(context, "MODAL") is result

    def test_resolve_modal_result_selects_by_name(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_modal_result

        first, second = object(), object()
        context = SimpleNamespace(results={"modal": {"A": first, "B": second}})
        assert resolve_modal_result(context, "B") is second

    def test_resolve_modal_result_requires_a_choice_when_ambiguous(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_modal_result

        context = SimpleNamespace(results={"modal": {"A": object(), "B": object()}})
        with pytest.raises(ValueError, match="multiple modal results"):
            resolve_modal_result(context)

    def test_resolve_modal_result_raises_without_a_modal_result(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_modal_result

        context = SimpleNamespace(results={})
        with pytest.raises(ValueError, match="no modal result"):
            resolve_modal_result(context)

    def test_resolve_modal_result_accepts_a_bare_result(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_modal_result

        result = object()
        context = SimpleNamespace(results={"modal": result})
        assert resolve_modal_result(context) is result

    def test_resolve_modal_result_rejects_a_name_against_a_bare_result(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_modal_result

        context = SimpleNamespace(results={"modal": object()})
        with pytest.raises(ValueError, match="cannot verify"):
            resolve_modal_result(context, "MODAL")

    def test_run_modal_rejects_a_duplicate_name(self, monkeypatch):
        import fea_toolkit.analysis.modal as analysis_modal
        from fea_toolkit.workflow.steps import StepContext
        from fea_toolkit.workflow.verbs.modal import run_modal

        context = StepContext(model_data=_slab_model(), model=object())
        context.results["modal"] = {"dup": object()}
        monkeypatch.setattr(analysis_modal, "run_modal_analysis", lambda *a, **k: object())
        with pytest.raises(ValueError, match="already exists"):
            run_modal(context, Step("modal", params={"name": "dup"}))

    def test_resolve_default_modal_case_prefers_the_spectrum_reference(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_default_modal_case

        md = SimpleNamespace(
            load_cases={
                "MODAL": self._load_case(
                    "MODAL", "LinModal", case_data={"CASE - MODAL 1 - GENERAL": {}}
                ),
                "SPECX": self._load_case(
                    "SPECX",
                    "LinRespSpec",
                    modal_case="MODAL",
                    case_data={"CASE - RESPONSE SPECTRUM": {}},
                ),
            }
        )
        assert resolve_default_modal_case(md) == "MODAL"

    def test_resolve_default_modal_case_falls_back_to_the_first_modal(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_default_modal_case

        md = SimpleNamespace(
            load_cases={
                "MODAL1": self._load_case("MODAL1", "LinModal"),
                "MODAL2": self._load_case("MODAL2", "LinModal"),
            }
        )
        assert resolve_default_modal_case(md) == "MODAL1"

    def test_resolve_default_modal_case_returns_empty_without_a_modal(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs.modal import resolve_default_modal_case

        assert resolve_default_modal_case(SimpleNamespace(load_cases={})) == ""
        assert resolve_default_modal_case(SimpleNamespace()) == ""


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

    def test_run_state_round_trips_through_the_dict(self):
        """A step's enabled/status survive the editable serialisation."""
        from dataclasses import replace

        recipe = Recipe(steps=[Step("mesh")])
        recipe.steps[0] = replace(recipe.steps[0], enabled=False, status="ok")
        rebuilt = Recipe.from_dict(recipe.to_dict())
        assert rebuilt.steps[0].enabled is False
        assert rebuilt.steps[0].status == "ok"
        assert rebuilt.to_dict() == recipe.to_dict()

    def test_an_untouched_step_has_no_status_key(self):
        """A pending step omits ``status`` (and a fresh recipe stays lean)."""
        recipe = Recipe(steps=[Step("mesh")])
        step_dict = recipe.to_dict()["steps"][0]
        assert "status" not in step_dict
        assert step_dict["enabled"] is True

    def test_the_python_export_strips_run_state(self):
        """The exported script describes the workflow, not its last run."""
        from dataclasses import replace

        recipe = self._recipe()
        recipe.steps[0] = replace(recipe.steps[0], enabled=False, status="ok")
        tree = ast.parse(recipe.to_python())
        literal = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(getattr(target, "id", "") == "RECIPE" for target in node.targets)
        )
        for step_dict in ast.literal_eval(literal)["steps"]:
            assert "enabled" not in step_dict
            assert "status" not in step_dict

    def test_set_enabled_cascades_to_precursors(self):
        """Checking a step enables every step it needs, transitively."""
        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("modal"),
                Step("response_spectrum"),
            ]
        )
        recipe.set_enabled(2, False)
        recipe.set_enabled(1, False)
        recipe.set_enabled(0, False)
        recipe.set_enabled(2, True)
        assert [step.enabled for step in recipe.steps] == [True, True, True]

    def test_set_enabled_unchecking_does_not_cascade(self):
        """Disabling a step never touches its precursors."""
        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("modal"),
                Step("response_spectrum"),
            ]
        )
        recipe.set_enabled(2, False)
        assert recipe.steps[0].enabled is True
        assert recipe.steps[1].enabled is True

    def test_set_enabled_rejects_an_out_of_range_index(self):
        with pytest.raises(IndexError, match="out of range"):
            Recipe().set_enabled(0, True)

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


class TestSaveModelVerb:
    """The ``save_model`` verb persists the prepared topology to a file."""

    def test_saves_the_prepared_topology_as_a_stage_file(self, tmp_path):
        from fea_toolkit.io.stage_reader import read_model_stages

        target = tmp_path / "saved.npz"
        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("save_model", params={"path": str(target), "fmt": "npz"}),
            ]
        )
        run = run_recipe(recipe, _slab_model())

        assert target.exists()
        assert read_model_stages(str(target), "mesh") == run.results[0].payload
        assert run.results[1].payload.title == "Saved model"

    def test_saves_json_via_the_codec(self, tmp_path):
        from fea_toolkit.io.model_codec import json_to_model

        target = tmp_path / "saved.json"
        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("save_model", params={"path": str(target), "fmt": "json"}),
            ]
        )
        run = run_recipe(recipe, _slab_model())

        assert target.exists()
        assert json_to_model(target.read_text(encoding="utf-8")) == run.results[0].payload

    def test_requires_a_path(self):
        recipe = Recipe(steps=[Step("mesh"), Step("save_model")])
        with pytest.raises(StepError):
            run_recipe(recipe, _slab_model())

    def test_needs_a_prepared_topology(self, tmp_path):
        recipe = Recipe(steps=[Step("save_model", params={"path": str(tmp_path / "out.h5")})])
        with pytest.raises(StepError):
            run_recipe(recipe, _slab_model())


class TestExportTclCheckbox:
    """Analysis verbs carry an ``export_tcl`` QC checkbox instead of a step."""

    def test_every_analysis_verb_declares_the_checkbox(self):
        for verb in ("run_static", "modal", "response_spectrum", "pushover"):
            spec = STEP_SPECS[verb]
            assert "export_tcl" in spec.params, verb
            assert "export_tcl_path" in spec.params, verb
            assert spec.params["export_tcl"].default is False, verb

    def test_default_tcl_path_derives_from_the_source(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs._tcl import default_tcl_path

        ctx = SimpleNamespace(model_data=SimpleNamespace(source="/data/Admin_0.7E.s2k"))
        assert default_tcl_path(ctx, "modal") == "/data/Admin_0.7E_model.tcl"

    def test_default_tcl_path_falls_back_to_the_verb_name(self):
        from types import SimpleNamespace

        from fea_toolkit.workflow.verbs._tcl import default_tcl_path

        ctx = SimpleNamespace(model_data=SimpleNamespace())  # no source
        assert default_tcl_path(ctx, "modal") == "modal_model.tcl"

    def test_checkbox_writes_the_model_tcl(self, tmp_path):
        from examples.sample_model import make_sample_model

        target = tmp_path / "run_static_model.tcl"
        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step(
                    "run_static",
                    params={
                        "cases": {"DEAD": {"DEAD": 1.0}},
                        "export_tcl": True,
                        "export_tcl_path": str(target),
                    },
                ),
            ]
        )
        run_recipe(recipe, make_sample_model())

        assert target.exists()
        text = target.read_text(encoding="utf-8")
        assert "node" in text and "fix" in text  # a domain script, not an empty file


class TestFixShellOnlyBase:
    """The ``fix_shell_only_base`` verb upgrades shell-only base restraints."""

    def _model(self):
        from fea_toolkit.model.sap_data import (
            AreaElement,
            FrameElement,
            Node,
            Restraint,
            SAPModelData,
        )

        return SAPModelData(
            nodes={
                "1": Node("1", 1, 0.0, 0.0, 0.0),  # shell-only base, pinned
                "2": Node("2", 2, 4.0, 0.0, 0.0),  # frame-connected base, pinned
                "3": Node("3", 3, 0.0, 0.0, 3.0),  # top
            },
            restraints={
                "1": Restraint([1, 1, 1, 0, 0, 0]),
                "2": Restraint([1, 1, 1, 0, 0, 0]),
            },
            materials={},
            sections={},
            frame_elements={"F1": FrameElement("F1", 11, "2", "3")},
            area_elements={"A1": AreaElement("A1", 21, ["1", "3"])},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )

    def test_upgrades_only_shell_only_base_nodes_and_copies(self):
        from fea_toolkit.workflow.steps import StepContext

        md = self._model()
        context = StepContext(model_data=md, model=None, results={})
        results = STEP_SPECS["fix_shell_only_base"].run(context, Step("fix_shell_only_base"))

        assert results[0].label == "Shell-only base fixed"
        assert context.model_data.restraints["1"].dofs == [1, 1, 1, 1, 1, 1]
        assert context.model_data.restraints["2"].dofs == [1, 1, 1, 0, 0, 0]  # frame base untouched
        # The caller's model is left untouched (copy-on-write).
        assert md.restraints["1"].dofs == [1, 1, 1, 0, 0, 0]


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

    def test_the_scaled_section_is_replaced_and_untouched_objects_are_shared(self):
        """Copy-on-write: the scaled section is a new object, untouched ones are shared."""
        model = _slab_model()
        model.sections["OTHER"] = ShellSection(
            name="OTHER",
            shape="Shell",
            material="C30",
            thickness=0.2,
            A=5.0,
            I33=2.0,
            I22=1.0,
            J=0.5,
        )
        source_section = model.sections["SLAB"]
        source_node = model.nodes["1"]
        source_area = model.area_elements["A1"]

        scaled = (
            run_recipe(Recipe(steps=[self._step(Selection(sections=["SLAB"]))]), model)
            .results[0]
            .payload
        )

        # The containers are new, but every untouched object is shared.
        assert scaled.sections is not model.sections
        assert scaled.nodes is not model.nodes
        assert scaled.sections["OTHER"] is model.sections["OTHER"]  # unselected → shared
        assert scaled.nodes["1"] is source_node
        assert scaled.area_elements["A1"] is source_area

        # The scaled section is a replacement, never a mutation of the caller's object.
        assert scaled.sections["SLAB"] is not source_section
        assert source_section.A == 5.0
        assert pytest.approx(0.05) == scaled.sections["SLAB"].A

    def test_a_selection_that_matches_nothing_leaves_every_section_alone(self):
        run = run_recipe(Recipe(steps=[self._step(Selection(sections=["NOPE"]))]), _slab_model())
        assert run.results[0].payload.sections["SLAB"].A == 5.0

    def test_a_factor_of_one_reports_that_nothing_changed(self):
        messages: list = []
        run_recipe(Recipe(steps=[self._step(factor=1.0)]), _slab_model(), log=messages.append)
        assert any("no property changed" in message for message in messages)

    def test_progress_routes_through_pipeline_log_when_log_is_absent(self):
        """``run_recipe(..., pipeline_log=...)`` merges ``context.log`` into it."""
        from fea_toolkit.io.log import PipelineLog

        log = PipelineLog(verbose=False)
        run_recipe(Recipe(steps=[self._step(factor=1.0)]), _slab_model(), pipeline_log=log)
        assert any(e["step"] == "workflow" for e in log.entries)
        assert any("no property changed" in e["msg"] for e in log.entries)

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


class TestCapacityAndMeshCheckVerbs:
    """The capacity and mesh-quality checks (P30 phase C, increment 2).

    The capacity checks wrap :mod:`fea_toolkit.capacity` and stay demand-free —
    they report a section's *capacity*, never a demand/capacity ratio.
    ``mesh_checks`` is the one check that needs a meshed topology.
    """

    def test_hinge_length_reports_one_row_per_member(self):
        from examples.sample_model import make_rc_frame_model

        model = make_rc_frame_model()
        run = run_recipe(Recipe(steps=[Step("hinge_length")]), model)
        table = run.results[0].payload
        assert table.columns == ("element", "section", "length", "Lp")
        assert {row[0] for row in table.rows} == set(model.frame_elements)

    def test_member_shear_capacity_reports_the_nominal_capacities(self):
        from examples.sample_model import make_rc_frame_model

        model = make_rc_frame_model()
        step = Step("member_shear_capacity", Selection(sections=["COL"]))
        run = run_recipe(Recipe(steps=[step]), model)
        table = run.results[0].payload
        assert table.columns == ("section", "Vc", "Vs", "Vn", "Vn_upper", "d", "dv", "bw")
        assert [row[0] for row in table.rows] == ["COL"]

    def test_wall_shear_check_reports_stress_against_the_code_limits(self):
        model = _slab_model()
        model.materials["C30"].Fc = 30.0e6  # Pa — the model is in N/m units
        run = run_recipe(
            Recipe(steps=[Step("wall_shear_check", params={"Nxy": 5000.0, "Ny": 10000.0})]),
            model,
        )
        table = run.results[0].payload
        assert table.columns == (
            "area",
            "tau",
            "tau_limit",
            "sigma",
            "sigma_limit",
            "ok_shear",
            "ok_normal",
        )
        assert [row[0] for row in table.rows] == ["A1"]

    def test_mesh_checks_needs_a_meshed_model(self):
        with pytest.raises(StepError) as info:
            run_recipe(Recipe(steps=[Step("mesh_checks")]), _slab_model())
        assert "needs 'geometry'" in str(info.value.cause)

    def test_mesh_checks_reports_the_summary_after_a_mesh_step(self):
        run = run_recipe(Recipe(steps=[Step("mesh"), Step("mesh_checks")]), _slab_model())
        table = run.results[-1].payload
        assert table.title == "Mesh quality"
        assert table.columns == ("check", "value")
        assert dict(table.rows)["passed"] in ("True", "False")


class TestFailuresAndCancellation:
    """A step that fails is either fatal or tolerated — never silent."""

    def test_a_non_optional_failure_raises_carrying_the_step_index(self):
        recipe = Recipe(steps=[Step("run_static")])  # no mesh step preceded it
        with pytest.raises(StepError) as info:
            run_recipe(recipe, _slab_model())
        assert info.value.index == 0
        assert info.value.verb == "run_static"
        assert "needs 'geometry'" in str(info.value.cause)

    def test_a_missing_precursor_is_rejected_by_name(self):
        recipe = Recipe(steps=[Step("mesh"), Step("response_spectrum")])  # no modal step
        with pytest.raises(StepError) as info:
            run_recipe(recipe, _slab_model())
        assert info.value.index == 1
        assert info.value.verb == "response_spectrum"
        assert "needs 'modal'" in str(info.value.cause)

    def test_a_non_optional_failure_carries_the_partial_run_state(self):
        recipe = Recipe(steps=[Step("mesh"), Step("response_spectrum")])
        with pytest.raises(StepError) as info:
            run_recipe(recipe, _slab_model())
        assert info.value.step_status == [
            (0, "mesh", "ok"),
            (1, "response_spectrum", "failed"),
        ]

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

    def test_a_disabled_step_is_skipped_without_needs_validation(self):
        """A disabled step is skipped — it neither runs nor checks its needs."""
        from dataclasses import replace

        recipe = Recipe(steps=[Step("run_static")])  # would need 'geometry'
        recipe.steps[0] = replace(recipe.steps[0], enabled=False)
        run = run_recipe(recipe, _slab_model())
        assert run.results == []
        assert run.failures == []
        assert run.step_status == [(0, "run_static", "skipped")]

    def test_a_disabled_precursor_fails_the_enabled_step_that_needs_it(self):
        """Skipping a step its successor needs surfaces as the successor's failure."""
        from dataclasses import replace

        recipe = Recipe(steps=[Step("mesh"), Step("run_static")])
        recipe.steps[0] = replace(recipe.steps[0], enabled=False)
        with pytest.raises(StepError) as info:
            run_recipe(recipe, _slab_model())
        assert info.value.index == 1
        assert "needs 'geometry'" in str(info.value.cause)

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
        assert "needs 'cases'" in str(info.value.cause)

    def test_a_re_mesh_invalidates_earlier_case_results(self):
        """A second mesh replaces the topology, so earlier solved cases are stale
        and a later combine fails its ``needs`` check instead of reducing them."""
        from examples.sample_model import make_sample_model

        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("run_static", params={"cases": {"DEAD": {"DEAD": 1.0}}}),
                Step("mesh"),
                Step("combine"),
            ]
        )
        with pytest.raises(StepError) as info:
            run_recipe(recipe, make_sample_model())
        assert info.value.verb == "combine"
        assert "needs 'cases'" in str(info.value.cause)

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
        assert "has no solved case" in str(info.value.cause)

    def test_a_chart_step_plots_model_response_spectra_without_solving(self):
        """'response_spectra' reads the model's parsed functions, no solve needed."""
        from matplotlib.figure import Figure

        from fea_toolkit.model.sap_data import ResponseSpectrumFunction

        md = _slab_model()
        md.response_spectrum_functions = {
            "UNIFRS": ResponseSpectrumFunction("UNIFRS", [0.0, 1.0], [1.0, 1.0])
        }
        run = run_recipe(Recipe(steps=[Step("chart", params={"chart": "response_spectra"})]), md)

        assert [result.kind for result in run.results] == ["figure"]
        figure = run.results[-1].payload
        assert isinstance(figure, Figure)

    def test_a_chart_step_renders_the_storey_force_profile_from_solved_cases(self):
        """A 'storey_forces' chart turns a solved lateral case into a figure."""
        from matplotlib.figure import Figure

        from examples.sample_model import make_sample_model

        recipe = Recipe(
            steps=[
                Step("mesh"),
                Step("run_static", params={"cases": {"WIND": {"WIND": 1.0}}}),
                Step("chart", params={"chart": "storey_forces"}),
            ]
        )
        run = run_recipe(recipe, make_sample_model())

        assert [result.kind for result in run.results] == ["geometry", "cases", "figure"]
        figure = run.results[-1].payload
        assert isinstance(figure, Figure)

    @pytest.mark.parametrize("kind", ["modal", "pushover", "csm"])
    def test_a_chart_step_analysis_kinds_require_their_preceding_verb(self, kind):
        """The analysis-result charts read a preceding verb's result, never re-solve."""
        with pytest.raises(StepError) as info:
            run_recipe(Recipe(steps=[Step("chart", params={"chart": kind})]), _slab_model())
        assert "preceding" in str(info.value.cause)

    def test_load_verification_reports_applied_vs_reactions(self):
        """A mesh + load_verification compares each pattern against its reactions."""
        from examples.sample_model import make_sample_model

        recipe = Recipe(steps=[Step("mesh"), Step("load_verification")])
        run = run_recipe(recipe, make_sample_model())

        assert [result.kind for result in run.results] == ["geometry", "table"]
        table = run.results[-1].payload
        assert table.title == "Load verification"
        # One row per load pattern, pairing Applied / Reaction with a Δ column.
        columns = table.columns
        assert columns[0] == "Load Pattern"
        assert any(c.startswith("Applied Fx") for c in columns)
        assert any(c.startswith("Reaction Fx") for c in columns)
        assert any(c.startswith("ΔFx") for c in columns)
        assert len(table.rows) >= 1
        assert all(isinstance(cell, str) for row in table.rows for cell in row)

    def test_load_verification_without_load_patterns_returns_nothing(self):
        """A model with no load patterns has nothing to verify — no table, no solve."""
        model = _slab_model()  # a bare slab: no load patterns
        run = run_recipe(Recipe(steps=[Step("mesh"), Step("load_verification")]), model)
        assert [result.kind for result in run.results] == ["geometry"]
