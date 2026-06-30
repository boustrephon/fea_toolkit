"""Optional constrained quadrilateral remeshing via Gmsh (GPL v2+).

This module provides an alternative to the built-in structured subdivision
(``mesh_area_elements``) using Gmsh's unstructured 2D mesher.  It supports
**line constraints** so frame-element edges that lie on or cross an area
boundary are preserved in the resulting mesh, avoiding the need for post-
process edge constraints.

Requires ``gmsh`` (``pip install fea_toolkit[mesh-remesh]``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ── Internal helpers ───────────────────────────────────────────────────


def _check_gmsh() -> bool:
    """Return True if ``gmsh`` is available."""
    try:
        import gmsh  # noqa: F401
        return True
    except ImportError:
        return False


def _newell_area(pts: List[Tuple[float, float, float]]) -> float:
    """3D polygon area via Newell's method."""
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        x1, y1, z1 = pts[i]
        x2, y2, z2 = pts[(i + 1) % len(pts)]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return 0.5 * np.sqrt(nx * nx + ny * ny + nz * nz)


# ========================================================================
# Public API
# ========================================================================


def remesh_areas(
    area_elements: Dict[str, Any],
    area_assignments: Dict[str, str],
    nodes: Dict[str, Any],
    area_mesh: Dict[str, Any],
    line_constraints: Optional[Dict[str, List[Tuple[str, str, str]]]] = None,
    target_length: float = 0.5,
    recombine: bool = True,
    verbose: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, Any], int]:
    """Remesh quadrilateral areas via Gmsh, optionally preserving frame-
    element edges as line constraints.

    Each area marked for meshing is sent to Gmsh as a surface.  Any frame
    edges whose endpoints lie on the area's boundary (``line_constraints``)
    are embedded as 1-D curves so the mesh conforms to them — no post-hoc
    ``equationConstraint`` is needed.

    Args:
        area_elements: ``{area_id: AreaElement}`` with ``.node_ids``,
            ``.thickness``, ``.inactive``.
        area_assignments: ``{area_id: section_name}``.
        nodes: ``{node_id: Node}`` with ``.x``, ``.y``, ``.z``,
            ``.node_tag``.
        area_mesh: ``{area_id: AreaMesh}`` with ``.auto_mesh``,
            ``.max_size``.
        line_constraints: ``{area_id: [(frame_id, node_a, node_b), ...]}`` —
            Frame edges to preserve, keyed by area.  Each tuple contains the
            frame ID (for reference) and the two endpoint node IDs whose
            coordinates are resolved from the *nodes* dict and embedded as
            1D curves in the Gmsh surface.  Build this with
            :func:`constrain_line`.
        target_length: Target mesh element edge length (same units as
            model).  Overridden by ``max_size`` from ``area_mesh`` when
            available.
        recombine: If True, Gmsh recombines triangles into quads via the
            Blossom algorithm.
        verbose: If True, prints Gmsh progress.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag)`` with the
        same contract as ``mesh_area_elements()`` — original areas are
        marked inactive and new sub-elements are added.
    """
    if not _check_gmsh():
        raise ImportError(
            "gmsh is required for remesh_areas(). "
            "Install with: pip install fea_toolkit[mesh-remesh]"
        )

    import gmsh

    if line_constraints is None:
        line_constraints = {}

    gmsh.initialize()
    if not verbose:
        gmsh.option.set_number("General.Terminal", 0)

    try:
        # ── Coordinate registry populated per-area from corner
        # nodes only, so adjacent areas' shared edges are still
        # deduplicated without collapsing intentionally separate
        # nodes at the same coordinate. ──
        _coord_key = lambda x, y, z: (round(x, 6), round(y, 6), round(z, 6))
        _coord_to_id: Dict[tuple, str] = {}

        # Seed next_tag from the highest area tag to avoid collisions.
        existing_tags = {nd.node_tag for nd in nodes.values()}
        next_node_tag = max(existing_tags) + 1 if existing_tags else 1

        next_tag = 1
        if area_elements:
            next_tag = max((ae.area_tag for ae in area_elements.values()), default=0) + 1

        for aid, mesh in area_mesh.items():
            if not getattr(mesh, "auto_mesh", False):
                continue
            # Resolve characteristic length: prefer max_size from mesh
            # assignment, fall back to the caller-supplied target_length.
            lc = getattr(mesh, "max_size", 0.0)
            if lc <= 0.0:
                lc = target_length
            if lc <= 0.0:
                continue

            elem = area_elements.get(aid)
            if elem is None or len(getattr(elem, "node_ids", [])) != 4:
                continue
            if getattr(elem, "inactive", False):
                continue

            nids = list(elem.node_ids)
            pts = []
            for nid in nids:
                nd = nodes.get(nid)
                if nd is None:
                    break
                pts.append((nd.x, nd.y, nd.z))
            if len(pts) != 4:
                continue

            # Each area gets its own Gmsh model so node tags don't
            # accumulate across iterations.
            gmsh.model.add(f"area_{aid}")

            # --- Add corner points to Gmsh model ---
            point_tags = []
            for pt in pts:
                tag = gmsh.model.occ.add_point(*pt, lc)
                point_tags.append(tag)

            # --- Create surface from 4 edges ---
            lines = []
            for k in range(4):
                tag = gmsh.model.occ.add_line(point_tags[k], point_tags[(k + 1) % 4])
                lines.append(tag)
            loop = gmsh.model.occ.add_curve_loop(lines)
            surf = gmsh.model.occ.add_surface_filling(loop)

            # --- Embed line constraints (frame edges) ---
            frame_constraints = line_constraints.get(aid, [])
            if frame_constraints:
                constraint_curves: List[int] = []
                for fid, nid_a, nid_b in frame_constraints:
                    nd_a = nodes.get(nid_a)
                    nd_b = nodes.get(nid_b)
                    if nd_a is None or nd_b is None:
                        continue
                    pta = (nd_a.x, nd_a.y, nd_a.z)
                    ptb = (nd_b.x, nd_b.y, nd_b.z)
                    # Skip constraints whose endpoints are not within
                    # tolerance of the area boundary (1‑mm check).
                    def _on_boundary(p, corners, tol=1e-3):
                        for k in range(4):
                            a, b = corners[k], corners[(k + 1) % 4]
                            ab = np.array(b) - np.array(a)
                            ap = np.array(p) - np.array(a)
                            cross = np.linalg.norm(np.cross(ab, ap))
                            edge_len = max(np.linalg.norm(ab), 1e-12)
                            if cross / edge_len < tol and 0 <= np.dot(ap, ab) / max(np.dot(ab, ab), 1e-12) <= 1:
                                return True
                        return False
                    if not (_on_boundary(pta, pts) and _on_boundary(ptb, pts)):
                        continue
                    ta = gmsh.model.occ.add_point(*pta, lc)
                    tb = gmsh.model.occ.add_point(*ptb, lc)
                    curve = gmsh.model.occ.add_line(ta, tb)
                    constraint_curves.append(curve)
                # Synchronise OCC model BEFORE embedding so the mesh
                # module sees the newly created points and curves.
                gmsh.model.occ.synchronize()
                if constraint_curves:
                    gmsh.model.mesh.embed(1, constraint_curves, 2, surf)

            gmsh.model.occ.synchronize()

            # --- Mesh settings ---
            gmsh.option.set_number("Mesh.CharacteristicLengthMin", lc * 0.5)
            gmsh.option.set_number("Mesh.CharacteristicLengthMax", lc)
            gmsh.option.set_number("Mesh.Algorithm", 6)  # Frontal Delaunay

            # --- Generate 2D mesh ---
            gmsh.model.mesh.generate(2)

            if recombine:
                gmsh.model.mesh.recombine()

            # --- Extract mesh nodes ---
            node_tags_local, coords, _ = gmsh.model.mesh.getNodes()
            coord_map = dict(zip(node_tags_local, coords.reshape((-1, 3))))

            # Separate corner nodes from interior/edge nodes
            corner_coords = set(
                (round(pt[0], 6), round(pt[1], 6), round(pt[2], 6))
                for pt in pts
            )

            # Get element types and connectivity for this surface
            all_elem_types, all_elem_tags, all_conn = gmsh.model.mesh.getElements(2, surf)
            quad_conn: List[List[int]] = []
            for etype_idx, etype in enumerate(all_elem_types):
                if etype == 3:  # 4-node quad
                    conn = np.array(all_conn[etype_idx]).reshape((-1, 4))
                    quad_conn.extend(conn.tolist())

            # This API only supports quad meshes — skip if
            # no quads were produced (e.g. recombine=False).
            if not quad_conn:
                gmsh.model.remove()
                continue

            # --- Map Gmsh nodes back to fea_toolkit nodes,
            # reusing any previously-created node at the same coordinate. ---
            sub_node_map: Dict[int, str] = {}  # gmsh_tag → fea_toolkit_id
            for gmsh_tag in node_tags_local:
                coord = coord_map[gmsh_tag]
                key = (round(coord[0], 6), round(coord[1], 6), round(coord[2], 6))
                existing = _coord_to_id.get(key)
                if existing is not None:
                    sub_node_map[gmsh_tag] = existing
                    continue
                # New interior/edge node
                nid = f"{aid}_gmsh_{gmsh_tag}"
                ntag = next_node_tag
                next_node_tag += 1
                from ..model.sap_data import Node  # noqa: E402
                nodes[nid] = Node(
                    node_id=nid, node_tag=ntag,
                    x=float(coord[0]), y=float(coord[1]), z=float(coord[2]),
                )
                _coord_to_id[key] = nid
                sub_node_map[gmsh_tag] = nid
                next_tag = max(next_tag, ntag + 1)

            # --- Create sub-area elements from Gmsh quads ---
            sec_name = area_assignments.get(aid, "")
            sub_count = 0
            for row in quad_conn:
                sub_id = f"{aid}_gmsh_{len([k for k in area_elements if k.startswith(f'{aid}_gmsh_')])}"
                sub_tag = next_tag
                next_tag += 1
                sub_nodes = [sub_node_map[int(t)] for t in row]
                from ..model.sap_data import AreaElement  # noqa: E402
                area_elements[sub_id] = AreaElement(
                    area_id=sub_id,
                    area_tag=sub_tag,
                    node_ids=sub_nodes,
                    thickness=getattr(elem, "thickness", 0.0),
                )
                if sec_name:
                    area_assignments[sub_id] = sec_name
                sub_count += 1

            # Only mark original as inactive when at least one
            # 4-node quad element was actually created.
            if sub_count > 0:
                elem.inactive = True

            # Remove this area's Gmsh model so the next iteration
            # starts fresh (no node-tag carryover).
            gmsh.model.remove()

    finally:
        gmsh.finalize()
    return area_elements, area_assignments, nodes, next_tag


def constrain_line(
    area_id: str,
    frame_id: str,
    node_a: str,
    node_b: str,
    nodes: Dict[str, Any],
    constraints: Dict[str, List[Tuple[str, str, str]]],
) -> None:
    """Register a frame element edge as a line constraint for a given area.

    This is a convenience helper to build the ``line_constraints`` dict
    for :func:`remesh_areas`.  Call it for each frame element whose
    endpoints lie on (or cross) the area's boundary.

    Args:
        area_id: The area element ID to constrain.
        frame_id: The frame element ID (for reference).
        node_a: Node ID of one end of the frame edge.
        node_b: Node ID of the other end.
        nodes: ``{node_id: Node}`` — used to resolve coordinates for
            deduplication.
        constraints: The ``line_constraints`` dict being built (modified
            in place).  Values are lists of ``(frame_id, node_a, node_b)``
            tuples.
    """
    if area_id not in constraints:
        constraints[area_id] = []
    constraints[area_id].append((frame_id, node_a, node_b))
