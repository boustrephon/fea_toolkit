"""The command-palette grammar, precursor scan and script import (P31)."""

import pytest

from fea_toolkit.workflow import (
    STEP_SPECS,
    Recipe,
    compile_command,
    missing_needs,
    satisfied_kinds,
    step_to_command,
)


def _assert_round_trip(command: str) -> None:
    step = compile_command(command)
    again = compile_command(step_to_command(step))
    assert again.verb == step.verb
    assert again.params == step.params
    if step.selection is None:
        assert again.selection is None
    else:
        assert again.selection.to_string() == step.selection.to_string()


def test_compile_verb_only():
    step = compile_command("mesh")
    assert step.verb == "mesh"
    assert step.selection is None
    assert step.params == {}


def test_compile_disambiguates_parameters_from_selection():
    step = compile_command('mesh section="brick wall" create_shells=True')
    assert step.verb == "mesh"
    assert step.params["create_shells"] is True
    assert step.selection is not None
    assert step.selection.to_string() == "section=brick wall"


def test_compile_negated_clause_is_a_selection():
    step = compile_command("check_brace_buckling NOT section=COL K=1.0")
    assert step.params["K"] == 1.0
    assert "NOT section=COL" in step.selection.to_string()


def test_compile_dict_and_list_values():
    assert compile_command('run_static cases={"DEAD": {"DEAD": 1.0}}').params["cases"] == {
        "DEAD": {"DEAD": 1.0}
    }
    assert compile_command("ida intensities=[0.5, 1.0, 2.0]").params["intensities"] == [
        0.5,
        1.0,
        2.0,
    ]


def test_step_to_command_round_trips_nested_string_keys():
    """A structured parameter's nested string keys must survive the round-trip.

    ``repr`` emits single-quoted dicts, whose inner spaces (``Wind direction=Y``)
    would split the clause when re-scanned.  The serializer must double-quote
    nested strings so recompilation succeeds.
    """
    step = compile_command('run_static cases={"Wind": {"Wind direction=Y": 1.0}}')
    again = compile_command(step_to_command(step))
    assert again.params["cases"] == {"Wind": {"Wind direction=Y": 1.0}}


def test_step_to_command_round_trips_a_non_bmp_nested_key():
    """A non-BMP character in a nested cases key keeps its original value.

    ``json.dumps`` with the default ``ensure_ascii=True`` escapes a non-BMP
    character to a surrogate pair, which ``ast.literal_eval`` re-reads as a
    *different* string (two lone surrogates) rather than the original emoji.
    """
    step = compile_command('run_static cases={"Wind \U0001f300": {"Wind": 1.0}}')
    again = compile_command(step_to_command(step))
    assert again.params["cases"] == {"Wind \U0001f300": {"Wind": 1.0}}


def test_compile_bare_word_parameter_is_a_string():
    assert compile_command("chart chart=storey_displacements").params["chart"] == (
        "storey_displacements"
    )


def test_compile_unknown_verb_raises():
    with pytest.raises(ValueError, match="unknown verb"):
        compile_command("bogus_verb")


def test_compile_empty_raises():
    with pytest.raises(ValueError, match="empty command"):
        compile_command("   ")


def test_compile_bad_parameter_value_raises():
    with pytest.raises(ValueError):
        compile_command("modal n_modes=notanint")


def test_step_to_command_round_trips():
    for command in (
        "mesh",
        'mesh section="brick wall" create_shells=True',
        'run_static cases={"DEAD": {"DEAD": 1.0}}',
        "ida intensities=[0.5, 1.0, 2.0] collapse_drift=0.08 direction=Y",
        "chart chart=storey_displacements",
    ):
        _assert_round_trip(command)


def test_parameter_and_selection_keys_are_disjoint():
    """A parameter key must never shadow a Selection key (the palette's rule)."""
    from fea_toolkit.model.selection import SELECT_KEYS

    select_keys = {key.lower() for key in SELECT_KEYS}
    for verb, spec in STEP_SPECS.items():
        clash = [name for name in spec.params if name.lower() in select_keys]
        assert not clash, f"{verb}: parameter names collide with selection keys: {clash}"


def test_satisfied_kinds_accumulates_in_order():
    recipe = Recipe()
    recipe.add("mesh")
    recipe.add("modal")
    recipe.add("run_static")
    assert satisfied_kinds(recipe) == {"geometry", "modal", "cases"}


def test_satisfied_kinds_a_geometry_step_invalidates_derived_results():
    recipe = Recipe()
    recipe.add("mesh")
    recipe.add("modal")
    recipe.add("mesh")  # re-mesh invalidates the modal result
    assert satisfied_kinds(recipe) == {"geometry"}


def test_missing_needs_reports_the_unsatisfied_kind():
    assert missing_needs("run_static", set()) == ["geometry"]
    assert missing_needs("run_static", {"geometry"}) == []
    assert missing_needs("pushover", {"geometry"}) == ["modal"]
    assert missing_needs("bogus_verb", {"geometry"}) == []


def test_from_python_reimports_to_python_output():
    recipe = Recipe(name="demo")
    recipe.add("mesh", params={"split_elements": True})
    recipe.add("run_static")
    again = Recipe.from_python(recipe.to_python())
    assert again.name == recipe.name
    assert [step.verb for step in again] == [step.verb for step in recipe]
    assert again.steps[0].params == {"split_elements": True}


def test_from_python_rejects_code_without_a_recipe_literal():
    with pytest.raises(ValueError, match="no RECIPE"):
        Recipe.from_python("print('hello')")


def test_from_python_rejects_non_literal_values():
    with pytest.raises(ValueError):
        Recipe.from_python("RECIPE = __import__('os').system('id')")


def test_from_python_rejects_unhashable_literal_key():
    with pytest.raises(ValueError):
        Recipe.from_python("RECIPE = {[1]: 2}")
