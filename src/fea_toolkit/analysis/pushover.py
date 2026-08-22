"""Pushover (nonlinear static) analysis — 4 directions with CSM.

Supports steel (OpenSeesPy) and RC (OpenSeesPy or Tcl/Xara export)
nonlinear pushover.

Steel path
    Wraps :func:`~fea_toolkit.opensees.pushover.run_pushover_4dir`.
    Uses :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
    with steel fiber sections, Hysteretic brace trusses, and CSM.

RC path (OpenSeesPy, preferred)
    Wraps :func:`~fea_toolkit.opensees.pushover.pushover_rc_openseespy`.
    Uses ``forceBeamColumn`` fiber sections (Concrete01, Steel02) and
    optional nonlinear shell walls.  Supports single-direction or
    four-direction push with CSM post-processing.  Dispatched from
    :func:`run_pushover_analysis` when ``material_type="rc"`` (unless
    ``config["use_tcl_fallback"]`` is set).

RC path (Tcl/Xara export, alternate backend)
    Exports the model to Tcl via
    :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`,
    appends ``pushover_tcl()`` commands, and runs via
    :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.
    Orchestrated in :mod:`fea_toolkit.analysis.pushover_tcl`.
"""

from typing import TYPE_CHECKING, Optional

from fea_toolkit.analysis.base import (
    _PUSHOVER_RC_DEFAULTS,
    AnalysisResult,
)
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


def run_pushover_analysis(
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
    directions: str = "4dir",
    name: str = "Pushover",
    config: Optional[dict] = None,
) -> AnalysisResult:
    """Run pushover analysis with CSM evaluation.

    Always requires the result of :func:`run_modal_analysis` — not just
    for the ``mode1`` lateral load pattern.  The CSM post-processing
    needs the fundamental period and modal effective mass to convert the
    pushover curve to an ADRS capacity spectrum and to locate the
    performance point.  ``lateral_load_type`` only controls the *shape*
    of the lateral load distribution, not whether modal analysis runs.

    Args:
        mesh_model: Pre-processed topology from the Preprocessor.
        modal_result: Result from a preceding :func:`run_modal_analysis`.
        material_type: ``"steel"`` (default) or ``"rc"``.  Steel uses
            OpenSeesPy directly with Hysteretic hinges.  RC runs in
            OpenSeesPy with ``forceBeamColumn`` + fiber sections, unless
            ``config["use_tcl_fallback"]`` is set to export to Tcl and
            run via Xara (alternate backend).
        gravity_patterns: Dict mapping pattern name → scale factor.
        lateral_load_type: One of ``"uniform"``, ``"triangular"``,
            ``"mode1"`` (default).
        max_disp_val: Maximum control displacement (m, default 0.30).
        num_steps: Number of push increments (default 50).
        brace_type: ``"truss"`` for steel braces, ``"beam"`` for elastic.
        brace_sections: Section names to treat as braces.
        rs_modal_base_shear: Per-mode RS base shear ``{"X": [...],
            "Y": [...]}`` for mode1 pattern validation (diagnostic only).
        spectrum: Pre-computed demand spectrum (T/Sa) for CSM
            performance-point search.  When ``None``, a GB 50011
            rare-event spectrum is built from the config ``tg`` /
            ``alpha_max_rare`` (or defaults).
        directions: ``"4dir"`` (default) or a single label ``"+X"``,
            ``"-X"``, ``"+Y"``, ``"-Y"``.  Only used by the RC
            OpenSeesPy path.
        name: Result label (default ``"Pushover"``).
        config: Builder config overrides (merged over the
            material-type defaults).

    Returns:
        :class:`AnalysisResult` whose ``data`` holds the pushover result
        dict (capacity curve, ADRS, performance point).
    """
    config = config or {}

    # ── RC paths ────────────────────────────────────────────────────
    if material_type == "rc":
        if config.get("use_tcl_fallback", False):
            from fea_toolkit.analysis.pushover_tcl import run_rc_pushover_tcl

            data, metadata = run_rc_pushover_tcl(
                mesh_model,
                _resolve_modal_data(modal_result),
                _build_rc_config(config),
                lateral_load_type=lateral_load_type,
                max_disp_val=max_disp_val,
                num_steps=num_steps,
            )
            return AnalysisResult(
                name=name, analysis_type="PushoverAnalysis", data=data, metadata=metadata
            )

        from fea_toolkit.opensees.pushover import pushover_rc_openseespy

        modal_data = _resolve_modal_data(modal_result)
        rc_config = _build_rc_config(config)
        dirs = rc_config.get("directions", directions)

        result = pushover_rc_openseespy(
            mesh_model,
            modal_result=modal_data,
            directions=dirs,
            gravity_patterns=gravity_patterns,
            lateral_load_type=lateral_load_type,
            max_disp=max_disp_val,
            num_steps=num_steps,
            config=rc_config,
            rs_modal_base_shear=rs_modal_base_shear,
            spectrum=spectrum,
            verbose=rc_config.get("verbose", False),
            node_mass_overrides=rc_config.get("node_mass_overrides"),
        )
        return AnalysisResult(
            name=name,
            analysis_type="PushoverAnalysis",
            data=result,
            metadata={
                "material_type": "rc",
                "path": "openseespy",
                "directions": dirs,
                "lateral_load_type": lateral_load_type,
                "max_disp_val": max_disp_val,
                "num_steps": num_steps,
                "config": config,
            },
        )

    # ── Steel path (OpenSeesPy) ─────────────────────────────────────
    from fea_toolkit.opensees.pushover import run_pushover_4dir

    modal_data = _resolve_modal_data(modal_result)

    result = run_pushover_4dir(
        mesh_model,
        modal_result=modal_data,
        gravity_patterns=gravity_patterns,
        lateral_load_type=lateral_load_type,
        max_disp_val=max_disp_val,
        num_steps=num_steps,
        brace_type=brace_type,
        brace_sections=brace_sections,
        rs_modal_base_shear=rs_modal_base_shear,
        spectrum=spectrum,
        verbose=config.get("verbose", False),
    )

    return AnalysisResult(
        name=name,
        analysis_type="PushoverAnalysis",
        data=result,
        metadata={
            "material_type": "steel",
            "lateral_load_type": lateral_load_type,
            "max_disp_val": max_disp_val,
            "num_steps": num_steps,
            "brace_type": brace_type,
            "config": config,
        },
    )
