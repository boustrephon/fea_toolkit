"""
Unified NPZ writer — assembles model geometry + analysis results into a
single ``.npz`` file following the schema in ``results_schema.py``.

Usage::

    from fea_toolkit.io.npz_writer import write_results_npz

    write_results_npz(
        "results.npz",
        md=model_data,
        static_results={"DEAD": {...}, "SL_X": {...}},
        modal_result={"periods": [...], "mode_shapes": {...}, ...},
        rs_results={"rs_x": {...}, "rs_y": {...}},
    )
"""

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from ..model.sap_data import SAPModelData
from .results_schema import make_pushover_key, make_static_key

if TYPE_CHECKING:
    from ..model.mesh_model import MeshModel


def _collect_geometry(
    md: SAPModelData,
    mesh_model: Optional["MeshModel"] = None,
) -> dict[str, np.ndarray]:
    """Extract model geometry arrays from SAPModelData.

    When *mesh_model* is provided, its post-processing node tags
    and shell element connectivity are used instead of the raw
    ``SAPModelData`` — this gives correct (split/meshed) shell
    geometry for NPZ files written after the preprocessor has run.

    Args:
        md: Parsed SAP2000 model data.
        mesh_model: Optional MeshModel with post-processed topology.
            When provided, uses ``mesh_model.nodes``,
            ``mesh_model.frame_elements``,
            ``mesh_model.area_elements``,
            ``mesh_model.frame_assignments``, and
            ``mesh_model.area_assignments`` for all geometry
            extraction (frames *and* shells).
    """
    arrays: dict[str, np.ndarray] = {}

    # ── Resolve source dicts ──────────────────────────────────────
    # Use MeshModel when available (post-split/mesh topology).
    _nodes = mesh_model.nodes if mesh_model is not None else md.nodes
    _frame_elements = mesh_model.frame_elements if mesh_model is not None else md.frame_elements
    _area_elements = mesh_model.area_elements if mesh_model is not None else md.area_elements
    _frame_assignments = (
        mesh_model.frame_assignments if mesh_model is not None else md.frame_assignments
    )
    _area_assignments = (
        mesh_model.area_assignments if mesh_model is not None else md.area_assignments
    )

    # Nodes
    node_tags = []
    node_sap_ids = []
    node_x, node_y, node_z = [], [], []
    for nid, nd in _nodes.items():
        node_tags.append(nd.node_tag)
        node_sap_ids.append(str(nid))
        node_x.append(nd.x)
        node_y.append(nd.y)
        node_z.append(nd.z)
    arrays["node_tag"] = np.array(node_tags, dtype=int)
    arrays["node_sap_id"] = np.array(node_sap_ids, dtype=str)
    arrays["node_x"] = np.array(node_x, dtype=float)
    arrays["node_y"] = np.array(node_y, dtype=float)
    arrays["node_z"] = np.array(node_z, dtype=float)

    # Frame elements — active only (skip inactive parents)
    frame_eid, frame_sap_id, frame_parent_sap_id, frame_sec_name = [], [], [], []
    frame_ni, frame_nj = [], []
    frame_parent_ni, frame_parent_nj = [], []
    frame_t_start, frame_t_end = [], []

    # Build parent-index lookup for t_start/t_end computation and parent endpoints.
    # Include inactive parents so split children reference the actual interval.
    parent_lookup: dict[str, tuple] = {}  # parent_id -> (t_locations, child_ids)
    parent_endpoints: dict[str, tuple] = {}  # parent_id -> (parent_node_i_tag, parent_node_j_tag)
    for eid, elem in _frame_elements.items():
        if elem.t_locations and elem.child_ids:
            parent_lookup[eid] = (elem.t_locations, elem.child_ids)
        if getattr(elem, "inactive", False):
            # Record parent endpoints for collapse_to_parents visualisation
            p_ni = _nodes.get(elem.node_i)
            p_nj = _nodes.get(elem.node_j)
            if p_ni and p_nj:
                parent_endpoints[eid] = (p_ni.node_tag, p_nj.node_tag)

    for eid, elem in _frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = _nodes.get(elem.node_i)
        nj = _nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        frame_eid.append(len(frame_eid))
        frame_sap_id.append(str(eid))
        frame_parent_sap_id.append(str(elem.parent_id) if elem.parent_id else "")
        sec = _frame_assignments.get(eid, "")
        frame_sec_name.append(sec)
        frame_ni.append(ni.node_tag)
        frame_nj.append(nj.node_tag)

        # Record parent endpoints for collapse_to_parents visualisation
        pid = elem.parent_id
        if pid and pid in parent_endpoints:
            p_ni_tag, p_nj_tag = parent_endpoints[pid]
            frame_parent_ni.append(p_ni_tag)
            frame_parent_nj.append(p_nj_tag)
        else:
            frame_parent_ni.append(0)
            frame_parent_nj.append(0)

        # Compute parametric position along parent
        if pid and pid in parent_lookup:
            t_locs, children = parent_lookup[pid]
            try:
                idx = children.index(eid)
                ts = t_locs[idx - 1] if idx > 0 else 0.0
                te = t_locs[idx] if idx < len(t_locs) else 1.0
                frame_t_start.append(ts)
                frame_t_end.append(te)
            except ValueError:
                frame_t_start.append(0.0)
                frame_t_end.append(1.0)
        else:
            frame_t_start.append(0.0)
            frame_t_end.append(1.0)

    arrays["frame_eid"] = np.array(frame_eid, dtype=int)
    arrays["frame_sap_id"] = np.array(frame_sap_id, dtype=str)
    arrays["frame_parent_sap_id"] = np.array(frame_parent_sap_id, dtype=str)
    arrays["frame_sec_name"] = np.array(frame_sec_name, dtype=str)
    arrays["frame_node_i"] = np.array(frame_ni, dtype=int)
    arrays["frame_node_j"] = np.array(frame_nj, dtype=int)
    arrays["frame_parent_node_i"] = np.array(frame_parent_ni, dtype=int)
    arrays["frame_parent_node_j"] = np.array(frame_parent_nj, dtype=int)
    arrays["frame_t_start"] = np.array(frame_t_start, dtype=float)
    arrays["frame_t_end"] = np.array(frame_t_end, dtype=float)

    # Shell elements (quad only)
    shell_eid, shell_sap_id, shell_sec_name, shell_parent_sap_id = [], [], [], []
    s1, s2, s3, s4 = [], [], [], []
    for aid, area in _area_elements.items():
        if getattr(area, "inactive", False):
            continue
        if len(area.node_ids) < 3:
            continue
        nids = area.node_ids[:4]
        tags = []
        for nid in nids:
            nd = _nodes.get(nid)
            if nd is None:
                break
            tags.append(nd.node_tag)
        if len(tags) < 3:
            continue
        # Pad to 4 (repeat last for triangles)
        while len(tags) < 4:
            tags.append(tags[-1])
        shell_eid.append(len(shell_eid))
        shell_sap_id.append(str(aid))
        sec2 = _area_assignments.get(aid, "")
        shell_sec_name.append(sec2)
        s1.append(tags[0])
        s2.append(tags[1])
        s3.append(tags[2])
        s4.append(tags[3])
        # Record parent SAP ID for collapse_to_parents visualisation
        pid = getattr(area, "parent_id", None)
        shell_parent_sap_id.append(str(pid) if pid else "")

    arrays["shell_eid"] = np.array(shell_eid, dtype=int)
    arrays["shell_sap_id"] = np.array(shell_sap_id, dtype=str)
    arrays["shell_sec_name"] = np.array(shell_sec_name, dtype=str)
    arrays["shell_node_1"] = np.array(s1, dtype=int)
    arrays["shell_node_2"] = np.array(s2, dtype=int)
    arrays["shell_node_3"] = np.array(s3, dtype=int)
    arrays["shell_node_4"] = np.array(s4, dtype=int)
    arrays["shell_parent_sap_id"] = np.array(shell_parent_sap_id, dtype=str)

    return arrays


def _collect_static(static_results: dict[str, Any]) -> dict[str, np.ndarray]:
    """Extract static analysis arrays.

    Each static case is a dict whose entries are serialized as flat
    ``static/{case}/{key}`` NPZ arrays:

    * The standard 12 frame force keys (``fx_i`` … ``mz_j``) are written as
      per-element arrays (shape ``(n_elem,)``, empty when no element forces
      are recorded).
    * ``nodal_displacements`` — dict of ``{tag: [dx, dy, dz]}`` — is written
      as ``node_dx`` / ``node_dy`` / ``node_dz`` arrays ordered by node tag.
    * **Scalar entries** — any remaining key whose value is a JSON-scalar
      (``int`` / ``float`` / ``str`` / ``bool`` / ``np.bool_`` / ``np.number``)
      or a ``{"value": scalar}`` dict — are persisted as shape-``(1,)``
      arrays.  This is how performance-point scalars (e.g.
      ``static/pp/+X/D_roof`` and the numpy ``converged`` flag) survive
      serialization; without it they are silently dropped.
    """
    arrays: dict[str, np.ndarray] = {}
    case_labels = list(static_results.keys())
    arrays["static_case_labels"] = np.array(case_labels, dtype=str)

    force_keys = [
        "fx_i",
        "fy_i",
        "fz_i",
        "mx_i",
        "my_i",
        "mz_i",
        "fx_j",
        "fy_j",
        "fz_j",
        "mx_j",
        "my_j",
        "mz_j",
    ]
    for case in case_labels:
        data = static_results[case]
        for key in force_keys:
            vals = data.get(key, data.get("element_forces", {}).get(key, []))
            arrays[make_static_key(case, key)] = np.asarray(vals, dtype=float)

        # Nodal displacements
        disp = data.get("nodal_displacements", {})
        if disp:
            # Convert dict to array ordered by node_tag
            tags = sorted(disp.keys(), key=int)
            for i, dof in enumerate(["dx", "dy", "dz"]):
                arr = np.array([disp[t][i] for t in tags], dtype=float)
                arrays[make_static_key(case, f"node_{dof}")] = arr

        # Scalar entries (including ``{"value": scalar}`` wrappers).
        # Keys already consumed above are skipped.
        consumed = set(force_keys) | {"nodal_displacements"}
        for key, raw_val in data.items():
            if key in consumed:
                continue
            # Unwrap a single-key ``{"value": scalar}`` wrapper.
            if isinstance(raw_val, dict):
                if set(raw_val) <= {"value"}:
                    val = raw_val.get("value")
                elif case == "pp":
                    # PP payloads must be flat ``f"{direction}/{field}"``
                    # keys with plain scalar values.  Nested per-direction
                    # cases (e.g. ``{"+X": {...}}``) or any other dict with
                    # more than the "value" key are malformed and must be
                    # fixed at the call site rather than silently dropped.
                    raise ValueError(
                        f"static case {case!r} key {key!r} is a nested dict — "
                        "only flat scalar values or {'value': scalar} wrappers "
                        f"are allowed (e.g. static/pp/+X/D_roof)."
                    )
                else:
                    # Non-PP nested data (e.g. ``reactions``) is not a
                    # serializable scalar — skip it as before.
                    continue
            else:
                val = raw_val
            if isinstance(val, (int, float, str, bool, np.bool_, np.number)):
                arrays[make_static_key(case, key)] = np.array([val])
            elif case == "pp":
                raise ValueError(
                    f"static case {case!r} key {key!r} has unsupported value "
                    f"type {type(val).__name__} — expected a JSON scalar "
                    "(int, float, str, bool) or a {'value': scalar} wrapper."
                )

    return arrays


def _collect_modal(
    modal_result: dict[str, Any], mode_shapes: Optional[dict] = None
) -> dict[str, np.ndarray]:
    """Extract modal analysis arrays."""
    arrays: dict[str, np.ndarray] = {}
    mp = modal_result.get("modal_props", {})
    periods = list(modal_result.get("periods", []))
    n = len(periods)
    if n == 0:
        return arrays

    arrays["modal/period"] = np.array(periods, dtype=float)
    arrays["modal/frequency"] = np.array([1.0 / p if p > 0 else 0.0 for p in periods], dtype=float)
    arrays["modal/omega"] = np.array(
        [2.0 * np.pi / p if p > 0 else 0.0 for p in periods], dtype=float
    )

    for key, npz_key in [
        ("partiMassRatiosMX", "modal/mx_ratio"),
        ("partiMassRatiosMY", "modal/my_ratio"),
        ("partiMassRatiosMZ", "modal/mz_ratio"),
        ("partiMassMX", "modal/mx_eff"),
        ("partiMassMY", "modal/my_eff"),
        ("partiMassMZ", "modal/mz_eff"),
    ]:
        vals = mp.get(key, [])
        padded = (list(vals) + [0.0] * n)[:n]
        arrays[npz_key] = np.array(padded, dtype=float)

    # Mode shapes (eigenvectors)
    if mode_shapes is not None and n > 0:
        # Build N_node × N_mode arrays
        node_tags = sorted(mode_shapes.get(0, {}).keys())
        if node_tags:
            n_nodes = len(node_tags)
            tag_to_idx = {t: i for i, t in enumerate(node_tags)}
            for dof_idx, npz_key in enumerate(["modal/mode_dx", "modal/mode_dy", "modal/mode_dz"]):
                arr = np.zeros((n_nodes, n))
                for midx in range(n):
                    node_vals = mode_shapes.get(midx, {})
                    for tag, disp in node_vals.items():
                        idx = tag_to_idx.get(tag)
                        if idx is not None:
                            arr[idx, midx] = disp[dof_idx]
                arrays[npz_key] = arr
            # Row alignment for the N_node × N_mode arrays above.  The
            # geometry ``node_tag`` array is written in MeshModel dict
            # order (see _collect_geometry), which is *not* sorted, so
            # standalone visualizers (plot_mode_animation NPZ path) must
            # pair row i of mode_dx/y/z against this explicit sorted tag
            # list rather than the geometry node_tag field.
            arrays["modal/node_tag"] = np.array(node_tags, dtype=int)

    return arrays


def _collect_rs(rs_x: Optional[dict] = None, rs_y: Optional[dict] = None) -> dict[str, np.ndarray]:
    """Extract response-spectrum arrays with directional keys."""
    arrays: dict[str, np.ndarray] = {}
    for direction, d_key in [("X", "x"), ("Y", "y")]:
        rs = rs_x if direction == "X" else rs_y
        if rs is None:
            continue
        len(rs.get("modal_periods", []))
        arrays[f"rs/sa_{d_key}"] = np.array(rs.get("spectral_accels", []), dtype=float)
        arrays[f"rs/eff_mass_{d_key}"] = np.array(rs.get("effective_masses", []), dtype=float)
        arrays[f"rs/v_base_{d_key}"] = np.array(rs.get("modal_base_shear", []), dtype=float)
        arrays[f"rs/v_cqc_{d_key}"] = np.array([rs.get("base_shear_cqc", 0.0)])
        arrays[f"rs/v_total_{d_key}"] = np.array([rs.get("base_shear_total", 0.0)])
    return arrays


def _collect_shell_forces(shell_forces: dict[str, dict[str, Any]]) -> dict[str, np.ndarray]:
    """Extract shell element force arrays from ``extract_static_shell_forces()``.

    Returns flat arrays keyed by ``shell_*`` for NPZ serialization.
    """
    arrays: dict[str, np.ndarray] = {}
    aids = list(shell_forces.keys())
    len(aids)
    arrays["shell_forces/sap_id"] = np.array(aids, dtype=str)
    for key in ("fx", "fy", "fxy", "mx", "my", "mxy"):
        arrays[f"shell_forces/{key}"] = np.array(
            [shell_forces[aid].get(key, 0.0) for aid in aids], dtype=float
        )
    arrays["shell_forces/elem_tag"] = np.array(
        [shell_forces[aid].get("elem_tag", 0) for aid in aids], dtype=int
    )
    arrays["shell_forces/sec_name"] = np.array(
        [shell_forces[aid].get("sec_name", "") for aid in aids], dtype=str
    )
    return arrays


def write_results_npz(
    path: str,
    md: SAPModelData,
    static_results: Optional[dict[str, Any]] = None,
    modal_result: Optional[dict[str, Any]] = None,
    mode_shapes: Optional[dict] = None,
    rs_results: Optional[dict[str, Any]] = None,
    shell_forces: Optional[dict[str, dict[str, Any]]] = None,
    force_unit: str = "kN",
    length_unit: str = "m",
    forces_coordinate_system: str = "local",
    mesh_model: Optional["MeshModel"] = None,
) -> str:
    """Write a unified NPZ file with model geometry + analysis results.

    Args:
        path: Output ``.npz`` file path.
        md: Parsed ``SAPModelData``.
        static_results: Dict from ``run_static_analysis()`` keyed by case.
        modal_result: Dict from ``run_modal_analysis()``.
        mode_shapes: Dict from ``extract_mode_shapes()``.
        rs_results: Dict with keys ``'rs_x'``, ``'rs_y'`` from ``run_rs()``.
        shell_forces: Dict from ``extract_static_shell_forces()``.
        force_unit: Force unit string for metadata.
        length_unit: Length unit string for metadata.
        forces_coordinate_system: Coordinate system of the recorded frame
            end-force arrays.  Defaults to ``"local"`` — frame end-forces
            (``static/*/fx_i``, ...) are recorded in the element LOCAL
            coordinate system via the OpenSees "localForces" query.  If a
            caller passes a different value, the forces must already be in
            that coordinate system before collection.
        mesh_model: Optional post-processed ``MeshModel``. When provided,
            geometry arrays (nodes, frame elements, shell elements) are
            extracted from ``mesh_model`` instead of ``md``, giving
            correct split/meshed topology for visualisation.
            Pass the same ``MeshModel`` that was passed to the
            ``AnalysisBuilder``.

    Returns:
        Absolute path to the saved file.
    """
    if forces_coordinate_system != "local":
        raise ValueError(
            f"forces_coordinate_system must be 'local', got {forces_coordinate_system!r}"
        )

    arrays: dict[str, np.ndarray] = {}

    # Geometry
    arrays.update(_collect_geometry(md, mesh_model))

    # Analysis types present
    analysis_types = []
    if static_results:
        analysis_types.append("static")
        arrays.update(_collect_static(static_results))
    if modal_result:
        analysis_types.append("modal")
        arrays.update(_collect_modal(modal_result, mode_shapes))
    if rs_results:
        analysis_types.append("rs")
        if rs_results.get("rs_x") or rs_results.get("rs_y"):
            arrays.update(_collect_rs(rs_results.get("rs_x"), rs_results.get("rs_y")))
    if shell_forces:
        analysis_types.append("shell_forces")
        arrays.update(_collect_shell_forces(shell_forces))
    # NOTE: Legacy flat aliases (shell_fx, shell_fy, ...) were previously
    # written here for backward compatibility.  As of 2026-07 all consumers
    # use the namespaced ``shell_forces/*`` keys or the geometry-native
    # ``shell_sap_id`` / ``shell_sec_name`` arrays.  No flat aliases are
    # emitted.
    arrays["analysis_types"] = np.array(analysis_types, dtype=str)
    arrays["force_unit"] = np.array([force_unit], dtype=str)
    arrays["length_unit"] = np.array([length_unit], dtype=str)
    arrays["created"] = np.array([datetime.datetime.now().isoformat()], dtype=str)
    arrays["forces_coordinate_system"] = np.array([forces_coordinate_system], dtype=str)

    path = str(Path(path).resolve())
    # Pyright's numpy stub declares ``allow_pickle`` before ``**kwds``;
    # ``**dict[str, ndarray]`` expansion triggers a false-positive overlap
    # diagnostic on ``savez_compressed``.  Unpack via an ``Any`` reference —
    # runtime behaviour is unchanged.
    _arrays: Any = arrays
    np.savez_compressed(path, **_arrays)
    return path


def _collect_pushover(
    mesh_model: "MeshModel",
    step_results: list[dict[str, Any]],
    direction: str = "+X",
) -> dict[str, np.ndarray]:
    """Collect pushover per-step results into NPZ arrays.

    Args:
        mesh_model: MeshModel for geometry arrays.
        step_results: List of per-step dicts from
            ``AnalysisBuilder.pushover_step_results``.
        direction: Push direction label (e.g. ``"+X"``).

    Returns:
        Dict of ``{array_name: np.ndarray}`` keyed per the
        ``PUSHOVER_GLOBAL_ARRAYS``, ``PUSHOVER_FRAME_ARRAYS``,
        ``PUSHOVER_SHELL_ARRAYS``, and ``PUSHOVER_NODE_DISP_ARRAYS`` schema.
    """
    arrays: dict[str, np.ndarray] = {}
    n_step = len(step_results)
    if n_step == 0:
        return arrays

    # ── Global arrays ─────────────────────────────────────────
    steps_arr = np.array([sd.get("step", 0) for sd in step_results], dtype=int)
    arrays[make_pushover_key(direction, "pushover/{direction}/step")] = steps_arr
    # control_disp and base_shear are NOT stored in step_results
    # (they come from the pushover result dict).  The caller must
    # add them separately if needed.

    # ── Frame arrays ──────────────────────────────────────────
    # Collect all recorded frame IDs in order of first appearance
    frame_ids: list[str] = []
    for sd in step_results:
        for eid in sd.get("frame_forces", {}):
            if eid not in frame_ids:
                frame_ids.append(eid)
    n_frame = len(frame_ids)

    if n_frame > 0:
        arrays[make_pushover_key(direction, "pushover/{direction}/frame_sap_id")] = np.array(
            frame_ids, dtype=str
        )

        # Pre-allocate 2D arrays: (n_step, n_frame)
        comp_keys = [
            "fx_i",
            "fy_i",
            "fz_i",
            "mx_i",
            "my_i",
            "mz_i",
            "fx_j",
            "fy_j",
            "fz_j",
            "mx_j",
            "my_j",
            "mz_j",
        ]
        comp_map = {
            f"frame_{k}": np.full((n_step, n_frame), np.nan, dtype=float) for k in comp_keys
        }
        frame_id_to_idx = {eid: i for i, eid in enumerate(frame_ids)}
        for si, sd in enumerate(step_results):
            for eid, forces in sd.get("frame_forces", {}).items():
                if eid not in frame_id_to_idx:
                    continue
                j = frame_id_to_idx[eid]
                for key in comp_keys:
                    comp_map[f"frame_{key}"][si, j] = forces.get(key, np.nan)

        for key, arr in comp_map.items():
            arrays[make_pushover_key(direction, f"pushover/{{direction}}/{key}")] = arr

    # ── Shell arrays ──────────────────────────────────────────
    shell_ids: list[str] = []
    for sd in step_results:
        for aid in sd.get("shell_forces", {}):
            if aid not in shell_ids:
                shell_ids.append(aid)
    n_shell = len(shell_ids)

    if n_shell > 0:
        arrays[make_pushover_key(direction, "pushover/{direction}/shell_sap_id")] = np.array(
            shell_ids, dtype=str
        )

        shell_comp_keys = ["Nx", "Ny", "Nxy", "Mx", "My", "Mxy"]
        shell_comp_map = {
            f"shell_{k}": np.full((n_step, n_shell), np.nan, dtype=float) for k in shell_comp_keys
        }
        shell_id_to_idx = {aid: i for i, aid in enumerate(shell_ids)}
        for si, sd in enumerate(step_results):
            for aid, forces in sd.get("shell_forces", {}).items():
                if aid not in shell_id_to_idx:
                    continue
                j = shell_id_to_idx[aid]
                for key in shell_comp_keys:
                    shell_comp_map[f"shell_{key}"][si, j] = forces.get(key, np.nan)

        for key, arr in shell_comp_map.items():
            arrays[make_pushover_key(direction, f"pushover/{{direction}}/{key}")] = arr

    # ── Node displacement arrays ──────────────────────────────
    # Use all geometry nodes so ``node_tag`` matches the ``N_node``
    # dimension declared by PUSHOVER_NODE_DISP_ARRAYS (which mirrors the
    # geometry ``N_node`` dim from ``_collect_geometry``).  Steps that did
    # not record a particular node are filled with NaN below; never emit a
    # subset of recorded node tags under the N_node schema.
    node_tags = sorted(nd.node_tag for nd in mesh_model.nodes.values())
    n_node = len(node_tags)

    if n_node > 0 and n_step > 0:
        # Check if we have displacement data
        has_disp = any(
            "node_displacements" in sd and sd["node_displacements"] for sd in step_results
        )
        if has_disp:
            arrays[make_pushover_key(direction, "pushover/{direction}/node_tag")] = np.array(
                node_tags, dtype=int
            )

            dx_arr = np.full((n_step, n_node), np.nan, dtype=float)
            dy_arr = np.full((n_step, n_node), np.nan, dtype=float)
            dz_arr = np.full((n_step, n_node), np.nan, dtype=float)

            for si, sd in enumerate(step_results):
                nd = sd.get("node_displacements", {})
                for j, tag in enumerate(node_tags):
                    disp = nd.get(tag)
                    if disp is not None:
                        dx_arr[si, j] = disp[0]
                        dy_arr[si, j] = disp[1]
                        dz_arr[si, j] = disp[2]

            arrays[make_pushover_key(direction, "pushover/{direction}/node_disp_x")] = dx_arr
            arrays[make_pushover_key(direction, "pushover/{direction}/node_disp_y")] = dy_arr
            arrays[make_pushover_key(direction, "pushover/{direction}/node_disp_z")] = dz_arr

    return arrays


def write_pushover_results_npz(
    path: str,
    mesh_model: "MeshModel",
    step_results: list[dict[str, Any]],
    direction: str = "+X",
    pushover_results: Optional[dict[str, Any]] = None,
    force_unit: str = "kN",
    length_unit: str = "m",
    forces_coordinate_system: str = "local",
) -> str:
    """Write pushover step results to NPZ.

    Writes geometry arrays (from the MeshModel) plus pushover-specific
    per-step frame and shell forces.

    Args:
        path: Output ``.npz`` file path.
        mesh_model: MeshModel for geometry arrays.
        step_results: List of per-step dicts from
            ``AnalysisBuilder.pushover_step_results``.
        direction: Push direction label (e.g. ``"+X"``).
        pushover_results: Optional full result dict from
            ``AnalysisBuilder.run_pushover_analysis()``.  When provided,
            global arrays (step, control_disp, base_shear) are included.
        force_unit: Force unit string for metadata.
        length_unit: Length unit string for metadata.
        forces_coordinate_system: Coordinate system of the recorded frame
            end-force arrays.  Defaults to ``"local"`` — frame end-forces
            (``pushover/{direction}/frame_fx_i``, ...) are recorded in the
            element LOCAL coordinate system via the OpenSees "localForces"
            query.  If a caller passes a different value, the forces must
            already be in that coordinate system before collection.

    Returns:
        Absolute path to the saved file.
    """
    if forces_coordinate_system != "local":
        raise ValueError(
            f"forces_coordinate_system must be 'local', got {forces_coordinate_system!r}"
        )

    arrays: dict[str, np.ndarray] = {}

    # ── Geometry (from MeshModel) ─────────────────────────────
    # Build geometry from mesh_model using a minimal SAPModelData stub
    # that ``_collect_geometry`` can read.  Since MeshModel and SAPModelData
    # share the same field names for nodes/frames/areas, we can pass the
    # mesh_model directly via the mesh_model parameter.
    # We need an SAPModelData stub for the ``md`` parameter.
    from ..model.sap_data import SAPModelData

    stub_md = SAPModelData(
        nodes=mesh_model.nodes,
        restraints={},
        materials=mesh_model.materials,
        sections=mesh_model.sections,
        frame_elements=mesh_model.frame_elements,
        area_elements=mesh_model.area_elements,
        frame_assignments=mesh_model.frame_assignments,
        area_assignments=mesh_model.area_assignments,
        groups=mesh_model.groups,
        frame_auto_mesh={},
    )
    arrays.update(_collect_geometry(stub_md, mesh_model=mesh_model))

    # ── Pushover arrays ───────────────────────────────────────
    analysis_types: list[str] = ["pushover"]
    po_arrays = _collect_pushover(mesh_model, step_results, direction=direction)

    # Add global arrays from pushover_results if provided
    if pushover_results is not None:
        steps_full = pushover_results.get("step", [])
        n_full = len(steps_full)
        if n_full > 0:
            # Align global arrays with recorded step_results.
            # step_results only contains entries for converged steps
            # (ok == 0), while the global arrays (step, control_disp,
            # base_shear) may contain entries for every iteration.  The
            # per-element force arrays in po_arrays are indexed by the
            # recorded steps, so the global arrays must always have
            # exactly ``n_aligned`` entries — trim to the recorded-step
            # ordering and NaN-pad any recorded step missing from the
            # full arrays.
            recorded_steps = [int(sd.get("step", 0)) for sd in step_results]
            len(recorded_steps)
            step_to_idx_full = {int(s): i for i, s in enumerate(steps_full)}
            full_disp = pushover_results.get("control_disp", [0.0] * n_full)
            full_shear = pushover_results.get("base_shear", [0.0] * n_full)

            aligned_steps = []
            aligned_disp = []
            aligned_shear = []
            for rs in recorded_steps:
                idx = step_to_idx_full.get(rs)
                if idx is not None:
                    aligned_steps.append(int(steps_full[idx]))
                    aligned_disp.append(float(full_disp[idx]))
                    aligned_shear.append(float(full_shear[idx]))
                else:
                    # Recorded step missing from the full arrays — pad
                    # with NaN to preserve the aligned length.
                    aligned_steps.append(rs)
                    aligned_disp.append(float("nan"))
                    aligned_shear.append(float("nan"))

            # Write aligned arrays directly into po_arrays so the
            # aligned values are actually used rather than being
            # shadowed by the step array from ``_collect_pushover``.
            po_arrays[make_pushover_key(direction, "pushover/{direction}/step")] = np.array(
                aligned_steps, dtype=int
            )
            po_arrays[make_pushover_key(direction, "pushover/{direction}/control_disp")] = np.array(
                aligned_disp, dtype=float
            )
            po_arrays[make_pushover_key(direction, "pushover/{direction}/base_shear")] = np.array(
                aligned_shear, dtype=float
            )

    arrays.update(po_arrays)

    # ── Metadata ──────────────────────────────────────────────
    arrays["analysis_types"] = np.array(analysis_types, dtype=str)
    arrays["force_unit"] = np.array([force_unit], dtype=str)
    arrays["length_unit"] = np.array([length_unit], dtype=str)
    arrays["created"] = np.array([datetime.datetime.now().isoformat()], dtype=str)
    arrays["forces_coordinate_system"] = np.array([forces_coordinate_system], dtype=str)

    path = str(Path(path).resolve())
    # Pyright's numpy stub declares ``allow_pickle`` before ``**kwds``;
    # ``**dict[str, ndarray]`` expansion triggers a false-positive overlap
    # diagnostic on ``savez_compressed``.  Unpack via an ``Any`` reference —
    # runtime behaviour is unchanged.
    _arrays: Any = arrays
    np.savez_compressed(path, **_arrays)
    return path
