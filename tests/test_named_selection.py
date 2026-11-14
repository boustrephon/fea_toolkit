"""Named selections, the Selection.selections field and the document verbs (P35)."""

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
