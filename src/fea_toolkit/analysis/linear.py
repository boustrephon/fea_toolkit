"""Static linear analysis runners.

These functions execute the OpenSees solver (or combine its results) for the
static-linear verification workflow.  They live in ``analysis/`` (not ``io/``)
because they run analyses rather than only formatting data.

The pandas *summary* helpers they depend on (``bounding_box``,
``load_pattern_totals``) remain in :mod:`fea_toolkit.io.report`.
"""

import math
from typing import Optional

import numpy as np
import pandas as pd

from fea_toolkit.io.report import bounding_box, load_pattern_totals
from fea_toolkit.model.sap_data import SAPModelData, patterns_from_case


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
        f"| Wind +X (Y‑Z face) | {x_face:.0f} | {fx:,.0f} | {p_x:.2f} |",
        f"| Wind +Y (X‑Z face) | {y_face:.0f} | {fy:,.0f} | {p_y:.2f} |",
    ]

    if max(p_x, p_y) > 0 and abs(p_x - p_y) / max(p_x, p_y) < 0.1:
        lines.append("")
        lines.append(
            "✅ Pressures are within 10 % — wind loads are consistent "
            "with the bounding box face areas."
        )

    return "\n".join(lines)


def static_load_verification(md, mesh_model, config: Optional[dict] = None):
    """Check equilibrium between applied loads and reactions.

    Combines the applied loads (from
    :func:`load_pattern_totals`) with the reactions (from
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

    df_applied = load_pattern_totals(md)
    fu = md.units.get("F", "?")

    ab = AnalysisBuilder(mesh_model, config)
    df_rxn = ab.check_load_equilibrium()

    merged = df_applied.merge(df_rxn, on="Load Pattern", how="outer", suffixes=("_applied", "_rxn"))

    def _clean(v):
        """Coerce a merged-cell value to a finite float, else 0.0."""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return 0.0
        return fv if math.isfinite(fv) else 0.0

    rows = []
    for _, row in merged.iterrows():
        pname = row["Load Pattern"]
        pat = md.load_patterns.get(pname)
        pat_type = pat.pattern_type if pat else "?"
        ax = _clean(row.get(f"Fx ({fu})", 0.0))
        ay = _clean(row.get(f"Fy ({fu})", 0.0))
        az = _clean(row.get(f"Fz ({fu})", 0.0))
        rx = _clean(row.get(f"Reaction Fx ({fu})", 0.0))
        ry = _clean(row.get(f"Reaction Fy ({fu})", 0.0))
        rz = _clean(row.get(f"Reaction Fz ({fu})", 0.0))
        dx = round(ax + rx, 1)
        dy = round(ay + ry, 1)
        dz = round(az + rz, 1)
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
                f"\u0394Fx ({fu})": dx,
                f"\u0394Fy ({fu})": dy,
                f"\u0394Fz ({fu})": dz,
            }
        )

    return pd.DataFrame(rows)


def run_linear_cases(
    md: SAPModelData,
    mesh_model,
    spec_cfg: Optional[dict] = None,
    linear_cfg: Optional[dict] = None,
) -> pd.DataFrame:
    """Run linear analysis cases and return a summary table.

    The Preprocessor work is done once (via *mesh_model*).  Each
    analysis case creates a lightweight ``AnalysisBuilder``.

    Auto-detects static load cases from the SAP2000 model, or uses
    the ``linear_cfg["cases"]`` list.
    """
    rows = []
    config = {"element_type": "elasticBeamColumn", "verbose": False}

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    # ── Static cases: auto-detect LinStatic, then merge user overrides ──
    # Always auto-detect all LinStatic cases from the model
    static_cases: dict[str, dict[str, float]] = {}
    for cname, lc in md.load_cases.items():
        if lc.case_type != "LinStatic":
            continue
        pats = patterns_from_case(lc)
        if pats:
            static_cases[cname] = pats

    # User-specified cases override or supplement auto-detected ones
    if linear_cfg and "cases" in linear_cfg:
        for entry in linear_cfg["cases"]:
            if isinstance(entry, str):
                lc = md.load_cases.get(entry)
                if lc is None:
                    print(f"  Warning: load case '{entry}' not found, skipping")
                    continue
                pats = patterns_from_case(lc)
                if pats:
                    static_cases[entry] = pats
            elif isinstance(entry, dict):
                for cname, pat_dict in entry.items():
                    static_cases[cname] = pat_dict

    # ── Filter out cases whose constituent patterns have zero loads ──
    def _pattern_has_loads(pname: str) -> bool:
        lp = md.load_patterns.get(pname)
        if lp is not None and lp.self_weight_factor > 0:
            return True
        return (
            any(ld.pattern == pname for ld in md.frame_dist_loads)
            or any(ld.pattern == pname for ld in md.joint_loads)
            or any(ld.pattern == pname for ld in md.area_gravity_loads)
            or any(ld.pattern == pname for ld in mesh_model.edge_loads_from_areas)
        )

    static_cases = {
        cname: pats
        for cname, pats in static_cases.items()
        if any(_pattern_has_loads(p) for p in pats)
    }

    from fea_toolkit.utils import sum_reactions_with_overturning

    for case_name, patterns in static_cases.items():
        ab = AnalysisBuilder(mesh_model, config)
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

    # ── Response spectrum cases ─────────────────────────────────
    n_modes = 12
    if linear_cfg and "n_modes" in linear_cfg:
        n_modes = int(linear_cfg["n_modes"])
    elif spec_cfg and "n_modes" in spec_cfg:
        n_modes = int(spec_cfg["n_modes"])

    if spec_cfg:
        from fea_toolkit.spectrum import _build_spectrum

        T_spec_built, Sa_spec_built, _, _, zeta_eff, _ = _build_spectrum(spec_cfg)
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

            def spectrum_func(T):
                return float(np.interp(T, T_spec, Sa_spec))

            rs = ab.run_response_spectrum_analysis(
                num_modes=n_modes,
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
                num_modes=n_modes,
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
