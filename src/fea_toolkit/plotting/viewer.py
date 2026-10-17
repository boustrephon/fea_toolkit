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

import logging
import warnings
from collections.abc import Iterable
from typing import Any, Optional, Union

import numpy as np

from ..model.geometry import frame_end_offsets_global
from ..model.load_glyphs import area_load_values, extract_load_glyphs
from .renderers import AnnotationDef, FrameGeom, HighlightDef, NodeGeom, RenderBackend, ShellGeom
from .renderers.base import RestraintGeom

logger = logging.getLogger(__name__)


def _resolve_backend(backend: str, **kwargs) -> RenderBackend:
    """Import and instantiate a render backend by name."""
    if backend == "pyvista":
        from .renderers.pyvista import PyVistaRenderer

        return PyVistaRenderer(**kwargs)
    elif backend == "rhino":
        raise ImportError("Rhino backend requires Rhino 8 and is not yet implemented.")
    else:
        raise ValueError(f"Unknown render backend: {backend!r}. Choices: 'pyvista', 'rhino'.")


def _section_names(model: Any) -> set[str]:
    """Every section name *model* references — definitions plus assignments.

    A results archive's display model carries section *names* on its element
    assignments but no section *definitions* (``sections`` is empty), so the
    palette must be built from the union or such a model renders grey.
    """
    names = set(getattr(model, "sections", {}) or {})
    names.update((getattr(model, "frame_assignments", {}) or {}).values())
    names.update((getattr(model, "area_assignments", {}) or {}).values())
    names.discard("")
    return names


def _section_palette(
    names: Iterable[str],
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
    ordered = sorted(set(names))
    return {n: palette[i % len(palette)] for i, n in enumerate(ordered)}


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


#: Element highlights are drawn as tubes whose radius is a fraction of the
#: geometry's bounding-box diagonal (floored, so tiny models still show one).
#: A single fraction then works for a metre-scale building and a
#: millimetre-scale detail alike.  Tuned by eye: 1.5 % read as an oversized
#: sleeve, so the default is now 0.75 %.
_HIGHLIGHT_RADIUS_FRACTION = 0.0075
_HIGHLIGHT_RADIUS_MIN = 0.01

#: Support-glyph length, as a fraction of the model's bounding-box diagonal and
#: floored in model units — the same self-scaling approach as the highlight, but
#: generous: a support symbol is meant to be read at whole-model zoom, where the
#: highlight's 0.75 % would be a pixel or two.
_RESTRAINT_SIZE_FRACTION = 0.04
_RESTRAINT_SIZE_MIN = 0.08

#: Applied-load glyphs — the fraction of the model diagonal a peak load value is
#: drawn at.  A line-load *intensity* and a point-load *force* are different
#: units, so the renderer normalises each kind to its own peak at this length
#: (see ``render_loads``).
_LOAD_SIZE_FRACTION = 0.05
_LOAD_SIZE_MIN = 0.05

#: Node markers.  A restrained node is drawn a little larger, in green, so a
#: model's supports read at a glance.  Both values stay **under** the selection
#: overlay (a blue sphere of a fixed, larger size) and use a colour it never
#: uses, so "restrained" and "selected" can never be mistaken for each other.
_NODE_COLOR = (0.3, 0.3, 0.3)
_CONSTRAINED_NODE_COLOR = (0.15, 0.65, 0.25)
_CONSTRAINED_NODE_SIZE_FACTOR = 1.2


def _offset_frame_endpoints(
    md: Any, elem: Any, ni: Any, nj: Any
) -> "tuple[np.ndarray, np.ndarray]":
    """Drawn ``(start, end)`` for a frame with rigid end offsets applied.

    Mirrors the AnalysisBuilder's ``-jntOffset`` lookup — ``get(frame_end_offsets,
    elem.elem_id)`` — so the flexible member is drawn centroid-to-centroid
    (``p_i + off_i`` → ``p_j + off_j``) and the drawn, analysed and exported
    geometry agree.  A frame without an offset record stays on the reference
    line unchanged.
    """
    start = np.array([ni.x, ni.y, ni.z], dtype=float)
    end = np.array([nj.x, nj.y, nj.z], dtype=float)
    offset = getattr(md, "frame_end_offsets", {}).get(elem.elem_id)
    if offset is None:
        return start, end
    end_i = offset.end_i
    end_j = offset.end_j
    # Clamp the combined longitudinal offsets to 0.99 times the member length,
    # exactly as the Rhino export does, so offsets that exceed the member
    # length cannot fold the drawn flexible span back on itself.
    length = float(np.linalg.norm(end - start))
    total_offset = end_i + end_j
    if total_offset > length * 0.99:
        scale = length * 0.99 / max(total_offset, 1e-12)
        end_i *= scale
        end_j *= scale
    off_i, off_j = frame_end_offsets_global(
        end - start,
        float(getattr(elem, "angle", 0.0)),
        end_i,
        end_j,
        offset.off_y_i,
        offset.off_z_i,
        offset.off_y_j,
        offset.off_z_j,
    )
    return start + off_i, end + off_j


class ModelViewer:
    """Backend-agnostic 3D viewer for structural models and results.

    Extracts geometry from a builder or model data, then delegates
    rendering to a pluggable backend (PyVista, Rhino, etc.).

    When ``collapse_to_parents=True`` (default ``False``), inactive parent
    elements are used instead of their active child sub-elements, showing
    the model as drawn in SAP2000 (unsplit geometry).

    A *selection* narrows what is drawn without copying the model: only the
    matching elements (and the joints they touch) reach the backend, and a
    joint-only selection brings the members framing into it along with it
    (:meth:`fea_toolkit.model.selection.Selection.resolve_connected`).

    Args:
        builder: An ``AnalysisBuilder`` instance that has been built.
            If ``None``, provide *model_data* or *mesh_model* instead.
        model_data: A ``SAPModelData`` instance.  Ignored if *builder*
            is provided.
        mesh_model: A ``MeshModel`` instance.  Ignored if *builder* or
            *model_data* is provided.  This is the post-split topology
            and is sufficient for ``show_model()`` — no builder needed.
        collapse_to_parents: Show unsplit parent elements (default ``False``).
        selection: Optional ``Selection`` narrowing the drawn geometry
            (default ``None`` draws the whole model).
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
        selection: Any = None,
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
        self._selection = selection
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
        self._restraints: list[RestraintGeom] = []
        self._loads: list = []
        self._section_colors: dict[str, tuple[float, float, float]] = {}
        # Display transforms — applied when drawing, never to the geometry above,
        # so the model itself stays the source of truth for results and selection.
        self._shrink: float = 1.0
        self._shell_opacity: float = 1.0
        self._load_scale_fraction: float = _LOAD_SIZE_FRACTION
        self._geom_extracted = False
        self._model_diag: Optional[float] = None

    # ── Geometry extraction ──────────────────────────────────────────

    def _extract_geometry(self) -> None:
        """Extract frame, shell, and node geometry from the model.

        When ``collapse_to_parents=True``, inactive parent elements are
        included instead of their active child sub-elements, showing the
        model as drawn in SAP2000.

        When a ``selection`` was given, only the elements and joints it
        resolves to are extracted; the model itself is untouched.
        """
        if self._geom_extracted:
            return

        md = self._model
        self._section_colors = _section_palette(_section_names(md))

        # Collapse-to-parents: include inactive parents and unsplit
        # elements (those without a parent); skip active children.
        # Otherwise: active elements only.
        collapse = self._collapse_to_parents

        # A selection is a filter over the *same* model: resolve it once to
        # (frames, areas, nodes) and keep only those.  ``resolve_connected``
        # brings the members framing into a selected joint with it.
        frame_sel = area_sel = node_sel = None
        if self._selection is not None:
            # Collapsed mode draws the parents, so it must resolve with them.
            selected_frames, selected_areas, selected_nodes = self._selection.resolve_connected(
                md, include_parents=collapse
            )
            frame_sel, area_sel, node_sel = (
                set(selected_frames),
                set(selected_areas),
                set(selected_nodes),
            )

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
            if frame_sel is not None and eid not in frame_sel:
                continue
            sec = assignments.get(eid, "")
            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            start, end = _offset_frame_endpoints(md, elem, ni, nj)
            self._frames.append(
                FrameGeom(
                    elem_id=eid,
                    section=sec,
                    node_i=elem.node_i,
                    node_j=elem.node_j,
                    start=start,
                    end=end,
                    angle=float(getattr(elem, "angle", 0.0)),
                )
            )

        for aid, ae in md.area_elements.items():
            if not include(aid, ae):
                continue
            if area_sel is not None and aid not in area_sel:
                continue
            sec = md.area_assignments.get(aid, "")
            verts = []
            node_ids = []
            for nid in ae.node_ids:
                nd = md.nodes.get(nid)
                if nd is None:
                    break
                verts.append([nd.x, nd.y, nd.z])
                node_ids.append(nid)
            if len(verts) < 3:
                continue
            self._shells.append(
                ShellGeom(
                    area_id=aid,
                    section=sec,
                    vertices=np.array(verts, dtype=float),
                    node_ids=tuple(node_ids),
                )
            )

        # Nodes
        for nid, nd in md.nodes.items():
            if node_sel is not None and nid not in node_sel:
                continue
            self._nodes.append(
                NodeGeom(
                    node_id=nid,
                    position=np.array([nd.x, nd.y, nd.z], dtype=float),
                )
            )

        # Supports — only the nodes that actually restrain something, so the
        # renderer never has to decide whether an entry means anything.  An
        # archive has no restraints at all (``mesh_model_from_geometry`` leaves
        # them empty), which is why this reads defensively.
        for nid, restraint in (getattr(md, "restraints", None) or {}).items():
            dofs = tuple(int(flag) for flag in getattr(restraint, "dofs", ()))
            nd = md.nodes.get(nid)
            if nd is None or not any(dofs):
                continue
            self._restraints.append(
                RestraintGeom(
                    node_id=nid,
                    position=np.array([nd.x, nd.y, nd.z], dtype=float),
                    dofs=dofs,
                )
            )

        self._geom_extracted = True

    # ── Model display ────────────────────────────────────────────────

    def show_model(
        self,
        show_nodes: bool = True,
        show_frames: bool = True,
        show_shells: bool = True,
        color_by_section: bool = True,
        opacity: float = 1.0,
        node_size: float = 0.02,
        shrink: float = 1.0,
        shell_opacity: Optional[float] = None,
        show_restraints: bool = True,
        restraint_style: str = "block",
        show_loads: bool = False,
        load_factors: Optional[dict[str, float]] = None,
        load_scale: float = _LOAD_SIZE_FRACTION,
        load_area_style: str = "arrows",
        load_arrows_per_shell: int = 9,
        show_load_colours: bool = False,
        show_extruded: bool = False,
        extruded_detail: str = "plates",
    ) -> "ModelViewer":
        """Display the structural model.

        Args:
            show_nodes: If True, draw node markers.
            show_frames: If True, draw the frame line skeleton.
            show_shells: If True, draw shell elements.
            color_by_section: If True, colour elements by section name.
            opacity: Element opacity.
            node_size: Node marker size.
            shrink: Draw every element at this fraction of its true size,
                centred on its true position — SAP2000's *shrink elements*, which
                opens up the joints so they can be seen and clicked.  ``1.0``
                (the default) is the true geometry.
            shell_opacity: Opacity for area elements **alone**.  ``None`` (the
                default) uses *opacity*, keeping the single-knob behaviour;
                passing a value lets a slab be made translucent while the frame
                lines stay crisp.
            show_restraints: If True, draw a support symbol at each restrained
                node — one glyph per restrained DOF (see
                :mod:`fea_toolkit.plotting.restraint_glyphs`).  Models without
                restraints, and results archives (which carry none), simply draw
                nothing.
            restraint_style: Support-symbol style — ``"block"`` (plates and
                discs, the default) or ``"arrow"`` (arrows and curls).
            show_loads: If True, draw applied-load glyphs — rectangular /
                trapezoidal flags for distributed loads, arrows for point loads,
                and a grid of arrows (or a one-sided extrusion) for area
                pressures (see :mod:`fea_toolkit.model.load_glyphs`).  Off by
                default: glyphs are dense and overlap the structure.
            load_factors: Optional ``{pattern: factor}`` narrowing and scaling
                which loads are drawn — pass a
                :class:`~fea_toolkit.model.load_glyphs.LoadSource`'s
                ``patterns`` to draw one source (a pattern, case or
                combination).  ``None`` draws every load at factor 1.0.
            load_scale: Fraction of the model diagonal a peak load value is
                drawn at (default :data:`_LOAD_SIZE_FRACTION`).
            load_area_style: How an area pressure is drawn — ``"arrows"`` (a
                grid of arrows over the face, the default) or ``"extrusion"``
                (a one-sided prism along the pressure direction).
            load_arrows_per_shell: Number of arrows drawn over each area face
                when ``load_area_style == "arrows"``.
            show_load_colours: If True, colour each area element by its
                shell-face pressure (a diverging warm/cool scale with a scalar
                bar), independent of ``show_loads``.
            show_extruded: When True, draw each frame as an extruded solid
                (its cross-section swept along the member) *in addition to* the
                line skeleton, which remains the pickable layer.
            extruded_detail: ``"plates"`` (the efficient open-surface
                representation) or ``"solid"`` (the closed perimeter with caps).

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        if color_by_section:
            colors = self._section_colors
        else:
            colors = dict.fromkeys(self._section_colors, (0.5, 0.5, 0.5))

        self._shrink = shrink
        self._shell_opacity = opacity if shell_opacity is None else shell_opacity
        self._restraint_style = restraint_style
        self._load_scale_fraction = load_scale

        if show_extruded:
            # The extruded solids are pure decoration (non-pickable); the line
            # skeleton below stays the pickable frame layer, so selection and
            # highlighting work exactly as they do for the line view.
            self._backend.render_extruded_frames(
                self._frames,
                getattr(self._model, "sections", {}) or {},
                colors,
                opacity=opacity,
                shrink=shrink,
                detail=extruded_detail,
            )
        if show_frames:
            self._backend.render_frames(self._frames, colors, opacity=opacity, shrink=shrink)

        if show_shells:
            self._backend.render_shells(
                self._shells, colors, opacity=self._shell_opacity, shrink=shrink
            )

        if show_nodes:
            # The full cloud first — it is the *pickable* one, and a pick reports
            # an index into the actor it hit, which is how a click on a marker maps
            # back to a node id.
            self._backend.render_nodes(self._nodes, color=_NODE_COLOR, radius=node_size)

            # A restrained node is then marked *over* that cloud: larger, green, and
            # **non-pickable**.  Splitting the cloud in two instead would shift the
            # pick index away from the one the selection index is built from, so the
            # marker is drawn as decoration rather than as its own node layer.
            supported = {r.node_id for r in self._restraints}
            if supported:
                self._backend.render_nodes(
                    [n for n in self._nodes if n.node_id in supported],
                    color=_CONSTRAINED_NODE_COLOR,
                    radius=node_size * _CONSTRAINED_NODE_SIZE_FACTOR,
                    pickable=False,
                )

        if show_restraints and self._restraints:
            self._backend.render_restraints(
                self._restraints, size=self.restraint_size(), style=self._restraint_style
            )

        if show_loads or show_load_colours:
            self._loads = extract_load_glyphs(self._model, pattern_factors=load_factors)
        if show_loads and self._loads:
            self._backend.render_loads(
                self._loads,
                size=self.load_size(),
                area_style=load_area_style,
                arrows_per_shell=load_arrows_per_shell,
            )
        if show_load_colours:
            self._backend.render_load_colours(
                self._shells, area_load_values(self._model, pattern_factors=load_factors)
            )

        return self

    def set_shell_opacity(self, opacity: float) -> "ModelViewer":
        """Re-apply an opacity to the drawn area elements, in place.

        The live counterpart of ``show_model(shell_opacity=…)``: an actor's
        opacity is a render property, so changing it does not have to rebuild the
        mesh — which is what lets a transparency control stay interactive on a
        large model.  The value is remembered, so it also applies to whatever
        this viewer draws next.

        Args:
            opacity: New opacity — ``0`` invisible, ``1`` opaque.

        Returns:
            ``self`` for chaining.
        """
        self._shell_opacity = float(opacity)
        self._backend.set_category_opacity("shells", opacity)
        return self

    # ── Results overlay ──────────────────────────────────────────────

    def overlay_deformed(
        self,
        displacements: Optional[dict[str, np.ndarray]] = None,
        scale: Optional[float] = None,
        auto_fraction: float = 0.1,
        color: tuple[float, float, float] = (0.3, 0.6, 1.0),
    ) -> "ModelViewer":
        """Overlay deformed shape on the model.

        Args:
            displacements: ``{node_id: (dx, dy, dz)}``.  If ``None``,
                reads from the builder's last static results.
            scale: Amplification factor.  ``None`` (default) auto-scales so the
                largest displacement is ``auto_fraction`` of the model diagonal.
            auto_fraction: Target shape size as a fraction of the model diagonal,
                used only when *scale* is ``None``.
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
            logger.warning("Warning: no displacement data available for deformed overlay.")
            return self

        if scale is None:
            scale = self._auto_amplification(displacements, auto_fraction)

        self._backend.render_deformed(
            self._frames,
            displacements,
            scale=scale,
            color=color,
            shells=self._shells,
        )
        return self

    def _auto_amplification(self, displacements: dict[str, np.ndarray], fraction: float) -> float:
        """Amplification making the largest displacement ``fraction`` of the model.

        A displacement is a small fraction of the model's size, so it is
        invisible 1:1 — the same reason SAP2000 offers a scale box.  The
        amplification is unit-agnostic: it targets a fraction of the model
        diagonal, so a millimetre model and a metre model both read the same
        way.
        """
        if self._frames:
            all_pts = np.vstack([f.start for f in self._frames] + [f.end for f in self._frames])
        elif self._shells:
            # A shell-only model (a slab with no frames) still deserves the same
            # auto-scale: measure its size from the shell vertices, not 1:1.
            all_pts = np.vstack([s.vertices for s in self._shells])
        else:
            return 1.0
        model_size = max(float(np.linalg.norm(np.ptp(all_pts, axis=0))), 1.0)
        max_disp = max(
            (float(np.linalg.norm(np.asarray(d, dtype=float)[:3])) for d in displacements.values()),
            default=0.0,
        )
        if max_disp < 1e-12:
            return 1.0
        return fraction * model_size / max_disp

    def deformed_amplification(
        self, displacements: dict[str, np.ndarray], fraction: float
    ) -> float:
        """The amplification :meth:`overlay_deformed` would use for *displacements*.

        Exposes :meth:`_auto_amplification` to a caller that needs the base scale
        once (e.g. to oscillate it for an animation) rather than letting
        :meth:`overlay_deformed` recompute it on every redraw.

        Args:
            displacements: ``{node_id: (dx, dy, dz)}``.
            fraction: Target shape size as a fraction of the model diagonal.

        Returns:
            The amplification factor, or ``1.0`` when there is nothing to scale.
        """
        self._extract_geometry()
        return self._auto_amplification(displacements, fraction)

    def overlay_forces(
        self,
        elem_forces: Optional[dict[str, dict]] = None,
        quantity: str = "Mz",
        use_local: bool = True,
        scale_factor: Optional[float] = None,
        auto_fraction: float = 0.1,
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
            scale_factor: Flag size scaling — length per force/moment unit.
                ``None`` (default) auto-scales so the largest flag is
                ``auto_fraction`` of the model diagonal.
            auto_fraction: Target flag size as a fraction of the model diagonal,
                used only when *scale_factor* is ``None``.

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
                logger.warning("Warning: could not read element forces from builder: %s", exc)
                elem_forces = None

        if not elem_forces:
            logger.warning("Warning: no element force data available.")
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
            logger.warning("Warning: no %s data found.", quantity)
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

        # Auto-scale: target flag height ≈ auto_fraction of the model diagonal.
        if scale_factor is None:
            max_val = max(vals) if vals else 1.0
            if max_val < 1e-12:
                max_val = 1.0
            all_pts = np.vstack([f.start for f in self._frames] + [f.end for f in self._frames])
            diag = np.ptp(all_pts, axis=0)
            model_size = max(np.linalg.norm(diag), 1.0)
            scale_factor = auto_fraction * model_size / max_val

        self._backend.render_force_flags(
            self._frames,
            forces,
            quantity=quantity,
            scale_factor=scale_factor,
        )
        return self

    def overlay_shell_forces(
        self,
        shell_forces: Optional[dict[str, dict]] = None,
        quantity: str = "Nxy",
    ) -> "ModelViewer":
        """Overlay a per-shell scalar field (stress/force resultants) on areas.

        The shell counterpart of :meth:`overlay_forces`: each area element is
        coloured by the chosen resultant on the same diverging scale the flag
        diagram uses.

        Args:
            shell_forces: ``{shell_id: {component: value}}`` — the shape
                :meth:`~fea_toolkit.io.results_repository.ResultsRepository.shell_forces`
                returns (keys ``fx``/``fy``/``fxy``/``mx``/``my``/``mxy``).
            quantity: Resultant to colour by — a display label (``Nx``, ``Ny``,
                ``Nxy``, ``Mx``, ``My``, ``Mxy``) or the array key underneath.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        if not shell_forces:
            logger.warning("Warning: no shell force data available.")
            return self

        from ..model.sap_data import SHELL_FORCE_QUANTITY_LABELS

        key = SHELL_FORCE_QUANTITY_LABELS.get(quantity, quantity)
        values: dict[str, float] = {}
        for shell_id, entry in shell_forces.items():
            if key in entry:
                values[shell_id] = float(entry[key])

        if not values:
            logger.warning("Warning: no %s data found.", quantity)
            return self

        self._backend.render_shell_scalar_field(self._shells, values)
        return self

    # ── Highlighting ─────────────────────────────────────────────────

    def _model_diagonal(self) -> float:
        """Bounding-box diagonal of the extracted geometry (``0.0`` when empty).

        Cached: the geometry only changes when a new model is displayed.
        """
        if self._model_diag is None:
            self._extract_geometry()
            pts: list = [f.start for f in self._frames] + [f.end for f in self._frames]
            pts += [n.position for n in self._nodes]
            for shell in self._shells:
                pts.extend(shell.vertices)
            if not pts:
                self._model_diag = 0.0
            else:
                spread = np.ptp(np.asarray(pts, dtype=float), axis=0)
                self._model_diag = float(np.linalg.norm(spread))
        return self._model_diag

    def geometry(self) -> "tuple[list[FrameGeom], list[ShellGeom], list[NodeGeom]]":
        """The extracted geometry that is actually being rendered.

        Returned **in render order**: a backend batches these lists into meshes
        in this order, so an index into a rendered cell maps straight back to
        an entity (see :class:`fea_toolkit.gui.controllers.selection.SelectionIndex`).

        Returns:
            ``(frames, shells, nodes)``, extracting them on first call.
        """
        self._extract_geometry()
        return self._frames, self._shells, self._nodes

    def highlight_radius(self) -> float:
        """Tube radius used for element highlights on this model.

        A fixed radius cannot suit every unit system -- ``0.03`` is a hairline
        on a metre-scale building and a fat tube on a millimetre-scale detail --
        so the default scales with the geometry.

        Returns:
            ``_HIGHLIGHT_RADIUS_FRACTION`` of the model's bounding-box diagonal,
            floored at ``_HIGHLIGHT_RADIUS_MIN``.
        """
        return max(_HIGHLIGHT_RADIUS_FRACTION * self._model_diagonal(), _HIGHLIGHT_RADIUS_MIN)

    def restraint_size(self) -> float:
        """Glyph length for support symbols, in model units.

        Scaled to the model exactly as :meth:`highlight_radius` is: a fixed length
        would be invisible on a bridge and swamp a small frame.  Capped at
        :meth:`_restraint_size_cap` (500 mm in the model's length unit), so a
        support symbol never grows past a legible real-world size.
        """
        scaled = max(_RESTRAINT_SIZE_FRACTION * self._model_diagonal(), _RESTRAINT_SIZE_MIN)
        return min(scaled, self._restraint_size_cap())

    def load_size(self) -> float:
        """Target glyph length for applied loads, in model units.

        The peak value of each load kind is drawn at this length (see
        :meth:`fea_toolkit.plotting.renderers.RenderBackend.render_loads`).
        Scaled to the model diagonal exactly like :meth:`restraint_size` — a
        fixed length would be invisible on a bridge and swamp a small frame.
        """
        return max(self._load_scale_fraction * self._model_diagonal(), _LOAD_SIZE_MIN)

    def _restraint_size_cap(self) -> float:
        """The support-glyph maximum size: 500 mm in the model's length unit."""
        from .._unit_scaling import length_scale_factor

        units = getattr(self._model, "units", None) or {}
        return 0.5 * length_scale_factor(units)

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
            radius: Tube radius for frame highlights.  Defaults to
                :meth:`highlight_radius` -- a model-scaled width.

        Returns:
            ``self`` for chaining.
        """
        self._extract_geometry()

        if radius is None:
            radius = self.highlight_radius()
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

        self._backend.render_highlights([h], shrink=self._shrink)
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
        self._backend.render_highlights([h], shrink=self._shrink)
        return self

    def clear_highlights(self) -> "ModelViewer":
        """Remove highlights added by :meth:`highlight_elements` / :meth:`highlight_nodes`.

        Selection highlighting is re-issued on every change, so this is what
        keeps successive selections from stacking up in the scene.

        Returns:
            ``self`` for chaining.
        """
        self._backend.clear_highlights()
        return self

    # ── Annotation ───────────────────────────────────────────────────

    def clear_deformed(self) -> "ModelViewer":
        """Remove the deformed-shape overlay, leaving the model itself drawn.

        Returns:
            ``self`` for chaining.
        """
        self._backend.clear_category("deformed")
        return self

    def clear_forces(self) -> "ModelViewer":
        """Remove the force-flag overlay, leaving the model itself drawn.

        The sibling of :meth:`clear_deformed`: a flag diagram and a deformed
        shape are independent overlays, so toggling one off must not disturb
        the other.

        Returns:
            ``self`` for chaining.
        """
        self._backend.clear_category("force_flags")
        return self

    def clear_shell_forces(self) -> "ModelViewer":
        """Remove the shell scalar-field overlay, leaving the model itself drawn.

        The sibling of :meth:`clear_forces`: a shell overlay, a flag diagram and
        a deformed shape are independent, so toggling one off must not disturb
        the others.

        Returns:
            ``self`` for chaining.
        """
        self._backend.clear_category("shell_forces")
        return self

    def clear_results(self) -> "ModelViewer":
        """Remove every results overlay — the deformed shape, force flags and shells.

        The model, the node cloud and the annotations are untouched: this clears
        what a *results* action put there, which is what a "clear results"
        command should undo.

        Returns:
            ``self`` for chaining.
        """
        self.clear_deformed()
        self.clear_forces()
        self.clear_shell_forces()
        return self

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
                logger.warning("Warning: node %s not found.", node_id)
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
