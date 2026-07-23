"""Base classes for the analysis framework."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Set, Type


# ────────────────────────────────────────────────────────────────────
# AnalysisDefaults — typed config per analysis type
# ────────────────────────────────────────────────────────────────────


@dataclass
class AnalysisDefaults:
    """Solver, element, and brace defaults for one analysis type.

    Each field has a sensible default for linear-elastic analysis.
    Analysis subclasses override specific fields via class-level
    ``defaults()``.
    """

    # Element / section
    element_type: str = "elasticBeamColumn"
    num_int_pts: int = 3
    use_elastic_sections: bool = True
    create_fiber_sections: bool = False
    geom_transf_type: str = "Linear"
    beam_integration: str = "Lobatto"
    simplify_distributed_loads: bool = False

    # Solver
    solver_test_type: str = "NormDispIncr"
    solver_test_tol: float = 1e-8
    solver_test_max_iter: int = 10
    solver_algorithm: str = "Newton"
    solver_constraints: str = "Transformation"
    solver_system: str = "BandGen"
    gravity_num_substeps: int = 1
    constraint_method: str = "spring"

    # Brace strategy
    brace_type: str = "beam"  # "beam" | "truss" | "hysteretic"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Named defaults instances (mirroring the auto-config table) ──────

STATIC_LINEAR_DEFAULTS = AnalysisDefaults(
    solver_test_tol=1e-8,
    brace_type="beam",
)

MODAL_DEFAULTS = AnalysisDefaults(
    brace_type="beam",
)

RESPONSE_SPECTRUM_DEFAULTS = AnalysisDefaults(
    solver_test_tol=1e-8,
    brace_type="beam",
)

PUSHOVER_STEEL_DEFAULTS = AnalysisDefaults(
    element_type="nonlinearBeamColumn",
    num_int_pts=5,
    use_elastic_sections=False,
    create_fiber_sections=True,
    solver_test_tol=1e-6,
    solver_algorithm="NewtonLineSearch",
    geom_transf_type="PDelta",
    brace_type="truss",
)

PUSHOVER_RC_DEFAULTS = AnalysisDefaults(
    element_type="forceBeamColumn",
    num_int_pts=5,
    use_elastic_sections=False,
    create_fiber_sections=True,
    solver_test_tol=1e-6,
    solver_algorithm="NewtonLineSearch",
    geom_transf_type="PDelta",
    brace_type="beam",
)


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
        # Start from analysis-type defaults
        self.config: dict = dict(self.defaults())
        # Overlay caller overrides
        if config:
            self.config.update(config)
        self._result: Optional[AnalysisResult] = None

    @classmethod
    @abstractmethod
    def defaults(cls) -> dict:
        """Return the :class:`AnalysisDefaults` dict for this type.

        Subclasses should return e.g. ``STATIC_LINEAR_DEFAULTS.to_dict()``.
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
