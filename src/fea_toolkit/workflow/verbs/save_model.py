"""The ``save_model`` verb — write the prepared model to a file.

A thin wrapper over :func:`fea_toolkit.io.stage_writer.write_model_file`: the
GUI's ``File ▸ Save model…`` action and this verb share one implementation, so
a recipe persists the meshed model exactly as the menu does.  The heavy import
lives inside the function so importing the manifest stays OpenSees-free.
"""

from typing import Any

from ..steps import TABLE, ParamSpec, Step, StepContext, StepResult, Table, validate_params

__all__ = ["SAVE_MODEL_PARAMS", "run_save_model"]

#: The options ``save_model`` exposes — exactly what :func:`write_model_file`
#: accepts, so a recipe cannot reach an undocumented option.
SAVE_MODEL_PARAMS: dict[str, ParamSpec] = {
    "path": ParamSpec(
        default="",
        type=str,
        help="Output file path — .json, .npz or .h5 (required).",
    ),
    "fmt": ParamSpec(
        default="h5",
        type=str,
        choices=("json", "npz", "h5"),
        help="File format — JSON codec, NPZ or HDF5 stage file.",
    ),
}


def run_save_model(context: StepContext, step: Step) -> list[StepResult]:
    """Write the prepared topology (and parsed model) to a file.

    Args:
        context: The run's working state.  Its ``model`` is the prepared
            ``MeshModel`` a previous ``mesh`` step produced; ``model_data`` is
            the parsed ``SAPModelData``.
        step: The step to run.  ``params`` carry ``path`` and ``fmt``.

    Returns:
        One :data:`~fea_toolkit.workflow.steps.TABLE` result summarising the
        written file, so the GUI shows it as a confirmation view.

    Raises:
        ValueError: If ``path`` is empty, or no topology has been prepared.
    """
    from ...io.stage_writer import write_model_file

    params: dict[str, Any] = validate_params("save_model", SAVE_MODEL_PARAMS, step.params)
    path = params["path"]
    if not path:
        raise ValueError("save_model: 'path' is required (the output file path)")
    if context.model is None:
        raise ValueError("save_model needs a prepared topology — run a 'mesh' step first")

    written = write_model_file(path, sap=context.model_data, mesh=context.model, fmt=params["fmt"])
    context.log(f"saved model to {written}")
    return [
        StepResult(
            kind=TABLE,
            label="Saved model",
            payload=Table(
                title="Saved model",
                columns=("path", "format"),
                rows=((written, params["fmt"]),),
            ),
        )
    ]
