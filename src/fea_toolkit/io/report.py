"""
Generic SAP2000 → pandas summary and report helpers.

All functions operate on a ``SAPModelData`` instance (``md``) and return
either a ``pd.DataFrame`` suitable for HTML/Quarto display or a simple
dict/string.  They are independent of any specific project — the same
functions can be used for any model parsed with ``SAP2000Parser``.
"""

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..model.sap_data import SAPModelData, ShellSection

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
    """Build the model and return a DataFrame of total applied load per pattern.

    Note:
        Load totals under the two-stage ``AnalysisBuilder`` are stored as
        scalar magnitudes rather than per-component dicts; for new
        workflows prefer ``AnalysisBuilder.check_load_equilibrium()`` or
        direct access to ``builder.load_totals``.

    Sums all joint loads, frame distributed loads, and self-weight for each
    load pattern defined in the model (as computed by the builder's
    ``load_totals`` attribute).
    """
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model

    _mm = preprocess_model(
        md, {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
    )
    b = AnalysisBuilder(_mm, {"element_type": "elasticBeamColumn", "verbose": False})
    b.build_domain()
    lt = getattr(b, "load_totals", {})

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

    rows = []
    fu = md.units.get("F", "?")
    for pname in sorted(lt, key=str.casefold):
        if pname not in linstatic_patterns:
            continue
        t = lt[pname]
        if isinstance(t, dict):
            fx = round(t.get("fx", 0), 2)
            fy = round(t.get("fy", 0), 2)
            fz = round(t.get("fz", 0), 2)
        else:
            fx = round(float(t), 2)
            fy = 0.0
            fz = 0.0
        # Skip patterns with zero load in all directions
        if fx == 0.0 and fy == 0.0 and fz == 0.0:
            continue
        rows.append(
            {
                "Load Pattern": pname,
                f"Fx ({fu})": fx,
                f"Fy ({fu})": fy,
                f"Fz ({fu})": fz,
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


def _force_unit_label(md) -> str:
    fu = md.units.get("F", "")
    lu = md.units.get("L", "")
    return f"{fu}\u00b7{lu}"


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


# ========================================================================
# Storey level summary
# ========================================================================


def storey_level_summary(md, z_tol: float = 0.01, print_results: bool = True) -> pd.DataFrame:
    """Report storey (floor) levels inferred from the model's node Z‑coordinates.

    Nodes are clustered by their Z coordinate.  For each level the
    following is reported:

    * **Level** – auto‑assigned label (Roof, Level N, Base / Ground).
    * **Z** – elevation in model length units.
    * **Nodes** – number of distinct nodes at that level.
    * **Restrained** – nodes with at least one fixed DOF.
    * **Diaph nodes** – nodes that would participate in a rigid floor
      diaphragm (all *unrestrained* nodes at the level).
    * **Horiz frames** – frame elements whose I‑node *and* J‑node lie at
      this level (i.e. horizontal beams).
    * **Horiz areas** – area elements whose *all* corner nodes lie at
      this level (i.e. slabs / floors).

    The printed table is a fixed‑width formatted view of the DataFrame.
    Each row corresponds to one detected storey level, sorted top‑to‑bottom.
    ``Horizontal`` means the element's nodes all lie at the same Z within
    the grouping tolerance — frames spanning between levels (columns,
    walls) are excluded, as are vertical or tilted area elements.  The
    ``Diaph nodes`` column shows how many nodes would be tied together by
    a rigid diaphragm at that level, which equals all unrestrained nodes
    (restrained nodes are typically at the base and excluded from the
    diaphragm).  A low frame or area count at a level may indicate a
    mezzanine or partial floor; a zero count at the base is expected
    since base nodes are individually restrained rather than
    diaphragm‑connected.

    This is useful whenever storey levels are inferred rather than
    explicitly provided by the analysis model.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.
        z_tol: Tolerance (in model length units) for grouping Z
            coordinates (default 0.01).
        print_results: If True (default), print the table to stdout.

    Returns:
        A :class:`pandas.DataFrame` sorted top‑to‑bottom.
    """

    # ── 1. Cluster nodes by Z (tolerance-based) ────────────────────────
    z_levels = {}
    sorted_nodes = sorted(md.nodes.items(), key=lambda x: x[1].z, reverse=True)
    cluster_reps = []
    for nid, nd in sorted_nodes:
        assigned = False
        for rep_z in cluster_reps:
            if abs(nd.z - rep_z) <= z_tol:
                z_levels.setdefault(rep_z, []).append(nid)
                assigned = True
                break
        if not assigned:
            cluster_reps.append(nd.z)
            z_levels.setdefault(nd.z, []).append(nid)

    sorted_zs = sorted(z_levels, reverse=True)

    # ── 2. Assign level labels ─────────────────────────────────────────
    # We try to give reasonable labels based on position in sorted list.
    n_levels = len(sorted_zs)
    level_labels = {}
    for i, z in enumerate(sorted_zs):
        if i == 0:
            level_labels[z] = "Roof"
        elif i == n_levels - 1:
            level_labels[z] = "Base / Ground"
        else:
            level_labels[z] = f"Level {n_levels - i - 1}"

    # ── 3. Pre‑compute restrained node set ─────────────────────────────
    restrained_set = set()
    for nid, r in md.restraints.items():
        if any(d == 1 for d in r.dofs):
            restrained_set.add(nid)

    # ── 4. Classify horizontal frames (beams) ──────────────────────────
    # A frame is "horizontal" if |Z_i - Z_j| < z_tol.  Assign it to the
    # Z level that matches both endpoints.
    level_horiz_frames = {z: set() for z in sorted_zs}
    for eid, elem in md.frame_elements.items():
        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        if abs(ni.z - nj.z) >= z_tol:
            continue  # not horizontal
        zkey = round(ni.z / z_tol) * z_tol
        if zkey in level_horiz_frames:
            level_horiz_frames[zkey].add(eid)
        else:
            # fallback: assign to nearest level
            nearest = min(sorted_zs, key=lambda z: abs(z - zkey))
            level_horiz_frames[nearest].add(eid)

    # ── 5. Classify horizontal areas (slabs) ───────────────────────────
    # An area is "horizontal" if all its corner nodes span ≤ z_tol in Z.
    level_horiz_areas = {z: set() for z in sorted_zs}
    for aid, area in md.area_elements.items():
        zs = []
        for nid in area.node_ids:
            nd = md.nodes.get(nid)
            if nd is None:
                break
            zs.append(nd.z)
        if len(zs) < 3:
            continue  # requires at least 3 nodes
        if max(zs) - min(zs) >= z_tol:
            continue  # not horizontal
        zkey = round(sum(zs) / len(zs) / z_tol) * z_tol
        if zkey in level_horiz_areas:
            level_horiz_areas[zkey].add(aid)
        else:
            nearest = min(sorted_zs, key=lambda z: abs(z - zkey))
            level_horiz_areas[nearest].add(aid)

    # ── 6. Assemble table ──────────────────────────────────────────────
    rows = []
    for z in sorted_zs:
        node_ids = z_levels[z]
        n_nodes = len(node_ids)
        n_restrained = sum(1 for nid in node_ids if nid in restrained_set)
        n_horiz_frames = len(level_horiz_frames[z])
        n_horiz_areas = len(level_horiz_areas[z])
        # Diaphragm nodes = all unrestrained nodes at this level
        diaph_nodes = [nid for nid in node_ids if nid not in restrained_set]
        n_diaph = len(diaph_nodes)

        rows.append(
            {
                "Level": level_labels[z],
                "Z": round(z, 3),
                "Nodes": n_nodes,
                "Restrained": n_restrained,
                "Diaph nodes": n_diaph,
                "Horiz frames": n_horiz_frames,
                "Horiz areas": n_horiz_areas,
            }
        )

    df = pd.DataFrame(rows)

    if print_results:
        # Use a clean formatted print
        col_widths = {
            "Level": max(len("Level"), *(len(str(r["Level"])) for r in rows)),
            "Z": 8,
            "Nodes": 6,
            "Restrained": 11,
            "Diaph nodes": 12,
            "Horiz frames": 13,
            "Horiz areas": 12,
        }
        sep = "  ".join("=" * w for w in col_widths.values())
        header = "  ".join(f"{k:<{col_widths[k]}}" for k in col_widths)
        print(sep)
        print(header)
        print(sep)
        for r in rows:
            row = (
                f"{r['Level']:<{col_widths['Level']}}  "
                f"{r['Z']:>8.3f}  "
                f"{r['Nodes']:>6}  "
                f"{r['Restrained']:>11}  "
                f"{r['Diaph nodes']:>12}  "
                f"{r['Horiz frames']:>13}  "
                f"{r['Horiz areas']:>12}"
            )
            print(row)
        print(sep)
        print(f"\n{len(sorted_zs)} storey levels detected (Z tolerance = {z_tol}).")

    return df


# ========================================================================
# Euler buckling check for braces
# ========================================================================


def brace_buckling_check(md, n_longest: int = 2, K: float = 1.0) -> pd.DataFrame:
    """Identify the longest braces and compute their Euler buckling capacity.

    Braces are identified by their section shape (Pipe, Angle, Double Angle,
    Tee, or Channel).  For each brace, the Euler buckling load is:

        P_cr = \u03c0\u00b2 E I_22 / (K L)\u00b2

    where I_22 is the minor-axis moment of inertia, L is the element length,
    and K is the effective length factor.

    Args:
        md: The parsed SAPModelData.
        n_longest: Number of longest braces to report (default 2).
        K: Effective length factor (default 1.0 \u2014 pinned-pinned).

    Returns:
        DataFrame with columns: ``Element`` (SAP2000 frame ID),
        ``Section``, ``Shape``, ``Material``,
        ``Length ({lu})`` (with the model's length unit),
        ``A ({lu}²)`` (cross-section area),
        ``I22 ({lu}⁴)`` (minor-axis second moment),
        ``Slenderness`` (λ = KL/r), and
        ``P_cr ({fu})`` (Euler buckling load, in force units).
    """
    from ..model.sap_data import (
        AngleSection,
        ChannelSection,
        DoubleAngleSection,
        PipeSection,
        TeeSection,
    )

    brace_shape_types = (
        PipeSection,
        AngleSection,
        DoubleAngleSection,
        TeeSection,
        ChannelSection,
    )
    fu = md.units.get("F", "N")
    lu = md.units.get("L", "m")

    rows = []
    for eid, elem in md.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sec_name = md.frame_assignments.get(eid)
        if not sec_name or sec_name not in md.sections:
            continue
        sec = md.sections[sec_name]
        if not isinstance(sec, brace_shape_types):
            continue
        mat = md.materials.get(sec.material)
        if mat is None:
            continue

        ni = md.nodes.get(elem.node_i)
        nj = md.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue

        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        if L < 1e-12:
            continue

        E = mat.E_mod if mat.E_mod and mat.E_mod > 0 else 2.0e11
        I22 = sec.I22 if sec.I22 and sec.I22 > 0 else sec.I33
        A = sec.A if sec.A and sec.A > 0 else 1e-4

        P_cr = (math.pi**2 * E * I22) / ((K * L) ** 2)
        r_val = math.sqrt(I22 / A)
        slenderness = (K * L) / r_val if r_val > 0 else float("inf")

        rows.append(
            {
                "Element": eid,
                "Section": sec_name,
                "Shape": type(sec).__name__.replace("Section", ""),
                "Material": sec.material,
                f"Length ({lu})": round(L, 3),
                f"A ({lu}\u00b2)": round(A, 6),
                f"I22 ({lu}\u2074)": round(I22, 8),
                "Slenderness": round(slenderness, 1),
                f"P_cr ({fu})": round(P_cr, 0),
            }
        )

    if not rows:
        return pd.DataFrame({"Note": ["No brace sections found in model."]})

    df = pd.DataFrame(rows)
    df = df.sort_values(f"Length ({lu})", ascending=False).head(n_longest).reset_index(drop=True)
    return df


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
        f"| Wind +{wind_case_x[-1]} (Y‑Z face) | {x_face:.0f} | {fx:,.0f} | {p_x:.2f} |",
        f"| Wind +{wind_case_y[-1]} (X‑Z face) | {y_face:.0f} | {fy:,.0f} | {p_y:.2f} |",
    ]

    if max(p_x, p_y) > 0 and abs(p_x - p_y) / max(p_x, p_y) < 0.1:
        lines.append("")
        lines.append(
            "✅ Pressures are within 10 % — wind loads are consistent "
            "with the bounding box face areas."
        )

    return "\n".join(lines)


def static_load_verification(md, mesh_model, config: dict = None):
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
    rows = []
    for _, row in merged.iterrows():
        pname = row["Load Pattern"]
        pat = md.load_patterns.get(pname)
        pat_type = pat.pattern_type if pat else "?"
        ax = row.get(f"Fx ({fu})", 0.0) or 0.0
        ay = row.get(f"Fy ({fu})", 0.0) or 0.0
        az = row.get(f"Fz ({fu})", 0.0) or 0.0
        rx = row.get(f"Reaction Fx ({fu})", 0.0) or 0.0
        ry = row.get(f"Reaction Fy ({fu})", 0.0) or 0.0
        rz = row.get(f"Reaction Fz ({fu})", 0.0) or 0.0
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
    md: "SAPModelData",
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
    import math

    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    # ── Static cases: auto-detect LinStatic, then merge user overrides ──
    # Always auto-detect all LinStatic cases from the model
    static_cases: dict[str, dict[str, float]] = {}
    for cname, lc in md.load_cases.items():
        if lc.case_type != "LinStatic":
            continue
        sd = lc.case_data.get("CASE - STATIC 1 - LOAD ASSIGNMENTS", [])
        if isinstance(sd, list):
            pats = {a["LoadName"]: a["LoadSF"] for a in sd if "LoadName" in a}
        elif isinstance(sd, dict):
            pats = {sd["LoadName"]: sd["LoadSF"]}
        else:
            pats = {}
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
                sd = lc.case_data.get("CASE - STATIC 1 - LOAD ASSIGNMENTS", [])
                if isinstance(sd, list):
                    pats = {a["LoadName"]: a["LoadSF"] for a in sd if "LoadName" in a}
                elif isinstance(sd, dict):
                    pats = {sd["LoadName"]: sd["LoadSF"]}
                else:
                    pats = {}
                if pats:
                    static_cases[entry] = pats
            elif isinstance(entry, dict):
                for cname, pat_dict in entry.items():
                    static_cases[cname] = pat_dict

    # ── Filter out cases whose constituent patterns have zero loads ──
    def _pattern_has_loads(m, pname: str) -> bool:
        lp = m.load_patterns.get(pname)
        if lp is not None and lp.self_weight_factor > 0:
            return True
        return (
            any(ld.pattern == pname for ld in m.frame_dist_loads)
            or any(ld.pattern == pname for ld in m.joint_loads)
            or any(ld.pattern == pname for ld in m.area_gravity_loads)
            or any(ld.pattern == pname for ld in m.edge_loads_from_areas)
        )

    static_cases = {
        cname: pats
        for cname, pats in static_cases.items()
        if any(_pattern_has_loads(mesh_model, p) for p in pats)
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
    if spec_cfg:
        from fea_toolkit.spectrum import _build_spectrum

        T_spec_built, Sa_spec_built, alpha_max, tg, _zeta_eff, _ = _build_spectrum(spec_cfg)
        T_spec = T_spec_built
        Sa_spec = Sa_spec_built
    else:
        # Fallback: rare spectrum with 5% damping
        from fea_toolkit.spectrum import _gb50011_spectrum

        zeta = 0.05
        gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
        eta_1 = max(0.0, 0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta))
        eta_2 = max(0.55, 1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta))
        md.units.get("L", "m")
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
            modal = ab.run_modal_analysis(num_modes=12, print_results=False)

            def spectrum_func(T):
                return float(np.interp(T, T_spec, Sa_spec))

            rs = ab.run_response_spectrum_analysis(
                num_modes=12,
                modal_periods=modal["periods"],
                spectrum_periods=T_spec,
                spectrum_accels=Sa_spec,
                direction=rs_dir,
                damping_ratio=0.05,
                print_results=False,
            )
            rs.get("base_shear_cqc", 0.0)
            rs.get("base_moment_cqc", 0.0)
            v_srss = rs.get("base_shear_srss", 0.0)
            m_srss = rs.get("base_moment_srss", 0.0)
            # Full 6-DoF reactions with overturning (from new centralized
            # per-mode lever-arm computation in run_response_spectrum_analysis)
            r_cqc = rs.get("base_reactions_cqc", {})

            rs_disp_cqc, rs_disp_srss = ab.compute_rs_nodal_displacements(
                num_modes=12,
                modal_periods=modal["periods"],
                eigenvalues=modal["eigenvalues"],
                spectrum_func=spectrum_func,
                direction=rs_dir,
                damping_ratio=0.05,
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
