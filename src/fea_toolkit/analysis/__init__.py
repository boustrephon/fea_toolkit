"""Analysis objects — typed, configurable, dependency-aware.

Each analysis type is a self-contained :class:`Analysis` subclass that
owns its configuration, knows its dependencies, and returns a typed
:class:`AnalysisResult`.  Analyses are composed via
:class:`AnalysisManager` which handles topological ordering and
result passing.
"""

from fea_toolkit.analysis.base import (
    AnalysisResult,
    Analysis,
    AnalysisDefaults,
)

from fea_toolkit.analysis.static import StaticAnalysis
from fea_toolkit.analysis.modal import ModalAnalysis
from fea_toolkit.analysis.rs import ResponseSpectrumAnalysis
from fea_toolkit.analysis.pushover import PushoverAnalysis
from fea_toolkit.analysis.manager import AnalysisManager

__all__ = [
    "AnalysisResult",
    "Analysis",
    "AnalysisDefaults",
    "StaticAnalysis",
    "ModalAnalysis",
    "ResponseSpectrumAnalysis",
    "PushoverAnalysis",
    "AnalysisManager",
]
