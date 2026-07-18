"""Visualisation helpers for fea_toolkit models and results.

Two backends are supported:

* **PyVista** — interactive 3D model view and deformed shape.
* **Matplotlib** — 2D force / moment diagrams along element height.

All functions gracefully fall back to a warning if the required package
is not installed.
"""

from typing import Dict, List, Optional, Any, TYPE_CHECKING
import math
import numpy as np

from ..model.geometry import get_SAP_vecxz
from ..utils import compute_flag_parts

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
    all_tris: list,
    sec_idxs: list,
    scale: float,
    amp: float,
) -> "tuple[pv.PolyData, Optional[pv.PolyData]]":
    """Build a single merged ``PolyData`` from frame-segment and shell-quad
    geometry, displaced by ``scale * amp`` along each element's eigenvector.

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
    all_tris : list of (n, i, j, k) tuples
        Triangle indices that subdivide each quad (shared winding).
    sec_idxs : list of int
        Per-quad section index (one per entry in *all_quads*).
    scale : float
        Base scale factor from the eigenvector normalisation.
    amp : float
        Amplitude multiplier for the current frame.

    Returns
    -------
    tuple[pv.PolyData, pv.PolyData | None]
        ``(frame_mesh, shell_mesh)`` — frame mesh contains ``lines`` only;
        shell mesh contains triangulated ``faces`` with a ``section_idx``
        cell scalar for per-section colouring.  *shell_mesh* is ``None``
        when there are no shell elements.
    """
    all_pts: list = []
    all_lines: list = []
    all_faces: list = []
    all_cell_data: list = []
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

    # Shell quads — triangulated, with per-face section index
    for quad, (t1, t2), sidx in zip(all_quads, all_tris, sec_idxs):
        p1, p2, p3, p4, d1, d2, d3, d4 = quad
        a1 = p1 + d1 * scale * amp
        a2 = p2 + d2 * scale * amp
        a3 = p3 + d3 * scale * amp
        a4 = p4 + d4 * scale * amp
        all_pts.extend([a1, a2, a3, a4])
        all_faces.append([3, offset + t1[1], offset + t1[2], offset + t1[3]])
        all_faces.append([3, offset + t2[1], offset + t2[2], offset + t2[3]])
        all_cell_data.extend([sidx, sidx])
        offset += 4

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
        faces = np.array(all_faces, dtype=int)
        sm = pv.PolyData(verts[n_frame_pts:])
        sm.faces = faces
        if len(all_cell_data) > 0:
            sm.cell_data['section_idx'] = np.array(all_cell_data, dtype=int)
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
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Display the model in an interactive 3D view using PyVista.

    Args:
        builder: An ``OpenSeesBuilder`` instance that has been built.
        show_nodes: If True, draw node markers.
        show_labels: If True, label nodes with their tags.
        color_by_section: If True, colour elements by section name.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        notebook: If True, return a plotter suitable for Jupyter embedding.
        **kwargs: Passed to ``pyvista.Plotter()``.

    Returns:
        ``pyvista.Plotter`` if *notebook* is True (for inline display),
        otherwise ``None`` (interactive window opens).

    Requires:
        ``pyvista`` — install via ``pip install pyvista``.
    """
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

    # Add nodes
    if show_nodes:
        node_pts = np.array([
            [n.x, n.y, n.z] for n in builder.model.nodes.values()
        ])
        if len(node_pts):
            cloud = pv.PolyData(node_pts)
            plotter.add_mesh(cloud, color='black', point_size=8,
                             render_points_as_spheres=True)

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

    Args:
        builder: Built ``OpenSeesBuilder``.
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

    Args:
        builder: Built ``OpenSeesBuilder``.
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
    **kwargs,
) -> Optional[Any]:
    """Display (and optionally animate) a mode shape in 3D using PyVista.

    For each mode, the eigenvector displacements from
    :meth:`OpenSeesBuilder.extract_mode_shapes` are applied as a deformed
    shape, scaled by *scale*.  When *animate* is ``True`` the amplitude
    oscillates sinusoidally, giving a visual feel for the vibration pattern.

    Args:
        builder: Built ``OpenSeesBuilder``.
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
        **kwargs: Passed to ``pyvista.Plotter()``.

    Requires:
        ``pyvista``.
    """
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
    for aid, area in builder.model.area_elements.items():
        if getattr(area, 'inactive', False):
            continue
        if len(area.node_ids) < 3:
            continue
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

    # ── Helper: triangulate a quad into two triangles ──
    def _tri_quad(v0, v1, v2, v3):
        """Return two triangle faces [v0,v1,v2] and [v0,v2,v3]."""
        return [3, v0, v1, v2], [3, v0, v2, v3]

    # Undeformed (grey)
    if show_original:
        for p1, p2, _, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=2, opacity=0.3)
        # Undeformed shells — triangulate each quad for robustness
        for sec_name, quads in shell_groups.items():
            for quad in quads:
                p1, p2, p3, p4 = quad[:4]
                t1, t2 = _tri_quad(0, 1, 2, 3)
                face = pv.PolyData([p1, p2, p3, p4], faces=t1 + t2)
                plotter.add_mesh(face, color='lightgrey', opacity=0.12,
                                 show_edges=True, edge_color='grey', line_width=0.5)

    # ── Helper: build deformed mesh (used by both animate and static paths) ──
    # Pre-compute the UNDEFORMED length of each frame segment so the number
    # of interpolation points stays constant across animation amplitudes.
    _seg_npoints = []
    for p1, p2, _, _ in segments:
        n = max(2, int(np.linalg.norm(p2 - p1) * 2))
        _seg_npoints.append(n)
    # Pre-compute triangle faces for each shell quad, grouped by section.
    # _group_tris[sec_name] = list of (t1, t2) pairs for that group's quads
    _group_tris: Dict[str, List[tuple]] = {}
    for sec_name, quads in shell_groups.items():
        tris = []
        for quad in quads:
            tris.append(_tri_quad(0, 1, 2, 3))
        _group_tris[sec_name] = tris
    # Build a flat list for the animation path, with section index per quad
    _all_quads_flat: List[list] = []
    _all_tris_flat: List[tuple] = []
    _all_sec_idxs: List[int] = []        # section index for each quad (used for per-face coloring)
    _sec_names_list = _sec_names_sorted  # index → name
    _sec_name_to_idx = {name: i for i, name in enumerate(_sec_names_list)}
    for sec_name, quads in shell_groups.items():
        sidx = _sec_name_to_idx[sec_name]
        for idx, quad in enumerate(quads):
            _all_quads_flat.append(quad)
            _all_tris_flat.append(_group_tris[sec_name][idx])
            _all_sec_idxs.append(sidx)

    def make_deformed(amp: float = 1.0):
        """Build merged PolyData for the deformed shape at amplitude *amp*.
        Point count is invariant w.r.t. *amp* — safe for animation updates.

        Shell triangles carry a ``section_idx`` cell scalar so they can be
        coloured by section when rendered.
        """
        return _build_deformed_mesh(
            segments, _seg_npoints,
            _all_quads_flat, _all_tris_flat, _all_sec_idxs,
            scale, amp,
        )

    if animate:
        # Animated mode: separate meshes for frames (lines) and shells (faces).
        # We keep references so we can update .points per frame.
        frame_mesh, shell_mesh = make_deformed(1.0)

        # Render frames as coloured lines
        if frame_mesh is not None and frame_mesh.n_points:
            plotter.add_mesh(frame_mesh, color='#c44e52',
                             line_width=4, opacity=0.85)

        # Render shells with per-section colours via cell scalars
        n_sections = len(_sec_names_list)
        if shell_mesh is not None and shell_mesh.n_points:
            if n_sections > 0:
                plotter.add_mesh(
                    shell_mesh,
                    scalars='section_idx',
                    cmap=_SECTION_COLORS[:n_sections],
                    show_edges=True, edge_color='#a03030',
                    opacity=0.85,
                    clim=[-0.5, n_sections - 0.5],
                )
            else:
                plotter.add_mesh(shell_mesh, color='#c44e52',
                                 show_edges=True, edge_color='#a03030',
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
            tris = _group_tris[sec_name]
            all_pts, all_faces = [], []
            offset = 0
            for quad, (t1, t2) in zip(quads, tris):
                p1, p2, p3, p4, d1, d2, d3, d4 = quad
                a1 = p1 + d1 * scale
                a2 = p2 + d2 * scale
                a3 = p3 + d3 * scale
                a4 = p4 + d4 * scale
                all_pts.extend([a1, a2, a3, a4])
                all_faces.append([3, offset + t1[1], offset + t1[2], offset + t1[3]])
                all_faces.append([3, offset + t2[1], offset + t2[2], offset + t2[3]])
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
            label_size = max(8, 14 - len(shell_groups))
            plotter.add_legend(
                legend_entries, border=True, size=[0.2, 0.12],
                loc='lower_right', face='white',
                label_size=label_size,
            )

    # Build title text with period if available
    period_str = ""
    if periods is not None and mode < len(periods):
        period_str = f"  T = {periods[mode]:.4f} s"

    if animate:
        import math as _math

        def callback(step):
            amp = _math.sin(2.0 * _math.pi * step / 60.0)
            nfm, nsm = make_deformed(amp)
            if frame_mesh is not None and nfm is not None:
                frame_mesh.points = nfm.points
            if shell_mesh is not None and nsm is not None:
                shell_mesh.points = nsm.points
            plotter.render()

        plotter.add_timer_event(600, 30, callback)
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
        builder: Built ``OpenSeesBuilder``.
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
    """3D shear force diagram — convenience wrapper around
    :func:`plot_static_moment_3d` with ``quantity`` set to a force.

    Parameters
    ----------
    quantity : str
        ``'Fz'`` (default), ``'Fy'``, or ``'Fx'``.
    **kwargs
        Passed through to :func:`plot_static_moment_3d`.
    """
    return plot_static_moment_3d(builder, elem_forces, quantity=quantity, **kwargs)


def plot_static_axial_3d(builder, elem_forces, **kwargs):
    """3D axial force diagram — convenience wrapper around
    :func:`plot_static_moment_3d` with ``quantity='Fx'``.

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

    When ``use_local=True`` (default), forces are transformed from global
    to **local** coordinates using the element's local axes
    (:meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder._get_local_axes`).
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
        builder: Built ``OpenSeesBuilder``.
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
            :meth:`OpenSeesBuilder.run_pushover_analysis`.
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
            keys (lists or arrays).  The :meth:`OpenSeesBuilder.run_pushover_analysis`
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
            :meth:`OpenSeesBuilder.pushover_to_adrs` (dict with keys
            ``'S_a'`` and ``'S_d'``).
        spectrum_periods: Periods (s) defining the elastic demand spectrum.
        spectrum_accels: Spectral accelerations (m/s²) corresponding to
            *spectrum_periods*.
        performance_point: Optional result dict from
            :meth:`OpenSeesBuilder.compute_performance_point`.  If provided
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
# OpenSeesBuilder.export_results_to_npz) and generate plots without
# needing the original OpenSeesBuilder or model objects.
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
    from ..io.npz_reader import read_results_npz, _get_static_cases
    d = read_results_npz(npz_path)

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
) -> "Figure":
    """2D diagram of a local force quantity vs elevation from an NPZ file.

    This is a **standalone** function — it does **not** require any
    ``OpenSeesBuilder`` or model objects.  Just pass the path to a
    ``.npz`` file created by :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.export_results_to_npz`.

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

    Standalone function — no ``OpenSeesBuilder`` or model objects needed.

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
        vecxz = get_SAP_vecxz(axis, 0.0)
        vec_z = vecxz / np.linalg.norm(vecxz)
        vec_y = np.cross(vec_z, axis)
        if np.linalg.norm(vec_y) > 1e-12:
            vec_y = vec_y / np.linalg.norm(vec_y)
        else:
            vec_y = np.array([0.0, 1.0, 0.0])
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


