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
from fea_toolkit.utils import g_from_units
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
        self._modal_result = modal_result
        self.material_type = material_type
        self.gravity_patterns = gravity_patterns
        self.lateral_load_type = lateral_load_type
        self.max_disp_val = max_disp_val
        self.num_steps = num_steps
        self.brace_type = brace_type
        self.brace_sections = brace_sections
        self.rs_modal_base_shear = rs_modal_base_shear
        super().__init__(mesh_model, name, config)

    @classmethod
    def defaults(cls) -> dict:
        """Return default config (steel defaults)."""
        return dict(_PUSHOVER_STEEL_DEFAULTS)

    @classmethod
    def defaults_for(cls, material_type: str = "steel") -> dict:
        """Material-type-specific defaults lookup."""
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
        2. Append ``pushover_tcl()`` commands with recorder output files
        3. Run via ``XaraTclRunner``
        4. Parse recorder output files via ``parse_pushover_results()``
        5. Return compatible ``AnalysisResult``

        Recorder output files (``*_disp.out``, ``*_bs.out``, ``*_reaction.out``)
        are written alongside the Tcl script in the ``output/`` directory, which
        is gitignored per project convention.
        """
        from pathlib import Path
        import os
        import datetime

        from fea_toolkit.opensees.recorder import (
            export_mesh_model_to_tcl,
            XaraTclRunner,
            parse_pushover_results,
        )
        from fea_toolkit.opensees.builder import pushover_tcl
        from fea_toolkit.model.mesh_model import MeshModel

        mm: MeshModel = self.mesh_model

        # ── Output directory ─────────────────────────────────────────
        out_dir = Path("output") / f"pushover_rc_{datetime.datetime.now():%Y%m%d_%H%M%S}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Determine control node (topmost node along Z)
        control_node = 1
        max_z = -1e12
        for nd in mm.nodes.values():
            if nd.z > max_z:
                max_z = nd.z
                control_node = nd.node_tag

        # Determine direction index: X=0, Y=1, Z=2 (default X) for lateral loads
        dir_index = 0
        if self.config.get("direction") == "Y":
            dir_index = 1
        elif self.config.get("direction") == "Z":
            dir_index = 2

        # DOF for control node displacement (1=X, 2=Y, 3=Z) — follows dir_index+1
        control_dof = dir_index + 1

        # Build lateral load pattern from mode 1 shape or uniform
        modal_data = self._modal_result.data
        if not isinstance(modal_data, dict):
            modal_data = {"modal": modal_data}

        modal_nested = modal_data.get("modal", modal_data)
        periods = modal_nested.get("periods", [])
        shapes = modal_nested.get("shapes", modal_nested.get("mode_shapes", {}))
        first_mode = shapes.get(1, shapes.get(0, {})) if shapes else {}

        # Lateral load pattern based on lateral_load_type
        lateral_loads: Dict[int, tuple] = {}
        if self.lateral_load_type == "uniform":
            # Uniform: unit masses at all nodes
            for nd in mm.nodes.values():
                load = [0.0, 0.0, 0.0]
                load[dir_index] = 1.0
                lateral_loads[nd.node_tag] = tuple(load)
        elif self.lateral_load_type == "triangular":
            # Triangular: proportional to mass × height
            heights = [nd.z for nd in mm.nodes.values()]
            min_z = min(heights) if heights else 0.0
            total_weight = 0.0
            for nd in mm.nodes.values():
                h = nd.z - min_z
                if h < 0:
                    h = 0.0
                load = [0.0, 0.0, 0.0]
                load[dir_index] = h
                lateral_loads[nd.node_tag] = tuple(load)
                total_weight += h
            if total_weight > 1e-12:
                for tag in lateral_loads:
                    lateral_loads[tag] = tuple(
                        v / total_weight for v in lateral_loads[tag])
        elif self.lateral_load_type == "mode1" and first_mode:
            # Mode 1 proportional: mass × mode1 shape
            total_weight = 0.0
            for nd in mm.nodes.values():
                mode_comp = first_mode.get(nd.node_tag, (1.0, 0.0, 0.0))
                w = abs(mode_comp[dir_index] if len(mode_comp) > dir_index else mode_comp[0])
                load = [0.0, 0.0, 0.0]
                load[dir_index] = w
                lateral_loads[nd.node_tag] = tuple(load)
                total_weight += w
            if total_weight > 0:
                for tag in lateral_loads:
                    lateral_loads[tag] = tuple(
                        v / total_weight for v in lateral_loads[tag])
        else:
            # Fallback: uniform in configured direction
            for nd in mm.nodes.values():
                load = [0.0, 0.0, 0.0]
                load[dir_index] = 1.0
                lateral_loads[nd.node_tag] = tuple(load)

        # Gravity loads — use MeshModel's computed mass when available
        gravity_loads: Dict[int, tuple] = {}
        # Scale gravitational acceleration to model units (never hardcode g).
        g = g_from_units(mm.units)
        for nd in mm.nodes.values():
            mass_val = getattr(nd, 'mass', None)
            if mass_val is None or mass_val <= 0.0:
                # Skip nodes without a valid mass rather than fabricating 1.0
                continue
            gravity_loads[nd.node_tag] = (0.0, 0.0, -mass_val * g)

        # RC config (overrides for fiber sections)
        rc_config = dict(_PUSHOVER_RC_DEFAULTS)
        rc_config.update(self.config)
        rc_config["create_fiber_sections"] = True

        output_prefix = "pushover_rc"

        # Generate Tcl suffix with recorder files — DOF matches direction
        tcl_suffix = pushover_tcl(
            control_node=control_node,
            dof=control_dof,
            max_disp=self.max_disp_val,
            num_steps=self.num_steps,
            lateral_loads=lateral_loads,
            gravity_loads=gravity_loads,
            adaptive=True,
        )

        # Write Tcl script to output directory
        tcl_path = str(out_dir / "model.tcl")
        export_mesh_model_to_tcl(
            mm, tcl_path, config=rc_config, tcl_suffix=tcl_suffix,
        )

        # Run via Xara — the runner sets cwd to the tcl file's directory,
        # so recorder output files are written alongside ``model.tcl``.
        runner = XaraTclRunner()
        ret, output = runner.run(tcl_path)

        # ── Validate return status ──
        if ret != 0:
            # Propagate failure without parsing outputs
            return AnalysisResult(
                name=self.name,
                analysis_type="PushoverAnalysis",
                data={
                    "error": f"XaraTclRunner returned status {ret}",
                    "output_raw": output,
                    "output_dir": str(out_dir) if out_dir.exists() else None,
                },
                metadata={
                    "material_type": "rc",
                    "lateral_load_type": self.lateral_load_type,
                    "max_disp_val": self.max_disp_val,
                    "num_steps": self.num_steps,
                    "tcl_path": None,
                    "output_dir": None,
                    "config": self.config,
                    "error": f"runner returned {ret}",
                },
            )

        # ── Parse recorder output files ─────────────────────────────
        disp_path = str(out_dir / f"{output_prefix}_disp.out")
        bs_path = str(out_dir / f"{output_prefix}_bs.out")
        reaction_path = str(out_dir / f"{output_prefix}_reaction.out")

        def _safe_list(arr, default=None):
            """Convert optional array-like to list; return empty list if missing/empty."""
            if arr is None:
                return default if default is not None else []
            try:
                lst = arr.tolist()
                if lst is None:
                    return default if default is not None else []
                return lst
            except (AttributeError, ValueError, TypeError):
                return default if default is not None else []

        def _safe_scalar(arr, default=0.0):
            """Extract first scalar from optional array-like; return default if missing/empty."""
            if arr is None:
                return default
            try:
                flat = arr.flatten()
                if flat.size == 0:
                    return default
                return float(flat[0])
            except (AttributeError, ValueError, IndexError, TypeError):
                return default

        result = {}
        if os.path.exists(disp_path) and os.path.exists(bs_path):
            try:
                parsed = parse_pushover_results(
                    disp_path, bs_path,
                    reaction_path if os.path.exists(reaction_path) else None,
                )
                result = {
                    "control_disp": _safe_list(parsed.get("control_disp")),
                    "base_shear": _safe_list(parsed.get("base_shear")),
                    "step": _safe_list(parsed.get("step")),
                    "base_rx": _safe_scalar(parsed.get("base_rx")),
                    "base_ry": _safe_scalar(parsed.get("base_ry")),
                    "base_rz": _safe_scalar(parsed.get("base_rz")),
                    "output_raw": output,
                    "output_dir": str(out_dir),
                }
                if "reaction_rx" in parsed:
                    result["reaction_rx"] = _safe_list(parsed.get("reaction_rx"))
            except Exception as exc:
                result = {"error": str(exc), "output_raw": output}
        else:
            # Fallback: try stdout parsing as before
            rx = ry = rz = 0.0
            for line in output.splitlines():
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
                "base_reactions": {"rx": rx, "ry": ry, "rz": rz},
                "output_raw": output,
                "output_dir": str(out_dir),
            }

        # Determine post-cleanup state for metadata
        out_dir_removed = False
        tcl_path_removed = False
        if not rc_config.get("keep_tcl", False) and not rc_config.get("keep_output", False):
            import shutil
            try:
                shutil.rmtree(str(out_dir), ignore_errors=True)
                out_dir_removed = True
                tcl_path_removed = True
            except OSError:
                pass

        return AnalysisResult(
            name=self.name,
            analysis_type="PushoverAnalysis",
            data=result,
            metadata={
                "material_type": "rc",
                "lateral_load_type": self.lateral_load_type,
                "max_disp_val": self.max_disp_val,
                "num_steps": self.num_steps,
                "tcl_path": None if tcl_path_removed else (tcl_path if os.path.exists(tcl_path) else None),
                "output_dir": None if out_dir_removed else (str(out_dir) if out_dir.exists() else None),
                "config": self.config,
            },
        )
