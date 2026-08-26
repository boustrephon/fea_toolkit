"""Static (linear) analysis — auto-detected load cases.

Wraps :func:`~fea_toolkit.analysis.linear.run_linear_cases`.
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import AnalysisResult

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel
    from fea_toolkit.model.sap_data import SAPModelData


def run_static_analysis(
    mesh_model: "MeshModel",
    md: "SAPModelData",
    spec_cfg: Optional[dict] = None,
    linear_cfg: Optional[dict] = None,
    name: str = "StaticAnalysis",
    config: Optional[dict] = None,
    collect_raw: bool = False,
) -> AnalysisResult:
    """Run static linear analysis cases.

    Auto-detects static load cases from the SAP2000 model, or uses the
    *linear_cfg* ``cases`` parameter to specify which to run.

    Args:
        mesh_model: Pre-processed topology from the Preprocessor.
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.
        spec_cfg: Spectrum config passed through to ``run_linear_cases()``
            for response-spectrum-related static verification.
        linear_cfg: Linear analysis config (e.g. ``{"cases": [...]}``).
        name: Result label (default ``"StaticAnalysis"``).
        config: Optional config dict, recorded in the result metadata.
        collect_raw: When ``True``, also retain the raw per-case results
            (nodal displacements + local element forces) under
            ``data["static_raw"]`` — the shape accepted by the unified
            writers.  ``False`` (default) keeps the existing behaviour.

    Returns:
        :class:`AnalysisResult` whose ``data`` holds ``df_linear``.
    """
    from fea_toolkit.analysis.linear import run_linear_cases

    raw_out: dict = {} if collect_raw else None
    df_linear = run_linear_cases(
        md,
        mesh_model,
        spec_cfg=spec_cfg,
        linear_cfg=linear_cfg,
        raw_out=raw_out,
    )
    data: dict = {
        "df_linear": df_linear,
    }
    if raw_out:
        data["static_raw"] = raw_out
    return AnalysisResult(
        name=name,
        analysis_type="StaticAnalysis",
        data=data,
        metadata={"spec_cfg": spec_cfg, "linear_cfg": linear_cfg, "config": config or {}},
    )
