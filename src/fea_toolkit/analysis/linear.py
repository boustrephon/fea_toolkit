"""Static linear analysis runners.

These functions execute the OpenSees solver (or combine its results) for the
static-linear verification workflow.  They live in ``analysis/`` (not ``io/``)
because they run analyses rather than only formatting data.

The pandas *summary* helpers they depend on (``bounding_box``,
``load_pattern_totals``) remain in :mod:`fea_toolkit.io.report`.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

try:
    import pandas as pd
except ImportError:  # pragma: no cover — pandas is optional (Rhino 8 CPython)

    class _MissingPandas:
        """Raise a clear error when a linear-analysis helper needs pandas.

        pandas is not a required dependency of the toolkit (see
        ``pyproject.toml`` core deps) and is absent from Rhino 8's
        bundled CPython.  The module must still import so that
        ``import fea_toolkit.analysis`` works without it; only actually
        calling a helper that needs a DataFrame raises.
        """

        def __getattr__(self, _name: str):
            raise RuntimeError(
                "linear analysis helpers require pandas, which is not "
                "installed in this Python environment (pip install pandas)."
            )

    pd = _MissingPandas()  # type: ignore[assignment]

from ..io.report import bounding_box
from ..model.sap_data import SAPModelData
from .case_listing import list_static_cases


def wind_sanity_data(
    md,
    df_linear,
    wind_case_x: str = "Wind+X",
    wind_case_y: str = "Wind+Y",
) -> dict:
    """Return structured wind-load sanity-check data.

    **Basis** — this is a plausibility test that the applied wind load
    scales with the building envelope, not a code/wind-tunnel pressure.
    Every number derives from the model itself:

    - ``x_face`` = bounding-box ``y_span`` × ``z_span`` — the face normal to
      global X (the windward face for the +X wind case);
    - ``y_face`` = bounding-box ``x_span`` × ``z_span`` — the face normal to
      global Y;
    - ``fx`` / ``fy`` = the absolute base reaction (``Fx`` from the
      ``Wind+X`` case, ``Fy`` from ``Wind+Y``) read from the linear
      analysis results;
    - ``p_x`` = ``fx / x_face`` and ``p_y`` = ``fy / y_face`` — the implied
      **mean pressure** on each face.

    ``within_10pct`` is ``True`` when ``|p_x - p_y| / max(p_x, p_y) < 0.1``
    — the two implied pressures agree, the signature of a consistent wind
    load set.  A large mismatch usually flags a missing/duplicated wind
    area or a load applied to the wrong face.  All quantities are in the
    model's own unit system.

    This is the structured form of :func:`wind_sanity_check`, so callers
    that build their own tables (e.g. the model review) can reuse the same
    numbers.

    Args:
        md: Model data (for node coordinates and units).
        df_linear: Linear analysis results; must contain ``Case``, ``Fx``,
            ``Fy`` columns.
        wind_case_x: Case name for the X wind load pattern.
        wind_case_y: Case name for the Y wind load pattern.

    Returns:
        Keys ``rows`` (unit-labelled table rows: ``Face``, ``Area``,
        ``Total``, ``Pressure``), ``within_10pct`` (bool), the raw values
        ``x_face`` / ``y_face`` / ``fx`` / ``fy`` / ``p_x`` / ``p_y``, the
        ``force_unit`` / ``length_unit`` labels and the ``bounding_box``.
    """
    bb = bounding_box(md)

    x_face = bb["y_span"] * bb["z_span"]
    y_face = bb["x_span"] * bb["z_span"]
    fu = md.units.get("F", "?")
    lu = md.units.get("L", "m")

    wind_x = df_linear[df_linear["Case"] == wind_case_x]
    wind_y = df_linear[df_linear["Case"] == wind_case_y]

    fx = abs(wind_x["Fx"].values[0]) if len(wind_x) else 0
    fy = abs(wind_y["Fy"].values[0]) if len(wind_y) else 0

    p_x = fx / x_face if x_face > 0 else 0
    p_y = fy / y_face if y_face > 0 else 0

    rows = [
        {
            "Face": "Wind +X (Y-Z face)",
            f"Area ({lu}\u00b2)": f"{x_face:.0f}",
            f"Total ({fu})": f"{fx:,.0f}",
            f"Pressure ({fu}/{lu}\u00b2)": f"{p_x:.2f}",
        },
        {
            "Face": "Wind +Y (X-Z face)",
            f"Area ({lu}\u00b2)": f"{y_face:.0f}",
            f"Total ({fu})": f"{fy:,.0f}",
            f"Pressure ({fu}/{lu}\u00b2)": f"{p_y:.2f}",
        },
    ]

    return {
        "rows": rows,
        "within_10pct": bool(max(p_x, p_y) > 0 and abs(p_x - p_y) / max(p_x, p_y) < 0.1),
        "x_face": x_face,
        "y_face": y_face,
        "fx": fx,
        "fy": fy,
        "p_x": p_x,
        "p_y": p_y,
        "force_unit": fu,
        "length_unit": lu,
        "bounding_box": bb,
    }


def wind_sanity_check(md, df_linear, wind_case_x: str = "Wind+X", wind_case_y: str = "Wind+Y"):
    """Return a markdown paragraph checking wind loads against face area.

    Parameters
    ----------
    md : SAPModelData
        Model data (for node coordinates and units).
    df_linear : pd.DataFrame
        Linear analysis results; must contain ``Case``, ``Fx``, ``Fy`` columns.
    wind_case_x, wind_case_y : str
        Case names for the X and Y wind load patterns.

    Returns
    -------
    str
        Markdown paragraph with bounding-box summary and wind-pressure table.
    """
    data = wind_sanity_data(md, df_linear, wind_case_x, wind_case_y)
    bb = data["bounding_box"]
    fu = data["force_unit"]
    lu = data["length_unit"]

    # Render the table body from the preformatted ``data["rows"]`` so the
    # markdown and the structured data used by the model review can never
    # drift apart.
    table_rows = [
        "| " + " | ".join(str(value) for value in row.values()) + " |" for row in data["rows"]
    ]

    lines = [
        f"**Bounding box:** "
        f"{bb['x_span']:.1f} {lu} (X) × "
        f"{bb['y_span']:.1f} {lu} (Y) × "
        f"{bb['z_span']:.1f} {lu} (Z) — "
        f"{bb['n_nodes']} nodes.",
        "",
        "**Wind load sanity check:**",
        "",
        f"| Face | Area ({lu}²) | Total {fu} | Pressure ({fu}/{lu}²) |",
        "|---|---|---|---|",
        *table_rows,
    ]

    if data["within_10pct"]:
        lines.append("")
        lines.append(
            "✅ Pressures are within 10 % — wind loads are consistent "
            "with the bounding box face areas."
        )

    return "\n".join(lines)


def static_load_verification(md, mesh_model, config: Optional[dict] = None):
    """Check equilibrium between applied loads and reactions.

    Combines the applied loads (per-pattern per-component totals
    accumulated by ``AnalysisBuilder.create_loads()``) with the
    reactions (from
    :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.check_load_equilibrium`)
    into a single table with Applied, Reaction, and Δ columns.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data.
    mesh_model :
        Pre-processed mesh model.
    config : dict, optional
        Builder configuration.

    Returns
    -------
    pd.DataFrame
        One row per load pattern with Applied/Reaction/Δ columns.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    if config is None:
        config = {"verbose": False}

    fu = md.units.get("F", "?")

    ab = AnalysisBuilder(mesh_model, config)
    ab.build_domain()
    # Apply ALL load patterns once so the per-pattern applied totals
    # (_sw/_gravity/_joint/_dist) are populated for every pattern.
    ab.create_loads()

    def _clean(v):
        """Coerce a merged-cell value to a finite float, else 0.0."""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return 0.0
        return fv if math.isfinite(fv) else 0.0

    # ── Applied totals: per-pattern per-component ─────────────────
    applied: dict[str, dict[str, float]] = {}
    for _src in (
        getattr(ab, "_sw_load_totals", {}),
        getattr(ab, "_gravity_load_totals", {}),
        getattr(ab, "_joint_load_totals", {}),
        getattr(ab, "_dist_load_totals", {}),
    ):
        for pname, t in _src.items():
            d = applied.setdefault(pname, dict.fromkeys(("fx", "fy", "fz", "mx", "my", "mz"), 0.0))
            for _k in d:
                d[_k] += _clean(t.get(_k, 0.0))

    # ── Reactions: per-pattern static runs ────────────────────────
    df_rxn = ab.check_load_equilibrium()

    rows = []
    all_names = set(applied) | set(df_rxn["Load Pattern"])
    for pname in sorted(all_names, key=str.casefold):
        pat = md.load_patterns.get(pname)
        pat_type = pat.pattern_type if pat else "?"
        a = applied.get(pname, dict.fromkeys(("fx", "fy", "fz", "mx", "my", "mz"), 0.0))
        rr = df_rxn[df_rxn["Load Pattern"] == pname]
        r = rr.iloc[0] if len(rr) else {}
        ax = _clean(a["fx"])
        ay = _clean(a["fy"])
        az = _clean(a["fz"])
        rx = _clean(r.get(f"Reaction Fx ({fu})", 0.0))
        ry = _clean(r.get(f"Reaction Fy ({fu})", 0.0))
        rz = _clean(r.get(f"Reaction Fz ({fu})", 0.0))
        rows.append(
            {
                "Load Pattern": pname,
                "Type": pat_type,
                f"Applied Fx ({fu})": round(ax, 1),
                f"Applied Fy ({fu})": round(ay, 1),
                f"Applied Fz ({fu})": round(az, 1),
                f"Reaction Fx ({fu})": round(rx, 1),
                f"Reaction Fy ({fu})": round(ry, 1),
                f"Reaction Fz ({fu})": round(rz, 1),
                f"\u0394Fx ({fu})": round(ax + rx, 1),
                f"\u0394Fy ({fu})": round(ay + ry, 1),
                f"\u0394Fz ({fu})": round(az + rz, 1),
            }
        )

    return pd.DataFrame(rows)


def _mesh_has_shells(mesh_model) -> bool:
    """Whether a preprocessed model carries areas the builder must build as shells.

    The Preprocessor can leave areas in one of two states: **shells** (meshed,
    carrying their own pressure) or **loads-only** (demoted to frame edge loads).
    It records the demoted ones on ``MeshModel.loads_only_area_ids``, so any area
    element *not* in that set is a shell the builder has to create.

    This is what makes the builder's config follow the mesh: without it a
    wall/slab-stiffened model is built with its areas silently missing — a
    mechanism, and the singular-matrix failure the Admin Building run hit.

    Args:
        mesh_model: A ``MeshModel`` (or anything with the two attributes).

    Returns:
        ``True`` when at least one area element is not loads-only.
    """
    areas = getattr(mesh_model, "area_elements", None) or {}
    loads_only = set(getattr(mesh_model, "loads_only_area_ids", None) or ())
    return any(area_id not in loads_only for area_id in areas)


def run_static_cases(
    mesh_model,
    cases: dict[str, dict[str, float]],
    *,
    config: Optional[dict] = None,
    raw_out: Optional[dict] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, str, int], None]] = None,
) -> list[dict]:
    """Run each ``{case: {pattern: factor}}`` entry as one static solve.

    This is the static half of :func:`run_linear_cases`, lifted out so a caller
    can run *only* static cases — no response-spectrum or modal pass — and can
    watch and stop it: *should_cancel* is polled **between** cases (a single
    ``run_static_analysis`` call is atomic and cannot be interrupted), and
    *on_progress* is told which case is starting.

    Each case is one solve of its factored patterns
    (``AnalysisBuilder.run_static_analysis(pattern_scales=...)``), so the factors
    are **load** multipliers, not a post-hoc scaling of results — scaling results
    is the load-combination layer's job
    (``docs/load_cases_and_combinations.md``).

    Args:
        mesh_model: Preprocessed :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        cases: ``{case_name: {pattern: factor}}`` — the cases to solve, in run
            order.  :func:`~fea_toolkit.analysis.case_listing.list_static_cases`
            produces this for the model's own cases.
        config: Optional ``AnalysisBuilder`` config (solver settings).  When it
            does not set ``create_shells``, that is derived from the mesh — see
            :func:`_mesh_has_shells` — so a shell-stiffened model is built with
            its areas rather than as a mechanism.
        raw_out: Optional dict filled with each case's raw results —
            ``raw_out[case] = {"nodal_displacements": ..., "element_forces": {...}}``
            — the shape :func:`~fea_toolkit.io.npz_writer.results_arrays` accepts
            as ``static_results``.  A case that **fails** contributes no entry,
            so ``set(cases) - set(raw_out)`` is the set that did not converge.
        should_cancel: Optional zero-argument callable polled between cases; when
            it returns ``True`` the run stops and the rows collected so far are
            returned.
        on_progress: Optional ``(index, case_name, total)`` callback, called
            **before** each case is solved (``index`` is 1-based) — the hook a
            GUI worker turns into a progress signal.

    Returns:
        One summary row per solved case, in :func:`dict` form — the rows
        :func:`run_linear_cases` appends to its table, in the same column order.
    """
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.utils import sum_reactions_with_overturning

    ab_config = {"element_type": "elasticBeamColumn", "verbose": False}
    # Build what the mesh holds: a preprocessed model with shell areas needs
    # ``create_shells`` set, or those areas are never created and a
    # wall/slab-stiffened model is a mechanism.  A caller's explicit setting wins.
    if _mesh_has_shells(mesh_model):
        ab_config["create_shells"] = True
    if config:
        ab_config.update(config)

    rows: list[dict] = []
    total = len(cases)
    for index, (case_name, patterns) in enumerate(cases.items(), start=1):
        if should_cancel is not None and should_cancel():
            break
        if on_progress is not None:
            on_progress(index, case_name, total)

        ab = AnalysisBuilder(mesh_model, ab_config)
        try:
            results = ab.run_static_analysis(pattern_scales=patterns, extract_reactions=True)
            # Use centralized overturning-moment computation (v1 match)
            rxn = results.get("reactions", {})
            summed = sum_reactions_with_overturning(rxn, mesh_model.nodes)
            fx = summed["fx"]
            fy = summed["fy"]
            fz = summed["fz"]
            mx = summed["mx"]
            my = summed["my"]
            mz = summed["mz"]
            disp = results.get("nodal_displacements", {})
            max_roof_disp = 0.0
            if disp:
                roof_id = max(mesh_model.nodes.values(), key=lambda n: n.z).node_id
                if roof_id in disp:
                    d = disp[roof_id]
                    max_roof_disp = math.hypot(d[0], d[1], d[2])

            # ── Raw per-case results (optional export) ─────────────
            # Filled in the same order as the summary row so the caller
            # can build unified/stage files with static frame forces and
            # nodal displacements.
            if raw_out is not None:
                entry = {"nodal_displacements": results.get("nodal_displacements", {})}
                try:
                    # Component-keyed arrays ordered to match the exported
                    # geometry (see AnalysisBuilder.static_element_force_arrays).
                    elem_forces = ab.static_element_force_arrays()
                    if elem_forces:
                        entry["element_forces"] = elem_forces
                except Exception:
                    pass
                raw_out[case_name] = entry
        except Exception as e:
            fx = fy = fz = mx = my = mz = max_roof_disp = 0.0
            print(f"  Warning: {case_name} failed — {e}")

        rows.append(
            {
                "Case": case_name,
                "Type": "Static",
                "Fx": fx,
                "Fy": fy,
                "Fz": fz,
                "Mx": mx,
                "My": my,
                "Mz": mz,
                "Roof disp": max_roof_disp if "Wind" in case_name else None,
            }
        )

    return rows


def run_case_set(
    md: SAPModelData,
    mesh_model,
    cases: dict[str, dict[str, float]],
    *,
    combinations: Optional[dict[str, dict[str, float]]] = None,
    config: Optional[dict] = None,
    raw_out: Optional[dict] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, str, int], None]] = None,
) -> dict:
    """Solve cases, reduce any combinations, and assemble an in-memory archive.

    The whole of the GUI's ``Analysis ▸ Run`` in one call, kept Qt-free so it is
    tested without the ``[gui]`` extra: it solves each case with
    :func:`run_static_cases`, reduces the requested combinations with
    :func:`~fea_toolkit.analysis.combinations.build_combination_results`, and
    assembles the archive with
    :func:`~fea_toolkit.io.npz_writer.results_arrays`.  **Nothing is written to
    disk** — the returned ``"arrays"`` goes straight to
    :class:`~fea_toolkit.io.results_repository.NpzResultsRepository`, which is
    what lets a result be viewed without an archive (P27).

    A combination whose leaf cases did not all solve is **not** reduced: an
    envelope over a missing case would be quietly wrong, so it is reported in
    ``"unreduced"`` instead.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData` — supplies
            the combination definitions and the archive's unit labels.
        mesh_model: The preprocessed model the cases are built from.
        cases: ``{case_name: {pattern: factor}}`` to solve, in run order.
        combinations: ``{combination_name: {leaf_case: factor}}`` to reduce from
            the case results.  The factors here are only used to decide whether
            a combination is reducible; the reduction reads its factors from the
            model's own combination definitions.
        raw_out: Optional dict filled with each case's raw results (see
            :func:`run_static_cases`).
        config: Optional ``AnalysisBuilder`` config.  ``create_shells`` is
            derived from the mesh when it is not given (see
            :func:`run_static_cases`).
        should_cancel: Polled between cases (see :func:`run_static_cases`).
        on_progress: ``(index, case_name, total)`` before each case (see
            :func:`run_static_cases`).

    Returns:
        ``{"arrays": {name: ndarray}, "cases": [name, ...], "failed": [...],
        "unreduced": [...], "cancelled": bool}`` — ``"failed"`` are the cases
        that did not converge, ``"unreduced"`` the combinations that could not
        be formed from what did.
    """
    from ..io.npz_writer import results_arrays
    from .combinations import build_combination_results

    raw: dict = {} if raw_out is None else raw_out
    run_static_cases(
        mesh_model,
        cases,
        config=config,
        raw_out=raw,
        should_cancel=should_cancel,
        on_progress=on_progress,
    )

    static_results: dict = dict(raw)
    requested = dict(combinations or {})
    unreduced = [name for name, leaves in requested.items() if not set(leaves) <= set(raw)]
    reducible = [name for name in requested if name not in unreduced]

    case_meta = None
    if reducible:
        combined, case_meta = build_combination_results(
            raw, model=md, combinations=reducible, return_meta=True
        )
        static_results.update(combined)

    arrays = results_arrays(
        md, static_results=static_results, mesh_model=mesh_model, case_meta=case_meta
    )
    cancelled = bool(should_cancel()) if should_cancel is not None else False
    return {
        "arrays": arrays,
        "cases": list(static_results),
        # A cancelled case did not *fail* — it was never run — so the two are
        # reported apart and the caller can say which happened.
        "failed": [] if cancelled else sorted(set(cases) - set(raw)),
        "unreduced": unreduced,
        "cancelled": cancelled,
    }


def run_linear_cases(
    md: SAPModelData,
    mesh_model,
    spec_cfg: Optional[dict] = None,
    linear_cfg: Optional[dict] = None,
    raw_out: Optional[dict] = None,
) -> pd.DataFrame:
    """Run linear analysis cases and return a summary table.

    The Preprocessor work is done once (via *mesh_model*).  Each
    analysis case creates a lightweight ``AnalysisBuilder``.

    Auto-detects static load cases from the SAP2000 model, or uses
    the ``linear_cfg["cases"]`` list.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.
        mesh_model: Pre-processed :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        spec_cfg: Spectrum config passed through to the RS pass.
        linear_cfg: Linear config (``cases``, ``n_modes``).
        raw_out: Optional dict filled with the raw per-case results as
            ``raw_out[case_name] = {"nodal_displacements": ...,
            "element_forces": {...}}`` — the shape accepted by
            :func:`fea_toolkit.io.stage_writer.write_model_stages` /
            :func:`fea_toolkit.io.unified_writer.write_results` (static
            results).  Element forces are extracted in the element
            **local** coordinate system and stored under the plain
            ``fx_i`` … ``mz_j`` keys (aligned with the geometry arrays).

    Returns:
        Summary table (DataFrame) — one row per analysis case.
    """
    rows = []
    config = {"element_type": "elasticBeamColumn", "verbose": False}

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    # ── Static cases: auto-detect LinStatic, merge overrides, drop empty ──
    # The listing lives in analysis/case_listing.py so the GUI's Analysis ▸ Run
    # dialog offers exactly the cases this runner would solve.
    static_cases = list_static_cases(md, mesh_model, config_cases=(linear_cfg or {}).get("cases"))
    rows.extend(run_static_cases(mesh_model, static_cases, config=config, raw_out=raw_out))

    # ── Response spectrum cases ─────────────────────────────────
    n_modes = 12
    if linear_cfg and "n_modes" in linear_cfg:
        n_modes = int(linear_cfg["n_modes"])
    elif spec_cfg and "n_modes" in spec_cfg:
        n_modes = int(spec_cfg["n_modes"])

    if spec_cfg:
        from fea_toolkit.spectrum import _build_spectrum

        from ..utils import g_from_units

        # g from the model's length unit so the spectral accelerations are
        # in the model's own unit system (matching the fallback branch).
        T_spec_built, Sa_spec_built, _, _, zeta_eff, _ = _build_spectrum(
            spec_cfg, g=g_from_units(md.units)
        )
        T_spec = T_spec_built
        Sa_spec = Sa_spec_built
    else:
        # Fallback: rare spectrum with 5% damping
        from fea_toolkit.spectrum import _gb50011_spectrum

        zeta_eff = 0.05
        gamma = 0.9 + (0.05 - zeta_eff) / (0.3 + 6.0 * zeta_eff)
        eta_1 = max(0.0, 0.02 + (0.05 - zeta_eff) / (4.0 + 32.0 * zeta_eff))
        eta_2 = max(0.55, 1.0 + (0.05 - zeta_eff) / (0.08 + 1.6 * zeta_eff))
        from ..utils import g_from_units

        gravity = g_from_units(md.units)
        tg = 0.25
        alpha_max = 0.50
        T_spec = np.linspace(0.0, 6.0, 300).tolist()
        Sa_spec = _gb50011_spectrum(
            T_spec, alpha_max, tg, gamma=gamma, eta1=eta_1, eta2=eta_2, g=gravity
        ).tolist()

    for rs_dir in ["X", "Y"]:
        ab = AnalysisBuilder(mesh_model, config)
        ab.build_domain()
        ab.compute_seismic_masses()
        try:
            modal = ab.run_modal_analysis(num_modes=n_modes, print_results=False)

            # Degenerate / single-element models return fewer converged modes
            # than requested (run_modal_analysis keeps only positive
            # eigenvalues).  Use the actual count so the RS pass and the
            # nodal-displacement superposition stay index-aligned.
            n_actual = len(modal["periods"])
            if n_actual == 0:
                raise ValueError("modal analysis returned no converged modes")

            def spectrum_func(T):
                return float(np.interp(T, T_spec, Sa_spec))

            rs = ab.run_response_spectrum_analysis(
                num_modes=n_actual,
                modal_periods=modal["periods"],
                spectrum_periods=T_spec,
                spectrum_accels=Sa_spec,
                direction=rs_dir,
                damping_ratio=zeta_eff,
                print_results=False,
            )
            v_srss = rs.get("base_shear_srss", 0.0)
            m_srss = rs.get("base_moment_srss", 0.0)
            # Full 6-DoF reactions with overturning (from new centralized
            # per-mode lever-arm computation in run_response_spectrum_analysis)
            r_cqc = rs.get("base_reactions_cqc", {})

            rs_disp_cqc, rs_disp_srss = ab.compute_rs_nodal_displacements(
                num_modes=n_actual,
                modal_periods=modal["periods"],
                eigenvalues=modal["eigenvalues"],
                spectrum_func=spectrum_func,
                direction=rs_dir,
                damping_ratio=zeta_eff,
                return_srss=True,
            )
            roof_tag = max(md.nodes.values(), key=lambda n: n.z).node_tag
            if roof_tag in rs_disp_cqc:
                d = rs_disp_cqc[roof_tag]
                roof_disp_rs = math.hypot(d[0], d[1], d[2])
                d_s = rs_disp_srss[roof_tag]
                roof_disp_srss = math.hypot(d_s[0], d_s[1], d_s[2])
            else:
                roof_disp_rs = roof_disp_srss = 0.0
        except Exception as e:
            v_srss = m_srss = roof_disp_rs = roof_disp_srss = 0.0
            r_cqc = {}
            print(f"  Warning: RS-{rs_dir} failed — {e}")

        rows.append(
            {
                "Case": f"RS-{rs_dir}",
                "Type": "Response Spectrum",
                "Fx": r_cqc.get("fx", 0.0),
                "Fy": r_cqc.get("fy", 0.0),
                "Fz": r_cqc.get("fz", 0.0),
                "Mx": r_cqc.get("mx", 0.0),
                "My": r_cqc.get("my", 0.0),
                "Mz": r_cqc.get("mz", 0.0),
                "Roof disp": roof_disp_rs,
                "Roof disp SRSS": roof_disp_srss,
                "V_srss": v_srss,
                "M_srss": m_srss,
            }
        )

    return pd.DataFrame(rows)
