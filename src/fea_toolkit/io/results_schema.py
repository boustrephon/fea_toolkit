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
#:
#: What each version means to a consumer:
#:
#: * ``1`` (:data:`SCHEMA_VERSION_LEGACY`) — written before the marker
#:   existed; infer the layout from the arrays that are present.
#: * ``2`` — the self-describing unified layout: one array dictionary for the
#:   model-stage file and the plain results archives, with the optional
#:   per-case combination metadata (:data:`CASE_META_KEYS`).
#: * ``3`` — the case metadata is **identity, not label**.  The
#:   ``static_case_kind`` array is no longer written (it only restated
#:   ``family`` plus one signed coordinate), case names are display labels
#:   derived from ``coords`` (``"<combo> [+RSX]"``, not ``"<combo> #1"``), and
#:   a reader groups from metadata only.  A ``2`` archive still reads — its
#:   cases group by ``group`` / ``coords`` where those arrays are present —
#:   but an archive carrying no ``static_case_*`` arrays at all now reads as
#:   one group per case instead of being paired by the ``#1`` / ``#2`` name
#:   convention, which is the incompatibility this version marks.  The
#:   stage-file array layout is unchanged; the marker is shared with it by
#:   design (see ``docs/model_stage_file.md``).
SCHEMA_VERSION = 3

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
    "frame_root_sap_id": ("N_frame", "str"),
    "frame_sec_name": ("N_frame", "str"),
    "frame_node_i": ("N_frame", "int"),
    "frame_node_j": ("N_frame", "int"),
    "frame_parent_node_i": ("N_frame", "int"),
    "frame_parent_node_j": ("N_frame", "int"),
    "frame_root_node_i": ("N_frame", "int"),  # optional — ultimate root endpoint
    "frame_root_node_j": ("N_frame", "int"),  # optional — ultimate root endpoint
    "frame_t_start": ("N_frame", "float"),  # optional — 0..1 parametric position
    "frame_t_end": ("N_frame", "float"),  # optional — 0..1 parametric position
    "shell_eid": ("N_shell", "int"),
    "shell_sap_id": ("N_shell", "str"),
    "shell_parent_sap_id": ("N_shell", "str"),
    "shell_root_sap_id": ("N_shell", "str"),
    "shell_sec_name": ("N_shell", "str"),
    "shell_node_1": ("N_shell", "int"),
    "shell_node_2": ("N_shell", "int"),
    "shell_node_3": ("N_shell", "int"),
    "shell_node_4": ("N_shell", "int"),
    "shell_root_node_1": ("N_shell", "int"),  # optional — ultimate root corner
    "shell_root_node_2": ("N_shell", "int"),  # optional — ultimate root corner
    "shell_root_node_3": ("N_shell", "int"),  # optional — ultimate root corner
    "shell_root_node_4": ("N_shell", "int"),  # optional — ultimate root corner
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

#: Shell (area) stress/force resultants — membrane + bending per unit width,
#: sized ``N_shell_result`` (the number of shells a static case actually
#: recorded, which may be fewer than the geometry's ``N_shell`` when areas are
#: loads-only or inactive).  The block is self-describing: ``shell_sap_id``
#: indexes it against the geometry's own ``shell_sap_id``, exactly like the
#: legacy global ``shell_forces/*`` block but **per case**
#: (``static/<case>/shell_*``).  The component names match
#: :meth:`~fea_toolkit.opensees._runner_static.AnalysisBuilder.extract_static_shell_forces`
#: (``fx``/``fy``/``fxy`` are ``Nx``/``Ny``/``Nxy``; ``mx``/``my``/``mxy`` are
#: ``Mx``/``My``/``Mxy``).
STATIC_SHELL_COMPONENT_ARRAYS = [
    "shell_fx",
    "shell_fy",
    "shell_fxy",
    "shell_mx",
    "shell_my",
    "shell_mxy",
]

#: The full per-case shell block: the component arrays above plus the index
#: arrays that keep it aligned with the geometry (``shell_sap_id``,
#: ``shell_elem_tag``, ``shell_sec_name``).
STATIC_SHELL_ARRAYS = [
    "shell_sap_id",
    "shell_elem_tag",
    "shell_sec_name",
    *STATIC_SHELL_COMPONENT_ARRAYS,
]

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
    # Shell (area) combined stress resultants — OPTIONAL: written only when a
    # producer supplies ``rs_shell_forces``.  Self-indexed by
    # ``rs/shell_sap_id`` (a run records only its active shells, which may be
    # fewer than the geometry's ``N_shell`` when areas are loads-only or
    # inactive), so the block is sized ``N_rs_shell`` — resolved in
    # ``validate_arrays`` from the length of ``rs/shell_sap_id``.
    # Component names match the static per-case ``shell_*`` block
    # (``fx``/``fy``/``fxy`` are Nx/Ny/Nxy; ``mx``/``my``/``mxy`` are
    # Mx/My/Mxy), combined across modes with the rule named by
    # ``rs/shell_combination``.
    "rs/shell_sap_id": ("N_rs_shell", "str"),
    "rs/shell_combination": ("", "str"),
    "rs/shell_direction": ("", "str"),
    "rs/shell_fx": ("N_rs_shell", "float"),
    "rs/shell_fy": ("N_rs_shell", "float"),
    "rs/shell_fxy": ("N_rs_shell", "float"),
    "rs/shell_mx": ("N_rs_shell", "float"),
    "rs/shell_my": ("N_rs_shell", "float"),
    "rs/shell_mxy": ("N_rs_shell", "float"),
}

# ── Pushover per-step results (direction-keyed) ───────────────────────────

PUSHOVER_GLOBAL_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/{load_type}/step": ("N_step", "int"),
    "pushover/{direction}/{load_type}/control_disp": ("N_step", "float"),
    "pushover/{direction}/{load_type}/base_shear": ("N_step", "float"),
}

PUSHOVER_FRAME_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/{load_type}/frame_sap_id": ("N_recorded_frame", "str"),
    "pushover/{direction}/{load_type}/frame_fx_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_fy_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_fz_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_mx_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_my_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_mz_i": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_fx_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_fy_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_fz_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_mx_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_my_j": ("N_step N_recorded_frame", "float"),
    "pushover/{direction}/{load_type}/frame_mz_j": ("N_step N_recorded_frame", "float"),
}

PUSHOVER_SHELL_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/{load_type}/shell_sap_id": ("N_recorded_shell", "str"),
    "pushover/{direction}/{load_type}/shell_Nx": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/{load_type}/shell_Ny": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/{load_type}/shell_Nxy": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/{load_type}/shell_Mx": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/{load_type}/shell_My": ("N_step N_recorded_shell", "float"),
    "pushover/{direction}/{load_type}/shell_Mxy": ("N_step N_recorded_shell", "float"),
}

PUSHOVER_NODE_DISP_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/{load_type}/node_tag": ("N_node", "int"),
    "pushover/{direction}/{load_type}/node_disp_x": ("N_step N_node", "float"),
    "pushover/{direction}/{load_type}/node_disp_y": ("N_step N_node", "float"),
    "pushover/{direction}/{load_type}/node_disp_z": ("N_step N_node", "float"),
}

# Condensed section-deformation provision (§5.1): per recorded frame element,
# per integration point, per step.  The deformation vector is stored as four
# components in the OpenSees ``eleResponse(tag, 'section', n, 'deformation')``
# order ``[ε, κ_z, κ_y, φ]`` — ``torsion`` (φ) is NaN when the section is
# flexure-only and returns only three values.
PUSHOVER_SECTION_ARRAYS: dict[str, tuple] = {
    "pushover/{direction}/{load_type}/section_frame_sap_id": ("N_recorded_section", "str"),
    "pushover/{direction}/{load_type}/section_num": ("N_recorded_section", "int"),
    "pushover/{direction}/{load_type}/section_deformation_axial": (
        "N_step N_recorded_section",
        "float",
    ),
    "pushover/{direction}/{load_type}/section_deformation_ky": (
        "N_step N_recorded_section",
        "float",
    ),
    "pushover/{direction}/{load_type}/section_deformation_kz": (
        "N_step N_recorded_section",
        "float",
    ),
    "pushover/{direction}/{load_type}/section_deformation_torsion": (
        "N_step N_recorded_section",
        "float",
    ),
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
#: that combination carries the same value; ``family`` says what the variants of
#: that group vary along (``"single"`` / ``"fork"`` / ``"envelope"`` / ``"path"``
#: / ``"srss"``) and ``coords`` is a variant's position on that axis, joined with
#: ``"|"`` (``"+RSX|-RSY"``, ``"max"``, ``"SUB|DEAD"``) — the stable identity a
#: renderer groups and labels by, and the name is derived from it.  A field is
#: written **only** when a caller supplies it.
CASE_META_KEYS: dict[str, str] = {
    "group": "static_case_group",
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
    ``family`` / ``coords`` that say what the variants of that group vary along)
    lets a reader group and label them from data instead of parsing the case
    names, which are display labels derived from that same identity and nothing
    more.

    Args:
        case_labels: Case names, in the order written to ``static_case_labels``.
        case_meta: ``{case: {field: str}}`` where *field* is one of
            :attr:`CASE_META_KEYS`, or ``None``.  A case absent from the
            mapping, or a key absent from its entry, yields ``""`` for the
            fields that *are* written.  A field no entry mentions is not
            written at all.

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


def make_pushover_key(direction: str, load_type: str, template: str) -> str:
    """Build the NPZ key for a pushover result array.

    Args:
        direction: Push direction label, e.g. ``"+X"``, ``"+Y"``.
        load_type: Lateral load distribution, e.g. ``"uniform"``,
            ``"triangular"``, ``"mode1"``.
        template: Key template containing ``{direction}`` and ``{load_type}``.

    Returns:
        Formatted array name, e.g. ``pushover/+X/uniform/step``.
    """
    return template.format(direction=direction, load_type=load_type)


def pushover_load_type(data: t.Mapping[str, t.Any], direction: str) -> str:
    """Return the lateral load type recorded for *direction* in *data*.

    Falls back to ``"uniform"`` when no ``pushover/{direction}/<load_type>/step``
    array is present (e.g. a legacy single-segment key, or an archive written
    before the load-type dimension existed).  When more than one load-type
    segment is present for *direction*, the load type is ambiguous and a
    :class:`ValueError` is raised rather than silently returning the first.

    Args:
        data: Flat results dict (NPZ-style keys).
        direction: Push direction label, e.g. ``"+X"``.

    Returns:
        The load-type segment, e.g. ``"uniform"``, ``"triangular"``, ``"mode1"``.

    Raises:
        ValueError: If *direction* records more than one load type.
    """
    prefix = f"pushover/{direction}/"
    load_types: list[str] = []
    for key in data:
        if key.startswith(prefix) and key.endswith("/step"):
            parts = key.split("/")
            if len(parts) >= 4:
                load_types.append(parts[2])
    unique = sorted(set(load_types))
    if not unique:
        return "uniform"
    if len(unique) > 1:
        raise ValueError(
            f"Multiple load types recorded for pushover direction {direction!r}: "
            f"{unique}.  The load type is ambiguous."
        )
    return unique[0]


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
    rs_shell_id = data.get("rs/shell_sap_id")
    if rs_shell_id is not None:
        dims["N_rs_shell"] = len(rs_shell_id)
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
        "frame_root_node_i",
        "frame_root_node_j",
        "shell_parent_sap_id",
        "frame_root_sap_id",
        "shell_root_sap_id",
        "shell_root_node_1",
        "shell_root_node_2",
        "shell_root_node_3",
        "shell_root_node_4",
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

    # ── Root connectivity must accompany a distinct ultimate root ──
    # ``frame_root_sap_id`` / ``shell_root_sap_id`` name the ultimate split
    # root; a distinct root id (anything other than "" / "?") is only
    # meaningful together with its root-node connectivity arrays, so an
    # archive that supplies a distinct root without those arrays is rejected.
    for root_key, node_keys in (
        ("frame_root_sap_id", ("frame_root_node_i", "frame_root_node_j")),
        (
            "shell_root_sap_id",
            ("shell_root_node_1", "shell_root_node_2", "shell_root_node_3", "shell_root_node_4"),
        ),
    ):
        root_arr = data.get(root_key)
        if root_arr is None:
            continue
        # A scalar root-id value is a shape error that ``_check_shape`` has
        # already reported; iterating it here would raise TypeError instead of
        # letting ``validate_arrays`` return its shape-error messages.
        if np.ndim(root_arr) == 0:
            continue
        distinct = [str(v) not in ("", "?") for v in root_arr]
        if not any(distinct):
            continue
        for node_key in node_keys:
            node_arr = data.get(node_key)
            if node_arr is None:
                messages.append(f"{root_key} identifies a distinct root but {node_key} is missing")
                continue
            if np.ndim(node_arr) == 0:
                continue
            # Connectivity arrays carry integer node tags; a non-integer dtype
            # (e.g. a string "0") would compare unequal to the integer zero
            # placeholder below and be silently accepted as a real tag, so
            # reject it outright instead.
            if node_arr.dtype.kind not in ("i", "u"):
                messages.append(
                    f"{node_key} has non-integer dtype ({node_arr.dtype}); "
                    "root connectivity must use integer node tags"
                )
                continue
            # A distinct root id is only meaningful with real connectivity; a
            # zero node tag is the missing-connectivity placeholder written by
            # ``_collect_geometry`` and must be rejected, not accepted.
            for i, has_root in enumerate(distinct):
                if has_root and i < len(node_arr) and node_arr[i] == 0:
                    messages.append(
                        f"{root_key} row {i} identifies a distinct root but "
                        f"{node_key} is zero (missing connectivity)"
                    )

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
            _optional_rs = {
                k for k in RS_ARRAYS if k.startswith(("rs/elem_", "rs/node_", "rs/shell_"))
            }
            for key, (shape_desc, dtype_str) in RS_ARRAYS.items():
                arr = data.get(key)
                if arr is None:
                    if key not in _optional_rs:
                        messages.append(f"Missing RS array: {key}")
                    continue
                _check_shape(key, arr, shape_desc, dtype_str)
        if "pushover" in types:
            # Detect (direction, load_type) pairs from arrays present
            pairs: set[tuple[str, str]] = set()
            for key in data:
                if key.startswith("pushover/") and "/step" in key:
                    parts = key.split("/")
                    if len(parts) >= 4:
                        pairs.add((parts[1], parts[2]))
                    elif len(parts) >= 2:
                        pairs.add((parts[1], "uniform"))  # legacy single-segment key
            for direction, load_type in sorted(pairs):
                # ── Resolve N_step / N_recorded_* per direction ──
                # Clear the shared dims first so each direction is
                # validated only against its own arrays — stale dims
                # from a previous direction must not leak through.
                for dk in ("N_step", "N_recorded_frame", "N_recorded_shell", "N_recorded_section"):
                    dims.pop(dk, None)
                step_arr = data.get(
                    make_pushover_key(direction, load_type, "pushover/{direction}/{load_type}/step")
                )
                if step_arr is not None:
                    dims["N_step"] = len(step_arr)
                frame_id_arr = data.get(
                    make_pushover_key(
                        direction, load_type, "pushover/{direction}/{load_type}/frame_sap_id"
                    )
                )
                if frame_id_arr is not None:
                    dims["N_recorded_frame"] = len(frame_id_arr)
                shell_id_arr = data.get(
                    make_pushover_key(
                        direction, load_type, "pushover/{direction}/{load_type}/shell_sap_id"
                    )
                )
                if shell_id_arr is not None:
                    dims["N_recorded_shell"] = len(shell_id_arr)
                sec_frame_id_arr = data.get(
                    make_pushover_key(
                        direction,
                        load_type,
                        "pushover/{direction}/{load_type}/section_frame_sap_id",
                    )
                )
                if sec_frame_id_arr is not None:
                    dims["N_recorded_section"] = len(sec_frame_id_arr)

                # ── Required: global arrays ──
                for template, (shape_desc, dtype_str) in PUSHOVER_GLOBAL_ARRAYS.items():
                    key = make_pushover_key(direction, load_type, template)
                    arr = data.get(key)
                    if arr is None:
                        messages.append(f"Missing pushover array: {key}")
                        continue
                    _check_shape(key, arr, shape_desc, dtype_str)

                # ── Optional: node-disp arrays (only present when
                #    displacement data was recorded — see has_disp in
                #    ``_collect_pushover``) ──
                for template, (shape_desc, dtype_str) in PUSHOVER_NODE_DISP_ARRAYS.items():
                    key = make_pushover_key(direction, load_type, template)
                    arr = data.get(key)
                    if arr is None:
                        continue  # not recorded for this model
                    _check_shape(key, arr, shape_desc, dtype_str)

                # ── Optional: frame + shell arrays ──
                for schema_set in (
                    PUSHOVER_FRAME_ARRAYS,
                    PUSHOVER_SHELL_ARRAYS,
                    PUSHOVER_SECTION_ARRAYS,
                ):
                    for template, (shape_desc, dtype_str) in schema_set.items():
                        key = make_pushover_key(direction, load_type, template)
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
