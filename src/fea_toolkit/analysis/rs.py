"""Response spectrum analysis — CQC combination.

Wraps :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
RS execution.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from fea_toolkit.analysis.base import (
    Analysis,
    AnalysisDefaults,
    AnalysisResult,
    RESPONSE_SPECTRUM_DEFAULTS,
)
from fea_toolkit.analysis.modal import ModalAnalysis


class ResponseSpectrumAnalysis(Analysis):
    """Run response spectrum analysis for one direction.

    Requires the result of a :class:`ModalAnalysis` for periods,
    eigenvalues, and mode shapes.

    Parameters
    ----------
    mesh_model : MeshModel
    modal_result : AnalysisResult
        Result from a preceding :class:`ModalAnalysis`.
    direction : str
        ``"X"`` or ``"Y"``.
    T_spec : list of float
        Period axis of the demand spectrum.
    Sa_spec : list of float
        Spectral accelerations (m/s²) corresponding to *T_spec*.
    damping : float
        Damping ratio for CQC (default 0.05).
    n_modes : int
        Number of modes to include (default 12).
    name : str, optional
    config : dict, optional
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        modal_result: AnalysisResult,
        direction: str,
        T_spec: List[float],
        Sa_spec: List[float],
        damping: float = 0.05,
        n_modes: int = 12,
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(mesh_model, name, config)
        self._modal_result = modal_result
        self.direction = direction
        self.T_spec = T_spec
        self.Sa_spec = Sa_spec
        self.damping = damping
        self.n_modes = n_modes

    @classmethod
    def defaults(cls) -> dict:
        return RESPONSE_SPECTRUM_DEFAULTS.to_dict()

    @property
    def requires(self) -> list:
        return [ModalAnalysis]

    @property
    def provides(self) -> set:
        return {"rs_nodal_displacements", "rs_element_forces",
                "rs_base_shear", "modal_base_shear"}

    def run(self) -> AnalysisResult:
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        modal_data = self._modal_result.data
        if not isinstance(modal_data, dict):
            # Already a dict from run_modal()
            pass
        modal = modal_data.get("modal", modal_data)
        periods = modal.get("periods", [])
        if not periods:
            raise RuntimeError(
                "ModalAnalysis result has no periods; "
                "run ModalAnalysis before ResponseSpectrumAnalysis."
            )

        config = {"element_type": "elasticBeamColumn", "verbose": False}
        ab = AnalysisBuilder(self.mesh_model, config)
        ab.build_domain()
        ab.compute_seismic_masses()
        # Must run modal on the builder's domain before RS
        ab.run_modal_analysis(num_modes=self.n_modes, print_results=False)

        rs_result = ab.run_response_spectrum_analysis(
            self.n_modes,
            periods,
            self.T_spec,
            self.Sa_spec,
            self.direction,
            self.damping,
            print_results=False,
        )

        # Also collect per-mode base shear
        modal_base_shear = list(rs_result.get("modal_base_shear", []))

        return AnalysisResult(
            name=self.name,
            analysis_type="ResponseSpectrumAnalysis",
            data={
                "rs_result": rs_result,
                "modal_base_shear": modal_base_shear,
                "direction": self.direction,
            },
            metadata={
                "direction": self.direction,
                "damping": self.damping,
                "n_modes": self.n_modes,
            },
        )
