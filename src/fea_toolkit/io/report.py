"""
Generic SAP2000 → pandas summary and report helpers.

All functions operate on a ``SAPModelData`` instance (``md``) and return
either a ``pd.DataFrame`` suitable for HTML/Quarto display or a simple
dict/string.  They are independent of any specific project — the same
functions can be used for any model parsed with ``SAP2000Parser``.
"""

from typing import Dict, List, Optional, Tuple, Any
import math
import numpy as np
import pandas as pd

from ..model.sap_data import SAPModelData, ShellSection


# ========================================================================
# Bounding box
# ========================================================================

def bounding_box(md) -> Dict[str, float]:
    """Return the model's node bounding box extents.

    Returns a dict with keys ``x_min``, ``x_max``, ``x_span``,
    ``y_min``, ``y_max``, ``y_span``, ``z_min``, ``z_max``, ``z_span``,
    and ``n_nodes``.
    """
    xs = [n.x for n in md.nodes.values()]
    ys = [n.y for n in md.nodes.values()]
    zs = [n.z for n in md.nodes.values()]
    return {
        "x_min": min(xs), "x_max": max(xs), "x_span": max(xs) - min(xs),
        "y_min": min(ys), "y_max": max(ys), "y_span": max(ys) - min(ys),
        "z_min": min(zs), "z_max": max(zs), "z_span": max(zs) - min(zs),
        "n_nodes": len(md.nodes),
    }


# ========================================================================
# Mass source summary
# ========================================================================

def summarise_mass_sources(md, g: float = 9.81) -> pd.DataFrame:
    """Return a DataFrame summarising all mass sources.

    Builds the model to compute total seismic mass and weight for each source.
    """
    from ..opensees.builder import OpenSeesBuilder

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
        b = OpenSeesBuilder(md, {
            "element_type": "elasticBeamColumn",
            "split_elements": True,
            "verbose": False,
        })
        b.build()
        node_masses = b.compute_seismic_masses(g=g)
        total_mass = sum(node_masses.values())
        total_weight = total_mass * g
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
    cols = [c for c in ["LoadPat", "Type", "SelfWtMult",
                         "\u03b1_max", "Tg (s)", "\u03b6", "SI"]
            if c in df.columns]
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

    Sums all joint loads, frame distributed loads, and self-weight for each
    load pattern defined in the model (as computed by the builder's
    ``load_totals`` attribute).
    """
    from ..opensees.builder import OpenSeesBuilder
    b = OpenSeesBuilder(md, {
        "element_type": "elasticBeamColumn",
        "split_elements": True,
        "verbose": False,
    })
    b.build()
    lt = getattr(b, "load_totals", {})
    rows = []
    fu = md.units.get("F", "?")
    for pname in sorted(lt, key=str.casefold):
        t = lt[pname]
        rows.append({
            "Load Pattern": pname,
            f"Fx ({fu})": round(t.get("fx", 0), 2),
            f"Fy ({fu})": round(t.get("fy", 0), 2),
            f"Fz ({fu})": round(t.get("fz", 0), 2),
        })
    return pd.DataFrame(rows)


# ========================================================================
# Material quantities
# ========================================================================

def material_summary(md) -> pd.DataFrame:
    """Return a DataFrame of material quantities (volume, weight)."""
    rows = []
    for eid, elem in md.frame_elements.items():
        if getattr(elem, 'inactive', False):
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
        rows.append({
            "material": sec.material,
            "section": sec_name,
            "elem_id": eid,
            "length_m": round(L, 3),
            "volume_m3": round(volume, 6),
            "weight": round(weight, 2),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    force_unit = md.units.get("F", "?")
    summary = df.groupby("material").agg(
        count=("elem_id", "count"),
        total_volume_m3=("volume_m3", "sum"),
        total_weight=("weight", "sum"),
    ).reset_index()
    total_row = pd.DataFrame([{
        "material": "<strong>Total</strong>",
        "count": summary["count"].sum(),
        "total_volume_m3": summary["total_volume_m3"].sum(),
        "total_weight": summary["total_weight"].sum(),
    }])
    summary = pd.concat([summary, total_row], ignore_index=True)
    summary.columns = [
        "Material", "Element count", "Total volume (m\u00b3)",
        f"Total weight ({force_unit})",
    ]
    for col in ["Total volume (m\u00b3)", f"Total weight ({force_unit})"]:
        summary[col] = summary[col].apply(
            lambda v: f"{v:.1f}" if v > 0 else "\u2014"
        )
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
        if getattr(elem, 'inactive', False):
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

        rows.append({
            "Section": sec_name,
            "Shape": shape,
            "Material": mat_name,
            "Count": 1,
            "A": sec.A if sec.A else 0,
            "I33": sec.I33 if sec.I33 else 0,
            "I22": sec.I22 if sec.I22 else 0,
            "Dimensions": "",
            "W_total": w_total,
            "elem_id": eid,
        })

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

        display.append({
            "Section": sec_name,
            "Shape": r["Shape"],
            "Material": r["Material"],
            "Count": total_count,
            f"A ({length_unit}\u00b2)": _sig4(r["A"]),
            f"I33 ({length_unit}\u2074)": _sig4(r["I33"]),
            f"I22 ({length_unit}\u2074)": _sig4(r["I22"]),
            "Dimensions": _nbsp(dims),
            f"Total weight ({force_unit})": round(total_w, 1),
        })

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
        area_mag = 0.5 * math.sqrt(nx*nx + ny*ny + nz*nz)
        if area_mag < 1e-12:
            continue

        weight = area_mag * thickness * mat.unit_weight

        rows.append({
            "Area": aid,
            "Section": sec_name,
            "Thickness": thickness,
            f"Area ({length_unit}\u00b2)": round(area_mag, 3),
            f"Weight ({force_unit})": round(weight, 1),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    display = []
    for sec_name, grp in df.groupby("Section"):
        total_area = grp[f"Area ({length_unit}\u00b2)"].sum()
        total_weight = grp[f"Weight ({force_unit})"].sum()
        count = len(grp)
        r = grp.iloc[0]
        display.append({
            "Section": sec_name,
            "Count": count,
            "Thickness": r["Thickness"],
            f"Total area ({length_unit}\u00b2)": round(total_area, 3),
            f"Total weight ({force_unit})": round(total_weight, 1),
        })

    result = pd.DataFrame(display)
    total_area = df[f"Area ({length_unit}\u00b2)"].sum()
    total_w = df[f"Weight ({force_unit})"].sum()
    total_row = {
        "Section": "**Total**",
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

    Columns: Mode, Period (s), Freq (Hz), Mx (%), My (%), Mz (%).
    """
    from ..opensees.builder import OpenSeesBuilder
    b = OpenSeesBuilder(md, {
        "element_type": "elasticBeamColumn",
        "split_elements": True,
        "verbose": False,
    })
    b.build()
    b.compute_seismic_masses()
    modal = b.run_modal_analysis(num_modes=n_modes, print_results=print_results)
    mp = modal["modal_props"]
    rows = []
    for i in range(modal["num_modes"]):
        rows.append({
            "Mode": i + 1,
            "Period (s)": round(modal["periods"][i], 4),
            "Freq (Hz)": round(modal["frequencies"][i], 4),
            "Mx (%)": round(mp.get("partiMassRatiosMX", [0])[i], 2),
            "My (%)": round(mp.get("partiMassRatiosMY", [0])[i], 2),
            "Mz (%)": round(mp.get("partiMassRatiosMZ", [0])[i], 2),
        })
    return pd.DataFrame(rows), modal


# ========================================================================
# Modal analysis table (enhanced — 6 DOFs including rotational)
# ========================================================================

def modal_table_enhanced(md, n_modes: int = 12, print_results: bool = False):
    """Run elastic modal analysis and return a DataFrame with 6 DOF participation.

    Columns: Mode, Period, Mx%, My%, Mz%, Rx%, Ry%, Rz%  + a SUM row.
    """
    from ..opensees.builder import OpenSeesBuilder
    b = OpenSeesBuilder(md, {
        "element_type": "elasticBeamColumn",
        "split_elements": True,
        "verbose": False,
    })
    b.build()
    b.compute_seismic_masses()
    modal = b.run_modal_analysis(num_modes=n_modes, print_results=print_results)
    mp = modal["modal_props"]
    n = modal["num_modes"]

    def _col(key, idx):
        lst = mp.get(key, [])
        return round(lst[idx], 2) if idx < len(lst) else 0.0

    rows = []
    for i in range(n):
        rows.append({
            "Mode": i + 1,
            "Period (s)": round(modal["periods"][i], 4),
            "Mx (%)": _col("partiMassRatiosMX", i),
            "My (%)": _col("partiMassRatiosMY", i),
            "Mz (%)": _col("partiMassRatiosMZ", i),
            "Rx (%)": _col("partiMassRatiosRMX", i),
            "Ry (%)": _col("partiMassRatiosRMY", i),
            "Rz (%)": _col("partiMassRatiosRMZ", i),
        })
    df = pd.DataFrame(rows)
    pct_cols = ["Mx (%)", "My (%)", "Mz (%)", "Rx (%)", "Ry (%)", "Rz (%)"]
    for c in pct_cols:
        df[c] = df[c].apply(lambda v: f"{v:.2f}")
    # SUM row
    sum_row = {"Mode": "<strong>SUM</strong>"}
    for col in df.columns:
        if col != "Mode" and col != "Period (s)":
            if col in pct_cols:
                sum_row[col] = f"{df[col].astype(float).sum():.2f}"
            else:
                sum_row[col] = round(df[col].sum(), 2)
        elif col == "Period (s)":
            sum_row[col] = "\u2014"
    df = pd.concat([df, pd.DataFrame([sum_row])], ignore_index=True)
    return df, modal, b


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
        DataFrame with one row per brace, containing section name, length,
        slenderness ratio, and Euler buckling load.
    """
    from ..model.sap_data import (
        PipeSection, AngleSection, DoubleAngleSection, TeeSection, ChannelSection,
    )
    brace_shape_types = (
        PipeSection, AngleSection, DoubleAngleSection,
        TeeSection, ChannelSection,
    )
    fu = md.units.get("F", "N")
    lu = md.units.get("L", "m")

    rows = []
    for eid, elem in md.frame_elements.items():
        if getattr(elem, 'inactive', False):
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

        P_cr = (math.pi ** 2 * E * I22) / ((K * L) ** 2)
        r_val = math.sqrt(I22 / A)
        slenderness = (K * L) / r_val if r_val > 0 else float('inf')

        rows.append({
            "elem_id": eid,
            "section": sec_name,
            "shape": type(sec).__name__.replace("Section", ""),
            "material": sec.material,
            f"length ({lu})": round(L, 3),
            f"A ({lu}\u00b2)": round(A, 6),
            f"I22 ({lu})\u2074": round(I22, 8),
            "\u03bb (slenderness)": round(slenderness, 1),
            f"P_cr ({fu})": round(P_cr, 0),
        })

    if not rows:
        return pd.DataFrame({"Note": ["No brace sections found in model."]})

    df = pd.DataFrame(rows)
    df = df.sort_values(f"length ({lu})", ascending=False).head(n_longest).reset_index(drop=True)
    return df
