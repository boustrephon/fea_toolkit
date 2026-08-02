"""Static (linear) analysis — auto-detected load cases.

Wraps :func:`~fea_toolkit.io.report.run_linear_cases`.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from fea_toolkit.analysis.base import (
    Analysis,
    AnalysisResult,
    _STATIC_LINEAR_DEFAULTS,
)
from fea_toolkit.analysis.modal import ModalAnalysis

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


class StaticAnalysis(Analysis):
    """Run static linear analysis cases.

    Auto-detects static load cases from the SAP2000 model, or uses
    the *cases* parameter to specify which to run.

    Parameters
    ----------
    mesh_model : MeshModel
    spec_cfg : dict, optional
        Spectrum config passed through to ``run_linear_cases()``
        for response-spectrum-related static verification.
    linear_cfg : dict, optional
        Linear analysis config (e.g. ``{"cases": [...]}``).
    name : str, optional
    config : dict, optional
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        spec_cfg: Optional[dict] = None,
        linear_cfg: Optional[dict] = None,
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(mesh_model, name, config)
        self.spec_cfg = spec_cfg
        self.linear_cfg = linear_cfg
        self._md: Optional[Any] = None

    def bind_md(self, md: Any) -> "StaticAnalysis":
        """Bind the parsed SAPModelData (required before run)."""
        self._md = md
        return self

    @classmethod
    def defaults(cls) -> dict:
        return dict(_STATIC_LINEAR_DEFAULTS)

    @property
    def requires(self) -> list:
        return []

    @property
    def provides(self) -> set:
        return {"df_linear", "rs_modal_x", "rs_modal_y"}

    def run(self) -> AnalysisResult:
        from fea_toolkit.io.report import run_linear_cases

        if self._md is None:
            raise RuntimeError(
                "StaticAnalysis requires md to be bound via bind_md()"
            )

        df_linear = run_linear_cases(
            self._md,
            self.mesh_model,
            spec_cfg=self.spec_cfg,
            linear_cfg=self.linear_cfg,
        )
        return AnalysisResult(
            name=self.name,
            analysis_type="StaticAnalysis",
            data={
                "df_linear": df_linear,
            },
            metadata={"spec_cfg": self.spec_cfg, "linear_cfg": self.linear_cfg},
        )
