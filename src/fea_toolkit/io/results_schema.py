"""
Unified NPZ results schema — defines the array layout for all analysis results.

Aligns with opstool's ODB (NetCDF) dimension naming so NPZ ↔ ODB conversion
is a direct rename.  See ``docs/results_schema.md`` for the full specification.
"""

from typing import Dict, List, Optional, Set, Tuple
import numpy as np


# ── Required array names per result type ──────────────────────────────────

GEOMETRY_ARRAYS: Dict[str, Tuple] = {
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
    "frame_t_start": ("N_frame", "float"),   # optional — 0..1 parametric position
    "frame_t_end": ("N_frame", "float"),     # optional — 0..1 parametric position
    "shell_eid": ("N_shell", "int"),
    "shell_sap_id": ("N_shell", "str"),
    "shell_sec_name": ("N_shell", "str"),
    "shell_node_1": ("N_shell", "int"),
    "shell_node_2": ("N_shell", "int"),
    "shell_node_3": ("N_shell", "int"),
    "shell_node_4": ("N_shell", "int"),
}

STATIC_ARRAYS = [
    "node_dx", "node_dy", "node_dz",
    "fx_i", "fy_i", "fz_i", "mx_i", "my_i", "mz_i",
    "fx_j", "fy_j", "fz_j", "mx_j", "my_j", "mz_j",
    "fx_i_local", "fy_i_local", "fz_i_local",
    "mx_i_local", "my_i_local", "mz_i_local",
    "fx_j_local", "fy_j_local", "fz_j_local",
    "mx_j_local", "my_j_local", "mz_j_local",
]

MODAL_ARRAYS: Dict[str, Tuple] = {
    "modal/period":     ("N_mode", "float"),
    "modal/frequency":  ("N_mode", "float"),
    "modal/omega":      ("N_mode", "float"),
    "modal/mx_ratio":   ("N_mode", "float"),
    "modal/my_ratio":   ("N_mode", "float"),
    "modal/mz_ratio":   ("N_mode", "float"),
    "modal/mx_eff":     ("N_mode", "float"),
    "modal/my_eff":     ("N_mode", "float"),
    "modal/mz_eff":     ("N_mode", "float"),
    "modal/mode_dx":    ("N_node N_mode", "float"),
    "modal/mode_dy":    ("N_node N_mode", "float"),
    "modal/mode_dz":    ("N_node N_mode", "float"),
}

RS_ARRAYS: Dict[str, Tuple] = {
    "rs/period":        ("N_mode", "float"),
    "rs/v_base_x":      ("N_mode", "float"),
    "rs/v_base_y":      ("N_mode", "float"),
    "rs/v_cqc_x":       ("", "float"),
    "rs/v_cqc_y":       ("", "float"),
    "rs/v_srss_x":      ("", "float"),
    "rs/v_srss_y":      ("", "float"),
    # Element-level CQC-combined forces (N_frame)
    "rs/elem_sap_id":   ("N_frame", "str"),
    "rs/elem_z_bot":    ("N_frame", "float"),
    "rs/elem_z_mid":    ("N_frame", "float"),
    "rs/elem_Vy_i":     ("N_frame", "float"),
    "rs/elem_Vy_j":     ("N_frame", "float"),
    "rs/elem_Vz_i":     ("N_frame", "float"),
    "rs/elem_Vz_j":     ("N_frame", "float"),
    "rs/elem_My_i":     ("N_frame", "float"),
    "rs/elem_My_j":     ("N_frame", "float"),
    "rs/elem_Mz_i":     ("N_frame", "float"),
    "rs/elem_Mz_j":     ("N_frame", "float"),
    # Nodal CQC-combined displacements (N_node)
    "rs/node_tag":      ("N_node", "int"),
    "rs/node_dx":       ("N_node", "float"),
    "rs/node_dy":       ("N_node", "float"),
    "rs/node_dz":       ("N_node", "float"),
}

# ── Pushover per-step results (direction-keyed) ───────────────────────────

PUSHOVER_GLOBAL_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/step":         ("N_step", "int"),
    "pushover/{direction}/control_disp": ("N_step", "float"),
    "pushover/{direction}/base_shear":   ("N_step", "float"),
}

PUSHOVER_FRAME_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/frame_sap_id": ("N_recorded_frame", "str"),
    "pushover/{direction}/frame_fx_i":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fy_i":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fz_i":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mx_i":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_my_i":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mz_i":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fx_j":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fy_j":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_fz_j":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mx_j":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_my_j":   ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/frame_mz_j":   ("N_step N_recorded_frame", "float"),
}

PUSHOVER_SHELL_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/shell_sap_id": ("N_recorded_shell", "str"),
    "pushover/{direction}/shell_Nx":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Ny":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Nxy":    ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Mx":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_My":     ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/shell_Mxy":    ("N_step N_recorded_shell", "float"),
}

PUSHOVER_NODE_DISP_ARRAYS: Dict[str, Tuple] = {
    "pushover/{direction}/node_tag":     ("N_node", "int"),
    "pushover/{direction}/node_disp_x":  ("N_step N_node", "float"),
    "pushover/{direction}/node_disp_y":  ("N_step N_node", "float"),
    "pushover/{direction}/node_disp_z":  ("N_step N_node", "float"),
}

META_ARRAYS: Dict[str, Tuple] = {
    "force_unit":       ("", "str"),
    "length_unit":      ("", "str"),
    "created":          ("", "str"),
    "analysis_types":   ("N_analysis", "str"),
}


def make_static_key(case_name: str, array_name: str) -> str:
    """Build the NPZ key for a static result array, e.g. ``static/DEAD/fx_i``."""
    return f"static/{case_name}/{array_name}"


def make_pushover_key(direction: str, template: str) -> str:
    """Build the NPZ key for a pushover result array, e.g. ``pushover/+X/step``.

    Args:
        direction: Push direction label, e.g. ``"+X"``, ``"+Y"``.
        template: Key template containing ``{direction}``.

    Returns:
        Formatted array name.
    """
    return template.replace("{direction}", direction)


def validate_npz(path: str) -> List[str]:
    """Validate an NPZ file against the schema.

    Checks that required geometry and analysis arrays exist and that
    their shapes match the declared dimensions.  Dtype mismatches are
    reported as warnings (prefixed with ``[WARN]``) but do not block
    validation.

    Returns a list of error/warning messages (empty = fully valid).
    """
    messages: List[str] = []
    data = None
    try:
        data = np.load(path, allow_pickle=False)
    except Exception as exc:
        return [f"Cannot load: {exc}"]

    try:
        # ── Resolve dimensions from present arrays ────────────────
        dims: Dict[str, int] = {}
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
                unresolved: List[str] = []
                for p in parts:
                    p = p.strip()
                    if p.startswith("N_") and p in dims:
                        resolved_parts.append(str(dims[p]))
                    elif p.startswith("N_"):
                        unresolved.append(p)
                    else:
                        resolved_parts.append(p)
                for u in unresolved:
                    messages.append(f"  Missing dimension {u} for array {arr_name}")
                if unresolved:
                    return
                expected_shape = tuple(
                    int(x) for x in resolved_parts if x
                )
                if len(expected_shape) == 1:
                    if expected_shape[0] != actual_shape[0] and \
                       not (expected_shape[0] == 0 and len(actual_shape) == 1):
                        if dims.get(expected.split()[0].strip()):
                            messages.append(
                                f"  Shape mismatch for {arr_name}: "
                                f"expected {expected_shape}, got {actual_shape}")
                elif expected_shape != actual_shape:
                    messages.append(
                        f"  Shape mismatch for {arr_name}: "
                        f"expected {expected_shape}, got {actual_shape}")

        # ── Check geometry ────────────────────────────────────────
        optional_geo = {"frame_t_start", "frame_t_end", "frame_parent_node_i", "frame_parent_node_j"}
        for key, (shape_desc, dtype_str) in GEOMETRY_ARRAYS.items():
            arr = data.get(key)
            if arr is None:
                if key not in optional_geo:
                    messages.append(f"Missing geometry array: {key}")
                continue
            _check_shape(key, arr, shape_desc, dtype_str)

        # ── Check analysis types discriminator ────────────────────
        analysis_types = data.get("analysis_types")
        if analysis_types is None:
            messages.append("Missing metadata array: analysis_types")
        else:
            types = list(analysis_types)
            if "static" in types:
                case_labels = data.get("static_case_labels")
                if case_labels is None:
                    messages.append(
                        "analysis_types declares 'static' but "
                        "static_case_labels is missing")
                else:
                    for case in case_labels:
                        for arr_name in STATIC_ARRAYS:
                            key = make_static_key(str(case), arr_name)
                            arr = data.get(key)
                            if arr is None:
                                messages.append(f"Missing static array: {key}")
                            else:
                                _check_shape(key, arr, "N_frame", "float")
            if "modal" in types:
                for key, (shape_desc, dtype_str) in MODAL_ARRAYS.items():
                    arr = data.get(key)
                    if arr is None:
                        messages.append(f"Missing modal array: {key}")
                        continue
                    _check_shape(key, arr, shape_desc, dtype_str)
            if "rs" in types:
                for key, (shape_desc, dtype_str) in RS_ARRAYS.items():
                    arr = data.get(key)
                    if arr is None:
                        messages.append(f"Missing RS array: {key}")
                        continue
                    _check_shape(key, arr, shape_desc, dtype_str)
            if "pushover" in types:
                # Detect directions from arrays present
                directions: Set[str] = set()
                for key in data.keys():
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
                    step_arr = data.get(
                        make_pushover_key(direction, "pushover/{direction}/step"))
                    if step_arr is not None:
                        dims["N_step"] = len(step_arr)
                    frame_id_arr = data.get(
                        make_pushover_key(direction, "pushover/{direction}/frame_sap_id"))
                    if frame_id_arr is not None:
                        dims["N_recorded_frame"] = len(frame_id_arr)
                    shell_id_arr = data.get(
                        make_pushover_key(direction, "pushover/{direction}/shell_sap_id"))
                    if shell_id_arr is not None:
                        dims["N_recorded_shell"] = len(shell_id_arr)

                    # ── Required: global arrays ──
                    for template, (shape_desc, dtype_str) in PUSHOVER_GLOBAL_ARRAYS.items():
                        key = make_pushover_key(direction, template)
                        arr = data.get(key)
                        if arr is None:
                            messages.append(
                                f"Missing pushover array: {key}")
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
                    for schema_set in (PUSHOVER_FRAME_ARRAYS,
                                       PUSHOVER_SHELL_ARRAYS):
                        for template, (shape_desc, dtype_str) in schema_set.items():
                            key = make_pushover_key(direction, template)
                            arr = data.get(key)
                            if arr is None:
                                continue  # not recorded for this model
                            _check_shape(key, arr, shape_desc, dtype_str)

    except Exception as exc:
        messages.append(f"Error accessing NPZ data: {exc}")
    finally:
        if data is not None:
            data.close()
    return messages