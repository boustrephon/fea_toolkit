"""Shared Tcl-export checkbox for the analysis verbs.

The standalone ``export_tcl`` verb was replaced by a checkbox on every analysis
step, because the QC artifact is an output *of* the analysis being checked and
so belongs on that step rather than as a free-standing one.  This module holds
the one shared parameter pair and the one helper, so the analysis verbs reuse a
single spelling of the operation instead of each growing its own path.

The helper dumps the prepared :class:`~fea_toolkit.model.mesh_model.MeshModel`
as a standalone OpenSees Tcl script **before** the analysis runs, so the file is
written even when the solve fails — the point of QC.  It is model-only: the Tcl
carries nodes, restraints, materials, sections and elements, not the analysis
commands, which are deterministic and rarely the thing being checked.

The heavy import lives inside the function, so importing the manifest stays
OpenSees-free.
"""

from pathlib import Path
from typing import Any

from ..steps import ParamSpec, StepContext

__all__ = ["EXPORT_TCL_PARAMS", "default_tcl_path", "maybe_export_tcl"]

EXPORT_TCL_PARAMS: dict[str, ParamSpec] = {
    "export_tcl": ParamSpec(
        default=False,
        type=bool,
        help="Also write the prepared model as a standalone OpenSees Tcl script.",
    ),
    "export_tcl_path": ParamSpec(
        default="",
        type=str,
        help=(
            "Tcl output path; empty derives '<input-dir>/<stem>_model.tcl' next "
            "to the model's source file, else '<verb>_model.tcl'."
        ),
    ),
}


def default_tcl_path(context: StepContext, verb: str) -> str:
    """Derive the Tcl path from the model's source file when it has one.

    The parsed model records its source path in ``SAPModelData.source``, so the
    QC file lands next to the input by default.  A model built in code (no
    source) falls back to ``<verb>_model.tcl`` in the working directory.
    """
    source = getattr(context.model_data, "source", "") or ""
    if source:
        p = Path(source)
        return str(p.parent / f"{p.stem or verb}_model.tcl")
    return f"{verb}_model.tcl"


def maybe_export_tcl(context: StepContext, verb: str, params: dict[str, Any]) -> None:
    """Write the prepared model as a Tcl script when ``export_tcl`` is set.

    Args:
        context: The run's working state; ``model`` is the prepared topology.
        verb: The analysis verb's name — the fallback file name and log prefix.
        params: The verb's validated parameters (``export_tcl`` /
            ``export_tcl_path``).
    """
    if not params.get("export_tcl"):
        return
    if context.model is None:
        context.log(f"{verb}: export_tcl requested but no prepared topology — skipped")
        return
    from ...opensees.recorder import export_mesh_model_to_tcl

    path = params.get("export_tcl_path") or default_tcl_path(context, verb)
    export_mesh_model_to_tcl(context.model, path)
    context.log(f"{verb}: exported OpenSees Tcl to {path}")
