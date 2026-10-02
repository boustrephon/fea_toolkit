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

from .steps import CASES, FIGURE, GEOMETRY, MODAL, MODEL, TABLE, StepSpec
from .verbs import (
    BRACE_BUCKLING_PARAMS,
    CHART_PARAMS,
    COMBINE_PARAMS,
    CONNECTIVITY_PARAMS,
    HINGE_LENGTH_PARAMS,
    MEMBER_SHEAR_CAPACITY_PARAMS,
    MESH_CHECKS_PARAMS,
    MESH_PARAMS,
    MODAL_PARAMS,
    PUSHOVER_PARAMS,
    RS_PARAMS,
    RUN_STATIC_PARAMS,
    SCALE_SECTIONS_PARAMS,
    SELF_WEIGHT_PARAMS,
    WALL_SHEAR_CHECK_PARAMS,
    run_chart,
    run_check_brace_buckling,
    run_check_connectivity,
    run_check_self_weight,
    run_combine,
    run_hinge_length,
    run_member_shear_capacity,
    run_mesh,
    run_mesh_checks,
    run_modal,
    run_pushover,
    run_response_spectrum,
    run_scale_sections,
    run_static,
    run_wall_shear_check,
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
    "mesh_checks": StepSpec(
        verb="mesh_checks",
        run=run_mesh_checks,
        params=MESH_CHECKS_PARAMS,
        kind=TABLE,
        needs=("geometry",),
        help="Report mesh quality — aspect ratios, flatness and skew.",
    ),
    "hinge_length": StepSpec(
        verb="hinge_length",
        run=run_hinge_length,
        params=HINGE_LENGTH_PARAMS,
        kind=TABLE,
        help="Plastic hinge length per ASCE 41-17 §10.8 for the selected members.",
    ),
    "member_shear_capacity": StepSpec(
        verb="member_shear_capacity",
        run=run_member_shear_capacity,
        params=MEMBER_SHEAR_CAPACITY_PARAMS,
        kind=TABLE,
        help="Nominal RC shear capacity (simplified MCFT) for the selected frame sections.",
    ),
    "wall_shear_check": StepSpec(
        verb="wall_shear_check",
        run=run_wall_shear_check,
        params=WALL_SHEAR_CHECK_PARAMS,
        kind=TABLE,
        help="In-plane shear / normal stress check (GB 50010) for the selected walls.",
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
        needs=("geometry",),
        help="Solve the model's static load cases.",
    ),
    "modal": StepSpec(
        verb="modal",
        run=run_modal,
        params=MODAL_PARAMS,
        kind=MODAL,
        needs=("geometry",),
        help="Solve the eigenvalue (modal) problem against the prepared topology.",
    ),
    "response_spectrum": StepSpec(
        verb="response_spectrum",
        run=run_response_spectrum,
        params=RS_PARAMS,
        kind=CASES,
        needs=("geometry", "modal"),
        help="CQC response-spectrum combination for one spectrum direction.",
    ),
    "pushover": StepSpec(
        verb="pushover",
        run=run_pushover,
        params=PUSHOVER_PARAMS,
        kind=CASES,
        needs=("geometry", "modal"),
        help="Nonlinear static (pushover) analysis with CSM evaluation.",
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
