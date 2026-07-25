"""Base classes for the analysis framework."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Set, Type


# ── Per-type defaults (simple dicts, no dataclass overhead) ──────────

_STATIC_LINEAR_DEFAULTS: dict = {
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

_MODAL_DEFAULTS: dict = {
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

_RESPONSE_SPECTRUM_DEFAULTS: dict = {
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
    "solver_test_type": "NormDispIncr",
    "solver_test_tol": 1e-6,
    "solver_test_max_iter": 10,
    "solver_algorithm": "NewtonLineSearch",
    "solver_constraints": "Transformation",
    "solver_system": "BandGen",
    "gravity_num_substeps": 10,
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
    config: Dict[str, Any] = field(default_factory=dict)
    kwargs: Dict[str, Any] = field(default_factory=dict)

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
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Analysis — abstract base
# ────────────────────────────────────────────────────────────────────


class Analysis(ABC):
    """One analysis case — owns config, knows how to run itself.

    Subclasses must implement :meth:`defaults` and :meth:`run`.

    Parameters
    ----------
    mesh_model : MeshModel
        Pre-processed topology from the Preprocessor.
    name : str, optional
        Human-readable label.  Defaults to the class name.
    config : dict, optional
        Overrides for analysis-type defaults.  Deep-merged on top of
        :meth:`defaults` so callers only need to provide differences.
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        self.mesh_model = mesh_model
        self.name = name or type(self).__name__
        # Start from analysis-type defaults (fresh copy every call)
        self.config: dict = dict(self.defaults())
        # Overlay caller overrides
        if config:
            self.config.update(config)
        self._result: Optional[AnalysisResult] = None

    @classmethod
    @abstractmethod
    def defaults(cls) -> dict:
        """Return the default config dict for this analysis type.

        Subclasses should return e.g. ``dict(_STATIC_LINEAR_DEFAULTS)``.
        """
        ...

    @abstractmethod
    def run(self) -> AnalysisResult:
        """Execute the analysis and return a result."""
        ...

    @property
    def requires(self) -> List[Type["Analysis"]]:
        """Analysis classes that must run before this one.

        Override to declare dependencies (e.g. ``[ModalAnalysis]``).
        The :class:`AnalysisManager` uses this for topological ordering.
        """
        return []

    @property
    def provides(self) -> Set[str]:
        """Data keys this analysis writes into its result."""
        return set()

    @property
    def result(self) -> Optional[AnalysisResult]:
        return self._result