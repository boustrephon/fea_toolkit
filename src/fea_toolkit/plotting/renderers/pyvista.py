"""PyVista render backend for ModelViewer."""

from typing import Dict, List, Optional, Tuple
import numpy as np

from .base import (
    RenderBackend, FrameGeom, ShellGeom, NodeGeom,
    HighlightDef, AnnotationDef,
)


# ── Colour palette for section-based colouring ────────────────────────
# Matplotlib "tab10" palette, RGB in 0..1 range.
_SECTION_PALETTE = [
    (0.122, 0.467, 0.706),  # blue
    (0.839, 0.153, 0.157),  # red
    (0.173, 0.627, 0.173),  # green
    (0.580, 0.404, 0.741),  # purple
    (0.549, 0.337, 0.294),  # brown
    (0.890, 0.467, 0.122),  # orange
    (0.737, 0.741, 0.133),  # yellow-green
    (0.094, 0.745, 0.765),  # cyan
    (0.314, 0.314, 0.314),  # grey
    (0.859, 0.373, 0.522),  # pink
]


def _section_color(sec_name: str, palette: List[Tuple] = _SECTION_PALETTE) -> Tuple:
    """Deterministic colour for a section name."""
    idx = hash(sec_name) % len(palette)
    return palette[idx]


def _unit_vec(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0, 0.0])


class PyVistaRenderer(RenderBackend):
    """Render backend using PyVista.

    Requires ``pyvista`` — install via ``pip install pyvista``.
    """

    def __init__(self, off_screen: bool = False, notebook: bool = False):
        self._plotter = None
        self._off_screen = off_screen
        self._notebook = notebook
        # Keep track of all actors so ``clear()`` can remove them
        self._actors: list = []

    # ── Plotter initialisation ───────────────────────────────────────

    @property
    def plotter(self):
        if self._plotter is None:
            import pyvista as pv
            # Use pyvista's global theme
            theme = pv.global_theme
            theme.font.label_size = 12
            kwargs = dict(off_screen=self._off_screen, notebook=self._notebook)
            # Only pass window_size in off_screen mode
            if self._off_screen:
                kwargs['window_size'] = [1920, 1080]
            self._plotter = pv.Plotter(**kwargs)
            self._plotter.show_axes()
            # Show grid on the ground plane
            try:
                self._plotter.show_grid(
                    grid='back', location='outer', ticks='both',
                )
            except Exception:
                pass
        return self._plotter

    # ── Frame elements ───────────────────────────────────────────────

    def render_frames(
        self,
        frames: List[FrameGeom],
        colors: Dict[str, Tuple[float, float, float]],
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
        mesh['rgb'] = per_line_color
        actor = p.add_mesh(
            mesh, scalars='rgb', rgb=True, opacity=opacity,
            line_width=2, show_scalar_bar=False,
        )
        self._actors.append(actor)

    # ── Shell elements ───────────────────────────────────────────────

    def render_shells(
        self,
        shells: List[ShellGeom],
        colors: Dict[str, Tuple[float, float, float]],
        opacity: float = 1.0,
    ) -> None:
        if not shells:
            return
        p = self.plotter
        import pyvista as pv

        all_verts: List[np.ndarray] = []
        all_faces: List[np.ndarray] = []
        shell_colors: List[Tuple] = []
        offset = 0

        for s in shells:
            nv = len(s.vertices)
            # Triangulate quad → two tris (0,1,2 and 0,2,3)
            # Works for both triangles (nv=3) and quads (nv=4)
            if nv == 3:
                all_faces.append(np.array([3, offset, offset + 1, offset + 2]))
            elif nv >= 4:
                all_faces.append(np.array([3, offset, offset + 1, offset + 2]))
                all_faces.append(np.array([3, offset, offset + 2, offset + 3]))
            all_verts.append(s.vertices)
            shell_colors.append(colors.get(s.section, (0.7, 0.7, 0.7)))
            offset += nv

        if not all_verts:
            return

        verts = np.vstack(all_verts)
        faces = np.hstack(all_faces) if len(all_faces) > 0 else np.array([], dtype=int)
        mesh = pv.PolyData(verts, faces=faces)
        # Per-face colours need duplicate vertex entries — use cell scalars
        # Build cell-by-cell colour array
        cell_colors = []
        for s in shells:
            c = colors.get(s.section, (0.7, 0.7, 0.7))
            n_tris = 2 if len(s.vertices) == 4 else 1
            for _ in range(n_tris):
                cell_colors.append(c)
        if cell_colors:
            mesh.cell_data['rgb'] = np.array(cell_colors)
            actor = p.add_mesh(
                mesh, scalars='rgb', rgb=True, opacity=opacity,
                show_edges=True, edge_color='grey', lighting=True,
                show_scalar_bar=False,
            )
            self._actors.append(actor)

    # ── Nodes ────────────────────────────────────────────────────────

    def render_nodes(
        self,
        nodes: List[NodeGeom],
        color: Tuple[float, float, float] = (0.3, 0.3, 0.3),
        radius: float = 0.02,
    ) -> None:
        if not nodes:
            return
        p = self.plotter
        import pyvista as pv

        pts = np.array([n.position for n in nodes])
        cloud = pv.PolyData(pts)
        actor = p.add_mesh(
            cloud, color=color, point_size=radius * 20,
            style='points', render_points_as_spheres=True,
            show_scalar_bar=False,
        )
        self._actors.append(actor)

    # ── Highlights ───────────────────────────────────────────────────

    def render_highlights(
        self,
        highlights: List[HighlightDef],
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
                    tube, color=h.color, opacity=0.85,
                    show_scalar_bar=False,
                )
                self._actors.append(actor)

            # ── Highlighted nodes ──
            if h.nodes:
                pts = np.array([n.position for n in h.nodes])
                cloud = pv.PolyData(pts)
                actor = p.add_mesh(
                    cloud, color=h.color,
                    point_size=15, style='points',
                    render_points_as_spheres=True,
                    show_scalar_bar=False,
                )
                self._actors.append(actor)

            # ── Label ──
            if h.label:
                centroid = np.zeros(3)
                count = 0
                if h.frames:
                    all_pts = np.vstack([f.start for f in h.frames] +
                                        [f.end for f in h.frames])
                    centroid += all_pts.sum(axis=0)
                    count += len(all_pts)
                if h.nodes:
                    for n in h.nodes:
                        centroid += n.position
                        count += 1
                if count > 0:
                    centroid /= count
                else:
                    continue
                lbl = p.add_point_labels(
                    [centroid], [h.label],
                    font_size=16, text_color=h.color,
                    point_color=h.color, point_size=8,
                    shape='rounded_rect',
                )
                self._actors.append(lbl)

    # ── Annotations ──────────────────────────────────────────────────

    def render_annotations(
        self,
        annotations: List[AnnotationDef],
    ) -> None:
        if not annotations:
            return
        p = self.plotter
        for a in annotations:
            actor = p.add_point_labels(
                [a.position], [a.text],
                font_size=a.font_size,
                text_color=a.color,
                point_color=a.color,
                point_size=4,
                shape='rounded_rect',
            )
            self._actors.append(actor)

    # ── Deformed shape ───────────────────────────────────────────────

    def render_deformed(
        self,
        frames: List[FrameGeom],
        displacements: Dict[str, np.ndarray],
        scale: float = 1.0,
        color: Tuple[float, float, float] = (0.3, 0.6, 1.0),
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
            mesh, color=color, opacity=0.7,
            line_width=2, show_scalar_bar=False,
        )
        self._actors.append(actor)

    # ── Force flags ──────────────────────────────────────────────────

    def render_force_flags(
        self,
        frames: List[FrameGeom],
        forces: Dict[str, Tuple[float, float]],
        quantity: str = "Mz",
        scale_factor: float = 1.0,
    ) -> None:
        if not frames or not forces:
            return
        p = self.plotter
        import pyvista as pv

        from ..utils import compute_flag_parts

        flag_verts: List[np.ndarray] = []
        flag_faces: List[np.ndarray] = []
        flag_colors: List[Tuple] = []
        vert_offset = 0

        for f in frames:
            fij = forces.get(f.elem_id)
            if fij is None:
                continue
            vi, vj = fij
            parts = compute_flag_parts(
                f.start, f.end, vi, vj, scale_factor
            )
            if parts is None:
                continue
            verts, tris, colors = parts  # (V, 3), (T, 3), (T, 3)
            nv = len(verts)
            flag_verts.append(verts)
            for tri in tris:
                flag_faces.append(np.array([3, tri[0] + vert_offset,
                                            tri[1] + vert_offset,
                                            tri[2] + vert_offset]))
            flag_colors.extend(colors)
            vert_offset += nv

        if not flag_verts:
            return

        all_verts = np.vstack(flag_verts)
        all_faces = np.hstack(flag_faces) if flag_faces else np.array([], dtype=int)
        mesh = pv.PolyData(all_verts, faces=all_faces)
        mesh.cell_data['rgb'] = np.array(flag_colors)
        actor = p.add_mesh(
            mesh, scalars='rgb', rgb=True, opacity=0.85,
            lighting=False, show_scalar_bar=True,
            scalar_bar_args={'title': quantity, 'n_colors': 10},
        )
        self._actors.append(actor)

    # ── Scene management ─────────────────────────────────────────────

    def clear(self) -> None:
        p = self._plotter
        if p is not None:
            for actor in self._actors:
                try:
                    p.remove_actor(actor)
                except Exception:
                    pass
        self._actors = []

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
            print("Warning: install 'nest_asyncio2' for HTML export: "
                  "pip install nest_asyncio2")
            raise
