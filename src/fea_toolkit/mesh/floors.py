"""Gmsh-based floor meshing for CSi SAFE slab models.

SAFE exports floor slabs as *objects* (arbitrary ``n``-gon areas with openings,
drop panels and stiff column zones) plus a single ``AUTOMATIC SLAB MESH
OPTIONS`` intent — it never pre-meshes.  The OpenSees shell builder only accepts
3- or 4-node elements, so an ``n``-gon slab area cannot be built directly.  This
module turns the per-level planar union of floor areas into a conforming
quad-dominant mesh of sub-``AreaElement``\\ s, in the same
``(area_elements, area_assignments, nodes, next_tag)`` contract as
:func:`fea_toolkit.model.geometry_mesh.mesh_area_elements`.

What it does per level (a set of coplanar, horizontal floor areas):

* each floor area polygon becomes a gmsh ``PlaneSurface``,
* each opening (void) polygon is cut out as a hole in the surface that contains
  it — and a floor union's nested boundary loops (an island) are resolved by
  nesting depth: an odd-depth loop is a hole, an even-depth loop is a surface,
* null (``"NONE"`` / load-patch) area boundary edges are *embedded* as mesh
  constraints so the patch boundary is tiled exactly for
  :func:`fea_toolkit.model.geometry_frames.transfer_null_area_loads`,
* beam frame edges (frames that carry a section assignment) and column / wall
  support points are *embedded* as 1-D / 0-D entities so the mesh conforms to
  them,
* the 2-D mesh is recombined into quads and every resulting element inherits the
  section assignment (thickness) of the parent floor polygon that contains its
  centroid.

Areas that are not approximately horizontal (e.g. a sloped ramp) are left
unmeshed and reported via the returned ``skipped`` list rather than being
silently corrupted.

Requires ``gmsh`` (``pip install fea_toolkit[mesh-remesh]``); imported lazily so
the core package never needs it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

import numpy as np

from ..model.openings import point_in_polygon_2d
from ..model.sap_data import AreaElement, Node


def _check_gmsh() -> bool:
    try:
        import gmsh  # noqa: F401

        return True
    except ImportError:
        return False


def _signed_area(poly: list[tuple[float, float]]) -> float:
    """Shoelace signed area of a 2-D polygon (positive → CCW)."""
    s = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _poly_2d(node_ids: list[str], nodes: dict[str, Node]) -> list[tuple[float, float]]:
    """Project an area's corner nodes to the (x, y) plane for 2-D tests."""
    pts: list[tuple[float, float]] = []
    for nid in node_ids:
        nd = nodes.get(nid)
        if nd is not None:
            pts.append((float(nd.x), float(nd.y)))
    return pts


def _chain_loops(edges: list[tuple[tuple, tuple]]) -> list[list[tuple]]:
    """Chain a set of undirected edges into closed vertex-ordered loops.

    Boundary vertices of a polygon union have degree two, so a simple walk
    reconstructs each loop.  Returns the vertex coordinate tuple of each loop.
    """
    from collections import defaultdict

    adj: dict[tuple, list[tuple]] = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    remaining = {frozenset(e) for e in edges}
    loops: list[list[tuple]] = []
    while remaining:
        a, b = tuple(remaining.pop())
        loop = [a, b]
        cur = b
        while cur != a:
            nxt = None
            for nb in adj.get(cur, []):
                if frozenset((cur, nb)) in remaining:
                    nxt = nb
                    break
            if nxt is None:
                break
            remaining.remove(frozenset((cur, nxt)))
            loop.append(nxt)
            cur = nxt
        if loop and loop[0] == loop[-1]:
            loop.pop()  # drop the closing duplicate of the start vertex
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def mesh_floors(
    area_elements: dict[str, AreaElement],
    area_assignments: dict[str, str],
    nodes: dict[str, Node],
    floor_mesh_options: Optional[dict[str, Any]] = None,
    area_roles: Optional[dict[str, str]] = None,
    frame_elements: Optional[dict[str, Any]] = None,
    frame_assignments: Optional[dict[str, str]] = None,
    restraints: Optional[dict[str, Any]] = None,
    sections: Optional[dict[str, Any]] = None,
    next_tag: int = 1,
    target_length: Optional[float] = None,
    recombine: bool = True,
    verbose: bool = False,
    excluded_area_ids: Optional[set[str]] = None,
) -> tuple[dict[str, AreaElement], dict[str, str], dict[str, Node], int, list[str]]:
    """Mesh SAFE floor areas into quads, preserving openings and constraints.

    Args:
        area_elements: ``{area_id: AreaElement}`` (modified in place — each
            meshed parent area is *replaced* by a copy with ``inactive=True``
            via :func:`dataclasses.replace`, and sub-elements are appended).
        area_assignments: ``{area_id: section_name}`` (modified in place).
        nodes: ``{node_id: Node}`` — new mesh nodes are appended here.
        floor_mesh_options: SAFE ``floor_mesh_options``; ``max_size`` drives the
            target element size in model length units.
        area_roles: Optional ``{area_id: "floor" | "wall"}``.  ``"wall"`` areas
            are never floor-meshed.
        frame_elements: ``{elem_id: FrameElement}`` — beams whose edges should
            be preserved in the mesh.
        frame_assignments: ``{elem_id: section_name}`` — only frames with an
            assignment are treated as beam constraints.
        restraints: ``{node_id: Restraint}`` — restrained points (columns /
            walls) are embedded as mesh nodes.
        sections: ``{name: Section}`` — used to resolve overlay precedence from
            each section's ``shape`` (``"Stiff"`` > ``"Drop"`` > anything else).
        next_tag: First free numeric tag for new nodes and elements.
        target_length: Fallback element size when ``floor_mesh_options`` has no
            ``max_size``.
        recombine: Recombine triangles into quads.
        verbose: Show gmsh terminal output.
        excluded_area_ids: Optional ``set[str]`` of area ids to exclude from
            floor meshing (loads-only areas) — they are never converted into
            floor shells.

    Returns:
        ``(area_elements, area_assignments, nodes, next_tag, skipped)`` where
        ``skipped`` lists area ids left unmeshed (non-horizontal).
    """
    if not _check_gmsh():
        raise ImportError(
            "gmsh is required for mesh_floors(). Install with: pip install fea_toolkit[mesh-remesh]"
        )
    import gmsh

    opts = floor_mesh_options or {}
    lc = float(opts.get("max_size") or target_length or 0.0)
    if lc <= 0.0:
        raise ValueError(
            "mesh_floors() needs a positive element size (floor_mesh_options['max_size'])."
        )

    roles = area_roles or {}
    excluded_ids = excluded_area_ids or set()
    opening_ids = {aid for aid, a in area_elements.items() if getattr(a, "opening", False)}
    null_ids = {aid for aid, a in area_elements.items() if getattr(a, "null", False)}

    # ── Partition floor vs opening areas by rounded Z level ──────────
    def _level_of(aid: str) -> Optional[float]:
        area = area_elements.get(aid)
        if area is None:
            return None
        zs = [nodes[n].z for n in area.node_ids if n in nodes]
        return round(float(sum(zs) / len(zs)), 3) if zs else None

    floors_by_level: dict[float, list[str]] = {}
    openings_by_level: dict[float, list[str]] = {}
    nulls_by_level: dict[float, list[str]] = {}
    skipped: list[str] = []

    for aid, area in area_elements.items():
        if getattr(area, "inactive", False):
            continue
        if aid in excluded_ids:
            continue  # loads-only area: never a floor shell
        if aid in null_ids:
            z = _level_of(aid)
            if z is not None:
                nulls_by_level.setdefault(z, []).append(aid)
            continue  # geometric object: not a shell, not a hole (loads deferred)
        if aid in opening_ids:
            z = _level_of(aid)
            if z is not None:
                openings_by_level.setdefault(z, []).append(aid)
            continue
        if roles.get(aid) == "wall":
            continue
        nids = [n for n in area.node_ids if n in nodes]
        zs = [nodes[n].z for n in nids]
        if not zs:
            continue
        if max(zs) - min(zs) > 1e-6:
            skipped.append(aid)
            continue
        floors_by_level.setdefault(round(float(sum(zs) / len(zs)), 3), []).append(aid)

    if not floors_by_level:
        return area_elements, area_assignments, nodes, next_tag, skipped

    # ── Coordinate registry (global, seeded from existing nodes) ──
    def _key(x: float, y: float, z: float) -> tuple:
        return (round(x, 6), round(y, 6), round(z, 6))

    coord_to_id: dict[tuple, str] = {_key(nd.x, nd.y, nd.z): nid for nid, nd in nodes.items()}

    existing_area_tags = {a.area_tag for a in area_elements.values()}
    existing_node_tags = {nd.node_tag for nd in nodes.values()}
    # One shared counter for every new node and sub-AreaElement, seeded above
    # both the existing node and area tags and the caller's ``next_tag`` hint.
    next_free_tag = max(max(existing_area_tags | existing_node_tags, default=0) + 1, next_tag)

    def _mesh_level(
        level_z: float, floor_ids: list[str], open_ids: list[str], null_ids_at_level: list[str]
    ) -> None:
        """Build and mesh one level's planar floor union in the current gmsh model."""
        nonlocal next_free_tag

        # ── 2-D polygons (XY projection) for containment tests ──
        floor_polys: dict[str, list[tuple[float, float]]] = {}
        for aid in floor_ids:
            poly = _poly_2d(area_elements[aid].node_ids, nodes)
            if len(poly) >= 3:
                floor_polys[aid] = poly
        open_polys: dict[str, list[tuple[float, float]]] = {}
        for aid in open_ids:
            poly = _poly_2d(area_elements[aid].node_ids, nodes)
            if len(poly) >= 3:
                open_polys[aid] = poly

        # ── Overlay precedence (Stiff > Drop > Slab) ──
        def _prec(aid: str) -> int:
            sec = (sections or {}).get(area_assignments.get(aid, ""))
            shape = getattr(sec, "shape", "") if sec is not None else ""
            return {"Stiff": 3, "Drop": 2}.get(shape, 1)

        # ── Union outer boundary from undirected edge counts ──
        edge_count: dict[frozenset, int] = {}
        edge_owner: dict[frozenset, str] = {}
        for aid, poly in floor_polys.items():
            n = len(poly)
            for i in range(n):
                a = (poly[i][0], poly[i][1], level_z)
                b = (poly[(i + 1) % n][0], poly[(i + 1) % n][1], level_z)
                if a != b:
                    key = frozenset((a, b))
                    edge_count[key] = edge_count.get(key, 0) + 1
                    edge_owner[key] = aid

        outer_edges: set[frozenset] = set()
        for edge, cnt in edge_count.items():
            if cnt != 1:
                continue
            a, b = tuple(edge)
            mx = (a[0] + b[0]) / 2.0
            my = (a[1] + b[1]) / 2.0
            owner = edge_owner[edge]
            if any(
                point_in_polygon_2d((mx, my), p) for aid, p in floor_polys.items() if aid != owner
            ):
                continue  # overlay edge inside another region
            outer_edges.add(edge)

        outer_loops = _chain_loops([tuple(e) for e in outer_edges])
        if not outer_loops:
            return

        # ── Add points and lines (dedup by coordinate / undirected edge) ──
        pt_tag: dict[tuple, int] = {}

        def _point(x: float, y: float, z: float) -> int:
            k = _key(x, y, z)
            tag = pt_tag.get(k)
            if tag is None:
                tag = gmsh.model.geo.add_point(x, y, z, lc)
                pt_tag[k] = tag
            return tag

        edge_lines: dict[frozenset, int] = {}

        def _line(a: tuple, b: tuple) -> int:
            key = frozenset((a, b))
            line = edge_lines.get(key)
            if line is None:
                if a > b:
                    a, b = b, a  # canonical direction: lexicographically smaller first
                line = gmsh.model.geo.add_line(_point(*a), _point(*b))
                edge_lines[key] = line
            return line

        def _curve_loop(verts: list[tuple]) -> int:
            signed: list[int] = []
            n = len(verts)
            for i in range(n):
                a, b = verts[i], verts[(i + 1) % n]
                line = _line(a, b)
                signed.append(line if a < b else -line)
            return gmsh.model.geo.add_curve_loop(signed)

        # ── Outer surfaces (CCW) with nested holes and opening voids (CW) ──
        # A floor union can be a ring: its outer boundary encloses an inner
        # boundary loop (an island such as a courtyard).  A loop's nesting depth
        # decides its role — an even-depth loop is a *surface*, an odd-depth
        # loop is a *hole* in its parent surface (added clockwise, with no
        # ``PlaneSurface`` of its own).  The direct parent is the *smallest*
        # loop that strictly encloses the candidate, tested with the candidate's
        # own vertices: they lie on its boundary, so they can never fall inside
        # one of its own holes and make an inner loop claim an outer one that
        # encloses it.
        norm_loops: list[tuple[list[tuple], list[tuple[float, float]]]] = []
        for loop in outer_loops:
            verts = list(loop)
            poly = [(v[0], v[1]) for v in verts]
            if _signed_area(poly) < 0.0:
                verts.reverse()
                poly.reverse()
            norm_loops.append((verts, poly))

        areas = [abs(_signed_area(poly)) for _verts, poly in norm_loops]
        parent_of: dict[int, int] = {}
        for i, (_vi, poly_i) in enumerate(norm_loops):
            best_area: Optional[float] = None
            for j, (_vj, poly_j) in enumerate(norm_loops):
                if j == i or areas[j] <= areas[i]:
                    continue  # an enclosing loop must be strictly larger
                if not any(point_in_polygon_2d(v, poly_j) for v in poly_i):
                    continue
                if best_area is None or areas[j] < best_area:
                    parent_of[i] = j
                    best_area = areas[j]

        depth_of: dict[int, int] = {}
        for i in range(len(norm_loops)):
            depth = 0
            cur = i
            while cur in parent_of:
                cur = parent_of[cur]
                depth += 1
            depth_of[i] = depth

        nested_holes: dict[int, list[int]] = {}
        for i, (verts, _poly) in enumerate(norm_loops):
            if depth_of[i] % 2 == 0:
                continue  # a surface (outer or island), not a hole
            # Clockwise curve loop (relative to the CCW parent surface).
            ov = [(v[0], v[1], level_z) for v in verts]
            ov.reverse()
            nested_holes.setdefault(parent_of[i], []).append(_curve_loop(ov))

        outer_surfaces: list[tuple[int, list[tuple[float, float]]]] = []
        for i, (verts, poly) in enumerate(norm_loops):
            if depth_of[i] % 2 == 1:
                continue  # a hole, not a surface of its own
            hole_loops: list[int] = list(nested_holes.get(i, []))
            for opoly in open_polys.values():
                cx = sum(p[0] for p in opoly) / len(opoly)
                cy = sum(p[1] for p in opoly) / len(opoly)
                if point_in_polygon_2d((cx, cy), poly):
                    ov = [(p[0], p[1], level_z) for p in opoly]
                    if _signed_area(opoly) > 0.0:
                        ov.reverse()
                    hole_loops.append(_curve_loop(ov))
            surf = gmsh.model.geo.add_plane_surface([_curve_loop(verts), *hole_loops])
            outer_surfaces.append((surf, poly))

        def _surface_for(pt: tuple[float, float]) -> Optional[int]:
            for surf, poly in outer_surfaces:
                if point_in_polygon_2d(pt, poly):
                    return surf
            return None

        # ── Constraint edges: internal tiling + overlay boundaries ──
        line_to_surf: dict[int, int] = {}
        point_to_surf: dict[int, int] = {}
        for edge in set(edge_count) - outer_edges:
            a, b = tuple(edge)
            surf = _surface_for(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
            if surf is None:
                continue
            line_to_surf[_line(a, b)] = surf

        # ── Null (load-patch) boundary edges: embedded so the mesh tiles each
        # patch boundary exactly — ``transfer_null_area_loads`` re-parents a
        # null area's pressure onto the slab sub-elements whose centroid lies
        # inside it, which is only exact when the patch boundary is a mesh
        # edge. ──
        for aid in null_ids_at_level:
            poly = _poly_2d(area_elements[aid].node_ids, nodes)
            n = len(poly)
            if n < 3:
                continue
            for i in range(n):
                a = (poly[i][0], poly[i][1], level_z)
                b = (poly[(i + 1) % n][0], poly[(i + 1) % n][1], level_z)
                if a == b:
                    continue
                surf = _surface_for(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
                if surf is None:
                    continue
                line_to_surf.setdefault(_line(a, b), surf)

        # ── Beam constraint lines (frames with a section assignment) ──
        if frame_elements:
            for fid, fe in frame_elements.items():
                if getattr(fe, "inactive", False):
                    continue
                if frame_assignments and not frame_assignments.get(fid):
                    continue
                na = nodes.get(getattr(fe, "node_i", ""))
                nb = nodes.get(getattr(fe, "node_j", ""))
                if na is None or nb is None:
                    continue
                if abs(na.z - level_z) > 1e-6 or abs(nb.z - level_z) > 1e-6:
                    continue
                surf = _surface_for(((na.x + nb.x) / 2.0, (na.y + nb.y) / 2.0))
                if surf is None:
                    continue
                line = _line((na.x, na.y, na.z), (nb.x, nb.y, nb.z))
                line_to_surf.setdefault(line, surf)

        # ── Embedded column / wall points (restrained nodes) ──
        if restraints:
            for nid in restraints:
                nd = nodes.get(nid)
                if nd is None or abs(nd.z - level_z) > 1e-6:
                    continue
                surf = _surface_for((nd.x, nd.y))
                if surf is None:
                    continue
                point_to_surf.setdefault(_point(nd.x, nd.y, nd.z), surf)

        gmsh.model.geo.synchronize()
        for p, s in point_to_surf.items():
            gmsh.model.mesh.embed(0, [p], 2, s)
        for line, s in line_to_surf.items():
            gmsh.model.mesh.embed(1, [line], 2, s)

        # ── Mesh settings + 2-D generation ──
        # A wide Min/Max band lets gmsh refine around the embedded constraints
        # (beams, columns, opening edges) even when ``lc`` is much larger than
        # the feature spacing — a coarse ``max_size`` otherwise sends the
        # frontal Delaunay into a very slow reconciliation.
        gmsh.option.set_number("Mesh.CharacteristicLengthMin", lc * 0.02)
        gmsh.option.set_number("Mesh.CharacteristicLengthMax", lc)
        gmsh.option.set_number("Mesh.Algorithm", 6)  # Frontal Delaunay
        if recombine:
            gmsh.option.set_number("Mesh.RecombineAll", 1)
        gmsh.model.mesh.generate(2)

        # ── Map gmsh nodes back to fea_toolkit nodes ──
        gmsh_node_tags, coords, _ = gmsh.model.mesh.get_nodes()
        coord_map = dict(zip(gmsh_node_tags, coords.reshape((-1, 3))))
        sub_node_map: dict[int, str] = {}
        for gtag in gmsh_node_tags:
            c = coord_map[gtag]
            k = _key(float(c[0]), float(c[1]), float(c[2]))
            existing = coord_to_id.get(k)
            if existing is not None:
                sub_node_map[gtag] = existing
                continue
            nid = f"_floor_{next_free_tag}"
            nodes[nid] = Node(
                node_id=nid,
                node_tag=next_free_tag,
                x=float(c[0]),
                y=float(c[1]),
                z=float(c[2]),
            )
            coord_to_id[k] = nid
            sub_node_map[gtag] = nid
            next_free_tag += 1

        # ── Extract 2-D elements (quads + triangles) ──
        elem_types, _elem_tags, elem_node_tags = gmsh.model.mesh.get_elements(2)
        cells: list[list[int]] = []
        for etype, flat in zip(elem_types, elem_node_tags):
            k = 4 if etype == 3 else 3 if etype == 2 else None
            if k is None:
                continue
            cells.extend(np.asarray(flat).reshape((-1, k)).tolist())

        # ── Emit sub-AreaElements (thickness by overlay precedence) ──
        for cell in cells:
            ctags = [sub_node_map[int(t)] for t in cell]
            cc = [coord_map[int(t)] for t in cell]
            cx = sum(float(c[0]) for c in cc) / len(cc)
            cy = sum(float(c[1]) for c in cc) / len(cc)
            winner: Optional[str] = None
            best = -1
            for aid, poly in floor_polys.items():
                prec = _prec(aid)
                if prec > best and point_in_polygon_2d((cx, cy), poly):
                    winner = aid
                    best = prec
            if winner is None:
                continue
            sec_name = area_assignments.get(winner, "")
            thickness = getattr(area_elements[winner], "thickness", 0.0)
            sub_id = f"{winner}_floor_{next_free_tag}"
            area_elements[sub_id] = AreaElement(
                area_id=sub_id, area_tag=next_free_tag, node_ids=ctags, thickness=thickness
            )
            if sec_name:
                area_assignments[sub_id] = sec_name
            next_free_tag += 1

        # ── Deactivate every meshed parent area (replaced by the level mesh) ──
        # ``replace`` (not ``area_elements[aid].inactive = True``) keeps this
        # copy-on-write: the parent AreaElement may be shared with the caller's
        # SAPModelData.
        if cells:
            for aid in floor_ids:
                area_elements[aid] = replace(area_elements[aid], inactive=True)

    gmsh.initialize()
    if not verbose:
        gmsh.option.set_number("General.Terminal", 0)
    try:
        for level_z in sorted(floors_by_level):
            floor_ids = floors_by_level[level_z]
            open_ids = openings_by_level.get(level_z, [])
            null_ids_at_level = nulls_by_level.get(level_z, [])
            _mesh_level(level_z, floor_ids, open_ids, null_ids_at_level)
            gmsh.model.remove()
    finally:
        gmsh.finalize()

    return area_elements, area_assignments, nodes, next_free_tag, skipped
