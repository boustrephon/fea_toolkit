"""The verb manifest — every verb the workflow layer offers, as pure data.

This module is the **single manifest** of what a recipe can do.  A caller that
wants to know the vocabulary — the GUI's recipe panel building its "add step"
menu and parameter forms, a validator, the CLI's help listing — imports this and
nothing else.  Because every implementation keeps its heavy imports inside the
function (see :mod:`fea_toolkit.workflow.verbs`), reading the manifest does not
load OpenSees, so a form can be rendered before a model is even open.

The registration order below is the order the verbs make sense in —
prepare properties, prepare topology, solve, reduce, emit — and is the order a
menu should offer them.
"""

from typing import Any

from .steps import (
    CASES,
    FIGURE,
    GEOMETRY,
    IDA,
    MODAL,
    MODEL,
    NLD,
    PUSHOVER,
    RS,
    SESSION,
    TABLE,
    StepSpec,
)
from .verbs import (
    BRACE_BUCKLING_PARAMS,
    BUILD_MODEL_PARAMS,
    CHART_PARAMS,
    CLOSE_PARAMS,
    COMBINE_PARAMS,
    CONNECTIVITY_PARAMS,
    FIX_SHELL_ONLY_BASE_PARAMS,
    HINGE_LENGTH_PARAMS,
    IDA_PARAMS,
    LOAD_VERIFICATION_PARAMS,
    MEMBER_SHEAR_CAPACITY_PARAMS,
    MESH_CHECKS_PARAMS,
    MESH_PARAMS,
    MODAL_PARAMS,
    NLD_PARAMS,
    OPEN_PARAMS,
    OPEN_RESULTS_PARAMS,
    PUSHOVER_PARAMS,
    REPORT_PARAMS,
    RS_PARAMS,
    RUN_STATIC_PARAMS,
    SAVE_MODEL_PARAMS,
    SAVE_PARAMS,
    SAVE_RESULTS_PARAMS,
    SCALE_SECTIONS_PARAMS,
    SELECT_PARAMS,
    SELF_WEIGHT_PARAMS,
    WALL_SHEAR_CHECK_PARAMS,
    run_build_model,
    run_chart,
    run_check_brace_buckling,
    run_check_connectivity,
    run_check_self_weight,
    run_close,
    run_combine,
    run_fix_shell_only_base,
    run_hinge_length,
    run_ida,
    run_load_verification,
    run_member_shear_capacity,
    run_mesh,
    run_mesh_checks,
    run_modal,
    run_nonlinear_dynamic,
    run_open,
    run_open_results,
    run_pushover,
    run_report,
    run_response_spectrum,
    run_save,
    run_save_model,
    run_save_results,
    run_scale_sections,
    run_select,
    run_static,
    run_wall_shear_check,
)

__all__ = ["ANALYSIS_CHAIN", "STEP_SPECS", "analysis_chain_rows", "list_verbs"]

#: The modal-dependent analysis verbs in dependency order — the chain the
#: Analysis wizard offers.  ``run_static`` is deliberately not listed: it is a
#: linear solve with no precursor beyond the geometry, and already has a
#: first-class menu entry of its own.
ANALYSIS_CHAIN = ("modal", "response_spectrum", "pushover", "nonlinear_dynamic", "ida")

#: Every verb, keyed by name.  Each entry pairs an implementation with the
#: parameters that implementation is allowed to read — a verb's parameter
#: surface is exactly what is declared here, so a recipe cannot reach an
#: undocumented option.
STEP_SPECS: dict[str, StepSpec] = {
    "open": StepSpec(
        verb="open",
        run=run_open,
        params=OPEN_PARAMS,
        kind=SESSION,
        needs=(),
        resets_document=True,
        help="Load a model file into the working document.",
        docs=("parser_coverage.md", "safe_import.md", "tcl_import.md"),
    ),
    "open_results": StepSpec(
        verb="open_results",
        run=run_open_results,
        params=OPEN_RESULTS_PARAMS,
        kind=CASES,
        needs=(),
        help="Load a results archive's cases (needs no model).",
        docs=("results_schema.md",),
    ),
    "save": StepSpec(
        verb="save",
        run=run_save,
        params=SAVE_PARAMS,
        kind=SESSION,
        needs=(),
        help="Write the working document to a (versioned) file.",
        docs=("json_serialization.md",),
    ),
    "close": StepSpec(
        verb="close",
        run=run_close,
        params=CLOSE_PARAMS,
        kind=SESSION,
        needs=(),
        resets_document=True,
        help="Close the working document (optionally saving it).",
    ),
    "select": StepSpec(
        verb="select",
        run=run_select,
        params=SELECT_PARAMS,
        kind=TABLE,
        needs=(),
        help="Make (or replace) a named selection.",
        docs=("workflow_authoring.md",),
    ),
    "check_connectivity": StepSpec(
        verb="check_connectivity",
        run=run_check_connectivity,
        params=CONNECTIVITY_PARAMS,
        kind=TABLE,
        help="Check for orphan nodes, duplicated coordinates and zero-area sections.",
        docs=("model_review.md",),
    ),
    "check_self_weight": StepSpec(
        verb="check_self_weight",
        run=run_check_self_weight,
        params=SELF_WEIGHT_PARAMS,
        kind=TABLE,
        help="Compare the model's self-weight against the weight its geometry implies.",
        docs=("model_review.md",),
    ),
    "check_brace_buckling": StepSpec(
        verb="check_brace_buckling",
        run=run_check_brace_buckling,
        params=BRACE_BUCKLING_PARAMS,
        kind=TABLE,
        help="Check the selected braces against Euler buckling.",
        docs=("model_review.md",),
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
        docs=("capacity.md",),
    ),
    "member_shear_capacity": StepSpec(
        verb="member_shear_capacity",
        run=run_member_shear_capacity,
        params=MEMBER_SHEAR_CAPACITY_PARAMS,
        kind=TABLE,
        help="Nominal RC shear capacity (simplified MCFT) for the selected frame sections.",
        docs=("shear_failure_modelling.md", "capacity.md"),
    ),
    "wall_shear_check": StepSpec(
        verb="wall_shear_check",
        run=run_wall_shear_check,
        params=WALL_SHEAR_CHECK_PARAMS,
        kind=TABLE,
        help="In-plane shear / normal stress check (GB 50010) for the selected walls.",
        docs=("capacity.md",),
    ),
    "build_model": StepSpec(
        verb="build_model",
        run=run_build_model,
        params=BUILD_MODEL_PARAMS,
        kind=SESSION,
        resets_document=True,
        help="Build a benchmark example model (FEMA 355C / SAC / generic frame).",
        docs=("benchmark_register.md", "fema355c_schedules.md"),
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
        docs=("stiffness_factors.md",),
    ),
    "fix_shell_only_base": StepSpec(
        verb="fix_shell_only_base",
        run=run_fix_shell_only_base,
        params=FIX_SHELL_ONLY_BASE_PARAMS,
        kind=MODEL,
        help=(
            "Upgrade the model's shell-only base nodes to full fixity before "
            "meshing (pinned → fixed rotations)."
        ),
        docs=("shell_support.md",),
    ),
    "mesh": StepSpec(
        verb="mesh",
        run=run_mesh,
        params=MESH_PARAMS,
        kind=GEOMETRY,
        help="Split frames, mesh areas into shells, and hold a selection back as loads-only.",
        docs=(
            "element_splitting.md",
            "shell_support.md",
            "member_offsets.md",
            "member_releases.md",
        ),
    ),
    "save_model": StepSpec(
        verb="save_model",
        run=run_save_model,
        params=SAVE_MODEL_PARAMS,
        kind=TABLE,
        needs=("geometry",),
        help="Write the prepared model to a JSON, NPZ or HDF5 file.",
        docs=("results_schema.md", "json_serialization.md"),
    ),
    "run_static": StepSpec(
        verb="run_static",
        run=run_static,
        params=RUN_STATIC_PARAMS,
        kind=CASES,
        needs=("geometry",),
        help="Solve the model's static load cases.",
        docs=("analysis.md",),
    ),
    "load_verification": StepSpec(
        verb="load_verification",
        run=run_load_verification,
        params=LOAD_VERIFICATION_PARAMS,
        kind=TABLE,
        needs=("geometry",),
        help="Load/reaction summary — applied loads vs summed reactions per static case, plus RS base shear/moment.",
        docs=("load_cases_and_combinations.md", "model_review.md"),
    ),
    "modal": StepSpec(
        verb="modal",
        run=run_modal,
        params=MODAL_PARAMS,
        kind=MODAL,
        needs=("geometry",),
        help="Solve the eigenvalue (modal) problem against the prepared topology.",
        docs=("modal_analysis.md", "shear_building_benchmark.md"),
    ),
    "response_spectrum": StepSpec(
        verb="response_spectrum",
        run=run_response_spectrum,
        params=RS_PARAMS,
        kind=RS,
        needs=("geometry", "modal"),
        help="CQC response-spectrum combination for one spectrum direction.",
        docs=("modal_analysis.md", "shear_building_benchmark.md"),
    ),
    "pushover": StepSpec(
        verb="pushover",
        run=run_pushover,
        params=PUSHOVER_PARAMS,
        kind=PUSHOVER,
        needs=("geometry", "modal"),
        help="Nonlinear static (pushover) analysis with CSM evaluation.",
        docs=("pushover_analysis.md", "vecchio_emara_benchmark.md", "duong_benchmark.md"),
        citations=("10.14359/1283",),
    ),
    "nonlinear_dynamic": StepSpec(
        verb="nonlinear_dynamic",
        run=run_nonlinear_dynamic,
        params=NLD_PARAMS,
        kind=NLD,
        needs=("geometry", "modal"),
        help="Nonlinear dynamic (time-history) analysis of a ground-motion record.",
        docs=("nonlinear_dynamic_analysis.md", "sac_nonlinear_benchmark.md"),
    ),
    "ida": StepSpec(
        verb="ida",
        run=run_ida,
        params=IDA_PARAMS,
        kind=IDA,
        needs=("geometry", "modal"),
        help="Incremental dynamic analysis — collapse intensity over a record ladder.",
        docs=(
            "sac_imk_collapse_benchmark.md",
            "ik2005_generic_frame_benchmark.md",
            "imk_deterioration_model.md",
        ),
        citations=("10.1002/eqe.495", "10.1061/(ASCE)ST.1943-541X.0000376"),
    ),
    "combine": StepSpec(
        verb="combine",
        run=run_combine,
        params=COMBINE_PARAMS,
        kind=CASES,
        needs=("cases",),
        help="Reduce the solved cases into load combinations.",
        docs=("load_combinations.md", "load_cases_and_combinations.md"),
    ),
    "save_results": StepSpec(
        verb="save_results",
        run=run_save_results,
        params=SAVE_RESULTS_PARAMS,
        kind=SESSION,
        needs=("cases",),
        help="Write the solved cases to a .npz results archive.",
        docs=("results_schema.md", "load_cases_and_combinations.md"),
    ),
    "report": StepSpec(
        verb="report",
        run=run_report,
        params=REPORT_PARAMS,
        kind=SESSION,
        needs=("cases",),
        help="Render the solved static cases as a plain-text report.",
        docs=("recipe_cli.md",),
    ),
    "chart": StepSpec(
        verb="chart",
        run=run_chart,
        params=CHART_PARAMS,
        kind=FIGURE,
        needs=(),
        help=(
            "Plot a figure — 'storey_displacements' / 'storey_forces' profile a "
            "solved case; 'response_spectra' plots the model's parsed spectra; "
            "'pushover' / 'modal' / 'csm' / 'ida' plot the analysis results."
        ),
        docs=("storey_response.md", "force_diagram_unification.md"),
    ),
}


def list_verbs() -> list[str]:
    """Verb names in registration order — preparation, topology, solve, reduce.

    Returns:
        The verb names, in the order a menu should offer them.
    """
    return list(STEP_SPECS)


def analysis_chain_rows(
    produced: Any = (),
    wanted: Any = (),
) -> list[tuple[str, str, bool]]:
    """``(verb, help, runnable)`` rows for the Analysis wizard, in chain order.

    A verb is *runnable* when its declared ``needs`` are satisfied by
    *produced* (the kinds already available — e.g. ``{"geometry"}`` for a
    preprocessed model) or by the kinds a *wanted* verb earlier in the chain
    produces.  This is the same vocabulary
    :func:`fea_toolkit.workflow.recipe.satisfied_kinds` uses, so the wizard
    cannot drift from the recipe validator's idea of what may run.

    Args:
        produced: Kinds satisfied before the chain begins.
        wanted: The verbs the user has selected (a subset of
            :data:`ANALYSIS_CHAIN`).

    Returns:
        ``(verb, help, runnable)`` triples, in :data:`ANALYSIS_CHAIN` order.
    """
    rows: list[tuple[str, str, bool]] = []
    have = set(produced)
    wanted_set = set(wanted)
    for verb in ANALYSIS_CHAIN:
        spec = STEP_SPECS[verb]
        missing = [kind for kind in spec.needs if kind not in have]
        rows.append((verb, spec.help, not missing))
        if verb in wanted_set:
            have.add(spec.kind)
    return rows
