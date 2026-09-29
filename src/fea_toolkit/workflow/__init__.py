"""Workflow authoring — recipes of steps applied to model selections.

The toolkit's analysis primitives (``Preprocessor``, ``run_case_set``,
``build_combination_results``) are composed *explicitly by the caller*.  A script
does that with Python control flow — which is powerful, but is code: it cannot
be inspected as a workflow, edited in a GUI, or diffed as a document.

This package is the declarative counterpart.  The unit is a **step** —
a verb, a :class:`~fea_toolkit.model.selection.Selection`, and parameters — and a
**recipe** is an ordered list of steps::

    from fea_toolkit.workflow import Recipe, run_recipe

    recipe = Recipe(name="Masonry building")
    recipe.add("scale_sections", Selection(sections=["brick wall"]), {"factor": 0.01})
    recipe.add("mesh", Selection(sections=["brick wall"]),
               {"split_slabs_at_walls": True})
    recipe.add("run_static", params={"cases": {"Self weight": {}}})
    recipe.add("combine", params={"envelope_mode": "maxmin"})

    run = run_recipe(recipe, md, log=print)

The recipe is pure data (:meth:`Recipe.to_dict` / :meth:`Recipe.to_json`), so it
can be saved, reviewed and replayed — and :meth:`Recipe.to_python` hands the same
workflow back as a script.

**Where the pieces live.**  A verb's implementation sits in
:mod:`fea_toolkit.workflow.verbs`, one module per verb, alongside the parameters
that verb declares.  :mod:`fea_toolkit.workflow.registry` is the manifest that
assembles them into ``STEP_SPECS`` — the single thing a caller reads to know the
vocabulary.  Nothing here imports OpenSees at module level, so the manifest (and
therefore a GUI's parameter forms) can be read without it.

**Not in the package.**  No analysis logic and no topology mutation of its own:
every verb delegates to the existing two-stage pipeline.  A recipe is a
*description* of work, never a second implementation of it.
"""

from .recipe import Recipe, RecipeRun, run_recipe
from .registry import STEP_SPECS, list_verbs
from .steps import (
    CASES,
    GEOMETRY,
    MODEL,
    ParamSpec,
    Step,
    StepContext,
    StepError,
    StepResult,
    StepSpec,
    validate_params,
)

__all__ = [
    "CASES",
    "GEOMETRY",
    "MODEL",
    "STEP_SPECS",
    "ParamSpec",
    "Recipe",
    "RecipeRun",
    "Step",
    "StepContext",
    "StepError",
    "StepResult",
    "StepSpec",
    "list_verbs",
    "run_recipe",
    "validate_params",
]
