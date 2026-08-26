"""Normalise model sources for visualisation / export.

Resolves the flexible-input pattern (§3.10) to one common shape:

* a ``SAPModelData`` (stage ``sap``),
* a ``MeshModel`` (stage ``mesh`` / ``mesh_parents``),
* an ``AnalysisBuilder``,
* a dict loaded from a stage/results file (``.h5`` or ``.npz``) — the
  lightweight geometry arrays produced by
  :func:`fea_toolkit.io.stage_writer.write_model_stages`.

:func:`resolve_model_source` returns a :class:`ResolvedSource` whose
attributes duck-type the model layer (``nodes``, ``frame_elements``,
``area_elements``, ``frame_assignments``, ``area_assignments``,
``sections``, ``materials``, ``groups``, ``restraints``,
``frame_end_offsets``), so consumers such as
:mod:`fea_toolkit.rhino.geometry` can run unchanged against every
source.

This module is OpenSees-free and safe to import inside Rhino 8.
"""

from __future__ import annotations

import dataclasses
import typing as t

from ..model.mesh_model import MeshModel
from ..model.sap_data import (
    AreaElement,
    FrameElement,
    FrameEndOffset,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)


@dataclasses.dataclass
class ResolvedSource:
    """Normalised view of a model source for Rhino geometry / attributes.

    Attributes mirror the ``SAPModelData`` field names so the existing
    geometry functions keep working; NPZ/H5 sources reconstruct the
    dataclasses from the stage-file arrays on the fly.
    """

    stage: str
    nodes: dict[str, Node]
    frame_elements: dict[str, FrameElement]
    area_elements: dict[str, AreaElement]
    frame_assignments: dict[str, str]
    area_assignments: dict[str, str]
    sections: dict[str, Section]
    materials: dict[str, Material]
    groups: dict[str, t.Any] = dataclasses.field(default_factory=dict)
    restraints: dict[str, Restraint] = dataclasses.field(default_factory=dict)
    frame_end_offsets: dict[str, FrameEndOffset] = dataclasses.field(default_factory=dict)
    units: dict[str, str] = dataclasses.field(default_factory=dict)
    model_name: str = ""
    #: Stage file raw dict (when resolved from NPZ/H5), else None.
    raw: t.Optional[dict[str, t.Any]] = None


def _resolve_from_model(source: t.Any, stage: str) -> ResolvedSource:
    """Resolve from an in-memory model object (SAPModelData / MeshModel /
    AnalysisBuilder)."""
    if hasattr(source, "model") and not isinstance(source, (SAPModelData, MeshModel)):
        source = source.model  # AnalysisBuilder
    elif hasattr(source, "mesh_model"):
        source = source.mesh_model  # AnalysisBuilder (mesh_model attribute)
    nodes = {str(k): v for k, v in source.nodes.items()}

    frame_elements: dict[str, FrameElement] = {}
    frame_assignments = dict(getattr(source, "frame_assignments", {}))
    for eid, elem in source.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        frame_elements[str(eid)] = elem

    area_elements: dict[str, AreaElement] = {}
    area_assignments = dict(getattr(source, "area_assignments", {}))
    for aid, area in source.area_elements.items():
        if getattr(area, "inactive", False):
            continue
        area_elements[str(aid)] = area

    return ResolvedSource(
        stage=stage,
        nodes=nodes,
        frame_elements=frame_elements,
        area_elements=area_elements,
        frame_assignments=frame_assignments,
        area_assignments=area_assignments,
        sections=dict(getattr(source, "sections", {})),
        materials=dict(getattr(source, "materials", {})),
        groups=dict(getattr(source, "groups", {})),
        restraints=dict(getattr(source, "restraints", {})),
        frame_end_offsets=dict(getattr(source, "frame_end_offsets", {})),
        units=dict(getattr(source, "units", {})),
        model_name=str(getattr(source, "model_name", "")),
    )


def _block_lookup(data: dict[str, t.Any], stage: str, name: str) -> t.Any:
    """Fetch a stage dictionary block tolerating both key layouts:
    ``stage/<stage>/<name>`` (raw ``read_results`` dict) and
    ``<name>`` (``read_stage_arrays`` stripped dict)."""
    if name in data:
        return data[name]
    return data.get(f"stage/{stage}/{name}")


def _section_from_dict(name: str, d: dict[str, t.Any]) -> t.Optional[Section]:
    """Rebuild a section dataclass from a dictionary block.

    Uses the codec registry so polymorphic section types round-trip.
    """
    if not isinstance(d, dict):
        return None
    try:
        from ..io.model_codec import dict_to_model

        payload = dict(d)
        payload.setdefault("name", name)
        payload.setdefault("__type__", d.get("type", "Section"))
        return dict_to_model(payload, cls=Section)  # type: ignore[return-value]
    except Exception:
        return None


def _resolve_from_dict(data: dict[str, t.Any], stage: str) -> ResolvedSource:
    """Resolve from a flat stage-file dict (keys as written by
    :func:`fea_toolkit.io.stage_writer.write_model_stages`)."""
    nodes: dict[str, Node] = {}
    tags = data.get("node_tag", [])
    for i in range(len(tags)):
        nid = str(data.get("node_sap_id", [""] * len(tags))[i])
        nodes[nid] = Node(
            node_id=nid,
            node_tag=int(tags[i]),
            x=float(data["node_x"][i]),
            y=float(data["node_y"][i]),
            z=float(data["node_z"][i]),
        )

    frame_elements: dict[str, FrameElement] = {}
    frame_assignments: dict[str, str] = {}
    sap_ids = data.get("frame_sap_id", [])
    for i in range(len(sap_ids)):
        eid = str(sap_ids[i])
        frame_elements[eid] = FrameElement(
            elem_id=eid,
            elem_tag=int(data.get("frame_elem_tag", [0] * len(sap_ids))[i]),
            node_i=str(data.get("frame_node_i", [0] * len(sap_ids))[i]),
            node_j=str(data.get("frame_node_j", [0] * len(sap_ids))[i]),
            angle=float(data.get("frame_angle", [0.0] * len(sap_ids))[i]),
            cardinal_point=int(data.get("frame_cardinal_point", [10] * len(sap_ids))[i]),
        )
        frame_assignments[eid] = str(data.get("frame_sec_name", [""] * len(sap_ids))[i])

    area_elements: dict[str, AreaElement] = {}
    area_assignments: dict[str, str] = {}
    flat = data.get("shell_node_ids_flat", [])
    offsets = data.get("shell_node_offsets", [])
    shell_ids = data.get("shell_sap_id", [])
    for i in range(len(shell_ids)):
        aid = str(shell_ids[i])
        if len(offsets) > i:
            start, end = int(offsets[i]), int(offsets[i + 1])
            node_ids = [str(t) for t in flat[start:end]]
        else:
            node_ids = []
        area_elements[aid] = AreaElement(
            area_id=aid,
            area_tag=int(data.get("shell_elem_tag", [0] * len(shell_ids))[i]),
            node_ids=node_ids,
            thickness=float(data.get("shell_thickness", [0.0] * len(shell_ids))[i]),
        )
        area_assignments[aid] = str(data.get("shell_sec_name", [""] * len(shell_ids))[i])

    # ── Dictionary blocks (self-describing sections / materials) ──
    sections: dict[str, Section] = {}
    materials: dict[str, Material] = {}
    try:
        import json

        raw_sec = _block_lookup(data, stage, "sections_json")
        if raw_sec is not None and len(raw_sec):
            payload = json.loads(raw_sec[0])
            for name, d in payload.items():
                sec = _section_from_dict(name, d)
                if sec is not None:
                    sections[name] = sec
        raw_mat = _block_lookup(data, stage, "materials_json")
        if raw_mat is not None and len(raw_mat):
            payload = json.loads(raw_mat[0])
            for name, d in payload.items():
                try:
                    from ..io.model_codec import dict_to_model

                    materials[name] = dict_to_model(dict(d), cls=Material)
                except Exception:
                    continue
    except Exception:
        pass

    units: dict[str, str] = {}
    try:
        import json

        raw_units = _block_lookup(data, stage, "units_json")
        if raw_units is not None and len(raw_units):
            units = json.loads(raw_units[0])
    except Exception:
        pass

    return ResolvedSource(
        stage=stage,
        nodes=nodes,
        frame_elements=frame_elements,
        area_elements=area_elements,
        frame_assignments=frame_assignments,
        area_assignments=area_assignments,
        sections=sections,
        materials=materials,
        units=units,
        raw=data,
    )


def resolve_model_source(source: t.Any, stage: t.Optional[str] = None) -> ResolvedSource:
    """Normalise any supported source to a :class:`ResolvedSource`.

    Args:
        source: One of
            * ``SAPModelData`` (stage defaults to ``sap``),
            * ``MeshModel`` / ``AnalysisBuilder`` (stage defaults to ``mesh``),
            * a flat dict loaded from a stage file (e.g. via
              :func:`fea_toolkit.io.stage_reader.read_stage_arrays`),
            * a path to a ``.h5`` / ``.npz`` stage file.
        stage: Explicit stage name.  ``None`` is inferred from the source
            type (``sap`` for ``SAPModelData``, ``mesh`` otherwise).

    Returns:
        Normalised :class:`ResolvedSource` with active (non-inactive)
        elements only.

    Raises:
        TypeError: If the source type is not supported.
    """
    if isinstance(source, (str,)):
        from ..io.stage_reader import read_stage_arrays

        data = read_stage_arrays(source, stage or "mesh")
        return _resolve_from_dict(data, stage or "mesh")

    if isinstance(source, dict):
        return _resolve_from_dict(source, stage or "mesh")

    if isinstance(source, SAPModelData):
        return _resolve_from_model(source, stage or "sap")

    if (
        isinstance(source, (MeshModel,))
        or hasattr(source, "frame_elements")
        or hasattr(source, "mesh_model")
    ):
        return _resolve_from_model(source, stage or "mesh")

    raise TypeError(
        f"resolve_model_source: unsupported source type {type(source).__name__}; "
        "expected SAPModelData, MeshModel, AnalysisBuilder, a stage-file dict/path."
    )
