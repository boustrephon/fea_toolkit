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

RC path (Tcl/Xara export, alternate backend)
    Exports the model to Tcl via
    :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`,
    appends ``pushover_tcl()`` commands, and runs via
    :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.
    Uses ``forceBeamColumn`` with fiber sections (Concrete01, Steel02).
    Orchestrated in :mod:`fea_toolkit.analysis.pushover_tcl`.
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

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


# ── Shared RC-config / modal-data helpers ─────────────────────────


def _build_rc_config(config: Optional[dict]) -> dict:
    """Merge user config over the RC defaults with fiber sections forced on."""
    rc_config = dict(_PUSHOVER_RC_DEFAULTS)
    rc_config.update(config or {})
    rc_config["create_fiber_sections"] = True
    return rc_config


def _resolve_modal_data(modal_result: AnalysisResult) -> dict:
    """Return the modal result data as a dict (unwrapping a bare value)."""
    modal_data = modal_result.data
    if not isinstance(modal_data, dict):
        modal_data = {"modal": modal_data}
    return modal_data


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
        run via Xara (alternate backend).
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
        # Material-type-specific defaults take precedence over the steel
        # defaults applied by Analysis.__init__ (which calls
        # ``self.defaults()``).  The caller's *config* overrides are then
        # merged on top so user-supplied keys win.
        material_defaults = self.defaults_for(self.material_type)
        if config:
            material_defaults.update(config)
        self.config = material_defaults

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
                return self._run_rc_tcl_path()
            return self._run_rc_path()
        return self._run_steel_path()

    # ── Steel path (OpenSeesPy) ───────────────────────────────────

    def _run_steel_path(self) -> AnalysisResult:
        from fea_toolkit.opensees.pushover import run_pushover_4dir

        modal_data = _resolve_modal_data(self._modal_result)

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

        modal_data = _resolve_modal_data(self._modal_result)

        # RC builder config: user overrides merged over RC defaults.
        rc_config = _build_rc_config(self.config)

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
        """Run RC pushover via the Tcl/Xara backend (alternate to OpenSeesPy).

        Delegates the full orchestration to
        :func:`~fea_toolkit.analysis.pushover_tcl.run_rc_pushover_tcl`
        and wraps the returned ``(data, metadata)`` in an ``AnalysisResult``.
        """
        from fea_toolkit.analysis.pushover_tcl import run_rc_pushover_tcl

        data, metadata = run_rc_pushover_tcl(
            self.mesh_model,
            _resolve_modal_data(self._modal_result),
            _build_rc_config(self.config),
            lateral_load_type=self.lateral_load_type,
            max_disp_val=self.max_disp_val,
            num_steps=self.num_steps,
        )
        return AnalysisResult(
            name=self.name,
            analysis_type="PushoverAnalysis",
            data=data,
            metadata=metadata,
        )
