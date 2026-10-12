"""The ``response_spectrum`` verb — CQC response-spectrum combination.

Wraps :func:`~fea_toolkit.analysis.rs.run_response_spectrum_analysis`, which
requires the modal result a preceding
:mod:`~fea_toolkit.workflow.verbs.modal` step stored on the context.  The
demand spectrum can come from three places, selected by ``spectrum_source``:

* ``"explicit"`` — the raw ``T_spec`` / ``Sa_spec`` pair the step declares;
* ``"model"`` — a named ``FUNCTION - RESPONSE SPECTRUM`` parsed from the .s2k;
* ``"auto_seismic"`` — rebuilt from the model's ``AUTO SEISMIC`` table
  (:func:`~fea_toolkit.spectrum.spectrum_from_auto_seismic`).

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..steps import RS, ParamSpec, Step, StepContext, StepResult, validate_params
from ._tcl import EXPORT_TCL_PARAMS, maybe_export_tcl
from .modal import resolve_modal_result

__all__ = ["RS_PARAMS", "resolve_spectrum", "run_response_spectrum"]

RS_PARAMS: dict[str, ParamSpec] = {
    "direction": ParamSpec(
        default="X",
        type=str,
        choices=("X", "Y"),
        help="Spectrum direction to combine.",
    ),
    "spectrum_source": ParamSpec(
        default="explicit",
        type=str,
        choices=("explicit", "model", "auto_seismic"),
        help=(
            "Where the demand spectrum comes from — 'explicit' (T_spec/Sa_spec), "
            "'model' (a named 'FUNCTION - RESPONSE SPECTRUM'), or 'auto_seismic' "
            "(rebuilt from the model's AUTO SEISMIC table)."
        ),
    ),
    "spectrum": ParamSpec(
        default="",
        type=str,
        help="Model response-spectrum function name (used when source is 'model').",
    ),
    "T_spec": ParamSpec(
        default=[],
        type=list,
        help="Period axis of the demand spectrum (used when source is 'explicit').",
    ),
    "Sa_spec": ParamSpec(
        default=[],
        type=list,
        help="Spectral accelerations corresponding to T_spec.",
    ),
    "damping": ParamSpec(
        default=0.05,
        type=float,
        help="Damping ratio for the CQC combination.",
    ),
    "n_modes": ParamSpec(
        default=None,
        type=int,
        help="Number of modes to include; leave at 'all' to use every mode.",
        allow_none=True,
    ),
    "modal_case": ParamSpec(
        default="",
        type=str,
        help=(
            "Name of the modal step whose result this spectrum combines.  Leave "
            "empty to use the only modal result present."
        ),
    ),
    "name": ParamSpec(
        default="ResponseSpectrum",
        type=str,
        help="Result label.",
    ),
    **EXPORT_TCL_PARAMS,
}


#: The only ``AUTO SEISMIC`` table
#: :func:`~fea_toolkit.spectrum.spectrum_from_auto_seismic` understands — SAP2000
#: writes other code families (IBC, UBC, …) with different fields, so matching
#: any ``"SEISMIC"`` name would hand it a record it cannot read.
_AUTO_SEISMIC_TABLE = "AUTO SEISMIC - CHINESE 2010"


def _find_auto_seismic_record(model_data) -> tuple[dict, str, list[str]]:
    """The supported ``AUTO SEISMIC`` record among the model's load patterns.

    Returns:
        ``(record, table_name, unsupported)`` — *record* / *table_name* are
        ``{}`` / ``""`` when no supported record exists, and *unsupported* lists
        the other ``AUTO SEISMIC`` tables the model defines but
        :func:`~fea_toolkit.spectrum.spectrum_from_auto_seismic` cannot read.
    """
    unsupported: list[str] = []
    matches: list[tuple[dict, str]] = []
    for lp in (getattr(model_data, "load_patterns", None) or {}).values():
        for table_name, record in (getattr(lp, "auto_data", None) or {}).items():
            upper = table_name.upper()
            if not upper.startswith("AUTO SEISMIC"):
                continue
            if upper == _AUTO_SEISMIC_TABLE:
                matches.append((record, table_name))
                continue
            if table_name not in unsupported:
                unsupported.append(table_name)
    if len(matches) > 1:
        raise ValueError(
            f"response_spectrum: multiple {_AUTO_SEISMIC_TABLE} records found "
            f"({len(matches)}) — a single AUTO SEISMIC definition is required"
        )
    if matches:
        return matches[0][0], matches[0][1], unsupported
    return {}, "", unsupported


def resolve_spectrum(
    context: StepContext,
    *,
    source: str,
    name: str,
    T_spec: list,
    Sa_spec: list,
) -> tuple[list, list]:
    """Resolve the demand spectrum ``(T, Sa)`` for the given *source*.

    Pure — no OpenSees — so it is unit-tested without the analysis engine.

    Args:
        context: The run's working state.  ``model_data`` supplies the parsed
            response-spectrum functions / ``AUTO SEISMIC`` tables a ``"model"``
            or ``"auto_seismic"`` source reads from.
        source: Where the demand spectrum comes from — ``"explicit"``,
            ``"model"``, or ``"auto_seismic"``.
        name: The model response-spectrum function name (used when *source* is
            ``"model"``).
        T_spec: The period axis of an explicit spectrum (used when *source* is
            ``"explicit"``).
        Sa_spec: The spectral accelerations paired with *T_spec*.

    Returns:
        The resolved ``(T, Sa)`` pair — a ``tuple`` of two ``list`` values —
        taken from the chosen source.

    Raises:
        ValueError: If the chosen source cannot provide a spectrum (unknown
            function name, no ``AUTO SEISMIC`` table, or an empty explicit pair).
    """
    if source == "model":
        functions = getattr(context.model_data, "response_spectrum_functions", None) or {}
        function = functions.get(name)
        if function is None:
            raise ValueError(
                f"response_spectrum: no model spectrum named {name!r}; known: {sorted(functions)}"
            )
        return function.periods, function.accels

    if source == "auto_seismic":
        from ...spectrum import spectrum_from_auto_seismic

        record, _table_name, unsupported = _find_auto_seismic_record(context.model_data)
        if not record:
            if unsupported:
                raise ValueError(
                    "response_spectrum: no supported AUTO SEISMIC table "
                    f"(supported: {_AUTO_SEISMIC_TABLE}; found: {unsupported})"
                )
            raise ValueError("response_spectrum: the model defines no AUTO SEISMIC table")
        T, Sa = spectrum_from_auto_seismic(record, units=context.model_data.units)
        return T, Sa

    if not T_spec or not Sa_spec:
        raise ValueError("response_spectrum needs a T_spec / Sa_spec spectrum")
    return T_spec, Sa_spec


def run_response_spectrum(context: StepContext, step: Step) -> list[StepResult]:
    """Combine one spectrum direction's modal response.

    Args:
        context: The run's working state.  ``model`` and ``results["modal"]``
            must already be present (``mesh`` then ``modal`` steps precede this).
            The modal result is chosen by the step's ``modal_case`` parameter.
        step: The step to run.  ``params`` supplies the direction, spectrum and
            the modal result to combine.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.RS` result whose payload is
        the response-spectrum :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared, or no spectrum is supplied.
    """
    from ...analysis.rs import run_response_spectrum_analysis

    params: dict[str, Any] = validate_params("response_spectrum", RS_PARAMS, step.params)
    maybe_export_tcl(context, "response_spectrum", params)

    T_spec, Sa_spec = resolve_spectrum(
        context,
        source=params["spectrum_source"],
        name=params["spectrum"],
        T_spec=params["T_spec"],
        Sa_spec=params["Sa_spec"],
    )

    result = run_response_spectrum_analysis(
        context.model,
        resolve_modal_result(context, params["modal_case"]),
        direction=params["direction"],
        T_spec=T_spec,
        Sa_spec=Sa_spec,
        damping=params["damping"],
        n_modes=params["n_modes"],
        name=params["name"],
        log=context.pipeline_log,
    )
    context.results["rs"] = result
    context.log(f"Response spectrum ({params['direction']}): complete")
    return [StepResult(kind=RS, label=f"Response spectrum ({params['direction']})", payload=result)]
