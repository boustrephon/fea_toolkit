"""The ``response_spectrum`` verb — CQC response-spectrum combination.

Wraps :func:`~fea_toolkit.analysis.rs.run_response_spectrum_analysis`, which
requires the modal result a preceding
:mod:`~fea_toolkit.workflow.verbs.modal` step stored on the context.  The
demand spectrum is supplied as its raw ``T_spec`` / ``Sa_spec`` pair; building
a code spectrum (GB 50011, EC8) is the caller's job
(:class:`~fea_toolkit.spectrum.ResponseSpectrum`), so this verb stays a thin
wrapper.

The heavy import lives inside the function, so importing this module — and the
verb manifest — does not load OpenSees.
"""

from typing import Any

from ..steps import RS, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["RS_PARAMS", "run_response_spectrum"]

RS_PARAMS: dict[str, ParamSpec] = {
    "direction": ParamSpec(
        default="X",
        type=str,
        choices=("X", "Y"),
        help="Spectrum direction to combine.",
    ),
    "T_spec": ParamSpec(
        default=[],
        type=list,
        help="Period axis of the demand spectrum.",
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
        default=12,
        type=int,
        help="Number of modes to include.",
    ),
    "name": ParamSpec(
        default="ResponseSpectrum",
        type=str,
        help="Result label.",
    ),
}


def run_response_spectrum(context: StepContext, step: Step) -> list[StepResult]:
    """Combine one spectrum direction's modal response.

    Args:
        context: The run's working state.  ``model`` and ``results["modal"]``
            must already be present (``mesh`` then ``modal`` steps precede this).
        step: The step to run.  ``params`` supplies the direction and spectrum.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.RS` result whose payload is
        the response-spectrum :class:`~fea_toolkit.analysis.base.AnalysisResult`.

    Raises:
        ValueError: If a parameter is not declared, or no spectrum is supplied.
    """
    from ...analysis.rs import run_response_spectrum_analysis

    params: dict[str, Any] = validate_params("response_spectrum", RS_PARAMS, step.params)
    if not params["T_spec"] or not params["Sa_spec"]:
        raise ValueError("response_spectrum needs a T_spec / Sa_spec spectrum")
    result = run_response_spectrum_analysis(
        context.model,
        context.results["modal"],
        direction=params["direction"],
        T_spec=params["T_spec"],
        Sa_spec=params["Sa_spec"],
        damping=params["damping"],
        n_modes=params["n_modes"],
        name=params["name"],
    )
    context.results["rs"] = result
    context.log(f"Response spectrum ({params['direction']}): complete")
    return [StepResult(kind=RS, label=f"Response spectrum ({params['direction']})", payload=result)]
