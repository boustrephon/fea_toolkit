"""Pushover (nonlinear static) analysis — 4 directions with CSM.

Supports steel (OpenSeesPy) and RC (OpenSeesPy or Tcl/Xara export)
nonlinear pushover.

Steel path
    Wraps :func:`~fea_toolkit.opensees.pushover.run_pushover_4dir`.
    Uses :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
    with steel fiber sections, Hysteretic brace trusses, and CSM.

RC path (OpenSeesPy, preferred)
    Wraps :func:`~fea_toolkit.opensees.pushover.pushover_rc_openseespy`.
    Uses :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
    with ``forceBeamColumn`` fiber sections (Concrete01, Steel02) and
    optional nonlinear shell walls.  Supports single-direction or
    four-direction push with CSM post-processing.  Dispatched from
    :meth:`PushoverAnalysis.run` when ``material_type="rc"`` (unless
    ``use_tcl_fallback`` is set).

RC path (Tcl/Xara export, legacy fallback)
    Exports the model to Tcl via
    :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`,
    appends ``pushover_tcl()`` commands, and runs via
    :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.
    Uses ``forceBeamColumn`` with fiber sections (Concrete01, Steel02).
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import (
    _PUSHOVER_RC_DEFAULTS,
    _PUSHOVER_STEEL_DEFAULTS,
    Analysis,
    AnalysisResult,
)
from fea_toolkit.analysis.modal import ModalAnalysis
from fea_toolkit.spectrum import ResponseSpectrum
from fea_toolkit.utils import g_from_units

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


# ── Shared load helpers (Tcl path and future ReplayConcrete path) ──


def _find_control_node(mm: "MeshModel") -> int:
    """Return the node tag of the topmost node (max Z)."""
    control_node = 1
    max_z = -1e12
    for nd in mm.nodes.values():
        if nd.z > max_z:
            max_z = nd.z
            control_node = nd.node_tag
    return control_node


def _build_lateral_loads(
    mm: "MeshModel",
    lateral_load_type: str,
    dir_index: int,
    shapes: Optional[dict] = None,
) -> dict[int, tuple]:
    """Build nodal lateral load pattern (uniform / triangular / mode1).

    Parameters
    ----------
    mm : MeshModel
        Frozen topology from the Preprocessor.
    lateral_load_type : str
        One of ``'uniform'``, ``'triangular'``, ``'mode1'``.
    dir_index : int
        Lateral direction index: 0=X, 1=Y, 2=Z.
    shapes : dict, optional
        Mode-shape dict (mode index → {node_tag: (dx, dy, dz)}) used
        only by the ``'mode1'`` pattern.

    Returns
    -------
    dict
        ``{node_tag: (fx, fy, fz)}`` — normalized weights (not scaled
        by gravity or mass) for the Tcl ``pushover_tcl()`` helper.
    """
    lateral_loads: dict[int, tuple] = {}
    if lateral_load_type == "uniform":
        # Uniform: unit weights at all nodes
        for nd in mm.nodes.values():
            load = [0.0, 0.0, 0.0]
            load[dir_index] = 1.0
            lateral_loads[nd.node_tag] = tuple(load)
    elif lateral_load_type == "triangular":
        # Triangular: proportional to height above base
        heights = [nd.z for nd in mm.nodes.values()]
        min_z = min(heights) if heights else 0.0
        total_weight = 0.0
        for nd in mm.nodes.values():
            h = max(nd.z - min_z, 0.0)
            load = [0.0, 0.0, 0.0]
            load[dir_index] = h
            lateral_loads[nd.node_tag] = tuple(load)
            total_weight += h
        if total_weight > 1e-12:
            for tag, ld in lateral_loads.items():
                lateral_loads[tag] = tuple(v / total_weight for v in ld)
    elif lateral_load_type == "mode1" and shapes:
        # Mode 1 proportional: mode-shape component in lateral direction
        first_mode = shapes.get(1, shapes.get(0, {})) if shapes else {}
        total_weight = 0.0
        for nd in mm.nodes.values():
            mode_comp = first_mode.get(nd.node_tag, (1.0, 0.0, 0.0))
            w = abs(mode_comp[dir_index] if len(mode_comp) > dir_index else mode_comp[0])
            load = [0.0, 0.0, 0.0]
            load[dir_index] = w
            lateral_loads[nd.node_tag] = tuple(load)
            total_weight += w
        if total_weight > 0:
            for tag, ld in lateral_loads.items():
                lateral_loads[tag] = tuple(v / total_weight for v in ld)
    else:
        # Fallback: uniform in configured direction
        for nd in mm.nodes.values():
            load = [0.0, 0.0, 0.0]
            load[dir_index] = 1.0
            lateral_loads[nd.node_tag] = tuple(load)

    return lateral_loads


def _build_gravity_loads(mm: "MeshModel") -> dict[int, tuple]:
    """Build nodal gravity loads from node masses and model-unit g.

    Uses :func:`~fea_toolkit.utils.g_from_units` for the unit-consistent
    gravitational acceleration — never hardcodes ``g``.
    """
    gravity_loads: dict[int, tuple] = {}
    g = g_from_units(mm.units)
    for nd in mm.nodes.values():
        mass_val = getattr(nd, "mass", None)
        if mass_val is None or mass_val <= 0.0:
            # Skip nodes without a valid mass rather than fabricating 1.0
            continue
        gravity_loads[nd.node_tag] = (0.0, 0.0, -mass_val * g)
    return gravity_loads


def _find_base_node_tags(mm: "MeshModel") -> list[int]:
    """Return node tags of restrained (support) nodes."""
    base_node_tags: list[int] = []
    for nid, nd in mm.nodes.items():
        r = mm.restraints.get(nid)
        if r is not None and any(int(x) != 0 for x in r.dofs):
            base_node_tags.append(nd.node_tag)
    return base_node_tags or [1]


class PushoverAnalysis(Analysis):
    """Run pushover analysis in 4 directions with CSM evaluation.

    Always requires the result of a :class:`ModalAnalysis` — not just
    for the ``mode1`` lateral load pattern.  The CSM post-processing
    needs the fundamental period and modal effective mass to convert
    the pushover curve to an ADRS capacity spectrum and to locate the
    performance point.  ``lateral_load_type`` only controls the *shape*
    of the lateral load distribution, not whether modal analysis runs.

    Parameters
    ----------
    mesh_model : MeshModel
    modal_result : AnalysisResult
        Result from a preceding :class:`ModalAnalysis`.
    material_type : str
        ``"steel"`` (default) or ``"rc"``.  Steel uses OpenSeesPy
        directly with Hysteretic hinges.  RC runs in OpenSeesPy with
        ``forceBeamColumn`` + fiber sections, unless
        ``config["use_tcl_fallback"]`` is set to export to Tcl and
        run via Xara (legacy).
    gravity_patterns : dict, optional
        Dict mapping pattern name → scale factor for gravity loads.
    lateral_load_type : str
        One of ``"uniform"``, ``"triangular"``, ``"mode1"`` (default).
        Controls the lateral load distribution shape only; modal
        analysis is always required regardless of this setting.
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
    spectrum : ResponseSpectrum, optional
        Pre-computed demand spectrum (T/Sa) for CSM performance-point
        search.  When ``None``, a GB 50011 rare-event spectrum is built
        from the config ``tg`` / ``alpha_max_rare`` (or defaults).
    name : str, optional
    config : dict, optional
        Builder config overrides.
    directions : str, optional
        ``"4dir"`` (default) or a single label ``"+X"``, ``"-X"``,
        ``"+Y"``, ``"-Y"``.  Only used by the RC OpenSeesPy path.
    """

    def __init__(
        self,
        mesh_model: "MeshModel",
        modal_result: AnalysisResult,
        material_type: str = "steel",
        gravity_patterns: Optional[dict[str, float]] = None,
        lateral_load_type: str = "mode1",
        max_disp_val: float = 0.30,
        num_steps: int = 50,
        brace_type: str = "truss",
        brace_sections: Optional[list] = None,
        rs_modal_base_shear: Optional[dict[str, list[float]]] = None,
        spectrum: Optional[ResponseSpectrum] = None,
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
        self.spectrum = spectrum
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

    def _accept_dependency(self, dep_result: AnalysisResult, dep_type: type["Analysis"]) -> None:
        if dep_type is ModalAnalysis and self._modal_result is None:
            self._modal_result = dep_result

    @property
    def requires(self) -> list:
        return [ModalAnalysis]

    @property
    def provides(self) -> set:
        return {"all_out", "fig_csm_plots", "df_compare"}

    def run(self) -> AnalysisResult:
        if self.material_type == "rc":
            if self.config.get("use_tcl_fallback", False):
                import warnings

                warnings.warn(
                    "PushoverAnalysis RC Tcl path is deprecated. "
                    "Set use_tcl_fallback=False (default) to use the "
                    "OpenSeesPy path.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return self._run_rc_tcl_path()
            return self._run_rc_path()
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
            spectrum=self.spectrum,
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

    # ── RC path (OpenSeesPy, preferred) ──────────────────────────

    def _run_rc_path(self) -> AnalysisResult:
        """Run RC pushover via OpenSeesPy (AnalysisBuilder).

        Wraps :func:`~fea_toolkit.opensees.pushover.pushover_rc_openseespy`
        with the same orchestration as :meth:`_run_steel_path` but for
        reinforced-concrete fiber sections.  Supports ``directions``
        ``"4dir"`` or a single label.

        The modal result dict is passed through unchanged; the helper
        extracts ``modal`` / ``shapes`` internally.
        """
        from fea_toolkit.opensees.pushover import pushover_rc_openseespy

        modal_data = self._modal_result.data
        if not isinstance(modal_data, dict):
            modal_data = {"modal": modal_data}

        # RC builder config: user overrides merged over RC defaults.
        rc_config = dict(_PUSHOVER_RC_DEFAULTS)
        rc_config.update(self.config)
        rc_config["create_fiber_sections"] = True

        directions = self.config.get("directions", "4dir")

        result = pushover_rc_openseespy(
            self.mesh_model,
            modal_result=modal_data,
            directions=directions,
            gravity_patterns=self.gravity_patterns,
            lateral_load_type=self.lateral_load_type,
            max_disp=self.max_disp_val,
            num_steps=self.num_steps,
            config=rc_config,
            rs_modal_base_shear=self.rs_modal_base_shear,
            spectrum=self.spectrum,
            verbose=rc_config.get("verbose", False),
            node_mass_overrides=rc_config.get("node_mass_overrides"),
        )

        return AnalysisResult(
            name=self.name,
            analysis_type="PushoverAnalysis",
            data=result,
            metadata={
                "material_type": "rc",
                "path": "openseespy",
                "directions": directions,
                "lateral_load_type": self.lateral_load_type,
                "max_disp_val": self.max_disp_val,
                "num_steps": self.num_steps,
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
        import datetime
        import os
        from pathlib import Path

        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.opensees.builder import pushover_tcl
        from fea_toolkit.opensees.recorder import (
            XaraTclRunner,
            export_mesh_model_to_tcl,
            parse_pushover_results,
        )

        mm: MeshModel = self.mesh_model

        # ── Output directory ─────────────────────────────────────────
        out_dir = Path("output") / f"pushover_rc_{datetime.datetime.now():%Y%m%d_%H%M%S}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Control node and direction resolution ────────────────
        control_node = _find_control_node(mm)

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
        shapes = modal_nested.get("shapes", modal_nested.get("mode_shapes", {}))

        lateral_loads = _build_lateral_loads(
            mm,
            self.lateral_load_type,
            dir_index,
            shapes=shapes,
        )

        # Gravity loads — use MeshModel's computed mass when available
        gravity_loads = _build_gravity_loads(mm)

        # RC config (overrides for fiber sections)
        rc_config = dict(_PUSHOVER_RC_DEFAULTS)
        rc_config.update(self.config)
        rc_config["create_fiber_sections"] = True

        output_prefix = "pushover_rc"

        # Determine base node tags from restrained nodes so the reaction
        # recorders monitor the actual supports (not an implicit node 1).
        base_node_tags = _find_base_node_tags(mm)

        # Generate Tcl suffix with recorder files — DOF matches direction.
        # ``output_prefix`` controls the recorder filenames so they match
        # the paths expected below (``pushover_rc_{disp,bs,reaction}.out``).
        tcl_suffix = pushover_tcl(
            control_node=control_node,
            dof=control_dof,
            max_disp=self.max_disp_val,
            num_steps=self.num_steps,
            lateral_loads=lateral_loads,
            gravity_loads=gravity_loads,
            adaptive=True,
            base_node_tags=base_node_tags,
            output_prefix=output_prefix,
        )

        # Write Tcl script to output directory
        tcl_path = str(out_dir / "model.tcl")
        export_mesh_model_to_tcl(
            mm,
            tcl_path,
            config=rc_config,
            tcl_suffix=tcl_suffix,
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
        # A single base node writes ``{prefix}_reaction.out``; multiple
        # base nodes write per-node ``{prefix}_reaction_{tag}.out`` files
        # (no bare ``{prefix}_reaction.out``), so only pass a reaction
        # path for the single-node case.
        reaction_path = (
            str(out_dir / f"{output_prefix}_reaction.out") if len(base_node_tags) == 1 else None
        )

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
                reaction_arg = (
                    reaction_path if reaction_path and os.path.exists(reaction_path) else None
                )
                parsed = parse_pushover_results(
                    disp_path,
                    bs_path,
                    reaction_arg,
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
                "tcl_path": None
                if tcl_path_removed
                else (tcl_path if os.path.exists(tcl_path) else None),
                "output_dir": None
                if out_dir_removed
                else (str(out_dir) if out_dir.exists() else None),
                "config": self.config,
            },
        )
