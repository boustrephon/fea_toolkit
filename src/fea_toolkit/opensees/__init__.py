"""OpenSees domain construction, analysis execution, and Tcl export.

Architecture
------------
The toolkit uses a **two-stage pipeline** (see docs/workflow.md):

1. **Preprocessor** (topology mutations) → produces a :class:`~fea_toolkit.model.mesh_model.MeshModel`.
2. **AnalysisBuilder** (OpenSees domain creation + analysis) — reads the frozen MeshModel.

Modules
-------
preprocessor — Topology preparation: element splitting, area meshing, edge constraints.
analysis_builder — Public :class:`AnalysisBuilder` facade composed from
    domain mixins:
    _materials — uniaxial + nD material creation.
    _sections — frame/shell section creation.
    _elements — frame/wall/shell element creation, braces, lumped hinges.
    _runners — analysis execution and result extraction (static/modal/RS/pushover).
    _constraints — edge constraints, nodes/restraints, connectivity diagnostics.
    _loads — load-pattern creation, gravity axial loads, rigid diaphragms.
    _limit_state — Elwood & Moehle column limit-state columns.
recorder — Tcl script export (:class:`RecordingOpenSees` proxy), Xara Tcl runtime runner.
builder — Tcl export functions (:func:`export_model_to_tcl`, :func:`pushover_tcl`).
pushover — 4-direction pushover runner with gravity + lateral load sequences.
"""

from ._loads import global_to_local_distributed_load
from .analysis_builder import AnalysisBuilder, run_modal
from .builder import (
    compute_lateral_loads,
    dynamic_time_history_tcl,
    export_model_to_tcl,
    modal_to_lateral_loads,
    pushover_tcl,
    triangular_lateral_loads,
    uniform_lateral_loads,
)
from .preprocessor import Preprocessor, preprocess_model
from .pushover import run_pushover_4dir
from .recorder import (
    RecordingOpenSees,
    XaraTclRunner,
    export_mesh_model_to_tcl,
    parse_pushover_results,
)

__all__ = [
    # Analysis builder
    "AnalysisBuilder",
    # Preprocessor
    "Preprocessor",
    # Tcl export & recording
    "RecordingOpenSees",
    "XaraTclRunner",
    "compute_lateral_loads",
    "dynamic_time_history_tcl",
    "export_mesh_model_to_tcl",
    # Builder (Tcl export)
    "export_model_to_tcl",
    "global_to_local_distributed_load",
    "modal_to_lateral_loads",
    "parse_pushover_results",
    "preprocess_model",
    "pushover_tcl",
    "run_modal",
    # Pushover
    "run_pushover_4dir",
    "triangular_lateral_loads",
    "uniform_lateral_loads",
]
