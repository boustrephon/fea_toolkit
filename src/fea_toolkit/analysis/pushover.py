"""Pushover (nonlinear static) analysis — 4 directions with CSM.

Supports steel (OpenSeesPy) and RC (Tcl/Xara export) nonlinear pushover.

Steel path
    Wraps :func:`~fea_toolkit.opensees.pushover.run_pushover_4dir`.
    Uses :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
    with steel fiber sections, Hysteretic brace trusses, and CSM.

RC path
    Exports the model to Tcl via
    :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`,
    appends ``pushover_tcl()`` commands, and runs via
    :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.
    Uses ``forceBeamColumn`` with fiber sections (Concrete01, Steel02).
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fea_toolkit.analysis.base import (
    Analysis,
    AnalysisResult,
    _PUSHOVER_STEEL_DEFAULTS,
    _PUSHOVER_RC_DEFAULTS,
)
from fea_toolkit.analysis.modal import ModalAnalysis

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


class PushoverAnalysis(Analysis):
    """Run pushover analysis in 4 directions with CSM evaluation.

    Requires the result of a :class:`ModalAnalysis` for mode shapes
    and periods (needed for ``mode1`` lateral load pattern and CSM).

    Parameters
    ----------
    mesh_model : MeshModel
    modal_result : AnalysisResult
        Result from a preceding :class:`ModalAnalysis`.
    material_type : str
        ``"steel"`` (default) or ``"rc"``.  Steel uses OpenSeesPy
        directly with Hysteretic hinges.  RC exports to Tcl and runs
        via Xara with ``forceBeamColumn`` + fiber sections.
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
    rs_modal_base_shear : dict, optional
        Per-mode RS base shear ``{"X": [...], "Y": [...]}`` for
        mode1 pattern validation against RS demand (diagnostic only).
    name : str, optional
    config : dict, optional
        Builder config overrides.
    """

    def __init__(
        self,
        mesh_model: "MeshModel",  # noqa: F821
        modal_result: AnalysisResult,
        material_type: str = "steel",
        gravity_patterns: Optional[Dict[str, float]] = None,
        lateral_load_type: str = "mode1",
        max_disp_val: float = 0.30,
        num_steps: int = 50,
        brace_type: str = "truss",
        brace_sections: Optional[list] = None,
        rs_modal_base_shear: Optional[Dict[str, List[float]]] = None,
        name: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(mesh_model, name, config)
        self._modal_result = modal_result
        self.material_type = material_type
        self.gravity_patterns = gravity_patterns
        self.lateral_load_type = lateral_load_type
        self.max_disp_val = max_disp_val
        self.num_steps = num_steps
        self.brace_type = brace_type
        self.brace_sections = brace_sections
        self.rs_modal_base_shear = rs_modal_base_shear

    @classmethod
    def defaults(cls, material_type: str = "steel") -> dict:
        """Return default config for the given material type."""
        if material_type == "rc":
            return dict(_PUSHOVER_RC_DEFAULTS)
        return dict(_PUSHOVER_STEEL_DEFAULTS)

    @property
    def requires(self) -> list:
        return [ModalAnalysis]

    @property
    def provides(self) -> set:
        return {"all_out", "fig_csm_plots", "df_compare"}

    def run(self) -> AnalysisResult:
        if self.material_type == "rc":
            return self._run_rc_tcl_path()
        return self._run_steel_path()

    # ── Steel path (OpenSeesPy) ───────────────────────────────────

    def _run_steel_path(self) -> AnalysisResult:
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
            rs_modal_base_shear=self.rs_modal_base_shear,
            verbose=self.config.get("verbose", False),
        )

        return AnalysisResult(
            name=self.name,
            analysis_type="PushoverAnalysis",
            data=result,
            metadata={
                "material_type": "steel",
                "lateral_load_type": self.lateral_load_type,
                "max_disp_val": self.max_disp_val,
                "num_steps": self.num_steps,
                "brace_type": self.brace_type,
                "config": self.config,
            },
        )

    # ── RC path (Tcl/Xara export) ────────────────────────────────

    def _run_rc_tcl_path(self) -> AnalysisResult:
        """Run RC pushover via Tcl export + XaraTclRunner.

        Workflow:
        1. Generate RC fiber Tcl via ``export_mesh_model_to_tcl()``
        2. Append ``pushover_tcl()`` commands
        3. Run via ``XaraTclRunner``
        4. Parse output (displacement, base reactions)
        5. Return compatible ``AnalysisResult``
        """
        from pathlib import Path
        import tempfile
        import os

        from fea_toolkit.opensees.recorder import (
            export_mesh_model_to_tcl,
            XaraTclRunner,
        )
        from fea_toolkit.opensees.builder import pushover_tcl
        from fea_toolkit.model.mesh_model import MeshModel

        mm: MeshModel = self.mesh_model

        # Determine control node (topmost node along Z)
        control_node = 1
        max_z = -1e12
        for nd in mm.nodes.values():
            if nd.z > max_z:
                max_z = nd.z
                control_node = nd.node_tag

        # Build lateral load pattern from mode 1 shape or uniform
        modal_data = self._modal_result.data
        if not isinstance(modal_data, dict):
            modal_data = {"modal": modal_data}

        periods = modal_data.get("modal", {}).get("periods", [])
        shapes = modal_data.get("shapes", {})
        first_mode = shapes.get(1, {}) if shapes else {}

        # Lateral load pattern based on lateral_load_type
        lateral_loads: Dict[int, tuple] = {}
        if self.lateral_load_type == "uniform":
            # Uniform: unit masses at all nodes
            for nd in mm.nodes.values():
                lateral_loads[nd.node_tag] = (1.0, 0.0, 0.0)
        elif self.lateral_load_type == "mode1" and first_mode:
            # Mode 1 proportional: mass × mode1 shape
            total_weight = 0.0
            for nd in mm.nodes.values():
                w = abs(first_mode.get(nd.node_tag, (1.0, 0.0, 0.0))[0])
                lateral_loads[nd.node_tag] = (w, 0.0, 0.0)
                total_weight += w
            if total_weight > 0:
                for tag in lateral_loads:
                    lateral_loads[tag] = (
                        lateral_loads[tag][0] / total_weight, 0.0, 0.0)
        else:
            for nd in mm.nodes.values():
                lateral_loads[nd.node_tag] = (1.0, 0.0, 0.0)

        # Gravity loads
        gravity_loads: Dict[int, tuple] = {}
        g = 9.81
        for nd in mm.nodes.values():
            mass = getattr(nd, 'mass', 0.0) or 1.0
            gravity_loads[nd.node_tag] = (0.0, 0.0, -mass * g)

        # RC config (overrides for fiber sections)
        rc_config = dict(_PUSHOVER_RC_DEFAULTS)
        rc_config.update(self.config)
        rc_config["create_fiber_sections"] = True

        # Generate Tcl
        tcl_suffix = pushover_tcl(
            control_node=control_node,
            dof=1,
            max_disp=self.max_disp_val,
            num_steps=self.num_steps,
            lateral_loads=lateral_loads,
            gravity_loads=gravity_loads,
            adaptive=True,
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tcl", delete=False, encoding="utf-8"
        ) as f:
            tcl_path = f.name
            export_mesh_model_to_tcl(
                mm, tcl_path, config=rc_config, tcl_suffix=tcl_suffix,
            )

        # Run via Xara
        runner = XaraTclRunner()
        ret, output = runner.run(tcl_path)

        # Clean up temp file
        try:
            os.unlink(tcl_path)
        except OSError:
            pass

        # Parse output
        disp = 0.0
        rx = 0.0
        ry = 0.0
        rz = 0.0
        for line in output.splitlines():
            if f"Control node" in line and "dof" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    try:
                        disp = float(parts[-1].strip())
                    except ValueError:
                        pass
            if "Base reactions:" in line:
                import re
                m = re.search(r"Rx\s*=\s*([-\d.e+]+)", line)
                if m:
                    rx = float(m.group(1))
                m = re.search(r"Ry\s*=\s*([-\d.e+]+)", line)
                if m:
                    ry = float(m.group(1))
                m = re.search(r"Rz\s*=\s*([-\d.e+]+)", line)
                if m:
                    rz = float(m.group(1))

        result = {
            "control_disp": [disp],
            "base_shear": [rx],
            "base_reactions": {"rx": rx, "ry": ry, "rz": rz},
            "output_raw": output,
        }

        return AnalysisResult(
            name=self.name,
            analysis_type="PushoverAnalysis",
            data=result,
            metadata={
                "material_type": "rc",
                "lateral_load_type": self.lateral_load_type,
                "max_disp_val": self.max_disp_val,
                "num_steps": self.num_steps,
                "tcl_path": tcl_path if os.path.exists(tcl_path) else None,
                "config": self.config,
            },
        )