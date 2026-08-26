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
:meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.export_results`
or :func:`~fea_toolkit.io.npz_writer.write_results_npz`.

Matching logic
--------------
Each Rhino object with a ``SAP_FrameID`` UserString (matching a
``sub_sap_ids`` entry) is coloured using a red‑white‑blue gradient
based on the force/moment magnitude at the element's I‑end.
"""

import numpy as np

# ---------------------------------------------------------------------------
# Colour mapping helpers (pure NumPy — no Rhino dependency on import)
# ---------------------------------------------------------------------------


def _value_to_rgb(val: float, vmin: float, vmax: float) -> tuple[int, int, int]:
    """Map *val* in [*vmin*, *vmax*] to an (R, G, B) tuple (0‑255).

    Uses a diverging red‑white‑blue scheme:
        negative → blue, zero → white, positive → red.

    The white midpoint is the whole point of a diverging scale: values
    near zero render as light tints, not mid‑grey, so a low‑magnitude
    result stays visually distinct from an uncoloured (layer‑coloured)
    object.
    """
    if abs(vmax - vmin) < 1e-15:
        return (255, 255, 255)  # white
    # Normalise to [-1, 1] (negative half-scaled to |vmin|, positive to vmax)
    if val >= 0:
        t = val / max(vmax, 1e-15) if vmax > 0 else 0.0
    else:
        t = val / abs(vmin) if vmin < 0 else 0.0
    t = max(-1.0, min(1.0, t))
    if t < 0:
        # blue (-1) -> white (0)
        f = -t
        r = int(255 * (1.0 - f))
        g = int(255 - 230 * f)
        b = 255
    else:
        # white (0) -> red (+1)
        f = t
        r = 255
        g = int(255 - 230 * f)
        b = int(255 - 255 * f)
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def _load_pushover_flag_values(
    data: dict,
    direction: str,
    quantity: str,
    step: int = None,
):
    """Rebase per-step pushover frame forces onto the mesh geometry order.

    The pushover force arrays are ordered by the pushover writer's frame
    list, which is *not* the same order as the mesh-stage geometry arrays
    (``frame_sap_id`` / ``frame_node_i`` / ``frame_node_j``) that carry the
    node tags used for flag geometry.  This helper pairs values by SAP ID
    so flag drawing can index positionally.

    Args:
        data: Flat stage-results dict (already ``flatten_stage``-promoted).
        direction: Pushover direction (``+X``, ``-X``, ``+Y``, ``-Y``).
        quantity: Frame force/moment quantity, e.g. ``Mz``.
        step: Step index (``None`` → last step, i.e. peak).

    Returns:
        ``(sap_ids, val_i, val_j)`` aligned with the geometry ``frame_sap_id``
        ordering (``NaN`` where a frame has no pushover result).  Empty
        tuples when the pushover arrays are unavailable.
    """
    from ..io.results_schema import make_pushover_key

    q = quantity.lower()
    pids = data.get(make_pushover_key(direction, "pushover/{direction}/frame_sap_id"))
    arr_i = data.get(make_pushover_key(direction, f"pushover/{{direction}}/frame_{q}_i"))
    arr_j = data.get(make_pushover_key(direction, f"pushover/{{direction}}/frame_{q}_j"))
    if pids is None or arr_i is None:
        return [], [], []

    n_steps = arr_i.shape[0] if arr_i.ndim > 1 else 1
    step_idx = max(0, min(step if step is not None else n_steps - 1, n_steps - 1))
    row_i = arr_i[step_idx] if arr_i.ndim > 1 else arr_i
    row_j = (
        arr_j[step_idx]
        if arr_j is not None and arr_j.ndim > 1
        else (arr_j if arr_j is not None else row_i)
    )

    val_i_map = {str(pids[k]): float(row_i[k]) for k in range(len(pids))}
    val_j_map = {str(pids[k]): float(row_j[k]) for k in range(len(pids))}

    geom = data.get("frame_sap_id")
    if geom is None:
        return [], [], []
    val_i = [val_i_map.get(str(s), float("nan")) for s in geom]
    val_j = [val_j_map.get(str(s), float("nan")) for s in geom]
    return list(geom), val_i, val_j


def _load_unified(source, stage: str = None) -> dict:
    """Load a unified results file (path or dict) into a flat array dict.

    Stage files written by
    :func:`~fea_toolkit.io.stage_writer.write_model_stages` namespace
    their geometry under ``stage/<stage>/...``; those arrays are promoted
    to the top level via
    :func:`~fea_toolkit.io.stage_reader.flatten_stage` so the result-
    colouring code sees unprefixed keys (``frame_sap_id``, ...).  Legacy
    NPZ files (no ``stage/`` keys) pass through unchanged.

    Args:
        source: Results-file path (``.npz`` / ``.h5``) or a flat dict
            already loaded with
            :func:`~fea_toolkit.io.npz_reader.read_results`.
        stage: Stage to promote for stage files (``None`` → auto,
            preferring ``mesh``).

    Returns:
        Flat ``{key: array}`` dict.
    """
    from ..io.npz_reader import read_results
    from ..io.stage_reader import flatten_stage

    d = read_results(source) if isinstance(source, str) else source
    if any(k.startswith("stage/") and k.count("/") >= 2 for k in d):
        return flatten_stage(d, stage=stage)
    return d


def _load_npz_quantities(
    source,
    quantity: str,
    use_local: bool = True,
    case: str = None,
    stage: str = None,
    aggregate_parents: bool = False,
):
    """Load a unified results file and return a dict ``{sap_id: value}``.

    Args:
        source: Results-file path (``.npz`` / ``.h5``) or a flat dict.
        quantity: Force/moment quantity, e.g. ``'Mz'``, ``'Fx'``.
        use_local: Use local-coordinate forces (default ``True``).
        case: Static-case key.  ``None`` = first available case.
        stage: Stage to promote for stage files (``None`` → auto).
        aggregate_parents: When ``True``, map child-element values back
            to their parent frame IDs (max-abs envelope over children)
            so SAP-stage geometry can be coloured from meshed-stage
            results.

    Returns:
        ``(values, (vmin, vmax), {})``.
    """
    from ..io.npz_reader import _get_static_cases, npz_build_parent_map

    d = _load_unified(source, stage=stage)
    cases = _get_static_cases(d)
    if case is not None and case not in cases:
        raise ValueError(f"Case '{case}' not found in results. Available: {cases}")
    case_name = case if case and case in cases else (cases[0] if cases else None)
    if case_name is None:
        return {}, (0.0, 0.0), {}
    pre = f"static/{case_name}/"

    q = quantity.lower()
    key_i = f"{pre}{q}_i"
    if use_local:
        loc_key = f"{pre}{q}_i_local"
        if loc_key in d:
            key_i = loc_key
    key_j = q.replace("_i", "_j") if "_i" in q else f"{q}_j"
    key_j = f"{pre}{key_j}"

    arr_i = d.get(key_i)
    arr_j = d.get(key_j)
    if arr_i is None or arr_j is None:
        raise ValueError(f"Quantity arrays '{key_i}' not found in results")

    values: dict = {}
    vmin, vmax = 0.0, 0.0
    sap_ids = d.get("frame_sap_id")
    if sap_ids is not None:
        for i in range(len(arr_i)):
            sid = str(sap_ids[i])
            v = float(arr_i[i])
            if not np.isnan(v):
                values[sid] = v
                vmin = min(vmin, v)
                vmax = max(vmax, v)

    if aggregate_parents:
        parent_map = npz_build_parent_map(d)
        for child, parent in parent_map.items():
            v = values.get(str(child))
            if v is None:
                continue
            cur = values.get(str(parent))
            if cur is None or abs(v) > abs(cur):
                values[str(parent)] = v
        vals = list(values.values())
        if vals:
            vmin = min(vals)
            vmax = max(vals)

    return values, (vmin, vmax), {}


def _colour_doc_objects(
    values: dict,
    id_key: str,
    vmin: float,
    vmax: float,
    layer_filter: str = "",
    skip_locked: bool = True,
) -> int:
    """Colour Rhino objects whose ``id_key`` UserString is in *values*.

    Shared by the frame and shell colouring entry points.  Only runs
    inside the Rhino process (imports ``scriptcontext`` at call time).

    Args:
        values: ``{sap_id: value}`` map.
        id_key: UserString attribute name to match (``SAP_FrameID`` /
            ``SAP_AreaID``).
        vmin, vmax: Value range for the diverging colour scale.
        layer_filter: Optional glob filter on the layer full path.
        skip_locked: Skip objects on locked layers (default ``True``).

    Returns:
        Number of objects coloured.
    """
    import fnmatch

    import Rhino
    import scriptcontext as sc
    from Rhino.DocObjects import ObjectColorSource

    doc = sc.doc
    coloured = 0

    # Iterate the ObjectTable directly — pythonnet does not expose the
    # ``ObjectTable`` indexer as ``__getitem__`` in Rhino 8's CPython, so
    # ``range(objs.Count)`` + ``objs[i]`` hits the abstract
    # ``_collections_abc.Sequence.__getitem__`` and always raises IndexError.
    for rh_obj in doc.Objects:
        if rh_obj is None or rh_obj.IsDeleted or (skip_locked and rh_obj.IsLocked):
            continue

        if layer_filter:
            # ``rh_obj.Layer`` is not available on every ``RhinoObject``
            # subclass in Rhino 8's CPython — ``ExtrusionObject`` raises
            # AttributeError — so resolve the layer path from the object's
            # ``Attributes.LayerIndex`` instead (``doc.Layers[i]`` works,
            # unlike ``doc.Objects[i]``).  ``Layer.FullPath`` uses ``::``
            # as its separator in real Rhino, while the filter API uses
            # ``/`` — normalise both sides before matching.
            layer_idx = rh_obj.Attributes.LayerIndex
            if layer_idx < 0:
                continue
            layer_path = doc.Layers[layer_idx].FullPath.replace("::", "/")
            if not fnmatch.fnmatch(layer_path, layer_filter.replace("::", "/")):
                continue

        attrs = rh_obj.Attributes
        us = attrs.GetUserString(id_key)
        if us is None or us not in values:
            continue

        val = values[us]
        rgb = _value_to_rgb(val, vmin, vmax)
        colour = Rhino.Display.ColorRGBA(rgb[0], rgb[1], rgb[2], 255)

        attrs.ObjectColor = colour
        attrs.ColorSource = ObjectColorSource.ColorFromObject
        rh_obj.CommitChanges()
        coloured += 1

    doc.Views.Redraw()
    return coloured


def colour_from_npz(
    npz_path: str,
    quantity: str = "Mz",
    use_local: bool = True,
    combo: str = None,
    layer_filter: str = "",
    skip_locked: bool = True,
    verbose: bool = True,
    stage: str = None,
    aggregate_parents: bool = False,
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
    stage : str or None
        Stage to promote for stage files (``None`` → auto, ``mesh``).
    aggregate_parents : bool
        Map child-element values back to their parent frame IDs
        (max-abs envelope) so SAP-stage geometry can be coloured from
        meshed-stage results.

    Returns
    -------
    int
        Number of objects coloured.
    """
    # Load results
    sap_values, (vmin, vmax), _meta = _load_npz_quantities(
        npz_path,
        quantity,
        use_local,
        case=combo,
        stage=stage,
        aggregate_parents=aggregate_parents,
    )
    if not sap_values:
        print(f"No {quantity} data found in {npz_path}")
        return 0

    if verbose:
        label = f"[{combo}] " if combo else ""
        print(f"{label}Loaded {len(sap_values)} elements from results")
        print(f"  {quantity} range: [{vmin:.4g}, {vmax:.4g}]")

    coloured = _colour_doc_objects(
        sap_values,
        "SAP_FrameID",
        vmin,
        vmax,
        layer_filter=layer_filter,
        skip_locked=skip_locked,
    )

    label = f"[{combo}] " if combo else ""
    if verbose:
        print(f"{label}Coloured {coloured} objects by {quantity}")

    return coloured


# ==========================================================================
# Convenience helpers
# ==========================================================================


def colour_frame_by_npz_ratio(
    npz_path: str,
    numerator: str = "Mz",
    denominator: str = "My",
    use_local: bool = True,
    stage: str = None,
    **kwargs,
) -> int:
    """Colour by the ratio of two force/moment quantities.

    Example: colour by ``Mz / My`` to highlight members where one
    bending direction dominates.

    Parameters
    ----------
    numerator, denominator : str
        Quantity names (e.g. ``'Mz'``, ``'My'``).
    use_local : bool
        Use local-coordinate forces (default ``True``).
    stage : str or None
        Stage to promote for stage files (``None`` → auto).
    **kwargs
        Passed through to :func:`colour_from_npz` (e.g. ``layer_filter``).
    """
    data = _load_unified(npz_path, stage=stage)
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
        print("Required arrays not found in results")
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

    coloured = _colour_doc_objects(
        ratios,
        "SAP_FrameID",
        vmin,
        vmax,
        layer_filter=kwargs.get("layer_filter", ""),
        skip_locked=kwargs.get("skip_locked", True),
    )

    if kwargs.get("verbose", True):
        print(f"Coloured {coloured} objects by ratio {numerator}/{denominator}")

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
    stage: str = None,
    pushover_direction: str = None,
    step: int = None,
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
    stage : str or None
        Stage to promote for stage files (``None`` → auto).
    pushover_direction : str or None
        When set (e.g. ``\"+X\"``), source the flags from the per‑step
        pushover frame forces for that direction instead of a static
        case — useful for stage files whose only per‑element forces live
        in the pushover arrays.
    step : int or None
        Pushover step index for *pushover_direction* (``None`` → last
        step, i.e. peak).

    Returns
    -------
    int
        Number of flags created.
    """
    # ── Rhino imports ──────────────────────────────────────────────
    import Rhino
    import Rhino.DocObjects as rd
    import Rhino.Geometry as rg
    import scriptcontext as sc

    # ── Load results data (auto-detect .npz / .h5 / stage file) ────
    is_h5 = str(npz_path).lower().endswith((".h5", ".hdf5"))
    if is_h5:
        data = _load_unified(npz_path, stage=stage)
        is_unified = True
    else:
        raw = np.load(npz_path, allow_pickle=True)
        is_unified = "analysis_types" in raw or "frame_eid" in raw

    if is_unified:
        from ..io.npz_reader import _get_static_cases

        data = _load_unified(npz_path, stage=stage)
        cases = _get_static_cases(data)
        case = combo if combo and combo in cases else (cases[0] if cases else None)
        pre = f"static/{case}/" if case else ""
        q = quantity.lower()

        sub_sap_ids = data.get("frame_sap_id")
        sub_n_i = data.get("frame_node_i")
        sub_n_j = data.get("frame_node_j")
        n_tags = data.get("node_tag")
        n_x = data.get("node_x")
        n_y = data.get("node_y")
        n_z = data.get("node_z")

        key_i = f"{pre}{q}_i"
        key_j = f"{pre}{q}_j"
        if use_local:
            loc_i = f"{pre}{q}_i_local"
            if loc_i in data:
                key_i = loc_i
                key_j = f"{pre}{q}_j_local"
        val_i_arr = data.get(key_i)
        val_j_arr = data.get(key_j)
    else:
        # Legacy format
        data = raw
        prefix = f"{combo}_" if combo else ""
        suffix = "_local" if use_local else ""
        key_i = f"{prefix}sub_{quantity.lower()}_i{suffix}"
        key_j = f"{prefix}sub_{quantity.lower()}_j{suffix}"
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

    # ── Pushover per-step frame forces (optional override) ─────────
    if pushover_direction:
        sub_sap_ids, val_i_arr, val_j_arr = _load_pushover_flag_values(
            data, pushover_direction, quantity, step
        )
        if not sub_sap_ids:
            print(f"No pushover '{pushover_direction}' frame {quantity} forces in {npz_path}")
            return 0
        sub_n_i = data.get("frame_node_i")
        sub_n_j = data.get("frame_node_j")

    # ── Build node coordinate lookup ───────────────────────────────
    node_coords: dict[int, rg.Point3d] = {}
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
    # Reuse the layers.py helpers — the inline path here used
    # ``layer_table.Find(name, True)`` with a ``/``-separated full path,
    # which Rhino's Find does not match (FullPath uses ``::``), so it
    # returned -1 and every flag fell through to the default layer.
    from .layers import _find_layer, create_or_get_layer

    layer_table = doc.Layers
    layer_index = create_or_get_layer(layer_name)
    # Defensive: trust the on-disk layer table over the creation return
    # value (pythonnet can mask Add()/Find() quirks on some builds).
    _verified = _find_layer(layer_table, layer_name)
    if _verified >= 0:
        layer_index = _verified

    # ── Delete old flags on this layer ──────────────────────────────
    del_idx = _find_layer(layer_table, layer_name)
    if del_idx >= 0:
        objs_to_del = []
        # Iterate directly — see ``_colour_doc_objects``: pythonnet does
        # not expose the ObjectTable indexer in Rhino 8's CPython.
        for rh_obj in doc.Objects:
            if rh_obj is None or rh_obj.IsDeleted:
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
            from ..model.geometry import get_local_axes

            _, vec_y, vec_z = get_local_axes(axis_u, 0.0)
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

        # Colour helper — diverging blue→white→red across ±max_abs (same
        # ramp as the frame/shell colouring, so zero reads white, not grey).
        def _c(val):
            return _value_to_rgb(float(val), -max_abs, max_abs)

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
            a.LayerIndex = layer_index
            a.ObjectColor = Rhino.Display.ColorRGBA(r, g, b, 255)
            a.ColorSource = rd.ObjectColorSource.ColorFromObject
            a.SetUserString("SAP_FrameID", str(fid))
            a.SetUserString(f"{quantity}_i", f"{vi_t:.4g}")
            a.SetUserString(f"{quantity}_j", f"{vj_t:.4g}")
            doc.Objects.AddMesh(mesh, a)

        # ── Build flag geometry via shared utility ─────────────────
        try:
            for verts, col_val in compute_flag_parts(
                (p_i.X, p_i.Y, p_i.Z),
                (p_j.X, p_j.Y, p_j.Z),
                vn,
                Fi,
                Fj,
                scale_factor,
            ):
                _add_flag_mesh(verts, col_val, str(sub_sap_ids[i]), Fi, Fj)
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
        print(f"Created {created} flag(s) on layer '{layer_name}' for {quantity}")

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
    stage: str = None,
) -> int:
    """Create flag diagrams for all six force/moment quantities at once.

    Each quantity goes on its own sub‑layer under
    ``SAP2000/Results/Flags/{quantity}``.  Re‑running replaces only
    the flags on those specific layers — other geometry is untouched.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` / ``.h5`` results file.
    use_local : bool
        Use local‑coordinate forces (default ``True``).
    combo : str or None
        Load‑combination key (prefix).  ``None`` = primary results.
    scale_factor : float or None
        Flag height per unit force/moment.  ``None`` = auto‑scale.
    verbose : bool
        Print progress messages.
    stage : str or None
        Stage to promote for stage files (``None`` → auto).

    Returns
    -------
    int
        Total number of flags created.
    """
    total = 0
    for qty, label in _ALL_QUANTITIES:
        n = create_result_flags(
            npz_path,
            quantity=qty,
            use_local=use_local,
            combo=combo,
            scale_factor=scale_factor,
            verbose=verbose,
            stage=stage,
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
    :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.detect_unconnected_edges`
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
    import Rhino
    import Rhino.DocObjects as rd
    import Rhino.Geometry as rg
    import scriptcontext as sc

    doc = sc.doc

    # ── Ensure debug layer ──────────────────────────────────────────
    from .layers import create_or_get_layer

    layer_idx = create_or_get_layer(layer_name)

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
        print(
            f"Marked {line_count} edge(s) and {slave_count} slave node(s) on layer '{layer_name}'"
        )

    return line_count
