"""The ``modal`` verb — solve the eigenvalue problem.

Wraps :func:`~fea_toolkit.analysis.modal.run_modal_analysis`.  The result is
stored on :attr:`~fea_toolkit.workflow.steps.StepContext.results` under
``"modal"`` — a mapping keyed by the step's ``name``, so a recipe may hold
several modal analyses and a later
:mod:`~fea_toolkit.workflow.verbs.response_spectrum` or
:mod:`~fea_toolkit.workflow.verbs.pushover` step picks one via its ``modal_case``
parameter (:func:`resolve_modal_result`).  The ordering between them is the
declared ``needs`` chain, not an ad-hoc context slot.

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..config_keys import BUILDER_CONFIG_KEYS
from ..steps import MODAL, ParamSpec, Step, StepContext, StepResult, validate_params
from ._tcl import EXPORT_TCL_PARAMS, maybe_export_tcl

__all__ = [
    "MODAL_PARAMS",
    "resolve_default_modal_case",
    "resolve_modal_result",
    "run_modal",
]

MODAL_PARAMS: dict[str, ParamSpec] = {
    "n_modes": ParamSpec(
        default=12,
        type=int,
        help="Number of modes to extract.",
    ),
    "name": ParamSpec(
        default="ModalAnalysis",
        type=str,
        help="Result label.",
    ),
    "config": ParamSpec(
        default={},
        type=dict,
        help=(
            "OpenSees builder overrides, e.g. {'fix_shell_only_base': True} to "
            "fully fix shell-only base nodes during the build only."
        ),
        manifest=BUILDER_CONFIG_KEYS,
    ),
    **EXPORT_TCL_PARAMS,
}


def resolve_modal_result(context: StepContext, modal_case: str = "") -> Any:
    """Resolve the modal result a spectrum / pushover step consumes.

    The ``modal`` verb stores each run's result under its ``name``, so a recipe
    may hold several modal analyses and a later step picks one by name.  This
    is the single place that choice is made:

    * ``modal_case`` names a result — return it (or raise, listing the known
      names);
    * with one result, ``modal_case`` is unnecessary — return it;
    * with several and no ``modal_case``, raise (a silent pick would be a
      silently-different model).

    A bare :class:`~fea_toolkit.analysis.base.AnalysisResult` is also accepted
    (the pre-named slot), so a caller that set the context directly still works
    — but only when *modal_case* is empty, since a bare result carries no name
    to verify a requested one against.

    Args:
        context: The run's working state.
        modal_case: The modal step's ``name`` to consume, or ``""`` to take the
            sole result.

    Returns:
        The chosen modal :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If no modal result exists, *modal_case* names none, or
            several results exist without a *modal_case*.
    """
    modal = context.results.get("modal")
    if modal is None:
        raise ValueError("no modal result — run a modal step first")
    if isinstance(modal, dict):
        if modal_case:
            if modal_case not in modal:
                raise ValueError(f"no modal result named {modal_case!r}; known: {sorted(modal)}")
            return modal[modal_case]
        if len(modal) == 1:
            return next(iter(modal.values()))
        raise ValueError(
            f"multiple modal results available ({sorted(modal)}); choose one with modal_case"
        )
    # A bare (pre-named) result carries no name to verify, so it is only
    # resolvable when no *modal_case* was requested.
    if modal_case:
        raise ValueError(
            f"cannot verify modal result {modal_case!r}: the context holds a single unnamed result"
        )
    return modal


def resolve_default_modal_case(model_data: Any) -> str:
    """The modal-case name a spectrum / pushover step should default to.

    Reads the model's load cases in file order and returns:

    1. the ``LoadCase.modal_case`` reference of the first response-spectrum
       case, when it names one of the model's modal cases;
    2. otherwise the first modal case in the file;
    3. otherwise ``""`` (the model defines no modal case — the user must add a
       ``modal`` step themselves).

    A modal case is a ``LoadCase`` whose ``case_data`` holds a ``CASE - MODAL …``
    table, or whose ``case_type`` reads as modal (e.g. SAP's ``"LinModal"``).

    Args:
        model_data: The parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.

    Returns:
        The default modal-case name, or ``""``.
    """
    load_cases = getattr(model_data, "load_cases", None) or {}
    modal_names = [
        name
        for name, lc in load_cases.items()
        if any(key.startswith("CASE - MODAL") for key in lc.case_data)
        or "modal" in (lc.case_type or "").lower()
    ]
    for lc in load_cases.values():
        if lc.modal_case and lc.modal_case in modal_names:
            return lc.modal_case
    return modal_names[0] if modal_names else ""


def run_modal(context: StepContext, step: Step) -> list[StepResult]:
    """Solve the eigenvalue problem against the prepared topology.

    Args:
        context: The run's working state.  ``model`` must already be prepared
            (a ``mesh`` step normally precedes this one); the result is stored
            on ``context.results["modal"][name]`` so a later spectrum / pushover
            step can pick one of several modal analyses by name.
        step: The step to run.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.MODAL` result whose payload is
        the modal :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared by this verb, or a result
            named ``params["name"]`` is already stored — a recipe must give
            each modal step a distinct ``name``.
    """
    from ...analysis.modal import run_modal_analysis

    params: dict[str, Any] = validate_params("modal", MODAL_PARAMS, step.params)
    maybe_export_tcl(context, "modal", params)
    result = run_modal_analysis(
        context.model,
        n_modes=params["n_modes"],
        name=params["name"],
        config=params["config"] or None,
        log=context.pipeline_log,
    )
    modal_results = context.results.setdefault("modal", {})
    if params["name"] in modal_results:
        raise ValueError(
            f"a modal result named {params['name']!r} already exists; "
            "give each modal step a distinct 'name'"
        )
    modal_results[params["name"]] = result
    context.log(f"Modal: {params['n_modes']} modes requested")
    return [StepResult(kind=MODAL, label="Modal", payload=result)]
