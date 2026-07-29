"""Visualisation helpers for fea_toolkit models and results.

Two backends are supported:

* **PyVista** — interactive 3D model view and deformed shape.
* **Matplotlib** — 2D force / moment diagrams along element height.

All functions gracefully fall back to a warning if the required package
is not installed.
"""

import warnings
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING
import math
import numpy as np

from ..model.geometry import get_SAP_vecxz, get_local_axes
from ..model.sap_data import SAPModelData
from ..utils import compute_flag_parts

# Types that represent NPZ/HDF5 results data (dict or Numpy NpzFile).
# Used by isinstance() checks throughout this module to dispatch between
# NPZ-data paths and builder/model paths.
_NPZ_TYPES = (dict, np.lib.npyio.NpzFile)

if TYPE_CHECKING:
    from ..model.selection import Selection


def _set_isometric_view(plotter) -> None:
    """Set an isometric view that works for any model (including 1D columns).

    Also enables terrain-style interaction so Z stays vertical when the
    user rotates the view with the mouse.
    """
    bounds = plotter.bounds
    z_range = max(bounds[5] - bounds[4], 1.0)
    x_range = max(bounds[1] - bounds[0], 0.1)
    y_range = max(bounds[3] - bounds[2], 0.1)
    horiz = max(x_range, y_range)
    cx = (bounds[0] + bounds[1]) * 0.5
    cy = (bounds[2] + bounds[3]) * 0.5
    cz = (bounds[4] + bounds[5]) * 0.5
    dist = max(horiz, z_range) * 1.5
    plotter.camera_position = [
        (cx + dist, cy + dist, cz + dist * 0.4),
        (cx, cy, cz),
        (0.0, 0.0, 1.0),
    ]
    plotter.enable_terrain_style(mouse_wheel_zooms=True, shift_pans=True)


# ============================================================================
# Shared mesh-construction helpers
# ============================================================================

def _build_deformed_mesh(
    segments: list,
    seg_npoints: list,
    all_quads: list,
    sec_idxs: list,
    scale: float,
    amp: float,
    shrink: float = 0.0,
) -> "tuple[Any, Optional[Any]]":
    """Build a single merged ``PolyData`` from frame-segment and shell-quad
    geometry, displaced by ``scale * amp`` along each element's eigenvector.

    Shell quads become ``[4, i, j, k, l]`` faces (not triangulated), so
    each quad maps to exactly one face — no cell‑count mismatch.

    Parameters
    ----------
    segments : list of (p1, p2, di, dj) tuples
        Frame segment data — each entry holds the two endpoints and their
        displacement vectors.
    seg_npoints : list of int
        Number of subdivision points per segment (fixed from undeformed
        length, so point count is invariant w.r.t. *amp*).
    all_quads : list of (p1, p2, p3, p4, d1, d2, d3, d4) tuples
        Shell quad data — four corners and their displacement vectors.
    sec_idxs : list of int
        Per-quad section index (one per entry in *all_quads*).  Used to
        assign a ``section_idx`` cell scalar on the shell mesh.
    scale : float
        Base scale factor from the eigenvector normalisation.
    amp : float
        Amplitude multiplier for the current frame.
    shrink : float
        Fraction to shrink shell quads toward their centroid
        (0.0 = full size, 0.1 = 10% gap at edges).  Applied to the
        rest-position vertices before displacement so the point-count
        invariant is preserved for animation.

    Returns
    -------
    tuple[pv.PolyData, pv.PolyData | None]
        ``(frame_mesh, shell_mesh)`` — frame mesh contains ``lines`` only;
        shell mesh contains quad ``faces`` with a ``section_idx`` cell
        scalar.  *shell_mesh* is ``None`` when there are no shell elements.
    """
    import pyvista as pv

    all_pts: list = []
    all_lines: list = []
    all_faces: list = []
    offset = 0

    # Frame lines
    for (p1, p2, di, dj), n in zip(segments, seg_npoints):
        d1 = np.array(di) * scale * amp
        d2 = np.array(dj) * scale * amp
        a = p1 + d1
        b = p2 + d2
        pts = np.linspace(a, b, n)
        all_pts.append(pts)
        for i in range(n - 1):
            all_lines.append([2, offset + i, offset + i + 1])
        offset += n

    n_frame_pts = offset

    # Shell quads — all faces are [4, i, j, k, l].  Use a shell-local
    # vertex offset (starting at 0) since the shell mesh will be created
    # from ``verts[n_frame_pts:]`` with its own 0‑based indexing.
    shell_offset = 0
    for quad in all_quads:
        p1, p2, p3, p4, d1, d2, d3, d4 = quad
        # Apply shrink to rest positions (before displacement) to
        # preserve point-count invariance for animation.
        if shrink:
            c = (p1 + p2 + p3 + p4) / 4
            p1 = p1 + (c - p1) * shrink
            p2 = p2 + (c - p2) * shrink
            p3 = p3 + (c - p3) * shrink
            p4 = p4 + (c - p4) * shrink
        a1 = p1 + d1 * scale * amp
        a2 = p2 + d2 * scale * amp
        a3 = p3 + d3 * scale * amp
        a4 = p4 + d4 * scale * amp
        all_pts.extend([a1, a2, a3, a4])
        all_faces.append([4, shell_offset, shell_offset + 1,
                          shell_offset + 2, shell_offset + 3])
        shell_offset += 4
        offset += 4  # still track global offset for verts partitioning

    if not all_pts:
        return pv.PolyData(), None

    verts = np.vstack(all_pts)

    # ── Frame mesh ──
    frame_mesh = pv.PolyData()
    if n_frame_pts > 0:
        cells = (np.array(all_lines, dtype=int) if all_lines
                 else np.empty((0, 3), dtype=int))
        fm = pv.PolyData(verts[:n_frame_pts])
        if len(cells) > 0:
            fm.lines = cells
        frame_mesh = fm

    # ── Shell mesh ──
    shell_mesh: Optional[pv.PolyData] = None
    if all_faces:
        # Build (N, 5) array — set faces BEFORE points so PyVista 0.48
        # correctly interprets the face structure.
        faces = np.array(all_faces, dtype=int)
        sm = pv.PolyData()
        sm.faces = faces
        sm.points = verts[n_frame_pts:]
        if sec_idxs:
            # Each quad → 1 face → 1 cell_data entry (no mismatch)
            sm.cell_data['section_idx'] = np.array(sec_idxs, dtype=int)
        shell_mesh = sm

    return frame_mesh, shell_mesh


# ============================================================================
# 3D model view (PyVista)
# ============================================================================

def plot_model_3d(
    builder,
    show_nodes: bool = True,
    show_labels: bool = False,
    color_by_section: bool = True,
    selection: Optional['Selection'] = None,
    show_constraints: bool = False,
    show_mesh_nodes: bool = False,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Display the model in an interactive 3D view using PyVista.

    .. deprecated::
       Use :func:`plot_mesh` instead — it works with both builder
       and NPZ data and supports all features of this function.

    Args:
        builder: An ``AnalysisBuilder`` instance that has been built.
        show_nodes: If True, draw node markers.
        show_labels: If True, label nodes with their tags.
        color_by_section: If True, colour elements by section name.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        show_constraints: If True, draw edge constraint lines as wide
            transparent yellow lines between master and slave nodes.
        show_mesh_nodes: If True, highlight mesh-created nodes (IDs
            containing ``_mesh_``) as green spheres.
        notebook: If True, return a plotter suitable for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pyvista.Plotter`` if *notebook* is True (for inline display),
        otherwise ``None`` (interactive window opens).

    Requires:
        ``pyvista`` — install via ``pip install pyvista``.
    """
    warnings.warn(
        "plot_model_3d is deprecated — use plot_mesh() instead "
        "(works with builder or NPZ data)",
        DeprecationWarning, stacklevel=2,
    )
    try:
        import pyvista as pv
    except ImportError:
        print("Warning: pyvista not installed.  Install with: pip install pyvista")
        return None

    # Build mesh
    pv.set_plot_theme("document")

    # Collect element lines
    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}
    lines = []
    _assignments = (builder.split_assignments if builder.split_elements
                    else builder.model.frame_assignments)

    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        sec = (_assignments or {}).get(eid, '?')
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        lines.append((p1, p2, sec))

    # Assign a colour per unique section
    all_secs = sorted({s for _, _, s in lines})
    if color_by_section and len(all_secs) > 1:
        cmap = [
            '#4c72b0', '#dd8452', '#55a868', '#c44e52', '#8172b3',
            '#937860', '#da8bc3', '#8c8c8c', '#ccb974', '#64b5cd',
        ]
        sec_colour = {s: cmap[i % len(cmap)] for i, s in enumerate(all_secs)}
    else:
        sec_colour = {s: '#4c72b0' for s in all_secs}

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # Add elements
    for p1, p2, sec in lines:
        n_pts = max(2, int(np.linalg.norm(p2 - p1) * 2))
        pts = np.linspace(p1, p2, n_pts)
        poly = pv.lines_from_points(pts)
        colour = sec_colour.get(sec, '#4c72b0')
        plotter.add_mesh(poly, color=colour, line_width=4,
                         label=sec if color_by_section else None)

    if color_by_section and len(all_secs) > 1:
        plotter.add_legend()

    # ── Shell / area elements ────────────────────────────────────
    # Active (sub-)elements: solid with section colours.
    # Inactive parents: grey wireframe (original SAP data overlay).
    if builder.model.area_elements:
        # Resolve selection for area filtering (if provided)
        sel_area_ids: Optional[Set[str]] = None
        if selection is not None:
            sel_area_ids = set(selection.get_area_ids(builder.model))

        # Collect active and inactive areas
        active_quads: Dict[str, list] = {}   # sec_name → [quad points]
        inactive_quads: list = []             # parent quads (grey overlay)

        for aid, area in builder.model.area_elements.items():
            if sel_area_ids is not None and aid not in sel_area_ids:
                continue
            if len(area.node_ids) < 3:
                continue
            nids = area.node_ids[:4]
            pts = []
            for nid in nids:
                nd = builder.model.nodes.get(nid)
                if nd is None:
                    break
                pts.append([nd.x, nd.y, nd.z])
            if len(pts) < 3:
                continue
            # Pad triangle to quad
            while len(pts) < 4:
                pts.append(pts[-1])

            is_inactive = getattr(area, 'inactive', False)
            if is_inactive:
                inactive_quads.append(np.array(pts))
            else:
                sec_name = builder.model.area_assignments.get(aid, 'unknown')
                active_quads.setdefault(sec_name, []).append(np.array(pts))

        # Render inactive (parent) quads as grey wireframe — Approach B
        if inactive_quads:
            for quad_pts in inactive_quads:
                face = pv.PolyData(quad_pts, faces=[4, 0, 1, 2, 3])
                plotter.add_mesh(face, color='lightgrey', opacity=0.15,
                                 show_edges=True, edge_color='grey',
                                 line_width=0.5)

        # Render active (sub-element) quads
        if active_quads:
            _shell_secs = sorted(active_quads.keys())
            _shell_cmap = [
                '#4c72b0', '#dd8452', '#55a868', '#c44e52', '#8172b3',
                '#937860', '#da8bc3', '#8c8c8c', '#ccb974', '#64b5cd',
            ]
            _shell_colour = {s: _shell_cmap[i % len(_shell_cmap)]
                            for i, s in enumerate(_shell_secs)}

            for sec_name, quads in active_quads.items():
                if color_by_section:
                    colour = _shell_colour.get(sec_name, '#4c72b0')
                else:
                    colour = '#4c72b0'  # neutral blue, matching frame default
                for quad_pts in quads:
                    pts = np.array(quad_pts)
                    is_tri = np.allclose(pts[2], pts[3])
                    if is_tri:
                        face = pv.PolyData(pts[:3], faces=[3, 0, 1, 2])
                    else:
                        face = pv.PolyData(pts, faces=[4, 0, 1, 2, 3])
                    plotter.add_mesh(face, color=colour, opacity=0.6,
                                     show_edges=True, edge_color=colour,
                                     line_width=1.0,
                                     label=sec_name if color_by_section else None)

    # Add nodes
    if show_nodes:
        node_pts = np.array([
            [n.x, n.y, n.z] for n in builder.model.nodes.values()
        ])
        if len(node_pts):
            cloud = pv.PolyData(node_pts)
            plotter.add_mesh(cloud, color='black', point_size=8,
                             render_points_as_spheres=True)

    # ── Mesh-created nodes (green spheres) ──────────────────────
    if show_mesh_nodes:
        mesh_pts = np.array([
            [n.x, n.y, n.z] for nid, n in builder.model.nodes.items()
            if "_mesh_" in nid
        ])
        if len(mesh_pts):
            cloud = pv.PolyData(mesh_pts)
            plotter.add_mesh(cloud, color='green', point_size=12,
                             render_points_as_spheres=True)

    # ── Edge constraint lines (wide transparent yellow) ─────────
    if show_constraints:
        # Try MeshModel detected_edge_pairs first (preprocessor path)
        raw_pairs = []
        mesh_model = getattr(builder, '_mesh_model', None)
        if mesh_model is not None:
            raw_pairs = getattr(mesh_model, 'detected_edge_pairs', [])
        # Fall back to builder's _saved_edge_constraints (legacy path)
        if not raw_pairs:
            raw_pairs = getattr(builder, '_saved_edge_constraints', [])
        if raw_pairs:
            lines_poly = []
            for entry in raw_pairs:
                if not isinstance(entry, tuple) or len(entry) < 2:
                    continue
                # find_constraint_edges returns (nids, master_chain, slave_nodes, ...)
                # master_chain = [(node_id, t), ...], slave_nodes = [(node_id, t), ...]
                master_nodes = entry[1] if len(entry) > 1 else []
                slave_nodes = entry[2] if len(entry) > 2 else []
                for mn in master_nodes:
                    nid = mn[0] if isinstance(mn, (list, tuple)) else mn
                    cnode = builder.model.nodes.get(nid) if isinstance(nid, str) else None
                    if cnode is None:
                        continue
                    for sn in slave_nodes:
                        sid = sn[0] if isinstance(sn, (list, tuple)) else sn
                        fnode = builder.model.nodes.get(sid) if isinstance(sid, str) else None
                        if fnode is None:
                            continue
                        lines_poly.append([cnode.x, cnode.y, cnode.z,
                                           fnode.x, fnode.y, fnode.z])
            if lines_poly:
                pts = np.array(lines_poly).reshape(-1, 3)
                n_lines = len(lines_poly)
                connectivity = np.column_stack([
                    np.full(n_lines, 2, dtype=int),
                    np.arange(0, 2 * n_lines, 2, dtype=int),
                    np.arange(1, 2 * n_lines + 1, 2, dtype=int),
                ]).ravel()
                poly = pv.PolyData(pts, lines=connectivity)
                plotter.add_mesh(poly, color='yellow', opacity=0.6,
                                 line_width=10)

    # Labels
    if show_labels:
        for nid, node in builder.model.nodes.items():
            plotter.add_point_labels(
                np.array([[node.x, node.y, node.z]]),
                [str(node.node_tag)],
                font_size=10, point_size=0,
            )

    plotter.show_grid()
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


# ============================================================================
# Unified mesh visualisation (builder or NPZ data)
# ============================================================================

def _collapse_to_parents(data, source):
    """Post‑process mesh data to replace child elements with their parents.

    For each frame/shell entry with a non‑empty ``parent`` field, children
    are removed and replaced by a single entry representing the original
    unsplit parent element.  Unsplit elements pass through unchanged.

    Parameters
    ----------
    data : dict
        Mesh data dict from :func:`_resolve_mesh_data` (builder or NPZ path).
    source : object
        Original data source — used to resolve parent geometry.

    Returns
    -------
    dict
        Updated mesh data dict with children collapsed into parents.
    """
    import numpy as np
    from ..model.sap_data import SAPModelData

    nodes = data["nodes"]

    # ── Collapse frames ─────────────────────────────────────────
    # Build group: parent_id -> [child_frame_entries]
    parent_groups: Dict[str, list] = {}
    for fr in data["frames"]:
        pid = fr.get("parent")
        if pid and pid != "?" and pid != "" and pid is not None:
            parent_groups.setdefault(pid, []).append(fr)

    if parent_groups:
        collapsed_frames = []
        seen_parents: set = set()

        # Build a node-tag-to-SAP-id lookup for resolving parent endpoints
        # from NPZ data (nodes are already populated by _resolve_mesh_data).
        tag_to_sid: Dict[int, str] = {}
        for sid, nd in nodes.items():
            tag_to_sid[nd["tag"]] = sid

        # For builder sources, build a full parent endpoint lookup from model
        parent_frame_endpoints: Dict[str, tuple] = {}
        if not isinstance(source, dict) and hasattr(source, 'model') or \
           (hasattr(source, 'frame_elements') and not isinstance(source, dict)):
            if hasattr(source, 'model'):
                model = source.model
            elif hasattr(source, 'mesh_model'):
                model = source.mesh_model
            elif isinstance(source, SAPModelData):
                model = source
            else:
                model = source
            for eid, elem in model.frame_elements.items():
                if getattr(elem, 'inactive', False):
                    parent_frame_endpoints[eid] = (elem.node_i, elem.node_j)

        # For NPZ sources, read parent endpoints from the new arrays
        # Keyed by parent_sap_id so the collapse_to_parents lookup by pid works.
        npz_parent_node_i: Dict[str, int] = {}
        npz_parent_node_j: Dict[str, int] = {}
        if isinstance(source, dict):
            nf = len(source.get("frame_eid", []))
            for i in range(nf):
                pid = str(source.get("frame_parent_sap_id", [""]*nf)[i])
                sid = str(source["frame_sap_id"][i])
                pni = int(source.get("frame_parent_node_i", [0]*nf)[i])
                pnj = int(source.get("frame_parent_node_j", [0]*nf)[i])
                if pid and pni != 0 and pnj != 0:
                    npz_parent_node_i[pid] = pni
                    npz_parent_node_j[pid] = pnj
                elif not pid or pid == sid:
                    # unsplit elements store their own node tags
                    pass

        for fr in data["frames"]:
            pid = fr.get("parent")
            if pid and pid != "?" and pid != "" and pid is not None:
                if pid in seen_parents:
                    continue  # already added
                seen_parents.add(pid)
                children = parent_groups.get(pid, [])

                # Resolve parent endpoints
                if not isinstance(source, dict):
                    # Builder path
                    p_ni_id, p_nj_id = parent_frame_endpoints.get(pid, (None, None))
                    if p_ni_id and p_nj_id:
                        p_ni = nodes.get(p_ni_id)
                        p_nj = nodes.get(p_nj_id)
                        if p_ni and p_nj:
                            collapsed_frames.append({
                                "id": pid,
                                "ni_id": p_ni_id,
                                "nj_id": p_nj_id,
                                "sec": children[0].get("sec", '?'),
                                "parent": None,
                            })
                            continue
                    # Fallback: derive from children endpoints
                    sorted_children = _sort_children_by_location(children, nodes)
                    if sorted_children:
                        first = sorted_children[0]
                        last = sorted_children[-1]
                        collapsed_frames.append({
                            "id": pid,
                            "ni_id": first.get("ni_id"),
                            "nj_id": last.get("nj_id"),
                            "ni_tag": first.get("ni_tag"),
                            "nj_tag": last.get("nj_tag"),
                            "sec": children[0].get("sec", '?'),
                            "parent": None,
                        })
                else:
                    # NPZ path — use parent node tags from NPZ arrays
                    p_ni_tag = npz_parent_node_i.get(pid, 0)
                    p_nj_tag = npz_parent_node_j.get(pid, 0)
                    if p_ni_tag and p_nj_tag:
                        collapsed_frames.append({
                            "id": pid,
                            "ni_tag": p_ni_tag,
                            "nj_tag": p_nj_tag,
                            "sec": children[0].get("sec", '?'),
                            "parent": None,
                        })
                    else:
                        # Fallback: derive from sorted children
                        sorted_children = _sort_children_by_location(children, nodes)
                        if sorted_children:
                            first = sorted_children[0]
                            last = sorted_children[-1]
                            collapsed_frames.append({
                                "id": pid,
                                "ni_tag": first.get("ni_tag"),
                                "nj_tag": last.get("nj_tag"),
                                "sec": children[0].get("sec", '?'),
                                "parent": None,
                            })
            else:
                # Unsplit element — pass through
                collapsed_frames.append(fr)
        data["frames"] = collapsed_frames

    # ── Collapse shells ─────────────────────────────────────────
    shell_parent_groups: Dict[str, list] = {}
    for sh in data["shells"]:
        pid = sh.get("parent")
        if pid and pid != "?" and pid != "" and pid is not None:
            shell_parent_groups.setdefault(pid, []).append(sh)

    if shell_parent_groups:
        collapsed_shells = []
        seen_shell_parents: set = set()
        for sh in data["shells"]:
            pid = sh.get("parent")
            if pid and pid != "?" and pid != "" and pid is not None:
                if pid in seen_shell_parents:
                    continue
                seen_shell_parents.add(pid)
                children = shell_parent_groups.get(pid, [])
                # Reconstruct parent from first child's parent reference
                # (parent node IDs are stored in the original area_element)
                parent_node_ids = None
                if not isinstance(source, dict):
                    model = (source.model if hasattr(source, 'model')
                             else source)
                    parent_area = model.area_elements.get(pid)
                    if parent_area is not None:
                        parent_node_ids = parent_area.node_ids[:4]
                if parent_node_ids:
                    collapsed_shells.append({
                        "id": pid,
                        "sec": children[0].get("sec", 'unknown'),
                        "node_ids": parent_node_ids,
                        "inactive": False,
                    })
                else:
                    # Fallback: use first child's geometry
                    collapsed_shells.append(children[0])
            else:
                collapsed_shells.append(sh)
        data["shells"] = collapsed_shells

    return data


def _sort_children_by_location(children, nodes, parent_axis=None):
    """Sort child frame entries by spatial position along the parent axis.

    For NPZ data (using ``ni_tag``/``nj_tag``), computes the midpoint
    along the element axis.  For builder data (using ``ni_id``/``nj_id``),
    sorts by node ID order along the parent span.

    When *parent_axis* is provided (a unit vector), children are sorted
    by their midpoint projected onto the parent axis via dot product,
    rather than using Z-only midpoint.  This gives correct ordering for
    slanted/curved elements that are not vertical.
    """
    import numpy as np

    if not children:
        return children

    def _mid_pos(fr):
        """Compute midpoint of the child element as a 3D point."""
        ni = _resolve_frame_node(nodes, fr, 'i')
        nj = _resolve_frame_node(nodes, fr, 'j')
        if ni and nj:
            return np.array([(ni["x"] + nj["x"]) * 0.5,
                             (ni["y"] + nj["y"]) * 0.5,
                             (ni["z"] + nj["z"]) * 0.5])
        return np.zeros(3)

    if parent_axis is not None:
        # Project midpoint onto parent axis for correct ordering
        return sorted(children, key=lambda fr: np.dot(_mid_pos(fr), parent_axis))
    else:
        # Fallback: Z-only (backward compatible for vertical elements)
        return sorted(children, key=lambda fr: _mid_pos(fr)[2])


def _resolve_mesh_data(source, collapse_to_parents=False):
    """Extract mesh geometry arrays from a builder, SAPModelData, or NPZ data dict.

    Supports three source types:

    * A ``SAPModelData`` instance (raw model, before preprocessing).
    * An ``AnalysisBuilder`` or builder object (after preprocessing).
    * A dict loaded from a unified NPZ file (via ``np.load()``).

    When *collapse_to_parents* is ``True``, child elements resulting from
    splitting are replaced by their original unsplit parent elements, so
    the displayed geometry matches what the engineer drew in SAP2000.

    Returns a dict with keys:
        nodes          – ``{node_id: {tag, x, y, z}}``
        frames         – ``[{id, ni_tag/ni_id, nj_tag/nj_id, sec, parent}]``
        shells         – ``[{id, sec, node_ids/node_tags, inactive, parent}]``
        orphan_nodes   – ``{node_id: {tag, x, y, z}}``
        edge_constraints – list of constraint tuples
        mesh_node_ids  – ``set`` of node IDs containing ``_mesh_``
    """
    import numpy as np

    data = {"nodes": {}, "frames": [], "shells": [], "orphan_nodes": {},
            "edge_constraints": [], "mesh_node_ids": set()}

    # ═════════════════════════════════════════════════════════════
    # Approach A: SAPModelData passthrough — raw importer output
    # ═════════════════════════════════════════════════════════════
    if isinstance(source, SAPModelData):
        for nid, nd in source.nodes.items():
            data["nodes"][nid] = {
                "tag": nd.node_tag, "x": nd.x, "y": nd.y, "z": nd.z,
            }
        for eid, elem in source.frame_elements.items():
            data["frames"].append({
                "id": eid, "ni_id": elem.node_i, "nj_id": elem.node_j,
                "sec": source.frame_assignments.get(eid, '?'),
                "parent": None,
            })
        for aid, area in source.area_elements.items():
            data["shells"].append({
                "id": aid, "sec": source.area_assignments.get(aid, 'unknown'),
                "node_ids": area.node_ids[:4],
                "inactive": False,
                "parent": None,
            })
        return data

    # ═════════════════════════════════════════════════════════════
    # NPZ / HDF5 data dict
    # ═════════════════════════════════════════════════════════════
    if isinstance(source, _NPZ_TYPES):
        n = len(source.get("node_tag", []))
        for i in range(n):
            tag = int(source["node_tag"][i])
            sid = str(source.get("node_sap_id", [""]*n)[i])
            node_entry = {
                "tag": tag, "x": float(source["node_x"][i]),
                "y": float(source["node_y"][i]), "z": float(source["node_z"][i]),
            }
            # Key by both SAP ID (string) and node tag (int) so that both
            # frame endpoint lookups (ni_tag/nj_tag) and shell vertex lookups
            # (shell_node_1..4) resolve immediately without a tag-search fallback.
            data["nodes"][sid] = node_entry
            data["nodes"][tag] = node_entry
            if "_mesh_" in sid:
                data["mesh_node_ids"].add(sid)

        nf = len(source.get("frame_eid", []))
        for i in range(nf):
            data["frames"].append({
                "id": str(source["frame_sap_id"][i]),
                "ni_tag": int(source["frame_node_i"][i]),
                "nj_tag": int(source["frame_node_j"][i]),
                "sec": str(source["frame_sec_name"][i]),
                "parent": str(source.get("frame_parent_sap_id", [""]*nf)[i]),
            })

        ns = len(source.get("shell_eid", []))
        shell_parent_arr = source.get("shell_parent_sap_id", [""] * ns)
        for i in range(ns):
            pid = str(shell_parent_arr[i]) if i < len(shell_parent_arr) else ""
            data["shells"].append({
                "id": str(source["shell_sap_id"][i]),
                "sec": str(source["shell_sec_name"][i]),
                "node_tags": [int(source[f"shell_node_{k}"][i]) for k in (1,2,3,4)],
                "parent": pid if pid and pid != "" and pid != "?" else None,
            })

        if collapse_to_parents:
            data = _collapse_to_parents(data, source)
        return data

    # ═════════════════════════════════════════════════════════════
    # Builder / AnalysisBuilder / MeshModel object
    # ═════════════════════════════════════════════════════════════
    builder = source
    if hasattr(builder, 'model'):
        model = builder.model
    elif hasattr(builder, 'mesh_model'):
        model = builder.mesh_model
    else:
        model = builder  # assume it's already a MeshModel
    elements = (builder.split_elements if hasattr(builder, 'split_elements')
                and builder.split_elements else model.frame_elements)
    assignments = (builder.split_assignments if hasattr(builder, 'split_assignments')
                   and builder.split_assignments else model.frame_assignments)

    # Nodes
    for nid, nd in model.nodes.items():
        data["nodes"][nid] = {
            "tag": nd.node_tag, "x": nd.x, "y": nd.y, "z": nd.z,
        }
        if "_mesh_" in nid:
            data["mesh_node_ids"].add(nid)

    # Orphan nodes
    mm = getattr(builder, '_mesh_model', None) if hasattr(builder, '_mesh_model') else None
    if mm is None:
        mm = getattr(builder, 'mesh_model', None)
    if mm is not None and hasattr(mm, 'orphan_nodes'):
        for nid, nd in mm.orphan_nodes.items():
            data["orphan_nodes"][nid] = {
                "tag": nd.node_tag, "x": nd.x, "y": nd.y, "z": nd.z,
            }

    if collapse_to_parents:
        # When collapsing, include inactive parents instead of children
        # Use the model's full frame_elements (including inactive parents)
        all_elements = model.frame_elements
        all_assignments = model.frame_assignments

        # Add inactive parent elements as frame entries
        for eid, elem in all_elements.items():
            if not getattr(elem, 'inactive', False):
                # Not a parent — skip, will be added by the normal loop
                continue
            data["frames"].append({
                "id": eid, "ni_id": elem.node_i, "nj_id": elem.node_j,
                "sec": (all_assignments or {}).get(eid, '?'),
                "parent": None,
            })

        # Add inactive parent area elements as shell entries
        for aid, area in model.area_elements.items():
            if not getattr(area, 'inactive', False):
                continue
            data["shells"].append({
                "id": aid, "sec": model.area_assignments.get(aid, 'unknown'),
                "node_ids": area.node_ids[:4],
                "inactive": False,
                "parent": None,
            })
    else:
        # Normal path: add active (child) elements only
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            data["frames"].append({
                "id": eid, "ni_id": elem.node_i, "nj_id": elem.node_j,
                "sec": (assignments or {}).get(eid, '?'),
                "parent": getattr(elem, 'parent_id', None),
            })

        for aid, area in model.area_elements.items():
            if getattr(area, 'inactive', False):
                continue
            data["shells"].append({
                "id": aid, "sec": model.area_assignments.get(aid, 'unknown'),
                "node_ids": area.node_ids[:4],
                "inactive": getattr(area, 'inactive', False),
                "parent": getattr(area, 'parent_id', None),
            })

        # Also add inactive parent area elements as grey wireframe overlays
        for aid, area in model.area_elements.items():
            if not getattr(area, 'inactive', False):
                continue
            data["shells"].append({
                "id": aid, "sec": model.area_assignments.get(aid, 'unknown'),
                "node_ids": area.node_ids[:4],
                "inactive": True,
                "parent": None,
            })

    # Edge constraints
    if mm is not None and hasattr(mm, 'detected_edge_pairs'):
        data["edge_constraints"] = list(mm.detected_edge_pairs)
    elif hasattr(builder, '_saved_edge_constraints'):
        data["edge_constraints"] = list(builder._saved_edge_constraints)

    return data


def _resolve_frame_node(nodes, fr, side='i'):
    """Resolve a frame endpoint node from resolved mesh data.

    Tries ``ni_id``/``nj_id`` (string key) first, then falls back to
    searching by ``ni_tag``/``nj_tag`` (integer tag).  Returns the node
    dict or ``None``.
    """
    key_id = f"n{side}_id"
    key_tag = f"n{side}_tag"
    nid = fr.get(key_id)
    if nid is not None:
        nd = nodes.get(nid)
        if nd is not None:
            return nd
    tag = fr.get(key_tag)
    if tag is not None:
        for nd in nodes.values():
            if nd["tag"] == tag:
                return nd
    return None


def _resolve_shell_node(nodes, ref):
    """Resolve a shell vertex node from resolved mesh data.

    Tries the string SAP ID key directly first, then int-to-string
    conversion, then falls back to searching by ``tag`` value (int)
    across all nodes.  Returns the node dict or ``None``.

    This is needed because:
    * ``_resolve_mesh_data`` indexes nodes by SAP ID (string, e.g.
      ``"area-1_sub_0_node_1"``).
    * NPZ shell connectivity arrays store node **tags** (integers,
      e.g. ``999``).
    * A direct ``nodes.get(tag)`` fails — the key system differs.
    """
    nd = nodes.get(ref)
    if nd is not None:
        return nd
    nd = nodes.get(str(ref))
    if nd is not None:
        return nd
    # Fallback: search by tag value
    for nd in nodes.values():
        if nd["tag"] == ref:
            return nd
    return None


def _render_scene(plotter, data, *,
                  shrink=0.0, xlim=None, ylim=None, zlim=None,
                  show_nodes=True, show_orphan_nodes=False,
                  show_mesh_nodes=False, show_frames=True, show_shells=True,
                  show_constraints=False, show_frame_labels=False,
                  show_node_labels=False, show_area_labels=False,
                  node_label_offset=0.4, tag_font=16,
                  section_colors=None):
    """Render mesh geometry from resolved data into a PyVista plotter.

    Parameters
    ----------
    plotter : pv.Plotter
        The plotter to render into.
    data : dict
        Mesh data dict from :func:`_resolve_mesh_data`.
    shrink : float
        Fraction to shrink quads/lines toward centroid.
    xlim, ylim, zlim : tuple or None
        (lo, hi) bounding-box filters.
    show_nodes, show_orphan_nodes, show_mesh_nodes : bool
        Toggle node groups.
    show_frames, show_shells : bool
        Toggle element groups.
    show_constraints : bool
        Draw edge constraint lines.
    show_frame_labels, show_node_labels, show_area_labels : bool
        Toggle text labels.
    """
    import numpy as np
    import pyvista as pv

    def _in_limits(pt):
        for lim, val in [(xlim, pt[0]), (ylim, pt[1]), (zlim, pt[2])]:
            if lim is None:
                continue
            lo, hi = lim
            if lo is not None and val < lo:
                return False
            if hi is not None and val > hi:
                return False
        return True

    def _shrink_quad(pts, factor):
        c = np.mean(pts, axis=0)
        return pts + (c - pts) * factor

    nodes = data["nodes"]
    cmap = ['#4c72b0', '#dd8452', '#55a868', '#c44e52', '#8172b3',
            '#937860', '#da8bc3', '#8c8c8c', '#ccb974', '#64b5cd']
    if section_colors is None:
        section_colors = {}

    # ── Frames ──────────────────────────────────────────────────
    if show_frames:
        frame_lines = []
        for fr in data["frames"]:
            ni = _resolve_frame_node(nodes, fr, 'i')
            nj = _resolve_frame_node(nodes, fr, 'j')
            if ni is None or nj is None:
                continue
            mid = [(ni["x"] + nj["x"]) / 2, (ni["y"] + nj["y"]) / 2, (ni["z"] + nj["z"]) / 2]
            if not _in_limits(mid):
                continue
            p1 = np.array([ni["x"], ni["y"], ni["z"]])
            p2 = np.array([nj["x"], nj["y"], nj["z"]])
            if shrink:
                m = (p1 + p2) / 2
                p1 = p1 + (m - p1) * shrink
                p2 = p2 + (m - p2) * shrink
            sec = fr.get("sec", '?')
            frame_lines.append((p1, p2, sec))

        all_secs = sorted({s for _, _, s in frame_lines})
        sec_col = section_colors or {s: cmap[i % len(cmap)]
                                      for i, s in enumerate(all_secs)}

        for p1, p2, sec in frame_lines:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            pts = np.linspace(p1, p2, n)
            poly = pv.lines_from_points(pts)
            plotter.add_mesh(poly, color=sec_col.get(sec, '#4c72b0'),
                             line_width=4, opacity=0.7)

    # ── Shells ──────────────────────────────────────────────────
    if show_shells:
        active_shells = {}
        inactive_shells = []
        for sh in data["shells"]:
            pts = []
            refs = sh.get("node_ids") or sh.get("node_tags") or []
            for ref in refs:
                # Nodes dict is dual-keyed (SAP ID string + int tag)
                nd = nodes.get(ref)
                if nd is None:
                    break
                pts.append([nd["x"], nd["y"], nd["z"]])
            if len(pts) < 3:
                continue
            centroid = np.mean(pts, axis=0)
            if not _in_limits(centroid):
                continue
            while len(pts) < 4:
                pts.append(pts[-1])
            if sh.get("inactive"):
                inactive_shells.append(np.array(pts))
            else:
                sec = sh.get("sec", 'unknown')
                active_shells.setdefault(sec, []).append(np.array(pts))

        for quad_pts in inactive_shells:
            qp = _shrink_quad(np.array(quad_pts), shrink) if shrink else np.array(quad_pts)
            plotter.add_mesh(pv.PolyData(qp, faces=[4, 0, 1, 2, 3]),
                            color='lightgrey', opacity=0.12,
                            show_edges=True, edge_color='grey', line_width=0.5)

        for i, (sec_name, quads) in enumerate(active_shells.items()):
            if section_colors:
                c = section_colors.get(sec_name) or cmap[i % len(cmap)]
            else:
                c = cmap[i % len(cmap)]
            for quad_pts in quads:
                pts = _shrink_quad(np.array(quad_pts), shrink) if shrink else np.array(quad_pts)
                is_tri = np.allclose(pts[2], pts[3])
                face = pv.PolyData(pts[:3], faces=[3, 0, 1, 2]) if is_tri else \
                       pv.PolyData(pts, faces=[4, 0, 1, 2, 3])
                plotter.add_mesh(face, color=c, opacity=0.35,
                                 show_edges=True, edge_color=c, line_width=0.8)

    # ── Nodes ───────────────────────────────────────────────────
    if show_nodes:
        # Deduplicate by tag: NPZ nodes may be dual-keyed (SAP ID + int tag)
        seen_tags = set()
        unique_nodes = []
        for n in nodes.values():
            tag = n.get("tag")
            if tag is not None and tag not in seen_tags:
                seen_tags.add(tag)
                unique_nodes.append(n)
        npts = np.array([[n["x"], n["y"], n["z"]]
                         for n in unique_nodes if _in_limits([n["x"], n["y"], n["z"]])])
        if len(npts):
            plotter.add_mesh(pv.PolyData(npts), color='black',
                             point_size=6, render_points_as_spheres=True)

    # ── Orphan nodes ────────────────────────────────────────────
    if show_orphan_nodes and data["orphan_nodes"]:
        opts = np.array([[n["x"], n["y"], n["z"]]
                         for n in data["orphan_nodes"].values()
                         if _in_limits([n["x"], n["y"], n["z"]])])
        if len(opts):
            plotter.add_mesh(pv.PolyData(opts), color='darkorange',
                             point_size=8, render_points_as_spheres=True)

    # ── Mesh-created nodes ──────────────────────────────────────
    if show_mesh_nodes:
        mpts = np.array([[nodes[nid]["x"], nodes[nid]["y"], nodes[nid]["z"]]
                         for nid in data["mesh_node_ids"] if nid in nodes
                         and _in_limits([nodes[nid]["x"], nodes[nid]["y"], nodes[nid]["z"]])])
        if len(mpts):
            plotter.add_mesh(pv.PolyData(mpts), color='lime',
                             point_size=10, render_points_as_spheres=True)

    # ── Edge constraints ────────────────────────────────────────
    if show_constraints and data["edge_constraints"]:
        segs = []
        for entry in data["edge_constraints"]:
            if not isinstance(entry, tuple) or len(entry) < 3:
                continue
            masters = entry[1] if len(entry) > 1 else []
            slaves = entry[2] if len(entry) > 2 else []
            for mn in masters:
                nid = mn[0] if isinstance(mn, (list, tuple)) else str(mn)
                cn = nodes.get(nid)
                if cn is None:
                    continue
                for sn in slaves:
                    sid = sn[0] if isinstance(sn, (list, tuple)) else str(sn)
                    fn = nodes.get(sid)
                    if fn is None:
                        continue
                    mid = [(cn["x"] + fn["x"]) / 2, (cn["y"] + fn["y"]) / 2,
                           (cn["z"] + fn["z"]) / 2]
                    if not _in_limits(mid):
                        continue
                    segs.append([cn["x"], cn["y"], cn["z"], fn["x"], fn["y"], fn["z"]])
        if segs:
            pts = np.array(segs).reshape(-1, 3)
            n_s = len(segs)
            conn = np.column_stack([
                np.full(n_s, 2, dtype=int),
                np.arange(0, 2 * n_s, 2, dtype=int),
                np.arange(1, 2 * n_s + 1, 2, dtype=int),
            ]).ravel()
            plotter.add_mesh(pv.PolyData(pts, lines=conn),
                             color='yellow', opacity=0.25, line_width=12)

    # ── Labels ──────────────────────────────────────────────────
    if show_node_labels:
        # Use the same tag-deduplicated unique_nodes collection as markers
        pts, tags = [], []
        for n in unique_nodes:
            if _in_limits([n["x"], n["y"], n["z"]]):
                tag_val = n.get("tag", "")
                pts.append([n["x"] + node_label_offset, n["y"] + node_label_offset, n["z"]])
                tags.append(f"N{tag_val}")
        if pts:
            plotter.add_point_labels(np.array(pts), tags, font_size=tag_font,
                                     point_size=0, shape=None)

    if show_frame_labels:
        pts, tags = [], []
        for fr in data["frames"]:
            ni = _resolve_frame_node(nodes, fr, 'i')
            nj = _resolve_frame_node(nodes, fr, 'j')
            if ni is None or nj is None:
                continue
            mid = [(ni["x"] + nj["x"]) / 2 - node_label_offset,
                   (ni["y"] + nj["y"]) / 2 - node_label_offset,
                   (ni["z"] + nj["z"]) / 2]
            if not _in_limits(mid):
                continue
            pts.append(mid)
            tags.append(f"F{fr['id']}")
        if pts:
            plotter.add_point_labels(np.array(pts), tags, font_size=tag_font,
                                     point_size=0, shape=None)

    if show_area_labels:
        pts, tags = [], []
        for sh in data["shells"]:
            npts = []
            for ref in (sh.get("node_ids") or []):
                nd = nodes.get(ref)
                if nd:
                    npts.append([nd["x"], nd["y"], nd["z"]])
            if len(npts) < 3:
                continue
            centroid = np.mean(npts, axis=0)
            if not _in_limits(centroid):
                continue
            pts.append([centroid[0], centroid[1], centroid[2] + node_label_offset])
            tags.append(f"A{sh['id']}")
        if pts:
            plotter.add_point_labels(np.array(pts), tags, font_size=tag_font,
                                     point_size=0, shape=None)

    plotter.show_grid()


def plot_mesh(source, *,
              collapse_to_parents=False,
              show_nodes=True, show_frames=True, show_shells=True,
              show_mesh_nodes=False, show_constraints=False,
              show_orphan_nodes=False, shrink=0.0,
              xlim=None, ylim=None, zlim=None,
              show_node_labels=False, show_frame_labels=False,
              show_area_labels=False, notebook=False, **kwargs):
    """Display a mesh in 3D from a builder, SAPModelData, or NPZ data dict.

    Single‑model viewer — accepts:

    * An ``SAPModelData`` instance (raw importer output, before preprocessing).
    * An ``AnalysisBuilder`` instance (built, after preprocessing).
    * A dict loaded from a unified NPZ file (via ``np.load()``).

    When ``collapse_to_parents=True``, split children are replaced with their
    unsplit parent elements, showing the model as drawn in SAP2000.

    Args:
        source: Builder, SAPModelData, or NPZ data dict.
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        show_nodes: Draw node markers.
        show_frames: Draw frame elements.
        show_shells: Draw shell elements.
        show_mesh_nodes: Highlight mesh‑created nodes (green).
        show_constraints: Draw edge constraint lines (yellow).
        show_orphan_nodes: Show orphan nodes (darkorange).
        shrink: Fraction to shrink quads/lines toward centroid.
        xlim, ylim, zlim: ``(lo, hi)`` bounding‑box filters.
        show_node_labels, show_frame_labels, show_area_labels: Add labels.
        notebook: Return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* is True, otherwise ``None``.
    """
    import pyvista as pv

    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)
    _render_scene(plotter, data,
                  show_nodes=show_nodes, show_frames=show_frames,
                  show_shells=show_shells, show_mesh_nodes=show_mesh_nodes,
                  show_constraints=show_constraints,
                  show_orphan_nodes=show_orphan_nodes, shrink=shrink,
                  xlim=xlim, ylim=ylim, zlim=zlim,
                  show_node_labels=show_node_labels,
                  show_frame_labels=show_frame_labels,
                  show_area_labels=show_area_labels)
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


def compare_meshes(source_a, source_b, *,
                   collapse_to_parents=False,
                   labels=("Model A", "Model B"),
                   notebook=False, **kwargs):
    """Side‑by‑side mesh comparison from two builders or NPZ data dicts.

    Shows two PyVista subplots (left = source_a, right = source_b).

    Args:
        source_a: First model (builder or NPZ dict).
        source_b: Second model (builder or NPZ dict).
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        labels: Pair of titles for the subplots.
        notebook: Return plotter for Jupyter embedding (default ``False``).
        **kwargs: Passed to :func:`_render_scene` (show_*, shrink, xlim, etc.).

    Returns:
        ``pv.Plotter`` if *notebook* is True, otherwise ``None``.
    """
    import pyvista as pv

    pv.set_plot_theme("document")
    plotter = pv.Plotter(shape=(1, 2), window_size=[2000, 900],
                         title=f"Mesh comparison: {labels[0]} (left) vs {labels[1]} (right)")

    for i, (src, label) in enumerate([(source_a, labels[0]), (source_b, labels[1])]):
        data = _resolve_mesh_data(src, collapse_to_parents=collapse_to_parents)
        plotter.subplot(0, i)
        plotter.add_text(label, position='upper_edge', font_size=28)
        _render_scene(plotter, data, **kwargs)
        _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


def plot_deformed_3d(
    builder,
    results: Dict[str, Any],
    scale: float = 10.0,
    show_original: bool = True,
    selection: Optional['Selection'] = None,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Overlay the deformed shape on the original model.

    .. deprecated::
       Use :func:`plot_deformed_displacement_3d` instead — it supports
       builder, AnalysisBuilder, and NPZ data, plus node colouring
       by displacement magnitude and value labels.

    Args:
        builder: Built ``AnalysisBuilder``.
        results: Output dict from ``builder.run_static_analysis()`` containing
                 ``nodal_displacements``.
        scale: Displacement magnification factor.
        show_original: If True, show the undeformed model in grey.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        notebook: If True, return plotter for Jupyter.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Requires:
        ``pyvista``.
    """
    warnings.warn(
        "plot_deformed_3d is deprecated and will be replaced by a "
        "unified version in a future release.",
        DeprecationWarning, stacklevel=2,
    )
    try:
        import pyvista as pv
    except ImportError:
        print("Warning: pyvista not installed.  Install with: pip install pyvista")
        return None

    disp = results.get('nodal_displacements', {})
    if not disp:
        print("No displacement data in results — run static analysis with "
              "extract_reactions=True first.")
        return None

    pv.set_plot_theme("document")

    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # Undeformed (greyed out)
    if show_original:
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            ni = builder.model.nodes.get(elem.node_i)
            nj = builder.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            p1 = np.array([ni.x, ni.y, ni.z])
            p2 = np.array([nj.x, nj.y, nj.z])
            n_pts = max(2, int(np.linalg.norm(p2 - p1) * 2))
            pts = np.linspace(p1, p2, n_pts)
            poly = pv.lines_from_points(pts)
            plotter.add_mesh(poly, color='lightgrey', line_width=2,
                             opacity=0.5)

    # Deformed (coloured)
    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        di = disp.get(ni.node_tag, (0, 0, 0))
        dj = disp.get(nj.node_tag, (0, 0, 0))
        p1 = np.array([ni.x + di[0] * scale,
                        ni.y + di[1] * scale,
                        ni.z + di[2] * scale])
        p2 = np.array([nj.x + dj[0] * scale,
                        nj.y + dj[1] * scale,
                        nj.z + dj[2] * scale])
        n_pts = max(2, int(np.linalg.norm(np.array([ni.x, ni.y, ni.z])
                                           - np.array([nj.x, nj.y, nj.z])) * 2))
        pts = np.linspace(p1, p2, n_pts)
        poly = pv.lines_from_points(pts)
        plotter.add_mesh(poly, color='#c44e52', line_width=4)

    plotter.show_grid()
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


# ============================================================================
# RS deformed shape (PyVista) — from CQC-combined nodal displacements
# ============================================================================

def plot_rs_deformed_3d(
    builder,
    rs_displacements: Dict[int, tuple],
    scale: float = 10.0,
    show_original: bool = True,
    selection: Optional['Selection'] = None,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Display the RS CQC‑combined deformed shape using PyVista.

    .. deprecated::
       Use :func:`plot_deformed_displacement_3d` instead — it supports
       builder, AnalysisBuilder, and NPZ data, plus node colouring
       by displacement magnitude and value labels.

    Args:
        builder: Built ``AnalysisBuilder``.
        rs_displacements: Dict from
            ``builder.compute_rs_nodal_displacements()`` mapping
            ``node_tag → (dx, dy, dz)``.
        scale: Displacement magnification factor.
        show_original: If True, show the undeformed model in grey.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        notebook: If True, return plotter for Jupyter.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Requires:
        ``pyvista``.
    """
    warnings.warn(
        "plot_rs_deformed_3d is deprecated and will be replaced by a "
        "unified version in a future release.",
        DeprecationWarning, stacklevel=2,
    )
    try:
        import pyvista as pv
    except ImportError:
        print("Warning: pyvista not installed.  Install with: pip install pyvista")
        return None

    if not rs_displacements:
        print("No RS displacement data — run compute_rs_nodal_displacements first.")
        return None

    pv.set_plot_theme("document")

    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # Undeformed (grey)
    if show_original:
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            ni = builder.model.nodes.get(elem.node_i)
            nj = builder.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            p1 = np.array([ni.x, ni.y, ni.z])
            p2 = np.array([nj.x, nj.y, nj.z])
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=2, opacity=0.5)

    # Deformed — coloured by displacement magnitude
    max_disp = max(
        math.sqrt(dx**2 + dy**2 + dz**2)
        for dx, dy, dz in rs_displacements.values()
    ) if rs_displacements else 1.0

    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        di = rs_displacements.get(ni.node_tag, (0, 0, 0))
        dj = rs_displacements.get(nj.node_tag, (0, 0, 0))
        p1 = np.array([ni.x + di[0] * scale,
                        ni.y + di[1] * scale,
                        ni.z + di[2] * scale])
        p2 = np.array([nj.x + dj[0] * scale,
                        nj.y + dj[1] * scale,
                        nj.z + dj[2] * scale])
        # Colour by average displacement magnitude along this element
        avg_disp = (math.sqrt(di[0]**2 + di[1]**2 + di[2]**2) +
                    math.sqrt(dj[0]**2 + dj[1]**2 + dj[2]**2)) * 0.5
        intensity = avg_disp / max_disp if max_disp > 0 else 0.0
        # Blue‑white‑red colour map
        r = min(1.0, intensity * 2)
        b = min(1.0, (1.0 - intensity) * 2)
        colour = (r, 0.0, b)

        n = max(2, int(np.linalg.norm(np.array([ni.x, ni.y, ni.z])
                                       - np.array([nj.x, nj.y, nj.z])) * 2))
        pts = np.linspace(p1, p2, n)
        poly = pv.lines_from_points(pts)
        plotter.add_mesh(poly, color=colour, line_width=4)

    plotter.show_grid()
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


# ============================================================================
# Displaced shape 3D (PyVista) — unified static/RS displacement viewer
# ============================================================================

def plot_deformed_displacement_3d(
    source,
    displacements: Dict,
    *,
    collapse_to_parents=False,
    scale: float = 10.0,
    show_undeformed: bool = True,
    shrink: float = 0.0,
    color_nodes: bool = True,
    colormap: str = "plasma",
    show_labels: bool = False,
    max_labels: int = 30,
    label_threshold: float = 0.01,
    label_unit: str = "mm",
    show_bounds: bool = True,
    camera: str = "iso",
    selection: Optional['Selection'] = None,
    save_screenshot: Optional[str] = None,
    screenshot_views: Optional[list] = None,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Display a displaced shape with node-colouring by displacement magnitude.

    Unified replacement for ``plot_deformed_3d`` and ``plot_rs_deformed_3d``.
    Works with any data source (builder, AnalysisBuilder, or NPZ dict).

    Args:
        source: ``AnalysisBuilder``, or NPZ data dict.
        displacements: Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model
            length units (e.g. metres).  For static analyses, pass the
            ``nodal_displacements`` dict from ``run_static_analysis()``.
            For RS, pass the CQC-combined dict from
            ``compute_rs_nodal_displacements()``.
        scale: Displacement magnification factor.
        show_undeformed: Show the undeformed mesh in grey.
        shrink: Fraction to shrink frame lines toward their midpoint
            (0.0 = full length, 0.1 = 10 percent gap at each end).
        color_nodes: If True, colour node markers by resultant displacement.
        colormap: PyVista colormap name for node colouring.
        show_labels: If True, overlay displacement value labels on nodes.
        max_labels: Max number of labelled nodes (highest displacements).
        label_threshold: Minimum displacement (m) for a node to be labelled.
        label_unit: Display unit for labels (``"mm"`` or ``"m"``).
        show_bounds: Show axis bounds grid.
        camera: Camera position (``"iso"``, ``"xy"``, ``"xz"``, ``"yz"``).
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which frame elements are shown.  Only supported
            when *source* is a builder/AnalysisBuilder (requires a model
            object to resolve selection criteria).  ``None`` means all.
        save_screenshot: Path to save a screenshot (PNG).
        screenshot_views: List of camera positions for multiple screenshots,
            e.g. ``["iso", "xy"]``.  ``None`` means just *camera*.
        notebook: Return plotter for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook*, else ``None``.

    Example::

        # Static analysis
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
        mm = preprocess_model(md)
        b = AnalysisBuilder(mm, ...)
        b.build_domain()
        b.create_loads({"DEAD": 1.0, "WIND": 1.0})
        results = b.run_static_analysis()
        plot_deformed_displacement_3d(b, results["nodal_displacements"],
                                       scale=20.0, show_labels=True)

        # RS analysis with selection
        sel = Selection(element_types=['Frame'], groups=['Moment Frame'])
        plot_deformed_displacement_3d(b, results["nodal_displacements"],
                                       scale=20.0, selection=sel)

        # RS analysis
        rs_disp = b.compute_rs_nodal_displacements(...)
        plot_deformed_displacement_3d(b, rs_disp, scale=50.0)
    """
    import math
    import numpy as np
    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    # ── Resolve mesh data ──────────────────────────────────────────
    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
    nodes = data["nodes"]
    frames = data["frames"]

    # ── Apply selection filter (builder sources only) ─────────────
    if selection is not None and not isinstance(source, _NPZ_TYPES):
        try:
            sel_ids = set(selection.get_frame_ids(source.model))
            frames = [fr for fr in frames if fr.get("id") in sel_ids
                      or str(fr.get("id")) in sel_ids]
            data["frames"] = frames   # also used by _render_scene
        except AttributeError:
            print("Warning: selection requires a builder/analysis-builder "
                  "source with a .model attribute — ignoring selection.")

    if not displacements:
        print("No displacement data provided.")
        return None

    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # ── Undeformed mesh (greyed out) ──────────────────────────────
    if show_undeformed:
        _render_scene(plotter, data, show_nodes=False, show_shells=True,
                      show_frames=True, show_constraints=False,
                      shrink=shrink)

    # ── Deformed frame lines (warm red) ───────────────────────────
    for fr in frames:
        ni = _resolve_frame_node(nodes, fr, 'i')
        nj = _resolve_frame_node(nodes, fr, 'j')
        if ni is None or nj is None:
            continue
        di = displacements.get(ni["tag"], (0, 0, 0))
        dj = displacements.get(nj["tag"], (0, 0, 0))
        p1 = np.array([ni["x"] + di[0]*scale, ni["y"] + di[1]*scale, ni["z"] + di[2]*scale])
        p2 = np.array([nj["x"] + dj[0]*scale, nj["y"] + dj[1]*scale, nj["z"] + dj[2]*scale])
        if shrink:
            m = (p1 + p2) / 2
            p1 = p1 + (m - p1) * shrink
            p2 = p2 + (m - p2) * shrink
        n_pts = max(2, int(np.linalg.norm(p2 - p1) * 2))
        pts = np.linspace(p1, p2, n_pts)
        poly = pv.lines_from_points(pts)
        plotter.add_mesh(poly, color="#c44e52", line_width=3)

    # ── Node colouring by displacement magnitude ──────────────────
    if color_nodes:
        node_pts = []
        node_disp = []
        seen_tags = set()
        for nid, nd in nodes.items():
            tag = nd.get("tag")
            if tag is not None and tag not in seen_tags:
                seen_tags.add(tag)
            else:
                continue
            d = displacements.get(nd["tag"], (0.0, 0.0, 0.0))
            mag = math.hypot(d[0], d[1], d[2])
            node_pts.append([nd["x"], nd["y"], nd["z"]])
            node_disp.append(mag)
        if node_pts:
            pts_arr = np.array(node_pts)
            disp_arr = np.array(node_disp)
            cloud = pv.PolyData(pts_arr)
            cloud["displacement (m)"] = disp_arr
            plotter.add_mesh(
                cloud,
                scalars="displacement (m)",
                cmap=colormap,
                point_size=10,
                render_points_as_spheres=True,
                scalar_bar_args={
                    "title": "Resultant\nDisplacement (m)",
                    "title_font_size": 12,
                    "label_font_size": 10,
                },
                clim=[0, max(disp_arr) * 1.1] if max(disp_arr) > 0 else None,
            )

    # ── Displacement labels ────────────────────────────────────────
    if show_labels:
        label_scale = 1000.0 if label_unit == "mm" else 1.0
        label_suffix = label_unit
        labeled = []
        seen_tags = set()
        for nid, nd in nodes.items():
            tag = nd.get("tag")
            if tag is not None and tag not in seen_tags:
                seen_tags.add(tag)
            else:
                continue
            d = displacements.get(nd["tag"], (0.0, 0.0, 0.0))
            mag = math.hypot(d[0], d[1], d[2])
            if mag > label_threshold:
                labeled.append((nd["x"], nd["y"], nd["z"], mag))
        labeled.sort(key=lambda t: -t[3])
        labeled = labeled[:max_labels]
        for x, y, z, d in labeled:
            val = round(d * label_scale)
            plotter.add_point_labels(
                np.array([[x, y, z]]),
                [f"{val}{label_suffix}"],
                point_size=0, font_size=10, text_color="black",
                shape="rounded_rect", shape_color="white",
                shape_opacity=0.8, always_visible=True,
            )

    # ── Axes, bounds, camera ──────────────────────────────────────
    if show_bounds:
        plotter.show_bounds(grid="back", location="outer", font_size=8, color="grey")
    plotter.add_axes(interactive=True, line_width=2, labels_off=False)

    _set_isometric_view(plotter)
    cam_map = {"iso": "iso", "xy": "xy", "xz": "xz", "yz": "yz"}
    cam_pos = cam_map.get(camera, "iso")
    if cam_pos == "iso":
        plotter.camera_position = "iso"
    else:
        plotter.camera_position = cam_pos
    plotter.camera.zoom(0.8)

    # ── Screenshot export ──────────────────────────────────────────
    views_to_capture = screenshot_views or ([camera] if save_screenshot else [])
    for v in views_to_capture:
        vcam = cam_map.get(v, "iso")
        if vcam == "iso":
            plotter.camera_position = "iso"
        else:
            plotter.camera_position = vcam
        plotter.camera.zoom(0.8)
        if save_screenshot:
            path = str(Path(save_screenshot))
            if len(views_to_capture) > 1:
                stem = Path(path).stem
                parent = Path(path).parent
                path = str(parent / f"{stem}_{v}.png")
            plotter.show(screenshot=path, auto_close=False)

    if notebook:
        return plotter
    if not save_screenshot:
        plotter.show()
    plotter.close()
    return None


# ============================================================================
# Mode shape 3D view (PyVista) — animated or static
# ============================================================================

def plot_mode_3d(
    builder,
    mode_shapes: Dict[int, Dict[int, tuple]],
    mode: int = 0,
    scale: float = 10.0,
    show_original: bool = True,
    animate: bool = True,
    periods: Optional[List[float]] = None,
    font_size: int = 14,
    selection: Optional['Selection'] = None,
    notebook: bool = False,
    anim_speed: float = 2.0,
    anim_amplitude: float = 1.5,
    **kwargs,
) -> Optional[Any]:
    """Display (and optionally animate) a mode shape in 3D using PyVista.

    .. deprecated::
       Use :func:`plot_mode_animation` instead — it works with both
       builder and NPZ data and supports all features of this function.

    For each mode, the eigenvector displacements from
    :meth:`AnalysisBuilder.extract_mode_shapes` are applied as a deformed
    shape, scaled by *scale*.  When *animate* is ``True`` the amplitude
    oscillates sinusoidally, giving a visual feel for the vibration pattern.

    Args:
        builder: Built ``AnalysisBuilder``.
        mode_shapes: Output of ``builder.extract_mode_shapes(num_modes)``.
        mode: 0‑based mode index to display.
        scale: Displacement magnification factor.
        show_original: If True, show the undeformed model in grey.
        animate: If True, oscillate the amplitude in a loop.
        periods: Optional list of modal periods (s).  If provided, the
            period for the displayed mode is shown in the title.
        font_size: Font size for the title text (default 14).
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.
        notebook: If True, return plotter for Jupyter.
        anim_speed: Animation speed multiplier (2.0 = default, 4.0 = 2×).
        anim_amplitude: Amplitude range multiplier (1.5 = default, 1.0 = ±100% of scale,
            2.0 = ±200%, etc.).
        **kwargs: Passed to ``pyvista.Plotter()``.

    Requires:
        ``pyvista``.
    """
    warnings.warn(
        "plot_mode_3d is deprecated — use plot_mode_animation() instead "
        "(works with builder or NPZ data)",
        DeprecationWarning, stacklevel=2,
    )
    try:
        import pyvista as pv
    except ImportError:
        print("Warning: pyvista not installed.  Install with: pip install pyvista")
        return None

    if mode not in mode_shapes or not mode_shapes[mode]:
        print(f"No mode shape data for mode {mode}.")
        return None

    pv.set_plot_theme("document")

    disp = mode_shapes[mode]  # {node_tag: (dx, dy, dz)}

    # ── Collect frame segments ──
    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}

    segments = []
    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        di = disp.get(ni.node_tag, (0, 0, 0))
        dj = disp.get(nj.node_tag, (0, 0, 0))
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        segments.append((p1, p2, di, dj))

    # ── Collect shell quads from area elements, grouped by section ──
    from collections import defaultdict
    shell_groups: Dict[str, list] = defaultdict(list)
    # section_name -> (p1, p2, p3, p4, d1, d2, d3, d4)
    inactive_shell_quads: List[list] = []  # parent quads for grey overlay
    for aid, area in builder.model.area_elements.items():
        if len(area.node_ids) < 3:
            continue
        is_inactive = getattr(area, 'inactive', False)
        sec_name = builder.model.area_assignments.get(aid, 'unknown')
        nids = area.node_ids[:4]  # at most 4 for a quad
        pts = []
        ds = []
        for nid in nids:
            nd = builder.model.nodes.get(nid)
            if nd is None:
                break
            tag = nd.node_tag
            pts.append(np.array([nd.x, nd.y, nd.z]))
            ds.append(np.array(disp.get(tag, (0, 0, 0))))
        if len(pts) == 4:
            if is_inactive:
                inactive_shell_quads.append(pts)  # no displacements for parents
            else:
                shell_groups[sec_name].append(pts + ds)

    if not segments and not shell_groups:
        print("No elements to display.")
        return None

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # ── Colour palette for shell section groups ──
    _SECTION_COLORS = [
        '#4c72b0', '#dd8452', '#55a868', '#c44e52', '#8172b3',
        '#937860', '#da8bc3', '#8c8c8c', '#ccb974', '#64b5cd',
    ]
    _sec_names_sorted = sorted(shell_groups.keys())
    _sec_colors = {name: _SECTION_COLORS[i % len(_SECTION_COLORS)]
                   for i, name in enumerate(_sec_names_sorted)}

    # Undeformed (grey)
    if show_original:
        for p1, p2, _, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=2, opacity=0.3)
        # Undeformed shells — inactive parents (coarse mesh) as grey overlay
        for quad_pts in inactive_shell_quads:
            face = pv.PolyData(quad_pts, faces=[4, 0, 1, 2, 3])
            plotter.add_mesh(face, color='lightgrey', opacity=0.12,
                             show_edges=True, edge_color='grey', line_width=0.5)

    # ── Helper: build deformed mesh (used by both animate and static paths) ──
    # Pre-compute the UNDEFORMED length of each frame segment so the number
    # of interpolation points stays constant across animation amplitudes.
    _seg_npoints = []
    for p1, p2, _, _ in segments:
        n = max(2, int(np.linalg.norm(p2 - p1) * 2))
        _seg_npoints.append(n)
    # Build flat lists for the animation path, with per-quad section index
    _all_quads_flat: List[list] = []
    _all_sec_idxs: List[int] = []
    _sec_name_to_idx = {name: i for i, name in enumerate(_sec_names_sorted)}
    for sec_name, quads in shell_groups.items():
        sidx = _sec_name_to_idx[sec_name]
        for quad in quads:
            _all_quads_flat.append(quad)
            _all_sec_idxs.append(sidx)

    def make_deformed(amp: float = 1.0):
        """Build merged PolyData for the deformed shape at amplitude *amp*.
        Point count is invariant w.r.t. *amp* — safe for animation updates.

        The shell mesh carries a ``section_idx`` cell scalar for per-section
        colouring (one entry per quad face, 1:1 mapping).
        """
        return _build_deformed_mesh(
            segments, _seg_npoints,
            _all_quads_flat, _all_sec_idxs,
            scale, amp,
        )

    if animate:
        # Animated mode: separate meshes for frames (lines) and shells (faces).
        # We keep references so we can update .points per frame.
        frame_mesh, shell_mesh = make_deformed(1.0)

        # Render frames as coloured lines
        if frame_mesh is not None and frame_mesh.n_points:
            plotter.add_mesh(frame_mesh, color='#555555',
                             line_width=4, opacity=0.85)

        # Render shells with per-section colours via cell scalars.
        # Each quad is a single face, so cell_data maps 1:1 to faces.
        n_sections = len(_sec_names_sorted)
        if shell_mesh is not None and shell_mesh.n_points:
            if n_sections > 0:
                plotter.add_mesh(
                    shell_mesh,
                    scalars='section_idx',
                    cmap=_SECTION_COLORS[:n_sections],
                    show_edges=True, edge_color='#333333',
                    opacity=0.85,
                    clim=[-0.5, n_sections - 0.5],
                )
            else:
                plotter.add_mesh(shell_mesh, color='#4c72b0',
                                 show_edges=True, edge_color='#333333',
                                 opacity=0.85)

        # ── Slider / animation callback ──
        def _on_animation(amp_val: float, fm=frame_mesh, sm=shell_mesh):
            nfm, nsm = make_deformed(amp_val)
            if fm is not None and nfm is not None:
                fm.points = nfm.points
            if sm is not None and nsm is not None:
                sm.points = nsm.points
    else:
        # Static mode: render each shell section group in its own colour
        for sec_name, quads in shell_groups.items():
            color = _sec_colors[sec_name]
            all_pts, all_faces = [], []
            offset = 0
            for quad in quads:
                p1, p2, p3, p4, d1, d2, d3, d4 = quad
                a1 = p1 + d1 * scale
                a2 = p2 + d2 * scale
                a3 = p3 + d3 * scale
                a4 = p4 + d4 * scale
                all_pts.extend([a1, a2, a3, a4])
                all_faces.append([4, offset, offset + 1, offset + 2, offset + 3])
                offset += 4
            if all_pts:
                verts = np.vstack(all_pts)
                faces = np.array(all_faces, dtype=int)
                group_mesh = pv.PolyData(verts, faces=faces)
                plotter.add_mesh(group_mesh, color=color,
                                 show_edges=True, edge_color='#333333',
                                 line_width=0.8, opacity=0.85)
        # Also draw frame lines on top in a neutral colour
        if segments:
            fm, _ = make_deformed(1.0)
            if fm is not None and fm.n_points:
                plotter.add_mesh(fm, color='#555555', line_width=3,
                                 opacity=0.8)
        # Legend for shell section colours
        if len(shell_groups) > 1:
            legend_entries = [(name, pv.Color(_sec_colors[name]))
                              for name in _sec_names_sorted]
            plotter.add_legend(
                legend_entries, border=True, size=[0.2, 0.12],
                loc='lower right', face='rectangle',
            )

    # Build title text with period if available
    period_str = ""
    if periods is not None and mode < len(periods):
        period_str = f"  T = {periods[mode]:.4f} s"

    if animate:
        import math as _math

        def callback(step):
            amp = anim_amplitude * _math.sin(
                2.0 * _math.pi * step * anim_speed / 60.0)
            nfm, nsm = make_deformed(amp)
            if frame_mesh is not None and nfm is not None:
                frame_mesh.points = nfm.points
            if shell_mesh is not None and nsm is not None:
                shell_mesh.points = nsm.points
            plotter.render()

        plotter.add_timer_event(max_steps=3600, interval=17, callback=callback)
        plotter.add_text(f"Mode {mode + 1}{period_str}  (oscillating)",
                         position='upper_edge', font_size=font_size)
    else:
        plotter.add_text(f"Mode {mode + 1}{period_str}",
                         position='upper_edge', font_size=font_size)

    plotter.show_grid()
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


# ============================================================================
# Mode shape animation (builder or NPZ data)
# ============================================================================

def plot_mode_animation(source, mode_shapes, mode=0, *,
                        collapse_to_parents=False,
                        scale=30.0, show_original=True,
                        shrink=0.0,
                        animate=True, periods=None,
                        font_size=14, anim_speed=2.0, anim_amplitude=1.5,
                        selection=None, notebook=False, **kwargs):
    """Display / animate a mode shape from a builder or NPZ data.

    Works with either:

    * An ``AnalysisBuilder`` (built) + mode shapes
      from ``extract_mode_shapes()``.
    * An NPZ data dict (from ``np.load()``) + mode shapes from the
      ``modal/mode_dx``, ``modal/mode_dy``, ``modal/mode_dz`` arrays.

    When drawing shells, each SAP section type gets its own colour from a
    fixed palette, with a legend in the lower-right corner (model-specific
    colours, not section-index cycling — required for visualising shells
    by material/assignment in mixed models like the admin building).

    Args:
        source: Builder or NPZ dict.
        mode_shapes: Dict ``{mode_idx: {node_tag: (dx, dy, dz)}}`` from
            ``extract_mode_shapes()``, OR ``None`` to extract from NPZ.
        mode: 0‑based mode index.
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        scale: Displacement magnification factor.
        show_original: Show undeformed model in grey.
        shrink: Fraction to shrink frame lines toward their midpoint
            (0.0 = full length, 0.1 = 10 percent gap at each end).
        animate: Oscillate amplitude sinusoidally.
        periods: List of modal periods (s).  The period for *mode* is shown.
        font_size, anim_speed, anim_amplitude: Display tuning.
        selection: Optional Selection to filter elements (builder only).
        notebook: Return plotter for Jupyter.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook*, else ``None``.
    """
    import numpy as np
    try:
        import pyvista as pv
    except ImportError:
        print("pyvista not installed — install with: pip install pyvista")
        return None

    _SECTION_COLORS = [
        '#4c72b0', '#dd8452', '#55a868', '#c44e52', '#8172b3',
        '#937860', '#da8bc3', '#8c8c8c', '#ccb974', '#64b5cd',
    ]

    # Resolve mode shape displacements for this mode
    if isinstance(source, _NPZ_TYPES) and mode_shapes is None:
        # NPZ data — extract mode shapes from arrays
        dx = source.get("modal/mode_dx")
        dy = source.get("modal/mode_dy")
        dz = source.get("modal/mode_dz")
        if dx is None or mode >= dx.shape[1]:
            print(f"No mode shape data for mode {mode} in NPZ.")
            return None
        tags = list(source.get("node_tag", []))
        mode_shapes = {mode: {int(tags[i]): (float(dx[i, mode]),
                                              float(dy[i, mode]),
                                              float(dz[i, mode]))
                              for i in range(len(tags))}}
    elif isinstance(source, _NPZ_TYPES):
        # NPZ data with pre-extracted mode_shapes — resolve tags
        pass

    if mode not in mode_shapes or not mode_shapes.get(mode):
        print(f"No mode shape data for mode {mode}.")
        return None

    disp = mode_shapes[mode]

    # Resolve mesh geometry into common format
    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)

    # ── Separate inactive (parent) shells from active shells ──
    # Also group active shells by section name for per-section colouring.
    inactive_shell_quads: list = []
    shell_groups: dict = {}
    for sh in data["shells"]:
        npts = []
        ds = []
        for ref in (sh.get("node_ids") or sh.get("node_tags") or []):
            # Nodes dict is dual-keyed (SAP ID string + int tag)
            nd = data["nodes"].get(ref)
            if nd is None:
                break
            npts.append(np.array([nd["x"], nd["y"], nd["z"]]))
            ds.append(np.array(disp.get(nd["tag"], (0, 0, 0))))
        if len(npts) == 4:
            if sh.get("inactive"):
                inactive_shell_quads.append(npts)
            else:
                sec = sh.get("sec", "unknown")
                shell_groups.setdefault(sec, []).append(tuple(npts) + tuple(ds))

    # Build frame segments
    segments = []
    seg_npoints = []
    for fr in data["frames"]:
        ni = _resolve_frame_node(data["nodes"], fr, 'i')
        nj = _resolve_frame_node(data["nodes"], fr, 'j')
        if ni is None or nj is None:
            continue
        p1 = np.array([ni["x"], ni["y"], ni["z"]])
        p2 = np.array([nj["x"], nj["y"], nj["z"]])
        if shrink:
            m = (p1 + p2) / 2
            p1 = p1 + (m - p1) * shrink
            p2 = p2 + (m - p2) * shrink
        di = np.array(disp.get(ni["tag"], (0, 0, 0)))
        dj = np.array(disp.get(nj["tag"], (0, 0, 0)))
        segments.append((p1, p2, di, dj))
        seg_npoints.append(max(2, int(np.linalg.norm(p2 - p1) * 2)))

    # Flatten shell groups for deformed mesh builder, tracking section index
    all_quads = []
    all_sec_idxs = []
    sec_names_sorted = sorted(shell_groups.keys())
    sec_name_to_idx = {name: i for i, name in enumerate(sec_names_sorted)}
    for sec_name, quads in shell_groups.items():
        sidx = sec_name_to_idx[sec_name]
        for quad in quads:
            all_quads.append(quad)
            all_sec_idxs.append(sidx)
    n_sections = len(sec_names_sorted)

    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # ── Undeformed shells ──
    if show_original:
        # Inactive (parent) quad overlays — shrink consistently with active shells
        if inactive_shell_quads:
            for quad_pts in inactive_shell_quads:
                if shrink:
                    arr = np.array(quad_pts)
                    c = np.mean(arr, axis=0)
                    quad_pts = arr + (c - arr) * shrink
                face = pv.PolyData(quad_pts, faces=[4, 0, 1, 2, 3])
                plotter.add_mesh(face, color='lightgrey', opacity=0.12,
                                 show_edges=True, edge_color='grey', line_width=0.5)
        # Undeformed active shells at amp=0
        if all_quads:
            _, und_shells = _build_deformed_mesh(
                segments, seg_npoints, all_quads, all_sec_idxs,
                scale, 0.0, shrink=shrink)
            if und_shells is not None and und_shells.n_points:
                plotter.add_mesh(und_shells, color='lightgrey',
                                 opacity=0.3, show_edges=True, line_width=1)

    # ── Undeformed frames ──
    if show_original:
        for p1, p2, _, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='#999999', line_width=1, opacity=0.5)

    # ── Deformed mesh (amp=1.0 for static, overridden during animation) ──
    frame_mesh, shell_mesh = _build_deformed_mesh(
        segments, seg_npoints, all_quads, all_sec_idxs,
        scale, 1.0, shrink=shrink)

    # Shells: per-section colours via cell scalars
    if shell_mesh is not None and shell_mesh.n_points:
        if n_sections > 0:
            plotter.add_mesh(
                shell_mesh,
                scalars='section_idx',
                cmap=_SECTION_COLORS[:n_sections],
                show_edges=True, edge_color='#333333',
                opacity=0.85,
                clim=[-0.5, n_sections - 0.5],
            )
        else:
            plotter.add_mesh(shell_mesh, color='#4c72b0',
                             show_edges=True, edge_color='#333333',
                             opacity=0.85)

    # Frames: dark grey (distinct from shell colours)
    if frame_mesh is not None and frame_mesh.n_points:
        plotter.add_mesh(frame_mesh, color='#555555', line_width=3,
                         opacity=0.8)

    # ── Section legend ──
    if n_sections > 1:
        legend_entries = [(name, pv.Color(_SECTION_COLORS[i % len(_SECTION_COLORS)]))
                          for i, name in enumerate(sec_names_sorted)]
        try:
            # Newer PyVista supports label_size; older versions don't
            plotter.add_legend(
                legend_entries, border=True, size=[0.2, 0.12],
                loc='lower right', face='rectangle',
                label_size=max(8, 14 - n_sections),
            )
        except TypeError:
            # Fallback for older PyVista without label_size kwarg
            plotter.add_legend(
                legend_entries, border=True, size=[0.2, 0.12],
                loc='lower right',
            )

    # ── Title ──
    period_str = ""
    if periods is not None and mode < len(periods):
        period_str = f"  T = {periods[mode]:.4f} s"
    plotter.add_text(f"Mode {mode + 1}  {period_str}",
                     position='upper_edge', font_size=font_size)
    plotter.show_grid()
    _set_isometric_view(plotter)

    # ── Animation ──
    if animate:
        import math as _math

        def callback(step):
            amp = _math.sin(anim_speed * 2.0 * _math.pi * step / 60.0) * anim_amplitude
            nfm, nsm = _build_deformed_mesh(
                segments, seg_npoints, all_quads, all_sec_idxs,
                scale, amp, shrink=shrink)
            if nfm is not None and nfm.n_points and frame_mesh.n_points:
                frame_mesh.points = nfm.points
            if shell_mesh is not None and nsm is not None and nsm.n_points:
                shell_mesh.points = nsm.points

        plotter.add_timer_event(max_steps=3600, interval=17, callback=callback)
        plotter.show(auto_close=False)
    else:
        plotter.show()

    if notebook:
        return plotter
    return None


# ============================================================================
# Unified force/moment diagram (builder or NPZ data)
# ============================================================================

def plot_force_diagram_3d(source, force_data=None, *,
                          collapse_to_parents=False,
                          quantity='My', mode='flag',
                          moment_scale=None, show_original=True,
                          combo=None, notebook=False, title=None,
                          **kwargs):
    """Draw a 3D force/moment diagram from a builder or NPZ/HDF5 data.

    Works with either:

    * An ``AnalysisBuilder`` (built) + a force dict
      from ``extract_static_element_forces()``.
    * An NPZ data dict (from ``np.load()``) — forces are read from the
      ``static/{combo}/`` arrays automatically.

    Geometry is resolved from *source* via :func:`_resolve_mesh_data`:
    the NPZ file already caches node coordinates and element connectivity,
    so no separate model file is needed.

    Args:
        source: Builder instance or NPZ data dict.
        force_data:
            *Builder path:* dict ``{elem_tag: {Fx, Fy, Fz, Mx, My, Mz,
            Fx_j, ...}}`` from ``extract_static_element_forces()``.
            *NPZ path:* ``None`` (forces extracted automatically), or a
            string naming the static combo (e.g. ``"DEAD"``) — equivalent
            to passing via the *combo* parameter.
        quantity: ``'My'``, ``'Mz'``, ``'Mx'``, ``'Fx'``, ``'Fy'``, ``'Fz'``.
        mode: ``'flag'`` (planar quadrilaterals) or ``'tube'`` (coloured
            cylinders).
        moment_scale: Extrusion length per unit quantity.  ``None`` =
            auto‑scale so the largest flag is 20 % of model height.
        show_original: Draw the centreline in grey.
        combo: For NPZ source, the static case name (e.g. ``"DEAD"``).
            ``None`` = first available case.
        notebook: Return plotter for Jupyter.
        title: Optional plot title.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pv.Plotter`` if *notebook* else ``None``.
    """
    import numpy as np
    try:
        import pyvista as pv
    except ImportError:
        print("pyvista is required.  pip install pyvista")
        return None

    is_moment = quantity.startswith("M")
    if not is_moment and not quantity.startswith("F"):
        print(f"Unsupported quantity '{quantity}'.  Use 'M*' or 'F*'.")
        return None

    # ── Resolve geometry ──────────────────────────────────────────
    data = _resolve_mesh_data(source, collapse_to_parents=collapse_to_parents)
    nodes = data["nodes"]
    frames = data["frames"]

    # ── Resolve force data ────────────────────────────────────────
    # force_map: {(ni_tag, nj_tag, idx): {Fx, Fy, Fz, Mx, My, Mz,
    #                                      Fx_j, Fy_j, Fz_j, Mx_j, My_j, Mz_j}}
    # The idx is the frame index in the resolved data for traceability.
    is_npz = isinstance(source, _NPZ_TYPES)

    if is_npz:
        # Extract static case name
        _combo = force_data if isinstance(force_data, str) else combo
        case_prefix = _resolve_npz_static_case(source, _combo)
        force_map = _extract_npz_frame_forces(source, case_prefix, frames)
    else:
        # Builder path — use provided force_data
        if force_data is None:
            print("force_data is required when source is a builder.")
            return None
        # Hoist invariant builder/model/elements resolution
        builder = source
        model = (builder.model if hasattr(builder, 'model')
                 else builder.mesh_model)
        elements = (builder.split_elements if hasattr(builder, 'split_elements')
                    and builder.split_elements else model.frame_elements)
        # Build a {(ni_tag, nj_tag): elem_tag} lookup once
        elem_by_node_pair: Dict[Tuple[int, int], int] = {}
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            eni = model.nodes.get(elem.node_i)
            enj = model.nodes.get(elem.node_j)
            if eni is None or enj is None:
                continue
            elem_by_node_pair[(eni.node_tag, enj.node_tag)] = elem.elem_tag

        force_map = {}
        for idx, fr in enumerate(frames):
            ni_tag = fr.get("ni_tag")
            nj_tag = fr.get("nj_tag")
            if ni_tag is None:
                # Builder path uses ni_id/nj_id (string ids)
                nid_i = fr["ni_id"]
                nid_j = fr["nj_id"]
                nd_i = model.nodes.get(nid_i)
                nd_j = model.nodes.get(nid_j)
                if nd_i is None or nd_j is None:
                    continue
                ni_tag = nd_i.node_tag
                nj_tag = nd_j.node_tag

            target_tag = elem_by_node_pair.get((ni_tag, nj_tag))
            if target_tag is not None and target_tag in force_data:
                force_map[idx] = force_data[target_tag]

    if not force_map:
        print(f"No {quantity} data to plot.")
        return None

    # ── Build flag/tube segments ──────────────────────────────────
    # Each segment: (p_i, p_j, vn, v_i, v_j)  for flags
    #               (p_i, p_j, val)            for tubes
    model_height = 0.0
    max_abs_val = 0.0
    segments = []
    values = []  # for tube mode

    for idx, fr in enumerate(frames):
        if idx not in force_map:
            continue
        f_local = _compute_local_forces(source, fr, nodes, force_map[idx],
                                        quantity)
        if f_local is None:
            continue

        # Node coordinates
        ni = _resolve_frame_node(nodes, fr, 'i')
        nj = _resolve_frame_node(nodes, fr, 'j')
        if ni is None or nj is None:
            continue

        p_i = np.array([ni["x"], ni["y"], ni["z"]])
        p_j = np.array([nj["x"], nj["y"], nj["z"]])
        model_height = max(model_height, ni["z"], nj["z"])

        v_i = f_local.get(quantity, 0.0)
        v_j = f_local.get(quantity + '_j', 0.0)
        max_abs_val = max(max_abs_val, abs(v_i), abs(v_j))

        if mode == "flag":
            vn = _compute_flag_direction(f_local, fr, nodes, quantity)
            segments.append((p_i, p_j, vn, v_i, v_j))
        else:
            values.append((p_i, p_j, (v_i + v_j) * 0.5))

    if not segments and not values:
        print(f"No {quantity} data to plot.")
        return None

    # ── Auto-scale ────────────────────────────────────────────────
    if mode == "flag" and moment_scale is None:
        moment_scale = (model_height * 0.2) / max(max_abs_val, 1.0)

    # ── Render ────────────────────────────────────────────────────
    pv.set_plot_theme("document")
    plotter = pv.Plotter(notebook=notebook, **kwargs)

    if show_original:
        for p_i, p_j, *_ in segments if mode == "flag" else \
                [(p_i, p_j) for p_i, p_j, _ in values]:
            n = max(2, int(np.linalg.norm(p_j - p_i) * 2))
            poly = pv.lines_from_points(np.linspace(p_i, p_j, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=1,
                             opacity=0.4)

    if mode == "flag":
        for p_i, p_j, vn, Fi, Fj in segments:
            for verts, col_val in compute_flag_parts(
                p_i, p_j, vn, Fi, Fj, moment_scale,
            ):
                _add_coloured_poly(plotter, verts, col_val, max_abs_val)
    else:
        for p_i, p_j, val in values:
            _add_coloured_tube(plotter, p_i, p_j, val, max_abs_val)

    kind = "Moment" if is_moment else "Force"
    plotter.add_text(f"{quantity}  (red = +ve, blue = −ve)",
                     position='lower_edge', font_size=10)
    if title:
        plotter.add_text(title, position='upper_edge', font_size=12)
    _set_isometric_view(plotter)

    if notebook:
        return plotter
    plotter.show()
    return None


# ── Internal helpers for unified force diagram ────────────────────────────

def _resolve_npz_static_case(source: dict, combo: str = None) -> str:
    """Determine the NPZ static case prefix (e.g. ``'static/DEAD/'``)."""
    from ..io.npz_reader import _get_static_cases
    cases = _get_static_cases(source)
    if not cases:
        raise ValueError("No static cases found in NPZ data.")
    name = combo if combo and combo in cases else cases[0]
    return f"static/{name}/"


def _extract_npz_frame_forces(source, case_prefix, frames):
    """Build force_map from NPZ arrays for the given static case.

    Returns ``{idx: {Fx, Fy, Fz, Mx, My, Mz, Fx_j, ..., local variants}}``.
    """
    import numpy as np
    force_map = {}
    qty_list = ['fx', 'fy', 'fz', 'mx', 'my', 'mz']
    for idx in range(len(frames)):
        entry = {}
        for q in qty_list:
            key_i = f"{case_prefix}{q}_i"
            key_j = f"{case_prefix}{q}_j"
            arr_i = source.get(key_i)
            arr_j = source.get(key_j)
            if arr_i is not None and idx < len(arr_i):
                entry[q.upper()] = float(arr_i[idx])
            if arr_j is not None and idx < len(arr_j):
                entry[f"{q.upper()}_j"] = float(arr_j[idx])
            # Local variants
            loc_i = f"{case_prefix}{q}_i_local"
            loc_j = f"{case_prefix}{q}_j_local"
            if loc_i in source:
                entry[f"{q.upper()}_i_local"] = float(source[loc_i][idx])
            if loc_j in source:
                entry[f"{q.upper()}_j_local"] = float(source[loc_j][idx])
        if entry:
            force_map[idx] = entry
    return force_map


def _compute_local_forces(source, fr, nodes, force_entry, quantity):
    """Transform global forces to local for one frame element.

    Local axes are computed from the element geometry via
    ``get_SAP_vecxz``, so the result is the same regardless of
    whether *source* is a builder or NPZ dict.  When pre-computed
    local arrays exist in the NPZ (``*_i_local`` / ``*_j_local``)
    those are used directly.
    """
    import numpy as np

    # If pre-computed local values exist in the entry, use them directly
    q_upper = quantity.upper()
    loc_key = f"{q_upper}_i_local"
    loc_key_j = f"{q_upper}_j_local"
    if loc_key in force_entry and loc_key_j in force_entry:
        return {
            quantity: force_entry[loc_key],
            f"{quantity}_j": force_entry[loc_key_j],
        }

    # Otherwise compute from global forces
    # Get node coordinates for element axis
    ni = _resolve_frame_node(nodes, fr, 'i')
    nj = _resolve_frame_node(nodes, fr, 'j')
    if ni is None or nj is None:
        return None

    axis = np.array([nj["x"] - ni["x"], nj["y"] - ni["y"],
                     nj["z"] - ni["z"]])
    axis_len = np.linalg.norm(axis)
    if axis_len < 1e-12:
        return None
    axis = axis / axis_len

    # Compute local axes
    try:
        vx, vy, vz = get_local_axes(axis)
    except Exception:
        return None

    T = np.vstack([vx, vy, vz])  # (3, 3) local ← global

    # Extract global forces
    def _g(q):
        return force_entry.get(q, force_entry.get(q.lower(), 0.0))

    f_i = np.array([_g('FX'), _g('FY'), _g('FZ')])
    m_i = np.array([_g('MX'), _g('MY'), _g('MZ')])
    f_j = np.array([_g('FX_j'), _g('FY_j'), _g('FZ_j')])
    m_j = np.array([_g('MX_j'), _g('MY_j'), _g('MZ_j')])

    f_i_loc = T @ f_i
    m_i_loc = T @ m_i
    f_j_loc = T @ f_j
    m_j_loc = T @ m_j

    return {
        'Fx': f_i_loc[0], 'Fy': f_i_loc[1], 'Fz': f_i_loc[2],
        'Mx': m_i_loc[0], 'My': m_i_loc[1], 'Mz': m_i_loc[2],
        'Fx_j': f_j_loc[0], 'Fy_j': f_j_loc[1], 'Fz_j': f_j_loc[2],
        'Mx_j': m_j_loc[0], 'My_j': m_j_loc[1], 'Mz_j': m_j_loc[2],
    }


def _compute_flag_direction(f_local, fr, nodes, quantity):
    """Determine the flag extrusion direction (vn) for a frame element."""
    import numpy as np
    axis = _get_element_axis(fr, nodes)
    if axis is None:
        return np.array([0.0, 1.0, 0.0])
    try:
        _, vy, vz = get_local_axes(axis)
    except Exception:
        vy = np.array([0.0, 1.0, 0.0])
        vz = np.array([0.0, 0.0, 1.0])

    if quantity == "Fx":
        return vz.copy()
    elif quantity == "Fy":
        return vy.copy()
    elif quantity == "Fz":
        return vz.copy()
    elif quantity == "Mx":
        return vy.copy()
    elif quantity == "My":
        return -vz.copy()
    elif quantity == "Mz":
        return vy.copy()
    else:
        return vz.copy()


def _get_element_axis(fr, nodes):
    """Return unit vector along a frame element from resolved data."""
    import numpy as np
    ni = _resolve_frame_node(nodes, fr, 'i')
    nj = _resolve_frame_node(nodes, fr, 'j')
    if ni is None or nj is None:
        return None
    d = np.array([nj["x"] - ni["x"], nj["y"] - ni["y"],
                  nj["z"] - ni["z"]])
    norm = np.linalg.norm(d)
    return d / norm if norm > 1e-12 else None


def _add_coloured_poly(plotter, verts, col_val, max_abs_val):
    """Add a coloured polygon to the plotter (flag mode)."""
    import numpy as np
    import pyvista as pv
    pts_arr = np.array(verts)
    n = len(verts)
    surf = pv.PolyData(pts_arr, faces=[n] + list(range(n)))
    t = min(abs(col_val) / max(max_abs_val, 1.0), 1.0)
    if col_val >= 0:
        colour = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
    else:
        colour = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
    plotter.add_mesh(surf, color=colour, opacity=0.85,
                     show_edges=False, smooth_shading=False, lighting=False)


def _add_coloured_tube(plotter, p_i, p_j, val, max_abs_val):
    """Add a coloured cylinder to the plotter (tube mode)."""
    import numpy as np
    import pyvista as pv
    axis = p_j - p_i
    axis_len = np.linalg.norm(axis)
    if axis_len < 1e-12:
        return
    p_mid = (p_i + p_j) / 2.0
    direction = axis / axis_len
    t = min(abs(val) / max(max_abs_val, 1.0), 1.0)
    radius = max(axis_len * 0.02, 0.05)
    if val >= 0:
        colour = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
    else:
        colour = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
    cyl = pv.Cylinder(center=p_mid, direction=direction, radius=radius,
                      height=axis_len * 0.9)
    plotter.add_mesh(cyl, color=colour, opacity=0.5, show_edges=False,
                     lighting=False)


# ============================================================================
# 3D moment diagram (PyVista) — extruded flags on the tension side
# ============================================================================

def plot_static_moment_3d(
    builder,
    elem_forces: Dict[int, Dict[str, float]],
    quantity: str = 'My',
    mode: str = 'flag',
    moment_scale: float = None,
    show_original: bool = True,
    show_reactions: bool = False,
    static_results: Optional[Dict[str, Any]] = None,
    selection: Optional['Selection'] = None,
    notebook: bool = False,
    title: str = None,
    **kwargs,
) -> Optional[Any]:
    """Draw a moment or force diagram in 3D on the structure.

    .. deprecated::
       Use :func:`plot_force_diagram_3d` instead — it works with both
       builder and NPZ/HDF5 data.

    Supports both moment quantities (``'My'``, ``'Mz'``, ``'Mx'``) and
    force quantities (``'Fx'``, ``'Fy'``, ``'Fz'``).

    Two display modes are available:

    * ``mode='flag'`` (default) — planar quadrilaterals extruded
      perpendicular to each member.  The flag height is proportional to
      the quantity magnitude.
    * ``mode='tube'`` — each element drawn as a coloured tube with a
      diverging red‑white‑blue colour map (blue = −ve, red = +ve).

    For the flag mode with moment quantities:

    * ``'My'`` — flags extend in the local **z** direction (bending about Y).
    * ``'Mz'`` — flags extend in the local **y** direction (bending about Z).

    For force quantities the flags extend in a world‑perpendicular
    direction (no J‑end sign flip).

    Uses **local** forces via :func:`_get_local_end_forces` so the sign
    and direction are correct regardless of member orientation.

    When ``show_reactions=True``, reaction forces at restrained nodes are
    drawn as coloured arrows (red = horizontal, green = vertical).

    Args:
        builder: Built ``AnalysisBuilder``.
        elem_forces: Dict from ``builder.extract_static_element_forces()``.
        quantity: ``'My'`` or ``'Mz'``.
        mode: ``'flag'`` (extruded flags) or ``'tube'`` (colour‑coded tubes).
        moment_scale: Extrusion length per unit moment (flag mode only).
                      If ``None``, auto‑scaled so the largest flag is
                      10 % of the model height.
        show_original: If True, draw the centreline in grey.
        show_reactions: If True, draw reaction arrows at restrained nodes.
        static_results: Dict from ``builder.run_static_analysis()``, required
                        when ``show_reactions=True``.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        notebook: If True, return plotter for Jupyter.
        title: Optional title string displayed at the top of the plot.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Requires:
        ``pyvista``.
    """
    warnings.warn(
        "plot_static_moment_3d is deprecated — use plot_force_diagram_3d() "
        "instead (works with builder or NPZ data)",
        DeprecationWarning, stacklevel=2,
    )
    try:
        import pyvista as pv
    except ImportError:
        print("Warning: pyvista not installed.  Install with: pip install pyvista")
        return None

    pv.set_plot_theme("document")

    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}

    if mode == 'flag':
        plotter = _plot_moment_flags(builder, elements, elem_forces, quantity,
                                     moment_scale, show_original, notebook,
                                     title=title, **kwargs)
    elif mode == 'tube':
        plotter = _plot_moment_tubes(builder, elements, elem_forces, quantity,
                                     show_original, notebook,
                                     title=title, **kwargs)
    else:
        print(f"Unknown mode '{mode}'.  Use 'flag' or 'tube'.")
        return None

    # Add reaction arrows if requested
    if show_reactions and static_results is not None and plotter is not None:
        _add_reaction_arrows(plotter, builder, static_results)

    if plotter is not None and notebook:
        return plotter
    return plotter  # the flag/tube function already called show()


def _get_local_end_forces(builder, elem, tag, elem_forces):
    """Transform global end forces to local coordinates for one element.

    Returns dict with local ``Fx``, ``Fy``, ``Fz``, ``Mx``, ``My``, ``Mz``
    and their ``_j`` counterparts, or ``None`` if axes cannot be computed.
    """
    import numpy as np
    try:
        vx, vy, vz = builder._get_local_axes(elem)
    except Exception:
        return None
    T = np.vstack([vx, vy, vz])  # (3, 3) local ← global
    f = elem_forces.get(tag, {})
    f_i = np.array([f.get('Fx', 0.0), f.get('Fy', 0.0), f.get('Fz', 0.0)])
    m_i = np.array([f.get('Mx', 0.0), f.get('My', 0.0), f.get('Mz', 0.0)])
    f_j = np.array([f.get('Fx_j', 0.0), f.get('Fy_j', 0.0), f.get('Fz_j', 0.0)])
    m_j = np.array([f.get('Mx_j', 0.0), f.get('My_j', 0.0), f.get('Mz_j', 0.0)])
    f_i_loc = T @ f_i
    m_i_loc = T @ m_i
    f_j_loc = T @ f_j
    m_j_loc = T @ m_j
    return {
        'Fx': f_i_loc[0], 'Fy': f_i_loc[1], 'Fz': f_i_loc[2],
        'Mx': m_i_loc[0], 'My': m_i_loc[1], 'Mz': m_i_loc[2],
        'Fx_j': f_j_loc[0], 'Fy_j': f_j_loc[1], 'Fz_j': f_j_loc[2],
        'Mx_j': m_j_loc[0], 'My_j': m_j_loc[1], 'Mz_j': m_j_loc[2],
    }


# Convenience wrappers for shear, axial, and other force diagrams
def plot_static_shear_3d(builder, elem_forces, quantity='Fz', **kwargs):
    """3D shear force diagram — convenience wrapper.

    .. deprecated::
       Deprecated together with :func:`plot_static_moment_3d`.

    Parameters
    ----------
    quantity : str
        ``'Fz'`` (default), ``'Fy'``, or ``'Fx'``.
    **kwargs
        Passed through to :func:`plot_static_moment_3d`.
    """
    return plot_static_moment_3d(builder, elem_forces, quantity=quantity, **kwargs)


def plot_static_axial_3d(builder, elem_forces, **kwargs):
    """3D axial force diagram — convenience wrapper.

    .. deprecated::
       Deprecated together with :func:`plot_static_moment_3d`.

    Parameters
    ----------
    **kwargs
        Passed through to :func:`plot_static_moment_3d`.
    """
    return plot_static_moment_3d(builder, elem_forces, quantity='Fx', **kwargs)


def _add_reaction_arrows(plotter, builder, static_results):
    """Add coloured arrows at restrained nodes showing reaction forces.

    Red arrows = horizontal resultant (fx, fy).
    Green arrows = vertical (fz).
    Arrow length is proportional to force magnitude, auto-scaled to 10 %
    of the model height for the largest force.
    """
    import numpy as np
    import pyvista as pv

    reactions = static_results.get("nodal_reactions", {})
    if not reactions:
        return

    # Compute model height from builder nodes
    z_vals = [n.z for n in builder.model.nodes.values()]
    z_range = max(z_vals) - min(z_vals) if z_vals else 1.0

    max_horiz = 0.0
    max_vert = 0.0
    arrow_data: list = []
    for nid_tag, r in reactions.items():
        fx, fy, fz = r[0], r[1], r[2]
        # Find the node by tag
        for node in builder.model.nodes.values():
            if node.node_tag == nid_tag:
                pos = np.array([node.x, node.y, node.z])
                break
        else:
            continue
        horiz = math.hypot(fx, fy)
        vert = abs(fz)
        if horiz > 1e-6:
            max_horiz = max(max_horiz, horiz)
            arrow_data.append(("horiz", pos, np.array([fx, fy, 0.0]), horiz))
        if vert > 1e-6:
            max_vert = max(max_vert, vert)
            arrow_data.append(("vert", pos, np.array([0.0, 0.0, fz]), vert))

    scale_h = (z_range * 0.08) / max(max_horiz, 1.0)
    scale_v = (z_range * 0.08) / max(max_vert, 1.0)

    for atype, pos, vec, mag in arrow_data:
        scale = scale_h if atype == "horiz" else scale_v
        arrow = pv.Arrow(start=pos, direction=vec / max(mag, 1e-12),
                         scale=mag * scale)
        colour = (0.9, 0.1, 0.1) if atype == "horiz" else (0.1, 0.8, 0.1)
        plotter.add_mesh(arrow, color=colour, opacity=0.85)


def _plot_moment_flags(builder, elements, elem_forces, quantity,
                       moment_scale, show_original, notebook,
                       title=None, **kwargs):
    """Flag‑based force/moment diagram (extruded on tension/sign side).

    Uses **local** forces so the flag always extends perpendicular to the
    member axis:

    * Moment quantities (``'Mz'``, ``'My'``, ``'Mx'``): flags extend in the
      corresponding local direction, J‑end negated for bending convention.
    * Force quantities (``'Fx'``, ``'Fy'``, ``'Fz'``): flags extend in a
      world‑perpendicular direction, no sign flip.
    """
    import pyvista as pv

    is_moment = quantity.startswith("M")
    if not is_moment and not quantity.startswith("F"):
        print(f"Unsupported quantity '{quantity}'.  Use 'M*' or 'F*'.")
        return None

    model_height = 0.0
    max_val = 0.0
    flags = []
    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        tag = elem.elem_tag
        if tag not in elem_forces:
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        p_i = np.array([ni.x, ni.y, ni.z])
        p_j = np.array([nj.x, nj.y, nj.z])
        model_height = max(model_height, ni.z, nj.z)

        # Use local forces for consistent sign convention
        loc = _get_local_end_forces(builder, elem, tag, elem_forces)
        if loc is None:
            continue
        v_i = loc.get(quantity, 0.0)
        v_j = loc.get(quantity + '_j', 0.0)

        # Flag offset direction (vn) based on quantity
        # Positive Fi → offset in +vn at I-end
        # Positive Fj → offset in -vn at J-end (baked-in negation)
        try:
            vx_e, vy_e, vz_e = builder._get_local_axes(elem)
        except Exception:
            continue
        if quantity == "Fx":
            vn = np.array(vz_e)
        elif quantity == "Fy":
            vn = np.array(vy_e)
        elif quantity == "Fz":
            vn = np.array(vz_e)
        elif quantity == "Mx":
            vn = np.array(vy_e)
        elif quantity == "My":
            vn = -np.array(vz_e)
        elif quantity == "Mz":
            vn = np.array(vy_e)
        else:
            vn = np.array(vz_e)

        max_val = max(max_val, abs(v_i), abs(v_j))
        flags.append((p_i, p_j, vn, v_i, v_j))

    if not flags:
        print(f"No {quantity} data to plot.")
        return None

    if moment_scale is None:
        moment_scale = (model_height * 0.2) / max(max_val, 1.0)

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    if show_original:
        for p_i, p_j, _, _, _ in flags:
            n = max(2, int(np.linalg.norm(p_j - p_i) * 2))
            poly = pv.lines_from_points(np.linspace(p_i, p_j, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=1, opacity=0.4)

    for p_i, p_j, vn, Fi, Fj in flags:
        for verts, col_val in compute_flag_parts(p_i, p_j, vn, Fi, Fj, moment_scale):
            pts_arr = np.array(verts)
            n = len(verts)
            surf = pv.PolyData(pts_arr, faces=[n] + list(range(n)))
            t = min(abs(col_val) / max(max_val, 1.0), 1.0)
            if col_val >= 0:
                colour = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
            else:
                colour = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
            plotter.add_mesh(surf, color=colour, opacity=0.85,
                             show_edges=False, smooth_shading=False, lighting=False)

    kind = "Moment" if is_moment else "Force"
    plotter.add_text(f"{quantity} (local)  (red = +ve, blue = −ve)",
                     position='lower_edge', font_size=10)
    if title:
        plotter.add_text(title, position='upper_edge', font_size=12)
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


def _plot_moment_tubes(builder, elements, elem_forces, quantity,
                       show_original, notebook, title=None, **kwargs):
    """Tube‑based force/moment diagram (colour‑coded along element).

    Uses **local** forces for consistent colour mapping regardless of
    member orientation.  Works for both moment (``'M*'``) and force
    (``'F*'``) quantities.
    """
    import pyvista as pv

    values = []
    segments = []
    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        tag = elem.elem_tag
        if tag not in elem_forces:
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        loc = _get_local_end_forces(builder, elem, tag, elem_forces)
        if loc is None:
            continue
        val = loc.get(quantity, 0.0)
        values.append(val)
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        segments.append((p1, p2, val))

    if not segments:
        print(f"No {quantity} data to plot.")
        return None

    vlim = max(abs(min(values)), abs(max(values)), 1.0)

    plotter = pv.Plotter(notebook=notebook, **kwargs)
    plotter.set_background('white')

    if show_original:
        for p1, p2, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=1, opacity=0.3)

    for p1, p2, val in segments:
        n = max(8, int(np.linalg.norm(p2 - p1) * 4))
        poly = pv.lines_from_points(np.linspace(p1, p2, n))
        norm_val = val / vlim
        t = abs(norm_val)
        if norm_val >= 0:
            colour = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
        else:
            colour = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
        radius = 0.02 * max(np.linalg.norm(p2 - p1), 0.1)
        tube = poly.tube(radius=radius)
        plotter.add_mesh(tube, color=colour, smooth_shading=False, lighting=False)

    kind = "Moment" if quantity.startswith("M") else "Force"
    plotter.add_text(f"{quantity}  (red = +ve, blue = −ve)",
                     position='lower_edge', font_size=10)
    if title:
        plotter.add_text(title, position='upper_edge', font_size=12)
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


# ============================================================================
# 2D force diagram (Matplotlib) — moment / shear vs elevation
# ============================================================================

def plot_static_force_diagram(
    builder,
    elem_forces: Dict[int, Dict[str, float]],
    quantity: str = 'Fz',
    title: str = None,
    selection: Optional['Selection'] = None,
    figsize=(6, 8),
    use_local: bool = True,
    **kwargs,
) -> Optional[Any]:
    """Plot a static element force/moment quantity vs elevation.

    .. deprecated::
       Use :func:`plot_npz_force_diagram` instead for standalone NPZ
       data.  The builder‑only path will be replaced in a future release.

    When ``use_local=True`` (default), forces are transformed from global
    to **local** coordinates using the element's local axes
    (:meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder._get_local_axes`).
    This ensures that the quantity has a consistent physical meaning
    regardless of member orientation:

    ============  ======================================================
    Quantity      Local meaning
    ============  ======================================================
    ``'Fx'``      Axial force (+ = tension)
    ``'Fy'``      Shear in local y‑direction
    ``'Fz'``      Shear in local z‑direction
    ``'Mx'``      Torsion
    ``'My'``      Bending about local y‑axis (minor)
    ``'Mz'``      Bending about local z‑axis (major)
    ============  ======================================================

    When ``use_local=False``, the raw global forces are plotted.

    For **moment** quantities, the J‑end value is negated so that the
    line connects I‑end → J‑end in the standard bending‑moment diagram
    convention (positive = tension on the same face of the member).

    For **force** quantities, both ends are plotted as‑is.

    The vertical axis shows Z‑elevation for all members.  This is most
    useful for vertical columns and walls.  Horizontal beams will plot
    their I‑end and J‑end at different elevations, showing the moment
    variation along their span.

    Args:
        builder: Built ``AnalysisBuilder``.
        elem_forces: Dict from ``builder.extract_static_element_forces()``.
        quantity: Force key — ``'Fx'``, ``'Fy'``, ``'Fz'``,
                  ``'Mx'``, ``'My'``, ``'Mz'``.
        title: Optional title.  Auto‑generated if omitted.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        figsize: Matplotlib figure size ``(width, height)``.
        use_local: If True (default), transform to local coordinates.
        **kwargs: Passed to ``matplotlib.pyplot.plot()``.

    Returns:
        The ``matplotlib.figure.Figure``.
    """
    warnings.warn(
        "plot_static_force_diagram is deprecated — use "
        "plot_npz_force_diagram() instead for standalone NPZ data, "
        "or plot_force_diagram_3d() for unified builder/NPZ support.",
        DeprecationWarning, stacklevel=2,
    )
    import matplotlib.pyplot as plt
    import numpy as np

    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}

    is_moment = quantity.startswith('M')
    j_key = quantity + '_j' if is_moment else quantity + '_j'

    # ── Helper: get local end forces for one element ──
    def _local_end_forces(elem, tag) -> dict:
        """Transform global end forces to local coordinates."""
        try:
            vx, vy, vz = builder._get_local_axes(elem)
        except Exception:
            return None
        # Build 3×3 rotation matrix (local ← global)
        T = np.vstack([vx, vy, vz])  # (3, 3)
        # Extract global force & moment vectors at I-end
        f_i_global = np.array([
            elem_forces[tag].get('Fx', 0.0),
            elem_forces[tag].get('Fy', 0.0),
            elem_forces[tag].get('Fz', 0.0),
        ])
        m_i_global = np.array([
            elem_forces[tag].get('Mx', 0.0),
            elem_forces[tag].get('My', 0.0),
            elem_forces[tag].get('Mz', 0.0),
        ])
        f_j_global = np.array([
            elem_forces[tag].get('Fx_j', 0.0),
            elem_forces[tag].get('Fy_j', 0.0),
            elem_forces[tag].get('Fz_j', 0.0),
        ])
        m_j_global = np.array([
            elem_forces[tag].get('Mx_j', 0.0),
            elem_forces[tag].get('My_j', 0.0),
            elem_forces[tag].get('Mz_j', 0.0),
        ])
        # Transform: local = T @ global
        f_i_local = T @ f_i_global
        m_i_local = T @ m_i_global
        f_j_local = T @ f_j_global
        m_j_local = T @ m_j_global
        return {
            'Fx': f_i_local[0], 'Fy': f_i_local[1], 'Fz': f_i_local[2],
            'Mx': m_i_local[0], 'My': m_i_local[1], 'Mz': m_i_local[2],
            'Fx_j': f_j_local[0], 'Fy_j': f_j_local[1], 'Fz_j': f_j_local[2],
            'Mx_j': m_j_local[0], 'My_j': m_j_local[1], 'Mz_j': m_j_local[2],
        }

    # Collect (z, value) pairs — two per element (I‑end and J‑end)
    z_coords: List[float] = []
    values: List[float] = []
    segments: List[List[int]] = []  # each = [idx_i, idx_j]

    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        tag = elem.elem_tag
        if tag not in elem_forces:
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue

        # Get the appropriate force dict (local or global)
        if use_local:
            f_local = _local_end_forces(elem, tag)
            if f_local is None:
                continue
            src = f_local
        else:
            src = elem_forces[tag]

        val_i = src.get(quantity, 0.0)
        # Negate J‑end for consistent diagram convention
        if j_key and j_key in src:
            val_j = -src.get(j_key, 0.0)
        else:
            val_j = val_i

        idx_i = len(z_coords)
        z_coords.append(ni.z)
        values.append(val_i)

        idx_j = len(z_coords)
        z_coords.append(nj.z)
        values.append(val_j)

        segments.append([idx_i, idx_j])

    if not values:
        print("No element force data to plot.")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    # Plot each element as a solid line segment
    line_kw = {k: v for k, v in kwargs.items()
               if k not in ('marker', 'linestyle')}

    for seg in segments:
        ax.plot([values[seg[0]], values[seg[1]]],
                [z_coords[seg[0]], z_coords[seg[1]]],
                **line_kw,
                )

    # Markers at the data points
    ax.plot(values, z_coords,
            **kwargs,
            linestyle='',
            )

    # Unit label
    unit_label = builder.units.get('F', 'N')
    if is_moment:
        length_unit = builder.units.get('L', 'm')
        unit_label = f"{unit_label}·{length_unit}"

    local_tag = " (local)" if use_local else ""
    ax.axvline(0, color='grey', linewidth=0.8, linestyle='--', alpha=0.5)
    ax.set_xlabel(f"{quantity}{local_tag} ({unit_label})")
    ax.set_ylabel("Elevation (m)")
    ax.set_title(title or f"{quantity}{local_tag} vs elevation")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig


def plot_force_diagram(
    elem_results: List[Dict[str, Any]],
    quantity: str = 'My_i',
    title: str = None,
    figsize=(6, 8),
    **kwargs,
) -> Optional[Any]:
    """Plot a CQC-combined force/moment quantity vs elevation.

    This produces a 2D line plot of the chosen quantity (e.g. ``'My_i'``,
    ``'Mz_i'``, ``'Vz_i'``, ``'Vy_i'``) at the I‑end of each element,
    plotted against the element's mid‑height.

    Args:
        elem_results: List of dicts from
                      ``builder.extract_element_rs_forces()['element_results']``.
        quantity: The result key to plot (e.g. ``'My_i'``, ``'Vz_i'``).
        title: Optional plot title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.
        **kwargs: Passed to ``matplotlib.pyplot.plot()``.

    Returns:
        The ``matplotlib.figure.Figure`` (so the caller can ``.savefig()`` or
        ``.show()``).

    Requires:
        ``matplotlib``.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  "
              "Install with: pip install matplotlib")
        return None

    if not elem_results:
        print("No element results to plot.")
        return None

    # Sort by elevation and extract
    sorted_res = sorted(elem_results, key=lambda r: r['z_mid'])
    z = [r['z_mid'] for r in sorted_res]
    vals = [r.get(quantity, 0.0) for r in sorted_res]

    # Determine unit label
    q = quantity.lower()
    if q.startswith('m'):
        unit = 'kN·m'
        quantity_label = quantity
    elif q.startswith('v'):
        unit = 'kN'
        quantity_label = quantity
    else:
        unit = ''
        quantity_label = quantity

    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(vals, z, '-o', **kwargs or {})
    ax.set_xlabel(f'{quantity_label} ({unit})')
    ax.set_ylabel('Elevation (m)')
    ax.set_title(title or f'{quantity_label} vs Elevation (CQC combined)')
    ax.grid(True, alpha=0.3)
    ax.axvline(0, color='grey', linewidth=0.5)

    fig.tight_layout()
    return fig


# ============================================================================
# Pushover capacity curve (Matplotlib)
# ============================================================================

def plot_pushover_curve(
    pushover_results: Dict[str, Any],
    title: str = None,
    figsize=(8, 6),
    **kwargs,
) -> Optional[Any]:
    """Plot the pushover capacity curve (base shear vs control displacement).

    Args:
        pushover_results: Output dict from
            :meth:`AnalysisBuilder.run_pushover_analysis`.
        title: Optional title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.
        **kwargs: Passed to ``matplotlib.pyplot.plot()``.

    Returns:
        The ``matplotlib.figure.Figure`` so the caller can ``.savefig()`` or
        ``.show()``.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  "
              "Install with: pip install matplotlib")
        return None

    disp = pushover_results.get('control_disp', [])
    shear = pushover_results.get('base_shear', [])

    if not disp or not shear:
        print("No pushover data to plot.")
        return None

    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(disp, shear, '-o', markersize=3, **kwargs or {})
    ax.set_xlabel('Control node displacement (m)')
    ax.set_ylabel('Base shear (kN)')
    ax.set_title(title or 'Pushover Capacity Curve')
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color='grey', linewidth=0.5)
    ax.axvline(0, color='grey', linewidth=0.5)

    fig.tight_layout()
    return fig


def plot_pushover_curve_enhanced(
    pushover_results: Dict[str, Any],
    title: str = None,
    figsize=(10, 6),
    design_disp: Optional[float] = None,
    unit_conversion: float = 1.0,
    **kwargs,
) -> Optional[Any]:
    """Enhanced pushover capacity curve with stiffness indicators.

    Plots base shear vs control displacement with:
    - **Initial and final tangent stiffness** (dashed lines)
    - **Area fill** under the curve
    - **Design drift marker** (optional vertical line)
    - Stiffness loss percentage annotation

    Args:
        pushover_results: Dict with ``'control_disp'`` and ``'base_shear'``
            keys (lists or arrays).  The :meth:`AnalysisBuilder.run_pushover_analysis`
            output uses kN; Tcl‑based output may use N — use *unit_conversion*.
        title: Plot title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.
        design_disp: Optional design drift displacement (m) to mark with a
            vertical dotted line.
        unit_conversion: Multiply base_shear by this factor (e.g. ``1/1000``
            if Tcl output is in N and desired display is kN).
        **kwargs: Passed to ``matplotlib.pyplot.plot()`` for the main curve.

    Returns:
        The ``matplotlib.figure.Figure``.
    """
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("Warning: matplotlib not installed.  "
              "Install with: pip install matplotlib")
        return None

    disp = np.asarray(pushover_results.get('control_disp', []), dtype=float)
    shear = np.asarray(pushover_results.get('base_shear', []), dtype=float)
    shear = shear * unit_conversion

    if len(disp) < 2 or len(shear) < 2 or len(disp) != len(shear):
        print("No pushover data to plot.")
        return None

    # Robust tangent stiffness via local regression over end segments
    n = len(disp)
    window = max(2, n // 20)  # 5 % of data points, at least 2
    # Initial stiffness: fit slope over first `window` points
    k0 = 0.0
    if disp[window - 1] > disp[0]:
        coeffs = np.polyfit(disp[:window], shear[:window], 1)
        k0 = coeffs[0]
    # Final stiffness: fit slope over last `window` points
    kf = 0.0
    if disp[-1] > disp[-window]:
        coeffs = np.polyfit(disp[-window:], shear[-window:], 1)
        kf = coeffs[0]
    loss_pct = (1 - kf / k0) * 100 if k0 > 0 else 0.0

    fig, ax = plt.subplots(figsize=figsize)

    # Main curve — merge caller kwargs with defaults
    plot_kw = dict(label='Pushover curve', linewidth=2)
    plot_kw.update(kwargs)
    ax.plot(disp, shear, 'b-', **plot_kw)

    # Area fill
    ax.fill_between(disp, 0, shear, alpha=0.08, color='blue')

    # Initial stiffness line
    if k0 > 0:
        ax.plot(disp, k0 * disp, 'r--', linewidth=1, alpha=0.5,
                label=f'Initial stiffness ({k0:.0f} kN/m)')

    # Final stiffness line
    if kf > 0:
        ax.plot(disp, kf * disp, 'g--', linewidth=1, alpha=0.5,
                label=f'Final stiffness ({kf:.0f} kN/m)')

    # Design drift marker
    if design_disp is not None:
        ax.axvline(design_disp, color='orange', linestyle=':', linewidth=1.5,
                   label=f'Design drift ({design_disp:.3f} m)')

    # Labels & title
    ax.set_xlabel('Control node displacement (m)')
    ax.set_ylabel('Base shear (kN)')
    ax.set_title(title or 'Pushover Capacity Curve')
    ax.legend(loc='lower right', fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color='grey', linewidth=0.5)
    ax.axvline(0, color='grey', linewidth=0.5)

    # Stiffness loss annotation
    ax.annotate(
        f'Stiffness loss: {loss_pct:.1f}%',
        xy=(0.97, 0.03), xycoords='axes fraction',
        ha='right', va='bottom', fontsize=10,
        bbox=dict(boxstyle='round,pad=0.3', fc='lightyellow', ec='gray', alpha=0.8),
    )

    fig.tight_layout()
    return fig


def plot_capacity_spectrum(
    capacity_adrs: Dict[str, List[float]],
    spectrum_periods: List[float],
    spectrum_accels: List[float],
    performance_point: Dict[str, Any] = None,
    title: str = None,
    figsize=(8, 6),
) -> Optional[Any]:
    """Plot the capacity spectrum in ADRS format, overlaid on the demand
    response spectrum.

    Args:
        capacity_adrs: ADRS curve from
            :meth:`AnalysisBuilder.pushover_to_adrs` (dict with keys
            ``'S_a'`` and ``'S_d'``).
        spectrum_periods: Periods (s) defining the elastic demand spectrum.
        spectrum_accels: Spectral accelerations (m/s²) corresponding to
            *spectrum_periods*.
        performance_point: Optional result dict from
            :meth:`AnalysisBuilder.compute_performance_point`.  If provided
            the bilinear yield point and performance point are annotated.
        title: Optional title.  Auto‑generated if omitted.
        figsize: Matplotlib figure size ``(width, height)``.

    Returns:
        The ``matplotlib.figure.Figure``.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Warning: matplotlib not installed.  "
              "Install with: pip install matplotlib")
        return None

    S_d = np.array(capacity_adrs.get('S_d', []))
    S_a = np.array(capacity_adrs.get('S_a', []))

    if len(S_d) < 2 or len(S_a) < 2 or len(S_d) != len(S_a):
        print("Insufficient or mismatched ADRS data to plot.")
        return None

    if len(spectrum_periods) != len(spectrum_accels):
        print("spectrum_periods and spectrum_accels have different lengths.")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    # --- Capacity spectrum ---
    ax.plot(S_d, S_a, '-o', markersize=3, label='Capacity (pushover)',
            color='tab:blue', zorder=3)

    # --- Demand spectrum (period lines + curve) ---
    T_spec = np.array(spectrum_periods)
    Sa_spec = np.array(spectrum_accels)
    Sd_spec = Sa_spec * (T_spec / (2.0 * math.pi)) ** 2
    ax.plot(Sd_spec, Sa_spec, '--', label='Demand (elastic)',
            color='tab:red', zorder=2)

    # --- Constant-period lines ---
    T_labels = [0.1, 0.2, 0.5, 1.0, 2.0, 4.0]
    S_d_max = max(S_d.max(), Sd_spec.max()) * 1.15
    S_a_max = max(S_a.max(), Sa_spec.max()) * 1.15
    for T in T_labels:
        sd_line = np.linspace(0, S_d_max, 50)
        sa_line = (2.0 * math.pi / T) ** 2 * sd_line
        ax.plot(sd_line, sa_line, ':', color='grey', linewidth=0.5, alpha=0.4)
        ax.text(sd_line[-1], sa_line[-1], f'T={T}s', fontsize=7,
                color='grey', alpha=0.6, va='bottom')

    # --- Performance point ---
    if performance_point is not None:
        S_dp = performance_point.get('S_dp')
        S_ap = performance_point.get('S_ap')
        S_dy = performance_point.get('S_dy')
        S_ay = performance_point.get('S_ay')

        # Bilinear yield point
        if S_dy is not None and S_ay is not None and S_dy > 0:
            ax.plot(S_dy, S_ay, 's', color='tab:orange', markersize=8,
                    zorder=5, label=f'Yield ({S_dy:.3f}, {S_ay:.1f})')
            # Bilinear line
            sd_bilin = np.linspace(0, S_dy, 20)
            K_init = S_ay / S_dy
            ax.plot(sd_bilin, K_init * sd_bilin, '-', color='tab:orange',
                    linewidth=1.5, alpha=0.7)
            # Post-yield line
            if S_dp > S_dy and S_dp > 0:
                sd_post = np.linspace(S_dy, max(S_dp * 1.2, S_d.max()), 20)
                K_post = (S_ap - S_ay) / (S_dp - S_dy) if S_dp != S_dy else 0
                ax.plot(sd_post, S_ay + K_post * (sd_post - S_dy), '-',
                        color='tab:orange', linewidth=1.5, alpha=0.7)

        # Performance point
        if S_dp is not None and S_ap is not None and S_dp > 0:
            ax.plot(S_dp, S_ap, 'D', color='tab:green', markersize=10,
                    zorder=6, label=f'Perf. Pt. ({S_dp:.3f}, {S_ap:.1f})')
            # Vertical & horizontal dashed lines
            ax.axvline(S_dp, color='tab:green', linewidth=0.8, linestyle='--',
                       alpha=0.5)
            ax.axhline(S_ap, color='tab:green', linewidth=0.8, linestyle='--',
                       alpha=0.5)

    ax.set_xlabel('Spectral displacement S$_d$ (m)')
    ax.set_ylabel('Spectral acceleration S$_a$ (m/s²)')
    ax.set_title(title or 'Capacity Spectrum Method – ADRS Format')
    ax.set_xlim(0, S_d_max)
    ax.set_ylim(0, S_a_max)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)

    fig.tight_layout()
    return fig


# =========================================================================
# Standalone NPZ plotter
#
# These functions load a .npz results file (exported by
# AnalysisBuilder.export_results_to_npz) and generate plots without
# needing the original AnalysisBuilder or model objects.
# =========================================================================


def _load_npz_for_plotting(npz_path: str, combo: str = None) -> dict:
    """Load a unified-format NPZ results file and build element‑centric arrays.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    combo : str or None
        Static case name.  ``None`` = first available case.

    Returns a dict with:
        - elem_data: list of dicts (one per sub‑element) with keys:
            sap_id, (x|y|z)_i, (x|y|z)_j, mid_z,
            fx_i, fy_i, fz_i, mx_i, my_i, mz_i,
            fx_j, fy_j, fz_j, mx_j, my_j, mz_j,
            and ``_local`` variants
        - metadata: always ``{}`` (reserved for future use)
        - force_unit, length_unit: unit strings
        - raw_data: the loaded npz dict
    """
    from ..io.npz_reader import read_results, _get_static_cases
    d = read_results(npz_path)

    force_unit = "?"
    length_unit = "?"
    elem_data: List[dict] = []

    # ── Unified schema ────────────────────────────────────────────
    cases = _get_static_cases(d)
    if combo is not None and combo not in cases:
        raise ValueError(
            f"Case '{combo}' not found in NPZ. Available: {cases}")
    case = combo if combo and combo in cases else (cases[0] if cases else None)

    fu = d.get("force_unit")
    force_unit = str(fu[0]) if fu is not None and len(fu) else "?"
    lu = d.get("length_unit")
    length_unit = str(lu[0]) if lu is not None and len(lu) else "?"

    # Node coords lookup
    nid = d.get("node_tag", np.array([]))
    nx = d.get("node_x", np.array([]))
    ny = d.get("node_y", np.array([]))
    nz = d.get("node_z", np.array([]))
    node_coords = {}
    for i in range(len(nid)):
        node_coords[int(nid[i])] = (float(nx[i]), float(ny[i]), float(nz[i]))

    # Frame elements
    fi = d.get("frame_node_i", np.array([]))
    fj = d.get("frame_node_j", np.array([]))
    sap_ids = d.get("frame_sap_id", np.array([]))
    pre = f"static/{case}/" if case else ""

    for i in range(len(fi)):
        c_i = node_coords.get(int(fi[i]), (0, 0, 0))
        c_j = node_coords.get(int(fj[i]), (0, 0, 0))
        mid_z = (c_i[2] + c_j[2]) / 2.0

        def _g(k):
            arr = d.get(f"{pre}{k}")
            return float(arr[i]) if arr is not None else 0.0

        entry = {
            "sap_id": str(sap_ids[i]),
            "x_i": c_i[0], "y_i": c_i[1], "z_i": c_i[2],
            "x_j": c_j[0], "y_j": c_j[1], "z_j": c_j[2],
            "mid_z": mid_z,
            "fx_i": _g("fx_i"), "fy_i": _g("fy_i"), "fz_i": _g("fz_i"),
            "mx_i": _g("mx_i"), "my_i": _g("my_i"), "mz_i": _g("mz_i"),
            "fx_j": _g("fx_j"), "fy_j": _g("fy_j"), "fz_j": _g("fz_j"),
            "mx_j": _g("mx_j"), "my_j": _g("my_j"), "mz_j": _g("mz_j"),
        }
        # Local forces (optional) — set NaN when missing
        for q in ("fx", "fy", "fz", "mx", "my", "mz"):
            loc_i = f"{pre}{q}_i_local"
            loc_j = f"{pre}{q}_j_local"
            entry[f"{q}_i_local"] = float(d[loc_i][i]) if loc_i in d else np.nan
            entry[f"{q}_j_local"] = float(d[loc_j][i]) if loc_j in d else np.nan
        elem_data.append(entry)

    return {
        "elem_data": elem_data,
        "metadata": {},
        "force_unit": force_unit,
        "length_unit": length_unit,
        "raw_data": d,
    }


def plot_npz_force_diagram(
    npz_path: str,
    quantity: str = "Mz",
    use_local: bool = True,
    combo: str = None,
    title: Optional[str] = None,
    figsize: tuple = (8, 6),
) -> "plt.Figure":
    """2D diagram of a local force quantity vs elevation from an NPZ file.

    This is a **standalone** function — it does **not** require any
    ``AnalysisBuilder`` or model objects.  Just pass the path to a
    ``.npz`` file created by :meth:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder.export_results_to_npz`.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    quantity : str
        Force quantity to plot.  Prefix with ``'M'`` for moment or
        ``'F'`` for axial/shear.  Examples: ``'Mz'``, ``'My'``, ``'Mx'``,
        ``'Fx'``, ``'Fy'``, ``'Fz'``.
    use_local : bool
        If ``True`` (default) use local‑coordinate forces.
    title : str or None
        Plot title.  Auto‑generated from the quantity if *None*.
    figsize : tuple
        Figure size ``(width, height)`` in inches.

    Returns
    -------
    matplotlib.figure.Figure
    """
    from matplotlib import pyplot as plt

    info = _load_npz_for_plotting(npz_path, combo=combo)
    elem_data = info["elem_data"]
    force_unit = info["force_unit"]
    length_unit = info["length_unit"]

    suffix = "_local" if use_local else ""
    q_i = f"{quantity.lower()}_i{suffix}"
    q_j = f"{quantity.lower()}_j{suffix}"

    fig, ax = plt.subplots(figsize=figsize)

    for ed in elem_data:
        v_i = ed.get(q_i, np.nan)
        v_j = ed.get(q_j, np.nan)
        if np.isnan(v_i) or np.isnan(v_j):
            continue
        z_i = ed["z_i"]
        z_j = ed["z_j"]
        # Negate J‑end for forces only (axial/shear satisfy F_j = –F_i)
        if not quantity.startswith("M"):
            v_j = -v_j
        ax.plot([v_i, v_j], [z_i, z_j], color="tab:blue", lw=1.0, alpha=0.7)

    ax.axvline(0, color="grey", lw=0.5, ls="--")
    kind = "Bending moment" if quantity.startswith("M") else "Force"
    ax.set_xlabel(f"{kind} {quantity} [{force_unit}]" + (" (local)" if use_local else ""))
    ax.set_ylabel(f"Elevation [{length_unit}]")
    ax.set_title(title or f"{kind} {quantity} vs elevation — standalone NPZ")
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def plot_npz_moment_3d(
    npz_path: str,
    quantity: str = "Mz",
    use_local: bool = True,
    combo: str = None,
    mode: str = "flag",
    title: Optional[str] = None,
    show_scale: bool = True,
    return_plotter: bool = False,
) -> Any:
    """3D force diagram from an NPZ results file using PyVista.

    Standalone function — no ``AnalysisBuilder`` or model objects needed.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    quantity : str
        Quantity to plot, e.g. ``'Mz'``, ``'My'``, ``'Fx'``, ``'Fy'``, ``'Fz'``.
    use_local : bool
        Use local‑coordinate forces (default ``True``).
    mode : str
        ``'flag'`` (default) for thin perpendicular rectangles, ``'tube'``
        for extruded circles.
    title : str or None
        Plot title (auto‑generated if *None*).
    show_scale : bool
        Deprecated — ignored.  A text legend is shown instead.
    return_plotter : bool
        If ``True`` return the ``pyvista.Plotter`` instead of calling
        ``plotter.show()``.

    Returns
    -------
    pyvista.Plotter or None
    """
    try:
        import pyvista as pv
    except ImportError:
        print("pyvista is required.  pip install pyvista")
        return None

    info = _load_npz_for_plotting(npz_path, combo=combo)
    elem_data = info["elem_data"]

    suffix = "_local" if use_local else ""
    q_i = f"{quantity.lower()}_i{suffix}"
    q_j = f"{quantity.lower()}_j{suffix}"

    # ── Collect non‑NaN values for scaling ─────────────────────────
    max_abs_val = 0.0
    for ed in elem_data:
        v_i = ed.get(q_i, np.nan)
        v_j = ed.get(q_j, np.nan)
        if not np.isnan(v_i) and not np.isnan(v_j):
            max_abs_val = max(max_abs_val, abs(v_i), abs(v_j))

    if max_abs_val < 1e-15:
        print(f"All {quantity} values are zero — nothing to plot.")
        return None

    # Compute model height from element coordinates for auto-scaling
    model_height = max(max(ed["z_i"], ed["z_j"]) for ed in elem_data) - \
                   min(min(ed["z_i"], ed["z_j"]) for ed in elem_data)
    model_height = max(model_height, 1.0)
    # Flag scale: largest flag = 20 % of model height (same as builder version)
    moment_scale = (model_height * 0.2) / max(max_abs_val, 1.0)

    plotter = pv.Plotter()
    plotter.set_background('white')
    plotter.title = title or f"{quantity} 3D — standalone NPZ"

    # ── Draw original structure wireframe ───────────────────────────
    raw = info["raw_data"]
    n_tags = raw.get("node_tags")
    n_x = raw.get("node_x")
    n_y = raw.get("node_y")
    n_z = raw.get("node_z")
    sub_n_i = raw.get("sub_node_i_tag")
    sub_n_j = raw.get("sub_node_j_tag")
    if all(a is not None for a in (n_tags, n_x, n_y, n_z, sub_n_i, sub_n_j)):
        node_map = {int(n_tags[k]): (float(n_x[k]), float(n_y[k]), float(n_z[k]))
                    for k in range(len(n_tags))}
        lines = []
        for k in range(len(sub_n_i)):
            ci = node_map.get(int(sub_n_i[k]))
            cj = node_map.get(int(sub_n_j[k]))
            if ci and cj:
                lines.append([ci, cj])
        if lines:
            first = True
            for seg in lines:
                plotter.add_lines(np.array(seg), color="grey", width=1,
                                  label="Structure" if first else None)
                first = False

    is_moment = quantity.startswith("M")

    for idx, ed in enumerate(elem_data):
        v_i = ed.get(q_i, np.nan)
        v_j = ed.get(q_j, np.nan)
        if np.isnan(v_i) or np.isnan(v_j):
            continue
        p_i = np.array([ed["x_i"], ed["y_i"], ed["z_i"]])
        p_j = np.array([ed["x_j"], ed["y_j"], ed["z_j"]])
        p_mid = (p_i + p_j) / 2.0
        axis = p_j - p_i
        axis_len = np.linalg.norm(axis)
        if axis_len < 1e-12:
            continue
        axis = axis / axis_len

        # Flag offset direction (vn) based on quantity
        _, vec_y, vec_z = get_local_axes(axis)
        if quantity == "Fx":
            vn = vec_z
        elif quantity == "Fy":
            vn = vec_y
        elif quantity == "Fz":
            vn = vec_z
        elif quantity == "Mx":
            vn = vec_y
        elif quantity == "My":
            vn = -vec_z
        elif quantity == "Mz":
            vn = vec_y
        else:
            vn = vec_z

        if mode == "flag":
            for verts, col_val in compute_flag_parts(
                p_i, p_j, vn, v_i, v_j, moment_scale,
            ):
                pts_arr = np.array(verts)
                n = len(verts)
                surf = pv.PolyData(pts_arr, faces=[n] + list(range(n)))
                t = min(abs(col_val) / max_abs_val, 1.0)
                if col_val >= 0:
                    c = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
                else:
                    c = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
                plotter.add_mesh(surf, color=c, opacity=0.6, show_edges=False,
                                 lighting=False)
        else:
            # tube mode — colour-coded radius (fixed fraction of element length)
            avg = (abs(v_i) + abs(v_j)) * 0.5
            radius = max(axis_len * 0.02, 0.05)
            if radius < 1e-6:
                continue
            cyl = pv.Cylinder(center=p_mid, direction=axis, radius=radius, height=axis_len * 0.9)
            t = min(avg / max_abs_val, 1.0)
            if v_i >= 0:
                c = (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
            else:
                c = (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)
            plotter.add_mesh(cyl, color=c, opacity=0.5, show_edges=False,
                             lighting=False)

    # Legend (text, not scalar bar — colours are explicit RGB, not a colormap)
    kind = "Moment" if quantity.startswith("M") else "Force"
    plotter.add_text(f"{quantity}  (red = +ve, blue = −ve)",
                     position='lower_edge', font_size=14)

    plotter.add_axes()
    _set_isometric_view(plotter)

    if return_plotter:
        return plotter
    plotter.show()
    return None


# ═══════════════════════════════════════════════════════════════════
# Building views (matplotlib)
# ═══════════════════════════════════════════════════════════════════


def plot_building_views(md, mesh_model=None,
                        window_size=(1200, 900)):
    """Return a 2×2 matplotlib figure with plan, two elevations, isometric.

    Uses the two-stage path when *mesh_model* is provided, otherwise
    falls back to the legacy ``OpenSeesBuilder`` path.

    Parameters
    ----------
    md : SAPModelData
        Parsed model data.
    mesh_model : optional
        Preprocessed mesh model for two-stage path.
    window_size : tuple
        PyVista window dimensions (width, height) in pixels.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    try:
        import pyvista as pv
    except ImportError:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "Building views: PyVista not available",
                ha="center", va="center", transform=ax.transAxes, fontsize=12)
        ax.axis("off")
        return fig

    _ab_views = None
    try:
        if mesh_model is not None:
            from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
            _ab_views = AnalysisBuilder(mesh_model, {"verbose": False})
            _ab_views.build_domain()
        else:
            from fea_toolkit.opensees.preprocessor import preprocess_model
            from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
            _mm = preprocess_model(md, {"element_type": "elasticBeamColumn",
                                        "split_elements": True, "verbose": False})
            _ab_views = AnalysisBuilder(_mm, {"verbose": False})
            _ab_views.build_domain()
    except Exception:
        import warnings
        warnings.warn("Could not build model for building views.")
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "Building views: model build failed",
                ha="center", va="center", transform=ax.transAxes, fontsize=12)
        ax.axis("off")
        return fig

    views = ["Plan (XY)", "Elevation (XZ)", "Elevation (YZ)", "Isometric"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 13))
    axes_flat = axes.flatten()
    pv.OFF_SCREEN = True

    xs = [n.x for n in md.nodes.values()]
    ys = [n.y for n in md.nodes.values()]
    zs = [n.z for n in md.nodes.values()]
    x_c, y_c, z_c = (max(xs)+min(xs))/2, (max(ys)+min(ys))/2, (max(zs)+min(zs))/2
    w_aspect = window_size[0] / window_size[1]

    for ax, title in zip(axes_flat, views):
        try:
            pl = plot_mesh(
                _ab_views, notebook=True,
                show_nodes=False, show_frames=True, show_shells=True,
            )
        except Exception:
            pl = None
        if pl is not None:
            try:
                pl.camera.parallel_projection = True
                if title == "Plan (XY)":
                    pl.view_xy()
                    uv_map = lambda x, y, z: (x, y)
                elif title == "Elevation (XZ)":
                    pl.view_xz()
                    uv_map = lambda x, y, z: (x, z)
                elif title == "Elevation (YZ)":
                    pl.view_yz()
                    uv_map = lambda x, y, z: (y, z)
                else:
                    pl.view_isometric()
                    inv_sqrt2 = 1.0 / math.sqrt(2)
                    inv_sqrt6 = 1.0 / math.sqrt(6)
                    uv_map = lambda x, y, z: ((-x + y) * inv_sqrt2,
                                              (-x - y + 2 * z) * inv_sqrt6)
                u_vals, v_vals = [], []
                for x in (min(xs), max(xs)):
                    for y in (min(ys), max(ys)):
                        for z in (min(zs), max(zs)):
                            u, v = uv_map(x, y, z)
                            u_vals.append(u)
                            v_vals.append(v)
                u_span = max(u_vals) - min(u_vals)
                v_span = max(v_vals) - min(v_vals)
                pl.camera.parallel_scale = (
                    max(v_span / 2, u_span / (2 * w_aspect)) * 1.1
                )
                pl.camera.focal_point = (x_c, y_c, z_c)
                pl.render()
                img = pl.screenshot(return_img=True, window_size=window_size)
            except Exception:
                img = np.zeros((100, 100, 3))
            pl.close()
            ax.imshow(img)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.axis("off")

    fig.suptitle("Structural Model Views", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.canvas.draw()
    return fig

def plot_model_comparison(
    md,
    mesh_model=None,
    out_dir=None,
    off_screen=True,
    LOADS_ONLY=None,
):
    """Open an interactive PyVista viewer (or save screenshots) comparing
    the original model geometry with the split/meshed model.

    When *mesh_model* is provided, uses it directly (no re-build needed).
    Otherwise falls back to the two-stage build path.
    """
    import copy
    from pathlib import Path
    import pyvista as pv
    from fea_toolkit.model.sap_data import Restraint
    from fea_toolkit.model.selection import Selection
    from fea_toolkit.opensees.preprocessor import preprocess_model
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    if LOADS_ONLY is None:
        LOADS_ONLY = set()

    md_copy = copy.deepcopy(md)

    # Fix shell-only base nodes
    min_z = min(nd.z for nd in md_copy.nodes.values())
    base_ids = {nd.node_id for nd in md_copy.nodes.values() if nd.z == min_z}
    frame_conn: set = set()
    for e in md_copy.frame_elements.values():
        if e.node_i in base_ids: frame_conn.add(e.node_i)
        if e.node_j in base_ids: frame_conn.add(e.node_j)
    for nid in sorted(base_ids - frame_conn):
        if nid in md_copy.restraints:
            md_copy.restraints[nid] = Restraint([1, 1, 1, 1, 1, 1])

    for sn in LOADS_ONLY:
        if sn in md_copy.sections:
            s = md_copy.sections[sn]
            for attr in ('A', 'I33', 'I22', 'J'):
                setattr(s, attr, getattr(s, attr) * 0.01)

    if mesh_model is not None:
        _mm = mesh_model
        # Build domain from existing MeshModel
        builder = AnalysisBuilder(_mm, {
            "element_type": "elasticBeamColumn",
            "verbose": False,
        })
        builder.build_domain()
    else:
        sel = Selection(sections=list(LOADS_ONLY), element_types=["Area"])
        _mm = preprocess_model(md_copy, {
            "element_type": "elasticBeamColumn",
            "split_elements": True,
            "create_shells": True,
            "verbose": False,
        })
        builder = AnalysisBuilder(_mm, {
            "element_type": "elasticBeamColumn",
            "verbose": False,
        })
        builder.build_domain()

    import openseespy.opensees as ops
    ops.wipe()

    mm = getattr(builder, 'mesh_model', None)
    if off_screen:
        return _save_comparison_images(md, builder, LOADS_ONLY, out_dir,
                                       mesh_model=mm)
    _run_interactive_viewer(md, builder, LOADS_ONLY, mesh_model=mm)

def _save_comparison_images(md, builder, LOADS_ONLY, out_dir, mesh_model=None):
    """Save PNG screenshots of original and meshed views."""
    from pathlib import Path
    import pyvista as pv
    pv.set_plot_theme("document")
    pv.OFF_SCREEN = True
    out_path = Path(out_dir).resolve() if out_dir else Path.cwd().resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    orig_png = str(out_path / "model_original.png")
    mesh_png = str(out_path / "model_meshed.png")

    plotter = pv.Plotter(off_screen=True, window_size=[1400, 900])
    _add_original_geometry(plotter, md)
    plotter.view_isometric()
    plotter.show(auto_close=False)
    plotter.screenshot(orig_png)
    plotter.close()

    plotter = pv.Plotter(off_screen=True, window_size=[1400, 900])
    _add_meshed_geometry(plotter, md, builder, LOADS_ONLY,
                          mesh_model=getattr(builder, 'mesh_model', None))
    plotter.view_isometric()
    plotter.show(auto_close=False)
    plotter.screenshot(mesh_png)
    plotter.close()

    print(f"  Model comparison saved: {orig_png}, {mesh_png}")
    return {"orig_png": orig_png, "mesh_png": mesh_png}

def _add_original_geometry(plotter, md):
    """Add original (unsplit) geometry actors to a plotter."""
    import pyvista as pv
    orig_nodes = {nid: nd for nid, nd in md.nodes.items()}
    orig_frames = {eid: e for eid, e in md.frame_elements.items()}
    orig_areas = {aid: a for aid, a in md.area_elements.items()}

    FRAME_SHRINK = 0.9
    pts, lines = [], []
    off = 0
    for eid, elem in orig_frames.items():
        ni = orig_nodes.get(elem.node_i)
        nj = orig_nodes.get(elem.node_j)
        if ni is None or nj is None: continue
        a = np.array([ni.x, ni.y, ni.z])
        b = np.array([nj.x, nj.y, nj.z])
        mid = (a + b) * 0.5
        a = mid + FRAME_SHRINK * (a - mid)
        b = mid + FRAME_SHRINK * (b - mid)
        pts.append(a.tolist())
        pts.append(b.tolist())
        lines.append([2, off, off + 1])
        off += 2
    if pts:
        plotter.add_mesh(pv.PolyData(np.array(pts), lines=np.array(lines, dtype=int)),
                         color='#4c72b0', line_width=4, opacity=0.85)

    all_verts, all_faces, cell_cols = [], [], []
    off = 0
    for aid, area in orig_areas.items():
        if len(area.node_ids) < 3: continue
        verts = []
        for nid in area.node_ids:
            nd = orig_nodes.get(nid)
            if nd is None: break
            verts.append([nd.x, nd.y, nd.z])
        if len(verts) < 3: continue
        n = len(verts)
        arr = np.array(verts)
        centroid = arr.mean(axis=0)
        arr = centroid + 0.9 * (arr - centroid)
        all_verts.extend(arr.tolist())
        for i in range(1, n - 1):
            all_faces.append([3, off, off + i, off + i + 1])
            cell_cols.append((0.29, 0.44, 0.69))
        off += n
    if all_verts:
        m = pv.PolyData(np.array(all_verts), faces=np.array(all_faces, dtype=int).ravel())
        m.cell_data['rgb'] = np.array(cell_cols)
        plotter.add_mesh(m, scalars='rgb', rgb=True, opacity=0.35,
                         lighting=False, show_edges=False)

    node_pts = np.array([[nd.x, nd.y, nd.z] for nd in orig_nodes.values()])
    plotter.add_mesh(pv.PolyData(node_pts), color='#333333',
                     point_size=12, style='points',
                     render_points_as_spheres=True)

def _add_meshed_geometry(plotter, md, builder, LOADS_ONLY, mesh_model=None):
    """Add meshed (split frames + shells) geometry actors to a plotter."""
    import pyvista as pv
    mm = mesh_model or getattr(builder, 'mesh_model', None)
    if mm is not None:
        mesh_frames = mm.frame_elements
        mesh_assign = mm.frame_assignments
        mesh_coords = {nid: (nd.x, nd.y, nd.z) for nid, nd in mm.nodes.items()}
    else:
        mesh_frames = builder.split_elements or builder.model.frame_elements
        mesh_assign = builder.split_assignments or builder.model.frame_assignments
        mesh_coords = {nid: (nd.x, nd.y, nd.z) for nid, nd in builder.model.nodes.items()}

    FRAME_SHRINK = 0.9

    for color, is_split in [('#3a5588', False), ('#dd8452', True)]:
        pts, lines = [], []
        off = 0
        for eid, elem in mesh_frames.items():
            if getattr(elem, 'inactive', False): continue
            pid = getattr(elem, 'parent_id', None)
            if (pid is not None) != is_split: continue
            ci = mesh_coords.get(elem.node_i)
            cj = mesh_coords.get(elem.node_j)
            if ci is None or cj is None: continue
            a = np.array(ci)
            b = np.array(cj)
            mid = (a + b) * 0.5
            a = mid + FRAME_SHRINK * (a - mid)
            b = mid + FRAME_SHRINK * (b - mid)
            pts.append(a.tolist())
            pts.append(b.tolist())
            lines.append([2, off, off + 1])
            off += 2
        if pts:
            plotter.add_mesh(pv.PolyData(np.array(pts), lines=np.array(lines, dtype=int)),
                             color=color, line_width=4, opacity=0.85)

    all_verts, all_faces, cell_cols = [], [], []
    off = 0
    _area_elems = mm.area_elements if mm is not None else builder.model.area_elements
    _area_asgn = mm.area_assignments if mm is not None else builder.model.area_assignments
    for aid, area in _area_elems.items():
        if getattr(area, 'inactive', False): continue
        sec_name = _area_asgn.get(aid, '')
        if sec_name in LOADS_ONLY: continue
        if len(area.node_ids) < 3: continue
        verts = []
        for nid in area.node_ids:
            nd = (mm.nodes.get(nid) if mm is not None
                  else builder.model.nodes.get(nid))
            if nd is None: break
            verts.append([nd.x, nd.y, nd.z])
        if len(verts) < 3: continue
        n = len(verts)
        arr = np.array(verts)
        centroid = arr.mean(axis=0)
        arr = centroid + 0.9 * (arr - centroid)
        all_verts.extend(arr.tolist())
        c = (0.20, 0.31, 0.48)
        for i in range(1, n - 1):
            all_faces.append([3, off, off + i, off + i + 1])
            cell_cols.append(c)
        off += n
    if all_verts:
        m = pv.PolyData(np.array(all_verts), faces=np.array(all_faces, dtype=int).ravel())
        m.cell_data['rgb'] = np.array(cell_cols)
        plotter.add_mesh(m, scalars='rgb', rgb=True, opacity=0.45,
                         lighting=False, show_edges=False)

    _model_nodes = mm.nodes if mm is not None else builder.model.nodes
    node_pts = np.array([[nd.x, nd.y, nd.z] for nd in _model_nodes.values()])
    plotter.add_mesh(pv.PolyData(node_pts), color='#333333',
                     point_size=10, style='points',
                     render_points_as_spheres=True)
    orig_ids = set(md.nodes.keys())
    split_ids = set(_model_nodes.keys()) - orig_ids
    if split_ids:
        split_pts = np.array([list(mesh_coords[t]) for t in split_ids if t in mesh_coords])
        if len(split_pts):
            plotter.add_mesh(pv.PolyData(split_pts), color='#ff8c00',
                             point_size=20, style='points',
                             render_points_as_spheres=True)

def _run_interactive_viewer(md, builder, LOADS_ONLY, mesh_model=None):
    """Open an interactive PyVista window with original/meshed toggle."""
    import pyvista as pv
    pv.set_plot_theme("document")
    plotter = pv.Plotter(window_size=[1400, 900])
    _add_original_geometry(plotter, md)
    _add_meshed_geometry(plotter, md, builder, LOADS_ONLY,
                          mesh_model=mesh_model)
    plotter.set_background('white')
    plotter.view_isometric()
    plotter.show()

