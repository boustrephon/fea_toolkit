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

import warnings
from typing import Any, Optional, Union

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


def _end_force_values(entry: dict, quantity: str, use_local: bool) -> Optional[tuple[float, float]]:
    """Extract ``(value_at_i, value_at_j)`` for *quantity* from one element entry.

    Tolerates the key conventions used across the toolkit:

    * ``{q}_i`` / ``{q}_j`` keys (with a ``_local`` suffix when *use_local*),
      e.g. ``mz_i_local`` / ``Mz_j`` — lower- and upper-case spellings;
    * the bare ``{quantity}`` (I-end) + ``{quantity}_j`` (J-end) form produced
      by :meth:`AnalysisBuilder.extract_static_element_forces`, which is
      always in local coordinates.

    Returns:
        ``(value_i, value_j)``, or ``None`` when no matching key is present.
    """
    q_low = quantity.lower()
    if use_local:
        # Local values only: an explicit ``_local`` suffix, or the bare
        # ``{quantity}`` / ``{quantity}_j`` form produced by
        # :meth:`AnalysisBuilder.extract_static_element_forces` (always local).
        # The bare ``{q}_i`` / ``{q}_j`` keys are documented **global** results
        # and must not be read as local values.
        pairs = (
            (f"{q_low}_i_local", f"{q_low}_j_local"),
            (f"{quantity}_i_local", f"{quantity}_j_local"),
            (quantity, f"{quantity}_j"),
        )
    else:
        # Global values only: ``{q}_i`` / ``{q}_j`` (lower- and upper-case
        # spellings).  The bare local form must not be read as a global value.
        pairs = (
            (f"{q_low}_i", f"{q_low}_j"),
            (f"{quantity}_i", f"{quantity}_j"),
        )
    for i_key, j_key in pairs:
        # Match on the I-end key: it identifies the key convention, and in
        # the builder form the I-end key is the bare quantity (``"Mz"``) whose
        # J-end partner is ``"Mz_j"``.
        if i_key in entry:
            return float(entry[i_key]), float(entry.get(j_key, 0.0))
    return None


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
        backend: Union[str, RenderBackend] = "pyvista",
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
        if isinstance(backend, RenderBackend):
            # An explicit backend instance was injected -- e.g. the Qt GUI's
            # QtRenderBackend wrapping an embedded QtInteractor.
            self._backend: RenderBackend = backend
        else:
            self._backend = _resolve_backend(backend, **kwargs)

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

        # Collapse-to-parents: include inactive parents and unsplit
        # elements (those without a parent); skip active children.
        # Otherwise: active elements only.
        collapse = self._collapse_to_parents

        def include(_eid, elem) -> bool:
            inactive = getattr(elem, "inactive", False)
            has_parent = getattr(elem, "parent_id", None) is not None
            if collapse:
                # Collapsed view: unsplit originals (active, no parent) plus
                # the inactive parents they were subdivided into.
                return inactive or not has_parent
            # Default view: every active element, including split children.
            return not inactive

        # ``self._model`` is the frozen ``MeshModel`` when a builder is
        # supplied (``builder.model is builder.mesh_model``) and the
        # ``SAPModelData`` otherwise.  Both expose ``frame_elements`` /
        # ``frame_assignments`` directly, so no legacy ``split_elements``
        # attributes are needed.
        elements = md.frame_elements
        assignments = md.frame_assignments

        for eid, elem in elements.items():
            if not include(eid, elem):
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
                    angle=float(getattr(elem, "angle", 0.0)),
                )
            )

        for aid, ae in md.area_elements.items():
            if not include(aid, ae):
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
            # ``run_static_analysis()`` caches its result dict on the
            # builder; ``nodal_displacements`` is keyed by node id.
            results = getattr(self._builder, "_last_static_results", None)
            if results is not None:
                raw = results.get("nodal_displacements", {})
                displacements = {}
                for nid, nd in self._model.nodes.items():
                    raw_d = raw.get(nid)
                    if raw_d is None:
                        # Tolerate a tag-keyed dict as well.
                        raw_d = raw.get(str(nd.node_tag))
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

        The flag diagram is a **local-quantity** visualisation — its geometry
        is extruded in the member's local transverse direction — so local
        components should drive it (see *use_local*).

        Args:
            elem_forces: Element force data.  Two shapes are accepted:

                * ``{elem_id: {component_key: value}}`` — keyed by SAP element
                  id or OpenSees tag, with ``{q}_i`` / ``{q}_j`` keys (the
                  ``_local`` suffix is read when ``use_local=True``);
                * the tag-keyed, upper-case ``{"Mz", "Mz_j", ...}`` dict from
                  :meth:`~fea_toolkit.opensees.AnalysisBuilder.extract_static_element_forces`.

                If ``None`` (and a builder is available), the builder is
                queried via ``extract_static_element_forces()``.
            quantity: Force/moment quantity (e.g. ``'Mz'``, ``'Fx'``).
            use_local: Read local-coordinate values (default ``True``).  The
                flag plane is local, so ``False`` (global components) is only
                geometrically consistent when the global and local axes
                coincide (e.g. planar frames); a warning is emitted.
            scale_factor: Flag size scaling.  Auto-computed if ``None``.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        # Resolve the builder's element forces on demand: element forces are
        # not cached on ``_last_static_results`` (which holds nodal
        # displacements and reactions only), so extract them via the
        # two-stage API.
        if elem_forces is None and self._builder is not None:
            try:
                elem_forces = self._builder.extract_static_element_forces()
            except Exception as exc:  # pragma: no cover - defensive
                print(f"Warning: could not read element forces from builder: {exc}")
                elem_forces = None

        if not elem_forces:
            print("Warning: no element force data available.")
            return self

        tag_map = getattr(self._builder, "frame_tag_map", {}) if self._builder else {}

        forces: dict[str, tuple[float, float]] = {}
        vals = []
        for f in self._frames:
            ef = elem_forces.get(f.elem_id)
            if ef is None and tag_map:
                ops_tag = tag_map.get(f.elem_id)
                if ops_tag is not None:
                    ef = elem_forces.get(ops_tag)
                    if ef is None:
                        ef = elem_forces.get(str(ops_tag))
            if ef is None:
                continue
            values = _end_force_values(ef, quantity, use_local)
            if values is None:
                continue
            vi, vj = values
            forces[f.elem_id] = (vi, vj)
            vals.extend([abs(vi), abs(vj)])

        if not forces:
            print(f"Warning: no {quantity} data found.")
            return self

        if not use_local:
            warnings.warn(
                "overlay_forces(use_local=False): the flag diagram is a "
                "local-quantity visualisation — global components are only "
                "consistent when the global and local axes coincide (e.g. "
                "planar frames).",
                UserWarning,
                stacklevel=2,
            )

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
