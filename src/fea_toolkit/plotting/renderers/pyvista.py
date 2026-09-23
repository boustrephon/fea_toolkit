"""PyVista render backend for ModelViewer."""

import contextlib
from typing import Any, Optional

import numpy as np

from .base import (
    AnnotationDef,
    FrameGeom,
    HighlightDef,
    NodeGeom,
    RenderBackend,
    ShellGeom,
)


def _unit_vec(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0, 0.0])


def _flag_direction(
    quantity: str,
    start: np.ndarray,
    end: np.ndarray,
    angle: float = 0.0,
) -> Optional[np.ndarray]:
    """Return the flag-diagram extrusion direction (unit normal) for a frame.

    Mirrors :func:`fea_toolkit.plotting.viz_forces._compute_flag_direction`
    so ``ModelViewer.overlay_forces`` produces the same diagram as the
    :func:`plot_force_diagram` path.  The mapping operates on the local
    force/moment quantity (``Fx``/``Fy``/``Fz``/``Mx``/``My``/``Mz``).

    Args:
        quantity: Force/moment quantity name.
        start: I-end global coordinates, shape ``(3,)``.
        end: J-end global coordinates, shape ``(3,)``.
        angle: SAP2000 section rotation (degrees) about the local x-axis.
            A nonzero value rotates the local y/z axes used to place the
            flag plane.

    Returns:
        Unit normal vector, or ``None`` for a zero-length member.
    """
    from ...model.geometry import get_local_axes

    axis = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
    norm = np.linalg.norm(axis)
    if norm < 1e-12:
        return None
    try:
        _, vy, vz = get_local_axes(axis / norm, angle)
    except Exception:
        # Mirror ``viz_forces._compute_flag_direction``: fall back to the
        # global-frame vy/vz and still apply the quantity mapping below.
        vy = np.array([0.0, 1.0, 0.0])
        vz = np.array([0.0, 0.0, 1.0])

    direction = {
        "Fx": vz,
        "Fy": vy,
        "Fz": vz,
        "Mx": vy,
        "My": -vz,
        "Mz": vy,
    }.get(quantity, vz)
    return np.array(direction, dtype=float).copy()


def _flag_rgb(col_val: float, max_abs: float) -> tuple[float, float, float]:
    """Map a signed flag value to a diverging RGB colour in ``0..1``.

    Identical colour mapping to
    :func:`fea_toolkit.plotting.viz_forces._add_coloured_poly`: positive →
    warm (red), negative → cool (blue), saturated by ``|col_val| / max_abs``.
    """
    t = min(abs(col_val) / max(max_abs, 1e-12), 1.0)
    if col_val >= 0:
        return (0.3 + 0.7 * t, 0.3 - 0.2 * t, 0.3 - 0.3 * t)
    return (0.3 - 0.3 * t, 0.3 - 0.2 * t, 0.3 + 0.7 * t)


class PyVistaRenderer(RenderBackend):
    """Render backend using PyVista.

    Requires ``pyvista`` — install via ``pip install pyvista``.
    """

    def __init__(
        self,
        off_screen: bool = False,
        notebook: bool = False,
        plotter: Optional[Any] = None,
    ):
        self._plotter = None
        self._off_screen = off_screen
        self._notebook = notebook
        # Keep track of all actors so ``clear()`` can remove them
        self._actors: list = []
        # ... and of the actors of each *category* (frames, shells, nodes,
        # highlights, ...) so one overlay can be removed or hidden without
        # rebuilding the whole scene -- repeated selection highlighting and
        # the GUI's display toggles both need this.
        self._categories: dict = {}
        if plotter is not None:
            # An injected plotter -- e.g. a ``pyvistaqt.QtInteractor`` that
            # embeds the viewport in a Qt app.  Use it directly and
            # decorate it the same way a lazily-created plotter would be.
            self._plotter = plotter
            self._decorate_plotter()

    # ── Plotter initialisation ───────────────────────────────────────

    @property
    def plotter(self):
        if self._plotter is None:
            import pyvista as pv

            # Use pyvista's global theme
            theme = pv.global_theme
            theme.font.label_size = 12
            kwargs = {"off_screen": self._off_screen, "notebook": self._notebook}
            # Only pass window_size in off_screen mode
            if self._off_screen:
                kwargs["window_size"] = [1920, 1080]
            self._plotter = pv.Plotter(**kwargs)
            self._decorate_plotter()
        return self._plotter

    def _decorate_plotter(self) -> None:
        """Add the standard axes triad and ground grid to the plotter."""
        self._plotter.show_axes()
        # Show grid on the ground plane
        with contextlib.suppress(Exception):
            self._plotter.show_grid(
                grid="back",
                location="outer",
                ticks="both",
            )

    # ── Actor bookkeeping ────────────────────────────────────────────

    def _add_actor(self, actor: Any, category: str) -> None:
        """Track *actor* globally and under *category*.

        Args:
            actor: The actor returned by ``plotter.add_mesh``.
            category: Logical group -- ``"frames"``, ``"shells"``, ``"nodes"``,
                ``"highlights"``, ``"annotations"``, ``"deformed"`` or
                ``"force_flags"``.
        """
        self._actors.append(actor)
        self._categories.setdefault(category, []).append(actor)

    def clear_category(self, category: str) -> None:
        """Remove every actor of *category* from the scene.

        Used to redraw one overlay -- e.g. the selection highlight -- without
        rebuilding the rest of the scene.

        Args:
            category: Category name, as passed to :meth:`_add_actor`.
        """
        actors = self._categories.pop(category, [])
        if not actors:
            return
        p = self._plotter
        if p is not None:
            for actor in actors:
                with contextlib.suppress(Exception):
                    p.remove_actor(actor)
        for actor in actors:
            with contextlib.suppress(ValueError):
                self._actors.remove(actor)

    def set_category_visible(self, category: str, visible: bool) -> None:
        """Show or hide every actor of *category*.

        Args:
            category: Category name, as passed to :meth:`_add_actor`.
            visible: New visibility.
        """
        for actor in self._categories.get(category, []):
            with contextlib.suppress(Exception):
                actor.SetVisibility(bool(visible))
        if self._plotter is not None:
            with contextlib.suppress(Exception):
                self._plotter.render()

    def clear_highlights(self) -> None:
        """Remove the actors drawn by :meth:`render_highlights`."""
        self.clear_category("highlights")

    # ── Frame elements ───────────────────────────────────────────────

    def render_frames(
        self,
        frames: list[FrameGeom],
        colors: dict[str, tuple[float, float, float]],
        opacity: float = 1.0,
    ) -> None:
        if not frames:
            return
        p = self.plotter

        # Build a single pyvista PolyData with all frame lines
        n = len(frames)
        points = np.zeros((n * 2, 3))
        lines = np.zeros((n, 3), dtype=int)  # VTK: [n_pts, i, j]
        per_line_color = np.zeros((n, 3))
        for idx, f in enumerate(frames):
            points[idx * 2] = f.start
            points[idx * 2 + 1] = f.end
            lines[idx] = [2, idx * 2, idx * 2 + 1]
            per_line_color[idx] = colors.get(f.section, (0.5, 0.5, 0.5))

        import pyvista as pv

        mesh = pv.PolyData(points, lines=lines)
        mesh["rgb"] = per_line_color
        actor = p.add_mesh(
            mesh,
            scalars="rgb",
            rgb=True,
            opacity=opacity,
            line_width=2,
            show_scalar_bar=False,
        )
        self._add_actor(actor, "frames")

    # ── Shell elements ───────────────────────────────────────────────

    def render_shells(
        self,
        shells: list[ShellGeom],
        colors: dict[str, tuple[float, float, float]],
        opacity: float = 1.0,
    ) -> None:
        if not shells:
            return
        p = self.plotter
        import pyvista as pv

        all_verts: list[np.ndarray] = []
        all_faces: list[np.ndarray] = []
        shell_colors: list[tuple] = []
        offset = 0

        for s in shells:
            nv = len(s.vertices)
            # Fan triangulation for arbitrary polygon (tris, quads, 5+)
            for i in range(1, nv - 1):
                all_faces.append(np.array([3, offset, offset + i, offset + i + 1]))
            all_verts.append(s.vertices)
            shell_colors.append(colors.get(s.section, (0.7, 0.7, 0.7)))
            offset += nv

        if not all_verts:
            return

        verts = np.vstack(all_verts)
        faces = np.hstack(all_faces) if len(all_faces) > 0 else np.array([], dtype=int)
        mesh = pv.PolyData(verts, faces=faces)
        # Per-face colours — build cell-by-cell colour array matching
        # the actual number of triangles produced per shell
        cell_colors = []
        for s in shells:
            c = colors.get(s.section, (0.7, 0.7, 0.7))
            n_tris = max(0, len(s.vertices) - 2)
            for _ in range(n_tris):
                cell_colors.append(c)
        if cell_colors:
            mesh.cell_data["rgb"] = np.array(cell_colors)
            actor = p.add_mesh(
                mesh,
                scalars="rgb",
                rgb=True,
                opacity=opacity,
                show_edges=True,
                edge_color="grey",
                lighting=True,
                show_scalar_bar=False,
            )
            self._add_actor(actor, "shells")

    # ── Nodes ────────────────────────────────────────────────────────

    def render_nodes(
        self,
        nodes: list[NodeGeom],
        color: tuple[float, float, float] = (0.3, 0.3, 0.3),
        radius: float = 0.02,
    ) -> None:
        if not nodes:
            return
        p = self.plotter
        import pyvista as pv

        pts = np.array([n.position for n in nodes])
        cloud = pv.PolyData(pts)
        actor = p.add_mesh(
            cloud,
            color=color,
            point_size=radius * 20,
            style="points",
            render_points_as_spheres=True,
            show_scalar_bar=False,
        )
        self._add_actor(actor, "nodes")

    # ── Highlights ───────────────────────────────────────────────────

    def render_highlights(
        self,
        highlights: list[HighlightDef],
    ) -> None:
        if not highlights:
            return
        p = self.plotter
        import pyvista as pv

        for h in highlights:
            # ── Highlighted frames ──
            if h.frames:
                n = len(h.frames)
                pts = np.zeros((n * 2, 3))
                lines = np.zeros((n, 3), dtype=int)
                for idx, f in enumerate(h.frames):
                    pts[idx * 2] = f.start
                    pts[idx * 2 + 1] = f.end
                    lines[idx] = [2, idx * 2, idx * 2 + 1]
                r = h.radius or 0.03
                mesh = pv.PolyData(pts, lines=lines)
                tube = mesh.tube(radius=r)
                actor = p.add_mesh(
                    tube,
                    color=h.color,
                    opacity=1.0,  # a selection cue must read over any section colour
                    show_scalar_bar=False,
                )
                self._add_actor(actor, "highlights")

            # ── Highlighted nodes ──
            if h.nodes:
                pts = np.array([n.position for n in h.nodes])
                cloud = pv.PolyData(pts)
                actor = p.add_mesh(
                    cloud,
                    color=h.color,
                    point_size=15,
                    style="points",
                    render_points_as_spheres=True,
                    show_scalar_bar=False,
                )
                self._add_actor(actor, "highlights")

            # ── Highlighted shells ──
            if h.shells:
                all_verts = []
                all_faces = []
                offset = 0
                for s in h.shells:
                    nv = len(s.vertices)
                    if nv < 3:
                        offset += nv
                        continue
                    # Fan triangulation for arbitrary polygon
                    for i in range(1, nv - 1):
                        all_faces.append(np.array([3, offset, offset + i, offset + i + 1]))
                    all_verts.append(s.vertices)
                    offset += nv
                if all_verts:
                    verts = np.vstack(all_verts)
                    faces = np.hstack(all_faces) if all_faces else np.array([], dtype=int)
                    mesh = pv.PolyData(verts, faces=faces)
                    n_tris = sum(
                        max(0, len(s.vertices) - 2) for s in h.shells if len(s.vertices) >= 3
                    )
                    cell_colors = [h.color] * n_tris
                    mesh.cell_data["rgb"] = np.array(cell_colors)
                    actor = p.add_mesh(
                        mesh,
                        scalars="rgb",
                        rgb=True,
                        opacity=0.7,
                        show_edges=True,
                        edge_color="grey",
                        lighting=True,
                        show_scalar_bar=False,
                    )
                    self._add_actor(actor, "highlights")

            # ── Label ──
            if h.label:
                centroid = np.zeros(3)
                count = 0
                if h.frames:
                    all_pts = np.vstack([f.start for f in h.frames] + [f.end for f in h.frames])
                    centroid += all_pts.sum(axis=0)
                    count += len(all_pts)
                if h.nodes:
                    for n in h.nodes:
                        centroid += n.position
                        count += 1
                if h.shells:
                    for s in h.shells:
                        for v in s.vertices:
                            centroid += v
                            count += 1
                if count > 0:
                    centroid /= count
                else:
                    continue
                lbl = p.add_point_labels(
                    [centroid],
                    [h.label],
                    font_size=16,
                    text_color=h.color,
                    point_color=h.color,
                    point_size=8,
                    shape="rounded_rect",
                )
                self._add_actor(lbl, "highlights")

    # ── Annotations ──────────────────────────────────────────────────

    def render_annotations(
        self,
        annotations: list[AnnotationDef],
    ) -> None:
        if not annotations:
            return
        p = self.plotter
        for a in annotations:
            actor = p.add_point_labels(
                [a.position],
                [a.text],
                font_size=a.font_size,
                text_color=a.color,
                point_color=a.color,
                point_size=4,
                shape="rounded_rect",
            )
            self._add_actor(actor, "annotations")

    # ── Deformed shape ───────────────────────────────────────────────

    def render_deformed(
        self,
        frames: list[FrameGeom],
        displacements: dict[str, np.ndarray],
        scale: float = 1.0,
        color: tuple[float, float, float] = (0.3, 0.6, 1.0),
    ) -> None:
        if not frames:
            return
        p = self.plotter
        import pyvista as pv

        n = len(frames)
        pts = np.zeros((n * 2, 3))
        lines = np.zeros((n, 3), dtype=int)
        for idx, f in enumerate(frames):
            d_i = displacements.get(f.node_i, np.zeros(3))
            d_j = displacements.get(f.node_j, np.zeros(3))
            pts[idx * 2] = f.start + d_i * scale
            pts[idx * 2 + 1] = f.end + d_j * scale
            lines[idx] = [2, idx * 2, idx * 2 + 1]

        mesh = pv.PolyData(pts, lines=lines)
        actor = p.add_mesh(
            mesh,
            color=color,
            opacity=0.7,
            line_width=2,
            show_scalar_bar=False,
        )
        self._add_actor(actor, "deformed")

    # ── Force flags ──────────────────────────────────────────────────

    def render_force_flags(
        self,
        frames: list[FrameGeom],
        forces: dict[str, tuple[float, float]],
        quantity: str = "Mz",
        scale_factor: float = 1.0,
    ) -> None:
        """Draw force/moment flag diagrams as one merged mesh.

        Args:
            frames: Frame geometries (undeformed) providing the member axes.
            forces: ``{elem_id: (value_at_i, value_at_j)}``.  Values must be
                **local** force/moment components — the flag plane is defined
                by the element's local transverse axis.
            quantity: Local component name (``'Mz'``, ``'My'``, ``'Fx'`` …).
                Also selects the extrusion direction (see
                :func:`_flag_direction`).
            scale_factor: Display scale (length per force/moment unit).
        """
        if not frames or not forces:
            return
        p = self.plotter
        import pyvista as pv

        from ...utils import compute_flag_parts

        # Peak |force| for diverging colour normalisation.
        max_abs = 0.0
        for f in frames:
            fij = forces.get(f.elem_id)
            if fij is not None:
                max_abs = max(max_abs, abs(fij[0]), abs(fij[1]))

        # ``compute_flag_parts`` yields ``(vertices, col_val)`` per polygon
        # part (3 or 4 corners), so accumulate a flat VTK face buffer and
        # one RGB colour per polygon (cell data).
        verts_acc: list[np.ndarray] = []
        faces_acc: list[int] = []
        colors_acc: list[tuple[float, float, float]] = []
        vert_offset = 0

        for f in frames:
            fij = forces.get(f.elem_id)
            if fij is None:
                continue
            vi, vj = fij
            vn = _flag_direction(quantity, f.start, f.end, getattr(f, "angle", 0.0))
            if vn is None:
                continue
            for verts, col_val in compute_flag_parts(f.start, f.end, vn, vi, vj, scale_factor):
                n = len(verts)
                verts_acc.append(np.asarray(verts, dtype=float))
                faces_acc.extend([n, *range(vert_offset, vert_offset + n)])
                colors_acc.append(_flag_rgb(col_val, max_abs))
                vert_offset += n

        if not verts_acc:
            return

        all_verts = np.vstack(verts_acc)
        mesh = pv.PolyData(all_verts, faces=np.asarray(faces_acc, dtype=int))
        mesh.cell_data["rgb"] = np.asarray(colors_acc, dtype=float)
        actor = p.add_mesh(
            mesh,
            scalars="rgb",
            rgb=True,
            opacity=0.85,
            lighting=False,
            show_scalar_bar=False,
        )
        self._add_actor(actor, "force_flags")

    # ── Scene management ─────────────────────────────────────────────

    def clear(self) -> None:
        p = self._plotter
        if p is not None:
            for actor in self._actors:
                with contextlib.suppress(Exception):
                    p.remove_actor(actor)
        self._actors = []
        self._categories = {}

    def show(self) -> None:
        p = self.plotter
        p.show()

    def screenshot(self, path: str) -> None:
        p = self.plotter
        p.screenshot(path)

    def export_html(self, path: str) -> None:
        p = self.plotter
        try:
            p.export_html(path)
        except ImportError:
            print("Warning: install 'nest_asyncio2' for HTML export: pip install nest_asyncio2")
            raise
