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
    _runners — analysis runner facade; the implementation is split into
        per-analysis-type mixins: _runner_static (static + seismic masses +
        extraction), _runner_modal, _runner_rs, _runner_pushover.
    _constraints — edge constraints, nodes/restraints, connectivity diagnostics.
    _loads — load-pattern creation, gravity axial loads, rigid diaphragms.
    _limit_state — Elwood & Moehle column limit-state columns.
recorder — Tcl script export (:class:`RecordingOpenSees` proxy), Xara Tcl runtime runner.
builder — Tcl export functions (:func:`export_model_to_tcl`, :func:`pushover_tcl`).
pushover — 4-direction pushover runner with gravity + lateral load sequences.

Import notes
------------
The ``preprocessor`` module is guaranteed OpenSees-free (pure model-layer
topology mutations) and is used inside Rhino 8, where ``openseespy``
cannot be installed (Python ≥ 3.10 required).  The solver-bound modules
(``analysis_builder``, ``builder``, ``pushover``, ``recorder``) are
therefore imported lazily via PEP 562: ``import fea_toolkit.opensees``
does **not** load ``openseespy`` unless a solver name is accessed.
"""

import importlib

from .preprocessor import Preprocessor, preprocess_model

_LAZY_IMPORTS: dict[str, str] = {
    # Analysis builder
    "AnalysisBuilder": "fea_toolkit.opensees.analysis_builder",
    "run_modal": "fea_toolkit.opensees.analysis_builder",
    # Standalone load transform helper (in the solver-bound _loads mixin)
    "global_to_local_distributed_load": "fea_toolkit.opensees._loads",
    "compute_lateral_loads": "fea_toolkit.opensees.builder",
    "dynamic_time_history_tcl": "fea_toolkit.opensees.builder",
    "export_model_to_tcl": "fea_toolkit.opensees.builder",
    "modal_to_lateral_loads": "fea_toolkit.opensees.builder",
    "pushover_tcl": "fea_toolkit.opensees.builder",
    "triangular_lateral_loads": "fea_toolkit.opensees.builder",
    "uniform_lateral_loads": "fea_toolkit.opensees.builder",
    "run_pushover_4dir": "fea_toolkit.opensees.pushover",
    "RecordingOpenSees": "fea_toolkit.opensees.recorder",
    "XaraTclRunner": "fea_toolkit.opensees.recorder",
    "export_mesh_model_to_tcl": "fea_toolkit.opensees.recorder",
    "parse_pushover_results": "fea_toolkit.opensees.recorder",
}

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


def __getattr__(name: str):
    """PEP 562 lazy attribute resolution for solver-bound modules."""
    module = _LAZY_IMPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(set(globals()) | set(_LAZY_IMPORTS) | set(__all__))
