"""Response spectrum analysis — CQC combination.

Wraps :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
RS execution.  Requires the result of a :func:`run_modal_analysis` call.
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import AnalysisResult

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


def run_response_spectrum_analysis(
    mesh_model: "MeshModel",
    modal_result: AnalysisResult,
    direction: str,
    T_spec: list[float],
    Sa_spec: list[float],
    damping: float = 0.05,
    n_modes: int = 12,
    name: str = "ResponseSpectrum",
    config: Optional[dict] = None,
) -> AnalysisResult:
    """Run response spectrum analysis for one direction.

    Args:
        mesh_model: Pre-processed topology.
        modal_result: Result from a preceding :func:`run_modal_analysis`.
        direction: ``"X"`` or ``"Y"``.
        T_spec: Period axis of the demand spectrum.
        Sa_spec: Spectral accelerations corresponding to *T_spec*.
        damping: Damping ratio for CQC (default 0.05).
        n_modes: Number of modes to include (default 12).
        name: Result label (default ``"ResponseSpectrum"``).
        config: Optional config dict, recorded in the result metadata.

    Returns:
        :class:`AnalysisResult` whose ``data`` holds the RS result dict,
        the per-mode ``modal_base_shear``, and the ``direction``.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    modal_data = modal_result.data
    if not isinstance(modal_data, dict):
        modal_data = {"modal": modal_data}
    modal = modal_data.get("modal", modal_data)
    periods = modal.get("periods", [])
    if not periods:
        raise RuntimeError(
            "ModalAnalysis result has no periods; "
            "run ModalAnalysis before ResponseSpectrumAnalysis."
        )

    builder_config = {"element_type": "elasticBeamColumn", "verbose": False}
    ab = AnalysisBuilder(mesh_model, builder_config)
    ab.build_domain()
    ab.compute_seismic_masses()
    # Must run modal on the builder's domain before RS
    ab.run_modal_analysis(num_modes=n_modes, print_results=False)

    rs_result = ab.run_response_spectrum_analysis(
        n_modes,
        periods,
        T_spec,
        Sa_spec,
        direction,
        damping,
        print_results=False,
    )

    # Also collect per-mode base shear
    modal_base_shear = list(rs_result.get("modal_base_shear", []))

    return AnalysisResult(
        name=name,
        analysis_type="ResponseSpectrumAnalysis",
        data={
            "rs_result": rs_result,
            "modal_base_shear": modal_base_shear,
            "direction": direction,
        },
        metadata={
            "direction": direction,
            "damping": damping,
            "n_modes": n_modes,
        },
    )
