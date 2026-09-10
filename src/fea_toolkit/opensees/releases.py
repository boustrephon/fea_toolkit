"""Member end release / partial-fixity planning (pure data, no OpenSees).

Shared by :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
(domain creation) and the Tcl export paths
(:mod:`fea_toolkit.opensees.recorder` / :mod:`fea_toolkit.opensees.builder`)
so the release topology and stiffnesses are identical in both.

A SAP2000 frame end release frees selected **local** DOFs at a member end;
partial fixity replaces a freed DOF with a semi-rigid spring.  Each released
end is modelled with the OpenSees "extra node + ``zeroLength``" pattern
(M. Scott, OpenSeesDigital 2022)::

    structural_node_i → zeroLength(release) → {eid}_rel_i … member

``P, V2, V3, T, M2, M3`` map 1:1 onto OpenSees ``zeroLength`` local DOFs
``1..6``.
"""

import warnings
from typing import Optional

import numpy as np

from ..model.geometry import get_local_axes
from ..model.sap_data import FRAME_RELEASE_DOF_LABELS

__all__ = [
    "DEFAULT_RIGIDITY_FACTOR",
    "DEFAULT_SOFTNESS_FACTOR",
    "RELEASE_DOF_LABELS",
    "emit_release_tcl",
    "member_end_stiffness",
    "plan_releases",
]

#: Local DOF labels in SAP2000 / OpenSees order (local DOF = index + 1).
RELEASE_DOF_LABELS = FRAME_RELEASE_DOF_LABELS

#: Rigidity factor for retained (non-released) DOFs (× member stiffness).
#: Canonical default — imported by ``AnalysisBuilder._set_defaults()`` and
#: used as the fallback during release element creation.  Never re-declare
#: this value elsewhere.
DEFAULT_RIGIDITY_FACTOR = 100.0

#: Softness factor for fully released DOFs (× member stiffness).  Non-zero so
#: an otherwise-floating released DOF (e.g. a pinned base) stays non-singular.
#: Canonical default — imported by ``AnalysisBuilder._set_defaults()``.
#: Never re-declare this value elsewhere.
DEFAULT_SOFTNESS_FACTOR = 1e-6


def member_end_stiffness(sec, mat, length: float) -> Optional[list]:
    """Member end stiffness for the six local DOFs (unit-consistent).

    Args:
        sec: Section (``A``, ``I22``, ``I33``, ``J``) or ``None``.
        mat: Material (``E_mod``, ``G_mod``) or ``None``.
        length: Member length in model units.

    Returns:
        ``[k_axial, k_shear, k_shear, k_torsion, k_bend_y, k_bend_z]`` in
        local-DOF order, or ``None`` when section / material data are
        insufficient to derive the values.
    """
    E = float(getattr(mat, "E_mod", 0.0) or 0.0) if mat is not None else 0.0
    A = float(getattr(sec, "A", 0.0) or 0.0) if sec is not None else 0.0
    if E <= 0.0 or A <= 0.0 or length <= 0.0:
        return None
    G = float(getattr(mat, "G_mod", 0.0) or 0.0) if mat is not None else 0.0
    I22 = float(getattr(sec, "I22", 0.0) or 0.0) if sec is not None else 0.0
    I33 = float(getattr(sec, "I33", 0.0) or 0.0) if sec is not None else 0.0
    J = float(getattr(sec, "J", 0.0) or 0.0) if sec is not None else 0.0
    if G <= 0.0:
        G = E / 2.6  # ν ≈ 0.3 fallback

    k_axial = E * A / length
    k_shear = G * A / length
    k_torsion = G * J / length if J > 0.0 else k_axial
    k_bend_y = E * I22 / length if I22 > 0.0 else k_axial
    k_bend_z = E * I33 / length if I33 > 0.0 else k_axial
    return [k_axial, k_shear, k_shear, k_torsion, k_bend_y, k_bend_z]


def plan_releases(model, config: Optional[dict] = None) -> dict:
    """Compute the release node / element plan for a model.

    Args:
        model: A ``MeshModel`` or ``SAPModelData`` (must expose ``nodes``,
            ``frame_elements``, ``frame_assignments``, ``sections``,
            ``materials`` and ``frame_releases``).
        config: Builder config (``apply_releases``, ``hinge_model``,
            ``release_rigidity_factor``, ``release_softness_factor``).

    Returns:
        Dict with keys:

        ``release_nodes``
            ``[{"node_id", "node_tag", "x", "y", "z"}, …]``.
        ``ends``
            ``[{"frame_id", "end", "node_id", "struct_node_tag", "orient",
            "dofs"}, …]`` where ``dofs`` is a list of ``(dir, stiffness)``
            pairs covering every DOF that is not an exact zero release.
        ``endpoints``
            ``{frame_id: (node_i_id, node_j_id)}`` with released ends
            re-pointed to the release-node ids.

    Releases that cannot be honoured — insufficient section/material data,
    unresolvable member local axes, or all six DOFs released at an end (a
    mechanism) — emit a :class:`UserWarning` and are omitted from the plan.
    """
    config = config or {}
    releases = getattr(model, "frame_releases", None) or {}
    empty = {"release_nodes": [], "ends": [], "endpoints": {}}
    if not releases or not config.get("apply_releases", True):
        return empty
    if config.get("hinge_model") == "lumped":
        return empty

    eta = float(config.get("release_rigidity_factor", DEFAULT_RIGIDITY_FACTOR))
    softness = float(config.get("release_softness_factor", DEFAULT_SOFTNESS_FACTOR))

    elements = model.frame_elements
    assignments = getattr(model, "frame_assignments", None) or {}
    nodes = model.nodes
    sections = getattr(model, "sections", {}) or {}
    materials = getattr(model, "materials", {}) or {}
    next_node_tag = max((nd.node_tag for nd in nodes.values()), default=0) + 1

    release_nodes: list = []
    ends: list = []
    endpoints: dict = {}

    for eid, release in releases.items():
        elem = elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            continue
        sec_name = assignments.get(eid)
        sec = sections.get(sec_name) if sec_name else None
        mat = materials.get(sec.material) if (sec is not None and sec.material) else None
        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        dx, dy, dz = nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
        length = float(np.sqrt(dx * dx + dy * dy + dz * dz))
        if length < 1e-12:
            continue
        member_k = member_end_stiffness(sec, mat, length)
        if member_k is None:
            warnings.warn(
                f"frame {eid}: release skipped — insufficient section/material "
                "data to derive the release stiffness.",
                stacklevel=2,
            )
            continue
        try:
            vx, vy, _vz = get_local_axes(np.array([dx, dy, dz]), getattr(elem, "angle", 0.0))
            orient = (
                float(vx[0]),
                float(vx[1]),
                float(vx[2]),
                float(vy[0]),
                float(vy[1]),
                float(vy[2]),
            )
        except Exception:
            # Without -orient the zeroLength acts on GLOBAL DOFs, which would
            # free the wrong component.  Surface it rather than degrade
            # silently.
            warnings.warn(
                f"frame {eid}: could not resolve the member local axes — the "
                "release zeroLength will act on global DOFs, not local ones.",
                stacklevel=2,
            )
            orient = None

        new_i, new_j = elem.node_i, elem.node_j
        for end, node, flags, springs in (
            ("I", ni, release.end_i, release.end_i_k),
            ("J", nj, release.end_j, release.end_j_k),
        ):
            if not any(flags):
                continue
            dofs: list = []
            for dof in range(6):
                if not flags[dof]:
                    dofs.append((dof + 1, eta * member_k[dof]))
                elif springs[dof]:
                    dofs.append((dof + 1, float(springs[dof])))
                elif softness > 0.0:
                    dofs.append((dof + 1, softness * member_k[dof]))
                # else: exact zero release — DOF omitted (may be singular).
            if not dofs:
                warnings.warn(
                    f"frame {eid}: all six DOFs released at end {end} — "
                    "release skipped (mechanism).",
                    stacklevel=2,
                )
                continue
            node_id = f"{eid}_rel_{end.lower()}"
            release_nodes.append(
                {
                    "node_id": node_id,
                    "node_tag": next_node_tag,
                    "x": node.x,
                    "y": node.y,
                    "z": node.z,
                }
            )
            next_node_tag += 1
            ends.append(
                {
                    "frame_id": eid,
                    "end": end,
                    "node_id": node_id,
                    "struct_node_tag": node.node_tag,
                    "orient": orient,
                    "dofs": dofs,
                }
            )
            if end == "I":
                new_i = node_id
            else:
                new_j = node_id
        endpoints[eid] = (new_i, new_j)

    return {"release_nodes": release_nodes, "ends": ends, "endpoints": endpoints}


def emit_release_tcl(plan: dict, start_elem_tag: int, start_mat_tag: int) -> list:
    """Render the release ``zeroLength`` block as Tcl lines.

    Shared by :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`
    and :func:`~fea_toolkit.opensees.builder.export_model_to_tcl` so both Tcl
    exports emit identical release blocks (and agree with the OpenSeesPy
    domain built by
    :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder._create_member_releases`,
    which consumes the same :func:`plan_releases` output).

    Args:
        plan: Output of :func:`plan_releases`.
        start_elem_tag: First element tag to use for the ``zeroLength``
            elements (the caller is responsible for avoiding collisions with
            frame / rigid-link element tags).
        start_mat_tag: First material tag to use for the ``Elastic`` release
            springs.

    Returns:
        Tcl lines — a blank line and section header first.  Empty when the
        plan contains no released ends.
    """
    ends = plan.get("ends") or []
    if not ends:
        return []

    node_tags = {rn["node_id"]: rn["node_tag"] for rn in plan.get("release_nodes", [])}
    lines: list = ["", "# ── Member end releases / partial fixity ──"]
    elem_tag = start_elem_tag
    mat_tag = start_mat_tag
    cache: dict = {}
    for rel in ends:
        dirs: list = []
        mats: list = []
        for dof, stiffness in rel["dofs"]:
            key = round(stiffness, 6)
            tag = cache.get(key)
            if tag is None:
                tag = mat_tag
                mat_tag += 1
                lines.append(f"uniaxialMaterial Elastic {tag} {stiffness:.10g}")
                cache[key] = tag
            dirs.append(dof)
            mats.append(tag)
        cmd = (
            f"element zeroLength {elem_tag} {rel['struct_node_tag']} "
            f"{node_tags[rel['node_id']]} -mat {' '.join(str(m) for m in mats)} "
            f"-dir {' '.join(str(d) for d in dirs)}"
        )
        if rel["orient"]:
            cmd += " -orient " + " ".join("0" if not v else f"{v:.10g}" for v in rel["orient"])
        lines.append(cmd)
        elem_tag += 1
    return lines
