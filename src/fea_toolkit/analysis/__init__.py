"""Analysis objects — typed, configurable, dependency-aware.

Each analysis type is a self-contained :class:`Analysis` subclass that
owns its configuration, knows its dependencies, and returns a typed
:class:`AnalysisResult`.  Analyses are composed via
:class:`AnalysisManager` which handles topological ordering and
result passing.
"""

from fea_toolkit.analysis.base import (
    Analysis,
    AnalysisCaseSpec,
    AnalysisResult,
)
from fea_toolkit.analysis.manager import AnalysisManager
from fea_toolkit.analysis.modal import ModalAnalysis
from fea_toolkit.analysis.nonlinear_dynamic import NonlinearDynamicAnalysis
from fea_toolkit.analysis.pushover import PushoverAnalysis
from fea_toolkit.analysis.rs import ResponseSpectrumAnalysis
from fea_toolkit.analysis.shear_capacity import (
    ShearCapacityResult,
    ShearFailureReport,
    member_shear_capacity,
    report_shear_failure,
    shear_backbone,
)
from fea_toolkit.analysis.static import StaticAnalysis

# ── Deprecated compatibility exports ──


def __getattr__(name):
    if name == "AnalysisDefaults":
        raise AttributeError(
            "AnalysisDefaults has been removed. Use AnalysisCaseSpec instead. "
            "Accessing via from fea_toolkit.analysis import AnalysisDefaults "
            "will fail with this error."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Analysis",
    "AnalysisCaseSpec",
    "AnalysisManager",
    "AnalysisResult",
    "ModalAnalysis",
    "NonlinearDynamicAnalysis",
    "PushoverAnalysis",
    "ResponseSpectrumAnalysis",
    "ShearCapacityResult",
    "ShearFailureReport",
    "StaticAnalysis",
    "member_shear_capacity",
    "report_shear_failure",
    "shear_backbone",
]
