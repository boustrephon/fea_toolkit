"""Backend-agnostic 3D model viewer for structural models and results.

Usage::

    from fea_toolkit.plotting.viewer import ModelViewer

    # From an AnalysisBuilder
    viewer = ModelViewer(builder)
    viewer.show_model(show_nodes=True, color_by_section=True)
    viewer.show()

    # From a MeshModel directly (no builder needed)
    viewer = ModelViewer(mesh_model=mm)
    viewer.show_model(show_nodes=True, color_by_section=True)
    viewer.show()

    # From a MeshModel with collapsed parents (unsplit geometry)
    viewer = ModelViewer(mesh_model=mm, collapse_to_parents=True)

    # With results (requires builder or explicit data)
    viewer.overlay_deformed(scale=50)
    viewer.highlight_elements(frame_ids=["1", "5"], label="Check")
    viewer.export_html("report.html")
"""

from typing import Any, Optional

import numpy as np

from .renderers import AnnotationDef, FrameGeom, HighlightDef, NodeGeom, RenderBackend, ShellGeom


def _resolve_backend(backend: str, **kwargs) -> RenderBackend:
    """Import and instantiate a render backend by name."""
    if backend == "pyvista":
        from .renderers.pyvista import PyVistaRenderer

        return PyVistaRenderer(**kwargs)
    elif backend == "rhino":
        raise ImportError("Rhino backend requires Rhino 8 and is not yet implemented.")
    else:
        raise ValueError(f"Unknown render backend: {backend!r}. Choices: 'pyvista', 'rhino'.")


def _section_palette(
    sections: dict[str, Any],
) -> dict[str, tuple[float, float, float]]:
    """Build a deterministic colour map from section names."""
    palette = [
        (0.122, 0.467, 0.706),
        (0.839, 0.153, 0.157),
        (0.173, 0.627, 0.173),
        (0.580, 0.404, 0.741),
        (0.549, 0.337, 0.294),
        (0.890, 0.467, 0.122),
        (0.737, 0.741, 0.133),
        (0.094, 0.745, 0.765),
        (0.314, 0.314, 0.314),
        (0.859, 0.373, 0.522),
    ]
    names = sorted(sections.keys())
    return {n: palette[i % len(palette)] for i, n in enumerate(names)}


class ModelViewer:
    """Backend-agnostic 3D viewer for structural models and results.

    Extracts geometry from a builder or model data, then delegates
    rendering to a pluggable backend (PyVista, Rhino, etc.).

    When ``collapse_to_parents=True`` (default ``False``), inactive parent
    elements are used instead of their active child sub-elements, showing
    the model as drawn in SAP2000 (unsplit geometry).

    Args:
        builder: An ``AnalysisBuilder`` instance that has been built.
            If ``None``, provide *model_data* or *mesh_model* instead.
        model_data: A ``SAPModelData`` instance.  Ignored if *builder*
            is provided.
        mesh_model: A ``MeshModel`` instance.  Ignored if *builder* or
            *model_data* is provided.  This is the post-split topology
            and is sufficient for ``show_model()`` — no builder needed.
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        backend: Render backend name — ``'pyvista'`` (default) or
            ``'rhino'``.
        **kwargs: Passed to the backend constructor.
    """

    def __init__(
        self,
        builder: Any = None,
        model_data: Any = None,
        mesh_model: Any = None,
        collapse_to_parents: bool = False,
        backend: str = "pyvista",
        **kwargs,
    ):
        if builder is not None:
            self._model = builder.model
            self._builder = builder
        elif model_data is not None:
            self._model = model_data
            self._builder = None
        elif mesh_model is not None:
            self._model = mesh_model
            self._builder = None
        else:
            raise ValueError("Provide one of 'builder', 'model_data', or 'mesh_model'.")

        self._collapse_to_parents = collapse_to_parents
        self._backend: RenderBackend = _resolve_backend(backend, **kwargs)

        # Extracted geometry (populated lazily)
        self._frames: list[FrameGeom] = []
        self._shells: list[ShellGeom] = []
        self._nodes: list[NodeGeom] = []
        self._section_colors: dict[str, tuple[float, float, float]] = {}
        self._geom_extracted = False

    # ── Geometry extraction ──────────────────────────────────────────

    def _extract_geometry(self) -> None:
        """Extract frame, shell, and node geometry from the model.

        When ``collapse_to_parents=True``, inactive parent elements are
        included instead of their active child sub-elements, showing the
        model as drawn in SAP2000.
        """
        if self._geom_extracted:
            return

        md = self._model
        self._section_colors = _section_palette(md.sections)

        if self._collapse_to_parents:
            # Include inactive parents instead of active children.
            # Walk all elements: include inactive parents and unsplit
            # elements (those without parent_id).  Skip active children.
            for eid, elem in md.frame_elements.items():
                is_inactive = getattr(elem, "inactive", False)
                has_parent = getattr(elem, "parent_id", None) is not None
                if not is_inactive and has_parent:
                    continue  # active child — skip
                sec = md.frame_assignments.get(eid, "")
                ni = md.nodes.get(elem.node_i)
                nj = md.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                self._frames.append(
                    FrameGeom(
                        elem_id=eid,
                        section=sec,
                        node_i=elem.node_i,
                        node_j=elem.node_j,
                        start=np.array([ni.x, ni.y, ni.z], dtype=float),
                        end=np.array([nj.x, nj.y, nj.z], dtype=float),
                    )
                )

            # Shells — include inactive parents
            for aid, ae in md.area_elements.items():
                is_inactive = getattr(ae, "inactive", False)
                has_parent = getattr(ae, "parent_id", None) is not None
                if not is_inactive and has_parent:
                    continue
                sec = md.area_assignments.get(aid, "")
                verts = []
                for nid in ae.node_ids:
                    nd = md.nodes.get(nid)
                    if nd is None:
                        break
                    verts.append([nd.x, nd.y, nd.z])
                if len(verts) < 3:
                    continue
                self._shells.append(
                    ShellGeom(
                        area_id=aid,
                        section=sec,
                        vertices=np.array(verts, dtype=float),
                    )
                )
        else:
            # Normal path: active elements only
            elements = (
                self._builder.split_elements
                if self._builder and self._builder.split_elements
                else md.frame_elements
            )
            assignments = (
                self._builder.split_assignments
                if self._builder and self._builder.split_elements
                else md.frame_assignments
            )

            for eid, elem in elements.items():
                if getattr(elem, "inactive", False):
                    continue
                sec = assignments.get(eid, "")
                ni = md.nodes.get(elem.node_i)
                nj = md.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                self._frames.append(
                    FrameGeom(
                        elem_id=eid,
                        section=sec,
                        node_i=elem.node_i,
                        node_j=elem.node_j,
                        start=np.array([ni.x, ni.y, ni.z], dtype=float),
                        end=np.array([nj.x, nj.y, nj.z], dtype=float),
                    )
                )

            for aid, ae in md.area_elements.items():
                if getattr(ae, "inactive", False):
                    continue
                sec = md.area_assignments.get(aid, "")
                verts = []
                for nid in ae.node_ids:
                    nd = md.nodes.get(nid)
                    if nd is None:
                        break
                    verts.append([nd.x, nd.y, nd.z])
                if len(verts) < 3:
                    continue
                self._shells.append(
                    ShellGeom(
                        area_id=aid,
                        section=sec,
                        vertices=np.array(verts, dtype=float),
                    )
                )

        # Nodes
        for nid, nd in md.nodes.items():
            self._nodes.append(
                NodeGeom(
                    node_id=nid,
                    position=np.array([nd.x, nd.y, nd.z], dtype=float),
                )
            )

        self._geom_extracted = True

    # ── Model display ────────────────────────────────────────────────

    def show_model(
        self,
        show_nodes: bool = True,
        show_shells: bool = True,
        color_by_section: bool = True,
        opacity: float = 1.0,
        node_size: float = 0.02,
    ) -> "ModelViewer":
        """Display the structural model.

        Args:
            show_nodes: If True, draw node markers.
            show_shells: If True, draw shell elements.
            color_by_section: If True, colour elements by section name.
            opacity: Element opacity.
            node_size: Node marker size.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        if color_by_section:
            colors = self._section_colors
        else:
            colors = dict.fromkeys(self._section_colors, (0.5, 0.5, 0.5))

        self._backend.render_frames(self._frames, colors, opacity=opacity)

        if show_shells:
            self._backend.render_shells(self._shells, colors, opacity=opacity)

        if show_nodes:
            self._backend.render_nodes(self._nodes, color=(0.3, 0.3, 0.3), radius=node_size)

        return self

    # ── Results overlay ──────────────────────────────────────────────

    def overlay_deformed(
        self,
        displacements: Optional[dict[str, np.ndarray]] = None,
        scale: float = 1.0,
        color: tuple[float, float, float] = (0.3, 0.6, 1.0),
    ) -> "ModelViewer":
        """Overlay deformed shape on the model.

        Args:
            displacements: ``{node_id: (dx, dy, dz)}``.  If ``None``,
                reads from the builder's last static results.
            scale: Amplification factor.
            color: Deformed shape colour (RGB 0..1).

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()
        if displacements is None and self._builder is not None:
            results = getattr(self._builder, "_last_static_results", None)
            if results is not None:
                raw = results.get("nodal_displacements", {})
                displacements = {}
                for nid, nd in self._model.nodes.items():
                    raw_d = raw.get(nd.node_tag)
                    if raw_d is not None:
                        displacements[nid] = np.array(raw_d[:3], dtype=float)
        if displacements is None:
            print("Warning: no displacement data available for deformed overlay.")
            return self

        self._backend.render_deformed(
            self._frames,
            displacements,
            scale=scale,
            color=color,
        )
        return self

    def overlay_forces(
        self,
        elem_forces: Optional[dict[str, dict]] = None,
        quantity: str = "Mz",
        use_local: bool = True,
        scale_factor: Optional[float] = None,
    ) -> "ModelViewer":
        """Overlay force/moment flag diagram.

        Args:
            elem_forces: Element force dict from static analysis.
                If ``None``, reads from the builder's last results.
            quantity: Force/moment quantity (e.g. ``'Mz'``, ``'Fx'``).
            use_local: Use local-coordinate values.
            scale_factor: Flag size scaling.  Auto-computed if ``None``.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()
        if elem_forces is None and self._builder is not None:
            results = getattr(self._builder, "_last_static_results", None)
            if results is not None:
                elem_forces = results.get("element_forces")

        if elem_forces is None:
            print("Warning: no element force data available.")
            return self

        suffix = "_local" if use_local else ""
        q_i = f"{quantity.lower()}_i{suffix}"
        q_j = f"{quantity.lower()}_j{suffix}"

        forces: dict[str, tuple[float, float]] = {}
        vals = []
        for f in self._frames:
            ef = elem_forces.get(f.elem_id)
            if ef is None and self._builder is not None:
                # Try numeric tag
                tag_map = getattr(self._builder, "frame_tag_map", {})
                ops_tag = tag_map.get(f.elem_id)
                if ops_tag is not None:
                    ef = elem_forces.get(str(ops_tag))
            if ef is None:
                continue
            vi = ef.get(q_i, 0.0)
            vj = ef.get(q_j, 0.0)
            forces[f.elem_id] = (vi, vj)
            vals.extend([abs(vi), abs(vj)])

        if not forces:
            print(f"Warning: no {quantity} data found.")
            return self

        # Auto-scale: target flag height ≈ 10% of model diagonal
        if scale_factor is None:
            max_val = max(vals) if vals else 1.0
            if max_val < 1e-12:
                max_val = 1.0
            all_pts = np.vstack([f.start for f in self._frames] + [f.end for f in self._frames])
            diag = np.ptp(all_pts, axis=0)
            model_size = max(np.linalg.norm(diag), 1.0)
            scale_factor = 0.1 * model_size / max_val

        self._backend.render_force_flags(
            self._frames,
            forces,
            quantity=quantity,
            scale_factor=scale_factor,
        )
        return self

    # ── Highlighting ─────────────────────────────────────────────────

    def highlight_elements(
        self,
        frame_ids: Optional[list[str]] = None,
        area_ids: Optional[list[str]] = None,
        color: tuple[float, float, float] = (1.0, 0.0, 0.0),
        label: Optional[str] = None,
        radius: Optional[float] = None,
    ) -> "ModelViewer":
        """Highlight specific elements.

        Args:
            frame_ids: Frame element IDs to highlight.
            area_ids: Area element IDs to highlight.
            color: Highlight colour (RGB 0..1).
            label: Optional text label near the highlighted group.
            radius: Tube radius for frame highlights.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        id_set = set(frame_ids or [])
        matched_frames = [f for f in self._frames if f.elem_id in id_set]
        matched_shells = [s for s in self._shells if s.area_id in (area_ids or [])]

        h = HighlightDef(
            frame_ids=frame_ids or [],
            area_ids=area_ids or [],
            color=color,
            label=label,
            radius=radius,
            frames=matched_frames,
            shells=matched_shells,
        )

        self._backend.render_highlights([h])
        return self

    def highlight_nodes(
        self,
        node_ids: list[str],
        color: tuple[float, float, float] = (0.0, 1.0, 0.0),
        label: Optional[str] = None,
    ) -> "ModelViewer":
        """Highlight specific nodes.

        Args:
            node_ids: Node IDs to highlight.
            color: Highlight colour (RGB 0..1).
            label: Optional text label.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        id_set = set(node_ids)
        matched_nodes = [n for n in self._nodes if n.node_id in id_set]
        h = HighlightDef(
            node_ids=node_ids,
            color=color,
            label=label,
            nodes=matched_nodes,
        )
        self._backend.render_highlights([h])
        return self

    # ── Annotation ───────────────────────────────────────────────────

    def annotate(
        self,
        text: str,
        node_id: Optional[str] = None,
        position: Optional[np.ndarray] = None,
        color: tuple[float, float, float] = (1.0, 1.0, 0.0),
        font_size: int = 14,
    ) -> "ModelViewer":
        """Add a text annotation in 3D space.

        Args:
            text: Annotation text.
            node_id: Attach to this node's position.
            position: Explicit 3D position.  Ignored if *node_id* given.
            color: Text colour (RGB 0..1).
            font_size: Font size in points.

        Returns:
            ``self`` for chaining.
        """
        if node_id is not None:
            nd = self._model.nodes.get(node_id)
            if nd is None:
                print(f"Warning: node {node_id} not found.")
                return self
            position = np.array([nd.x, nd.y, nd.z], dtype=float)
        elif position is None:
            raise ValueError("Provide either 'node_id' or 'position'.")

        ann = AnnotationDef(
            text=text,
            position=position,
            color=color,
            font_size=font_size,
        )
        self._backend.render_annotations([ann])
        return self

    # ── Display & export ─────────────────────────────────────────────

    def show(self) -> None:
        """Display the interactive view."""
        self._backend.show()

    def screenshot(self, path: str) -> None:
        """Save a screenshot.

        Args:
            path: Output path (e.g. ``'view.png'``).
        """
        self._backend.screenshot(path)

    def export_html(self, path: str) -> None:
        """Export to standalone interactive HTML.

        Args:
            path: Output path (e.g. ``'view.html'``).
        """
        self._backend.export_html(path)

    def clear(self) -> None:
        """Remove all actors from the scene."""
        self._backend.clear()
