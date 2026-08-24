"""3D force / moment diagram rendering and NPZ force-data helpers.

The unified 2D/RS/3D force-diagram dispatcher lives in
:mod:`fea_toolkit.plotting.force_diagram`; this module provides the 3D
renderer and the NPZ element-data loader."""

from typing import Optional

import numpy as np

from ..model.geometry import get_local_axes
from ..utils import compute_flag_parts
from .viz_model import _resolve_frame_node


def _render_frame_force_diagram(
    plotter,
    source,
    frames: list,
    nodes: dict,
    force_map: dict,
    quantity: str,
    mode: str,
    *,
    show_original: bool = True,
    moment_scale: Optional[float] = None,
) -> tuple[float, float]:
    """Render frame force diagram (flags or tubes) on an existing plotter.

    *force_map* maps ``{frame_index: {Fx, Fy, Fz, Mx, My, Mz, Fx_j, ...}}`` —
    the caller is responsible for building this dict from whatever source
    (static force data, pushover envelope, etc.).

    Returns ``(model_height, max_abs_val)`` for callers that need to
    compute auto-scaling or add supplementary geometry.
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return 0.0, 0.0

    model_height = 0.0
    max_abs_val = 0.0
    segs_flag: list = []  # (p_i, p_j, vn, Fi, Fj)
    segs_tube: list = []  # (p_i, p_j, val)

    for idx, fr in enumerate(frames):
        if idx not in force_map:
            continue
        f_local = _compute_local_forces(source, fr, nodes, force_map[idx], quantity)
        if f_local is None:
            continue

        ni = _resolve_frame_node(nodes, fr, "i")
        nj = _resolve_frame_node(nodes, fr, "j")
        if ni is None or nj is None:
            continue

        p_i = np.array([ni["x"], ni["y"], ni["z"]])
        p_j = np.array([nj["x"], nj["y"], nj["z"]])
        model_height = max(model_height, ni["z"], nj["z"])

        v_i = f_local.get(quantity, 0.0)
        v_j = f_local.get(quantity + "_j", 0.0)
        max_abs_val = max(max_abs_val, abs(v_i), abs(v_j))

        if mode == "flag":
            vn = _compute_flag_direction(f_local, fr, nodes, quantity)
            segs_flag.append((p_i, p_j, vn, v_i, v_j))
        else:
            segs_tube.append((p_i, p_j, (v_i + v_j) * 0.5))

    if not segs_flag and not segs_tube:
        return model_height, max_abs_val

    # Auto-scale
    if mode == "flag" and moment_scale is None:
        moment_scale = (model_height * 0.2) / max(max_abs_val, 1.0)

    # Show original centreline in grey
    if show_original:
        for seg in segs_flag if mode == "flag" else [(p_i, p_j) for p_i, p_j, _ in segs_tube]:
            p_i, p_j = seg[0], seg[1]
            n = max(2, int(np.linalg.norm(p_j - p_i) * 2))
            poly = pv.lines_from_points(np.linspace(p_i, p_j, n))
            plotter.add_mesh(poly, color="lightgrey", line_width=1, opacity=0.4)

    if mode == "flag":
        for p_i, p_j, vn, Fi, Fj in segs_flag:
            for verts, col_val in compute_flag_parts(p_i, p_j, vn, Fi, Fj, moment_scale):
                _add_coloured_poly(plotter, verts, col_val, max_abs_val)
    else:
        for p_i, p_j, val in segs_tube:
            _add_coloured_tube(plotter, p_i, p_j, val, max_abs_val)

    return model_height, max_abs_val


def _resolve_npz_static_case(source: dict, combo: str = None) -> str:
    """Determine the NPZ static case prefix (e.g. ``'static/DEAD/'``)."""
    from ..io.npz_reader import _get_static_cases

    cases = _get_static_cases(source)
    if not cases:
        raise ValueError("No static cases found in NPZ data.")
    name = combo if combo and combo in cases else cases[0]
    return f"static/{name}/"


def _extract_npz_frame_forces(source, case_prefix, frames):
    """Build force_map from NPZ arrays for the given static case.

    Returns ``{idx: {Fx, Fy, Fz, Mx, My, Mz, Fx_j, ..., local variants}}``.

    When the NPZ declares ``forces_coordinate_system == "local"`` (the
    current schema — the recorders use OpenSees ``localForces``), the
    ``fx_i ... mz_j`` arrays are already in the element local coordinate
    system.  The ``*_i_local`` / ``*_j_local`` variant keys are then
    populated from those arrays directly so
    :func:`_compute_local_forces` uses them verbatim (no second
    rotation).  Legacy global-force NPZ files (no metadata key) keep
    the old behaviour.
    """
    import numpy as np

    _cs_val = None
    _cs_arr = source.get("forces_coordinate_system")
    if _cs_arr is not None:
        try:
            _cs_val = str(np.asarray(_cs_arr).ravel()[0])
        except Exception:
            _cs_val = None
    _forces_local = _cs_val == "local"

    force_map = {}
    qty_list = ["fx", "fy", "fz", "mx", "my", "mz"]
    for idx in range(len(frames)):
        entry = {}
        for q in qty_list:
            key_i = f"{case_prefix}{q}_i"
            key_j = f"{case_prefix}{q}_j"
            arr_i = source.get(key_i)
            arr_j = source.get(key_j)
            if arr_i is not None and idx < len(arr_i):
                entry[q.upper()] = float(arr_i[idx])
            if arr_j is not None and idx < len(arr_j):
                entry[f"{q.upper()}_j"] = float(arr_j[idx])
            # Local variants
            if _forces_local:
                # Forces are already stored in local coordinates — expose
                # them under the local variant keys directly.
                if arr_i is not None and idx < len(arr_i):
                    entry[f"{q.upper()}_i_local"] = float(arr_i[idx])
                if arr_j is not None and idx < len(arr_j):
                    entry[f"{q.upper()}_j_local"] = float(arr_j[idx])
            else:
                loc_i = f"{case_prefix}{q}_i_local"
                loc_j = f"{case_prefix}{q}_j_local"
                if loc_i in source:
                    entry[f"{q.upper()}_i_local"] = float(source[loc_i][idx])
                if loc_j in source:
                    entry[f"{q.upper()}_j_local"] = float(source[loc_j][idx])
        if entry:
            force_map[idx] = entry
    return force_map


def _compute_local_forces(source, fr, nodes, force_entry, quantity):
    """Transform global forces to local for one frame element.

    Local axes are computed from the element geometry via
    ``get_SAP_vecxz``, so the result is the same regardless of
    whether *source* is a builder or NPZ dict.  When pre-computed
    local arrays exist in the NPZ (``*_i_local`` / ``*_j_local``)
    those are used directly.
    """
    import numpy as np

    # If pre-computed local values exist in the entry, use them directly
    q_upper = quantity.upper()
    loc_key = f"{q_upper}_i_local"
    loc_key_j = f"{q_upper}_j_local"
    if loc_key in force_entry and loc_key_j in force_entry:
        return {
            quantity: force_entry[loc_key],
            f"{quantity}_j": force_entry[loc_key_j],
        }

    # Otherwise compute from global forces
    # Get node coordinates for element axis
    ni = _resolve_frame_node(nodes, fr, "i")
    nj = _resolve_frame_node(nodes, fr, "j")
    if ni is None or nj is None:
        return None

    axis = np.array([nj["x"] - ni["x"], nj["y"] - ni["y"], nj["z"] - ni["z"]])
    axis_len = np.linalg.norm(axis)
    if axis_len < 1e-12:
        return None
    axis = axis / axis_len

    # Compute local axes
    try:
        vx, vy, vz = get_local_axes(axis)
    except Exception:
        return None

    T = np.vstack([vx, vy, vz])  # (3, 3) local ← global

    # Extract global forces
    def _g(q):
        return force_entry.get(q, force_entry.get(q.lower(), 0.0))

    f_i = np.array([_g("FX"), _g("FY"), _g("FZ")])
    m_i = np.array([_g("MX"), _g("MY"), _g("MZ")])
    f_j = np.array([_g("FX_j"), _g("FY_j"), _g("FZ_j")])
    m_j = np.array([_g("MX_j"), _g("MY_j"), _g("MZ_j")])

    f_i_loc = T @ f_i
    m_i_loc = T @ m_i
    f_j_loc = T @ f_j
    m_j_loc = T @ m_j

    return {
        "Fx": f_i_loc[0],
        "Fy": f_i_loc[1],
        "Fz": f_i_loc[2],
        "Mx": m_i_loc[0],
        "My": m_i_loc[1],
        "Mz": m_i_loc[2],
        "Fx_j": f_j_loc[0],
        "Fy_j": f_j_loc[1],
        "Fz_j": f_j_loc[2],
        "Mx_j": m_j_loc[0],
        "My_j": m_j_loc[1],
        "Mz_j": m_j_loc[2],
    }


def _compute_flag_direction(f_local, fr, nodes, quantity):
    """Determine the flag extrusion direction (vn) for a frame element."""
    import numpy as np

    axis = _get_element_axis(fr, nodes)
    if axis is None:
        return np.array([0.0, 1.0, 0.0])
    try:
        _, vy, vz = get_local_axes(axis)
    except Exception:
        vy = np.array([0.0, 1.0, 0.0])
        vz = np.array([0.0, 0.0, 1.0])

    if quantity == "Fx":
        return vz.copy()
    elif quantity == "Fy":
        return vy.copy()
    elif quantity == "Fz":
        return vz.copy()
    elif quantity == "Mx":
        return vy.copy()
    elif quantity == "My":
        return -vz.copy()
    elif quantity == "Mz":
        return vy.copy()
    else:
        return vz.copy()


def _get_element_axis(fr, nodes):
    """Return unit vector along a frame element from resolved data."""
    import numpy as np

    ni = _resolve_frame_node(nodes, fr, "i")
    nj = _resolve_frame_node(nodes, fr, "j")
    if ni is None or nj is None:
        return None
    d = np.array([nj["x"] - ni["x"], nj["y"] - ni["y"], nj["z"] - ni["z"]])
    norm = np.linalg.norm(d)
    return d / norm if norm > 1e-12 else None


def _add_coloured_poly(plotter, verts, col_val, max_abs_val):
    """Add a coloured polygon to the plotter (flag mode)."""
    import numpy as np
    import pyvista as pv

    pts_arr = np.array(verts)
    n = len(verts)
    surf = pv.PolyData(pts_arr, faces=[n, *list(range(n))])
    t = min(abs(col_val) / max(max_abs_val, 1.0), 1.0)
    if col_val >= 0:
        colour = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
    else:
        colour = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
    plotter.add_mesh(
        surf, color=colour, opacity=0.85, show_edges=False, smooth_shading=False, lighting=False
    )


def _add_coloured_tube(plotter, p_i, p_j, val, max_abs_val):
    """Add a coloured cylinder to the plotter (tube mode)."""
    import numpy as np
    import pyvista as pv

    axis = p_j - p_i
    axis_len = np.linalg.norm(axis)
    if axis_len < 1e-12:
        return
    p_mid = (p_i + p_j) / 2.0
    direction = axis / axis_len
    t = min(abs(val) / max(max_abs_val, 1.0), 1.0)
    radius = max(axis_len * 0.02, 0.05)
    if val >= 0:
        colour = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
    else:
        colour = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
    cyl = pv.Cylinder(center=p_mid, direction=direction, radius=radius, height=axis_len * 0.9)
    plotter.add_mesh(cyl, color=colour, opacity=0.5, show_edges=False, lighting=False)


def _load_npz_for_plotting(npz_path: str, combo: str = None) -> dict:
    """Load a unified-format NPZ results file and build element‑centric arrays.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    combo : str or None
        Static case name.  ``None`` = first available case.

    Returns a dict with:
        - elem_data: list of dicts (one per sub‑element) with keys:
            sap_id, (x|y|z)_i, (x|y|z)_j, mid_z,
            fx_i, fy_i, fz_i, mx_i, my_i, mz_i,
            fx_j, fy_j, fz_j, mx_j, my_j, mz_j,
            and ``_local`` variants
        - metadata: always ``{}`` (reserved for future use)
        - force_unit, length_unit: unit strings
        - raw_data: the loaded npz dict
    """
    from ..io.npz_reader import _get_static_cases, read_results

    d = read_results(npz_path)

    force_unit = "?"
    length_unit = "?"
    elem_data: list[dict] = []

    # ── Unified schema ────────────────────────────────────────────
    cases = _get_static_cases(d)
    if combo is not None and combo not in cases:
        raise ValueError(f"Case '{combo}' not found in NPZ. Available: {cases}")
    case = combo if combo and combo in cases else (cases[0] if cases else None)

    fu = d.get("force_unit")
    force_unit = str(fu[0]) if fu is not None and len(fu) else "?"
    lu = d.get("length_unit")
    length_unit = str(lu[0]) if lu is not None and len(lu) else "?"

    # Current NPZ schema stores frame end forces in the element LOCAL
    # coordinate system (OpenSees "localForces").  When the metadata key
    # says so, the bare ``fx_i ... mz_j`` arrays are local — expose them
    # under the ``*_i_local`` / ``*_j_local`` variant keys below.
    _cs_arr = d.get("forces_coordinate_system")
    _forces_local = False
    if _cs_arr is not None:
        try:
            _forces_local = str(np.asarray(_cs_arr).ravel()[0]) == "local"
        except Exception:
            _forces_local = False

    # Node coords lookup
    nid = d.get("node_tag", np.array([]))
    nx = d.get("node_x", np.array([]))
    ny = d.get("node_y", np.array([]))
    nz = d.get("node_z", np.array([]))
    node_coords = {}
    for i in range(len(nid)):
        node_coords[int(nid[i])] = (float(nx[i]), float(ny[i]), float(nz[i]))

    # Frame elements
    fi = d.get("frame_node_i", np.array([]))
    fj = d.get("frame_node_j", np.array([]))
    sap_ids = d.get("frame_sap_id", np.array([]))
    pre = f"static/{case}/" if case else ""

    for i in range(len(fi)):
        c_i = node_coords.get(int(fi[i]), (0, 0, 0))
        c_j = node_coords.get(int(fj[i]), (0, 0, 0))
        mid_z = (c_i[2] + c_j[2]) / 2.0

        def _g(k, i=i):
            arr = d.get(f"{pre}{k}")
            return float(arr[i]) if arr is not None else 0.0

        entry = {
            "sap_id": str(sap_ids[i]),
            "x_i": c_i[0],
            "y_i": c_i[1],
            "z_i": c_i[2],
            "x_j": c_j[0],
            "y_j": c_j[1],
            "z_j": c_j[2],
            "mid_z": mid_z,
            "fx_i": _g("fx_i"),
            "fy_i": _g("fy_i"),
            "fz_i": _g("fz_i"),
            "mx_i": _g("mx_i"),
            "my_i": _g("my_i"),
            "mz_i": _g("mz_i"),
            "fx_j": _g("fx_j"),
            "fy_j": _g("fy_j"),
            "fz_j": _g("fz_j"),
            "mx_j": _g("mx_j"),
            "my_j": _g("my_j"),
            "mz_j": _g("mz_j"),
        }
        # Local forces (optional) — set NaN when missing
        for q in ("fx", "fy", "fz", "mx", "my", "mz"):
            loc_i = f"{pre}{q}_i_local"
            loc_j = f"{pre}{q}_j_local"
            if _forces_local:
                # Bare arrays are already local — mirror them into the
                # local variant keys so ``use_local=True`` consumers read
                # verified local values without re-transformation.
                arr_i = d.get(f"{pre}{q}_i")
                arr_j = d.get(f"{pre}{q}_j")
                entry[f"{q}_i_local"] = float(arr_i[i]) if arr_i is not None else np.nan
                entry[f"{q}_j_local"] = float(arr_j[i]) if arr_j is not None else np.nan
            else:
                entry[f"{q}_i_local"] = float(d[loc_i][i]) if loc_i in d else np.nan
                entry[f"{q}_j_local"] = float(d[loc_j][i]) if loc_j in d else np.nan
        elem_data.append(entry)

    return {
        "elem_data": elem_data,
        "metadata": {},
        "force_unit": force_unit,
        "length_unit": length_unit,
        "raw_data": d,
    }
