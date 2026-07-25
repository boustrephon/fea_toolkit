"""Analysis objects — typed, configurable, dependency-aware.

Each analysis type is a self-contained :class:`Analysis` subclass that
owns its configuration, knows its dependencies, and returns a typed
:class:`AnalysisResult`.  Analyses are composed via
:class:`AnalysisManager` which handles topological ordering and
result passing.
"""

import warnings

from fea_toolkit.analysis.base import (
    AnalysisResult,
    Analysis,
    AnalysisCaseSpec,
)

from fea_toolkit.analysis.static import StaticAnalysis
from fea_toolkit.analysis.modal import ModalAnalysis
from fea_toolkit.analysis.rs import ResponseSpectrumAnalysis
from fea_toolkit.analysis.pushover import PushoverAnalysis
from fea_toolkit.analysis.manager import AnalysisManager

# ── Deprecated compatibility exports ──
_AnalysisDefaults = None  # placeholder for removed AnalysisDefaults


def __getattr__(name):
    if name == "AnalysisDefaults":
        warnings.warn(
            "AnalysisDefaults has been removed. Use AnalysisCaseSpec instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AnalysisResult",
    "Analysis",
    "AnalysisCaseSpec",
    # Deprecated — kept for compatibility; will be removed in a future version
    "AnalysisDefaults",
    "StaticAnalysis",
    "ModalAnalysis",
    "ResponseSpectrumAnalysis",
    "PushoverAnalysis",
    "AnalysisManager",
]
