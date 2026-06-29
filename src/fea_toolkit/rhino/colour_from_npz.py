"""Colour Rhino objects by force/moment quantities from an NPZ results file.

This is a **Rhino-only** script — it runs inside Rhino's CPython
environment and uses ``rhinoscriptsyntax`` and ``Rhino.Geometry``.

Usage in Rhino's Python editor (or ``RunPythonScript``)::

    import sys
    sys.path.append(r"/path/to/fea_toolkit/src")
    from fea_toolkit.rhino.colour_from_npz import colour_from_npz

    colour_from_npz(
        npz_path=r"C:\\path\\to\\results.npz",
        quantity="Mz",          # force/moment quantity
        use_local=True,         # use local-coordinate forces
        layer_filter="SAP2000/Frames/*",
    )

The NPZ file must have been created by
:meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.export_results_to_npz`.

Matching logic
--------------
Each Rhino object with a ``SAP_FrameID`` UserString (matching a
``sub_sap_ids`` entry) is coloured using a red‑white‑blue gradient
based on the force/moment magnitude at the element's I‑end.
"""

from typing import List, Optional, Tuple
import numpy as np

# ---------------------------------------------------------------------------
# Colour mapping helpers (pure NumPy — no Rhino dependency on import)
# ---------------------------------------------------------------------------

def _value_to_rgb(val: float, vmin: float, vmax: float) -> Tuple[int, int, int]:
    """Map *val* in [*vmin*, *vmax*] to an (R, G, B) tuple (0‑255).

    Uses a diverging red‑white‑blue scheme:
        negative → blue, zero → white, positive → red.
    """
    if abs(vmax - vmin) < 1e-15:
        return (255, 255, 255)  # white
    # Normalise to [-1, 1]
    if val >= 0:
        t = val / max(vmax, 1e-15) if vmax > 0 else 0.0
        r = int(255 * (0.3 + 0.7 * t))
        g = int(255 * (0.3 - 0.2 * t))
        b = int(255 * (0.3 - 0.3 * t))
    else:
        t = val / min(vmin, -1e-15) if vmin < 0 else 0.0
        r = int(255 * (0.3 - 0.3 * t))
        g = int(255 * (0.3 - 0.2 * t))
        b = int(255 * (0.3 + 0.7 * t))
    return (max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b)))


def _load_npz_quantities(npz_path: str, quantity: str, use_local: bool = True,
                          combo: str = None):
    """Load an NPZ and return a dict ``{sap_id: value}``.

    The value is the **I‑end** force/moment for that element (in local
    coordinates if *use_local* is ``True``).

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    quantity : str
        Force/moment quantity, e.g. ``'Mz'``, ``'Fx'``.
    use_local : bool
        Use local‑coordinate forces.
    combo : str or None
        Load‑combination key (prefix).  ``None`` = primary results.

    Returns
    -------
    dict
        ``{sap_id_str: float_value}``
    tuple
        ``(vmin, vmax)`` — min and max across all non‑NaN values
    dict
        Parsed metadata dict
    """
    data = np.load(npz_path, allow_pickle=True)

    # Parse metadata
    meta = {}
    meta_raw = data.get("metadata_json")
    if meta_raw is not None:
        try:
            import json
            meta = json.loads(str(meta_raw.item()))
        except Exception:
            pass

    prefix = f"{combo}_" if combo else ""
    suffix = "_local" if use_local else ""
    key_i = f"{prefix}sub_{quantity.lower()}_i{suffix}"

    # Fall back to global if local not available
    if use_local and key_i not in data:
        key_i = f"{prefix}sub_{quantity.lower()}_i"

    values: dict = {}
    vmin, vmax = 0.0, 0.0
    arr = data.get(key_i)
    sap_ids = data.get("sub_sap_ids")
    if arr is not None and sap_ids is not None:
        for i in range(len(arr)):
            sid = str(sap_ids[i])
            v = float(arr[i])
            if not np.isnan(v):
                values[sid] = v
                vmin = min(vmin, v)
                vmax = max(vmax, v)

    return values, (vmin, vmax), meta


# ==========================================================================
# Public API
# ==========================================================================

def colour_from_npz(
    npz_path: str,
    quantity: str = "Mz",
    use_local: bool = True,
    combo: str = None,
    layer_filter: str = "",
    skip_locked: bool = True,
    verbose: bool = True,
) -> int:
    """Colour Rhino frame objects by a force/moment quantity from an NPZ file.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    quantity : str
        Force/moment quantity, e.g. ``'Mz'``, ``'My'``, ``'Fx'``, ``'Fz'``.
    use_local : bool
        Use local‑coordinate forces (default ``True``).
    combo : str or None
        Load‑combination key (prefix).  ``None`` = primary results.
    layer_filter : str
        Optional layer name filter (glob).  Only objects on layers whose
        full path matches will be coloured.  Example: ``"SAP2000/Frames/*"``
    skip_locked : bool
        Skip objects on locked layers (default ``True``).
    verbose : bool
        Print progress messages.

    Returns
    -------
    int
        Number of objects coloured.
    """
    # Rhino imports — must happen at call time inside Rhino
    import rhinoscriptsyntax as rs
    import scriptcontext as sc
    import Rhino
    from Rhino.DocObjects import ObjectColorSource

    # Load NPZ data
    sap_values, (vmin, vmax), meta = _load_npz_quantities(
        npz_path, quantity, use_local, combo=combo
    )
    if not sap_values:
        print(f"No {quantity} data found in {npz_path}")
        return 0

    if verbose:
        label = f"[{combo}] " if combo else ""
        print(f"{label}Loaded {len(sap_values)} elements from NPZ")
        print(f"  {quantity} range: [{vmin:.4g}, {vmax:.4g}]")

    # Find all frame objects with SAP_FrameID UserString
    doc = sc.doc
    objs = doc.Objects
    coloured = 0

    for i in range(objs.Count):
        rh_obj = objs[i]
        if rh_obj is None:
            continue
        if rh_obj.IsDeleted or (skip_locked and rh_obj.IsLocked):
            continue

        # Layer filter
        if layer_filter:
            import fnmatch
            layer_path = rh_obj.Layer.FullPath
            if not fnmatch.fnmatch(layer_path, layer_filter):
                continue

        # Check for SAP_FrameID UserString
        attrs = rh_obj.Attributes
        us = attrs.GetUserString("SAP_FrameID")
        if us is None or us not in sap_values:
            continue

        val = sap_values[us]
        rgb = _value_to_rgb(val, vmin, vmax)
        colour = Rhino.Display.ColorRGBA(rgb[0], rgb[1], rgb[2], 255)

        # Apply object colour
        attrs.ObjectColor = colour
        attrs.ColorSource = ObjectColorSource.ColorFromObject
        rh_obj.CommitChanges()

        coloured += 1

    label = f"[{combo}] " if combo else ""
    if verbose:
        print(f"{label}Coloured {coloured} objects by {quantity}")

    # Force viewport redraw
    doc.Views.Redraw()
    return coloured



# ==========================================================================
# Convenience helpers
# ==========================================================================

def colour_frame_by_npz_ratio(
    npz_path: str,
    numerator: str = "Mz",
    denominator: str = "My",
    use_local: bool = True,
    **kwargs,
) -> int:
    """Colour by the ratio of two force/moment quantities.

    Example: colour by ``Mz / My`` to highlight members where one
    bending direction dominates.

    Parameters
    ----------
    numerator, denominator : str
        Quantity names (e.g. ``'Mz'``, ``'My'``).
    **kwargs
        Passed through to :func:`colour_from_npz`.
    """
    import numpy as np

    data = np.load(npz_path, allow_pickle=True)
    suffix = "_local" if use_local else ""

    def _get(key):
        k = f"sub_{key.lower()}_i{suffix}"
        if use_local and k not in data:
            k = f"sub_{key.lower()}_i"
        return data.get(k)

    num_arr = _get(numerator)
    den_arr = _get(denominator)
    sap_ids = data.get("sub_sap_ids")
    if num_arr is None or den_arr is None or sap_ids is None:
        print("Required arrays not found in NPZ")
        return 0

    ratios: dict = {}
    vmin, vmax = 0.0, 0.0
    for i in range(len(sap_ids)):
        d = float(den_arr[i])
        if abs(d) > 1e-15:
            r = float(num_arr[i]) / d
            if not np.isnan(r):
                sid = str(sap_ids[i])
                ratios[sid] = r
                vmin = min(vmin, r)
                vmax = max(vmax, r)

    # Re-use colouring logic with a temporary value map
    import rhinoscriptsyntax as rs
    import scriptcontext as sc
    import Rhino
    from Rhino.DocObjects import ObjectColorSource

    coloured = 0
    doc = sc.doc
    for i in range(doc.Objects.Count):
        rh_obj = doc.Objects[i]
        if rh_obj is None or rh_obj.IsDeleted:
            continue
        us = rh_obj.Attributes.GetUserString("SAP_FrameID")
        if us is None or us not in ratios:
            continue
        val = ratios[us]
        rgb = _value_to_rgb(val, vmin, vmax)
        colour = Rhino.Display.ColorRGBA(rgb[0], rgb[1], rgb[2], 255)
        attrs = rh_obj.Attributes
        attrs.ObjectColor = colour
        attrs.ColorSource = ObjectColorSource.ColorFromObject
        rh_obj.CommitChanges()
        coloured += 1

    if kwargs.get("verbose", True):
        print(f"Coloured {coloured} objects by ratio {numerator}/{denominator}")

    doc.Views.Redraw()
    return coloured


# ==========================================================================
# Create Rhino flag geometry from NPZ results
# ==========================================================================

_FLAGS_LAYER = "SAP2000/Results/Flags"


def create_result_flags(
    npz_path: str,
    quantity: str = "Mz",
    use_local: bool = True,
    combo: str = None,
    scale_factor: float = None,
    layer_name: str = None,
    verbose: bool = True,
) -> int:
    """Create 3D flag geometry in Rhino from an NPZ results file.

    For each frame element a planar quadrilateral (flag) is created on a
    dedicated layer, offset perpendicular to the member axis proportional
    to the chosen force/moment quantity.  Red = +ve, blue = −ve.

    Re‑running with the same *quantity* replaces the old flags (deletes
    the previous objects on that layer).  Different quantities sit on
    separate sub‑layers so they can be toggled independently.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    quantity : str
        Force/moment quantity, e.g. ``'Mz'``, ``'My'``, ``'Fx'``, ``'Fz'``.
    use_local : bool
        Use local‑coordinate forces (default ``True``).
    scale_factor : float or None
        Flag height per unit force/moment.  If ``None``, auto‑scaled so
        the largest flag is 20 % of the model height.
    layer_name : str or None
        Name of the Rhino layer for the flags.  Default is
        ``"SAP2000/Results/Flags/{quantity}"``.
    verbose : bool
        Print progress messages.

    Returns
    -------
    int
        Number of flags created.
    """
    # ── Rhino imports ──────────────────────────────────────────────
    import scriptcontext as sc
    import Rhino
    import Rhino.Geometry as rg
    import Rhino.DocObjects as rd
    # ── Load NPZ data ──────────────────────────────────────────────
    data = np.load(npz_path, allow_pickle=True)
    prefix = f"{combo}_" if combo else ""
    suffix = "_local" if use_local else ""
    key_i = f"{prefix}sub_{quantity.lower()}_i{suffix}"
    key_j = f"{prefix}sub_{quantity.lower()}_j{suffix}"

    # Fall back to global if local not available
    if use_local and key_i not in data:
        key_i = f"{prefix}sub_{quantity.lower()}_i"
        key_j = f"{prefix}sub_{quantity.lower()}_j"

    sub_sap_ids = data.get("sub_sap_ids")
    sub_n_i = data.get("sub_node_i_tag")
    sub_n_j = data.get("sub_node_j_tag")
    n_tags = data.get("node_tags")
    n_x = data.get("node_x")
    n_y = data.get("node_y")
    n_z = data.get("node_z")
    val_i_arr = data.get(key_i)
    val_j_arr = data.get(key_j)

    if any(a is None for a in (sub_sap_ids, sub_n_i, sub_n_j,
                                n_tags, n_x, n_y, n_z,
                                val_i_arr, val_j_arr)):
        print("Required NPZ arrays not found")
        return 0

    # ── Build node coordinate lookup ───────────────────────────────
    node_coords: Dict[int, rg.Point3d] = {}
    z_vals = []
    for k in range(len(n_tags)):
        pt = rg.Point3d(float(n_x[k]), float(n_y[k]), float(n_z[k]))
        node_coords[int(n_tags[k])] = pt
        z_vals.append(float(n_z[k]))

    model_height = max(z_vals) - min(z_vals) if z_vals else 1.0

    # ── Compute max absolute value for auto‑scale ──────────────────
    max_abs = 0.0
    for i in range(len(sub_sap_ids)):
        v_i = float(val_i_arr[i]) if not np.isnan(float(val_i_arr[i])) else 0.0
        v_j = float(val_j_arr[i]) if not np.isnan(float(val_j_arr[i])) else 0.0
        max_abs = max(max_abs, abs(v_i), abs(v_j))

    if max_abs < 1e-15:
        print("All values are zero — nothing to create.")
        return 0

    if scale_factor is None:
        scale_factor = (model_height * 0.2) / max_abs

    # ── Determine layer ────────────────────────────────────────────
    doc = sc.doc
    if layer_name is None:
        layer_name = f"{_FLAGS_LAYER}/{quantity}"
    # Create layer path if needed (matching layers.py pattern)
    layer_table = doc.Layers
    parts = layer_name.split("/")
    for j in range(1, len(parts) + 1):
        sub = "/".join(parts[:j])
        if layer_table.Find(sub, True) < 0:
            new_layer = rd.Layer()
            new_layer.Name = sub
            new_layer.Color = Rhino.Display.ColorRGBA(200, 200, 200, 255)
            if j > 1:
                parent_idx = layer_table.Find("/".join(parts[:j-1]), True)
                if parent_idx >= 0:
                    new_layer.ParentLayerId = layer_table[parent_idx].Id
            layer_table.Add(new_layer)

    # ── Delete old flags on this layer ──────────────────────────────
    del_idx = layer_table.Find(layer_name, True)
    if del_idx >= 0:
        objs_to_del = []
        for i in range(doc.Objects.Count):
            try:
                rh_obj = doc.Objects[i]
            except Exception:
                continue
            if rh_obj is None:
                continue
            if rh_obj.Attributes.LayerIndex == del_idx:
                objs_to_del.append(rh_obj.Id)
        if objs_to_del:
            doc.Objects.Delete(objs_to_del, True)

    # ── Create flags ───────────────────────────────────────────────
    created = 0

    for i in range(len(sub_sap_ids)):
        v_i = float(val_i_arr[i]) if not np.isnan(float(val_i_arr[i])) else 0.0
        v_j = float(val_j_arr[i]) if not np.isnan(float(val_j_arr[i])) else 0.0
        if abs(v_i) < 1e-12 and abs(v_j) < 1e-12:
            continue

        # Get element end points
        n_i_tag = int(sub_n_i[i])
        n_j_tag = int(sub_n_j[i])
        p_i = node_coords.get(n_i_tag)
        p_j = node_coords.get(n_j_tag)
        if p_i is None or p_j is None:
            continue

        # Compute element axis
        axis = np.array([p_j.X - p_i.X, p_j.Y - p_i.Y, p_j.Z - p_i.Z])
        axis_len = np.linalg.norm(axis)
        if axis_len < 1e-12:
            continue
        axis_u = axis / axis_len

        # Compute local axes (SAP2000 convention, angle=0 default)
        try:
            from ..model.geometry import get_SAP_vecxz
            vecxz = get_SAP_vecxz(axis_u, 0.0)
        except (ImportError, ModuleNotFoundError):
            _gz = np.array([0.0, 0.0, 1.0])
            if abs(np.dot(axis_u, _gz)) > 0.9999:
                _gy = np.array([0.0, 1.0, 0.0])
                vecxz = _gy if axis_u[2] > 0 else -_gy
            else:
                vecxz = np.cross(axis_u, _gz)
                vecxz = vecxz / np.linalg.norm(vecxz)
        vec_z = vecxz / np.linalg.norm(vecxz)
        vec_y = np.cross(vec_z, axis_u)
        vec_y = vec_y / np.linalg.norm(vec_y)

        # Flag offset direction (vn) based on quantity
        # Positive Fi → offset in +vn at I-end
        # Positive Fj → offset in -vn at J-end (baked-in negation via pt2 − s·Fj·vn)
        if quantity == "Fx":
            vn = np.array([vec_z[0], vec_z[1], vec_z[2]])
        elif quantity == "Fy":
            vn = np.array([vec_y[0], vec_y[1], vec_y[2]])
        elif quantity == "Fz":
            vn = np.array([vec_z[0], vec_z[1], vec_z[2]])
        elif quantity == "Mx":
            vn = np.array([vec_y[0], vec_y[1], vec_y[2]])
        elif quantity == "My":
            vn = -np.array([vec_z[0], vec_z[1], vec_z[2]])
        elif quantity == "Mz":
            vn = np.array([vec_y[0], vec_y[1], vec_y[2]])
        else:
            vn = np.array([vec_z[0], vec_z[1], vec_z[2]])

        # Use original (un-negated) values
        Fi = float(val_i_arr[i]) if not np.isnan(float(val_i_arr[i])) else 0.0
        Fj = float(val_j_arr[i]) if not np.isnan(float(val_j_arr[i])) else 0.0
        if abs(Fi) < 1e-12 and abs(Fj) < 1e-12:
            continue

        from ..utils import compute_flag_parts

        # Colour helper
        def _c(val):
            t = min(abs(val) / max_abs, 1.0) if max_abs > 0 else 0.0
            if val >= 0:
                return (int(255 * (0.3 + 0.7 * t)),
                        int(255 * (0.3 - 0.2 * t)),
                        int(255 * (0.3 - 0.3 * t)))
            else:
                return (int(255 * (0.3 - 0.3 * t)),
                        int(255 * (0.3 - 0.2 * t)),
                        int(255 * (0.3 + 0.7 * t)))

        def _add_flag_mesh(verts, col_val, fid, vi_t, vj_t):
            """Add a coloured Mesh flag with attributes and UserText."""
            mesh = rg.Mesh()
            for v in verts:
                mesh.Vertices.Add(float(v[0]), float(v[1]), float(v[2]))
            if len(verts) == 4:
                mesh.Faces.AddFace(0, 1, 2, 3)
            else:
                mesh.Faces.AddFace(0, 1, 2)
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            r, g, b = _c(col_val)
            for _ in range(len(verts)):
                mesh.VertexColors.Add(r, g, b)
            a = rd.ObjectAttributes()
            lidx = layer_table.Find(layer_name, True)
            if lidx >= 0:
                a.LayerIndex = lidx
            a.ObjectColor = Rhino.Display.ColorRGBA(r, g, b, 255)
            a.ColorSource = rd.ObjectColorSource.ColorFromObject
            a.SetUserString("SAP_FrameID", str(fid))
            a.SetUserString(f"{quantity}_i", f"{vi_t:.4g}")
            a.SetUserString(f"{quantity}_j", f"{vj_t:.4g}")
            doc.Objects.AddMesh(mesh, a)

        # ── Build flag geometry via shared utility ─────────────────
        try:
            for verts, col_val in compute_flag_parts(
                (p_i.X, p_i.Y, p_i.Z), (p_j.X, p_j.Y, p_j.Z),
                vn, Fi, Fj, scale_factor,
            ):
                _add_flag_mesh(verts, col_val, int(sub_sap_ids[i]), Fi, Fj)
                created += 1
        except Exception:
            continue

    doc.Views.Redraw()
    # Reset CPlane to World XY
    try:
        import rhinoscriptsyntax as rs
        rs.Command("_-CPlane _World _XY", 0)
    except Exception:
        pass

    if verbose:
        print(f"Created {created} flag(s) on layer '{layer_name}' "
              f"for {quantity}")

    return created


# ==========================================================================
# Convenience: import all six force/moment types at once
# ==========================================================================

_ALL_QUANTITIES = [
    ("Mz", "major-axis moment"),
    ("My", "minor-axis moment"),
    ("Mx", "torsion"),
    ("Fx", "axial force (F1)"),
    ("Fz", "major shear (V3)"),
    ("Fy", "minor shear (V2)"),
]


def create_all_result_flags(
    npz_path: str,
    use_local: bool = True,
    combo: str = None,
    scale_factor: float = None,
    verbose: bool = True,
) -> int:
    """Create flag diagrams for all six force/moment quantities at once.

    Each quantity goes on its own sub‑layer under
    ``SAP2000/Results/Flags/{quantity}``.  Re‑running replaces only
    the flags on those specific layers — other geometry is untouched.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    use_local : bool
        Use local‑coordinate forces (default ``True``).
    combo : str or None
        Load‑combination key (prefix).  ``None`` = primary results.
    scale_factor : float or None
        Flag height per unit force/moment.  ``None`` = auto‑scale.
    verbose : bool
        Print progress messages.

    Returns
    -------
    int
        Total number of flags created.
    """
    total = 0
    for qty, label in _ALL_QUANTITIES:
        n = create_result_flags(
            npz_path, quantity=qty, use_local=use_local,
            combo=combo, scale_factor=scale_factor, verbose=verbose,
        )
        total += n
    if verbose:
        c = f" [{combo}]" if combo else ""
        print(f"Total{c}: {total} flags across 6 layers")
    return total


# ==========================================================================
# Visualise unconnected shell edges (debug aid)
# ==========================================================================

_DEBUG_LAYER = "SAP2000/Debug/UnconnectedEdges"


def mark_unconnected_edges(
    reports: list,
    layer_name: str = _DEBUG_LAYER,
    mark_slave_nodes: bool = True,
    verbose: bool = True,
) -> int:
    """Draw thick red lines for coarse edges with unconnected slave nodes.

    Use after retrieving detection results from
    :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.detect_unconnected_edges`
    to visualise where slab meshes are discontinuous.

    Parameters
    ----------
    reports : list of dict
        Detection output (each entry has ``master_coords_i``,
        ``master_coords_j``, ``slave_node``, ``coords``, etc.).
    layer_name : str
        Rhino layer for the edge lines (created if needed).
    mark_slave_nodes : bool
        Also draw small red dots at each slave node location.
    verbose : bool
        Print progress message.

    Returns
    -------
    int
        Number of edge lines created.
    """
    import scriptcontext as sc
    import Rhino
    import Rhino.Geometry as rg
    import Rhino.DocObjects as rd

    doc = sc.doc

    # ── Ensure debug layer ──────────────────────────────────────────
    layer_table = doc.Layers
    parts = layer_name.split("/")
    for j in range(1, len(parts) + 1):
        sub = "/".join(parts[:j])
        idx = layer_table.Find(sub, True)
        if idx < 0:
            new_layer = rd.Layer()
            new_layer.Name = sub
            new_layer.Color = Rhino.Display.ColorRGBA(200, 50, 50, 255)
            if j > 1:
                parent = layer_table.Find("/".join(parts[:j-1]), True)
                if parent >= 0:
                    new_layer.ParentLayerId = layer_table[parent].Id
            layer_table.Add(new_layer)

    layer_idx = layer_table.Find(layer_name, True)
    if layer_idx < 0:
        layer_idx = -1

    # ── Deduplicate edges ───────────────────────────────────────────
    seen: set = set()
    unique_edges = []
    for r in reports:
        key = (r["master_node_i"], r["master_node_j"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(r)

    # ── Draw lines ──────────────────────────────────────────────────
    attr = rd.ObjectAttributes()
    if layer_idx >= 0:
        attr.LayerIndex = layer_idx
    attr.ObjectColor = Rhino.Display.ColorRGBA(255, 30, 30, 255)
    attr.ColorSource = rd.ObjectColorSource.ColorFromObject

    line_count = 0
    for r in unique_edges:
        p1 = rg.Point3d(*r["master_coords_i"])
        p2 = rg.Point3d(*r["master_coords_j"])
        line = rg.Line(p1, p2)
        # Thick line via a narrow extrusion / tube is complex; use
        # a simple Line object (thickness controlled by Rhino display).
        guid = doc.Objects.AddLine(line, attr)
        if guid is not None:
            line_count += 1

    # ── Mark slave nodes ────────────────────────────────────────────
    if mark_slave_nodes:
        dot_attr = rd.ObjectAttributes()
        if layer_idx >= 0:
            dot_attr.LayerIndex = layer_idx
        dot_attr.ObjectColor = Rhino.Display.ColorRGBA(255, 30, 30, 255)
        dot_attr.ColorSource = rd.ObjectColorSource.ColorFromObject

        for r in reports:
            pt = rg.Point3d(*r["coords"])
            doc.Objects.AddPoint(pt, dot_attr)

    doc.Views.Redraw()

    if verbose:
        slave_count = len(reports)
        print(f"Marked {line_count} edge(s) and {slave_count} slave node(s) "
              f"on layer '{layer_name}'")

    return line_count
