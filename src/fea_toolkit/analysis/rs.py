"""Response spectrum analysis — CQC combination.

Wraps :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
RS execution.  Requires the result of a :func:`run_modal_analysis` call.
"""

from typing import TYPE_CHECKING, Optional

from .base import AnalysisResult

if TYPE_CHECKING:
    from fea_toolkit.io.log import PipelineLog
    from fea_toolkit.model.mesh_model import MeshModel


def run_response_spectrum_analysis(
    mesh_model: "MeshModel",
    modal_result: AnalysisResult,
    direction: str,
    T_spec: list[float],
    Sa_spec: list[float],
    damping: float = 0.05,
    n_modes: Optional[int] = None,
    name: str = "ResponseSpectrum",
    config: Optional[dict] = None,
    log: Optional["PipelineLog"] = None,
    record_frame_forces: bool = True,
    record_shell_forces: bool = True,
    element_extraction: Optional[str] = "recorder",
) -> AnalysisResult:
    """Run response spectrum analysis for one direction.

    Args:
        mesh_model: Pre-processed topology.
        modal_result: Result from a preceding :func:`run_modal_analysis`.
        direction: ``"X"`` or ``"Y"``.
        T_spec: Period axis of the demand spectrum.
        Sa_spec: Spectral accelerations corresponding to *T_spec*.
        damping: Damping ratio for CQC (default 0.05).
        n_modes: Number of modes to include; ``None`` (the default) uses every
            mode the modal result provides.
        name: Result label (default ``"ResponseSpectrum"``).
        config: Optional config dict, recorded in the result metadata.
        log: Optional :class:`~fea_toolkit.io.log.PipelineLog` (or a ``bool``)
            forwarded to the :class:`AnalysisBuilder` that runs the RS pass.
        record_frame_forces: When ``True`` (the default), extract the
            CQC-combined per-element local end forces (the ``rs/elem_*`` block
            consumed by the RS force diagram) and store them under
            ``data["element_forces"]``.
        record_shell_forces: When ``True`` (the default), extract the
            CQC-combined per-shell stress resultants (the ``rs/shell_*`` block)
            and store them under ``data["shell_forces"]``.
        element_extraction: Per-mode force-extraction strategy passed to
            :meth:`~fea_toolkit.opensees._runner_rs.AnalysisBuilder.extract_element_rs_forces`
            — ``"recorder"`` (the default; one all-modes pass behind an Element
            recorder) or ``"per_mode"``.  ``None`` defers to the builder config.

    Returns:
        :class:`AnalysisResult` whose ``data`` holds the RS result dict,
        the per-mode ``modal_base_shear``, the ``direction``, the CQC-combined
        ``node_displacements`` (``{node_tag: (dx, dy, dz)}``) for a
        deformed-shape view, and — when requested — the combined
        ``element_forces`` and ``shell_forces`` blocks.

    Examples:
        >>> from fea_toolkit import SAP2000Parser, preprocess_model
        >>> from fea_toolkit.analysis import run_modal_analysis, run_response_spectrum_analysis
        >>> md = SAP2000Parser("model.s2k").parse().get_model_data()
        >>> mesh = preprocess_model(md, {"element_type": "elasticBeamColumn"})
        >>> modal = run_modal_analysis(mesh, n_modes=12)
        >>> T = [0.1, 0.2, 0.5, 1.0, 2.0]   # demand-spectrum periods
        >>> Sa = [0.4, 0.4, 0.3, 0.15, 0.05]  # spectral accelerations
        >>> rs = run_response_spectrum_analysis(mesh, modal, "X", T, Sa)
        >>> rs.data["modal_base_shear"]  # per-mode base shear
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    from ..io.log import coerce_log

    modal_data = modal_result.data
    if not isinstance(modal_data, dict):
        modal_data = {"modal": modal_data}
    modal = modal_data.get("modal", modal_data)
    periods = modal.get("periods", [])
    if not periods:
        raise RuntimeError(
            "run_modal_analysis result has no periods; run "
            "run_modal_analysis before run_response_spectrum_analysis."
        )

    # ``None`` means every mode the modal result provides; otherwise clamp to
    # the periods actually available so the RS analysis never requests more
    # modes than the modal result provides.
    n_modes = len(periods) if n_modes is None else min(n_modes, len(periods))

    builder_config = {"element_type": "elasticBeamColumn", "verbose": False}
    builder_config.update(config or {})
    ab = AnalysisBuilder(mesh_model, builder_config, log=log)
    ab.build_domain()
    ab.compute_seismic_masses()
    # Must run modal on the builder's domain before RS
    ab.run_modal_analysis(num_modes=n_modes, print_results=False)

    rs_result = ab.run_response_spectrum_analysis(
        n_modes,
        periods,
        T_spec,
        Sa_spec,
        direction,
        damping,
        print_results=False,
    )

    # Also collect per-mode base shear
    modal_base_shear = list(rs_result.get("modal_base_shear", []))

    # CQC-combined peak nodal displacements — the deformed-shape field a results
    # view needs.  Computed here (not in the GUI) so every RS consumer shares one
    # computation; kept best-effort, because a model with no usable eigenvectors
    # still reports its base shears.
    node_displacements: dict = {}
    eigenvalues = modal.get("eigenvalues", [])
    if eigenvalues:
        try:
            import numpy as np

            node_displacements = ab.compute_rs_nodal_displacements(
                num_modes=n_modes,
                modal_periods=periods,
                eigenvalues=eigenvalues,
                spectrum_func=lambda T: float(np.interp(T, T_spec, Sa_spec)),
                direction=direction,
                damping_ratio=damping,
            )
        except Exception as exc:
            coerce_log(log).warn(
                "rs",
                f"Could not compute CQC-combined nodal displacements: {exc}",
            )
            node_displacements = {}

    # ── Element-level combined forces (opt-in, default on) ──────────
    # The recorder path is the cheap strategy: one all-modes pass behind an
    # Element recorder instead of ``n_modes × n_elements`` eleResponse calls
    # (verified bit-identical to the per-mode loop).  Best-effort — a model
    # whose element types the recorder cannot lay out still reports its base
    # shears and displacements.
    element_forces: dict = {}
    if record_frame_forces:
        try:
            element_forces = dict(
                ab.extract_element_rs_forces(
                    num_modes=n_modes,
                    modal_periods=periods,
                    spectrum_periods=T_spec,
                    spectrum_accels=Sa_spec,
                    direction=direction,
                    damping_ratio=damping,
                    combination="cqc",
                    extraction=element_extraction,
                    print_results=False,
                )
            )
            element_forces["direction"] = direction
        except Exception as exc:
            coerce_log(log).warn("rs", f"Could not extract RS element forces: {exc}")
            element_forces = {}

    # ── Shell (area) combined resultants (opt-in, default on) ───────
    shell_forces: dict = {}
    if record_shell_forces:
        try:
            shell_forces = ab.extract_rs_shell_forces(
                num_modes=n_modes,
                modal_periods=periods,
                spectrum_periods=T_spec,
                spectrum_accels=Sa_spec,
                direction=direction,
                damping_ratio=damping,
                combination="cqc",
                print_results=False,
            )
            shell_forces["direction"] = direction
        except Exception as exc:
            coerce_log(log).warn("rs", f"Could not extract RS shell forces: {exc}")
            shell_forces = {}

    return AnalysisResult(
        name=name,
        analysis_type="ResponseSpectrumAnalysis",
        data={
            "rs_result": rs_result,
            "modal_base_shear": modal_base_shear,
            "direction": direction,
            "node_displacements": node_displacements,
            "element_forces": element_forces,
            "shell_forces": shell_forces,
        },
        metadata={
            "direction": direction,
            "damping": damping,
            "n_modes": n_modes,
            "config": config or {},
        },
    )
