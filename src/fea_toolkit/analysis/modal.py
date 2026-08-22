"""Modal analysis — eigenvalue solution.

Wraps :func:`~fea_toolkit.opensees.analysis_builder.run_modal`.
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import AnalysisResult

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


def run_modal_analysis(
    mesh_model: "MeshModel",
    n_modes: int = 12,
    name: str = "ModalAnalysis",
    config: Optional[dict] = None,
) -> AnalysisResult:
    """Run an eigenvalue (modal) analysis.

    Args:
        mesh_model: Pre-processed topology from the Preprocessor.
        n_modes: Number of modes to extract (default 12).
        name: Result label (default ``"ModalAnalysis"``).
        config: Optional config dict, recorded in the result metadata.

    Returns:
        :class:`AnalysisResult` whose ``data`` holds the modal result dict
        (periods, frequencies, mode shapes, participation factors).
    """
    from fea_toolkit.opensees.analysis_builder import run_modal

    result = run_modal(mesh_model, n_modes=n_modes)
    return AnalysisResult(
        name=name,
        analysis_type="ModalAnalysis",
        data=result,
        metadata={"n_modes": n_modes, "config": config or {}},
    )
