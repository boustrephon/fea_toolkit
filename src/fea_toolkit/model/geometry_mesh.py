"""Area-mesh geometry helpers.

Area meshing, frame-overlap detection, constraint-edge discovery, and
wall/slab intersection handling.  Re-exported by
:mod:`fea_toolkit.model.geometry`."""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Optional

import numpy as np

from ..model.sap_data import (
    AreaElement,
    AreaMesh,
    FrameElement,
    Group,
    JointLoad,
    Node,
    Restraint,
    SAPModelData,
)


def _point_uv_on_quad(
    pt: np.ndarray,
    corners: list[np.ndarray],
) -> Optional[tuple[float, float]]:
    """Estimate parametric (u, v) of *pt* on a bilinear quad.

    Uses Newton iteration on the bilinear surface
    ``p(u,v) = (1-v)[(1-u)c0 + u*c1] + v[(1-u)c3 + u*c2]``.

    Returns:
        ``(u, v)`` clamped to ``[0, 1]``, or ``None`` if the point is
        not on the quad (residual exceeds 1 % of the quad diagonal).
    """
    c0, c1, c2, c3 = corners
    diag = float(np.linalg.norm(c2 - c0))
    tol = max(1e-8, 0.01 * diag)
    u, v = 0.5, 0.5
    for _ in range(20):
        top = c0 * (1.0 - u) + c1 * u
        bot = c3 * (1.0 - u) + c2 * u
        p = top * (1.0 - v) + bot * v
        dp_du = (c1 - c0) * (1.0 - v) + (c2 - c3) * v
        dp_dv = bot - top
        r = p - pt
        J = np.column_stack([dp_du, dp_dv])  # (3, 2)
        JTJ = J.T @ J
        try:
            delta = np.linalg.solve(JTJ, -J.T @ r)
        except np.linalg.LinAlgError:
            break
        u = float(np.clip(u + delta[0], 0.0, 1.0))
        v = float(np.clip(v + delta[1], 0.0, 1.0))
        if float(np.linalg.norm(delta)) < 1e-8:
            break
    # Residual check: reject points that aren't actually on the quad
    top = c0 * (1.0 - u) + c1 * u
    bot = c3 * (1.0 - u) + c2 * u
    p_final = top * (1.0 - v) + bot * v
    if float(np.linalg.norm(p_final - pt)) > tol:
        return None
    return u, v


def _propagate_edge_restraints(
    node_grid: Sequence[Sequence[Optional[str]]],
    n_u: int,
    n_v: int,
    restraints: dict[str, Restraint],
) -> None:
    """Propagate edge-node restraints via bitwise-AND of adjacent corners.

    For each intermediate node on the four edges of an ``n_u×n_v`` mesh
    grid, looks up the two corner nodes at the ends of that edge in
    *restraints*.  If both corners are restrained, the intermediate node
    receives the bitwise AND of the two ``dofs`` lists, provided at least
    one DOF survives the AND (an all-zero result is not stored).  Interior nodes
    receive any DOF bit that is common to **all four** corners (the
    bitwise AND across the four corner ``dofs`` lists).  Corners are
    untouched.  Nodes that already carry an explicit restraint (for
    example, pre-existing nodes reused via coordinate deduplication) are
    never overwritten — only newly created nodes inherit the propagated
    restraints.

    Example: corner A has ``dofs=[1,0,1,0,0,1]`` and corner B has
    ``dofs=[1,1,1,1,0,0]`` — a node created between them receives
    ``[1,0,1,0,0,0]``.

    The function returns immediately when there are no restraints to
    propagate or when neither mesh direction has intermediate edge nodes
    (``n_u < 2`` **and** ``n_v < 2``).  A one-row mesh (``n_u >= 2``,
    ``n_v == 1``) is still processed: the intermediate nodes of its
    bottom and top edges inherit the corner restraints, while its
    left/right edges have no intermediate nodes.

    Args:
        node_grid: ``(n_v+1)×(n_u+1)`` grid of node IDs (str or None).
        n_u: Subdivision count in the u-direction (grid columns = n_u+1).
        n_v: Subdivision count in the v-direction (grid rows = n_v+1).
        restraints: Model restraints dict, modified in place.
    """
    if not restraints or (n_u < 2 and n_v < 2):
        return

    def _and_dofs(r1: Restraint, r2: Restraint) -> list[int]:
        return [a & b for a, b in zip(r1.dofs, r2.dofs)]

    def _apply(c1_id: Optional[str], c2_id: Optional[str], nid: Optional[str]) -> None:
        if nid is None or c1_id is None or c2_id is None:
            return
        if nid in (c1_id, c2_id):
            return
        # Nodes that already carry an explicit restraint (e.g. existing
        # nodes reused via coordinate deduplication) keep it unchanged.
        if nid in restraints:
            return
        r1 = restraints.get(c1_id)
        r2 = restraints.get(c2_id)
        if r1 is not None and r2 is not None:
            dofs = _and_dofs(r1, r2)
            # Skip all-zero intersections — a Restraint with no fixed DOF is
            # a no-op that would otherwise clutter later propagation and the
            # Tcl/OpenSees fix commands.  Matches the interior block below.
            if any(dofs):
                restraints[nid] = Restraint(dofs=dofs)

    c_bl = node_grid[0][0]  # bottom-left  (u=0, v=0)
    c_br = node_grid[0][n_u]  # bottom-right (u=n_u, v=0)
    c_tr = node_grid[n_v][n_u]  # top-right    (u=n_u, v=n_v)
    c_tl = node_grid[n_v][0]  # top-left     (u=0, v=n_v)

    # bottom edge (j=0): c_bl -- c_br
    for i in range(1, n_u):
        _apply(c_bl, c_br, node_grid[0][i])
    # top edge (j=n_v): c_tl -- c_tr
    for i in range(1, n_u):
        _apply(c_tl, c_tr, node_grid[n_v][i])
    # left edge (i=0): c_bl -- c_tl
    for j in range(1, n_v):
        _apply(c_bl, c_tl, node_grid[j][0])
    # right edge (i=n_u): c_br -- c_tr
    for j in range(1, n_v):
        _apply(c_br, c_tr, node_grid[j][n_u])

    # ── Interior nodes: DOFs common to all four corners ──────────
    r_bl = restraints.get(c_bl)
    r_br = restraints.get(c_br)
    r_tr = restraints.get(c_tr)
    r_tl = restraints.get(c_tl)
    if all(r is not None for r in (r_bl, r_br, r_tr, r_tl)):
        common = [r_bl.dofs[k] & r_br.dofs[k] & r_tr.dofs[k] & r_tl.dofs[k] for k in range(6)]
        if any(common):
            for j in range(1, n_v):
                for i in range(1, n_u):
                    nid = node_grid[j][i]
                    if nid is not None and nid not in restraints:
                        restraints[nid] = Restraint(dofs=list(common))


def mesh_area_elements(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    area_mesh: dict[str, AreaMesh],
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
    restraints: Optional[dict[str, Restraint]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide area elements into a grid of smaller shell elements.

    Only areas with ``auto_mesh=True`` and a positive ``max_size`` are
    subdivided.  The subdivision count along each edge is calculated from
    ``max_size`` so that no sub-element exceeds that dimension.

    Sub-elements inherit:
      * Section assignment from the parent area.
      * Thickness from the parent area.
      * Group membership (if *groups* is provided) — each sub-element is
        added to every group that contained the parent area ID.

    New nodes created on the perimeter edges inherit a restraint equal to
    the bitwise AND of the two corner-node restraints at the ends of that
    edge (see :func:`_propagate_edge_restraints`).  Interior nodes inherit
    any DOF bit that is common to all four corner restraints.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        area_mesh: ``{area_id: AreaMesh}`` from parsed s2k data.
        next_tag: Next available numeric tag for new nodes and elements.
        groups: Optional ``{group_name: Group}`` — group memberships are
            propagated to sub-elements.
        restraints: Optional ``{node_id: Restraint}`` — model restraints
            dict.  Edge-node restraints are propagated here (modified in
            place).

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided areas added and original areas marked inactive.
    """

    # Coordinate-based node registry for subdividing areas.
    # Populated per-area from corner nodes only, so adjacent areas'
    # shared edges are still deduplicated without collapsing
    # intentionally separate nodes at the same coordinate.
    def _coord_key(x, y, z):
        return (round(x, 6), round(y, 6), round(z, 6))

    _coord_to_id: dict[tuple, str] = {}

    # ── Pre-build vectorised cache of real SAP2000 nodes ──────────
    # Avoids a full nodes.items() scan inside each area's mesh loop.
    _cached_ids: list[str] = []
    _cached_pos: list[np.ndarray] = []
    for nid, nd in nodes.items():
        if nd.node_tag > 999999:
            continue  # internal tag, not a real SAP2000 node
        _cached_ids.append(nid)
        _cached_pos.append(np.array([nd.x, nd.y, nd.z], dtype=float))
    _cached_pos_arr = np.array(_cached_pos)  # shape (N, 3)

    for aid, mesh in area_mesh.items():
        if not mesh.auto_mesh or mesh.max_size <= 0.0:
            continue

        elem = area_elements.get(aid)
        if elem is None or len(elem.node_ids) != 4:
            continue  # only quad areas are meshed
        if getattr(elem, "inactive", False):
            continue  # already subdivided in a previous build

        # Seed registry from this area's corner nodes
        for nid in elem.node_ids:
            nd = nodes.get(nid)
            if nd is not None:
                _coord_to_id[_coord_key(nd.x, nd.y, nd.z)] = nid

        # Compute area bounding box + plane normal for seeding
        # existing interior / edge nodes.
        box_corners = []
        for nid in elem.node_ids:
            nd = nodes.get(nid)
            if nd is not None:
                box_corners.append(np.array([nd.x, nd.y, nd.z]))
        if len(box_corners) >= 3:
            box_pts = np.array(box_corners)
            # Scale-aware tolerance: fraction of max_size, with a floor
            # to keep shared-edge detection consistent across unit systems.
            _tol = max(mesh.max_size * 0.001, 0.001)
            bbox_min = box_pts.min(axis=0) - _tol
            bbox_max = box_pts.max(axis=0) + _tol
            # Cross product of two edges gives plane normal
            v1 = box_pts[1] - box_pts[0]
            v2 = box_pts[-1] - box_pts[0]
            n_plane = np.cross(v1, v2)
            n_len = np.linalg.norm(n_plane)
            if n_len > 1e-12:
                n_plane /= n_len
                # Vectorised bounding-box + plane-distance mask
                in_bbox = np.all(
                    (_cached_pos_arr >= bbox_min) & (_cached_pos_arr <= bbox_max),
                    axis=1,
                )
                near_plane = np.abs(np.dot(_cached_pos_arr - box_pts[0], n_plane)) < _tol
                for idx in np.flatnonzero(in_bbox & near_plane):
                    nid = _cached_ids[idx]
                    if nid in _coord_to_id:
                        continue  # already seeded
                    nd = nodes.get(nid)
                    if nd is not None:
                        _coord_to_id[_coord_key(nd.x, nd.y, nd.z)] = nid

        # Gather corner nodes, ensuring we have unique corners (4-node quad)
        corner_ids = [str(nid) for nid in elem.node_ids]
        if len(corner_ids) != 4 or len(set(corner_ids)) != 4:
            continue

        corners = []
        for nid in corner_ids:
            nd = nodes.get(nid)
            if nd is None:
                break
            corners.append(np.array([nd.x, nd.y, nd.z], dtype=float))
        if len(corners) != 4:
            continue

        # Normalise to CCW winding so all sub-cells have consistent
        # shell normals (OpenSees ShellMITC4 expects CCW ordering).
        # Compute the face normal using the cross product of the two
        # diagonals, then project the quad onto the plane perpendicular
        # to the dominant normal axis for a reliable 2D winding test.
        diag1 = corners[2] - corners[0]
        diag2 = corners[3] - corners[1]
        n_face = np.cross(diag1, diag2)
        abs_n = np.abs(n_face)
        dom = int(np.argmax(abs_n))  # 0=X, 1=Y, 2=Z
        # 2D signed area on the projection plane perpendicular to the
        # dominant normal axis.  Negative → clockwise → reverse.
        if dom == 0:  # project onto YZ  (ey × ez = +ex, right-handed)
            u = [c[1] for c in corners]
            v = [c[2] for c in corners]
        elif dom == 1:  # project onto ZX  (ez × ex = +ey, right-handed)
            u = [c[2] for c in corners]
            v = [c[0] for c in corners]
        else:  # project onto XY  (ex × ey = +ez, right-handed)
            u = [c[0] for c in corners]
            v = [c[1] for c in corners]
        # 2D signed area (shoelace) on the projection plane.
        # Negative → clockwise → reverse the vertex order.
        signed_2d = (
            (u[0] * v[1] - u[1] * v[0])
            + (u[1] * v[2] - u[2] * v[1])
            + (u[2] * v[3] - u[3] * v[2])
            + (u[3] * v[0] - u[0] * v[3])
        ) * 0.5
        if signed_2d < 0:  # clockwise → reverse
            corner_ids = [corner_ids[0], corner_ids[3], corner_ids[2], corner_ids[1]]
            corners = [corners[0], corners[3], corners[2], corners[1]]

        # Determine subdivision counts from max_size
        def _edge_length(a, b):
            return float(np.linalg.norm(b - a))

        l01 = _edge_length(corners[0], corners[1])
        l12 = _edge_length(corners[1], corners[2])
        l23 = _edge_length(corners[2], corners[3])
        l30 = _edge_length(corners[3], corners[0])

        # Use the longest edge in each parametric direction so that
        # no sub-element exceeds max_size, even on tapered faces.
        len_u = max(l01, l23)  # I→J direction (edge 0-1, 2-3)
        len_v = max(l12, l30)  # orthogonal direction (edge 1-2, 3-0)

        n_u = max(1, math.ceil(len_u / mesh.max_size))
        n_v = max(1, math.ceil(len_v / mesh.max_size))

        if n_u == 1 and n_v == 1:
            continue  # no subdivision needed

        # Cap subdivision to prevent memory explosion from tiny max_size.
        MAX_SUBDIVIDE = 100
        if n_u > MAX_SUBDIVIDE or n_v > MAX_SUBDIVIDE:
            raise ValueError(
                f"Area {aid}: subdivision {n_u}×{n_v} exceeds maximum "
                f"{MAX_SUBDIVIDE} (len_u={len_u:.2f}, len_v={len_v:.2f}, "
                f"max_size={mesh.max_size}). Increase max_size."
            )

        # ── Check for interior seed nodes (e.g. wall edge nodes that ──
        # ── lie inside this slab area).  When found, switch to an    ──
        # ── irregular subdivision so the mesh passes through them.   ──
        interior_seeds: list[tuple[float, float]] = []  # (u, v)
        _corner_set = set(corner_ids)
        # Reverse map from _coord_to_id to check seeded nodes
        seeded_ids = set(_coord_to_id.values())
        for nid in seeded_ids:
            if nid in _corner_set:
                continue
            nd_ref = nodes.get(nid)
            if nd_ref is None:
                continue
            pos = np.array([nd_ref.x, nd_ref.y, nd_ref.z], dtype=float)
            uv = _point_uv_on_quad(pos, corners)
            if uv is None:
                continue
            u, v = uv
            # Classify position: corner / perimeter / interior
            at_corner = (u <= 1e-6 or u >= 1.0 - 1e-6) and (v <= 1e-6 or v >= 1.0 - 1e-6)
            if at_corner:
                continue
            interior_seeds.append((u, v))

        # Fold interior seeds into the division lists
        if interior_seeds:
            # Merge near-duplicates and sort
            _tol_uv = 1e-6
            u_vals = sorted(
                set(
                    [0.0, 1.0]
                    + [round(i / n_u, 8) for i in range(n_u + 1)]
                    + [s[0] for s in interior_seeds]
                )
            )
            v_vals = sorted(
                set(
                    [0.0, 1.0]
                    + [round(j / n_v, 8) for j in range(n_v + 1)]
                    + [s[1] for s in interior_seeds]
                )
            )
            # Deduplicate near-equal values
            u_vals = [u_vals[0]] + [
                u for u in u_vals[1:] if u - u_vals[u_vals.index(u) - 1] > _tol_uv
            ]
            v_vals = [v_vals[0]] + [
                v for v in v_vals[1:] if v - v_vals[v_vals.index(v) - 1] > _tol_uv
            ]
            n_u = len(u_vals) - 1
            n_v = len(v_vals) - 1
            # Cap check
            if n_u > MAX_SUBDIVIDE or n_v > MAX_SUBDIVIDE:
                raise ValueError(
                    f"Area {aid}: irregular subdivision {n_u}×{n_v} exceeds "
                    f"maximum {MAX_SUBDIVIDE}. Reduce max_size or remove "
                    f"interior seed nodes."
                )
            use_irregular = True
        else:
            u_vals = [i / n_u for i in range(n_u + 1)]
            v_vals = [j / n_v for j in range(n_v + 1)]
            use_irregular = False

        # Bilinear interpolation to create grid points
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        if use_irregular:
            for j, v in enumerate(v_vals):
                for i, u in enumerate(u_vals):
                    top = corners[0] * (1.0 - u) + corners[1] * u
                    bot = corners[3] * (1.0 - u) + corners[2] * u
                    grid[j, i] = top * (1.0 - v) + bot * v
        else:
            for j in range(n_v + 1):
                v = j / n_v
                for i in range(n_u + 1):
                    u = i / n_u
                    top = corners[0] * (1.0 - u) + corners[1] * u
                    bot = corners[3] * (1.0 - u) + corners[2] * u
                    grid[j, i] = top * (1.0 - v) + bot * v

        # Create new nodes for interior grid points (skip corners)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                if i == 0 and j == 0:
                    node_grid[j][i] = corner_ids[0]
                    continue
                if i == n_u and j == 0:
                    node_grid[j][i] = corner_ids[1]
                    continue
                if i == n_u and j == n_v:
                    node_grid[j][i] = corner_ids[2]
                    continue
                if i == 0 and j == n_v:
                    node_grid[j][i] = corner_ids[3]
                    continue
                new_id = f"{aid}_mesh_{j}_{i}"
                pt = grid[j, i]
                # Reuse existing node at the same coordinates so
                # adjacent meshed areas share edge/interior nodes.
                ck = _coord_key(float(pt[0]), float(pt[1]), float(pt[2]))
                existing = _coord_to_id.get(ck)
                if existing is not None:
                    node_grid[j][i] = existing
                    continue
                new_tag = next_tag
                next_tag += 1
                nodes[new_id] = Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                _coord_to_id[ck] = new_id
                node_grid[j][i] = new_id

        # Mark original area as inactive
        elem.inactive = True

        # Determine which groups contain the parent area
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                ref = f"Area:{aid}"
                if ref in g.objects:
                    parent_groups.append(gname)

        # Create sub-area elements (CCW ordering: 0→1→2→3 per sub-quad)
        sec_name = area_assignments.get(aid, "")
        for j in range(n_v):
            for i in range(n_u):
                sub_id = f"{aid}_sub_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                # Quad corners in CCW order
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                area_elements[sub_id] = AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                    thickness=elem.thickness,
                    parent_id=aid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                # Propagate group membership
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

        # Propagate edge-node restraints using bitwise-AND of adjacent
        # corners (only if both corners are restrained).
        if restraints is not None:
            _propagate_edge_restraints(node_grid, n_u, n_v, restraints)

    return area_elements, area_assignments, nodes, next_tag


def subdivide_area_mesh(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    n: int,
    selection: Optional[set[str]] = None,
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
    restraints: Optional[dict[str, Restraint]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide each coarse shell quad into an N×N grid of sub-elements.

    Operates on model data (``AreaElement`` / ``Node`` dataclasses) so the
    refined mesh is visible to NPZ export, PyVista, Rhino, and diagnostics.

    Each parent area is marked ``inactive=True`` and linked to its children
    via ``parent_id`` / ``child_ids``.  Sub-elements inherit the parent's
    section assignment, thickness, and group membership.

    New interior nodes are created with coordinate-based deduplication so
    adjacent subdivided areas share edge nodes.

    New nodes created on the perimeter edges inherit a restraint equal to
    the bitwise AND of the two corner-node restraints at the ends of that
    edge (see :func:`_propagate_edge_restraints`).  Interior nodes inherit
    any DOF bit that is common to all four corner restraints.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        n: Subdivision count (2 = 2×2 grid, 3 = 3×3, etc.).  Must be ≥ 2.
        selection: Optional set of area IDs to subdivide.  ``None`` = all.
        next_tag: Next available numeric tag for new nodes and elements.
        groups: Optional ``{group_name: Group}`` — group memberships are
            propagated to sub-elements.
        restraints: Optional ``{node_id: Restraint}`` — model restraints
            dict.  Edge-node restraints are propagated here (modified in
            place).

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)``.
    """
    if n < 2:
        return area_elements, area_assignments, nodes, next_tag

    def _coord_key(x, y, z):
        return (round(x, 6), round(y, 6), round(z, 6))

    # Seed coordinate registry from active quadrilateral area corner
    # nodes only — not from every node in the model — so that
    # coincident offset/release/disconnected nodes are not spuriously
    # reused during deduplication.
    _coord_to_id: dict[tuple, str] = {}
    for aid, elem in area_elements.items():
        if getattr(elem, "inactive", False):
            continue
        if len(elem.node_ids) != 4:
            continue
        for nid in elem.node_ids:
            nd = nodes.get(nid)
            if nd is not None:
                _coord_to_id[_coord_key(nd.x, nd.y, nd.z)] = nid

    for aid, elem in list(area_elements.items()):
        if selection is not None and aid not in selection:
            continue
        if getattr(elem, "inactive", False):
            continue
        if len(elem.node_ids) != 4:
            continue

        # Gather corner coordinates
        corners = []
        corner_ids = list(elem.node_ids)
        for nid in corner_ids:
            nd = nodes.get(nid)
            if nd is None:
                break
            corners.append(np.array([nd.x, nd.y, nd.z], dtype=float))
        if len(corners) != 4:
            continue

        # Normalise winding to counter-clockwise using projected
        # signed area so child shells have consistent normals.
        e3 = corners[1] - corners[0]
        e4 = corners[3] - corners[0]
        normal = np.cross(e3, e4)
        plane_z = np.array([0.0, 0.0, 1.0])
        if np.dot(normal, plane_z) < 0.0:
            corners[2], corners[3] = corners[3], corners[2]

        # Bilinear interpolation to create (n+1)² grid points
        grid = np.zeros((n + 1, n + 1, 3))
        for j in range(n + 1):
            v = j / n
            for i in range(n + 1):
                u = i / n
                top = corners[0] * (1.0 - u) + corners[1] * u
                bot = corners[3] * (1.0 - u) + corners[2] * u
                grid[j, i] = top * (1.0 - v) + bot * v

        # Create node grid — reuse existing nodes at corners, create new
        # interior nodes with coordinate dedup
        node_grid = [[None] * (n + 1) for _ in range(n + 1)]
        for j in range(n + 1):
            for i in range(n + 1):
                if i == 0 and j == 0:
                    node_grid[j][i] = corner_ids[0]
                    continue
                if i == n and j == 0:
                    node_grid[j][i] = corner_ids[1]
                    continue
                if i == n and j == n:
                    node_grid[j][i] = corner_ids[2]
                    continue
                if i == 0 and j == n:
                    node_grid[j][i] = corner_ids[3]
                    continue
                pt = grid[j, i]
                ck = _coord_key(float(pt[0]), float(pt[1]), float(pt[2]))
                existing = _coord_to_id.get(ck)
                if existing is not None:
                    node_grid[j][i] = existing
                    continue
                new_id = f"{aid}_sub_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                nodes[new_id] = Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                _coord_to_id[ck] = new_id
                node_grid[j][i] = new_id

        # Determine which groups contain the parent area
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                ref = f"Area:{aid}"
                if ref in g.objects:
                    parent_groups.append(gname)

        # Mark parent inactive
        elem.inactive = True

        # Create n² sub-elements
        sec_name = area_assignments.get(aid, "")
        for j in range(n):
            for i in range(n):
                sub_id = f"{aid}_sub_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                area_elements[sub_id] = AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                    thickness=elem.thickness,
                    parent_id=aid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                # Propagate group membership
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

        # Propagate edge-node restraints using bitwise-AND of adjacent
        # corners (only if both corners are restrained).
        if restraints is not None:
            _propagate_edge_restraints(node_grid, n, n, restraints)

    return area_elements, area_assignments, nodes, next_tag


def split_areas_at_frame_edges(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    frame_elements: dict[str, FrameElement],
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide areas at frame-element edge nodes not at area corners.

    For each area that is not already subdivided (``inactive=False``),
    finds all frame element nodes that lie on the area's perimeter edges.
    The area is then subdivided into a grid that passes through those
    intermediate edge nodes, creating sub-elements whose corner nodes
    include the frame connection points.

    This mirrors the ``AtFrames`` splitting applied to frame elements \u2014
    it ensures that frame elements terminating on an area edge are
    connected to the shell mesh.

    Sub-elements inherit:
      * Section assignment from the parent area.
      * Thickness from the parent area.
      * Parent-child relationship (``parent_id`` / ``child_ids``).

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` \u2014 new interior nodes are added here.
        frame_elements: ``{frame_id: FrameElement}`` \u2014 used to find frame
            node locations that should connect to area edges.
        next_tag: Next available numeric tag for new nodes and elements.
        groups: Optional ``{group_name: Group}`` \u2014 group memberships are
            propagated to sub-elements.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided areas added and original areas marked inactive.
    """
    from .sap_data import AreaElement as _AreaElement
    from .sap_data import Node as _Node

    # Collect all frame node IDs
    frame_node_ids: set = set()
    for fe in frame_elements.values():
        frame_node_ids.add(fe.node_i)
        frame_node_ids.add(fe.node_j)

    # Coordinates of frame nodes (for collinearity tests)
    frame_node_coords: dict[str, np.ndarray] = {}
    # \u2500\u2500 Spatial indices for frame nodes \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # Z-band index \u2014 groups nodes by integer Z-band (efficient for
    # slabs).  Uses int(z / tol) for robust bucket keys \u2014 avoids
    # round() pitfalls where nearby values can land in different buckets.
    z_band_tol = 0.1  # 100\u202fmm band width
    frame_nodes_by_z: dict[int, list[str]] = {}
    # XY spatial grid \u2014 groups nodes by integer XY cell (efficient
    # for walls).  Cell size should be large enough that a wall\u2019s
    # bounding box overlaps at least one cell, small enough that the
    # number of candidate nodes per cell stays manageable.
    xy_grid_size = 0.5  # 500\u202fmm cell size
    frame_nodes_xy: dict[tuple[int, int], list[str]] = {}
    for nid in frame_node_ids:
        nd = nodes.get(nid)
        if nd is not None:
            arr = np.array([nd.x, nd.y, nd.z], dtype=float)
            frame_node_coords[nid] = arr
            z_key = int(nd.z / z_band_tol)
            frame_nodes_by_z.setdefault(z_key, []).append(nid)
            x_key = int(round(nd.x / xy_grid_size))
            y_key = int(round(nd.y / xy_grid_size))
            frame_nodes_xy.setdefault((x_key, y_key), []).append(nid)

    if not frame_node_coords:
        return area_elements, area_assignments, nodes, next_tag

    for aid, elem in list(area_elements.items()):
        if getattr(elem, "inactive", False):
            continue
        if elem is None or len(elem.node_ids) != 4:
            continue

        corners = list(elem.node_ids)
        corner_coords = [
            np.array([nodes[c].x, nodes[c].y, nodes[c].z], dtype=float) if c in nodes else None
            for c in corners
        ]
        if any(c is None for c in corner_coords):
            continue

        # Ensure CCW ordering (same convention as mesh_area_elements)
        c0, c1, c2, c3 = corner_coords
        u = c1 - c0
        v = c3 - c0
        normal = np.cross(u, v)
        signed = float(np.dot(normal, np.cross(c2 - c0, c3 - c0)))
        if signed < 0:
            corners = [corners[0], corners[3], corners[2], corners[1]]
            corner_coords = [corner_coords[0], corner_coords[3], corner_coords[2], corner_coords[1]]

        # Four edges: (0\u21921), (1\u21922), (2\u21923), (3\u21920)
        edges = [
            (0, 1, "u"),  # edge 0\u21921, u-direction
            (1, 2, "v"),  # edge 1\u21922, v-direction
            (2, 3, "u"),  # edge 2\u21923, u-direction (reverse)
            (3, 0, "v"),  # edge 3\u21920, v-direction (reverse)
        ]

        # Choose spatial filter based on orientation
        normal_len = float(np.linalg.norm(normal))
        nz_abs = abs(normal[2]) / normal_len if normal_len > 0 else 0.0
        # Warn if a vertical area (|nz|\u22480) has a slab-like section name
        sec_name = area_assignments.get(aid, "")
        if nz_abs < 0.1 and "slab" in sec_name.lower():
            warnings.warn(
                f'Area {aid}: section "{sec_name}" assigned to a vertical '
                f"element (|nz|={nz_abs:.3f}) \u2014 likely mis-classified in "
                f"the source model.",
                UserWarning,
                stacklevel=2,
            )
        local_frame_ids: set = set()

        if nz_abs > 0.707:
            # \u2500\u2500 Slab (near-horizontal): Z-band filter \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
            # Slabs span a single Z level, so only 1\u20132 bands need
            # checking \u2014 very efficient.
            az_min = float(min(c[2] for c in corner_coords))
            az_max = float(max(c[2] for c in corner_coords))
            z0 = int(az_min / z_band_tol)
            z1 = int(az_max / z_band_tol)
            for zb in range(z0, z1 + 1):
                local_frame_ids.update(frame_nodes_by_z.get(zb, []))
        else:
            # \u2500\u2500 Wall (near-vertical): XY spatial grid filter \u2500\u2500\u2500\u2500
            # Walls span multiple Z levels but occupy a narrow XY
            # footprint.  The XY grid limits candidate nodes to those
            # in the area\u2019s XY bounding box.
            xs = [float(c[0]) for c in corner_coords]
            ys = [float(c[1]) for c in corner_coords]
            margin = max(xy_grid_size * 1.5, 0.5)  # at least 500\u202fmm margin
            ix_min = int(round((min(xs) - margin) / xy_grid_size))
            ix_max = int(round((max(xs) + margin) / xy_grid_size))
            iy_min = int(round((min(ys) - margin) / xy_grid_size))
            iy_max = int(round((max(ys) + margin) / xy_grid_size))
            for ix in range(ix_min, ix_max + 1):
                for iy in range(iy_min, iy_max + 1):
                    local_frame_ids.update(frame_nodes_xy.get((ix, iy), []))

        edge_node_lists: dict[str, list] = {"u": [], "v": []}
        for ei, ej, direction in edges:
            p_i = corner_coords[ei]
            p_j = corner_coords[ej]
            edge_vec = p_j - p_i
            edge_len_sq = float(np.dot(edge_vec, edge_vec))
            if edge_len_sq < 1e-12:
                continue
            edge_len = float(np.sqrt(edge_len_sq))
            edge_dir = edge_vec / edge_len

            for nid in local_frame_ids:
                if nid in corners:
                    continue  # skip area corner nodes
                npos = frame_node_coords[nid]
                vec_ip = npos - p_i
                # Collinearity: cross product near zero
                cross_len = float(np.linalg.norm(np.cross(edge_vec, vec_ip)))
                if cross_len / edge_len > 1e-4:
                    continue
                # Within segment: projection t between -tol and 1+tol
                t = float(np.dot(vec_ip, edge_dir)) / edge_len
                if t < -1e-6 or t > 1 + 1e-6:
                    continue
                # Store with adjustment for reversed edges
                if direction == "u":
                    if ei == 0:  # edge 0\u21921: t as-is
                        edge_node_lists["u"].append(t)
                    else:  # edge 2\u21923: reverse \u2192 1-t
                        edge_node_lists["u"].append(1.0 - t)
                elif ei == 1:  # edge 1\u21922: t as-is
                    edge_node_lists["v"].append(t)
                else:  # edge 3\u21920: reverse \u2192 1-t
                    edge_node_lists["v"].append(1.0 - t)

        # Deduplicate and sort t-values (add 0 and 1 as implicit boundaries)
        u_vals = sorted(set([0.0, 1.0] + edge_node_lists["u"]))
        v_vals = sorted(set([0.0, 1.0] + edge_node_lists["v"]))

        # Only subdivide if there\u2019s at least one intermediate node
        if len(u_vals) <= 2 and len(v_vals) <= 2:
            continue

        # Ensure minimum spacing between t-values (merge near-duplicates)
        _tol = 1e-6
        u_vals = [u_vals[0]] + [u for u in u_vals[1:] if u - u_vals[u_vals.index(u) - 1] > _tol]
        v_vals = [v_vals[0]] + [v for v in v_vals[1:] if v - v_vals[v_vals.index(v) - 1] > _tol]

        n_u = len(u_vals) - 1  # number of sub-divisions in u-direction
        n_v = len(v_vals) - 1

        # Ensure reasonable aspect ratio \u2014 if one direction has far fewer
        # divisions than the other, subdivide the coarser direction so
        # element aspect ratios stay below max_aspect_ratio.
        max_aspect = 4.0
        if n_u > 0 and n_v > 0:
            # Estimate element sizes from edge lengths
            len_u = max(
                float(np.linalg.norm(corner_coords[1] - corner_coords[0])),
                float(np.linalg.norm(corner_coords[2] - corner_coords[3])),
            )
            len_v = max(
                float(np.linalg.norm(corner_coords[2] - corner_coords[1])),
                float(np.linalg.norm(corner_coords[3] - corner_coords[0])),
            )
            elem_u = len_u / n_u
            elem_v = len_v / n_v
            if elem_u > elem_v * max_aspect and elem_u > 0:
                # u-direction too coarse \u2014 subdivide u_vals more
                extra = int(math.ceil(elem_u / (elem_v * max_aspect))) - 1
                for k in range(1, extra + 1):
                    u_vals.append(k / (extra + 1))
                u_vals = sorted(set(u_vals))
            elif elem_v > elem_u * max_aspect and elem_v > 0:
                # v-direction too coarse \u2014 subdivide v_vals more
                extra = int(math.ceil(elem_v / (elem_u * max_aspect))) - 1
                for k in range(1, extra + 1):
                    v_vals.append(k / (extra + 1))
                v_vals = sorted(set(v_vals))
            n_u = len(u_vals) - 1
            n_v = len(v_vals) - 1

        # Bilinear grid at parametric positions
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        for j, v in enumerate(v_vals):
            for i, u in enumerate(u_vals):
                top = corner_coords[0] * (1 - u) + corner_coords[1] * u
                bot = corner_coords[3] * (1 - u) + corner_coords[2] * u
                grid[j, i] = top * (1 - v) + bot * v

        # Build spatial coordinate cache once (reuse across grid points)
        _pos_cache_np: dict[str, np.ndarray] = {
            nid: np.array([nd.x, nd.y, nd.z], dtype=float) for nid, nd in nodes.items()
        }
        # Merge frame node coords into cache
        for nid, npos in frame_node_coords.items():
            _pos_cache_np[nid] = npos
        # Create nodes for grid points (reusing frame nodes at same coords)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                pt = grid[j, i]
                # Check spatial cache for existing node at this position
                found = None
                for nid, npos in _pos_cache_np.items():
                    if np.linalg.norm(npos - pt) < 1e-4:
                        found = nid
                        break
                if found is not None:
                    node_grid[j][i] = found
                    continue
                # Create new node
                new_id = f"{aid}_af_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                nd = _Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                nodes[new_id] = nd
                _pos_cache_np[new_id] = np.array([pt[0], pt[1], pt[2]], dtype=float)
                node_grid[j][i] = new_id

        # Mark original as inactive and record parent-child
        elem.inactive = True
        elem.child_ids = []

        # Determine parent groups
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                ref = f"Area:{aid}"
                if ref in g.objects:
                    parent_groups.append(gname)

        sec_name = area_assignments.get(aid, "")
        for j in range(n_v):
            for i in range(n_u):
                sub_id = f"{aid}_af_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                # Deduplicate collinear collapse
                deduped = []
                for n in (n0, n1, n2, n3):
                    if not deduped or n != deduped[-1]:
                        deduped.append(n)
                if len(deduped) < 3:
                    continue
                area_elements[sub_id] = _AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=deduped,
                    thickness=getattr(elem, "thickness", 0.0),
                    inactive=False,
                    parent_id=aid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

    return area_elements, area_assignments, nodes, next_tag


def warn_frame_overlaps(
    frame_elements: dict[str, FrameElement],
    frame_assignments: Optional[dict[str, str]],
    nodes: dict[str, Node],
    *,
    prefix: str = "",
    exclude_types: frozenset = frozenset({"brick wall"}),
) -> None:
    """Warn about overlapping or collinear frame elements.

    Builds a frame-only edge registry and checks for:
    * identical overlaps (same end nodes),
    * partial overlaps (collinear edges sharing a node).

    Overlapping duplicate members should be fixed in the source model.

    Note:
        This uses a lightweight check rather than the full sweep-line
        algorithm from :func:`find_constraint_edges` because simple
        sub-segment overlaps (e.g. a beam spanning half a longer beam)
        don't create chain junctions for the sweep to detect.

    Args:
        frame_elements: ``{frame_id: FrameElement}``.
        frame_assignments: ``{frame_id: section_name}`` or ``None``.
        nodes: ``{node_id: Node}``.
        prefix: Optional prefix for warning messages.
        exclude_types: Section name patterns to skip (case-insensitive
            substring match).
    """

    COSINE_TOL = 0.9999

    assign = frame_assignments or {}
    _node_arr = {nid: np.array([nd.x, nd.y, nd.z], dtype=float) for nid, nd in nodes.items()}

    def _is_excluded(name: str) -> bool:
        name_lower = name.lower()
        return any(excl.lower() in name_lower for excl in exclude_types)

    def _pos_key(nid: str) -> tuple:
        nd = nodes[nid]
        return (nd.x, nd.y, nd.z, nid)

    def _cos_match(d1, d2):
        return float(np.dot(d1, d2)) > COSINE_TOL

    def _chain_dir(key, from_node):
        """Unit vector from from_node toward the other end of key."""
        other = key[1] if key[0] == from_node else key[0]
        vec = _node_arr[other] - _node_arr[from_node]
        nrm = float(np.linalg.norm(vec))
        return vec / nrm if nrm > 1e-12 else None

    # Build frame-only edge registry
    frame_reg: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fid, felem in frame_elements.items():
        if getattr(felem, "inactive", False) or felem is None:
            continue
        if _is_excluded(assign.get(fid, "")):
            continue
        nA, nB = felem.node_i, felem.node_j
        if nA == nB:
            continue
        key = (nA, nB) if _pos_key(nA) <= _pos_key(nB) else (nB, nA)
        frame_reg[key].append(fid)

    # ── 1. Identical overlaps (same key, 2+ frame elements) ────
    for key, fids in frame_reg.items():
        if len(fids) >= 2:
            names = {fid: assign.get(fid, "?") for fid in fids}
            warnings.warn(
                f"{prefix}Overlapping frame elements on edge "
                f"{key[0]}\u2192{key[1]}: {names}.  "
                f"Consolidate in the source model.",
                stacklevel=2,
            )

    # ── 2. Partial overlaps (different keys, collinear, share
    #      a node, different lengths) ────────────────────────────
    node_edges: dict[str, list[tuple]] = defaultdict(list)
    for (nA, nB), fids in frame_reg.items():
        fid = fids[0]
        ndA, ndB = _node_arr[nA], _node_arr[nB]
        length = float(np.linalg.norm(ndB - ndA))
        node_edges[nA].append(((nA, nB), fid, length))
        node_edges[nB].append(((nA, nB), fid, length))

    checked: set = set()
    for _node, incident in node_edges.items():
        if len(incident) < 2:
            continue
        for i in range(len(incident)):
            ki, fidi, leni = incident[i]
            for j in range(i + 1, len(incident)):
                kj, fidj, lenj = incident[j]
                pair = frozenset({(ki, fidi), (kj, fidj)})
                if pair in checked:
                    continue
                checked.add(pair)
                d_i = _chain_dir(ki, _node)
                d_j = _chain_dir(kj, _node)
                if d_i is None or d_j is None:
                    continue
                if not _cos_match(d_i, d_j):
                    continue
                if abs(leni - lenj) > 1e-9:
                    ni = assign.get(fidi, "?")
                    nj = assign.get(fidj, "?")
                    warnings.warn(
                        f"{prefix}Overlapping collinear frame elements: "
                        f"{fidi} ({ni}) on {ki[0]}\u2192{ki[1]} "
                        f"and {fidj} ({nj}) on {kj[0]}\u2192{kj[1]}.  "
                        f"Consolidate in the source model.",
                        stacklevel=2,
                    )


def find_constraint_edges(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    frame_elements: Optional[dict[str, FrameElement]] = None,
    frame_assignments: Optional[dict[str, str]] = None,
    exclude_types: frozenset = frozenset({"brick"}),
    verbose: bool = True,
) -> list[tuple[list[str], list[tuple], list[tuple], str, str]]:
    """Find tears in the final mesh via sweep-line chain following.

    Detects locations where two (or more) adjacent elements share a
    geometric edge but have incompatible meshes — one chain has intermediate
    nodes the other doesn't.  These need line/edge constraints in OpenSees.

    Supports both area elements (2D shells) and frame elements (1D beams).
    A beam running along a meshed slab edge will be detected: the slab's
    sub-elements create intermediate mesh nodes on the shared edge while
    the single beam element spans the full length.

    .. note::
       Frame-element edges are only compared against area-element edges.
       Frame-vs-frame overlaps are **skipped** — they indicate modelling
       errors (duplicate/collinear members) and should be resolved in the
       source model rather than patched with constraints.  Use
       :func:`warn_frame_overlaps` to detect them separately.

    Uses a sorted-tuple edge registry where each key ``(nA, nB)`` is
    ordered by node position (X → Y → Z), making the direction implicit:
    nA → nB is the positive direction.  Compatible edges (same key with
    2+ elements) are identified automatically because they produce the
    identical sorted tuple.

    Non-structural element types (e.g. brick infill walls used as load
    panels) are excluded via ``exclude_types`` using case-insensitive
    substring matching (e.g. ``{'brick'}`` matches ``'Brick'``,
    ``'Brick Wall'``, ``'BRICK WALL'``, ``'BrickInfill'``, etc.).

    Args:
        area_elements: ``{area_id: AreaElement}`` — all area elements.
        area_assignments: ``{area_id: section_name}``.
        nodes: ``{node_id: Node}`` — all nodes.
        frame_elements: Optional ``{frame_id: FrameElement}`` — frame
            elements.  Pass post-split elements (e.g. ``builder.split_elements``)
            for best results.
        frame_assignments: Optional ``{frame_id: section_name}``.
        exclude_types: Set of section name patterns to skip.  Each entry
            is matched as a case-insensitive substring.

    Returns:
        List of tuples.  Each tuple has:
        - ``merged_nodes`` — all unique nodes along the shared edge,
            sorted by position.
        - ``master_chain`` — ``[(node_id, t_param), ...]`` — the nodes
            belonging to the coarsest element along the edge.  These
            define the master edge for constraint application.
        - ``slave_nodes`` — ``[(node_id, t_param), ...]`` — all
            remaining nodes from finer-meshed elements, sorted by
            t-parameter.  These need to be constrained to the master
            edge.
        - ``type_a``, ``type_b`` — the two most common section names
            (for backward compatibility).

        The t-parameters are in the range [0, 1] along the edge from
        the first merged node to the last.
    """

    COSINE_TOL = 0.9999

    # ── Helper: case-insensitive substring matching ─────────────
    # SAP2000 section names vary in case (e.g. "Brick Wall",
    # "BRICK WALL", "BrickInfill").  Each entry in exclude_types is
    # matched as a case-insensitive substring of the section name.
    def _is_excluded(name: str) -> bool:
        name_lower = name.lower()
        return any(excl.lower() in name_lower for excl in exclude_types)

    # ── 0. Node arrays & position sort key ──────────────────────
    _node_arr: dict[str, np.ndarray] = {}
    for nid, nd in nodes.items():
        _node_arr[nid] = np.array([nd.x, nd.y, nd.z], dtype=float)

    def _pos_key(nid: str) -> tuple:
        nd = nodes[nid]
        return (nd.x, nd.y, nd.z, nid)

    # ── 1. Build sorted-tuple edge registry ─────────────────────
    # {(nA, nB): [elem_id, ...]}  where nA ≤ nB by position (X→Y→Z).
    # Direction from nA→nB is the positive direction.
    # Separate sets track which keys came from area vs frame elements
    # so the sweep can skip frame-vs-frame comparisons (those should
    # be resolved as modeling errors, not constraints).
    edge_reg: dict[tuple[str, str], list[str]] = defaultdict(list)
    area_keys: set = set()  # keys contributed by area elements
    frame_keys: set = set()  # keys contributed by frame elements

    for aid, elem in area_elements.items():
        if getattr(elem, "inactive", False) or elem is None or len(elem.node_ids) < 3:
            continue
        if _is_excluded(area_assignments.get(aid, "")):
            continue
        nids = list(elem.node_ids)
        n = len(nids)
        for i in range(n):
            j = (i + 1) % n
            nA, nB = nids[i], nids[j]
            if nA == nB:
                continue
            key = (nA, nB) if _pos_key(nA) <= _pos_key(nB) else (nB, nA)
            edge_reg[key].append(aid)
            area_keys.add(key)

    # ── 1b. Add frame element edges to the registry ─────────────
    if frame_elements is not None:
        assign = frame_assignments or {}
        for fid, felem in frame_elements.items():
            if getattr(felem, "inactive", False) or felem is None:
                continue
            if _is_excluded(assign.get(fid, "")):
                continue
            nA, nB = felem.node_i, felem.node_j
            if nA == nB:
                continue
            key = (nA, nB) if _pos_key(nA) <= _pos_key(nB) else (nB, nA)
            edge_reg[key].append(fid)
            frame_keys.add(key)

    # ── 2. Build node→keys index for chain following ────────────
    # Derived directly from the registry — not a separate data model.
    node_keys: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for nA, nB in edge_reg:
        node_keys[nA].append((nA, nB))
        node_keys[nB].append((nA, nB))

    # ── Helpers ─────────────────────────────────────────────────
    def _cos_match(d1: np.ndarray, d2: np.ndarray) -> bool:
        return float(np.dot(d1, d2)) > COSINE_TOL

    def _chain_dir(key: tuple, from_node: str) -> np.ndarray:
        """Unit vector from from_node to the other end of the key."""
        other = key[1] if key[0] == from_node else key[0]
        vec = _node_arr[other] - _node_arr[from_node]
        nrm = float(np.linalg.norm(vec))
        return vec / nrm if nrm > 1e-12 else None

    def _follow(start_node: str, key: tuple) -> list[str]:
        """Follow a single chain from start_node through key."""
        other = key[1] if key[0] == start_node else key[0]
        chain = [start_node, other]
        cur = other
        acc_dir = _node_arr[cur] - _node_arr[start_node]
        nrm = float(np.linalg.norm(acc_dir))
        if nrm > 1e-12:
            acc_dir /= nrm
        else:
            return chain
        visited = {start_node, cur}
        while cur in node_keys:
            found = None
            for k in node_keys[cur]:
                d = _chain_dir(k, cur)
                if d is not None and _cos_match(d, acc_dir):
                    found = k
                    break
            if found is None:
                break
            nxt = found[1] if found[0] == cur else found[0]
            if nxt in visited:
                break
            visited.add(nxt)
            chain.append(nxt)
            vec = _node_arr[nxt] - _node_arr[start_node]
            nrm = float(np.linalg.norm(vec))
            if nrm > 1e-12:
                acc_dir = vec / nrm
            cur = nxt
        return chain

    def _truncate_at_junction(c1: list[str], c2: list[str]):
        """Truncate both chains at the first common node after start.
        Returns (truncated_c1, truncated_c2) or None if no junction."""
        set2 = set(c2[1:])
        for i, nid in enumerate(c1[1:], 1):
            if nid in set2:
                j = c2.index(nid)
                return c1[: i + 1], c2[: j + 1]
        return None

    def _collect(chains: list[list[str]], d: np.ndarray) -> list[str]:
        nodes_set: set = set()
        for ch in chains:
            nodes_set.update(ch)
        ref = _node_arr[chains[0][0]]
        return sorted(
            nodes_set,
            key=lambda nid: float(np.dot(_node_arr[nid] - ref, d)),
        )

    # ── 3. Single pass: horizontal edges (by Z-band) ────────────
    # All edges in this model are at constant Z — there are no true
    # vertical (Z-changing) tears.  A single Z-band sweep suffices.
    z_groups: dict[float, list[tuple]] = defaultdict(list)
    for nA, nB in edge_reg:
        zk = round((_node_arr[nA][2] + _node_arr[nB][2]) * 0.5, 1)
        z_groups[zk].append((nA, nB))

    tears: list[tuple] = []  # each: (merged_nodes, [(chain, elem_id), ...])

    for zk, group in z_groups.items():
        if len(group) < 2:
            continue
        # Sort by min-X then min-Y of the two nodes
        group.sort(
            key=lambda k: (
                min(_node_arr[k[0]][0], _node_arr[k[1]][0]),
                min(_node_arr[k[0]][1], _node_arr[k[1]][1]),
            )
        )
        active: list[tuple] = []  # list of (nA, nB)

        for nA, nB in group:
            x_min = min(_node_arr[nA][0], _node_arr[nB][0])

            # Retire keys whose max X < sweep position
            active = [
                k for k in active if max(_node_arr[k[0]][0], _node_arr[k[1]][0]) >= x_min - 1e-6
            ]

            # Check both nA and nB against the active set
            for k in active:
                if k == (nA, nB):
                    continue  # same key → compatible edge, skip
                shared = {nA, nB} & set(k)
                if not shared:
                    continue
                sn = next(iter(shared))
                d1 = _chain_dir((nA, nB), sn)
                d2 = _chain_dir(k, sn)
                if d1 is None or d2 is None:
                    continue
                if not _cos_match(d1, d2):
                    continue
                # Skip frame-vs-frame comparisons — overlapping frame
                # elements should be fixed in the source model, not
                # patched with constraints.  Frame-vs-area IS wanted.
                if (nA, nB) not in area_keys and k not in area_keys:
                    continue
                c1 = _follow(sn, (nA, nB))
                c2 = _follow(sn, k)
                trunc = _truncate_at_junction(c1, c2)
                if trunc is not None:
                    ordered = _collect([trunc[0], trunc[1]], d1)
                    if len(ordered) >= 3:
                        # Store the element IDs from the two starting keys
                        ea = edge_reg[(nA, nB)][0]
                        eb = edge_reg[k][0]
                        tears.append((ordered, [(trunc[0], ea), (trunc[1], eb)]))

            active.append((nA, nB))

    # ── Helper: resolve element ID to type name ─────────────────
    # Checks area_assignments first, then frame_assignments.
    fa = frame_assignments or {}

    def _elem_type(eid: str) -> str:
        return area_assignments.get(eid) or fa.get(eid) or "unknown"

    # ── 4. Merge overlapping tears ──────────────────────────────
    # Two tears overlap if they share ≥1 node and are colinear
    # (absolute cosine of endpoint vectors > COSINE_TOL).
    # Each tear carries the per-element node chains for ALL elements
    # sharing the edge — there can be 2, 3, or more.
    merged: list[tuple[list[str], list[tuple], str, str]] = []
    # (merged_nodes, [(chain_with_t, type), ...], type_a, type_b)
    used = [False] * len(tears)

    for i in range(len(tears)):
        if used[i]:
            continue
        nids_i, chains_i = tears[i]
        group_nids = [nids_i]
        # elem_id → set_of_nodes for each participating element
        elem_chains: dict[str, set] = {}
        for chain, eid in chains_i:
            elem_chains[eid] = set(chain)
        types_by_elem: dict[str, str] = {eid: _elem_type(eid) for chain, eid in chains_i}
        used[i] = True
        # Direction from first tear's endpoints
        fa_i = _node_arr[nids_i[0]]
        la_i = _node_arr[nids_i[-1]]
        d_i = la_i - fa_i
        dni = float(np.linalg.norm(d_i))
        if dni > 1e-12:
            d_i /= dni
        else:
            d_i = np.array([1.0, 0.0, 0.0])

        for j in range(i + 1, len(tears)):
            if used[j]:
                continue
            nids_j, chains_j = tears[j]
            shared = set(nids_i) & set(nids_j)
            if not shared:
                continue
            fa_j = _node_arr[nids_j[0]]
            la_j = _node_arr[nids_j[-1]]
            d_j = la_j - fa_j
            dnj = float(np.linalg.norm(d_j))
            if dnj > 1e-12:
                d_j /= dnj
            else:
                d_j = np.array([1.0, 0.0, 0.0])
            if abs(float(np.dot(d_i, d_j))) <= COSINE_TOL:
                continue
            group_nids.append(nids_j)
            for chain, eid in chains_j:
                if eid in elem_chains:
                    elem_chains[eid].update(chain)
                else:
                    elem_chains[eid] = set(chain)
                    types_by_elem[eid] = _elem_type(eid)
            used[j] = True

        all_nodes: set = set()
        ref_node = group_nids[0][0]
        for t in group_nids:
            all_nodes.update(t)
        ordered = sorted(
            all_nodes,
            key=lambda nid: float(np.dot(_node_arr[nid] - _node_arr[ref_node], d_i)),
        )
        if len(ordered) >= 3:
            # Compute t-parameters along the edge
            span_vec = _node_arr[ordered[-1]] - _node_arr[ordered[0]]
            span_len = float(np.linalg.norm(span_vec))
            span_dir = span_vec / span_len if span_len > 1e-12 else np.array([1.0, 0.0, 0.0])
            ref_pt = _node_arr[ordered[0]]

            def _chain_with_t(
                nodes: set, ref_pt=ref_pt, span_dir=span_dir, span_len=span_len
            ) -> list[tuple]:
                items = [
                    (nid, float(np.dot(_node_arr[nid] - ref_pt, span_dir)) / span_len)
                    for nid in nodes
                    if nid in _node_arr
                ]
                items.sort(key=lambda x: x[1])
                return items

            # Build per-element chains with t-values, WITHOUT merging by type
            # (merging by type mixes coarse and fine nodes from different
            # elements of the same section — we need to keep them separate
            # to correctly identify which chain is the coarsest master).
            all_chains: list[tuple] = []  # [(chain_with_t, type, elem_id), ...]
            for eid, elem_nodes in elem_chains.items():
                t = types_by_elem[eid]
                all_chains.append((_chain_with_t(elem_nodes), t, eid))

            # Sort by chain length (fewest nodes = coarsest mesh = master)
            all_chains.sort(key=lambda x: len(x[0]))

            # The shortest chain defines the master edge
            master_chain = all_chains[0][0] if all_chains else []

            # Warn if master chain doesn't span the full tear (t=0 to t=1)
            if verbose and len(master_chain) >= 2:
                t_start = master_chain[0][1]
                t_end = master_chain[-1][1]
                if t_start > 0.01 or t_end < 0.99:
                    print(
                        f"  ⚠ Master chain t-range [{t_start:.3f}, {t_end:.3f}] "
                        f"does not span full tear — slave nodes outside this "
                        f"range will use extrapolated interpolation"
                    )

            # All other chains are slaves
            slave_chains = [c for c, _, _ in all_chains[1:]] if len(all_chains) > 1 else []

            # Collect all slave nodes (excluding master nodes)
            master_nodes = {nid for nid, _ in master_chain}
            slave_nodes: list[tuple] = []
            for sc in slave_chains:
                for nid, tval in sc:
                    if nid not in master_nodes:
                        slave_nodes.append((nid, tval))
            slave_nodes.sort(key=lambda x: x[1])

            # Verify master chain integrity: all master nodes should be
            # colinear and in order along the span direction
            if len(master_chain) >= 2:
                m0 = _node_arr[master_chain[0][0]]
                m1 = _node_arr[master_chain[-1][0]]
                span_dir = m1 - m0
                span_len = float(np.linalg.norm(span_dir))
                if span_len > 1e-12:
                    span_dir /= span_len
                    # Check each intermediate master node is on the span line
                    for nid, tval in master_chain[1:-1]:
                        pt = _node_arr[nid]
                        proj = float(np.dot(pt - m0, span_dir))
                        off = float(np.linalg.norm(pt - (m0 + proj * span_dir)))
                        if off > 0.01 * span_len:
                            # Master node is off the span line — add to slaves
                            slave_nodes.append((nid, tval))
                            master_chain = [
                                (m0_n, m0_t) for m0_n, m0_t in master_chain if m0_n != nid
                            ]

            type_names = sorted({t for _, t, _ in all_chains})
            type_a = type_names[0] if type_names else "unknown"
            type_b = type_names[-1] if len(type_names) > 1 else type_a
            merged.append((ordered, master_chain, slave_nodes, type_a, type_b))

    # ── 5. Format results ───────────────────────────────────────
    # Returns: [(merged_nodes, master_chain, slave_nodes, type_a, type_b)]
    # master_chain: [(node_id, t), ...] — coarsest element's nodes (master edge)
    # slave_nodes:  [(node_id, t), ...] — all non-master nodes in order
    results: list[tuple[list[str], list[tuple], list[tuple], str, str]] = []
    for nids, master, slaves, ta, tb in merged:
        results.append((nids, master, slaves, ta, tb))

    return results


def find_wall_nodes_inside_slabs(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    z_tol: float = 0.5,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Identify wall-area nodes that lie inside slab areas.

    A *wall* is any area whose corners span more than *z_tol* in Z
    (vertical orientation).  A *slab* is any area whose corners are all
    within *z_tol* (horizontal orientation).

    For each slab, this function finds all wall nodes that are on the
    slab's Z plane and within the slab's XY bounding box but NOT at a
    slab corner.  These are wall-to-slab connections that the regular
    meshing pipeline may miss.

    Args:
        area_elements: ``{area_id: AreaElement}``.
        area_assignments: ``{area_id: section_name}``.
        nodes: ``{node_id: Node}``.
        z_tol: Z-span threshold (same units as model) — areas with
            Z-span ≤ *z_tol* are considered horizontal (slabs), those
            with Z-span > *z_tol* are vertical (walls).

    Returns:
        List of dicts, one per wall-in-slab finding::

            {
                "slab_id": str,         # slab area ID
                "wall_id": str,         # wall area ID
                "nodes": [              # wall nodes inside the slab
                    {
                        "node_id": str,
                        "node_tag": int,
                        "x": float, "y": float, "z": float,
                    }
                ],
                "slab_X": (float, float),   # slab X range
                "slab_Y": (float, float),   # slab Y range
                "slab_Z": float,             # slab Z level
                "section": str,              # wall section name
            }
    """
    # ── Classify areas ──────────────────────────────────────────
    slab_ids: set = set()
    wall_ids: set = set()
    for aid, ae in area_elements.items():
        if getattr(ae, "inactive", False):
            continue
        nds = [nodes.get(n) for n in ae.node_ids]
        nds = [n for n in nds if n is not None]
        if len(nds) < 4:
            continue
        zs = [n.z for n in nds]
        z_span = max(zs) - min(zs)
        if z_span <= z_tol:
            slab_ids.add(aid)
        else:
            wall_ids.add(aid)

    if not slab_ids or not wall_ids:
        return []

    # ── Gather wall node coordinates ────────────────────────────
    # Wall nodes that are at each wall's bottom Z (typically a slab level)
    wall_nodes_at_z: dict[float, list[dict]] = defaultdict(list)
    for wid in wall_ids:
        ae = area_elements[wid]
        for nid in ae.node_ids:
            nd = nodes.get(nid)
            if nd is None:
                continue
            wall_nodes_at_z[round(nd.z, 4)].append(
                {
                    "node_id": nid,
                    "node_tag": nd.node_tag,
                    "x": nd.x,
                    "y": nd.y,
                    "z": nd.z,
                    "wall_id": wid,
                    "section": area_assignments.get(wid, ""),
                }
            )

    if not wall_nodes_at_z:
        return []

    # ── Check each slab for interior wall nodes ─────────────────
    findings: list[dict[str, Any]] = []
    for sid in slab_ids:
        ae = area_elements[sid]
        corner_nds = [nodes.get(n) for n in ae.node_ids if n in nodes]
        corner_nds = [n for n in corner_nds if n is not None]
        if len(corner_nds) < 4:
            continue

        xs = [n.x for n in corner_nds]
        ys = [n.y for n in corner_nds]
        zs = [n.z for n in corner_nds]
        slab_z = round(sum(zs) / len(zs), 4)

        # Warn if slab is rotated off axis — bounding-box containment
        # may produce false positives for non-orthogonal slabs.
        _slab_rotated = False
        for k in range(len(corner_nds)):
            n1 = corner_nds[k]
            n2 = corner_nds[(k + 1) % len(corner_nds)]
            if abs(n1.x - n2.x) > 1e-6 and abs(n1.y - n2.y) > 1e-6:
                _slab_rotated = True
                break
        if _slab_rotated and verbose:
            print(
                f"  ⚠ Slab {sid} is rotated — wall-node containment "
                f"uses axis-aligned bounding box, may have false "
                f"positives"
            )

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        margin = max((x_max - x_min), (y_max - y_min)) * 0.001

        wall_node_set = wall_nodes_at_z.get(slab_z, [])
        if not wall_node_set:
            continue

        interior: list[dict] = []
        seen_walls: set = set()
        for wn in wall_node_set:
            # Inside slab XY projection (not at corners)
            if not (x_min - margin <= wn["x"] <= x_max + margin):
                continue
            if not (y_min - margin <= wn["y"] <= y_max + margin):
                continue
            # Skip slab corner nodes
            is_corner = any(
                abs(wn["x"] - cn.x) < margin and abs(wn["y"] - cn.y) < margin for cn in corner_nds
            )
            if is_corner:
                continue
            interior.append(wn)
            seen_walls.add(wn["wall_id"])

        if not interior:
            continue

        for wid in sorted(seen_walls):
            wall_nodes = [wn for wn in interior if wn["wall_id"] == wid]
            findings.append(
                {
                    "slab_id": sid,
                    "wall_id": wid,
                    "nodes": wall_nodes,
                    "slab_X": (x_min, x_max),
                    "slab_Y": (y_min, y_max),
                    "slab_Z": float(np.mean(zs)),
                    "section": wall_nodes[0]["section"],
                }
            )

    return findings


def print_wall_inside_slab_report(
    findings: list[dict[str, Any]],
    file=None,
    verbose: bool = False,
) -> None:
    """Print a summary report of wall-in-slab findings.

    Args:
        findings: Output from :func:`find_wall_nodes_inside_slabs`.
        file: Output stream (default ``sys.stdout``).
        verbose: If True, also print individual wall–slab details
            (node coordinates, slab bounds).  Default is False.
    """
    n = len(findings)
    if n == 0:
        print("  No wall nodes found inside slab areas.", file=file)
        return
    print(f"  ⚠ {n} wall–slab intersection(s) detected", file=file)
    if verbose:
        for f in findings:
            sec = f["section"]
            coords = "; ".join(
                f"{wn['node_id']}({wn['x']:.1f},{wn['y']:.1f},{wn['z']:.2f})" for wn in f["nodes"]
            )
            print(
                f"    Wall {f['wall_id']:>4} ({sec}) inside slab {f['slab_id']:>4}\n"
                f"      Nodes: {coords}\n"
                f"      Slab bounds: X∈{f['slab_X']} Y∈{f['slab_Y']} Z={f['slab_Z']:.2f}",
                file=file,
            )


def split_slabs_at_wall_intersections(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    next_tag: int = 1,
    groups: Optional[dict[str, Group]] = None,
    z_tol: float = 0.5,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int]:
    """Subdivide slab areas at wall-edge intersection lines.

    Before the regular max_size-based meshing, this function detects
    wall areas whose edge nodes fall inside slab areas and splits the
    slab along those intersection lines.  The resulting sub-areas then
    mesh naturally to share nodes with the wall at the interface.

    This mirrors the pattern of :func:`split_areas_at_frame_edges` but
    for wall-slab pairs instead of frame-slab pairs.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new interior nodes are added here.
        next_tag: Next available numeric tag for new nodes.
        groups: Optional ``{group_name: Group}`` — group memberships
            propagated to sub-elements.
        z_tol: Z-span threshold for wall vs slab classification.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with
        subdivided slab areas added.
    """
    from .sap_data import AreaElement as _AreaElement
    from .sap_data import Node as _Node

    # ── 1. Find intersections ───────────────────────────────────
    findings = find_wall_nodes_inside_slabs(
        area_elements,
        area_assignments,
        nodes,
        z_tol=z_tol,
    )
    if not findings:
        return area_elements, area_assignments, nodes, next_tag

    # ── 2. Group findings by slab ───────────────────────────────
    # Each group: (slab_id, [(wall_id, wall_nodes), ...])
    slab_groups: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for f in findings:
        slab_groups[f["slab_id"]].append((f["wall_id"], f["nodes"]))

    # ── 3. For each slab, collect parametric split positions ────
    for sid, wall_list in slab_groups.items():
        elem = area_elements.get(sid)
        if elem is None or getattr(elem, "inactive", False):
            continue
        if len(elem.node_ids) != 4:
            continue

        # Slab corner coordinates (CCW)
        corners_list = [nodes.get(n) for n in elem.node_ids if n in nodes]
        if len(corners_list) != 4:
            continue
        # Ensure CCW ordering matching mesh_area_elements convention
        c0 = np.array([corners_list[0].x, corners_list[0].y, corners_list[0].z])
        c1 = np.array([corners_list[1].x, corners_list[1].y, corners_list[1].z])
        c2 = np.array([corners_list[2].x, corners_list[2].y, corners_list[2].z])
        c3 = np.array([corners_list[3].x, corners_list[3].y, corners_list[3].z])
        corners_arr = [c0, c1, c2, c3]

        # Get wall interior nodes as numpy arrays
        interior_pts: list[np.ndarray] = []
        for wid, wall_nodes in wall_list:
            for wn in wall_nodes:
                interior_pts.append(np.array([wn["x"], wn["y"], wn["z"]]))

        if not interior_pts:
            continue

        # Compute parametric (u, v) for each interior point
        u_vals: set = {0.0, 1.0}
        v_vals: set = {0.0, 1.0}
        for pt in interior_pts:
            uv = _point_uv_on_quad(pt, corners_arr)
            if uv is None:
                continue
            u, v = uv
            if 1e-6 < u < 1.0 - 1e-6:
                u_vals.add(round(u, 8))
            if 1e-6 < v < 1.0 - 1e-6:
                v_vals.add(round(v, 8))

        if len(u_vals) <= 2 and len(v_vals) <= 2:
            continue  # no interior splits needed

        # Sort and deduplicate
        u_list = sorted(u_vals)
        v_list = sorted(v_vals)
        _tol = 1e-6
        u_list = [u_list[0]] + [u for u in u_list[1:] if u - u_list[u_list.index(u) - 1] > _tol]
        v_list = [v_list[0]] + [v for v in v_list[1:] if v - v_list[v_list.index(v) - 1] > _tol]

        n_u = len(u_list) - 1
        n_v = len(v_list) - 1

        # Bilinear grid at parametric positions
        grid = np.zeros((n_v + 1, n_u + 1, 3))
        for j, v in enumerate(v_list):
            for i, u in enumerate(u_list):
                top = c0 * (1.0 - u) + c1 * u
                bot = c3 * (1.0 - u) + c2 * u
                grid[j, i] = top * (1.0 - v) + bot * v

        # Build coordinate cache
        _pos_cache: dict[str, np.ndarray] = {}
        for nid, nd in nodes.items():
            _pos_cache[nid] = np.array([nd.x, nd.y, nd.z])
        for pt in interior_pts:
            pass  # already in nodes

        # Create grid nodes (reuse existing)
        node_grid = [[None] * (n_u + 1) for _ in range(n_v + 1)]
        corner_ids = list(elem.node_ids)
        for j in range(n_v + 1):
            for i in range(n_u + 1):
                if i == 0 and j == 0:
                    node_grid[j][i] = corner_ids[0]
                    continue
                if i == n_u and j == 0:
                    node_grid[j][i] = corner_ids[1]
                    continue
                if i == n_u and j == n_v:
                    node_grid[j][i] = corner_ids[2]
                    continue
                if i == 0 and j == n_v:
                    node_grid[j][i] = corner_ids[3]
                    continue
                pt = grid[j, i]
                # Reuse existing node within tolerance
                found = None
                for nid, npos in _pos_cache.items():
                    if np.linalg.norm(npos - pt) < 1e-4:
                        found = nid
                        break
                if found is not None:
                    node_grid[j][i] = found
                    continue
                new_id = f"{sid}_wi_{j}_{i}"
                new_tag = next_tag
                next_tag += 1
                nodes[new_id] = _Node(
                    node_id=new_id,
                    node_tag=new_tag,
                    x=float(pt[0]),
                    y=float(pt[1]),
                    z=float(pt[2]),
                )
                _pos_cache[new_id] = np.array([pt[0], pt[1], pt[2]])
                node_grid[j][i] = new_id

        # Mark original slab inactive
        elem.inactive = True

        # Determine parent groups
        parent_groups: list[str] = []
        if groups is not None:
            for gname, g in groups.items():
                if f"Area:{sid}" in g.objects:
                    parent_groups.append(gname)

        # Create sub-areas (CCW ordering)
        sec_name = area_assignments.get(sid, "")
        for j in range(n_v):
            for i in range(n_u):
                sub_id = f"{sid}_wi_sub_{j}_{i}"
                sub_tag = next_tag
                next_tag += 1
                n0 = node_grid[j][i]
                n1 = node_grid[j][i + 1]
                n2 = node_grid[j + 1][i + 1]
                n3 = node_grid[j + 1][i]
                area_elements[sub_id] = _AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=[n0, n1, n2, n3],
                    thickness=elem.thickness,
                    parent_id=sid,
                )
                elem.child_ids.append(sub_id)
                if sec_name:
                    area_assignments[sub_id] = sec_name
                if parent_groups:
                    sub_ref = f"Area:{sub_id}"
                    for gname in parent_groups:
                        groups[gname].objects.append(sub_ref)

    return area_elements, area_assignments, nodes, next_tag


def remove_floating_nodes(
    md: SAPModelData,
    z_tolerance: float = 0.5,
) -> list[dict[str, Any]]:
    """Remove nodes not connected to any element, redistributing loads and mass.

    SAP2000 can define nodes that are not referenced by any frame or
    area element but still carry mass (from mass source) or loads
    (joint loads).  These cause singularities in OpenSees.

    For each floating node:
      1. Accumulate its mass and joint loads.
      2. Find the nearest connected node at the same elevation band.
      3. Add mass/loads to that node.
      4. Remove the floating node from ``md.nodes``.

    Modifies *md* in‑place (removes nodes, transfers restraints and
    joint loads to the nearest connected neighbour).

    Args:
        md: Model data to scan and clean.
        z_tolerance: Elevation tolerance for matching nodes (same units
            as model coordinates).

    Returns:
        List of dicts documenting each removed node, with keys:
        ``node_id``, ``x``, ``y``, ``z``, ``mass_source``, ``loads``,
        ``restrained``, ``nearest_node``, ``distance``.
    """
    connected: set = set()
    for fe in md.frame_elements.values():
        if not getattr(fe, "inactive", False):
            connected.add(fe.node_i)
            connected.add(fe.node_j)
    for ae in md.area_elements.values():
        if not getattr(ae, "inactive", False):
            connected.update(ae.node_ids)

    joint_loads: dict[str, list[float]] = {}
    for jl in getattr(md, "joint_loads", []):
        nid = getattr(jl, "node_id", "") or getattr(jl, "node", "")
        if not nid:
            continue
        if nid not in joint_loads:
            joint_loads[nid] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        joint_loads[nid][0] += getattr(jl, "fx", 0.0) or 0.0
        joint_loads[nid][1] += getattr(jl, "fy", 0.0) or 0.0
        joint_loads[nid][2] += getattr(jl, "fz", 0.0) or 0.0
        joint_loads[nid][3] += getattr(jl, "mx", 0.0) or 0.0
        joint_loads[nid][4] += getattr(jl, "my", 0.0) or 0.0
        joint_loads[nid][5] += getattr(jl, "mz", 0.0) or 0.0

    ms_has_masses = any(
        getattr(src, "masses", False) for src in getattr(md, "mass_sources", {}).values()
    )

    restraints = getattr(md, "restraints", {})

    floating: list[str] = [nid for nid in md.nodes if nid not in connected]
    if not floating:
        return []

    rows: list[dict[str, Any]] = []
    removed_nodes: list[str] = []

    for nid in floating:
        nd = md.nodes[nid]
        loads = joint_loads.get(nid, [0.0] * 6)
        has_loads = any(abs(v) > 1e-12 for v in loads)
        has_restraint = nid in restraints
        has_mass = ms_has_masses

        if not (has_mass or has_loads or has_restraint):
            removed_nodes.append(nid)
            continue

        best_dist = float("inf")
        best_nid = None
        for cnid in connected:
            cnd = md.nodes[cnid]
            if abs(cnd.z - nd.z) > z_tolerance:
                continue
            d = np.linalg.norm([cnd.x - nd.x, cnd.y - nd.y, cnd.z - nd.z])
            if d < best_dist:
                best_dist = d
                best_nid = cnid

        if best_nid is None:
            for cnid in connected:
                cnd = md.nodes[cnid]
                d = np.linalg.norm([cnd.x - nd.x, cnd.y - nd.y, cnd.z - nd.z])
                if d < best_dist:
                    best_dist = d
                    best_nid = cnid

        if has_loads and best_nid:
            if not hasattr(md, "joint_loads") or md.joint_loads is None:
                md.joint_loads = []
            md.joint_loads.append(
                JointLoad(
                    node=best_nid,
                    fx=loads[0],
                    fy=loads[1],
                    fz=loads[2],
                    mx=loads[3],
                    my=loads[4],
                    mz=loads[5],
                )
            )

        if has_restraint and best_nid and best_nid not in restraints:
            restraints[best_nid] = restraints.pop(nid)

        rows.append(
            {
                "node_id": nid,
                "x": nd.x,
                "y": nd.y,
                "z": nd.z,
                "mass_source": ms_has_masses,
                "loads": (
                    f"({loads[0]:.1f}, {loads[1]:.1f}, {loads[2]:.1f}, "
                    f"{loads[3]:.1f}, {loads[4]:.1f}, {loads[5]:.1f})"
                ),
                "restrained": has_restraint,
                "nearest_node": best_nid,
                "distance": best_dist,
            }
        )
        removed_nodes.append(nid)

    for nid in removed_nodes:
        md.nodes.pop(nid, None)
        restraints.pop(nid, None)

    return rows
