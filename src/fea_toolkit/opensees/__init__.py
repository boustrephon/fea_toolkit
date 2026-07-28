"""OpenSees domain construction, analysis execution, and Tcl export.

Architecture
------------
The toolkit uses a **two-stage pipeline** (see docs/workflow.md):

1. **Preprocessor** (topology mutations) → produces a :class:`~fea_toolkit.model.mesh_model.MeshModel`.
2. **AnalysisBuilder** (OpenSees domain creation + analysis) — reads the frozen MeshModel.

Modules
-------
preprocessor — Topology preparation: element splitting, area meshing, edge constraints.
analysis_builder — OpenSees domain construction, loads, modal/static/pushover/RS analysis.
recorder — Tcl script export (:class:`RecordingOpenSees` proxy), Xara Tcl runtime runner.
builder — Legacy Tcl export functions (:func:`export_model_to_tcl`, :func:`pushover_tcl`).
pushover — 4-direction pushover runner with gravity gravity + lateral load sequences.
"""

from .preprocessor import Preprocessor, preprocess_model
from .analysis_builder import AnalysisBuilder, run_modal

from .recorder import (
    RecordingOpenSees,
    XaraTclRunner,
    export_mesh_model_to_tcl,
    parse_pushover_results,
)

from .builder import (
    export_model_to_tcl,
    pushover_tcl,
    compute_lateral_loads,
    modal_to_lateral_loads,
    uniform_lateral_loads,
    triangular_lateral_loads,
)

from .pushover import run_pushover_4dir

__all__ = [
    # Preprocessor
    "Preprocessor",
    "preprocess_model",
    # Analysis builder
    "AnalysisBuilder",
    "run_modal",
    # Tcl export & recording
    "RecordingOpenSees",
    "XaraTclRunner",
    "export_mesh_model_to_tcl",
    "parse_pushover_results",
    # Legacy builder (Tcl export)
    "export_model_to_tcl",
    "pushover_tcl",
    "compute_lateral_loads",
    "modal_to_lateral_loads",
    "uniform_lateral_loads",
    "triangular_lateral_loads",
    # Pushover
    "run_pushover_4dir",
]