"""Base containers for the analysis framework.

Holds the shared :class:`AnalysisResult` / :class:`AnalysisCaseSpec`
containers and the per-type default config dicts used by the module-level
analysis functions in :mod:`fea_toolkit.analysis`.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

# ── Per-type defaults (simple dicts, no dataclass overhead) ──────────
# Linear-elastic defaults (static / modal / response spectrum): a single
# shared dict — the three analysis types use identical solver and element
# settings; they differ only in what they execute.

_LINEAR_ELASTIC_DEFAULTS: dict = {
    "element_type": "elasticBeamColumn",
    "num_int_pts": 3,
    "use_elastic_sections": True,
    "create_fiber_sections": False,
    "geom_transf_type": "Linear",
    "beam_integration": "Lobatto",
    "simplify_distributed_loads": False,
    "solver_test_type": "NormDispIncr",
    "solver_test_tol": 1e-8,
    "solver_test_max_iter": 10,
    "solver_algorithm": "Newton",
    "solver_constraints": "Transformation",
    "solver_system": "BandGen",
    "gravity_num_substeps": 1,
    "constraint_method": "spring",
    "brace_type": "beam",
}

_PUSHOVER_STEEL_DEFAULTS: dict = {
    "element_type": "nonlinearBeamColumn",
    "num_int_pts": 5,
    "use_elastic_sections": False,
    "create_fiber_sections": True,
    "geom_transf_type": "PDelta",
    "beam_integration": "Lobatto",
    "simplify_distributed_loads": False,
    "solver_test_type": "NormDispIncr",
    "solver_test_tol": 1e-6,
    "solver_test_max_iter": 10,
    "solver_algorithm": "NewtonLineSearch",
    "solver_constraints": "Transformation",
    "solver_system": "BandGen",
    "gravity_num_substeps": 10,
    "constraint_method": "spring",
    "brace_type": "truss",
}

_PUSHOVER_RC_DEFAULTS: dict = {
    "element_type": "forceBeamColumn",
    "num_int_pts": 5,
    "use_elastic_sections": False,
    "create_fiber_sections": True,
    "geom_transf_type": "PDelta",
    "beam_integration": "Lobatto",
    "simplify_distributed_loads": False,
    # Solver: relaxed RC fibre settings (see docs/_pending_work.md,
    # 2026-08-04).  forceBeamColumn performs its own internal state
    # determination on top of the global solver, so the strict generic
    # defaults (NormDispIncr 1e-6 / 10 / NewtonLineSearch) stall around
    # 0.006 m control displacement on full RC buildings.  The confirmed
    # settings (v6 rc_config) are NormDispIncr 1e-4 / 20 / Newton, with
    # the per-step NormUnbalance + ModifiedNewton(-initial) fallback.
    "solver_test_type": "NormDispIncr",
    "solver_test_tol": 1e-4,
    "solver_test_max_iter": 20,
    "solver_algorithm": "Newton",
    "solver_constraints": "Transformation",
    "solver_system": "BandGen",
    "gravity_num_substeps": 10,
    # Explicit per-step fallback chain (also applied by default by the
    # builder — must stay in sync with
    # AnalysisBuilder.PUSHOVER_FALLBACK_DEFAULTS).
    "pushover_fallback_defaults": {
        "solver_test_type": "NormUnbalance",
        "solver_test_max_iter": 1000,
        "solver_algorithm": "ModifiedNewton",
    },
    "constraint_method": "spring",
    "brace_type": "beam",
}

# ── Nonlinear dynamic defaults ────────────────────────────────────

_NONLINEAR_DYNAMIC_DEFAULTS: dict = {
    "element_type": "forceBeamColumn",
    "num_int_pts": 5,
    "use_elastic_sections": False,
    "create_fiber_sections": True,
    "geom_transf_type": "PDelta",
    "beam_integration": "Lobatto",
    "simplify_distributed_loads": False,
    "solver_test_type": "NormDispIncr",
    "solver_test_tol": 1e-5,
    "solver_test_max_iter": 20,
    "solver_algorithm": "NewtonLineSearch",
    "solver_constraints": "Transformation",
    "solver_system": "BandGen",
    "gravity_num_substeps": 10,
    "constraint_method": "spring",
    "brace_type": "beam",
    "damping": 0.05,
    "rayleigh_damping": True,
    "num_steps": 1000,
    "dt": 0.005,
    "newmark_gamma": 0.5,
    "newmark_beta": 0.25,
}


# ────────────────────────────────────────────────────────────────────
# AnalysisCaseSpec — minimal per-case runtime contract
# ────────────────────────────────────────────────────────────────────


@dataclass
class AnalysisCaseSpec:
    """Minimal, solver-agnostic description of one analysis case.

    The shared ``MeshModel`` is the canonical prepared state.  Each case
    spec only describes how that shared model should be realized for one
    analysis run.

    Parameters
    ----------
    name : str
        User-facing case label.
    analysis_type : str
        Logical analysis kind, e.g. ``"static"``, ``"modal"``,
        ``"response_spectrum"``, ``"pushover"``.
    config : dict, optional
        Case-specific overrides applied at analysis time.
    kwargs : dict, optional
        Additional solver or post-processing arguments for the case.
    """

    name: str
    analysis_type: str
    config: dict[str, Any] = field(default_factory=dict)
    kwargs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ────────────────────────────────────────────────────────────────────
# AnalysisResult — typed data container
# ────────────────────────────────────────────────────────────────────


@dataclass
class AnalysisResult:
    """Result of one analysis execution.

    Parameters
    ----------
    name : str
        User-assigned label (e.g. ``"Pushover Steel"``).
    analysis_type : str
        Class name of the analysis that produced this result
        (e.g. ``"StaticAnalysis"``).
    data : dict
        Dict of arrays, DataFrames, and figures produced by the analysis.
    metadata : dict
        Config snapshot, timestamps, log entries.
    """

    name: str
    analysis_type: str
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# (Analysis ABC removed — the analysis subpackage now exposes
# module-level functions returning AnalysisResult, composed explicitly
# by the caller instead of via a dependency-graph manager.)
# ────────────────────────────────────────────────────────────────────
