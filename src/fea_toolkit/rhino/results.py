"""Unified results application for Rhino — colouring and deformed shapes.

A single entry point, :func:`apply_results`, reads a results file
(``.npz`` or ``.h5`` — plain results **or** a stage file) and applies
any combination of:

* **frame colouring** — static force/moment quantities via
  :func:`fea_toolkit.rhino.colour_from_npz.colour_from_npz`;
* **shell colouring** — pushover in-plane membrane forces
  (:func:`colour_shells_from_results`);
* **deformed-shape overlay** — displaced frame lines and shell quads on
  a dedicated ``Results/Deformed`` layer (:func:`create_deformed_geometry`),
  from static, modal, response-spectrum, or pushover node displacements.

All public functions are **Rhino-only**: they lazily import
``scriptcontext`` at call time.  The data-extraction helpers
(``_load_pushover_shell_quantities``, ``_load_deformed_arrays``) are
NumPy-only and unit-testable outside Rhino.

Example inside Rhino's Python editor::

    import sys
    sys.path.append(r"/path/to/fea_toolkit/src")
    from fea_toolkit.rhino.results import apply_results

    apply_results(
        r"C:/models/mesh_results.h5",
        stage="mesh",
        frames=True,          # colour frames by Mz (static, local)
        shells=True,          # colour shells by Nx (pushover, last step)
        shell_quantity="Nx",
        deformed=True,        # deformed-shape overlay (static, auto-scale)
        deformed_source="static",
    )
"""

import numpy as np

# ═══════════════════════════════════════════════════════════════
# Data extraction (pure NumPy — unit-testable outside Rhino)
# ═══════════════════════════════════════════════════════════════


def _get_pushover_directions(data: dict) -> list[str]:
    """Return the pushover directions present in a flat results dict."""
    dirs: list[str] = []
    for key in data:
        if key.startswith("pushover/") and key.count("/") >= 2:
            dirname = key.split("/")[1]
            if dirname not in dirs:
                dirs.append(dirname)
    return dirs


def _load_pushover_shell_quantities(
    data: dict,
    quantity: str = "Nx",
    direction: str = None,
    step: int = None,
    aggregate_parents: bool = False,
):
    """Extract a per-shell value map from pushover results.

    Args:
        data: Flat results dict (already stage-flattened).
        quantity: In-plane shell quantity — ``Nx``, ``Ny``, ``Nxy``,
            ``Mx``, ``My``, ``Mxy``.
        direction: Pushover direction (``+X`` / ``-X`` / ``+Y`` / ``-Y``).
            ``None`` → first available.
        step: Pushover step index.  ``None`` → last step (peak).
        aggregate_parents: Map child-shell values back to their parent
            area IDs (max-abs envelope over children).

    Returns:
        ``(values, (vmin, vmax))`` where ``values`` maps shell/area SAP
        ID → scalar value.  Empty when the quantity is unavailable.
    """
    from ..io.results_schema import make_pushover_key

    directions = _get_pushover_directions(data)
    dirname = direction or (directions[0] if directions else None)
    if dirname is None:
        return {}, (0.0, 0.0)

    ids = data.get(make_pushover_key(dirname, "pushover/{direction}/shell_sap_id"))
    arr = data.get(make_pushover_key(dirname, f"pushover/{{direction}}/shell_{quantity}"))
    if ids is None or arr is None:
        return {}, (0.0, 0.0)

    n_steps = arr.shape[0] if arr.ndim > 1 else 1
    step_idx = max(0, min(step if step is not None else n_steps - 1, n_steps - 1))

    values: dict = {}
    for i in range(len(ids)):
        v = float(arr[step_idx, i]) if arr.ndim > 1 else float(arr[i])
        if not np.isnan(v):
            values[str(ids[i])] = v

    if aggregate_parents:
        parents = data.get("shell_parent_sap_id")
        if parents is not None:
            for i in range(len(ids)):
                parent = str(parents[i]) if parents[i] else ""
                if not parent:
                    continue
                v = values.get(str(ids[i]))
                if v is None:
                    continue
                cur = values.get(parent)
                if cur is None or abs(v) > abs(cur):
                    values[parent] = v

    vals = list(values.values())
    vmin = min(vals) if vals else 0.0
    vmax = max(vals) if vals else 0.0
    return values, (vmin, vmax)


def _load_deformed_arrays(
    data: dict,
    source_type: str = "static",
    case: str = None,
    mode: int = None,
    direction: str = None,
    step: int = None,
):
    """Load node-displacement arrays for a deformed-shape overlay.

    Args:
        data: Flat results dict (already stage-flattened).
        source_type: ``"static"`` (default), ``"modal"``, ``"rs"`` or
            ``"pushover"``.
        case: Static case name (``None`` → first available).
        mode: Modal mode index (0-based) for ``source_type="modal"``.
        direction, step: Pushover direction / step index for
            ``source_type="pushover"`` (step ``None`` → last step).

    Returns:
        ``(dx, dy, dz, tags, label)`` where *dx/dy/dz* are float arrays
        whose rows are ordered by ascending node tag, *tags* is the
        explicit node-tag list paired with those rows (``None`` for
        static, whose arrays are written already tag-sorted without a tag
        list), and *label* names the source.  Returns ``None`` when the
        data is unavailable.
    """
    from ..io.npz_reader import _get_static_cases
    from ..io.results_schema import make_pushover_key

    if source_type == "static":
        cases = _get_static_cases(data)
        case_name = case if case and case in cases else (cases[0] if cases else None)
        if case_name is None:
            return None
        pre = f"static/{case_name}/"
        dx = data.get(f"{pre}node_dx")
        dy = data.get(f"{pre}node_dy")
        dz = data.get(f"{pre}node_dz")
        tags = None
        label = f"static/{case_name}"
    elif source_type == "modal":
        mdx = data.get("modal/mode_dx")
        mdy = data.get("modal/mode_dy")
        mdz = data.get("modal/mode_dz")
        if mdx is None or mdy is None or mdz is None:
            return None
        mode_idx = 0 if mode is None else mode
        n_modes = mdx.shape[1] if mdx.ndim > 1 else 1
        mode_idx = max(0, min(mode_idx, n_modes - 1))
        dx, dy, dz = mdx[:, mode_idx], mdy[:, mode_idx], mdz[:, mode_idx]
        tags = data.get("modal/node_tag")
        label = f"modal/{mode_idx + 1}"
    elif source_type == "rs":
        dx = data.get("rs/node_dx")
        dy = data.get("rs/node_dy")
        dz = data.get("rs/node_dz")
        tags = data.get("rs/node_tag")
        label = "rs"
    elif source_type == "pushover":
        directions = _get_pushover_directions(data)
        dirname = direction or (directions[0] if directions else None)
        if dirname is None:
            return None
        pdx = data.get(make_pushover_key(dirname, "pushover/{direction}/node_disp_x"))
        pdy = data.get(make_pushover_key(dirname, "pushover/{direction}/node_disp_y"))
        pdz = data.get(make_pushover_key(dirname, "pushover/{direction}/node_disp_z"))
        if pdx is None or pdy is None or pdz is None:
            return None
        n_steps = pdx.shape[0] if pdx.ndim > 1 else 1
        step_idx = max(0, min(step if step is not None else n_steps - 1, n_steps - 1))
        dx, dy, dz = pdx[step_idx], pdy[step_idx], pdz[step_idx]
        tags = data.get(make_pushover_key(dirname, "pushover/{direction}/node_tag"))
        label = f"pushover/{dirname}/step{step_idx}"
    else:
        raise ValueError(
            f"Unknown deformed source_type {source_type!r} — use "
            "'static', 'modal', 'rs' or 'pushover'"
        )

    if dx is None or dy is None or dz is None:
        return None
    return (
        np.asarray(dx, dtype=float),
        np.asarray(dy, dtype=float),
        np.asarray(dz, dtype=float),
        tags,
        label,
    )


# ═══════════════════════════════════════════════════════════════
# Rhino entry points
# ═══════════════════════════════════════════════════════════════


def colour_shells_from_results(
    source,
    quantity: str = "Nx",
    direction: str = None,
    step: int = None,
    layer_filter: str = "",
    stage: str = None,
    aggregate_parents: bool = False,
    verbose: bool = True,
) -> int:
    """Colour Rhino shell objects by a pushover in-plane force quantity.

    Matches objects carrying a ``SAP_AreaID`` UserString against the
    ``pushover/{direction}/shell_{quantity}`` arrays (last pushover step
    by default, or the explicit *step*).  Uses the same red-white-blue
    diverging scale as the frame colouring.

    Args:
        source: Results-file path (``.npz`` / ``.h5``) or a flat dict.
        quantity: In-plane shell quantity — ``Nx``, ``Ny``, ``Nxy``,
            ``Mx``, ``My``, ``Mxy``.
        direction: Pushover direction (``None`` → first available).
        step: Pushover step index (``None`` → last step, i.e. peak).
        layer_filter: Optional glob filter on the layer full path.
        stage: Stage to promote for stage files (``None`` → auto).
        aggregate_parents: Map child-shell values to parent area IDs.
        verbose: Print progress messages.

    Returns:
        Number of shell objects coloured.
    """
    from .colour_from_npz import _colour_doc_objects, _load_unified

    data = _load_unified(source, stage=stage)
    values, (vmin, vmax) = _load_pushover_shell_quantities(
        data,
        quantity,
        direction=direction,
        step=step,
        aggregate_parents=aggregate_parents,
    )
    if not values:
        print(f"No {quantity} data found for shells in {source}")
        return 0

    if verbose:
        label = f"[{direction}] " if direction else ""
        print(f"{label}Loaded {len(values)} shells from results")
        print(f"  {quantity} range: [{vmin:.4g}, {vmax:.4g}]")

    coloured = _colour_doc_objects(values, "SAP_AreaID", vmin, vmax, layer_filter=layer_filter)
    if verbose:
        print(f"Coloured {coloured} shell objects by {quantity}")
    return coloured


def create_deformed_geometry(
    source,
    *,
    source_type: str = "static",
    case: str = None,
    mode: int = None,
    direction: str = None,
    step: int = None,
    scale: float = None,
    stage: str = None,
    layer_root: str = "SAP2000/Results",
    verbose: bool = True,
) -> int:
    """Create a deformed-shape overlay from node displacements.

    Builds displaced frame lines and shell quads directly from the file's
    own geometry + displacement arrays (so it works whether or not the
    original geometry was imported), on a dedicated
    ``{layer_root}/Deformed/{label}`` layer.  Objects are tagged with
    ``RES_Kind`` / ``RES_Deformed`` / ``SAP_FrameID`` / ``SAP_AreaID``
    UserStrings so they can be filtered or queried.

    Args:
        source: Results-file path (``.npz`` / ``.h5``) or a flat dict.
        source_type: ``"static"`` (default), ``"modal"``, ``"rs"`` or
            ``"pushover"``.
        case: Static case name (``None`` → first available).
        mode: Modal mode index (0-based) for ``source_type="modal"``.
        direction, step: Pushover direction / step for ``"pushover"``.
        scale: Displacement scale factor.  ``None`` → auto-scale (5% of
            the largest model dimension per unit displacement).
        stage: Stage to promote for stage files (``None`` → auto).
        layer_root: Root layer under which ``Deformed/{label}`` is created.
        verbose: Print progress messages.

    Returns:
        Number of deformed objects created.
    """
    from .colour_from_npz import _load_unified
    from .layers import create_or_get_layer

    data = _load_unified(source, stage=stage)
    loaded = _load_deformed_arrays(
        data,
        source_type,
        case=case,
        mode=mode,
        direction=direction,
        step=step,
    )
    if loaded is None:
        print(f"No {source_type} displacements found in {source}")
        return 0
    dx, dy, dz, disp_tags, label = loaded

    node_x = data.get("node_x")
    node_y = data.get("node_y")
    node_z = data.get("node_z")
    frame_sap = data.get("frame_sap_id")
    frame_i = data.get("frame_node_i")
    frame_j = data.get("frame_node_j")
    shell_sap = data.get("shell_sap_id")
    shell_nodes = [data.get(f"shell_node_{k}") for k in (1, 2, 3, 4)]
    if node_x is None or node_y is None or node_z is None:
        return 0

    n = len(node_x)
    if len(dx) != n or len(dy) != n or len(dz) != n:
        print("Displacement arrays do not match the geometry node count")
        return 0

    # ── Node tag ↔ geometry-row mapping ─────────────────────────────
    # ``node_x/y/z`` rows are in MeshModel dict order, but the frame /
    # shell endpoint arrays store 1-based OpenSees node TAGS and the
    # displacement rows are ordered by ascending node tag.  Map every tag
    # to its geometry row so all three stay aligned.
    node_tag = data.get("node_tag")
    if node_tag is not None and len(node_tag) == n:
        tag_to_row = {int(t): i for i, t in enumerate(node_tag)}
        row_tags = [int(t) for t in node_tag]
    else:
        # Legacy files without a node_tag array: assume sequential 1-based
        # tags in row order.
        tag_to_row = {int(i + 1): i for i in range(n)}
        row_tags = list(range(1, n + 1))

    # Displacement rows are paired with an explicit tag list for modal /
    # rs / pushover; static is written already tag-sorted (no list).
    if disp_tags is None:
        disp_tags = sorted(tag_to_row)
    disp_by_tag: dict = {}
    for i, tag in enumerate(disp_tags):
        if i < len(dx):
            disp_by_tag[int(tag)] = (float(dx[i]), float(dy[i]), float(dz[i]))

    # ── Auto-scale from the model bounding box ────────────────────
    if scale is None:
        span = max(
            float(np.max(node_x) - np.min(node_x)),
            float(np.max(node_y) - np.min(node_y)),
            float(np.max(node_z) - np.min(node_z)),
            1e-12,
        )
        max_disp = max(
            float(np.max(np.abs(dx))),
            float(np.max(np.abs(dy))),
            float(np.max(np.abs(dz))),
        )
        scale = (span * 0.05) / max_disp if max_disp > 1e-15 else 1.0

    # ── Rhino imports — must happen at call time inside Rhino ─────
    import Rhino.DocObjects as rd
    import Rhino.Geometry as rg
    import scriptcontext as sc

    doc = sc.doc
    # The label embeds a source separator (e.g. "static/DEAD"); flatten it
    # to "_" so it forms a single layer segment rather than nested layers.
    layer_name = f"{layer_root}/Deformed/{label.replace('/', '_')}"
    layer_idx = create_or_get_layer(layer_name)

    coords = []
    for r in range(n):
        d = disp_by_tag.get(row_tags[r], (0.0, 0.0, 0.0))
        coords.append(
            rg.Point3d(
                float(node_x[r]) + d[0] * scale,
                float(node_y[r]) + d[1] * scale,
                float(node_z[r]) + d[2] * scale,
            )
        )

    def _attributes(kind: str) -> rd.ObjectAttributes:
        attrs = rd.ObjectAttributes()
        if layer_idx >= 0:
            attrs.LayerIndex = layer_idx
        attrs.SetUserString("RES_Kind", kind)
        attrs.SetUserString("RES_Deformed", label)
        return attrs

    count = 0

    # ── Displaced frame lines ─────────────────────────────────────
    if frame_sap is not None and frame_i is not None and frame_j is not None:
        for i in range(len(frame_sap)):
            ni = tag_to_row.get(int(frame_i[i]))
            nj = tag_to_row.get(int(frame_j[i]))
            if ni is None or nj is None:
                continue
            attrs = _attributes("Frame")
            attrs.SetUserString("SAP_FrameID", str(frame_sap[i]))
            doc.Objects.AddLine(rg.Line(coords[ni], coords[nj]), attrs)
            count += 1

    # ── Displaced shell quads ─────────────────────────────────────
    if shell_sap is not None and all(x is not None for x in shell_nodes):
        for i in range(len(shell_sap)):
            idxs = []
            for k in range(4):
                ridx = tag_to_row.get(int(shell_nodes[k][i]))
                if ridx is not None and 0 <= ridx < n:
                    idxs.append(ridx)
            # Triangles leave node 4 as -1 / equal to node 3.
            if len(idxs) < 3:
                continue
            mesh = rg.Mesh()
            for idx in idxs:
                mesh.Vertices.Add(coords[idx].X, coords[idx].Y, coords[idx].Z)
            if len(idxs) == 4:
                mesh.Faces.AddFace(0, 1, 2, 3)
            else:
                mesh.Faces.AddFace(0, 1, 2)
            mesh.Normals.ComputeNormals()
            attrs = _attributes("Shell")
            attrs.SetUserString("SAP_AreaID", str(shell_sap[i]))
            doc.Objects.AddMesh(mesh, attrs)
            count += 1

    doc.Views.Redraw()
    if verbose:
        print(f"Created {count} deformed object(s) on layer '{layer_name}'")
    return count


def apply_results(
    source,
    *,
    stage: str = None,
    quantity: str = "Mz",
    case: str = None,
    use_local: bool = True,
    frames: bool = True,
    shells: bool = False,
    shell_quantity: str = "Nx",
    shell_direction: str = None,
    shell_step: int = None,
    deformed: bool = False,
    deformed_source: str = "static",
    deformed_mode: int = None,
    deformed_scale: float = None,
    layer_filter: str = "",
    aggregate_parents: bool = False,
    verbose: bool = True,
) -> dict:
    """One-call results application for a Rhino document.

    Reads a results file (``.npz`` / ``.h5`` — plain or stage file),
    then applies the requested actions:

    * ``frames=True`` — colour frame objects by a static force/moment
      quantity (``quantity``, ``case``, ``use_local``);
    * ``shells=True`` — colour shell objects by a pushover in-plane
      quantity (``shell_quantity``, ``shell_direction``, ``shell_step``);
    * ``deformed=True`` — create a deformed-shape overlay
      (``deformed_source``, ``deformed_mode``, ``deformed_scale``).

    ``aggregate_parents=True`` maps child-element results back to their
    parent SAP IDs (max-abs envelope) so SAP-stage geometry can be
    coloured from meshed-stage results.

    Args:
        source: Results-file path (``.npz`` / ``.h5``) or a flat dict.
        stage: Stage to promote for stage files (``None`` → auto).
        quantity: Frame force/moment quantity (default ``'Mz'``).
        case: Static case name (``None`` → first available).
        use_local: Use local-coordinate frame forces.
        frames: Colour frame objects (default ``True``).
        shells: Colour shell objects (default ``False``).
        shell_quantity: Shell quantity — ``Nx``, ``Ny``, ``Nxy``,
            ``Mx``, ``My``, ``Mxy`` (default ``'Nx'``).
        shell_direction: Pushover direction (``None`` → first).
        shell_step: Pushover step index (``None`` → last step).
        deformed: Create a deformed-shape overlay (default ``False``).
        deformed_source: ``"static"`` / ``"modal"`` / ``"rs"`` /
            ``"pushover"``.
        deformed_mode: Modal mode index (0-based).
        deformed_scale: Deformed-shape scale (``None`` → auto).
        layer_filter: Optional glob filter on the layer full path.
        aggregate_parents: Map child results to parent IDs.
        verbose: Print progress messages.

    Returns:
        Summary dict: ``{"coloured_frames": int, "coloured_shells": int,
        "deformed_objects": int}``.
    """
    from .colour_from_npz import colour_from_npz

    out: dict = {"coloured_frames": 0, "coloured_shells": 0, "deformed_objects": 0}

    if frames:
        out["coloured_frames"] = colour_from_npz(
            source,
            quantity=quantity,
            use_local=use_local,
            combo=case,
            layer_filter=layer_filter,
            verbose=verbose,
            stage=stage,
            aggregate_parents=aggregate_parents,
        )

    if shells:
        out["coloured_shells"] = colour_shells_from_results(
            source,
            quantity=shell_quantity,
            direction=shell_direction,
            step=shell_step,
            layer_filter=layer_filter,
            stage=stage,
            aggregate_parents=aggregate_parents,
            verbose=verbose,
        )

    if deformed:
        out["deformed_objects"] = create_deformed_geometry(
            source,
            source_type=deformed_source,
            case=case,
            mode=deformed_mode,
            scale=deformed_scale,
            stage=stage,
            verbose=verbose,
        )

    return out
