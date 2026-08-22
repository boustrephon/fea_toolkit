"""
Report generation orchestration — config-driven pipeline.

Uses the two-stage Preprocessor + AnalysisBuilder architecture.

Not to be confused with :mod:`fea_toolkit.io.report` (pandas summary
tables) or :mod:`fea_toolkit.plotting.report` (matplotlib figures) — this
module orchestrates the whole report run.

The central entry point is :func:`generate_report`, which accepts a
parsed ``SAPModelData``, an optional pre-computed ``MeshModel``, and a
configuration dict, then runs all enabled analyses and returns a
standardised results dictionary.

Typical usage::

    from fea_toolkit.io.s2k_parser import SAP2000Parser
    from fea_toolkit.report import generate_report

    parser = SAP2000Parser("model.s2k")
    parser.parse()
    md = parser.get_model_data()

    results = generate_report(md, config={
        "spectrum": {"code": "GB50011", "intensity": 7},
    })
"""

import pickle
from pathlib import Path
from typing import Optional

import pandas as pd

from fea_toolkit.analysis import (
    run_modal_analysis,
    run_pushover_analysis,
    run_response_spectrum_analysis,
    run_static_analysis,
)
from fea_toolkit.analysis.linear import static_load_verification, wind_sanity_check
from fea_toolkit.io.log import AnalysisLog
from fea_toolkit.io.report import (
    bounding_box,
    material_summary,
    pushover_comparison_table,
    section_summary,
)
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import SAPModelData
from fea_toolkit.model.selection import Selection
from fea_toolkit.model.storey_response import compute_linear_storey_responses
from fea_toolkit.model.stories import identify_stories, plot_stories, stories_dataframe
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.plotting.report import (
    plot_csm_4panel,
    plot_storey_displacements,
    plot_storey_forces,
)
from fea_toolkit.plotting.viz import plot_model_comparison
from fea_toolkit.spectrum import _build_spectrum, plot_seismic_spectrum
from fea_toolkit.utils import (
    build_gravity_patterns,
    deep_merge,
    infer_loads,
    pick_wind,
)

# ────────────────────────────────────────────────────────────────────
# Generic defaults — override at the call site per-project.
# Never include project-specific values (spectrum codes, paths, etc.)
# here; those belong in the calling script's own ``_DEFAULT_CONFIG``.
# ────────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG: dict = {
    "general": {
        "n_modes": 12,
        "verbose": True,
        "force_recompute": False,
    },
    "loads": {
        "auto_detect": True,
        "gravity": None,
        "wind_x": None,
        "wind_y": None,
    },
    "spectrum": {},
    "pushover": {
        "patterns": ["uniform", "triangular", "mode1"],
        "directions": ["+X", "-X", "+Y", "-Y"],
        "max_disp": 0.30,
        "num_steps": 50,
        "brace_type": "truss",
        "brace_sections": None,
    },
    "linear": {"run": True},
    "checks": {"run": True},
    "stories": {"run": True},
    "storey_response": {"run": True},
    "static_verification": {"run": True},
    "model_viewer": {"enabled": False, "off_screen": True},
    "analysis_log": {"enabled": True},
}


# ────────────────────────────────────────────────────────────────────
# Orchestrator
# ────────────────────────────────────────────────────────────────────


def generate_report(
    md: SAPModelData,
    mesh_model: Optional[MeshModel] = None,
    config: Optional[dict] = None,
    out_dir: Optional[str] = None,
    **overrides,
) -> dict:
    """Run the full two-stage analysis pipeline and return the canonical
    report result bundle.

    The returned dictionary is the shared repository-owned result contract.
    It is intentionally generic and stable so that the NPZ writer and any
    downstream helper can consume it without re-implementing the analysis
    orchestration.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data from
        :func:`~fea_toolkit.io.s2k_parser.SAP2000Parser`.
    mesh_model : MeshModel, optional
        Pre-processed mesh model.  When *None* (default), the
        Preprocessor runs automatically.  Pass a pre-built
        ``MeshModel`` to skip preprocessing (useful when calling
        ``generate_report`` multiple times with different configs).
    config : dict, optional
        Configuration dictionary.  Keys follow ``_DEFAULT_CONFIG``.
        Deep-merged on top of the defaults so you only need to
        provide overrides.
    out_dir : str, optional
        Output directory for cached results and exported figures.
        Defaults to ``./output``.
    **overrides
        Flat override keys using ``__`` as a nesting separator,
        e.g. ``general__n_modes=6``.  Applied after *config*.

    Returns
    -------
    dict
        Standardised result dictionary.  Key sections:

        * ``config`` — merged configuration used for this run
        * ``md`` — original ``SAPModelData``
        * ``mesh_model`` — the ``MeshModel`` (preprocessed)
        * ``connectivity`` / ``self_weight`` — model checks
        * ``modal`` / ``df_modal`` — modal analysis
        * ``df_linear`` / ``rs_modal_x`` / ``rs_modal_y`` — linear + RS
        * ``all_out`` / ``fig_csm_plots`` / ``df_compare`` — pushover + CSM
        * ``df_sections`` / ``df_materials`` — section & material tables
        * ``fig_spec`` — seismic spectrum figure
        * ``bounding_box`` / ``wind_check`` / ``inferred_loads`` — metadata
        * ``spec_label`` / ``alpha_max`` / ``tg`` — spectrum parameters
        * ``model_comparison`` — PyVista comparison (if enabled)
        * ``model_stories`` / ``df_stories`` / ``stories_png`` — storeys
        * ``df_storey_disp`` / ``df_storey_drift`` — storey response
          ``df_storey_shear`` / ``df_storey_moment``
        * ``fig_storey_forces`` / ``fig_storey_disp`` — storey figures
        * ``df_load_verify`` — static load verification table
        * ``brace_ids`` — list of brace-element IDs
        * ``analysis_log`` — :class:`~fea_toolkit.io.log.AnalysisLog`
          instance (only present when logging was enabled)
    """
    # ── Config merging ───────────────────────────────────────────
    cfg = deep_merge(_DEFAULT_CONFIG.copy(), config or {})

    flat = {}
    for k, v in overrides.items():
        parts = k.split("__", 1)
        if len(parts) == 2:
            flat.setdefault(parts[0], {})[parts[1]] = v
        else:
            flat[k] = v
    if flat:
        cfg = deep_merge(cfg, flat)

    gen_cfg = cfg.get("general", {})
    verbose = gen_cfg.get("verbose", True)
    force_recompute = gen_cfg.get("force_recompute", False)
    n_modes = gen_cfg.get("n_modes", 12)
    push_cfg = cfg.get("pushover") or {}
    load_cfg = cfg.get("loads") or {}
    spec_cfg = cfg.get("spectrum") or {}
    push_spec_cfg = push_cfg.get("spectrum") or spec_cfg
    zeta_cfg = spec_cfg.get("damping", 0.05)
    log_cfg = cfg.get("analysis_log", {"enabled": True})

    log = AnalysisLog() if log_cfg.get("enabled", True) else None

    resolved_out = Path(out_dir or "output")
    resolved_out.mkdir(parents=True, exist_ok=True)

    # ── Cache check ──────────────────────────────────────────────
    model_stem = Path(cfg.get("model", {}).get("path", "")).stem
    cache_path = resolved_out / f"{model_stem}_results.pkl"

    if not force_recompute and cache_path.exists():
        if verbose:
            print(f"Loading cached results from {cache_path}")
        if log:
            log.info("cache", "reusing cached results")
        with open(cache_path, "rb") as f:
            return dict(pickle.load(f))

    if log:
        log.info("config", f"Model: {model_stem}")

    # ── Auto-detect loads ────────────────────────────────────────
    raw_tables = getattr(md, "_raw_tables", {})
    inferred = infer_loads(raw_tables) if load_cfg.get("auto_detect", True) else {}
    if load_cfg.get("gravity") is None and inferred:
        load_cfg["gravity"] = build_gravity_patterns(inferred)
    if load_cfg.get("wind_x") is None and inferred.get("wind"):
        load_cfg["wind_x"] = pick_wind(inferred, "+X")
    if load_cfg.get("wind_y") is None and inferred.get("wind"):
        load_cfg["wind_y"] = pick_wind(inferred, "+Y")

    # ── Preprocessor (runs ONCE) ─────────────────────────────────
    if mesh_model is None:
        if verbose:
            print("Running Preprocessor (topology work)...")
        mesh_model = preprocess_model(md, cfg.get("builder", {}))
        if log:
            log.info(
                "preprocessor",
                f"{len(mesh_model.nodes)} nodes, "
                f"{len(mesh_model.frame_elements)} frames, "
                f"{len(mesh_model.area_elements)} areas",
            )
        if verbose:
            print(
                f"  MeshModel created: {len(mesh_model.nodes)} nodes, "
                f"{len(mesh_model.frame_elements)} frames, "
                f"{len(mesh_model.area_elements)} areas"
            )
    else:
        if verbose:
            print("Using pre-computed MeshModel (skipping Preprocessor)")
        if log:
            log.info("preprocessor", "reused existing MeshModel")

    # ── Brace summary ────────────────────────────────────────────
    sel = Selection.from_brace_sections(md)
    brace_ids = sel.get_frame_ids(md)

    # ── Static load verification ─────────────────────────────────
    df_load_verify = pd.DataFrame()
    if cfg.get("static_verification", {}).get("run", True):
        if verbose:
            print("Running static load verification...")
        try:
            df_load_verify = static_load_verification(md, mesh_model)
            _lv_patterns = cfg.get("static_verification", {}).get("patterns")
            if _lv_patterns and not df_load_verify.empty:
                df_load_verify = df_load_verify[df_load_verify["Load Pattern"].isin(_lv_patterns)]
            if log:
                log.info("load_verify", f"{len(df_load_verify)} patterns")
            if verbose:
                print(f"  Verified {len(df_load_verify)} load patterns")
        except Exception as e:
            if log:
                log.error("load_verify", str(e))
            print(f"  Static load verification failed: {e}")

    # ── Connectivity check ───────────────────────────────────────
    connectivity = None
    self_weight = None
    if cfg.get("checks", {}).get("run", True):
        if verbose:
            print("Running connectivity check...")
        try:
            from fea_toolkit.model.checks import check_model_connectivity

            connectivity = check_model_connectivity(md)
            if verbose:
                print(f"  {connectivity['summary']}")
                if connectivity.get("orphan_nodes"):
                    print(f"  ⚠ {len(connectivity['orphan_nodes'])} orphan node(s)")
                if connectivity.get("shell_only_base_nodes"):
                    print(
                        f"  ⚠ {len(connectivity['shell_only_base_nodes'])} shell-only base node(s)"
                    )
        except Exception as e:
            if log:
                log.warning("connectivity", str(e))
            print(f"  Connectivity check failed: {e}")

        # ── Self-weight check ────────────────────────────────────
        if verbose:
            print("Checking self-weight consistency...")
        try:
            fu_md = md.units.get("F", "N")
            applied = 0.0
            expected = 0.0
            if not df_load_verify.empty:
                dead = df_load_verify[df_load_verify["Load Pattern"] == "DEAD"]
                if len(dead):
                    r = dead.iloc[0]
                    applied = abs(r.get(f"Reaction Fz ({fu_md})", 0))
                    expected = abs(r.get(f"Applied Fz ({fu_md})", 0))
            passed = abs(expected - applied) / max(expected, 1.0) < 0.02
            self_weight = {
                "expected": expected,
                "applied": applied,
                "passed": passed,
                "by_section": {},
            }
            if verbose:
                print(f"  Expected: {expected:.0f} {fu_md}")
                print(f"  Applied:  {applied:.0f} {fu_md}")
                print(f"  Status:   {'✓ PASS' if passed else '✗ FAIL'}")
        except Exception as e:
            if log:
                log.warning("self_weight", str(e))
            print(f"  Self-weight check failed: {e}")

    # ── Build spectrum (common to both paths) ────────────────────
    if spec_cfg:
        T_spec, Sa_spec, alpha_max, tg, zeta, spec_label = _build_spectrum(spec_cfg)
    else:
        T_spec, Sa_spec, alpha_max, tg, zeta, spec_label = (
            [],
            [],
            0.0,
            0.25,
            zeta_cfg,
            "",
        )

    if push_spec_cfg:
        (_, _, push_alpha_max, push_tg, push_zeta, _) = _build_spectrum(push_spec_cfg)
    else:
        push_alpha_max, push_tg, push_zeta = alpha_max, tg, zeta

    # ── Core analyses (modal, linear, RS, pushover) ──────────────
    # Explicit pipeline order: modal first (feeds RS, pushover, CSM),
    # then static linear, response spectrum, and pushover.  Each step
    # is a module-level function returning an AnalysisResult.
    if verbose:
        print("Running analyses...")

    _man_results: dict = {}

    # Modal (always required)
    _man_results["ModalAnalysis"] = run_modal_analysis(
        mesh_model, n_modes=n_modes, name="ModalAnalysis", config={"verbose": verbose}
    )
    modal_result = _man_results["ModalAnalysis"].data
    modal = modal_result["modal"]

    # Static linear (if enabled)
    if cfg.get("linear", {}).get("run", True):
        _man_results["StaticAnalysis"] = run_static_analysis(
            mesh_model,
            md,
            spec_cfg=spec_cfg,
            linear_cfg=cfg.get("linear"),
            name="StaticAnalysis",
        )

    # Response spectrum (one per direction)
    if T_spec and Sa_spec:
        for rs_dir in ("X", "Y"):
            _man_results[f"RS-{rs_dir}"] = run_response_spectrum_analysis(
                mesh_model,
                modal_result=_man_results["ModalAnalysis"],
                direction=rs_dir,
                T_spec=T_spec,
                Sa_spec=Sa_spec,
                damping=0.05,
                n_modes=n_modes,
                name=f"RS-{rs_dir}",
            )

    # Pushover (one per pattern)
    # Note: rs_modal_base_shear is not available until after modal + RS
    # run, so it's omitted here (the mode1 diagnostic is skipped).
    if cfg.get("pushover") and push_cfg.get("patterns") and push_cfg.get("directions"):
        for pattern in push_cfg["patterns"]:
            _man_results[f"Pushover-{pattern}"] = run_pushover_analysis(
                mesh_model,
                modal_result=_man_results["ModalAnalysis"],
                gravity_patterns=load_cfg["gravity"],
                lateral_load_type=pattern,
                max_disp_val=push_cfg["max_disp"],
                num_steps=push_cfg["num_steps"],
                brace_type=push_cfg.get("brace_type", "beam"),
                brace_sections=push_cfg.get("brace_sections"),
                name=f"Pushover-{pattern}",
            )

    if log:
        log.info("analyses", f"ran {len(_man_results)} analyses")

    # ── Extract results ──────────────────────────────────────
    if log:
        log.info("modal", f"{n_modes} modes, T1={modal['periods'][0]:.3f}s")

    # Build participation DataFrame (same format as inline path)
    mp = modal["modal_props"]
    pct_cols = ["Mx (%)", "My (%)", "Mz (%)", "Rx (%)", "Ry (%)", "Rz (%)"]
    _mp_keys = {
        "Mx (%)": "partiMassRatiosMX",
        "My (%)": "partiMassRatiosMY",
        "Mz (%)": "partiMassRatiosMZ",
        "Rx (%)": "partiMassRatiosRMX",
        "Ry (%)": "partiMassRatiosRMY",
        "Rz (%)": "partiMassRatiosRMZ",
    }
    modal_rows = []
    for i in range(modal["num_modes"]):
        row = {
            "Mode": i + 1,
            "Period (s)": round(modal["periods"][i], 4),
            "Freq (Hz)": round(modal["frequencies"][i], 4),
        }
        for col, key in _mp_keys.items():
            row[col] = round(mp.get(key, [0])[i], 2)
        modal_rows.append(row)
    df_modal = pd.DataFrame(modal_rows)
    for c in pct_cols:
        df_modal[c] = df_modal[c].apply(lambda v: f"{v:.2f}")
    sum_row = {"Mode": "<strong>SUM</strong>", "Period (s)": "\u2014", "Freq (Hz)": "\u2014"}
    for col in pct_cols:
        sum_row[col] = f"{df_modal[col].astype(float).sum():.2f}"
    df_modal = pd.concat([df_modal, pd.DataFrame([sum_row])], ignore_index=True)

    # Static linear
    _static_ar = _man_results.get("StaticAnalysis")
    df_linear = _static_ar.data.get("df_linear", pd.DataFrame()) if _static_ar else pd.DataFrame()

    # RS per-mode base shear
    rs_modal_x = (
        _man_results["RS-X"].data.get("modal_base_shear", []) if "RS-X" in _man_results else []
    )
    rs_modal_y = (
        _man_results["RS-Y"].data.get("modal_base_shear", []) if "RS-Y" in _man_results else []
    )

    # Pushover
    all_out = {}
    for _name, _ar in _man_results.items():
        if _name.startswith("Pushover-"):
            all_out[_name.split("-", 1)[1]] = _ar.data

    # CSM plots + comparison table
    fig_csm_plots: dict = {}
    df_compare = pd.DataFrame()
    if all_out:
        if log:
            log.info("pushover", "done")
        if verbose:
            print("Generating CSM plots...")
        for pat in push_cfg.get("patterns", []):
            if pat in all_out:
                if log:
                    log.info("csm_plot", f"{pat} — generating")
                fig_csm_plots[pat] = plot_csm_4panel(
                    all_out[pat],
                    modal,
                    tg=push_tg,
                    zeta=push_zeta,
                    alpha_max_rare=push_alpha_max,
                    out_dir=str(resolved_out),
                )
                if log:
                    log.info("csm_plot", f"{pat} — done")
        df_compare = pushover_comparison_table(
            all_out, df_linear, patterns=push_cfg.get("patterns", [])
        )
    else:
        if verbose:
            print("Pushover: skipped (no manager results)")
        if log:
            log.info("pushover", "skipped")

    # ── Summary tables (common) ──────────────────────────────────
    df_sections = section_summary(md)
    df_materials = material_summary(md)
    fig_spec = plot_seismic_spectrum(spec_cfg, modal)

    # ── Model viewer ─────────────────────────────────────────────
    viewer_cfg = cfg.get("model_viewer", {"enabled": False})
    model_comparison = None
    if viewer_cfg.get("enabled", False):
        if verbose:
            print("Generating model comparison views...")
        viewer_out = viewer_cfg.get("out_dir") or str(resolved_out)
        model_comparison = plot_model_comparison(
            md,
            mesh_model=mesh_model,
            out_dir=viewer_out,
            off_screen=viewer_cfg.get("off_screen", True),
        )

    # ── Storey detection ─────────────────────────────────────────
    story_cfg = cfg.get("stories", {"run": True})
    model_stories = None
    df_stories = None
    stories_png = None
    if story_cfg.get("run", True):
        if verbose:
            print("Detecting storey levels...")
        try:
            model_stories = identify_stories(md, raw_tables=raw_tables)
            if model_stories:
                df_stories = stories_dataframe(model_stories)
                fig = plot_stories(md, model_stories, off_screen=True, window_size=(1400, 900))
                if fig:
                    stories_png = str(resolved_out / "model_stories.png")
                    fig.savefig(stories_png, dpi=150, bbox_inches="tight")
                    if verbose:
                        print(f"  Saved storey visualisation to {stories_png}")
        except Exception as e:
            if log:
                log.warning("storey_detection", str(e))
            print(f"  Storey detection failed: {e}")

    # ── Storey response ──────────────────────────────────────────
    story_response_cfg = cfg.get("storey_response", {"run": True})
    df_storey_disp = pd.DataFrame()
    df_storey_drift = pd.DataFrame()
    df_storey_shear = pd.DataFrame()
    df_storey_moment = pd.DataFrame()
    fig_storey_forces = None
    fig_storey_disp = None
    if story_response_cfg.get("run", True) and model_stories:
        if verbose:
            print("Computing storey-level structural responses...")
        try:
            sr = compute_linear_storey_responses(
                md,
                mesh_model,
                model_stories,
                cfg.get("linear", {}),
                df_linear,
                modal_result,
                T_spec if spec_cfg else None,
                Sa_spec if spec_cfg else None,
                n_modes=n_modes,
            )
            df_storey_disp = sr["df_disp"]
            df_storey_drift = sr["df_drift"]
            df_storey_shear = sr["df_shear"]
            df_storey_moment = sr["df_moment"]

            # Filter to only specified cases
            linear_cases = set()
            for entry in cfg.get("linear", {}).get("cases", []):
                if isinstance(entry, dict):
                    linear_cases.update(entry.keys())
                elif isinstance(entry, str):
                    linear_cases.add(entry)
            keep_cases = linear_cases | {"RS-X", "RS-Y"}
            _struct_cols = {"Storey", "Elevation", "Elevation (m)"}
            if keep_cases:
                for _df in [
                    df_storey_disp,
                    df_storey_drift,
                    df_storey_shear,
                    df_storey_moment,
                ]:
                    _drop = [
                        c for c in _df.columns if c not in keep_cases and c not in _struct_cols
                    ]
                    if _drop:
                        _df.drop(columns=_drop, inplace=True, errors="ignore")

            if not df_storey_disp.empty and not df_storey_drift.empty:
                if log:
                    log.info("storey_fig", "displacement — generating")
                fig_storey_disp = plot_storey_displacements(df_storey_disp, df_storey_drift)
                if log:
                    log.info("storey_fig", "displacement — done")
            if not df_storey_shear.empty and not df_storey_moment.empty:
                if log:
                    log.info("storey_fig", "forces — generating")
                fig_storey_forces = plot_storey_forces(df_storey_shear, df_storey_moment)
                if log:
                    log.info("storey_fig", "forces — done")
            if log:
                log.info(
                    "storey_response",
                    f"disp={len(df_storey_disp)}, "
                    f"drift={len(df_storey_drift)}, "
                    f"shear={len(df_storey_shear)}, "
                    f"moment={len(df_storey_moment)}",
                )
            if verbose:
                print(
                    f"  Storey response: disp={len(df_storey_disp)}, "
                    f"drift={len(df_storey_drift)}, "
                    f"shear={len(df_storey_shear)}, "
                    f"moment={len(df_storey_moment)}"
                )
        except Exception as e:
            if log:
                log.error("storey_response", str(e))
            print(f"  Storey response failed: {e}")
            import traceback

            traceback.print_exc()

    # ── Assemble results dict ────────────────────────────────────
    # Determine first pattern for convenience references
    patterns = push_cfg.get("patterns", [])
    first_pattern = patterns[0] if patterns else None

    results: dict = {
        "config": cfg,
        "md": md,
        "mesh_model": mesh_model,
        "connectivity": connectivity,
        "self_weight": self_weight,
        "modal": modal_result,
        "df_modal": df_modal,
        "df_linear": df_linear,
        "rs_modal_x": rs_modal_x,
        "rs_modal_y": rs_modal_y,
        "all_out": all_out,
        "fig_csm_plots": fig_csm_plots,
        "fig_spec": fig_spec,
        "df_compare": df_compare,
        "df_sections": df_sections,
        "df_materials": df_materials,
        "brace_ids": brace_ids,
        "bounding_box": bounding_box(md),
        "wind_check": (
            wind_sanity_check(md, df_linear) if not df_linear.empty else "Linear analysis skipped"
        ),
        "inferred_loads": inferred,
        "spec_label": spec_label,
        "alpha_max": alpha_max,
        "tg": tg,
        "out_dir": str(resolved_out),
        "model_comparison": model_comparison,
        "model_stories": model_stories,
        "df_stories": df_stories,
        "stories_png": stories_png,
        "df_load_verify": df_load_verify,
        "df_storey_disp": df_storey_disp,
        "df_storey_drift": df_storey_drift,
        "df_storey_shear": df_storey_shear,
        "df_storey_moment": df_storey_moment,
        "fig_storey_forces": fig_storey_forces,
        "fig_storey_disp": fig_storey_disp,
    }
    # Convenience: a single fig_csm for the first pattern
    if first_pattern and first_pattern in fig_csm_plots:
        results["fig_csm"] = fig_csm_plots[first_pattern]
    else:
        results["fig_csm"] = None

    if log:
        results["analysis_log"] = log

    # ── Cache ────────────────────────────────────────────────────
    with open(cache_path, "wb") as f:
        pickle.dump(results, f)
    if verbose:
        print(f"Results cached to {cache_path}")
    if log:
        log.info("cache", f"saved to {cache_path.name}")

    return results
