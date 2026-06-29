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

from ..model.geometry import get_SAP_vecxz
from ..utils import compute_flag_parts

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
        tip = pos + vec * scale
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
    """Load an NPZ results file and build element‑centric arrays.

    Parameters
    ----------
    npz_path : str
        Path to the ``.npz`` results file.
    combo : str or None
        Load‑combination key (prefix).  ``None`` = primary results.

    Returns a dict with:
        - elem_data: list of dicts (one per sub‑element) with keys:
            sap_id, (x|y|z)_i, (x|y|z)_j, mid_z,
            fx_i, fy_i, fz_i, mx_i, my_i, mz_i,
            fx_j, fy_j, fz_j, mx_j, my_j, mz_j,
            and ``_local`` variants
        - metadata: parsed metadata dict (or ``{}``)
        - force_unit, length_unit: unit strings
        - raw_data: the loaded npz dict
    """
    data = np.load(npz_path, allow_pickle=True)
    prefix = f"{combo}_" if combo else ""

    # Metadata
    metadata_raw = data.get("metadata_json")
    metadata = {}
    if metadata_raw is not None:
        try:
            metadata = json.loads(str(metadata_raw.item()))
        except Exception:
            pass

    def _s(key) -> str:
        arr = data.get(key)
        if arr is not None:
            return str(arr.item())
        return "?"

    force_unit = _s("force_unit")
    length_unit = _s("length_unit")

    # Build look‑up: node_tag → (x, y, z)
    n_tags = data.get("node_tags")
    n_x = data.get("node_x")
    n_y = data.get("node_y")
    n_z = data.get("node_z")
    node_coords: Dict[int, tuple] = {}
    if n_tags is not None and n_x is not None:
        for i in range(len(n_tags)):
            node_coords[int(n_tags[i])] = (
                float(n_x[i]),
                float(n_y[i]),
                float(n_z[i]),
            )

    # Build element list
    has_local = metadata.get("has_local_forces", metadata.get("has_local", False)) or f"{prefix}sub_fx_i_local" in data
    elem_data: List[dict] = []
    for i in range(len(data["sub_elem_tags"])):
        n_i_tag = int(data["sub_node_i_tag"][i])
        n_j_tag = int(data["sub_node_j_tag"][i])
        c_i = node_coords.get(n_i_tag, (0, 0, 0))
        c_j = node_coords.get(n_j_tag, (0, 0, 0))
        x_i, y_i, z_i = c_i
        x_j, y_j, z_j = c_j
        mid_z = (z_i + z_j) / 2.0

        def _g(k: str) -> float:
            pk = f"{prefix}{k}"
            arr = data.get(pk)
            return float(arr[i]) if arr is not None else np.nan

        entry: dict = {
            "sap_id": str(data["sub_sap_ids"][i]),
            "x_i": x_i, "y_i": y_i, "z_i": z_i,
            "x_j": x_j, "y_j": y_j, "z_j": z_j,
            "mid_z": mid_z,
            "fx_i": _g("sub_fx_i"), "fy_i": _g("sub_fy_i"), "fz_i": _g("sub_fz_i"),
            "mx_i": _g("sub_mx_i"), "my_i": _g("sub_my_i"), "mz_i": _g("sub_mz_i"),
            "fx_j": _g("sub_fx_j"), "fy_j": _g("sub_fy_j"), "fz_j": _g("sub_fz_j"),
            "mx_j": _g("sub_mx_j"), "my_j": _g("sub_my_j"), "mz_j": _g("sub_mz_j"),
        }
        if has_local:
            for q in ("fx", "fy", "fz", "mx", "my", "mz"):
                k = f"sub_{q}_i_local"
                entry[f"{q}_i_local"] = _g(k) if f"{prefix}{k}" in data else _g(f"sub_{q}_i")
                k = f"sub_{q}_j_local"
                entry[f"{q}_j_local"] = _g(k) if f"{prefix}{k}" in data else _g(f"sub_{q}_j")
        elem_data.append(entry)

    return {
        "elem_data": elem_data,
        "metadata": metadata,
        "force_unit": force_unit,
        "length_unit": length_unit,
        "raw_data": data,
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


