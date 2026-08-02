"""Modal analysis — eigenvalue solution.

Wraps :func:`~fea_toolkit.opensees.analysis_builder.run_modal`.
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import (
    _MODAL_DEFAULTS,
    Analysis,
    AnalysisResult,
)

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


class ModalAnalysis(Analysis):
    """Run an eigenvalue (modal) analysis.

    Parameters
    ----------
    mesh_model : MeshModel
        Pre-processed topology.
    n_modes : int, optional
        Number of modes to extract (default 12).
    name : str, optional
    config : dict, optional
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        n_modes: int = 12,
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(mesh_model, name, config)
        self.n_modes = n_modes

    @classmethod
    def defaults(cls) -> dict:
        return dict(_MODAL_DEFAULTS)

    @property
    def provides(self) -> set:
        return {"periods", "frequencies", "shapes", "participation"}

    def run(self) -> AnalysisResult:
        from fea_toolkit.opensees.analysis_builder import run_modal

        result = run_modal(self.mesh_model, n_modes=self.n_modes)
        return AnalysisResult(
            name=self.name,
            analysis_type="ModalAnalysis",
            data=result,
            metadata={"n_modes": self.n_modes, "config": self.config},
        )
