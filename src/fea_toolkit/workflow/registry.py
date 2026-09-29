"""The verb manifest — every verb the workflow layer offers, as pure data.

This module is the **single manifest** of what a recipe can do.  A caller that
wants to know the vocabulary — the GUI's recipe panel building its "add step"
menu and parameter forms, a validator, the CLI's help listing — imports this and
nothing else.  Because every implementation keeps its heavy imports inside the
function (see :mod:`fea_toolkit.workflow.verbs`), reading the manifest does not
load OpenSees, so a form can be rendered before a model is even open.

The registration order below is the order the verbs make sense in —
prepare properties, prepare topology, solve, reduce — and is the order a menu
should offer them.
"""

from .steps import CASES, FIGURE, GEOMETRY, MODEL, TABLE, StepSpec
from .verbs import (
    BRACE_BUCKLING_PARAMS,
    CHART_PARAMS,
    COMBINE_PARAMS,
    CONNECTIVITY_PARAMS,
    MESH_PARAMS,
    RUN_STATIC_PARAMS,
    SCALE_SECTIONS_PARAMS,
    SELF_WEIGHT_PARAMS,
    run_chart,
    run_check_brace_buckling,
    run_check_connectivity,
    run_check_self_weight,
    run_combine,
    run_mesh,
    run_scale_sections,
    run_static,
)

__all__ = ["STEP_SPECS", "list_verbs"]

#: Every verb, keyed by name.  Each entry pairs an implementation with the
#: parameters that implementation is allowed to read — a verb's parameter
#: surface is exactly what is declared here, so a recipe cannot reach an
#: undocumented option.
STEP_SPECS: dict[str, StepSpec] = {
    "check_connectivity": StepSpec(
        verb="check_connectivity",
        run=run_check_connectivity,
        params=CONNECTIVITY_PARAMS,
        kind=TABLE,
        help="Check for orphan nodes, duplicated coordinates and zero-area sections.",
    ),
    "check_self_weight": StepSpec(
        verb="check_self_weight",
        run=run_check_self_weight,
        params=SELF_WEIGHT_PARAMS,
        kind=TABLE,
        help="Compare the model's self-weight against the weight its geometry implies.",
    ),
    "check_brace_buckling": StepSpec(
        verb="check_brace_buckling",
        run=run_check_brace_buckling,
        params=BRACE_BUCKLING_PARAMS,
        kind=TABLE,
        help="Check the selected braces against Euler buckling.",
    ),
    "scale_sections": StepSpec(
        verb="scale_sections",
        run=run_scale_sections,
        params=SCALE_SECTIONS_PARAMS,
        kind=MODEL,
        help=(
            "Reduce a selection's section stiffness in place — the masonry "
            "'soften the wall' option, keeping its elements, loads and mass."
        ),
    ),
    "mesh": StepSpec(
        verb="mesh",
        run=run_mesh,
        params=MESH_PARAMS,
        kind=GEOMETRY,
        help="Split frames, mesh areas into shells, and hold a selection back as loads-only.",
    ),
    "run_static": StepSpec(
        verb="run_static",
        run=run_static,
        params=RUN_STATIC_PARAMS,
        kind=CASES,
        needs=("model",),
        help="Solve the model's static load cases.",
    ),
    "combine": StepSpec(
        verb="combine",
        run=run_combine,
        params=COMBINE_PARAMS,
        kind=CASES,
        needs=("cases",),
        help="Reduce the solved cases into load combinations.",
    ),
    "chart": StepSpec(
        verb="chart",
        run=run_chart,
        params=CHART_PARAMS,
        kind=FIGURE,
        needs=("cases",),
        help="Plot the solved cases' storey displacement and drift profiles.",
    ),
}


def list_verbs() -> list[str]:
    """Verb names in registration order — preparation, topology, solve, reduce.

    Returns:
        The verb names, in the order a menu should offer them.
    """
    return list(STEP_SPECS)
