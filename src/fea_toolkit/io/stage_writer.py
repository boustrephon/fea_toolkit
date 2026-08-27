"""Stage-file writer — export model stages (SAP / mesh) with full attributes.

Writes a single NPZ or HDF5 file that is **self-describing**:

* ``stage/sap/model_json`` and ``stage/mesh/model_json`` — the complete
  ``SAPModelData`` / ``MeshModel`` payloads (codec-encoded), enabling a
  lossless ``read_model_stages()`` round-trip (``==`` equality) and
  re-analysis via :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`.
* ``stage/<name>/*_json`` dictionary blocks — sections, materials,
  groups, element types, element properties, nD materials, layered shell
  sections, units, config — so the file is a self-contained deliverable
  even without the codec path.
* ``stage/<name>/geometry`` — lightweight per-element arrays (nodes,
  frames, shells incl. **ragged** shell connectivity) that consumers
  (Rhino, PyVista) can read without decoding the model JSON.

Both formats are written with identical key names so
:func:`fea_toolkit.io.npz_reader.read_results` and the stage reader
expose one flat, format-independent view.

Usage::

    from fea_toolkit.io.stage_writer import write_model_stages

    write_model_stages(\"model.h5\", sap=md, mesh=mesh, config=config,
                       static_results=static, fmt=\"h5\")
"""

from __future__ import annotations

import datetime
import json
import typing as t
from pathlib import Path

import numpy as np

from ..model.sap_data import SAPModelData
from ..utils import force_unit_label, length_unit_label
from ._serial import _write_h5, _write_npz, collect_geometry_arrays
from .model_codec import model_to_json

# ═══════════════════════════════════════════════════════════════════
# Dictionary blocks + metadata
# ═══════════════════════════════════════════════════════════════════


def _json_block(payload: t.Any) -> np.ndarray:
    return np.array([json.dumps(payload, sort_keys=True, separators=(",", ":"))], dtype=str)


def _dataclass_fields(obj: t.Any) -> dict[str, t.Any]:
    """JSON-safe field dump of a dataclass instance (no discriminator)."""
    from .model_codec import model_to_dict

    d = model_to_dict(obj)
    d.pop("__type__", None)
    return d


def _section_dict(sec: t.Any) -> dict[str, t.Any]:
    """Section dict with a ``type`` discriminator and shape id."""
    out = _dataclass_fields(sec)
    out["type"] = type(sec).__name__
    out["shape_id"] = sec.shape_id if hasattr(sec, "shape_id") else "GEN"
    return out


def collect_dictionary_arrays(model: t.Any) -> dict[str, np.ndarray]:
    """Collect self-describing dictionary blocks from a model.

    Only *active* elements are included in the element-type /
    element-property blocks so consumers can rely on them matching the
    geometry arrays.
    """
    arrays: dict[str, np.ndarray] = {}
    m = model

    arrays["sections_json"] = _json_block(
        {name: _section_dict(sec) for name, sec in getattr(m, "sections", {}).items()}
    )
    arrays["materials_json"] = _json_block(
        {name: _dataclass_fields(mat) for name, mat in getattr(m, "materials", {}).items()}
    )
    arrays["groups_json"] = _json_block(
        {name: _dataclass_fields(grp) for name, grp in getattr(m, "groups", {}).items()}
    )
    arrays["restraints_json"] = _json_block(
        {name: _dataclass_fields(r) for name, r in getattr(m, "restraints", {}).items()}
    )
    arrays["units_json"] = _json_block(dict(getattr(m, "units", {})))
    arrays["model_name"] = np.array([str(getattr(m, "model_name", ""))], dtype=str)

    active_frames = {
        eid: elem
        for eid, elem in getattr(m, "frame_elements", {}).items()
        if not getattr(elem, "inactive", False)
    }
    active_areas = {
        aid: area
        for aid, area in getattr(m, "area_elements", {}).items()
        if not getattr(area, "inactive", False)
    }
    arrays["frame_element_types_json"] = _json_block(
        {str(k): v for k, v in getattr(m, "frame_element_types", {}).items() if k in active_frames}
    )
    arrays["area_element_types_json"] = _json_block(
        {str(k): v for k, v in getattr(m, "area_element_types", {}).items() if k in active_areas}
    )
    arrays["frame_element_properties_json"] = _json_block(
        {
            str(k): _dataclass_fields(v)
            for k, v in getattr(m, "frame_element_properties", {}).items()
            if k in active_frames
        }
    )
    arrays["area_element_properties_json"] = _json_block(
        {
            str(k): _dataclass_fields(v)
            for k, v in getattr(m, "area_element_properties", {}).items()
            if k in active_areas
        }
    )
    arrays["nd_materials_json"] = _json_block(
        {name: _dataclass_fields(v) for name, v in getattr(m, "nd_materials", {}).items()}
    )
    arrays["layered_shell_sections_json"] = _json_block(
        {name: _dataclass_fields(v) for name, v in getattr(m, "layered_shell_sections", {}).items()}
    )
    return arrays


def build_metadata(
    *,
    sap: t.Optional[SAPModelData] = None,
    mesh: t.Optional[t.Any] = None,
    config: t.Optional[dict] = None,
    static_results: t.Optional[dict] = None,
    modal_result: t.Optional[dict] = None,
    pushover_results: t.Optional[dict] = None,
    force_unit: t.Optional[str] = None,
    length_unit: t.Optional[str] = None,
    forces_coordinate_system: str = "local",
    source_file: t.Optional[str] = None,
) -> dict[str, t.Any]:
    """Build the file-level provenance metadata dict."""
    from .. import __version__

    model = mesh if mesh is not None else sap
    units = getattr(model, "units", {}) if model is not None else {}
    meta: dict[str, t.Any] = {
        "created": datetime.datetime.now().isoformat(),
        "toolkit_version": __version__,
        "source_file": str(source_file) if source_file else "",
        "model_name": getattr(model, "model_name", "") if model is not None else "",
        "force_unit": force_unit or force_unit_label(units),
        "length_unit": length_unit or length_unit_label(units),
        "units": dict(units),
        "forces_coordinate_system": forces_coordinate_system,
        "stages": {},
    }
    if sap is not None:
        meta["stages"]["sap"] = {
            "nodes": len(sap.nodes),
            "frames": len(sap.frame_elements),
            "areas": len(sap.area_elements),
        }
    if mesh is not None:
        meta["stages"]["mesh"] = {
            "nodes": len(mesh.nodes),
            "frames": len(
                [e for e in mesh.frame_elements.values() if not getattr(e, "inactive", False)]
            ),
            "areas": len(
                [a for a in mesh.area_elements.values() if not getattr(a, "inactive", False)]
            ),
            "walls": len(getattr(mesh, "wall_elements", {})),
        }
    if config:
        meta["config"] = {k: v for k, v in config.items() if isinstance(v, (str, int, float, bool))}
    if static_results:
        meta["static_cases"] = list(static_results.keys())
    if modal_result:
        meta["num_modes"] = len(modal_result.get("periods", []))
    if pushover_results:
        meta["pushover_directions"] = list(pushover_results.keys())
    return meta


def _stage_arrays(
    model: t.Any,
    *,
    geometry: bool,
    dictionaries: bool,
    model_json: bool,
) -> dict[str, np.ndarray]:
    """Collect all arrays for one stage."""
    arrays: dict[str, np.ndarray] = {}
    if model_json:
        arrays["model_json"] = np.array([model_to_json(model)], dtype=str)
    if dictionaries:
        arrays.update(collect_dictionary_arrays(model))
    if geometry:
        arrays.update(collect_geometry_arrays(model, ragged=True))
    return arrays


# ═══════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════


def write_model_stages(
    path: str,
    *,
    sap: t.Optional[SAPModelData] = None,
    mesh: t.Optional[t.Any] = None,
    config: t.Optional[dict] = None,
    static_results: t.Optional[dict] = None,
    modal_result: t.Optional[dict] = None,
    mode_shapes: t.Optional[dict] = None,
    pushover_results: t.Optional[dict] = None,
    fmt: str = "npz",
    geometry: bool = True,
    dictionaries: bool = True,
    model_json: bool = True,
    force_unit: t.Optional[str] = None,
    length_unit: t.Optional[str] = None,
    forces_coordinate_system: str = "local",
    source_file: t.Optional[str] = None,
) -> str:
    """Write model stages (``sap`` / ``mesh``) plus optional results to a
    single NPZ or HDF5 file.

    Args:
        path: Output file path (``.npz`` or ``.h5``).
        sap: ``SAPModelData`` (stage ``sap``).  At least one of *sap* /
            *mesh* must be provided.
        mesh: ``MeshModel`` from :func:`~fea_toolkit.opensees.preprocessor.preprocess_model`
            (stage ``mesh``).
        config: Builder/Preprocessor config dict — stored in the file
            metadata so the model can be re-analysed after a round-trip.
        static_results: Static analysis results dict (same shape accepted
            by :func:`fea_toolkit.io.unified_writer.write_results`).
        modal_result: Modal analysis results dict.
        mode_shapes: Mode-shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``
            — written under ``modal/mode_dx`` … ``modal/mode_dz`` (required
            for modal deformed-shape overlays in Rhino).
        pushover_results: Pushover results per direction, as
            ``{direction: (step_results, results)}`` where *step_results*
            is the builder's ``pushover_step_results`` list and *results*
            the direction result dict (provides the global
            ``step`` / ``control_disp`` / ``base_shear`` arrays).  Arrays
            are written under ``pushover/{direction}/...`` via
            :func:`fea_toolkit.io.npz_writer.collect_pushover_arrays`.
        fmt: ``\"npz\"`` (default) or ``\"h5\"``.
        geometry: Write the lightweight geometry arrays (Rhino / PyVista
            fast path).
        dictionaries: Write the self-describing dictionary blocks
            (sections, materials, ...).
        model_json: Write the full codec payload enabling the lossless
            :func:`fea_toolkit.io.stage_reader.read_model_stages`
            round-trip.  Set to ``False`` to shrink the file if
            round-tripping is not needed.
        force_unit: Force-unit label for metadata.  ``None`` derives it
            from the model units.
        length_unit: Length-unit label for metadata.  ``None`` derives it
            from the model units.
        forces_coordinate_system: Coordinate system of recorded frame
            end-forces (``\"local\"``).
        source_file: Optional source path recorded for provenance.

    Returns:
        Absolute path to the written file.

    Raises:
        ValueError: If neither *sap* nor *mesh* is provided, or *fmt* is
            not ``\"npz\"`` / ``\"h5\"``.
    """
    if sap is None and mesh is None:
        raise ValueError("write_model_stages requires at least one of 'sap' or 'mesh'")
    if fmt not in ("npz", "h5"):
        raise ValueError(f"Unsupported format '{fmt}'; expected 'npz' or 'h5'")

    from .results_schema import SCHEMA_VERSION

    arrays: dict[str, np.ndarray] = {}

    # ── File-level provenance ─────────────────────────────────────
    meta = build_metadata(
        sap=sap,
        mesh=mesh,
        config=config,
        static_results=static_results,
        modal_result=modal_result,
        pushover_results=pushover_results,
        force_unit=force_unit,
        length_unit=length_unit,
        forces_coordinate_system=forces_coordinate_system,
        source_file=source_file,
    )
    arrays["schema_version"] = np.array([SCHEMA_VERSION], dtype=int)
    arrays["metadata_json"] = np.array(
        [json.dumps(meta, sort_keys=True, separators=(",", ":"))], dtype=str
    )

    # ── Stages ────────────────────────────────────────────────────
    if sap is not None:
        arrays.update(
            {
                f"stage/sap/{k}": v
                for k, v in _stage_arrays(
                    sap, geometry=geometry, dictionaries=dictionaries, model_json=model_json
                ).items()
            }
        )
    if mesh is not None:
        arrays.update(
            {
                f"stage/mesh/{k}": v
                for k, v in _stage_arrays(
                    mesh, geometry=geometry, dictionaries=dictionaries, model_json=model_json
                ).items()
            }
        )

    # ── Results ───────────────────────────────────────────────────
    analysis_types: list[str] = []
    if static_results:
        analysis_types.append("static")
        from .unified_writer import collect_static_arrays

        arrays.update(collect_static_arrays(static_results))
    if modal_result:
        analysis_types.append("modal")
        from .npz_writer import _collect_modal

        arrays.update(_collect_modal(modal_result, mode_shapes=mode_shapes))
    if pushover_results:
        analysis_types.append("pushover")
        from .npz_writer import collect_pushover_arrays

        for direction, (step_results, po_results) in pushover_results.items():
            if step_results:
                arrays.update(
                    collect_pushover_arrays(
                        mesh,
                        step_results,
                        direction=direction,
                        pushover_results=po_results,
                    )
                )
    if analysis_types:
        arrays["analysis_types"] = np.array(analysis_types, dtype=str)

    path = str(Path(path).resolve())
    if fmt == "h5":
        _write_h5(path, arrays)
    else:
        _write_npz(path, arrays)
    return path
