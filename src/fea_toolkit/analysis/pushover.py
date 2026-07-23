"""Pushover (nonlinear static) analysis — 4 directions with CSM.

Wraps :func:`~fea_toolkit.opensees.pushover.run_pushover_4dir`.
"""

from typing import Any, Dict, List, Optional

from fea_toolkit.analysis.base import (
    Analysis,
    AnalysisResult,
    PUSHOVER_STEEL_DEFAULTS,
)
from fea_toolkit.analysis.modal import ModalAnalysis


class PushoverAnalysis(Analysis):
    """Run pushover analysis in 4 directions with CSM evaluation.

    Requires the result of a :class:`ModalAnalysis` for mode shapes
    and periods (needed for ``mode1`` lateral load pattern and CSM).

    Parameters
    ----------
    mesh_model : MeshModel
    modal_result : AnalysisResult
        Result from a preceding :class:`ModalAnalysis`.
    gravity_patterns : dict, optional
        Dict mapping pattern name → scale factor for gravity loads.
    lateral_load_type : str
        One of ``"uniform"``, ``"triangular"``, ``"mode1"`` (default).
    max_disp_val : float
        Maximum control displacement (m, default 0.30).
    num_steps : int
        Number of push increments (default 50).
    brace_type : str
        ``"truss"`` for steel braces, ``"beam"`` for elastic (default
        "truss" per steel defaults).
    brace_sections : list, optional
        Section names to treat as braces.
    name : str, optional
    config : dict, optional
        Builder config overrides.
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        modal_result: AnalysisResult,
        gravity_patterns: Optional[Dict[str, float]] = None,
        lateral_load_type: str = "mode1",
        max_disp_val: float = 0.30,
        num_steps: int = 50,
        brace_type: str = "truss",
        brace_sections: Optional[list] = None,
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(mesh_model, name, config)
        self._modal_result = modal_result
        self.gravity_patterns = gravity_patterns
        self.lateral_load_type = lateral_load_type
        self.max_disp_val = max_disp_val
        self.num_steps = num_steps
        self.brace_type = brace_type
        self.brace_sections = brace_sections

    @classmethod
    def defaults(cls) -> dict:
        return PUSHOVER_STEEL_DEFAULTS.to_dict()

    @property
    def requires(self) -> list:
        return [ModalAnalysis]

    @property
    def provides(self) -> set:
        return {"all_out", "fig_csm_plots", "df_compare"}

    def run(self) -> AnalysisResult:
        from fea_toolkit.opensees.pushover import run_pushover_4dir

        modal_data = self._modal_result.data
        if not isinstance(modal_data, dict):
            modal_data = {"modal": modal_data}

        result = run_pushover_4dir(
            self.mesh_model,
            modal_result=modal_data,
            gravity_patterns=self.gravity_patterns,
            lateral_load_type=self.lateral_load_type,
            max_disp_val=self.max_disp_val,
            num_steps=self.num_steps,
            brace_type=self.brace_type,
            brace_sections=self.brace_sections,
            verbose=self.config.get("verbose", False),
        )

        return AnalysisResult(
            name=self.name,
            analysis_type="PushoverAnalysis",
            data=result,
            metadata={
                "lateral_load_type": self.lateral_load_type,
                "max_disp_val": self.max_disp_val,
                "num_steps": self.num_steps,
                "brace_type": self.brace_type,
                "config": self.config,
            },
        )
