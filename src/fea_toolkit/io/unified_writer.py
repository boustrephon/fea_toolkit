"""Unified results writer — export MeshModel + analysis results to NPZ or HDF5.

Builds on the existing NPZ schema (``results_schema.md``) and adds a
format-agnostic writer that works with both ``SAPModelData`` and
``MeshModel``.  HDF5 output uses h5py (optional dependency).

Usage::

    from fea_toolkit.io.unified_writer import write_results

    # NPZ (default)
    write_results("results.npz", mesh_model=mesh,
                   static_results=static, modal_result=modal)

    # HDF5 (requires h5py)
    write_results("results.h5", mesh_model=mesh,
                   static_results=static, modal_result=modal,
                   fmt="h5")
"""

import datetime
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..model.mesh_model import MeshModel
from ..utils import force_unit_label, length_unit_label
from ._serial import _write_h5, _write_npz, collect_geometry_arrays
from .results_schema import SCHEMA_VERSION, make_static_key

# ═══════════════════════════════════════════════════════════════════
# Results collection
# ═══════════════════════════════════════════════════════════════════


def collect_static_arrays(static_results: dict[str, Any]) -> dict[str, np.ndarray]:
    """Extract static analysis arrays.

    Accepts both formats:

    * **Case‑nested** (legacy builder): ``{"DEAD": {"nodal_displacements": ...,
      "element_forces": ...}}``
    * **Flat** (AnalysisBuilder): ``{"nodal_displacements": ...,
      "reactions": ...}`` (stored under case ``"1"``)
    """
    arrays: dict[str, np.ndarray] = {}

    # Detect format
    has_nested_cases = any(
        isinstance(v, dict) and ("nodal_displacements" in v or "element_forces" in v)
        for v in static_results.values()
    )

    if has_nested_cases:
        case_labels = list(static_results.keys())
        arrays["static_case_labels"] = np.array(case_labels, dtype=str)
        for case in case_labels:
            data = static_results[case]
            _collect_case_forces(arrays, case, data)
            _collect_case_displacements(arrays, case, data)
    else:
        # Flat format — treat as single unnamed case
        arrays["static_case_labels"] = np.array(["1"], dtype=str)
        _collect_case_forces(arrays, "1", static_results)
        _collect_case_displacements(arrays, "1", static_results)

    return arrays


def _collect_case_forces(arrays: dict[str, np.ndarray], case: str, data: dict[str, Any]) -> None:
    """Collect element force arrays for one static case."""
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
    for key in force_keys:
        vals = data.get(key, data.get("element_forces", {}).get(key, []))
        arrays[make_static_key(case, key)] = np.asarray(vals, dtype=float)


def _collect_case_displacements(
    arrays: dict[str, np.ndarray], case: str, data: dict[str, Any]
) -> None:
    """Collect nodal displacement arrays for one static case."""
    disp = data.get("nodal_displacements", {})
    if not disp:
        return

    def _disp_sort_key(k: str):
        try:
            return (0, int(k), k)
        except ValueError:
            return (1, 0, k)

    tags = sorted(disp.keys(), key=_disp_sort_key)
    for i, dof in enumerate(["dx", "dy", "dz"]):
        arr = np.array([disp[t][i] for t in tags], dtype=float)
        arrays[make_static_key(case, f"node_{dof}")] = arr


def collect_modal_arrays(
    modal_result: dict[str, Any],
    mode_shapes: Optional[dict] = None,
) -> dict[str, np.ndarray]:
    """Extract modal analysis arrays.

    Single source of truth for the ``modal/*`` block: :func:`write_results`
    (the model-review export path) and
    :func:`fea_toolkit.io.npz_writer.write_results_npz` both reach this
    function, so a new key is added in exactly one place.  It used to be
    duplicated verbatim in ``npz_writer._collect_modal``, and the two copies
    drifted — the rotational ratios existed in one and not the other.
    """
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
        # Rotational participating-mass ratios.  OpenSees always reports
        # these next to the translational trio (``partiMassRatiosRM*``), but
        # the writer originally copied only the three translations — it
        # mirrored the console table, which printed just %X/%Y/%Z.  The
        # result was that six-DOF participation was absent from *every*
        # archive, and the mode-shape annotation had no RX/RY/RZ row to show.
        ("partiMassRatiosRMX", "modal/rx_ratio"),
        ("partiMassRatiosRMY", "modal/ry_ratio"),
        ("partiMassRatiosRMZ", "modal/rz_ratio"),
        ("partiMassMX", "modal/mx_eff"),
        ("partiMassMY", "modal/my_eff"),
        ("partiMassMZ", "modal/mz_eff"),
    ]:
        vals = mp.get(key, [])
        padded = (list(vals) + [0.0] * n)[:n]
        arrays[npz_key] = np.array(padded, dtype=float)

    if mode_shapes is not None and n > 0:
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
            # geometry ``node_tag`` array is written in model dict order
            # (not sorted), so stand-alone visualisers (mode-shape
            # animation) must pair row i of mode_dx/y/z against this
            # explicit sorted tag list rather than the geometry field.
            arrays["modal/node_tag"] = np.array(node_tags, dtype=int)

    return arrays


def collect_rs_arrays(
    rs_x: Optional[dict] = None, rs_y: Optional[dict] = None
) -> dict[str, np.ndarray]:
    """Extract response-spectrum arrays (base shear, moment, roof displacement).

    Single source of truth for the flat ``rs/*`` block:
    :func:`write_results` (the model-review export path),
    :func:`fea_toolkit.io.npz_writer.write_results_npz` (through
    ``_collect_rs``) and :func:`fea_toolkit.io.stage_writer.write_model_stages`
    all reach this function, so a new key is added in exactly one place.  It
    used to be duplicated in ``npz_writer._collect_rs``, and the two copies
    drifted — the per-mode ``rs/sa_*`` / ``rs/eff_mass_*`` / ``rs/v_total_*``
    keys existed in one and the combined ``rs/v_srss_*`` / ``rs/m_*`` /
    ``rs/roof_disp_*`` keys in the other, so neither writer archived a
    complete block.

    ``rs/period`` is taken from the first available result dict
    (same periods apply to both X and Y directions).  Per-direction scalars
    default to 0.0 when a producer does not supply them — e.g. the scalar
    ``cqc_base_shear`` path has no moment or roof displacement.  The per-mode
    ``rs/sa_*`` / ``rs/eff_mass_*`` arrays come from the
    ``spectrum.cqc_base_shear`` payload (``spectral_accels`` /
    ``effective_masses``) and are written empty when a producer omits them.
    """
    arrays: dict[str, np.ndarray] = {}
    first = rs_x or rs_y
    if first is not None:
        periods = first.get("modal_periods", [])
        arrays["rs/period"] = np.array(periods, dtype=float)
    for direction, d_key in [("X", "x"), ("Y", "y")]:
        rs = rs_x if direction == "X" else rs_y
        if rs is None:
            continue
        # Per-mode arrays: spectral acceleration and effective mass (the
        # legacy ``_collect_rs`` pair), plus the per-mode base shear.
        arrays[f"rs/sa_{d_key}"] = np.array(rs.get("spectral_accels", []), dtype=float)
        arrays[f"rs/eff_mass_{d_key}"] = np.array(rs.get("effective_masses", []), dtype=float)
        arrays[f"rs/v_base_{d_key}"] = np.array(rs.get("modal_base_shear", []), dtype=float)
        # Combined scalars — defaulted to 0.0 so the keys are always present.
        arrays[f"rs/v_cqc_{d_key}"] = np.array([rs.get("base_shear_cqc", 0.0)])
        arrays[f"rs/v_srss_{d_key}"] = np.array([rs.get("base_shear_srss", 0.0)])
        arrays[f"rs/v_total_{d_key}"] = np.array([rs.get("base_shear_total", 0.0)])
        arrays[f"rs/m_cqc_{d_key}"] = np.array([rs.get("base_moment_cqc", 0.0)])
        arrays[f"rs/m_srss_{d_key}"] = np.array([rs.get("base_moment_srss", 0.0)])
        arrays[f"rs/roof_disp_cqc_{d_key}"] = np.array([rs.get("roof_disp_cqc", 0.0)])
        arrays[f"rs/roof_disp_srss_{d_key}"] = np.array([rs.get("roof_disp_srss", 0.0)])
    return arrays


#: Element end-force components written by
#: :func:`collect_rs_element_force_arrays`, matching the record keys produced by
#: ``AnalysisBuilder.extract_element_rs_forces``.  NPZ keys are the lower-case
#: forms (``rs/elem_fx_i`` … ``rs/elem_mz_j``), mirroring the static
#: ``fx_i`` … ``mz_j`` convention.
_RS_ELEMENT_FORCE_COMPONENTS = (
    "Fx_i",
    "Fy_i",
    "Fz_i",
    "Mx_i",
    "My_i",
    "Mz_i",
    "Fx_j",
    "Fy_j",
    "Fz_j",
    "Mx_j",
    "My_j",
    "Mz_j",
)

#: Deprecated per-element alias keys (see ``RS_ARRAYS`` in results_schema.py).
#:
#: ``Vy``/``Vz`` used to be derived from the moment gradient; they now hold the
#: local shears.  Written so the 2D RS renderer and older consumers keep
#: working.  Delete alongside the schema aliases — see
#: ``docs/deprecation_plan.md``.
_RS_ELEMENT_LEGACY_ALIASES = ("Vy_i", "Vy_j", "Vz_i", "Vz_j", "My_i", "My_j", "Mz_i", "Mz_j")

#: Canonical component each deprecated alias mirrors.  ``Vy`` / ``Vz`` were once
#: derived from the moment gradient and now hold the local shears — i.e. the
#: canonical ``Fy`` / ``Fz`` components (compare ``_RS_LEGACY_ALIASES`` in
#: ``opensees/_runner_rs.py``); ``My`` / ``Mz`` are canonical names in their
#: own right.  Reading the canonical key here means the alias arrays do not
#: depend on the producer having echoed the legacy name back onto the record.
_RS_ELEMENT_LEGACY_SOURCES: dict[str, str] = {
    alias: alias.replace("Vy", "Fy").replace("Vz", "Fz") for alias in _RS_ELEMENT_LEGACY_ALIASES
}


def collect_rs_element_force_arrays(
    rs_element_forces: Optional[dict[str, Any]] = None,
) -> dict[str, np.ndarray]:
    """Extract element-level RS force arrays (combined across modes).

    Expects *rs_element_forces* to have the structure returned by
    ``extract_element_rs_forces()``::

        {
            "element_results": [
                {"elem_id": "1", "z_bot": 0.0, "z_mid": 5.0,
                 "Fx_i": 3.0, "Fy_i": 10.0, ..., "Mz_j": -12.0},
                ...
            ],
            "combination": "cqc",
        }

    Writes ``rs/elem_sap_id``, ``rs/elem_z_bot``, ``rs/elem_z_mid``, the
    combination rule (``rs/elem_combination``) and the full local end-force set
    ``rs/elem_fx_i`` … ``rs/elem_mz_j`` — one row per element — plus the
    deprecated ``rs/elem_Vy_i`` … ``rs/elem_Mz_j`` aliases.
    """
    arrays: dict[str, np.ndarray] = {}
    if not rs_element_forces:
        return arrays

    results = rs_element_forces.get("element_results", [])
    if not results:
        return arrays

    arrays["rs/elem_sap_id"] = np.array([r["elem_id"] for r in results], dtype=str)
    arrays["rs/elem_z_bot"] = np.array([r["z_bot"] for r in results], dtype=float)
    arrays["rs/elem_z_mid"] = np.array([r["z_mid"] for r in results], dtype=float)
    arrays["rs/elem_combination"] = np.array(
        [str(rs_element_forces.get("combination") or "cqc")], dtype=str
    )
    arrays["rs/elem_direction"] = np.array(
        [str(rs_element_forces.get("direction") or "")], dtype=str
    )

    for qty in _RS_ELEMENT_FORCE_COMPONENTS:
        arrays[f"rs/elem_{qty.lower()}"] = np.array([r.get(qty, 0.0) for r in results], dtype=float)

    # ── Legacy aliases (DEPRECATED — see _RS_ELEMENT_LEGACY_ALIASES) ──
    for alias, canonical in _RS_ELEMENT_LEGACY_SOURCES.items():
        arrays[f"rs/elem_{alias}"] = np.array([r.get(canonical, 0.0) for r in results], dtype=float)

    return arrays


def collect_rs_nodal_displacement_arrays(
    rs_nodal_displacements: Optional[dict[int, tuple]] = None,
) -> dict[str, np.ndarray]:
    """Extract RS nodal displacement arrays (CQC-combined).

    Expects *rs_nodal_displacements* to have the structure returned by
    ``compute_rs_nodal_displacements()``::

        {node_tag: (dx, dy, dz), ...}

    Writes ``rs/node_tag`` and ``rs/node_dx``, ``rs/node_dy``,
    ``rs/node_dz`` arrays sorted by node tag.
    """
    arrays: dict[str, np.ndarray] = {}
    if not rs_nodal_displacements:
        return arrays

    tags = sorted(rs_nodal_displacements.keys())
    arrays["rs/node_tag"] = np.array(tags, dtype=int)
    for i, dof in enumerate(["dx", "dy", "dz"]):
        key = f"rs/node_{dof}"
        arrays[key] = np.array([rs_nodal_displacements[t][i] for t in tags], dtype=float)

    return arrays


# ═══════════════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════════════


def _build_metadata(
    model,
    static_results=None,
    modal_result=None,
    config=None,
    forces_coordinate_system="local",
    force_unit=None,
    length_unit=None,
) -> str:
    """Build JSON metadata string."""
    meta = {
        "created": datetime.datetime.now().isoformat(),
        "force_unit": force_unit or force_unit_label(getattr(model, "units", {})),
        "length_unit": length_unit or length_unit_label(getattr(model, "units", {})),
        "forces_coordinate_system": forces_coordinate_system,
        "model_name": getattr(model, "model_name", ""),
        "num_nodes": len(model.nodes),
        "num_frames": len(
            [e for e in model.frame_elements.values() if not getattr(e, "inactive", False)]
        ),
        "num_areas": len(
            [a for a in model.area_elements.values() if not getattr(a, "inactive", False)]
        ),
    }
    if static_results:
        # Use same nested/flat detection as collect_static_arrays
        has_nested_cases = any(
            isinstance(v, dict) and ("nodal_displacements" in v or "element_forces" in v)
            for v in static_results.values()
        )
        if has_nested_cases:
            meta["static_cases"] = list(static_results.keys())
            meta["has_local_forces"] = any("fx_i" in r for r in static_results.values())
        else:
            meta["static_cases"] = ["1"]
            meta["has_local_forces"] = "fx_i" in static_results or any(
                "fx_i" in v for v in static_results.values() if isinstance(v, dict)
            )
    if modal_result:
        meta["num_modes"] = len(modal_result.get("periods", []))
    if config:
        meta["config"] = {k: v for k, v in config.items() if isinstance(v, (str, int, float, bool))}
    return json.dumps(meta)


# ═══════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════


def write_results(
    path: str,
    model=None,
    mesh_model: Optional[MeshModel] = None,
    static_results: Optional[dict[str, Any]] = None,
    modal_result: Optional[dict[str, Any]] = None,
    mode_shapes: Optional[dict] = None,
    rs_results: Optional[dict[str, dict]] = None,
    rs_element_forces: Optional[dict[str, Any]] = None,
    rs_nodal_displacements: Optional[dict[int, tuple]] = None,
    pushover_results: Optional[dict[str, tuple]] = None,
    fmt: str = "npz",
    config: Optional[dict] = None,
    force_unit: Optional[str] = None,
    length_unit: Optional[str] = None,
    forces_coordinate_system: str = "local",
) -> str:
    """Write model geometry + analysis results to a unified output file.

    Accepts either a ``MeshModel`` or a ``SAPModelData`` (via the
    *model* parameter).  Supports NPZ (default) and HDF5 formats.

    Args:
        path: Output file path (``.npz`` or ``.h5``).
        model: ``SAPModelData`` or ``MeshModel`` (alternative to *mesh_model*).
        mesh_model: ``MeshModel`` (preferred — from Preprocessor).
        static_results: Dict of static analysis results keyed by case name.
        modal_result: Dict from ``run_modal_analysis()``.
        mode_shapes: Dict of mode shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``.
        rs_results: Dict with keys ``rs_x``, ``rs_y`` from ``run_rs()``.
        pushover_results: Pushover per-step results per direction as
            ``{direction: (step_results, results)}`` — written under
            ``pushover/{direction}/...`` via
            :func:`fea_toolkit.io.npz_writer.collect_pushover_arrays`
            (requires *mesh_model* for the node tags).
        fmt: ``"npz"`` (default) or ``"h5"``.
        config: Builder config dict (included in metadata).
        force_unit: Optional force-unit label override.  ``None`` derives
            it from ``model.units`` via
            :func:`fea_toolkit.utils.force_unit_label`.
        length_unit: Optional length-unit label override.  ``None`` derives
            it from ``model.units`` via
            :func:`fea_toolkit.utils.length_unit_label`.
        forces_coordinate_system: Coordinate system of the recorded frame
            end-force arrays (``"local"`` or ``"global"``).  Defaults to
            ``"local"`` — the OpenSees ``localForces`` recorder convention.

    Returns:
        Absolute path to the written file.
    """
    if forces_coordinate_system not in ("local", "global"):
        raise ValueError(
            f"forces_coordinate_system must be 'local' or 'global', "
            f"got {forces_coordinate_system!r}"
        )

    # Resolve model source (MeshModel or SAPModelData — both have .nodes)
    src = mesh_model or model
    if src is None:
        raise ValueError("Either mesh_model or model must be provided")

    # ``rs_element_forces`` / ``rs_nodal_displacements`` are supplementary
    # sub-blocks of the response-spectrum stage: they only make sense with the
    # core ``rs_results`` block (which supplies ``rs/period`` and the
    # per-direction base shears).  Rejecting the combination up front keeps the
    # ``analysis_types`` manifest honest — a force-only call must not advertise
    # an ``"rs"`` stage that has no base data behind it.
    if (rs_element_forces or rs_nodal_displacements) and not rs_results:
        raise ValueError(
            "rs_results is required when rs_element_forces or rs_nodal_displacements are provided"
        )

    # Collect all arrays
    arrays: dict[str, np.ndarray] = {}

    # Geometry
    if hasattr(src, "nodes"):
        arrays.update(collect_geometry_arrays(src))

    # Static results
    if static_results:
        arrays.update(collect_static_arrays(static_results))

    # Modal results
    if modal_result:
        arrays.update(collect_modal_arrays(modal_result, mode_shapes=mode_shapes))

    # RS results
    if rs_results:
        arrays.update(
            collect_rs_arrays(
                rs_x=rs_results.get("rs_x"),
                rs_y=rs_results.get("rs_y"),
            )
        )

    # RS element forces
    if rs_element_forces:
        arrays.update(collect_rs_element_force_arrays(rs_element_forces))

    # RS nodal displacements
    if rs_nodal_displacements:
        arrays.update(collect_rs_nodal_displacement_arrays(rs_nodal_displacements))

    # Pushover per-step results (per direction)
    analysis_types: list[str] = []
    if pushover_results and mesh_model is not None:
        from .npz_writer import collect_pushover_arrays

        for direction, (step_results, po_results) in pushover_results.items():
            if step_results:
                arrays.update(
                    collect_pushover_arrays(
                        mesh_model,
                        step_results,
                        direction=direction,
                        pushover_results=po_results,
                    )
                )
    if static_results:
        analysis_types.append("static")
    if modal_result:
        analysis_types.append("modal")
    if rs_results:
        analysis_types.append("rs")
    if pushover_results:
        analysis_types.append("pushover")
    # Always written — an empty array for a geometry-only file, matching the
    # legacy ``write_results_npz`` manifest so ``describe_results_npz`` and
    # the review's archive summary read identically from either writer.
    arrays["analysis_types"] = np.array(analysis_types, dtype=str)

    # ── Canonical unit / local-force metadata (same keys as npz_writer) ──
    # Length-1 string arrays so the unified plotting readers
    # (``_load_npz_for_plotting`` etc.) resolve units from either writer.
    _fu = force_unit or force_unit_label(getattr(src, "units", {}))
    _lu = length_unit or length_unit_label(getattr(src, "units", {}))
    arrays["force_unit"] = np.array([_fu], dtype=str)
    arrays["length_unit"] = np.array([_lu], dtype=str)
    arrays["forces_coordinate_system"] = np.array([forces_coordinate_system], dtype=str)
    # Creation timestamp — ``describe_results_npz`` reads this scalar.
    arrays["created"] = np.array([datetime.datetime.now().isoformat()], dtype=str)

    # Metadata
    arrays["metadata_json"] = np.array(
        [
            _build_metadata(
                src,
                static_results,
                modal_result,
                config,
                forces_coordinate_system,
                force_unit=force_unit,
                length_unit=length_unit,
            )
        ]
    )

    # File-level schema marker — mirrors the stage file and
    # ``write_results_npz`` so a standalone results archive is
    # self-describing.  Read back by
    # :func:`fea_toolkit.io.npz_reader.get_schema_version`.
    arrays["schema_version"] = np.array([SCHEMA_VERSION], dtype=int)

    # Write — validate fmt explicitly
    if fmt == "h5":
        _write_h5(path, arrays)
    elif fmt == "npz":
        _write_npz(path, arrays)
    else:
        raise ValueError(f"Unsupported format '{fmt}'; expected 'npz' or 'h5'")

    return str(Path(path).resolve())
