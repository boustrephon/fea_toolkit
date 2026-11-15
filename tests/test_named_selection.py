"""Named selections, the Selection.selections field and the document verbs (P35)."""

import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.model.named_selection import NamedSelection, resolve_named_selections
from fea_toolkit.model.selection import Selection

# ── Selection.selections reference field ─────────────────────────────


def test_selection_selections_field_round_trips():
    sel = Selection.from_string("selection=core")
    assert sel.selections == ["core"]
    assert sel.to_string() == "selection=core"
    assert Selection.from_string(sel.to_string()).selections == ["core"]


def test_selection_selections_composes_with_other_criteria():
    sel = Selection.from_string("type=Frame selection=core")
    assert sel.element_types == ["Frame"]
    assert sel.selections == ["core"]


def test_selection_exclude_selections_round_trips():
    sel = Selection.from_string("NOT selection=core")
    assert sel.exclude_selections == ["core"]
    assert Selection.from_string(sel.to_string()).exclude_selections == ["core"]


# ── NamedSelection storage ───────────────────────────────────────────


def test_named_selection_round_trips():
    ns = NamedSelection(
        name="core",
        selection=Selection.from_string("section=COL"),
        element_ids=["1"],
        node_ids=["2"],
    )
    again = NamedSelection.from_dict(ns.to_dict())
    assert again.name == "core"
    assert again.selection.to_string() == "section=COL"
    assert again.element_ids == ["1"]
    assert again.node_ids == ["2"]


def test_resolve_named_selections_expands_references():
    model = make_sample_model()
    store = {
        "core": NamedSelection(
            name="core", selection=Selection(sections=["COL"]), element_ids=["101"]
        ),
    }
    resolved = resolve_named_selections(
        Selection(selections=["core"], sections=["BEAM"]), store, model
    )
    assert resolved.selections is None
    assert "101" in resolved.element_ids
    assert resolved.sections == ["BEAM"]


def test_resolve_named_selections_ignores_unknown_names():
    resolved = resolve_named_selections(
        Selection(selections=["missing"], sections=["BEAM"]), {}, make_sample_model()
    )
    assert resolved.selections is None
    assert resolved.sections == ["BEAM"]


def test_reference_only_unknown_name_resolves_to_no_match_not_unrestricted():
    """A reference-only selection whose name is unknown must not mean 'everything'.

    The established contract ignores unknown reference names, but an unresolved
    *positive* reference must still resolve to an empty scope — never an
    unrestricted (match-everything) selection that a consumer like
    ``scale_sections`` would read as "every section".
    """
    model = make_sample_model()
    resolved = resolve_named_selections(Selection(selections=["missing"]), {}, model)
    assert resolved.selections is None
    assert resolved.get_frame_ids(model) == []
    assert resolved.get_area_ids(model) == []
    assert resolved.get_node_ids(model) == []


def test_resolve_named_selections_expands_exclude_references():
    model = make_sample_model()
    store = {
        "core": NamedSelection(name="core", selection=Selection(sections=["UB300"])),
    }
    resolved = resolve_named_selections(
        Selection(exclude_selections=["core"], element_types=["Frame"]), store, model
    )
    assert resolved.exclude_selections is None
    assert resolved.exclude_element_ids == ["Frame:1"]  # the model's single UB300 frame
    assert resolved.element_types == ["Frame"]


def test_named_selection_round_trips_an_empty_criterion_list():
    """An empty criterion list stays distinct from None (it means "no match").

    ``Selection.to_string()`` omits empty lists, so serialising through it would
    turn ``element_types=[]`` (match nothing) into ``element_types=None`` (match
    everything).  The field-payload form must keep the two apart.
    """
    ns = NamedSelection(
        name="core",
        selection=Selection(element_types=[], sections=["COL"]),
    )
    again = NamedSelection.from_dict(ns.to_dict())
    assert again.selection.element_types == []
    assert again.selection.sections == ["COL"]
    # The empty ``element_types`` criterion still means "no match" after reload.
    assert again.selection.get_frame_ids(make_sample_model()) == []
    assert again.selection.get_area_ids(make_sample_model()) == []


def test_a_node_only_selection_stays_valid_with_an_element_only_exclusion():
    """A node-only selection whose excluded selection has only element ids stays valid.

    ``exclude_element_ids`` names frames/areas only, so merging element ids into a
    node-scoped selection would violate its validation invariant (element ids
    without node ids).  The resolver drops the element-only exclusion and leaves
    the node selection unchanged.
    """
    model = make_sample_model()
    store = {
        "core": NamedSelection(name="core", selection=Selection(sections=["UB300"])),
    }
    resolved = resolve_named_selections(
        Selection(element_types=["Node"], exclude_selections=["core"]), store, model
    )
    assert resolved.exclude_selections is None
    assert resolved.element_types == ["Node"]
    assert resolved.exclude_element_ids is None
    assert resolved.exclude_node_ids is None


def test_a_story_scoped_named_selection_resolves_against_storey_elevations(monkeypatch):
    """A named selection carrying a ``story`` criterion keeps its story scope."""
    import fea_toolkit.model.stories as stories_mod

    model = make_sample_model()
    # The sample model's single frame spans z=0..10 (mid-height 5.0); pin the
    # storey detection so a level at 5.0 matches it, then resolve a named
    # selection scoped to that story.
    monkeypatch.setattr(
        stories_mod,
        "identify_stories",
        lambda md: [type("Story", (), {"name": "Level5", "elevation": 5.0})()],
    )
    store = {
        "core": NamedSelection(name="core", selection=Selection(story=["Level5"])),
    }
    resolved = resolve_named_selections(
        Selection(selections=["core"], element_types=["Frame"]), store, model
    )
    assert resolved.selections is None
    assert "Frame:1" in resolved.element_ids  # the story-matched frame, type-qualified


def test_a_type_qualified_pick_resolves_only_that_type():
    """A picked frame '1' resolves the frame, not a same-id area."""
    from fea_toolkit.model.sap_data import AreaElement

    model = make_sample_model()
    # An area that shares the frame's label "1" (the collision finding 6 guards).
    model.area_elements["1"] = AreaElement("1", 1, ["1", "2"])
    model.area_assignments["1"] = "UB300"

    store = {"core": NamedSelection(name="core", element_ids=["Frame:1"])}
    resolved = resolve_named_selections(
        Selection(selections=["core"], element_types=["Frame", "Area"]), store, model
    )
    assert resolved.element_ids == ["Frame:1"]
    assert resolved.get_frame_ids(model) == ["1"]
    assert resolved.get_area_ids(model) == []


def test_an_empty_expression_does_not_broaden_explicit_picks():
    """A named selection with only explicit picks and an empty criterion resolves
    to just the picks — the empty expression must not broaden to the whole model."""
    from fea_toolkit.model.sap_data import FrameElement

    model = make_sample_model()
    model.frame_elements["2"] = FrameElement("2", 2, "1", "2")

    # The ``select`` verb stores an empty Selection with explicit picks.
    store = {"core": NamedSelection(name="core", selection=Selection(), element_ids=["1"])}
    resolved = resolve_named_selections(Selection(selections=["core"]), store, model)
    assert resolved.element_ids == ["1"]
    # The empty expression must not have pulled in frame "2" (or the whole model).
    assert resolved.get_frame_ids(model) == ["1"]


def test_a_recipe_step_referencing_a_named_selection_expands_it():
    """A ``scale_sections`` step expands its ``selection=core`` reference at run time."""
    from fea_toolkit.workflow import Recipe, Step, run_recipe

    model = make_sample_model()
    original_A = model.sections["UB300"].A
    recipe = Recipe(
        steps=[
            Step("select", params={"name": "core", "expression": "section=UB300"}),
            Step(
                "scale_sections",
                selection=Selection.from_string("selection=core"),
                params={"factor": 0.5},
            ),
        ]
    )
    run = run_recipe(recipe, model)
    assert [result.kind for result in run.results] == ["table", "model"]
    scaled = run.results[-1].payload
    assert pytest.approx(original_A * 0.5) == scaled.sections["UB300"].A


def test_next_version_path_increments(tmp_path):
    from fea_toolkit.io.model_loader import next_version_path

    base = str(tmp_path / "model.s2k")
    assert next_version_path(base).endswith("model_v1.s2k")
    (tmp_path / "model_v1.s2k").touch()
    assert next_version_path(base).endswith("model_v2.s2k")


# ── Document verbs ───────────────────────────────────────────────────


def _context():
    from fea_toolkit.workflow.steps import StepContext

    return StepContext(model_data=make_sample_model())


def test_select_stores_a_named_selection():
    from fea_toolkit.workflow import STEP_SPECS, Step

    context = _context()
    results = STEP_SPECS["select"].run(
        context, Step("select", params={"name": "core", "expression": "type=Frame"})
    )
    assert "core" in context.named_selections
    assert results[0].kind == "table"


def test_save_and_open_round_trip(tmp_path):
    from fea_toolkit.workflow import STEP_SPECS, Step

    context = _context()
    path = str(tmp_path / "doc.json")
    STEP_SPECS["save"].run(context, Step("save", params={"path": path, "versioned": True}))
    assert context.document_path.endswith("_v1.json")

    STEP_SPECS["open"].run(context, Step("open", params={"path": context.document_path}))
    assert context.model_data is not None
    assert context.document_path.endswith("_v1.json")


def test_close_clears_the_document():
    from fea_toolkit.workflow import STEP_SPECS, Step

    context = _context()
    context.document_path = "x.json"
    context.model = object()
    STEP_SPECS["close"].run(context, Step("close"))
    assert context.model is None
    assert context.document_path == ""
    assert context.document_dirty is False


def test_close_with_save_writes_the_document(tmp_path):
    from fea_toolkit.workflow import STEP_SPECS, Step

    context = _context()
    context.document_path = str(tmp_path / "doc.json")
    STEP_SPECS["close"].run(context, Step("close", params={"save": True}))
    assert (tmp_path / "doc.json").exists()


def test_a_document_recipe_opens_then_selects(tmp_path):
    from fea_toolkit.io.model_codec import model_to_json
    from fea_toolkit.workflow import Recipe, run_recipe

    path = tmp_path / "doc.json"
    path.write_text(model_to_json(make_sample_model()), encoding="utf-8")
    recipe = Recipe(name="doc")
    recipe.add("open", params={"path": str(path)})
    recipe.add("select", params={"name": "core", "expression": "type=Frame"})
    run = run_recipe(recipe, make_sample_model())
    assert [result.kind for result in run.results] == ["session", "table"]
