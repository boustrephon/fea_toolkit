"""The ``report`` verb — render the solved cases as a plain-text report.

Delegates to :func:`~fea_toolkit.analysis.report_text.render_static_text_report`
so the formatting lives in exactly one pure place; the verb only gathers the
solved cases and their applied/reaction summaries and hands them over.  The
report is written to the run's log (stdout under a batch CLI) and returned as a
:data:`~fea_toolkit.workflow.steps.SESSION` result, so it is a side-effect step
— like ``save`` — rather than a view.  Static-only for now.
"""

from ..steps import SESSION, ParamSpec, Step, StepContext, StepResult, validate_params

__all__ = ["REPORT_PARAMS", "run_report"]

REPORT_PARAMS: dict[str, ParamSpec] = {}


def run_report(context: StepContext, step: Step) -> list[StepResult]:
    """Render a plain-text report of the solved static cases.

    Args:
        context: The run's working state.  ``case_results`` holds the solved
            cases; ``results["static_summaries"]`` the applied/reaction
            summaries a ``run_static`` step recorded.
        step: The step to run — no parameters.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.SESSION` result whose payload is
        the report text.
    """
    validate_params("report", REPORT_PARAMS, step.params)

    if not context.case_results:
        context.log("report: no solved cases to report")
        return [StepResult(kind=SESSION, label="Report", payload="No solved cases to report.")]

    from ...analysis.report_text import render_static_text_report

    text = render_static_text_report(
        context.model_data,
        context.model,
        dict(context.case_results),
        context.results.get("static_summaries"),
    )
    context.log(text)
    return [StepResult(kind=SESSION, label="Report", payload=text)]
