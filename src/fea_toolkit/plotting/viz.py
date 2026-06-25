"""Visualisation helpers for fea_toolkit models and results.

Two backends are supported:

* **PyVista** — interactive 3D model view and deformed shape.
* **Matplotlib** — 2D force / moment diagrams along element height.

All functions gracefully fall back to a warning if the required package
is not installed.
"""

from typing import Dict, List, Optional, Any, Callable, TYPE_CHECKING
import math
import numpy as np

if TYPE_CHECKING:
    from ..model.selection import Selection


def _set_isometric_view(plotter) -> None:
    """Set an isometric view that works for any model (including 1D columns)."""
    bounds = plotter.bounds
    z_range = max(bounds[5] - bounds[4], 1.0)
    x_range = max(bounds[1] - bounds[0], 0.1)
    y_range = max(bounds[3] - bounds[2], 0.1)
    horiz = max(x_range, y_range)
    cx = (bounds[0] + bounds[1]) * 0.5
    cy = (bounds[2] + bounds[3]) * 0.5
    cz = (bounds[4] + bounds[5]) * 0.5
    dist = max(horiz, z_range) * 1.5
    plotter.camera.position = (cx + dist, cy + dist, cz + dist * 0.4)
    plotter.camera.focal_point = (cx, cy, cz)
    plotter.camera.view_up = (0.0, 0.0, 1.0)


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
    assignments = (builder.split_assignments if builder.split_elements
                   else builder.model.frame_assignments)

    lines = []
    labels_section = {}
    for eid, elem in elements.items():
        if getattr(elem, 'inactive', False):
            continue
        ni = builder.model.nodes.get(elem.node_i)
        nj = builder.model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            continue
        sec = (assignments or {}).get(eid, '?')
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        lines.append((p1, p2, sec))

    # Assign a colour per unique section
    all_secs = sorted({s for _, _, s in lines})
    if color_by_section and len(all_secs) > 1:
        cmap = pv.ColorCycle(values=[
            '#4c72b0', '#dd8452', '#55a868', '#c44e52', '#8172b3',
            '#937860', '#da8bc3', '#8c8c8c', '#ccb974', '#64b5cd',
        ])
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
    assignments = (builder.split_assignments if builder.split_elements
                   else builder.model.frame_assignments)

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

    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}

    # Build segment data: (p1, p2, di, dj)
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

    if not segments:
        print("No elements to display.")
        return None

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    # Undeformed
    if show_original:
        for p1, p2, _, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=2, opacity=0.3)

    # Deformed mesh (we'll update it if animating)
    def make_deformed(amp: float = 1.0):
        """Build a merged PolyData for the deformed shape at amplitude *amp*."""
        all_pts = []
        all_lines = []
        offset = 0
        for p1, p2, di, dj in segments:
            d1 = np.array(di) * scale * amp
            d2 = np.array(dj) * scale * amp
            a = p1 + d1
            b = p2 + d2
            n = max(2, int(np.linalg.norm(b - a) * 2))
            pts = np.linspace(a, b, n)
            all_pts.append(pts)
            for i in range(n - 1):
                all_lines.append([2, offset + i, offset + i + 1])
            offset += n
        if not all_pts:
            return pv.PolyData()
        verts = np.vstack(all_pts)
        cells = np.array(all_lines, dtype=int)
        return pv.PolyData(verts, lines=cells)

    deformed_mesh = make_deformed(1.0)
    actor = plotter.add_mesh(deformed_mesh, color='#c44e52', line_width=4)

    # Build title text with period if available
    period_str = ""
    if periods is not None and mode < len(periods):
        period_str = f"  T = {periods[mode]:.4f} s"

    if animate:
        import math as _math
        import time as _time

        mesh_ref = deformed_mesh

        def callback():
            t = _time.time()
            amp = _math.sin(t * 2.0)
            new_mesh = make_deformed(amp)
            plotter.update_coordinates(new_mesh.points, mesh=mesh_ref)
            plotter.render()

        plotter.add_callback(callback, 30)
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
    selection: Optional['Selection'] = None,
    notebook: bool = False,
    **kwargs,
) -> Optional[Any]:
    """Draw a moment diagram in 3D on the structure.

    Two display modes are available:

    * ``mode='flag'`` (default) — planar quadrilaterals extruded
      perpendicular to each member on the tension side.  The flag height
      is proportional to the moment magnitude.
    * ``mode='tube'`` — each element drawn as a coloured tube with a
      diverging red‑white‑blue colour map (blue = −ve, red = +ve).

    For the flag mode:

    * ``'My'`` — flags extend in the local **z** direction (bending about Y).
    * ``'Mz'`` — flags extend in the local **y** direction (bending about Z).

    Forces are extracted in the **global** system, then combined with local
    axes for the offset direction.  The J‑end moment is negated so that the
    flag draws consistently on the tension side along the whole element.

    Args:
        builder: Built ``OpenSeesBuilder``.
        elem_forces: Dict from ``builder.extract_static_element_forces()``.
        quantity: ``'My'`` or ``'Mz'``.
        mode: ``'flag'`` (extruded flags) or ``'tube'`` (colour‑coded tubes).
        moment_scale: Extrusion length per unit moment (flag mode only).
                      If ``None``, auto‑scaled so the largest flag is
                      10 % of the model height.
        show_original: If True, draw the centreline in grey.
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

    pv.set_plot_theme("document")

    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}

    if mode == 'flag':
        return _plot_moment_flags(builder, elements, elem_forces, quantity,
                                  moment_scale, show_original, notebook, **kwargs)
    elif mode == 'tube':
        return _plot_moment_tubes(builder, elements, elem_forces, quantity,
                                  show_original, notebook, **kwargs)
    else:
        print(f"Unknown mode '{mode}'.  Use 'flag' or 'tube'.")
        return None


def _plot_moment_flags(builder, elements, elem_forces, quantity,
                       moment_scale, show_original, notebook, **kwargs):
    """Flag‑based moment diagram (extruded on tension side)."""
    import pyvista as pv

    offset_axis = 'z' if quantity == 'My' else 'y'
    if quantity not in ('My', 'Mz'):
        print(f"Unsupported quantity '{quantity}'.  Use 'My' or 'Mz'.")
        return None

    model_height = 0.0
    max_moment = 0.0
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
        try:
            vx, vy, vz = builder._get_local_axes(elem)
        except Exception:
            continue
        offset_dir = vz if offset_axis == 'z' else vy
        Mi = elem_forces[tag].get(quantity, 0.0)
        # Negate J-end so the flag draws consistently on the tension side
        Mj = -elem_forces[tag].get(quantity + '_j', 0.0)
        max_moment = max(max_moment, abs(Mi), abs(Mj))
        flags.append((p_i, p_j, offset_dir, Mi, Mj))

    if not flags:
        print("No moment data to plot.")
        return None

    if moment_scale is None:
        moment_scale = (model_height * 0.1) / max(max_moment, 1.0)

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    if show_original:
        for p_i, p_j, _, _, _ in flags:
            n = max(2, int(np.linalg.norm(p_j - p_i) * 2))
            poly = pv.lines_from_points(np.linspace(p_i, p_j, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=1, opacity=0.4)

    for p_i, p_j, odir, Mi, Mj in flags:
        off_i = odir * Mi * moment_scale
        off_j = odir * Mj * moment_scale
        pts = np.array([p_i, p_j, p_j + off_j, p_i + off_i])
        face = np.array([4, 0, 1, 2, 3])
        surf = pv.PolyData(pts, faces=face)
        avg = (Mi + Mj) * 0.5
        if avg >= 0:
            intensity = min(avg / max(max_moment, 1.0), 1.0)
            colour = (0.3 + 0.7 * intensity, 0.0, 0.0)
        else:
            intensity = min(abs(avg) / max(max_moment, 1.0), 1.0)
            colour = (0.0, 0.0, 0.3 + 0.7 * intensity)
        plotter.add_mesh(surf, color=colour, opacity=0.85,
                         show_edges=False, smooth_shading=True)

    plotter.add_text(f"{quantity}  (red = +ve, blue = −ve)",
                     position='lower_edge', font_size=10)
    _set_isometric_view(plotter)
    if notebook:
        return plotter
    plotter.show()
    return None


def _plot_moment_tubes(builder, elements, elem_forces, quantity,
                       show_original, notebook, **kwargs):
    """Tube‑based moment diagram (colour‑coded along element)."""
    import pyvista as pv

    moments = []
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
        val = elem_forces[tag].get(quantity, 0.0)
        moments.append(val)
        p1 = np.array([ni.x, ni.y, ni.z])
        p2 = np.array([nj.x, nj.y, nj.z])
        segments.append((p1, p2, val))

    if not segments:
        print("No moment data to plot.")
        return None

    vlim = max(abs(min(moments)), abs(max(moments)), 1.0)

    plotter = pv.Plotter(notebook=notebook, **kwargs)

    if show_original:
        for p1, p2, _ in segments:
            n = max(2, int(np.linalg.norm(p2 - p1) * 2))
            poly = pv.lines_from_points(np.linspace(p1, p2, n))
            plotter.add_mesh(poly, color='lightgrey', line_width=1, opacity=0.3)

    for p1, p2, val in segments:
        n = max(8, int(np.linalg.norm(p2 - p1) * 4))
        poly = pv.lines_from_points(np.linspace(p1, p2, n))
        norm_val = val / vlim
        if norm_val <= 0:
            intensity = abs(norm_val)
            colour = (0.0, 0.0, 0.4 + 0.6 * (1.0 - intensity))
        else:
            intensity = norm_val
            colour = (0.4 + 0.6 * intensity, 0.0, 0.0)
        radius = 0.02 * max(np.linalg.norm(p2 - p1), 0.1)
        tube = poly.tube(radius=radius)
        plotter.add_mesh(tube, color=colour, smooth_shading=True)

    plotter.add_text(f"{quantity}  (red = +ve, blue = −ve)",
                     position='lower_edge', font_size=10)
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
    **kwargs,
) -> Optional[Any]:
    """Plot a static element force/moment quantity vs elevation.

    The forces are in the **global** coordinate system.  For a vertical
    structure like a chimney:

    * ``'Fz'`` — axial force (compression), use this for vertical loads.
    * ``'My'`` — bending moment about Y (from lateral loads in X).
    * ``'Fx'`` / ``'Fy'`` — shear in X / Y directions.

    Args:
        builder: Built ``OpenSeesBuilder``.
        elem_forces: Dict from ``builder.extract_static_element_forces()``.
        quantity: Global force key — ``'Fx'``, ``'Fy'``, ``'Fz'``,
                  ``'Mx'``, ``'My'``, ``'Mz'``.
        title: Optional title.  Auto‑generated if omitted.
        selection: Optional :class:`~fea_toolkit.model.selection.Selection`
            to restrict which elements are shown.  ``None`` means all.
        figsize: Matplotlib figure size ``(width, height)``.
        **kwargs: Passed to ``matplotlib.pyplot.plot()``.

    Returns:
        The ``matplotlib.figure.Figure``.
    """
    # Build a list matching the format expected by plot_force_diagram
    elements = (builder.split_elements if builder.split_elements
                else builder.model.frame_elements)
    if selection is not None:
        sel_ids = set(selection.get_frame_ids(builder.model))
        elements = {eid: elem for eid, elem in elements.items()
                    if eid in sel_ids}
    entries = []
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
        z_mid = (ni.z + nj.z) * 0.5
        entries.append({
            'z_mid': z_mid,
            quantity: elem_forces[tag].get(quantity, 0.0),
        })
    if not entries:
        print("No element force data to plot.")
        return None
    return plot_force_diagram(
        entries, quantity, title=title, figsize=figsize, **kwargs,
    )


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

    if len(S_d) < 2 or len(S_a) < 2:
        print("Insufficient ADRS data to plot.")
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
