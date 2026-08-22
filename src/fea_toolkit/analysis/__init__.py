"""Analysis helpers — module-level functions returning typed results.

Each analysis type is a module-level function that owns its configuration
arguments and returns a typed :class:`AnalysisResult`.  Results are
composed explicitly by the caller (e.g. :func:`fea_toolkit.report.generate_report`)
in a readable order — no dependency-graph machinery.
"""

from fea_toolkit.analysis.base import (
    AnalysisCaseSpec,
    AnalysisResult,
)
from fea_toolkit.analysis.linear import (
    run_linear_cases,
    static_load_verification,
    wind_sanity_check,
)
from fea_toolkit.analysis.modal import run_modal_analysis
from fea_toolkit.analysis.nonlinear_dynamic import run_nonlinear_dynamic_analysis
from fea_toolkit.analysis.pushover import run_pushover_analysis
from fea_toolkit.analysis.rs import run_response_spectrum_analysis
from fea_toolkit.analysis.static import run_static_analysis
from fea_toolkit.capacity.elwood_limit_state import (
    ElwoodColumnGeometry,
    ElwoodColumnParameters,
    axial_capacity_surface,
    elwood_axial_deg_slope,
    elwood_axial_drift_at_failure,
    elwood_column_geometry,
    elwood_column_parameters,
    elwood_limit_state_envelope,
    elwood_shear_drift_at_failure,
    elwood_shear_limit_force,
    elwood_spring_slopes,
    three_point_axial_surface,
)
from fea_toolkit.capacity.shear_capacity import (
    ShearCapacityResult,
    ShearFailureReport,
    member_shear_capacity,
    report_shear_failure,
    shear_backbone,
)

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
    "AnalysisCaseSpec",
    "AnalysisResult",
    "ElwoodColumnGeometry",
    "ElwoodColumnParameters",
    "ShearCapacityResult",
    "ShearFailureReport",
    "axial_capacity_surface",
    "elwood_axial_deg_slope",
    "elwood_axial_drift_at_failure",
    "elwood_column_geometry",
    "elwood_column_parameters",
    "elwood_limit_state_envelope",
    "elwood_shear_drift_at_failure",
    "elwood_shear_limit_force",
    "elwood_spring_slopes",
    "member_shear_capacity",
    "report_shear_failure",
    "run_linear_cases",
    "run_modal_analysis",
    "run_nonlinear_dynamic_analysis",
    "run_pushover_analysis",
    "run_response_spectrum_analysis",
    "run_static_analysis",
    "shear_backbone",
    "static_load_verification",
    "three_point_axial_surface",
    "wind_sanity_check",
]
