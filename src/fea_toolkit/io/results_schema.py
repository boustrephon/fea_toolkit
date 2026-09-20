"""
Unified NPZ results schema — defines the array layout for all analysis results.

Aligns with opstool's ODB (NetCDF) dimension naming so NPZ ↔ ODB conversion
is a direct rename.  See ``docs/results_schema.md`` for the full specification.
"""

import typing as t

import numpy as np

# ── File schema version ──────────────────────────────────────────────
# Legacy files written before versioning was introduced carry no
# ``schema_version`` array and are treated as ``SCHEMA_VERSION_LEGACY``.
SCHEMA_VERSION_LEGACY = 1
#: Version of the unified results-file layout.  Bump on a
#: backward-incompatible change (consumers read the array and may warn
#: or adapt).  Written as a ``schema_version`` array by
#: :func:`fea_toolkit.io.stage_writer.write_model_stages`,
#: :func:`fea_toolkit.io.npz_writer.write_results_npz` and
#: :func:`fea_toolkit.io.unified_writer.write_results`.
SCHEMA_VERSION = 2

# ── Required array names per result type ──────────────────────────────────

GEOMETRY_ARRAYS: dict[str, tuple] = {
    "node_tag": ("N_node", "int"),
    "node_sap_id": ("N_node", "str"),
    "node_x": ("N_node", "float"),
    "node_y": ("N_node", "float"),
    "node_z": ("N_node", "float"),
    "frame_eid": ("N_frame", "int"),
    "frame_sap_id": ("N_frame", "str"),
    "frame_parent_sap_id": ("N_frame", "str"),
    "frame_sec_name": ("N_frame", "str"),
    "frame_node_i": ("N_frame", "int"),
    "frame_node_j": ("N_frame", "int"),
    "frame_parent_node_i": ("N_frame", "int"),
    "frame_parent_node_j": ("N_frame", "int"),
    "frame_t_start": ("N_frame", "float"),  # optional — 0..1 parametric position
    "frame_t_end": ("N_frame", "float"),  # optional — 0..1 parametric position
    "shell_eid": ("N_shell", "int"),
    "shell_sap_id": ("N_shell", "str"),
    "shell_parent_sap_id": ("N_shell", "str"),
    "shell_sec_name": ("N_shell", "str"),
    "shell_node_1": ("N_shell", "int"),
    "shell_node_2": ("N_shell", "int"),
    "shell_node_3": ("N_shell", "int"),
    "shell_node_4": ("N_shell", "int"),
}

#: Nodal displacement arrays — sized ``N_node`` (not ``N_frame``).
STATIC_NODAL_ARRAYS = [
    "node_dx",
    "node_dy",
    "node_dz",
]

#: Element end forces — sized ``N_frame``.  By project convention these are
#: recorded in the element **local** system, flagged by the
#: ``forces_coordinate_system`` metadata array (``"local"``);
#: ``_extract_npz_frame_forces`` derives the ``*_local`` aliases from them at
#: read time.  Required whenever a static case is present.
STATIC_FORCE_ARRAYS = [
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

#: Explicit ``*_local`` variant aliases of the force arrays above — sized
#: ``N_frame``.  Optional: producers that rely on the
#: ``forces_coordinate_system`` metadata do not write them (visualisers
#: synthesise the aliases on read), so they are present only when a producer
#: recorded them explicitly.
STATIC_LOCAL_FORCE_ARRAYS = [
    "fx_i_local",
    "fy_i_local",
    "fz_i_local",
    "mx_i_local",
    "my_i_local",
    "mz_i_local",
    "fx_j_local",
    "fy_j_local",
    "fz_j_local",
    "mx_j_local",
    "my_j_local",
    "mz_j_local",
]

#: Full set of static array names (union) — kept for reference.
STATIC_ARRAYS = STATIC_NODAL_ARRAYS + STATIC_FORCE_ARRAYS + STATIC_LOCAL_FORCE_ARRAYS

MODAL_ARRAYS: dict[str, tuple] = {
    "modal/period": ("N_mode", "float"),
    "modal/frequency": ("N_mode", "float"),
    "modal/omega": ("N_mode", "float"),
    "modal/mx_ratio": ("N_mode", "float"),
    "modal/my_ratio": ("N_mode", "float"),
    "modal/mz_ratio": ("N_mode", "float"),
    "modal/rx_ratio": ("N_mode", "float"),
    "modal/ry_ratio": ("N_mode", "float"),
    "modal/rz_ratio": ("N_mode", "float"),
    "modal/mx_eff": ("N_mode", "float"),
    "modal/my_eff": ("N_mode", "float"),
    "modal/mz_eff": ("N_mode", "float"),
    "modal/mode_dx": ("N_node N_mode", "float"),
    "modal/mode_dy": ("N_node N_mode", "float"),
    "modal/mode_dz": ("N_node N_mode", "float"),
    "modal/node_tag": ("N_node", "int"),
}

RS_ARRAYS: dict[str, tuple] = {
    "rs/period": ("N_mode", "float"),
    "rs/v_base_x": ("N_mode", "float"),
    "rs/v_base_y": ("N_mode", "float"),
    "rs/v_cqc_x": ("", "float"),
    "rs/v_cqc_y": ("", "float"),
    "rs/v_srss_x": ("", "float"),
    "rs/v_srss_y": ("", "float"),
    # Combined base overturning moment (force × length)
    "rs/m_cqc_x": ("", "float"),
    "rs/m_cqc_y": ("", "float"),
    "rs/m_srss_x": ("", "float"),
    "rs/m_srss_y": ("", "float"),
    # Combined roof displacement (length)
    "rs/roof_disp_cqc_x": ("", "float"),
    "rs/roof_disp_cqc_y": ("", "float"),
    "rs/roof_disp_srss_x": ("", "float"),
    "rs/roof_disp_srss_y": ("", "float"),
    # Element-level combined forces (N_frame) — OPTIONAL: written only when a
    # producer supplies ``rs_element_forces``.  Forces are in the element
    # **local** system (``ops.eleResponse(tag, "localForces")``), matching the
    # static ``fx_i`` … ``mz_j`` convention, and are combined across modes with
    # the rule named by ``rs/elem_combination`` (CQC by default, or SRSS).
    "rs/elem_sap_id": ("N_frame", "str"),
    "rs/elem_z_bot": ("N_frame", "float"),
    "rs/elem_z_mid": ("N_frame", "float"),
    #: Modal combination rule used for the element block ('cqc' or 'srss').
    "rs/elem_combination": ("", "str"),
    #: Excitation direction the element block refers to ('X' / 'Y' / 'Z').
    "rs/elem_direction": ("", "str"),
    "rs/elem_fx_i": ("N_frame", "float"),
    "rs/elem_fy_i": ("N_frame", "float"),
    "rs/elem_fz_i": ("N_frame", "float"),
    "rs/elem_mx_i": ("N_frame", "float"),
    "rs/elem_my_i": ("N_frame", "float"),
    "rs/elem_mz_i": ("N_frame", "float"),
    "rs/elem_fx_j": ("N_frame", "float"),
    "rs/elem_fy_j": ("N_frame", "float"),
    "rs/elem_fz_j": ("N_frame", "float"),
    "rs/elem_mx_j": ("N_frame", "float"),
    "rs/elem_my_j": ("N_frame", "float"),
    "rs/elem_mz_j": ("N_frame", "float"),
    # ── Legacy aliases (DEPRECATED — delete with the 2D-only RS readers) ──
    # ``Vy``/``Vz`` were historically *derived* from the moment gradient and
    # are now simply the local shears (``Vy == rs/elem_fy_*``,
    # ``Vz == rs/elem_fz_*``).  They are retained because the 2D RS renderer
    # (``_build_series_from_rs`` / ``_render_rs``) and archives predating the
    # full-component block read these keys.  See ``docs/deprecation_plan.md``.
    "rs/elem_Vy_i": ("N_frame", "float"),
    "rs/elem_Vy_j": ("N_frame", "float"),
    "rs/elem_Vz_i": ("N_frame", "float"),
    "rs/elem_Vz_j": ("N_frame", "float"),
    "rs/elem_My_i": ("N_frame", "float"),
    "rs/elem_My_j": ("N_frame", "float"),
    "rs/elem_Mz_i": ("N_frame", "float"),
    "rs/elem_Mz_j": ("N_frame", "float"),
    # Nodal CQC-combined displacements (N_node) — OPTIONAL: written only
    # when a producer supplies ``rs_nodal_displacements``.
    "rs/node_tag": ("N_node", "int"),
    "rs/node_dx": ("N_node", "float"),
    "rs/node_dy": ("N_node", "float"),
    "rs/node_dz": ("N_node", "float"),
}

# ── Pushover per-step results (direction-keyed) ───────────────────────────

PUSHOVER_GLOBAL_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/step": ("N_step", "int"),
    "pushover/{direction}/control_disp": ("N_step", "float"),
    "pushover/{direction}/base_shear": ("N_step", "float"),
}

PUSHOVER_FRAME_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/frame_sap_id": ("N_recorded_frame", "str"),
    "pushover/{direction}/frame_fx_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fy_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fz_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mx_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_my_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mz_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fx_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fy_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fz_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mx_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_my_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mz_j": ("N_step N_recorded_frame", "float"),
}

PUSHOVER_SHELL_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/shell_sap_id": ("N_recorded_shell", "str"),
    "pushover/{direction}/shell_Nx": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Ny": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Nxy": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Mx": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_My": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Mxy": ("N_step N_recorded_shell", "float"),
}

PUSHOVER_NODE_DISP_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/node_tag": ("N_node", "int"),
    "pushover/{direction}/node_disp_x": ("N_step N_node", "float"),
    "pushover/{direction}/node_disp_y": ("N_step N_node", "float"),
    "pushover/{direction}/node_disp_z": ("N_step N_node", "float"),
}

META_ARRAYS: dict[str, tuple] = {
    "force_unit": ("", "str"),
    "length_unit": ("", "str"),
    "forces_coordinate_system": ("", "str"),
    "created": ("", "str"),
    "analysis_types": ("N_analysis", "str"),
}


def make_static_key(case_name: str, array_name: str) -> str:
    """Build the NPZ key for a static result array, e.g. ``static/DEAD/fx_i``."""
    return f"static/{case_name}/{array_name}"


#: Optional per-case metadata fields and the top-level arrays they populate.
#: ``group`` names the combination a case was generated from — every variant of
#: that combination carries the same value; ``kind`` is the magnitude-sense
#: marker (``"+QE"`` / ``"-QE"``) for a **single**-sense forked
#: response-spectrum combination; ``family`` distinguishes a fork from an
#: envelope pair (``"single"`` / ``"fork"`` / ``"envelope"`` / ``"path"`` /
#: ``"srss"``); ``coords`` is the variant's coordinate tuple joined with ``"|"``
#: (``"+RSX|-RSY"``, ``"max"``, ``"min"``) — the stable identity a multi-fork
#: renderer groups and labels by.  A field is written **only** when a caller
#: supplies it, so an archive annotating just ``group``/``kind`` stays
#: byte-identical to one written before the richer fields existed.
CASE_META_KEYS: dict[str, str] = {
    "group": "static_case_group",
    "kind": "static_case_kind",
    "family": "static_case_family",
    "coords": "static_case_coords",
}


def case_meta_arrays(
    case_labels: t.Sequence[str], case_meta: t.Optional[t.Mapping[str, t.Mapping[str, str]]]
) -> dict[str, np.ndarray]:
    """Build the optional per-case metadata arrays (``static_case_*``).

    A combination that mixes a response-spectrum **magnitude** with signed
    gravity/wind terms is emitted twice — the two senses the earthquake can act
    in — and both variants are needed to read the result.  Recording which cases
    came from the same combination (:attr:`CASE_META_KEYS` ``group``, plus the
    ``family`` / ``coords`` that say whether the group is a ±fork or a max/min
    envelope pair) lets a reader pair them from data instead of parsing the
    ``"<combo> #1"`` / ``"<combo> #2"`` label convention that
    :func:`~fea_toolkit.model.load_combinations.generate_combination_results`
    happens to emit.

    Args:
        case_labels: Case names, in the order written to ``static_case_labels``.
        case_meta: ``{case: {field: str}}`` where *field* is one of
            :attr:`CASE_META_KEYS`, or ``None``.  A case absent from the
            mapping, or a key absent from its entry, yields ``""`` for the
            fields that *are* written.  A field no entry mentions is not
            written at all — so an unannotated archive, or one annotating only
            ``group``/``kind``, is byte-identical to one written before the
            richer fields existed.

    Returns:
        ``{array_key: ndarray}`` for the fields present in *case_meta*, aligned
        to *case_labels* — or ``{}`` when *case_meta* is empty, so callers can
        ``arrays.update(...)`` unconditionally.

    See Also:
        :func:`~fea_toolkit.model.load_combinations.combination_case_meta` for
        the package-side producer,
        ``docs/force_diagram_unification.md`` → *Two-sided (envelope) results*.
    """
    if not case_meta:
        return {}
    provided = {
        field for entry in case_meta.values() if isinstance(entry, t.Mapping) for field in entry
    }
    out: dict[str, np.ndarray] = {}
    for field, array_key in CASE_META_KEYS.items():
        if field not in provided:
            continue
        out[array_key] = np.array(
            [str((case_meta.get(case) or {}).get(field, "")) for case in case_labels],
            dtype=str,
        )
    return out


def make_pushover_key(direction: str, template: str) -> str:
    """Build the NPZ key for a pushover result array, e.g. ``pushover/+X/step``.

    Args:
        direction: Push direction label, e.g. ``"+X"``, ``"+Y"``.
        template: Key template containing ``{direction}``.

    Returns:
        Formatted array name.
    """
    return template.replace("{direction}", direction)


def validate_arrays(data: t.Mapping[str, t.Any]) -> list[str]:
    """Validate a flat array mapping against the schema.

    This is the shared validation core behind :func:`validate_npz` — it
    accepts any ``{key: np.ndarray}`` mapping (a loaded NPZ file, or a
    plain dict in tests / in-process consumers).

    Returns a list of error/warning messages (empty = fully valid).
    """
    messages: list[str] = []

    # ── Resolve dimensions from present arrays ──────────────────────
    dims: dict[str, int] = {}
    tag_arr = data.get("node_tag")
    if tag_arr is not None:
        dims["N_node"] = len(tag_arr)
    for key in ("frame_eid", "frame_sap_id"):
        arr = data.get(key)
        if arr is not None:
            dims["N_frame"] = len(arr)
            break
    for key in ("shell_eid", "shell_sap_id"):
        arr = data.get(key)
        if arr is not None:
            dims["N_shell"] = len(arr)
            break
    for key in ("modal/period", "modal/frequency"):
        arr = data.get(key)
        if arr is not None:
            dims["N_mode"] = len(arr)
            break
    at = data.get("analysis_types")
    if at is not None:
        dims["N_analysis"] = len(at)

    def _check_shape(arr_name: str, arr, shape_desc: str, dtype_str: str):
        """Validate shape and dtype of a single array."""
        if arr is None:
            return
        expected = shape_desc.strip()
        actual_shape = arr.shape
        if expected:
            parts = expected.split()
            resolved_parts = []
            unresolved: list[str] = []
            for p in parts:
                part = p.strip()
                if part.startswith("N_") and part in dims:
                    resolved_parts.append(str(dims[part]))
                elif part.startswith("N_"):
                    unresolved.append(part)
                else:
                    resolved_parts.append(part)
            for u in unresolved:
                messages.append(f"  Missing dimension {u} for array {arr_name}")
            if unresolved:
                return
            expected_shape = tuple(int(x) for x in resolved_parts if x)
            # An expected shape of (0,) is tolerated only when the
            # actual shape is exactly (0,) as well — any non-empty
            # one-dimensional actual array (e.g. (5,)) is still a
            # mismatch.  All other shape combinations use exact
            # tuple comparison.
            if (
                not (expected_shape == (0,) and actual_shape == (0,))
                and expected_shape != actual_shape
            ):
                messages.append(
                    f"  Shape mismatch for {arr_name}: "
                    f"expected {expected_shape}, got {actual_shape}"
                )

    # ── Check geometry ─────────────────────────────────────────────
    optional_geo = {
        "frame_t_start",
        "frame_t_end",
        "frame_parent_node_i",
        "frame_parent_node_j",
        "shell_parent_sap_id",
        # Ragged shell form is accepted as an alternative to the
        # fixed-quad ``shell_node_1..4`` layout (see
        # ``io/_serial.py::collect_geometry_arrays``).
        "shell_elem_tag",
        "shell_thickness",
        "shell_node_ids_flat",
        "shell_node_offsets",
    }
    # Ragged connectivity replaces the fixed-quad schema — require the
    # flat/offsets pair when present and skip the quad arrays.
    if data.get("shell_node_ids_flat") is not None:
        if data.get("shell_node_offsets") is None or len(data["shell_node_offsets"]) == 0:
            messages.append("Missing geometry array: shell_node_offsets")
        optional_geo |= {"shell_node_1", "shell_node_2", "shell_node_3", "shell_node_4"}
    for key, (shape_desc, dtype_str) in GEOMETRY_ARRAYS.items():
        arr = data.get(key)
        if arr is None:
            if key not in optional_geo:
                messages.append(f"Missing geometry array: {key}")
            continue
        _check_shape(key, arr, shape_desc, dtype_str)

    # ── Check analysis types discriminator ─────────────────────────
    analysis_types = data.get("analysis_types")
    if analysis_types is None:
        messages.append("Missing metadata array: analysis_types")
    else:
        types = list(analysis_types)
        if "static" in types:
            case_labels = data.get("static_case_labels")
            if case_labels is None:
                messages.append(
                    "analysis_types declares 'static' but static_case_labels is missing"
                )
            else:
                # Nodal displacements are N_node-sized; element forces are
                # N_frame-sized.  Checking every static array against
                # N_frame produced false shape mismatches on any model where
                # the node and frame counts differ (the common case).
                required_static = [(a, "N_node") for a in STATIC_NODAL_ARRAYS] + [
                    (a, "N_frame") for a in STATIC_FORCE_ARRAYS
                ]
                for case in case_labels:
                    for arr_name, dim in required_static:
                        key = make_static_key(str(case), arr_name)
                        arr = data.get(key)
                        if arr is None:
                            messages.append(f"Missing static array: {key}")
                        else:
                            _check_shape(key, arr, dim, "float")
                    # Optional: explicit ``*_local`` alias arrays — present
                    # only when a producer recorded them (see
                    # STATIC_LOCAL_FORCE_ARRAYS).
                    for arr_name in STATIC_LOCAL_FORCE_ARRAYS:
                        key = make_static_key(str(case), arr_name)
                        arr = data.get(key)
                        if arr is not None:
                            _check_shape(key, arr, "N_frame", "float")
        if "modal" in types:
            # modal/node_tag is an optional row-alignment convenience
            # (added 2026-08 for the NPZ mode-plotting path), and the
            # rotational ratios (rx/ry/rz) post-date the original modal
            # schema — legacy modal NPZ files predating them must still
            # validate, so all four are exempt from the required-array
            # check.
            _optional_modal = {
                "modal/node_tag",
                "modal/rx_ratio",
                "modal/ry_ratio",
                "modal/rz_ratio",
            }
            for key, (shape_desc, dtype_str) in MODAL_ARRAYS.items():
                arr = data.get(key)
                if arr is None:
                    if key not in _optional_modal:
                        messages.append(f"Missing modal array: {key}")
                    continue
                _check_shape(key, arr, shape_desc, dtype_str)
        if "rs" in types:
            # Element-level and nodal CQC arrays are optional: they are
            # written only when the producer supplies ``rs_element_forces``
            # / ``rs_nodal_displacements``.  The review exports the nodal
            # block but does not compute element-level RS forces; a
            # per-mode/combined-scalars-only producer writes neither.
            # ``collect_rs_arrays()`` always writes the remaining keys.
            _optional_rs = {k for k in RS_ARRAYS if k.startswith(("rs/elem_", "rs/node_"))}
            for key, (shape_desc, dtype_str) in RS_ARRAYS.items():
                arr = data.get(key)
                if arr is None:
                    if key not in _optional_rs:
                        messages.append(f"Missing RS array: {key}")
                    continue
                _check_shape(key, arr, shape_desc, dtype_str)
        if "pushover" in types:
            # Detect directions from arrays present
            directions: set[str] = set()
            for key in data:
                if key.startswith("pushover/") and "/step" in key:
                    parts = key.split("/")
                    if len(parts) >= 2:
                        directions.add(parts[1])
            for direction in sorted(directions):
                # ── Resolve N_step / N_recorded_* per direction ──
                # Clear the shared dims first so each direction is
                # validated only against its own arrays — stale dims
                # from a previous direction must not leak through.
                for dk in ("N_step", "N_recorded_frame", "N_recorded_shell"):
                    dims.pop(dk, None)
                step_arr = data.get(make_pushover_key(direction, "pushover/{direction}/step"))
                if step_arr is not None:
                    dims["N_step"] = len(step_arr)
                frame_id_arr = data.get(
                    make_pushover_key(direction, "pushover/{direction}/frame_sap_id")
                )
                if frame_id_arr is not None:
                    dims["N_recorded_frame"] = len(frame_id_arr)
                shell_id_arr = data.get(
                    make_pushover_key(direction, "pushover/{direction}/shell_sap_id")
                )
                if shell_id_arr is not None:
                    dims["N_recorded_shell"] = len(shell_id_arr)

                # ── Required: global arrays ──
                for template, (shape_desc, dtype_str) in PUSHOVER_GLOBAL_ARRAYS.items():
                    key = make_pushover_key(direction, template)
                    arr = data.get(key)
                    if arr is None:
                        messages.append(f"Missing pushover array: {key}")
                        continue
                    _check_shape(key, arr, shape_desc, dtype_str)

                # ── Optional: node-disp arrays (only present when
                #    displacement data was recorded — see has_disp in
                #    ``_collect_pushover``) ──
                for template, (shape_desc, dtype_str) in PUSHOVER_NODE_DISP_ARRAYS.items():
                    key = make_pushover_key(direction, template)
                    arr = data.get(key)
                    if arr is None:
                        continue  # not recorded for this model
                    _check_shape(key, arr, shape_desc, dtype_str)

                # ── Optional: frame + shell arrays ──
                for schema_set in (PUSHOVER_FRAME_ARRAYS, PUSHOVER_SHELL_ARRAYS):
                    for template, (shape_desc, dtype_str) in schema_set.items():
                        key = make_pushover_key(direction, template)
                        arr = data.get(key)
                        if arr is None:
                            continue  # not recorded for this model
                        _check_shape(key, arr, shape_desc, dtype_str)

    return messages


def validate_npz(path: str) -> list[str]:
    """Validate an NPZ file against the schema.

    Loads the archive and delegates to :func:`validate_arrays`, which
    checks that required geometry and analysis arrays exist and that
    their shapes match the declared dimensions.  Dtype mismatches are
    reported as warnings (prefixed with ``[WARN]``) but do not block
    validation.

    Returns a list of error/warning messages (empty = fully valid).
    """
    data = None
    try:
        data = np.load(path, allow_pickle=False)
    except Exception as exc:
        return [f"Cannot load: {exc}"]

    try:
        return validate_arrays(data)
    except Exception as exc:
        return [f"Error accessing NPZ data: {exc}"]
    finally:
        if data is not None:
            data.close()
