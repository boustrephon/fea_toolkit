"""
Generic SAP2000 → pandas summary and report helpers.

All functions operate on a ``SAPModelData`` instance (``md``) and return
either a ``pd.DataFrame`` suitable for HTML/Quarto display or a simple
dict/string.  They are independent of any specific project — the same
functions can be used for any model parsed with ``SAP2000Parser``.

This module only *formats* data — the **linear** analysis runners that
execute the OpenSees solver live in :mod:`fea_toolkit.analysis.linear`;
the other runners (modal, response-spectrum, pushover, nonlinear-dynamic)
live in their respective modules under :mod:`fea_toolkit.analysis`.  Not
to be confused with :mod:`fea_toolkit.report` (report orchestration) or
:mod:`fea_toolkit.plotting.report` (matplotlib figures).
"""

from __future__ import annotations

import math
from typing import Any, Optional

try:
    import pandas as pd
except ImportError:  # pragma: no cover — pandas is optional (Rhino 8 CPython)

    class _MissingPandas:
        """Raise a clear error when a report helper needs pandas.

        pandas is not a required dependency of the toolkit (see
        ``pyproject.toml`` core deps) and is absent from Rhino 8's
        bundled CPython.  The module must still import so that
        ``import fea_toolkit.io`` works without it; only actually calling
        a report helper raises.
        """

        def __getattr__(self, _name: str):
            raise RuntimeError(
                "report helpers require pandas, which is not "
                "installed in this Python environment (pip install pandas)."
            )

    pd = _MissingPandas()  # type: ignore[assignment]

from ..model.sap_data import ShellSection

# ========================================================================
# Bounding box
# ========================================================================


def bounding_box(md) -> dict[str, float]:
    """Return the model's node bounding box extents.

    Returns a dict with keys ``x_min``, ``x_max``, ``x_span``,
    ``y_min``, ``y_max``, ``y_span``, ``z_min``, ``z_max``, ``z_span``,
    and ``n_nodes``.
    """
    xs = [n.x for n in md.nodes.values()]
    ys = [n.y for n in md.nodes.values()]
    zs = [n.z for n in md.nodes.values()]
    return {
        "x_min": min(xs),
        "x_max": max(xs),
        "x_span": max(xs) - min(xs),
        "y_min": min(ys),
        "y_max": max(ys),
        "y_span": max(ys) - min(ys),
        "z_min": min(zs),
        "z_max": max(zs),
        "z_span": max(zs) - min(zs),
        "n_nodes": len(md.nodes),
    }


# ========================================================================
# Mass source summary
# ========================================================================


def summarise_mass_sources(md) -> pd.DataFrame:
    """Return a DataFrame summarising all mass sources."""
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model
    from ..utils import g_from_units

    rows = []
    for ms_name, ms in md.mass_sources.items():
        row = {
            "Name": ms_name,
            "Default": "Yes" if ms.is_default else "",
            "Joints": "Yes" if ms.masses else "",
            "Elements": "Yes" if ms.elements else "",
        }
        if ms.load_pattern:
            row["Patterns"] = ", ".join(
                f"{lp}\u00d7{mult:.2f}" for lp, mult in ms.load_pattern.items()
            )
        else:
            row["Patterns"] = ""
        rows.append(row)

    if md.mass_sources:
        _mm = preprocess_model(
            md, {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
        )
        b = AnalysisBuilder(_mm, {"element_type": "elasticBeamColumn", "verbose": False})
        b.build_domain()
        node_masses = b.compute_seismic_masses()
        total_mass = sum(node_masses.values())
        total_weight = total_mass * g_from_units(md.units)
        for row in rows:
            row["Mass (t)"] = f"{total_mass:.2f}"
            row["Weight (kN)"] = f"{total_weight:.1f}"

    df = pd.DataFrame(rows).fillna("")
    cols = ["Name", "Default", "Joints", "Elements", "Patterns", "Mass (t)", "Weight (kN)"]
    return df[[c for c in cols if c in df.columns]]


# ========================================================================
# Load case summary
# ========================================================================


def summarise_load_cases(md) -> pd.DataFrame:
    """Return a DataFrame summarising all load cases.

    For each load case, shows Type, DesignType, DesignAct, and type-specific
    details:

    * **LinModal** — number of modes
    * **LinStatic** — load assignments (LoadName / LoadSF)
    * **LinRespSpec** — load names and average ConstDamp
    """
    rows = []
    for cname, lc in md.load_cases.items():
        row = {
            "Case": cname,
            "Type": lc.case_type,
            "DesignType": lc.design_type,
            "DesignAct": lc.design_action,
            "Details": "",
        }
        ctype = lc.case_type.lower() if lc.case_type else ""

        if ctype == "linmodal":
            modal_data = lc.case_data.get("CASE - MODAL 1 - GENERAL", {})
            n_modes = modal_data.get("MaxNumModes", "?")
            mode_type = modal_data.get("ModeType", "Eigen")
            row["Details"] = f"{mode_type} Modes: {n_modes}"

        elif ctype == "linstatic":
            static_loads = lc.case_data.get("CASE - STATIC 1 - LOAD ASSIGNMENTS", [])
            if isinstance(static_loads, list):
                parts = []
                for sl in static_loads:
                    ln = sl.get("LoadName", "?")
                    sf = sl.get("LoadSF", "?")
                    parts.append(f"{ln}\u00d7{sf}")
                row["Details"] = ", ".join(parts) if parts else ""
            elif isinstance(static_loads, dict):
                ln = static_loads.get("LoadName", "?")
                sf = static_loads.get("LoadSF", "?")
                row["Details"] = f"{ln}\u00d7{sf}"

        elif ctype == "linrespspec":
            rs = lc.case_data.get("CASE - RESPONSE SPECTRUM", {})
            loads = rs.get("LoadAssignments", [])
            if loads:
                load_names = [la.get("LoadName", "?") for la in loads]
                row["Details"] = ", ".join(load_names)
            const_damp = rs.get("ConstDamp", None)
            if const_damp is not None:
                row["Details"] += f"  (\u03b6={const_damp})"

        rows.append(row)

    df = pd.DataFrame(rows)
    cols = ["Case", "Type", "DesignType", "DesignAct", "Details"]
    return df[cols]


# ========================================================================
# Load pattern summary
# ========================================================================


def summarise_load_patterns(md) -> pd.DataFrame:
    """Return a DataFrame summarising all load patterns.

    Shows name, type, self-weight factor, and auto-seismic/wind parameters.
    """
    rows = []
    for lp_name, lp in md.load_patterns.items():
        row = {
            "LoadPat": lp_name,
            "Type": lp.pattern_type,
            "SelfWtMult": lp.self_weight_factor,
        }
        for table_name, data in lp.auto_data.items():
            if "SEISMIC" in table_name.upper():
                row["\u03b1_max"] = data.get("AlphaMax", "")
                row["Tg (s)"] = data.get("Tg", "")
                row["\u03b6"] = data.get("DampRatio", "")
                row["SI"] = data.get("SI", "")
            elif "WIND" in table_name.upper():
                row["Wind params"] = str(data)
        rows.append(row)

    df = pd.DataFrame(rows)
    for col in ["LoadPat", "Type", "SelfWtMult", "\u03b1_max", "Tg (s)", "\u03b6", "SI"]:
        if col not in df.columns:
            df[col] = ""
    cols = [
        c
        for c in ["LoadPat", "Type", "SelfWtMult", "\u03b1_max", "Tg (s)", "\u03b6", "SI"]
        if c in df.columns
    ]
    for c in df.columns:
        if c not in cols:
            cols.append(c)
    df = df[cols].fillna("")
    return df


# ========================================================================
# Load pattern totals (from builder)
# ========================================================================


def load_pattern_totals(md) -> pd.DataFrame:
    """Return a DataFrame of total applied load per pattern.

    Builds the two-stage model and applies all load patterns via
    ``AnalysisBuilder.create_loads()``, then reports the per-pattern
    per-component applied totals accumulated by the builder
    (self-weight + gravity + joint + distributed loads).

    Note:
        Load totals under the two-stage ``AnalysisBuilder`` are stored as
        per-component dicts on the builder (``_sw_load_totals``,
        ``_gravity_load_totals``, ``_joint_load_totals``,
        ``_dist_load_totals``).  For per-pattern reaction comparison prefer
        ``AnalysisBuilder.check_load_equilibrium()``.
    """
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model

    _mm = preprocess_model(
        md, {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
    )
    b = AnalysisBuilder(_mm, {"element_type": "elasticBeamColumn", "verbose": False})
    b.build_domain()
    b.create_loads()  # apply all patterns so per-pattern totals are populated

    # Determine which patterns are referenced by LinStatic load cases
    linstatic_patterns: set = set()
    for cname, lc in md.load_cases.items():
        if lc.case_type != "LinStatic":
            continue
        sd = lc.case_data.get("CASE - STATIC 1 - LOAD ASSIGNMENTS", [])
        if isinstance(sd, list):
            for ass in sd:
                if "LoadName" in ass:
                    linstatic_patterns.add(ass["LoadName"])
        elif isinstance(sd, dict) and "LoadName" in sd:
            linstatic_patterns.add(sd["LoadName"])

    # Per-pattern per-component applied totals
    totals: dict[str, dict[str, float]] = {}
    for _src in (
        getattr(b, "_sw_load_totals", {}),
        getattr(b, "_gravity_load_totals", {}),
        getattr(b, "_joint_load_totals", {}),
        getattr(b, "_dist_load_totals", {}),
    ):
        for pname, t in _src.items():
            d = totals.setdefault(pname, dict.fromkeys(("fx", "fy", "fz"), 0.0))
            for k in ("fx", "fy", "fz"):
                d[k] += float(t.get(k, 0.0) or 0.0)

    rows = []
    fu = md.units.get("F", "?")
    for pname in sorted(totals, key=str.casefold):
        if pname not in linstatic_patterns:
            continue
        t = totals[pname]
        # Skip patterns with zero load in all directions
        if abs(t["fx"]) < 1e-9 and abs(t["fy"]) < 1e-9 and abs(t["fz"]) < 1e-9:
            continue
        rows.append(
            {
                "Load Pattern": pname,
                f"Fx ({fu})": round(t["fx"], 2),
                f"Fy ({fu})": round(t["fy"], 2),
                f"Fz ({fu})": round(t["fz"], 2),
            }
        )
    return pd.DataFrame(rows)


# ========================================================================
# Material quantities
# ========================================================================


def material_summary(md) -> pd.DataFrame:
    """Return a DataFrame of material quantities (volume, weight)."""
    rows = []
    for eid, elem in md.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sec_name = md.frame_assignments.get(eid)
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        if isinstance(sec, ShellSection):
            continue
        mat = md.materials.get(sec.material)
        if mat is None:
            continue
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        volume = sec.A * L
        weight = volume * mat.unit_weight
        rows.append(
            {
                "material": sec.material,
                "section": sec_name,
                "elem_id": eid,
                "length_m": round(L, 3),
                "volume_m3": round(volume, 6),
                "weight": round(weight, 2),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    force_unit = md.units.get("F", "?")
    summary = (
        df.groupby("material")
        .agg(
            count=("elem_id", "count"),
            total_volume_m3=("volume_m3", "sum"),
            total_weight=("weight", "sum"),
        )
        .reset_index()
    )
    total_row = pd.DataFrame(
        [
            {
                "material": "<strong>Total</strong>",
                "count": summary["count"].sum(),
                "total_volume_m3": summary["total_volume_m3"].sum(),
                "total_weight": summary["total_weight"].sum(),
            }
        ]
    )
    summary = pd.concat([summary, total_row], ignore_index=True)
    summary.columns = [
        "Material",
        "Element count",
        "Total volume (m\u00b3)",
        f"Total weight ({force_unit})",
    ]
    for col in ["Total volume (m\u00b3)", f"Total weight ({force_unit})"]:
        summary[col] = summary[col].apply(lambda v: f"{v:.1f}" if v > 0 else "\u2014")
    summary["Element count"] = summary["Element count"].apply(
        lambda v: f"{v}" if v > 0 else "\u2014"
    )
    return summary


# ========================================================================
# Formatting helpers for section tables
# ========================================================================


def _sig4(val):
    """Format a number to 4 significant figures, or return '\u2014' if zero."""
    if val is None or val == 0.0:
        return "\u2014"
    return f"{val:.4g}"


def _nbsp(val):
    """Return em-dash if *val* is empty / None / '\u2014', else *val*."""
    if val is None or (isinstance(val, str) and (val.strip() == "" or val == "\u2014")):
        return "\u2014"
    return str(val)


# ========================================================================
# Section summary
# ========================================================================


def section_summary(md) -> pd.DataFrame:
    """Return a DataFrame of structural sections with dimensions and weights.

    Includes a **total** row per section summed over all frame elements.
    """
    force_unit = md.units.get("F", "?")
    length_unit = md.units.get("L", "m")

    rows = []
    for eid, elem in md.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sec_name = md.frame_assignments.get(eid)
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        if isinstance(sec, ShellSection):
            continue
        mat = md.materials.get(sec.material, None)
        mat_name = sec.material if mat else "?"
        shape = type(sec).__name__

        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)

        gamma = mat.unit_weight if mat and mat.unit_weight else 0
        w_total = gamma * sec.A * L if sec.A else 0

        rows.append(
            {
                "Section": sec_name,
                "Shape": shape,
                "Material": mat_name,
                "Count": 1,
                "A": sec.A or 0,
                "I33": sec.I33 or 0,
                "I22": sec.I22 or 0,
                "Dimensions": "",
                "W_total": w_total,
                "elem_id": eid,
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    display = []
    for sec_name, grp in df.groupby("Section"):
        r = grp.iloc[0]
        total_count = len(grp)
        total_w = grp["W_total"].sum()

        sec = md.sections.get(sec_name)
        dims = ""
        if sec:
            if hasattr(sec, "depth") and sec.depth:
                dims = f"d={_sig4(sec.depth)}"
                if hasattr(sec, "bf") and sec.bf:
                    dims += f" bf={_sig4(sec.bf)}"
                if hasattr(sec, "tf") and sec.tf:
                    dims += f" tf={_sig4(sec.tf)}"
                if hasattr(sec, "tw") and sec.tw:
                    dims += f" tw={_sig4(sec.tw)}"
            elif hasattr(sec, "od") and sec.od:
                dims = f"OD={_sig4(sec.od)}"
                if hasattr(sec, "t") and sec.t:
                    dims += f" t={_sig4(sec.t)}"
            elif hasattr(sec, "diameter") and sec.diameter:
                dims = f"\u2300={_sig4(sec.diameter)}"

        display.append(
            {
                "Section": sec_name,
                "Shape": r["Shape"],
                "Material": r["Material"],
                "Count": total_count,
                f"A ({length_unit}\u00b2)": _sig4(r["A"]),
                f"I33 ({length_unit}\u2074)": _sig4(r["I33"]),
                f"I22 ({length_unit}\u2074)": _sig4(r["I22"]),
                "Dimensions": _nbsp(dims),
                f"Total weight ({force_unit})": round(total_w, 1),
            }
        )

    return pd.DataFrame(display)


# ========================================================================
# Area (shell) section summary
# ========================================================================


def area_section_summary(md) -> pd.DataFrame:
    """Return a DataFrame of area (shell) sections with area and weight."""
    force_unit = md.units.get("F", "?")
    length_unit = md.units.get("L", "m")

    rows = []
    for aid, area_elem in md.area_elements.items():
        sec_name = md.area_assignments.get(aid, "")
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        mat = md.materials.get(sec.material)
        if mat is None:
            continue
        thickness = area_elem.thickness
        if thickness < 1e-12:
            continue

        # Polygon area via Newell's method
        pts = []
        for nid in area_elem.node_ids:
            nd = md.nodes.get(nid)
            if nd is None:
                break
            pts.append((nd.x, nd.y, nd.z))
        if len(pts) < 3:
            continue
        nx = ny = nz = 0.0
        for i in range(len(pts)):
            x1, y1, z1 = pts[i]
            x2, y2, z2 = pts[(i + 1) % len(pts)]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        area_mag = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        if area_mag < 1e-12:
            continue

        weight = area_mag * thickness * mat.unit_weight

        rows.append(
            {
                "Area": aid,
                "Section": sec_name,
                "Thickness": thickness,
                f"Area ({length_unit}\u00b2)": round(area_mag, 3),
                f"Weight ({force_unit})": round(weight, 1),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    display = []
    for sec_name, grp in df.groupby("Section"):
        total_area = grp[f"Area ({length_unit}\u00b2)"].sum()
        total_weight = grp[f"Weight ({force_unit})"].sum()
        count = len(grp)
        r = grp.iloc[0]
        display.append(
            {
                "Section": sec_name,
                "Count": count,
                "Thickness": r["Thickness"],
                f"Total area ({length_unit}\u00b2)": round(total_area, 3),
                f"Total weight ({force_unit})": round(total_weight, 1),
            }
        )

    result = pd.DataFrame(display)
    total_area = df[f"Area ({length_unit}\u00b2)"].sum()
    total_w = df[f"Weight ({force_unit})"].sum()
    total_row = {
        "Section": "<strong>Total</strong>",
        "Count": "",
        "Thickness": "",
        f"Total area ({length_unit}\u00b2)": round(total_area, 3),
        f"Total weight ({force_unit})": round(total_w, 1),
    }
    result = pd.concat([result, pd.DataFrame([total_row])], ignore_index=True)
    return result


# ========================================================================
# Modal analysis table (basic — 3 translational DOFs)
# ========================================================================


def modal_table(md, n_modes: int = 12, print_results: bool = False) -> pd.DataFrame:
    """Run elastic modal analysis and return a DataFrame of mode properties.

    Note:
        For new code, run modal analysis on an ``AnalysisBuilder`` and
        build the DataFrame from its result dict directly.

    Columns: Mode, Period (s), Freq (Hz), Mx (%), My (%), Mz (%).
    """
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model

    _mm = preprocess_model(
        md, {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
    )
    b = AnalysisBuilder(_mm, {"element_type": "elasticBeamColumn", "verbose": False})
    b.build_domain()
    b.compute_seismic_masses()
    modal = b.run_modal_analysis(num_modes=n_modes, print_results=print_results)
    mp = modal["modal_props"]
    rows = []
    for i in range(modal["num_modes"]):
        rows.append(
            {
                "Mode": i + 1,
                "Period (s)": round(modal["periods"][i], 4),
                "Freq (Hz)": round(modal["frequencies"][i], 4),
                "Mx (%)": round(mp.get("partiMassRatiosMX", [0])[i], 2),
                "My (%)": round(mp.get("partiMassRatiosMY", [0])[i], 2),
                "Mz (%)": round(mp.get("partiMassRatiosMZ", [0])[i], 2),
            }
        )
    return pd.DataFrame(rows), modal


# ========================================================================
# Modal analysis table (enhanced — 6 DOFs including rotational)
# ========================================================================


def modal_table_enhanced(md, n_modes: int = 12, print_results: bool = False):
    """Run elastic modal analysis and return a DataFrame with 6 DOF participation.

    Note:
        For new code, run modal analysis on an ``AnalysisBuilder`` and
        build the DataFrame from its result dict directly.

    Columns: Mode, Period, Mx%, My%, Mz%, Rx%, Ry%, Rz%  + a SUM row.
    """
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model

    _mm = preprocess_model(
        md, {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
    )
    b = AnalysisBuilder(_mm, {"element_type": "elasticBeamColumn", "verbose": False})
    b.build_domain()
    b.compute_seismic_masses()
    modal = b.run_modal_analysis(num_modes=n_modes, print_results=print_results)
    mp = modal["modal_props"]
    n = modal["num_modes"]

    def _col(key, idx):
        lst = mp.get(key, [])
        return round(lst[idx], 2) if idx < len(lst) else 0.0

    rows = []
    for i in range(n):
        rows.append(
            {
                "Mode": i + 1,
                "Period (s)": round(modal["periods"][i], 4),
                "Mx (%)": _col("partiMassRatiosMX", i),
                "My (%)": _col("partiMassRatiosMY", i),
                "Mz (%)": _col("partiMassRatiosMZ", i),
                "Rx (%)": _col("partiMassRatiosRMX", i),
                "Ry (%)": _col("partiMassRatiosRMY", i),
                "Rz (%)": _col("partiMassRatiosRMZ", i),
            }
        )
    df = pd.DataFrame(rows)
    pct_cols = ["Mx (%)", "My (%)", "Mz (%)", "Rx (%)", "Ry (%)", "Rz (%)"]
    for c in pct_cols:
        df[c] = df[c].apply(lambda v: f"{v:.2f}")
    # SUM row
    sum_row = {"Mode": "<strong>SUM</strong>"}
    for col in df.columns:
        if col not in {"Mode", "Period (s)"}:
            if col in pct_cols:
                sum_row[col] = f"{df[col].astype(float).sum():.2f}"
            else:
                sum_row[col] = round(df[col].sum(), 2)
        elif col == "Period (s)":
            sum_row[col] = "\u2014"
    df = pd.concat([df, pd.DataFrame([sum_row])], ignore_index=True)
    return df, modal, b


# ========================================================================
# Modal participation DataFrame from existing results (no re-analysis)
# ========================================================================


def modal_participation_df(modal_result: dict[str, Any]) -> Optional[pd.DataFrame]:
    """Build a modal participation DataFrame from existing modal results.

    Uses the modal_props dict returned by
    :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.run_modal_analysis`.
    Does **not** re-run the analysis.

    Columns: ``Mode``, ``Period (s)``, ``Mx (%)``, ``My (%)``, ``Mz (%)``,
    ``Rx (%)``, ``Ry (%)``, ``Rz (%)`` with a SUM row.

    Returns ``None`` when *modal_result* contains no period data.
    """
    mp = modal_result.get("modal_props", {})
    periods = list(modal_result.get("periods", []))
    n = len(periods)
    if n == 0:
        return None

    ratio_keys = [
        "partiMassRatiosMX",
        "partiMassRatiosMY",
        "partiMassRatiosMZ",
        "partiMassRatiosRMX",
        "partiMassRatiosRMY",
        "partiMassRatiosRMZ",
    ]
    col_names = [
        "Mx (%)",
        "My (%)",
        "Mz (%)",
        "Rx (%)",
        "Ry (%)",
        "Rz (%)",
    ]

    rows = []
    for i in range(n):
        row = {"Mode": i + 1, "Period (s)": round(periods[i], 4)}
        for key, col in zip(ratio_keys, col_names):
            vals = mp.get(key, [])
            row[col] = f"{round(vals[i], 2) if i < len(vals) else 0.0:.2f}"
        rows.append(row)

    df = pd.DataFrame(rows)
    sum_row = {"Mode": "<strong>SUM</strong>", "Period (s)": "\u2014"}
    for col in col_names:
        sum_row[col] = f"{df[col].astype(float).sum():.2f}"
    df = pd.concat([df, pd.DataFrame([sum_row])], ignore_index=True)
    return df


def save_modal_participation_html(
    modal_result: dict[str, Any],
    out_dir: str = "output",
) -> Optional[str]:
    """Build an HTML modal‑participation table from existing results and save.

    Also prints a clean text summary to the console.

    Returns the path to the saved HTML file, or ``None``.
    """
    from pathlib import Path

    df = modal_participation_df(modal_result)
    if df is None:
        return None

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "modal_table.html"

    # HTML — bold SUM row, minimal styling
    html = df.to_html(index=False, escape=False)
    html = html.replace(
        '<table border="1" class="dataframe">',
        '<table style="font-size:0.85em;border-collapse:collapse">',
    )
    with open(path, "w") as f:
        f.write(html)
    print(f"  Saved {path}")

    # Text summary
    print(f"\n  Modal participation summary ({len(df) - 1} modes):")
    header = f"  {'Mode':>5} {'Period':>8}  {'Mx%':>7} {'My%':>7} {'Mz%':>7}"
    print(header)
    print("  " + "-" * len(header))
    for _, row in df.iterrows():
        if row["Mode"] == "<strong>SUM</strong>":
            print("  " + "-" * len(header))
            m = "SUM"
            p = "\u2014"
        else:
            m = str(int(row["Mode"]))
            p = f"{row['Period (s)']:.4f}"
        mx = row.get("Mx (%)", "\u2014")
        my = row.get("My (%)", "\u2014")
        mz = row.get("Mz (%)", "\u2014")
        print(f"  {m:>5} {p:>8}  {mx:>7} {my:>7} {mz:>7}")
    print()

    return str(path)


# ========================================================================
# Linear analysis table formatting
# ========================================================================


def format_linear_table(df_linear: pd.DataFrame, units: dict) -> pd.DataFrame:
    """Format the linear analysis table for display.

    * Forces/moments use ``:,.0f`` format.
    * Displacements are converted to mm or in depending on model length unit.
    * Blank cells are replaced with em-dash.
    """
    lu = units.get("L", "m")
    if lu in ("m", "cm"):
        disp_target = "mm"
        disp_scale = 1000.0 if lu == "m" else 10.0
    elif lu in ("ft", "in"):
        disp_target = "in"
        disp_scale = 12.0 if lu == "ft" else 1.0
    else:
        disp_target = lu
        disp_scale = 1.0

    rows = []
    for _, row in df_linear.iterrows():
        r = {}
        r["Case"] = row["Case"]
        r["Type"] = row["Type"]
        for col in ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]:
            val = row.get(col, 0.0)
            if isinstance(val, (int, float)) and abs(val) > 0.001:
                r[col] = f"{val:,.0f}"
            else:
                r[col] = "\u2014"
        disp = row.get("Roof disp", None)
        if disp is not None and isinstance(disp, (int, float)) and abs(disp) > 1e-12:
            r[f"Roof disp ({disp_target})"] = f"{disp * disp_scale:,.1f}"
        else:
            r[f"Roof disp ({disp_target})"] = "\u2014"
        rows.append(r)

    return pd.DataFrame(rows)


def pushover_comparison_table(
    all_out,
    linear_df,
    patterns: list = None,
):
    """Compare pushover performance points with linear analysis results.

    When multiple patterns are present (e.g. ``'uniform'``,
    ``'triangular'``, ``'mode1'``), each direction produces one row
    per pattern, with the pattern name as part of the direction label.

    Parameters
    ----------
    all_out : dict
        ``{pattern_name: {direction: {"pp": {...}, ...}}}``.
    linear_df : pd.DataFrame
        Linear analysis results with ``Case``, ``Fx``, ``Fy`` columns.
    patterns : list, optional
        Pattern names to include (default: all keys in *all_out*).

    Returns
    -------
    pd.DataFrame
    """
    import pandas as pd

    if patterns is None:
        patterns = list(all_out.keys())
    rows = []
    has_linear = not linear_df.empty and "Case" in linear_df.columns
    for label in ["+X", "-X", "+Y", "-Y"]:
        for pat in patterns:
            pat_out = all_out.get(pat, {})
            data = pat_out.get(label)
            if data is None:
                continue
            pp = data["pp"]
            dir_letter = label[1]

            push_v = pp["V_base"] if pp.get("converged") else 0.0
            push_d = pp["D_roof"] if pp.get("converged") else 0.0

            row = {
                "Direction": f"{label} ({pat})",
                "Pushover V_base": round(push_v, 1) if push_v != 0 else "-",
                "Pushover D_roof": round(push_d, 4) if push_d != 0 else "-",
                "Ductility u": round(pp["mu"], 2) if pp.get("converged") else "-",
                "CSM converged": "Y" if pp.get("converged") else "N",
            }

            if has_linear:
                vcol = f"F{dir_letter.lower()}"
                rd = dir_letter
                rs_case = linear_df[linear_df["Case"] == f"RS-{rd}"]
                wind_plus = linear_df[linear_df["Case"] == f"Wind+{rd}"]
                rs_v = (
                    abs(rs_case[vcol].values[0])
                    if len(rs_case) and vcol in rs_case.columns
                    else 0.0
                )
                wind_v = (
                    abs(wind_plus[vcol].values[0])
                    if len(wind_plus) and vcol in wind_plus.columns
                    else 0.0
                )
                row[f"RS-{rd} V_base"] = round(rs_v, 1) if rs_v > 0 else "-"
                row[f"Wind+{rd} V_base"] = round(wind_v, 1) if wind_v > 0 else "-"

            rows.append(row)

    return pd.DataFrame(rows).fillna("-")
