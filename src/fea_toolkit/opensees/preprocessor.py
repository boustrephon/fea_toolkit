"""Model preprocessor — topology mutations producing a ``MeshModel``.

The :class:`Preprocessor` consumes a ``SAPModelData`` instance and produces a
:class:`~fea_toolkit.model.mesh_model.MeshModel` — a frozen, serialisable
topology with all splitting, meshing, subdividing, and constraint detection
already applied.  No OpenSees domain objects are created.
"""

import copy
import warnings
from dataclasses import fields
from typing import Any, Optional

from ..model.geometry import (
    apply_frame_end_offsets,
    convert_area_loads_to_edge_loads,
    derive_rigid_end_offsets,
    find_constraint_edges,
    find_wall_nodes_inside_slabs,
    mesh_area_elements,
    print_wall_inside_slab_report,
    split_elements,
    split_slabs_at_wall_intersections,
    subdivide_area_mesh,
)
from ..model.mesh_model import MeshModel, WallElement
from ..model.sap_data import (
    AreaElementProperties,
    FrameDistributedLoad,
    FrameElement,
    FrameElementProperties,
    FrameEndOffset,
    LayeredShellSection,
    NDMaterial,
    Node,
    SAPModelData,
    ShellFiberLayer,
    ShellSection,
)
from ..model.selection import Selection
from ..utils import (
    scale_material_dict,
    stress_scale_factor,
)


class Preprocessor:
    """Prepare model topology for OpenSees analysis.

    Performs all geometry mutations (splitting, meshing, subdividing,
    constraint detection) as pure data operations.  The output is a
    ``MeshModel`` that can be consumed by the
    :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
    or by the Tcl export path.

    Args:
        config: Configuration dict.  Shares a subset of keys with
            :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
            (runtime solver settings, element types), but also accepts
            preprocessing‑specific options that must be supplied **before**
            constructing the ``AnalysisBuilder``:
            ``detect_wall_slab_intersections`` (default ``True``),
            ``split_slabs_at_walls`` (default ``False``),
            ``rigid_end_zones`` (default ``False``) to auto-generate rigid
            joint-zone offsets (``rigid_offset_factor`` x the intersecting
            member's depth, default 0.5), ``rigid_offset_absolute`` for a
            fixed-length override, ``joint_extents`` to subtract explicit
            joint-element panel dimensions, and the two
            independent Z tolerances (in model length units):
            ``area_diaphragm_z_tolerance`` (default ``0.5``) for treating
            an area element as horizontal during storey-level detection,
            and ``diaphragm_z_tolerance`` (default ``0.01``) for matching
            nodes to a detected diaphragm elevation in the AnalysisBuilder.
            The latter is stored on the ``MeshModel`` as
            ``diaphragm_z_tolerance`` for the builder to consume.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or {}

    # ── Public API ────────────────────────────────────────────────

    def run(
        self,
        model_data: SAPModelData,
        load_shell_selection: Optional[Selection] = None,
    ) -> MeshModel:
        """Run the full topology preparation pipeline.

        Operates on a **copy** of *model_data* so the original is untouched.

        Args:
            model_data: Parsed SAP2000 model data.
            load_shell_selection: Optional :class:`Selection` designating loads‑only
                areas (no shell elements created for them).  Nodes
                referenced only by such areas are moved to
                ``MeshModel.orphan_nodes`` — they are kept for
                visualisation but not created in OpenSees.

        Returns:
            A fully prepared :class:`~fea_toolkit.model.mesh_model.MeshModel`.
        """
        # Work on a copy so the original SAPModelData is not mutated
        md = copy.deepcopy(model_data)

        # ── 1. Frame element classification (pre-split) ──────────
        # Always-on geometric classification: every non-inactive frame gets
        # a role (beam/column/brace) regardless of whether stiffness_factors
        # or element_strategies are configured.  The roles are stored on the
        # MeshModel and consumed by the builder (stiffness-factor section
        # variants, element-property role defaults, future design-role
        # detection).
        frame_element_types: dict[str, str] = {}
        for eid, elem in md.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            frame_element_types[eid] = self._classify_element_type(
                elem, is_area=False, nodes=md.nodes
            )

        # ── 2. Element splitting ─────────────────────────────────
        split_dist_loads: list[FrameDistributedLoad] = []
        if self.config.get("split_elements", True):
            new_elems, new_assigns, split_dist_loads = split_elements(
                md.nodes,
                md.frame_elements,
                md.frame_assignments,
                getattr(md, "frame_dist_loads", []),
                getattr(md, "frame_auto_mesh", {}),
                tol=1e-6,
                verbose=self.config.get("verbose", False),
            )
        else:
            new_elems = md.frame_elements
            new_assigns = md.frame_assignments
            split_dist_loads = getattr(md, "frame_dist_loads", [])

        # Propagate element types to split children
        for child_id, child_elem in new_elems.items():
            parent_id = getattr(child_elem, "parent_id", None)
            if parent_id and parent_id in frame_element_types:
                frame_element_types[child_id] = frame_element_types[parent_id]

        # ── 3. Frame end offsets ─────────────────────────────────
        offset_rigid_links: list[tuple] = []
        effective_offsets: dict[str, FrameEndOffset] = dict(
            getattr(md, "frame_end_offsets", {}) or {}
        )
        if self.config.get("rigid_end_zones", False):
            auto = derive_rigid_end_offsets(
                new_elems,
                new_assigns,
                md.nodes,
                md.sections,
                factor=float(self.config.get("rigid_offset_factor", 0.5)),
                absolute=self.config.get("rigid_offset_absolute"),
                joint_extents=self.config.get("joint_extents"),
                verbose=self.config.get("verbose", False),
            )
            # Explicit S2K offsets win over auto-derived values.
            auto.update(effective_offsets)
            effective_offsets = auto

        if effective_offsets:
            max_node_tag = max((n.node_tag for n in md.nodes.values()), default=0)
            new_elems, new_assigns, md.nodes, _next_tag, offset_rigid_links = (
                apply_frame_end_offsets(
                    new_elems,
                    new_assigns,
                    md.nodes,
                    effective_offsets,
                    next_tag=max_node_tag + 1,
                )
            )

        # ── 3b. Split areas at frame edges ────────────────────────
        # Ensures slab mesh nodes coincide with frame split points.
        # Only needed when shells are created — when create_shells=False
        # the areas are loads-only and their loads are converted to frame
        # edge loads in step 4.  Unconditional splitting would create
        # orphan _af_ nodes with no element connectivity → singular matrix.
        create_shells = self.config.get("create_shells", False)
        if create_shells and self.config.get("split_areas_at_frame_edges", True):
            from ..model.geometry import split_areas_at_frame_edges

            max_tag = max((ae.area_tag for ae in md.area_elements.values()), default=0)
            max_ntag = max((nd.node_tag for nd in md.nodes.values()), default=0)
            md.area_elements, md.area_assignments, md.nodes, _ = split_areas_at_frame_edges(
                md.area_elements,
                md.area_assignments,
                md.nodes,
                new_elems,
                next_tag=max(max_tag, max_ntag) + 1,
                groups=getattr(md, "groups", None),
            )

        # ── 4. Convert area loads to frame edge loads ────────────
        edge_loads_from_areas: list = []
        loads_only_area_ids: set[str] = set()
        # create_shells already fetched at step 3b, reuse
        if load_shell_selection is not None:
            edge_loads_from_areas = self._convert_area_loads(
                md,
                load_shell_selection,
                new_elems,
            )
            loads_only_area_ids = set(load_shell_selection.get_area_ids(md))
        elif not create_shells:
            # No shell mode + no selection → convert all area loads
            edge_loads_from_areas = self._convert_area_loads(
                md,
                load_shell_selection,
                new_elems,
            )
        # else: create_shells=True + selection=None → all areas become shells
        #       → no load conversion (handled by shell elements)

        # ── 5. Mesh area elements ─────────────────────────────────
        if create_shells:
            self._mesh_areas(md, selection=load_shell_selection)
            self._merge_coincident_nodes(md)

            # Split frames at shell mesh nodes.
            # Only splits frames whose root parent has AtJoints=True
            # in the original SAP2000 auto-mesh data.
            if self.config.get("split_frames_at_shell_nodes", True):
                _before = len(new_elems)
                (
                    new_elems,
                    new_assigns,
                    split_dist_loads,
                    frame_element_types,
                    edge_loads_from_areas,
                ) = self._split_frames_at_shell_subdiv(
                    md,
                    new_elems,
                    new_assigns,
                    split_dist_loads,
                    frame_element_types,
                    edge_loads=edge_loads_from_areas,
                    exclude_area_ids=loads_only_area_ids,
                )
                _after = len(new_elems)
                if _after > _before:
                    print(
                        f"    _split_frames_at_shell_subdiv: "
                        f"split {_after - _before} new frame(s) "
                        f"({_before} → {_after})"
                    )
                self._merge_coincident_nodes(md)

            # N×N shell subdivision (opt-in, additional refinement)
            self._subdivide_shells_in_model_data(md)

            if self.config.get("subdivide_shells", 0):
                # Second round of frame splitting for sub-division nodes
                (
                    new_elems,
                    new_assigns,
                    split_dist_loads,
                    frame_element_types,
                    edge_loads_from_areas,
                ) = self._split_frames_at_shell_subdiv(
                    md,
                    new_elems,
                    new_assigns,
                    split_dist_loads,
                    frame_element_types,
                    edge_loads=edge_loads_from_areas,
                    exclude_area_ids=loads_only_area_ids,
                )
                self._merge_coincident_nodes(md)

            # Classify areas after meshing (always-on geometric role)
            area_element_types: dict[str, str] = {}
            for aid, area in md.area_elements.items():
                if getattr(area, "inactive", False):
                    continue
                area_element_types[aid] = self._classify_element_type(
                    area, is_area=True, nodes=md.nodes
                )
        else:
            area_element_types = {}

        # ── 6. Detect constraint edges (opt-in diagnostic) ──────
        raw_edges = []
        if self.config.get("detect_constraint_edges", False):
            raw_edges = self._detect_constraint_edges(md, new_elems, new_assigns)

        # ── 7. Detect diaphragm levels ───────────────────────────
        # Returns (sorted Z elevations, per-constraint node groups).
        # The per-group components preserve S2K constraint identity so
        # independent diaphragms at the same elevation stay separate.
        diaphragm_levels, diaphragm_components = self._detect_diaphragm_levels(md)

        # ── 8. Build MeshModel ───────────────────────────────────
        base_z = min((nd.z for nd in md.nodes.values()), default=None)

        # Pre-assign material and section tags so AnalysisBuilder
        # does not need to auto-assign them (deterministic recreation).
        material_tags: dict[str, int] = {}
        for i, mat_name in enumerate(md.materials.keys(), start=1):
            material_tags[mat_name] = i

        section_tags: dict[str, int] = {}
        for i, sec_name in enumerate(md.sections.keys(), start=len(material_tags) + 1):
            section_tags[sec_name] = i

        # ── 8b. Create type-specific section variants (stiffness factors) ──
        # For each (section, element_type) pair where the type factor != 1.0,
        # create a variant section in md.sections with a modified material E_mod.
        # The AnalysisBuilder._add_beam_column looks for variant_key in section_tags.
        _sf = self.config.get("stiffness_factors")
        if _sf and frame_element_types:
            # Determine which types have non-unity factors
            variant_types = {
                etype
                for etype in ("beam", "column", "brace", "wall", "slab")
                if _sf.get(etype, 1.0) != 1.0
            }
            # Collect which sections need which variant
            _needed: dict[str, set] = {}  # sec_name → set of etypes
            for eid, etype in frame_element_types.items():
                if etype in variant_types:
                    sec_name = new_assigns.get(eid, "")
                    if sec_name and sec_name in md.sections:
                        _needed.setdefault(sec_name, set()).add(etype)
            next_mat_tag = max(material_tags.values(), default=0) + 1
            next_sec_tag = max(section_tags.values(), default=0) + 1
            for sec_name, etypes in _needed.items():
                base_sec = md.sections[sec_name]
                for etype in sorted(etypes):
                    variant_sec_name = f"{sec_name}__{etype}"
                    if variant_sec_name in section_tags:
                        continue
                    # Clone the material with scaled E_mod
                    # Per ACI 318, cracked-section stiffness factors only
                    # apply to concrete materials.  Composite/steel sections
                    # are left at full gross stiffness.
                    base_mat_name = base_sec.material
                    base_mat = md.materials.get(base_mat_name)
                    if base_mat is None or base_mat.type.lower() != "concrete":
                        if self.config.get("verbose", False):
                            print(
                                f"  ⚠ Section '{sec_name}' material "
                                f"'{base_mat_name}' is not concrete — "
                                f"skipping stiffness factor for {etype}"
                            )
                        continue
                    variant_mat_name = f"{base_mat_name}__{etype}"
                    if variant_mat_name not in material_tags:
                        factor = _sf.get(etype, 1.0)
                        from copy import deepcopy

                        var_mat = deepcopy(base_mat)
                        if var_mat.E_mod:
                            var_mat.E_mod *= factor
                        if var_mat.G_mod:
                            var_mat.G_mod *= factor
                        md.materials[variant_mat_name] = var_mat
                        material_tags[variant_mat_name] = next_mat_tag
                        next_mat_tag += 1
                    # Clone the section pointing to the modified material
                    var_sec = deepcopy(base_sec)
                    var_sec.material = variant_mat_name
                    var_sec.name = variant_sec_name
                    md.sections[variant_sec_name] = var_sec
                    section_tags[variant_sec_name] = next_sec_tag
                    next_sec_tag += 1

        # ── 9. Remove orphan nodes (not referenced by any element) ──
        # Nodes that carry joint loads are retained even when they are not
        # referenced by any frame/area element — the MASS SOURCE → JOINT
        # LOADS flow lumps mass onto those nodes, so dropping them would
        # silently discard all seismic mass (and the eigen problem would
        # have no mass).
        referenced: set[str] = set()
        for fe in new_elems.values():
            if not getattr(fe, "inactive", False):
                referenced.add(fe.node_i)
                referenced.add(fe.node_j)
        for aid, ae in md.area_elements.items():
            if getattr(ae, "inactive", False):
                continue
            if aid in loads_only_area_ids:
                continue  # these areas have no shells, skip their nodes
            referenced.update(ae.node_ids)
        for jl in getattr(md, "joint_loads", []):
            referenced.add(str(jl.node_id))
        # Joint nodes referenced only by the rigid links (from frame end
        # offsets) must be retained — the links connect the shortened
        # member ends back to the original joint node, so dropping them
        # would leave those nodes (and their slaves) unconnected.
        for _lid, _n_i, _n_j, _tag in offset_rigid_links:
            referenced.add(_n_i)
            referenced.add(_n_j)
        orphan_nodes: dict[str, Node] = {}
        for nid in list(md.nodes.keys()):
            if nid not in referenced:
                orphan_nodes[nid] = md.nodes.pop(nid)
        if orphan_nodes:
            pass  # kept for visualisation

        # ── 10. Resolve per-element creation properties ────────────
        # Build the MeshModel first, then resolve properties directly
        # onto it using the mutated model_data (md) which contains
        # derived elements (split/meshed children).
        mesh_model = MeshModel(
            nodes=md.nodes,
            frame_elements=new_elems,
            frame_assignments=new_assigns,
            area_elements=md.area_elements,
            area_assignments=md.area_assignments,
            frame_dist_loads=split_dist_loads,
            edge_loads_from_areas=edge_loads_from_areas,
            detected_edge_pairs=raw_edges,
            diaphragm_levels=diaphragm_levels,
            diaphragm_components=diaphragm_components,
            offset_rigid_links=offset_rigid_links,
            frame_element_types=frame_element_types,
            area_element_types=area_element_types,
            diaphragm_z_tolerance=float(self.config.get("diaphragm_z_tolerance", 0.01)),
            materials=md.materials,
            sections=md.sections,
            groups=getattr(md, "groups", {}),
            restraints=md.restraints,
            base_z=base_z,
            units=md.units,
            model_name=getattr(md, "name", ""),
            material_tags=material_tags,
            section_tags=section_tags,
            loads_only_area_ids=loads_only_area_ids,
            orphan_nodes=orphan_nodes,
            # Pass through load collections for AnalysisBuilder consumption
            joint_loads=getattr(md, "joint_loads", []),
            frame_gravity_loads=getattr(md, "frame_gravity_loads", []),
            area_gravity_loads=getattr(md, "area_gravity_loads", []),
            area_uniform_loads=getattr(md, "area_uniform_loads", []),
            load_patterns=getattr(md, "load_patterns", {}),
            mass_sources=getattr(md, "mass_sources", {}),
        )
        # Resolve against the mutated md so derived-element IDs
        # (from splitting/meshing) can be matched by selections.
        self._resolve_element_properties(mesh_model, md)

        return mesh_model

    # ── Internal helpers ──────────────────────────────────────────

    def _classify_element_type(
        self, elem, is_area: bool = False, nodes: Optional[dict[str, Node]] = None
    ) -> str:
        """Classify a frame or area element into a structural type.

        This is the **geometry signal** for element classification.  The
        companion **section-type signal** is
        ``Selection.from_brace_sections()``.  Both are documented in
        ``docs/element_classification.md``.

        Classification is **always-on**: it runs for every non-inactive
        element at preprocess time (not only when ``stiffness_factors`` is
        configured) and the result is stored on the ``MeshModel`` as
        ``frame_element_types`` / ``area_element_types``.

        The thresholds are configurable via the flat ``config`` dict
        (defaults below preserve the historical behaviour):

        * ``column_vertical_ratio`` (default ``4.0``) — column iff the
          vertical span ``dz`` exceeds ``ratio`` times the horizontal span
          ``dh``.  Equivalent to ``atan(1/4) ≈ 14°`` from vertical.
        * ``classification_span_floor`` (default ``0.01``) — length-unit
          floor applied to ``dh`` in the column rule and to both spans in
          the brace rule (guards zero-length spans).
        * ``slab_z_tolerance`` (default ``0.02``) — area horizontality
          tolerance for the slab-vs-wall decision.

        Classification rules
        --------------------
        **Area elements:**
          ``'slab'`` if all corner Z-coordinates are within
          ``slab_z_tolerance`` length units of each other (horizontal);
          ``'wall'`` otherwise.

        **Frame elements (geometry-based):**
          * ``'column'`` if the vertical span (dz) exceeds
            ``column_vertical_ratio`` times the horizontal span
            (√(dx² + dy²)).  Near-vertical.
          * ``'brace'`` if BOTH horizontal span and vertical span exceed
            ``classification_span_floor`` length units (diagonal).
          * ``'beam'`` otherwise (horizontal or near-horizontal).

        The geometry-only ``'brace'`` label is a **hint** — the final
        brace decision for pushover also requires the section to be a
        recognised brace shape (Pipe, Angle, etc.).  This two-signal
        approach avoids false positives (e.g. a horizontal pipe handrail
        classified as a brace).  The explicit, overridable **design role**
        that ultimately drives modelling is Phase 2 (see
        ``docs/element_classification.md``).

        Args:
            elem: A ``FrameElement`` or ``AreaElement``.
            is_area: ``True`` for area elements, ``False`` for frames.
            nodes: Node dict (required for frame elements to resolve
                element node IDs to coordinates).

        Returns:
            One of ``'beam'``, ``'column'``, ``'brace'``, ``'slab'``,
            ``'wall'``, or ``'unknown'``.
        """
        if nodes is None:
            nodes = {}

        column_ratio = float(self.config.get("column_vertical_ratio", 4.0))
        span_floor = float(self.config.get("classification_span_floor", 0.01))
        slab_tol = float(self.config.get("slab_z_tolerance", 0.02))

        if is_area:
            zs = []
            for nid in elem.node_ids:
                nd = nodes.get(nid)
                if nd is None:
                    return "unknown"
                zs.append(nd.z)
            return "slab" if max(zs) - min(zs) < slab_tol else "wall"

        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            return "unknown"

        dz = abs(ni.z - nj.z)
        dx = abs(ni.x - nj.x)
        dy = abs(ni.y - nj.y)
        dh = (dx**2 + dy**2) ** 0.5

        if dz > column_ratio * max(dh, span_floor):
            return "column"
        if dh > span_floor and dz > span_floor:
            return "brace"
        return "beam"

    def _convert_area_loads(self, md, selection, frame_elements) -> list:
        """Convert area uniform loads to frame edge loads."""
        sel_area_ids: set[str] = set()
        if selection is not None:
            sel_area_ids = set(selection.get_area_ids(md))

        area_loads = getattr(md, "area_uniform_loads", [])
        if selection is not None:
            area_loads = [ld for ld in area_loads if ld.area_id in sel_area_ids]
        if not area_loads:
            return []

        # When selection is provided, only include matching areas;
        # otherwise include all area elements referenced by the loads.
        load_area_ids = {ld.area_id for ld in area_loads}
        if selection is not None:
            area_filter = {aid: ae for aid, ae in md.area_elements.items() if aid in sel_area_ids}
        else:
            area_filter = {aid: ae for aid, ae in md.area_elements.items() if aid in load_area_ids}

        return convert_area_loads_to_edge_loads(
            md.nodes,
            area_filter,
            frame_elements,
            area_loads,
        )

    def _mesh_areas(self, md, selection=None):
        """Subdivide area elements per AREA MESH ASSIGNMENTS.

        Also handles wall-slab intersection detection and optional
        splitting (mirrors the legacy builder's behaviour).
        """
        # ── Wall-slab intersection detection ─────────────────────
        if self.config.get("detect_wall_slab_intersections", True):
            ws_findings = find_wall_nodes_inside_slabs(
                md.area_elements,
                md.area_assignments,
                md.nodes,
            )
            if ws_findings:
                print_wall_inside_slab_report(ws_findings)

                # Optional auto-split
                if self.config.get("split_slabs_at_walls", False):
                    max_tag = max(
                        (ae.area_tag for ae in md.area_elements.values()),
                        default=0,
                    )
                    max_ntag = max(
                        (nd.node_tag for nd in md.nodes.values()),
                        default=0,
                    )
                    next_tag = max(max_tag, max_ntag) + 1
                    areas_ws, assign_ws, nodes_ws, _ = split_slabs_at_wall_intersections(
                        md.area_elements,
                        md.area_assignments,
                        md.nodes,
                        next_tag=next_tag,
                        groups=getattr(md, "groups", None),
                    )
                    md.area_elements = areas_ws
                    md.area_assignments = assign_ws
                    md.nodes = nodes_ws
                    print(f"    → Split at {len(ws_findings)} wall intersection(s)")

        # ── Area meshing ─────────────────────────────────────────
        area_mesh = getattr(md, "area_mesh", {})
        if not area_mesh:
            return

        # Exclude loads-only areas
        if selection is not None:
            loads_only = set(selection.get_area_ids(md))
            mesh_filtered = {aid: m for aid, m in area_mesh.items() if aid not in loads_only}
        else:
            mesh_filtered = area_mesh

        if not mesh_filtered:
            return

        max_elem_tag = max((ae.area_tag for ae in md.area_elements.values()), default=0)
        max_node_tag = max((nd.node_tag for nd in md.nodes.values()), default=0)
        next_tag = max(max_elem_tag, max_node_tag) + 1

        areas, assignments, nodes, _ = mesh_area_elements(
            md.area_elements,
            md.area_assignments,
            md.nodes,
            mesh_filtered,
            next_tag=next_tag,
            restraints=getattr(md, "restraints", None),
        )
        md.area_elements = areas
        md.area_assignments = assignments
        md.nodes = nodes
        # Clear so builder's _mesh_areas doesn't re-mesh
        md.area_mesh = {}

    def _merge_coincident_nodes(self, md):
        """Merge mesh-created nodes at the same coordinates.

        Pure data operation — no OpenSees calls.

        Merges mesh nodes into existing protected (original SAP / frame)
        nodes at the same coordinates, as well as deduplicating mesh
        nodes among themselves.
        """
        from collections import defaultdict

        mesh_nodes = {nid: nd for nid, nd in md.nodes.items() if "_mesh_" in nid}
        if not mesh_nodes:
            return

        # Protected nodes: original SAP nodes + frame element nodes
        protected: set = set()
        for fid, fe in md.frame_elements.items():
            if not getattr(fe, "inactive", False):
                protected.add(fe.node_i)
                protected.add(fe.node_j)
        for ae in md.area_elements.values():
            for nid in ae.node_ids:
                if "_mesh_" not in nid:
                    protected.add(nid)
        for eid, offset in getattr(md, "frame_end_offsets", {}).items():
            if hasattr(offset, "node_i") and offset.node_i:
                protected.add(offset.node_i)
            if hasattr(offset, "node_j") and offset.node_j:
                protected.add(offset.node_j)

        # Group ALL nodes by rounded coordinates (not just mesh nodes)
        coord_map: dict[str, list[str]] = defaultdict(list)
        for nid, nd in md.nodes.items():
            key = f"{nd.x:.4f}_{nd.y:.4f}_{nd.z:.4f}"
            coord_map[key].append(nid)

        remap: dict[str, str] = {}
        for key, ids in coord_map.items():
            if len(ids) < 2:
                continue
            # Prefer a protected node as survivor so mesh nodes
            # merge into original SAP / frame element nodes.
            survivor = None
            for nid in ids:
                if nid in protected:
                    survivor = nid
                    break
            if survivor is None:
                # No protected node — pick first unprotected
                for nid in ids:
                    if nid not in protected:
                        survivor = nid
                        break
            if survivor is None:
                survivor = ids[0]
            for dup in ids:
                if dup == survivor:
                    continue
                if dup in protected:
                    continue  # never remap a protected node
                remap[dup] = survivor

        if not remap:
            return

        # Remap area element node references
        for ae in md.area_elements.values():
            ae.node_ids = [remap.get(nid, nid) for nid in ae.node_ids]

        # Remove duplicate nodes
        for dup in remap:
            md.nodes.pop(dup, None)

    def _subdivide_shells_in_model_data(self, md):
        """N×N subdivision of shell elements at the model-data level."""
        subdivide_cfg = self.config.get("subdivide_shells", 0)
        if not subdivide_cfg:
            return

        n = subdivide_cfg if isinstance(subdivide_cfg, int) else subdivide_cfg.get("n", 2)
        if n < 2:
            return

        # Determine which areas to subdivide
        area_ids = set(md.area_elements.keys())
        if isinstance(subdivide_cfg, dict) and "selection" in subdivide_cfg:
            sel = subdivide_cfg["selection"]
            if sel is not None:
                area_ids = set(sel.get_area_ids(md))

        for sid in sorted(area_ids, key=lambda x: (0, int(x)) if x.isdigit() else (1, x)):
            ae = md.area_elements.get(sid)
            if ae is None or getattr(ae, "inactive", False):
                continue
            if len(ae.node_ids) < 4:
                continue

            max_tag = max((nd.node_tag for nd in md.nodes.values()), default=0)
            next_tag = max_tag + 1

            sub_areas, sub_assigns, sub_nodes, _ = subdivide_area_mesh(
                {sid: ae},
                {sid: md.area_assignments.get(sid, "")},
                md.nodes,
                n=n,
                next_tag=next_tag,
                groups=md.groups if hasattr(md, "groups") else {},
                restraints=getattr(md, "restraints", None),
            )
            md.area_elements.update(sub_areas)
            md.area_assignments.update(sub_assigns)
            md.nodes.update(sub_nodes)

    def _split_frames_at_shell_subdiv(
        self,
        md,
        frame_elements,
        frame_assignments,
        dist_loads,
        frame_element_types,
        edge_loads=None,
        exclude_area_ids=None,
    ):
        """Split frame elements at shell subdivision edge nodes.
        For each frame element whose root parent has ``AtJoints=True``
        in the original SAP2000 ``frame_auto_mesh`` data, checks whether
        any shell mesh node lies on the frame segment.  If so, the frame
        is split into sub-segments.

        Uses a :class:`~fea_toolkit.model.geometry.SpatialGrid` (cell
        size auto-sized to about 1 percent of model extent) for fast broad-phase
        pre-filtering — only candidates whose cell overlaps the frame's
        bounding box are passed to the ``point_on_segment`` check.

        Args:
            md : ModelData
                Model data container with ``area_elements``, ``nodes``.
            frame_elements : dict
                ``{elem_id: FrameElement}`` — may be extended with new
                child elements.
            frame_assignments : dict
                ``{elem_id: section_name}``.
            dist_loads : list
                Distributed loads to redistribute.
            frame_element_types : dict
                ``{elem_id: element_type_str}``.
            edge_loads : list, optional
                Edge loads (from area-to-frame conversion) to redistribute
                to split children.  If ``None``, treated as empty list.
            exclude_area_ids : set, optional
                Set of area IDs whose nodes should be excluded from
                frame-splitting consideration.  Typically loads-only
                areas (e.g. brick walls) that won't produce shell
                elements in OpenSees — splitting frames at their
                sub-nodes would create unnecessary intermediate nodes
                with no shell connection.

        Returns
        -------
        tuple
            ``(new_elements, new_assignments, new_dist_loads,
            frame_element_types, new_edge_loads)``.
        """

        from ..model.geometry import SpatialGrid, compute_t_location, point_on_segment

        # Build a lookup of root parent → AtJoints flag
        auto_mesh = getattr(md, "frame_auto_mesh", {})
        _at_joints: dict[str, bool] = {}
        for eid in frame_elements:
            # Root parent is the original SAP ID (before any "-" suffixes)
            root_id = eid.split("-")[0]
            if root_id not in _at_joints:
                am = auto_mesh.get(root_id, {})
                _at_joints[root_id] = am.get("AtJoints", False) if isinstance(am, dict) else False

        # Collect all sub-node coordinates from active area elements,
        # excluding loads-only areas that won't produce shells.
        exclude_ids = exclude_area_ids or set()
        sub_nodes: dict[str, Node] = {}
        for ae in md.area_elements.values():
            if getattr(ae, "inactive", False):
                continue
            if ae.area_id in exclude_ids:
                continue
            for nid in ae.node_ids:
                nd = md.nodes.get(nid)
                if nd is not None:
                    sub_nodes[nid] = nd

        if not sub_nodes:
            return (
                frame_elements,
                frame_assignments,
                dist_loads,
                frame_element_types,
                (edge_loads or []),
            )

        # Build spatial grid of sub-nodes
        sub_coords: dict[str, tuple] = {nid: (nd.x, nd.y, nd.z) for nid, nd in sub_nodes.items()}
        # Estimate grid cell size as 1% of model extent
        xs = [c[0] for c in sub_coords.values()]
        ys = [c[1] for c in sub_coords.values()]
        zs = [c[2] for c in sub_coords.values()]
        extent_x = max(xs) - min(xs)
        extent_y = max(ys) - min(ys)
        extent_z = max(zs) - min(zs)
        cell_size = max(1.0, (extent_x + extent_y + extent_z) / 300.0)
        grid = SpatialGrid(cell_size)
        for snid, scoord in sub_coords.items():
            grid.add_point(snid, scoord)

        new_frames: dict[str, FrameElement] = {}
        new_assigns: dict[str, str] = {}
        new_loads: list = []
        new_edge_loads: list = []
        _edge_loads_in = edge_loads or []
        # Track which frame IDs were split (to exclude original loads)
        _split_frame_ids: set = set()
        # Track new child types
        updated_types: dict[str, str] = dict(frame_element_types)

        _split_count = 0
        _at_joints_count = sum(1 for v in _at_joints.values() if v)

        for eid, elem in frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            # Skip frames whose root parent doesn't have AtJoints=True
            root_id = eid.split("-")[0]
            if not _at_joints.get(root_id, False):
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, "")
                continue

            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, "")
                continue

            p1 = (ni.x, ni.y, ni.z)
            p2 = (nj.x, nj.y, nj.z)

            # Find sub-nodes on this segment using spatial grid pre-filter
            tol = 1e-4
            mins = (min(p1[0], p2[0]) - tol, min(p1[1], p2[1]) - tol, min(p1[2], p2[2]) - tol)
            maxs = (max(p1[0], p2[0]) + tol, max(p1[1], p2[1]) + tol, max(p1[2], p2[2]) + tol)
            candidates = grid.points_in_bbox(mins, maxs)

            t_values: list[float] = []
            for snid, scoord in candidates:
                if snid in (elem.node_i, elem.node_j):
                    continue
                if point_on_segment(scoord, p1, p2, tol=tol):
                    t = compute_t_location(scoord, p1, p2)
                    if 0 < t < 1:
                        t_values.append((t, snid))

            if not t_values:
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, "")
                continue

            _split_count += 1

            t_values.sort(key=lambda x: x[0])

            # Collect child element IDs (sequential numbering)
            child_ids: list[str] = []
            prev_nid = elem.node_i
            seg_idx = 0
            for t, snid in t_values:
                child_id = f"{eid}-{seg_idx}"
                seg_idx += 1
                child_ids.append(child_id)
                new_frames[child_id] = FrameElement(
                    elem_id=child_id,
                    elem_tag=elem.elem_tag,  # will be assigned by tag map
                    node_i=prev_nid,
                    node_j=snid,
                    angle=elem.angle,
                    inactive=False,
                    parent_id=eid,
                )
                new_assigns[child_id] = frame_assignments.get(eid, "")
                if eid in updated_types:
                    updated_types[child_id] = updated_types[eid]
                prev_nid = snid

            # Last segment → original end node
            child_id = f"{eid}-{seg_idx}"
            child_ids.append(child_id)
            new_frames[child_id] = FrameElement(
                elem_id=child_id,
                elem_tag=elem.elem_tag,
                node_i=prev_nid,
                node_j=elem.node_j,
                angle=elem.angle,
                inactive=False,
                parent_id=eid,
                child_ids=child_ids,
            )
            new_assigns[child_id] = frame_assignments.get(eid, "")
            if eid in updated_types:
                updated_types[child_id] = updated_types[eid]

            # Compute per-child parametric boundaries from split-node positions
            boundaries = [0.0] + [t for t, _ in t_values] + [1.0]

            # Redistribute distributed loads
            elem_loads = [ld for ld in dist_loads if ld.frame_id == eid]
            for ld in elem_loads:
                load_span = ld.rdist_b - ld.rdist_a
                for i, cid in enumerate(child_ids):
                    a_local = boundaries[i]
                    b_local = boundaries[i + 1]
                    new_ld = FrameDistributedLoad(
                        frame_id=cid,
                        pattern=ld.pattern,
                        direction=ld.direction,
                        load_type=ld.load_type,
                        shape=ld.shape,
                        val_a=ld.val_a,
                        val_b=ld.val_b,
                        rdist_a=max(0.0, ld.rdist_a + a_local * load_span),
                        rdist_b=min(1.0, ld.rdist_a + b_local * load_span),
                        dist_a=0.0,
                        dist_b=0.0,
                        coord_sys=ld.coord_sys,
                    )
                    new_loads.append(new_ld)

            # Redistribute edge loads (from area-to-frame conversion)
            elem_edge_loads = [ld for ld in _edge_loads_in if ld.frame_id == eid]
            for ld in elem_edge_loads:
                load_span = ld.rdist_b - ld.rdist_a
                for i, cid in enumerate(child_ids):
                    a_local = boundaries[i]
                    b_local = boundaries[i + 1]
                    new_el = FrameDistributedLoad(
                        frame_id=cid,
                        pattern=ld.pattern,
                        direction=ld.direction,
                        load_type=ld.load_type,
                        shape=ld.shape,
                        val_a=ld.val_a,
                        val_b=ld.val_b,
                        rdist_a=max(0.0, ld.rdist_a + a_local * load_span),
                        rdist_b=min(1.0, ld.rdist_a + b_local * load_span),
                        dist_a=0.0,
                        dist_b=0.0,
                        coord_sys=ld.coord_sys,
                    )
                    new_edge_loads.append(new_el)

            # Mark original as inactive
            elem.inactive = True
            _split_frame_ids.add(eid)

        # Include non-split frames and remaining loads
        for eid, elem in frame_elements.items():
            if not getattr(elem, "inactive", False):
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, "")
        # Append every source load whose frame was not split.  Multiple
        # load records for the same unsplit frame (different patterns,
        # directions, or spans) are all preserved — no frame_id-based
        # deduplication.
        for ld in dist_loads:
            if ld.frame_id in _split_frame_ids:
                continue  # already redistributed to children
            new_loads.append(ld)
        for ld in _edge_loads_in:
            if ld.frame_id in _split_frame_ids:
                continue  # already redistributed to children
            new_edge_loads.append(ld)

        _mesh_ids = len([n for n in sub_nodes if "_mesh_" in n])
        print(
            f"    [_split_frames_at_shell_subdiv] "
            f"{len(sub_nodes)} total sub-nodes ({_mesh_ids} mesh-created), "
            f"{_at_joints_count} frames with AtJoints=True, "
            f"{_split_count} frames split into children",
            flush=True,
        )
        return new_frames, new_assigns, new_loads, updated_types, new_edge_loads

    def _detect_constraint_edges(self, md, frame_elements, frame_assignments) -> list:
        """Detect constraint edges where coarse and fine meshes meet."""
        verbose = self.config.get("verbose", False)
        exclude_types = self.config.get("exclude_types", frozenset({"brick"}))
        raw_edges = find_constraint_edges(
            md.area_elements,
            md.area_assignments,
            md.nodes,
            frame_elements=frame_elements,
            frame_assignments=frame_assignments,
            exclude_types=exclude_types,
            verbose=verbose,
        )
        return raw_edges

    def _detect_diaphragm_levels(self, md) -> tuple[list[float], list[tuple[float, list[str]]]]:
        """Detect storey levels and per-group diaphragm components.

        Four sources feed this method, selected by the ``rigid_diaphragms``
        config value (see :meth:`_apply_rigid_diaphragms` in the
        AnalysisBuilder for the full decision tree):

        1. **Explicit groups** (``rigid_diaphragms: [ {name, nodes|selection},
           ... ]``) — each dict defines one named diaphragm group.  A
           ``nodes`` list is used verbatim; a ``selection`` key is resolved
           against the model data — matching **area** elements contribute
           their vertex nodes, matching **frame** elements contribute their
           two end nodes, and the union becomes the group.  This bypasses
           all detection.
        2. **Joint constraints** — Z-axis DIAPHRAGM constraints parsed from
           the ``.s2k`` file (``CONSTRAINT DEFINITIONS - DIAPHRAGM`` +
           ``JOINT CONSTRAINT ASSIGNMENTS``).  For each Z-axis diaphragm,
           the storey elevation is the rounded mean Z of its assigned
           joints.  This is the canonical source for frame-only models
           that carry explicit diaphragm definitions (e.g. the
           SeismoStruct Ex12 verification model).
        3. **Horizontal area elements** — each nearly-horizontal shell's
           mean Z (backward-compatible fallback for models without
           explicit constraints, e.g. slab-only models).
        4. **Storey detection** (``rigid_diaphragms: True``) — force
           :func:`fea_toolkit.model.stories.identify_stories` even when the
           model carries explicit S2K diaphragm constraints.  The storey
           node clusters become the diaphragm components.

        Returns
        -------
        tuple
            ``(levels, components)`` where *levels* is the sorted list of
            Z elevations with horizontal diaphragm behaviour, and
            *components* is a list of ``(mean_z, [joint_id, ...])`` tuples
            — one per explicit Z-axis DIAPHRAGM constraint, preserving the
            S2K constraint grouping so independent diaphragms at the same
            elevation are not merged.  *components* is empty when no
            explicit constraints are present (area-only fallback).
        """
        levels: set[float] = set()
        z_tol = float(self.config.get("area_diaphragm_z_tolerance", 0.5))
        components: list[tuple[float, list[str]]] = []

        config_val = self.config.get("rigid_diaphragms", None)

        # ── Source 1: explicit named groups from config ────────────
        # A list-of-dicts means the user bypasses all detection and
        # supplies the exact diaphragm groups they want.
        explicit_groups = self._resolve_explicit_diaphragm_groups(md)
        if explicit_groups is not None:
            for z, node_ids in explicit_groups:
                if not node_ids:
                    continue
                levels.add(round(z, 4))
                components.append((round(z, 4), node_ids))
            components.sort(key=lambda item: item[0])
            return sorted(levels), components

        # ── Source 2: explicit Z-axis diaphragm constraints ────────
        # Skip Path A when `rigid_diaphragms: True` forces storey-based
        # detection (Path B) instead.
        constraints = getattr(md, "constraints", {})
        assignments = getattr(md, "constraint_assignments", {})
        if config_val is not True:
            for cname, con in constraints.items():
                if con.constraint_type != "DIAPHRAGM":
                    continue
                axis = str(con.constraint_data.get("Axis", "")).upper()
                if axis and axis != "Z":
                    continue
                # Group assigned joints for this constraint
                joint_ids = [jid for jid, c in assignments.items() if c == cname]
                zs = []
                for jid in joint_ids:
                    nd = md.nodes.get(jid)
                    if nd is not None:
                        zs.append(nd.z)
                if not zs:
                    continue
                z_mean = round(sum(zs) / len(zs), 4)
                # Keep only joints that survive topology preprocessing.
                surviving = [jid for jid in joint_ids if jid in md.nodes]
                components.append((z_mean, surviving))
                if len(surviving) >= 2:
                    levels.add(z_mean)
                else:
                    # A diaphragm needs at least two surviving joints; if the
                    # topology pass removed too many, drop the component so the
                    # horizontal-area fallback (Source 3 below) can still run.
                    components.pop()
                    levels.discard(z_mean)

        # ── Source 3: horizontal area elements ────────────────────
        if not components:
            for ae in md.area_elements.values():
                if getattr(ae, "inactive", False):
                    continue
                zs = []
                for nid in ae.node_ids:
                    nd = md.nodes.get(nid)
                    if nd is not None:
                        zs.append(nd.z)
                if not zs:
                    continue
                z_span = max(zs) - min(zs)
                if z_span <= z_tol:
                    levels.add(round(sum(zs) / len(zs), 4))

        # ── Source 4: forced storey-based detection (Path B) ──────
        # `rigid_diaphragms: True` skips S2K constraints and instead
        # derives one diaphragm component per identified storey.
        if config_val is True and not components:
            components = self._storey_diaphragm_components(md)
            for z, _ids in components:
                levels.add(round(z, 4))

        # Deterministic ordering: sort components by elevation.
        components.sort(key=lambda item: item[0])
        return sorted(levels), components

    def _resolve_explicit_diaphragm_groups(self, md) -> Optional[list[tuple[float, list[str]]]]:
        """Resolve the ``rigid_diaphragms: [ {name, nodes|selection}, ... ]``
        config form into ``(mean_z, node_ids)`` components.

        Returns ``None`` when the config value is not a list-of-dicts (so
        the caller falls through to normal detection).  Node-based groups
        use the supplied ID list verbatim (filtered to nodes that survive
        preprocessing).  Selection-based groups collect the vertex nodes of
        every matching **area** element and both end nodes of every
        matching **frame** element; the union is the group.

        The group's elevation is the mean Z of its resolved nodes, since
        the builder emits a single ``rigidDiaphragm`` per component and
        needs a representative Z for sorting / diagnostics.
        """
        config_val = self.config.get("rigid_diaphragms", None)
        if not isinstance(config_val, list) or not config_val:
            return None
        if not all(isinstance(item, dict) for item in config_val):
            return None  # legacy [z1, z2, ...] float list

        components: list[tuple[float, list[str]]] = []
        for group_dict in config_val:
            if not isinstance(group_dict, dict):
                continue
            name = group_dict.get("name", "")
            if "nodes" in group_dict:
                node_ids = [str(nid) for nid in group_dict["nodes"]]
                node_ids = [nid for nid in node_ids if nid in md.nodes]
            elif "selection" in group_dict:
                sel = group_dict["selection"]
                node_ids = self._nodes_from_selection(md, sel)
            else:
                raise ValueError(
                    "rigid_diaphragms explicit group must contain either "
                    f"'nodes' or 'selection' keys — got: {sorted(group_dict)}"
                )
            node_ids = list(dict.fromkeys(node_ids))  # dedupe, keep order
            zs = []
            for nid in node_ids:
                nd = md.nodes.get(nid)
                if nd is not None:
                    zs.append(nd.z)
            z_mean = round(sum(zs) / len(zs), 4) if zs else 0.0
            if self.config.get("verbose", False):
                print(
                    f"  [diaphragm] explicit group '{name}': {len(node_ids)} node(s) @ z={z_mean}"
                )
            components.append((z_mean, node_ids))
        return components

    def _nodes_from_selection(self, md, sel) -> list[str]:
        """Collect node IDs from frame/area elements matching a Selection.

        Matching **frame** elements contribute ``node_i`` and ``node_j``;
        matching **area** elements contribute all vertex ``node_ids``.  The
        union (in first-seen order) is returned.  The selection is resolved
        against ``md`` — the (mutated) model data used for preprocessing —
        so freshly-split/meshed elements are included.
        """
        if not isinstance(sel, Selection):
            # Accept raw dict selectors too — mirror frame_groups config.
            sel = Selection(
                element_types=sel.get("element_types"),
                sections=sel.get("sections") or sel.get("section_name"),
                materials=sel.get("materials"),
                groups=sel.get("groups"),
                element_ids=sel.get("element_ids"),
            )
        node_ids: list[str] = []
        for fid in sel.get_frame_ids(md):
            fe = md.frame_elements.get(fid)
            if fe is None or getattr(fe, "inactive", False):
                continue
            node_ids.append(fe.node_i)
            node_ids.append(fe.node_j)
        for aid in sel.get_area_ids(md):
            ae = md.area_elements.get(aid)
            if ae is None or getattr(ae, "inactive", False):
                continue
            node_ids.extend(ae.node_ids)
        return list(dict.fromkeys(node_ids))

    def _storey_diaphragm_components(self, md) -> list[tuple[float, list[str]]]:
        """Derive diaphragm components from storey-level detection (Path B).

        Uses :func:`fea_toolkit.model.stories.identify_stories` to cluster
        nodes into storey levels; each storey becomes one
        ``(elevation, node_ids)`` component.  Imported lazily because
        ``model.stories`` imports ``openseespy`` at module level.
        """
        from ..model.stories import identify_stories

        story_levels = identify_stories(md)
        components: list[tuple[float, list[str]]] = []
        for story in story_levels:
            node_ids = [nid for nid in (story.node_ids or []) if nid in md.nodes]
            if not node_ids:
                continue
            z_mean = round(story.elevation, 4)
            components.append((z_mean, node_ids))
        return components

    _WALL_ELEMENT_TYPES = ("SFI_MVLEM_3D", "E_SFI_MVLEM_3D", "MVLEM_3D")

    def _resolve_wall_elements(
        self,
        mesh_model: MeshModel,
        model_data: SAPModelData,
    ) -> None:
        """Generate wall macro-elements from wall-classified areas.

        When ``element_strategies.wall.element_type`` is one of
        ``SFI_MVLEM_3D`` / ``E_SFI_MVLEM_3D`` / ``MVLEM_3D``, each
        wall-classified area (corners span more than the configured
        ``slab_z_tolerance`` length units in Z) is converted into a single
        :class:`~fea_toolkit.model.mesh_model.WallElement` macro-element.
        The area is marked inactive so the AnalysisBuilder skips shell
        creation for it (the macro-element replaces the shell).

        Two material families are supported, selected by
        ``material_type``:

        * ``"FSAM"`` (default) — SFI_MVLEM_3D / E_SFI_MVLEM_3D per-fibre
          FSAM nD materials (``fsam_material_names``).
        * ``"uniaxial"`` — MVLEM_3D per-fibre uniaxial concrete + steel
          tags plus a single horizontal shear spring
          (``concrete_names`` / ``steel_names`` / ``shear_name``) and a
          per-fibre density (``rho``).

        Fiber discretisation:
          * ``n_fibers`` (default 5) controls the number of macro-fibers
            ``m``.
          * Total wall width is split into ``m`` equal-width strips.
          * Fiber thickness comes from the wall's assigned shell section
            ``thickness`` (fallback 0.3 length units).
          * FSAM: each fiber references one FSAM nD material.  The config
            may list explicit names under ``fsam_materials``; otherwise
            all FSAM-type ``nd_materials`` (in insertion order) are
            reused, cycling to fill ``m`` fibers.
          * Uniaxial: every fiber references the configured
            ``concrete_material``; ``steel_material`` is assigned to the
            outermost ``boundary_fibers`` fibers (mirroring the
            boundary-enriched layout of the converged MVLEM probe) and
            ``dummy_material`` fills the interior.  ``shear_material`` is
            the single horizontal shear spring; ``density`` feeds the
            per-fiber ``-rho`` list.

        The corner nodes are sorted into OpenSees quad order: bottom
        pair (lowest Z) then top pair, each ordered left→right by X.

        Args:
            mesh_model: Prepared MeshModel (mutated in place).
            model_data: Mutated SAPModelData from the topology pass.
        """
        wall_strategy = (self.config.get("element_strategies") or {}).get("wall", {})
        elem_type = wall_strategy.get("element_type")
        if elem_type not in self._WALL_ELEMENT_TYPES:
            return

        n_fibers = int(wall_strategy.get("n_fibers", 5))
        cor = float(wall_strategy.get("CoR", 0.4))
        material_type = wall_strategy.get("material_type", "FSAM")

        # ── Resolve per-fiber material names ──────────────────────
        if material_type == "uniaxial":
            # MVLEM_3D: uniaxial concrete + steel + shear spring.
            concrete_name = wall_strategy.get("concrete_material", "concrete")
            steel_name = wall_strategy.get("steel_material", "steel")
            dummy_name = wall_strategy.get("dummy_material", "dummy")
            shear_name = wall_strategy.get("shear_material", "shear")
            density = float(wall_strategy.get("density", 2400.0))
            n_bdry = int(wall_strategy.get("boundary_fibers", 1))
            concrete_names = [concrete_name] * n_fibers
            steel_names = [dummy_name] * n_fibers
            for i in range(min(n_bdry, n_fibers)):
                steel_names[i] = steel_name
                steel_names[-(i + 1)] = steel_name
            rho = [density] * n_fibers
            if not all(
                n in mesh_model.materials
                for n in (concrete_name, steel_name, dummy_name, shear_name)
            ):
                if self.config.get("verbose", False):
                    print(
                        "  ⚠ [wall_elements] MVLEM_3D requested but one of "
                        f"concrete/steel/dummy/shear materials "
                        f"({concrete_name}, {steel_name}, {dummy_name}, "
                        f"{shear_name}) is missing from the model materials — "
                        "skipping wall element generation"
                    )
                return
        else:
            # FSAM family: resolve explicit or cycled FSAM nD names.
            explicit_mats = wall_strategy.get("fsam_materials") or []
            fsam_names_in_order = [
                name for name, nd in mesh_model.nd_materials.items() if nd.material_type == "FSAM"
            ]
            if not fsam_names_in_order and not explicit_mats:
                if self.config.get("verbose", False):
                    print(
                        f"  ⚠ [wall_elements] {elem_type} requested but no "
                        "FSAM nD materials configured — skipping wall "
                        "element generation"
                    )
                return
            if explicit_mats:
                fiber_mats = [explicit_mats[i % len(explicit_mats)] for i in range(n_fibers)]
            else:
                fiber_mats = [
                    fsam_names_in_order[i % len(fsam_names_in_order)] for i in range(n_fibers)
                ]

        # Wall elements are tagged past the maximum *node* tag (with a
        # 1000 offset) and past every existing element tag: the generated
        # elem_tags must not collide with any node or element tag in the
        # OpenSees domain (nodes and elements share the tag namespace).
        max_node_tag = max(
            (nd.node_tag for nd in mesh_model.nodes.values()),
            default=0,
        )
        max_frame_tag = max(
            (fe.elem_tag for fe in mesh_model.frame_elements.values()),
            default=0,
        )
        max_area_tag = max(
            (ae.area_tag for ae in mesh_model.area_elements.values()),
            default=0,
        )
        next_tag = max(10000, max_node_tag + 1000, max_frame_tag + 1, max_area_tag + 1)

        # Wall-vs-slab horizontality tolerance — same config key and default
        # as Preprocessor._classify_element_type so wall-element generation
        # agrees with the area_element_types role stored on the MeshModel.
        slab_z_tol = float(self.config.get("slab_z_tolerance", 0.02))

        wall_idx = 0
        for aid, area in mesh_model.area_elements.items():
            if getattr(area, "inactive", False):
                continue
            if aid in mesh_model.loads_only_area_ids:
                continue
            nids = list(area.node_ids)
            if len(nids) < 4:
                continue

            nodes = mesh_model.nodes
            missing = [nid for nid in nids if nid not in nodes]
            if missing:
                continue

            # Classify geometry: slab when the corner Z-span is below
            # slab_z_tolerance — strict '<' mirrors _classify_element_type,
            # keeping wall-element generation consistent with the
            # area_element_types role computed for the same areas.
            zs = [nodes[nid].z for nid in nids]
            if max(zs) - min(zs) < slab_z_tol:
                continue  # horizontal → slab

            # Resolve section thickness
            sec_name = mesh_model.area_assignments.get(aid, "")
            sec = mesh_model.sections.get(sec_name) if sec_name else None
            if isinstance(sec, ShellSection):
                thickness = sec.thickness if sec.thickness and sec.thickness > 0 else 0.3
            else:
                thickness = float(wall_strategy.get("thickness", 0.3))
            per_fiber_thick = [thickness] * n_fibers

            # Sort corners into OpenSees quad order [i, j, k, l]:
            # bottom pair (lowest Z) then top pair, each ordered L→R along
            # the wall's dominant horizontal axis.  The wall width direction
            # is the axis with the larger horizontal spread — X if the
            # x-range exceeds the y-range, else Y (e.g. a YZ-plane wall has
            # x-span 0 so Y is used).  An X-only sort would tie for YZ walls
            # whose corners share x and z.
            _xs = [nodes[nid].x for nid in nids]
            _ys = [nodes[nid].y for nid in nids]
            _width_axis = "x" if (max(_xs) - min(_xs)) >= (max(_ys) - min(_ys)) else "y"
            sorted_nodes = sorted(nids, key=lambda nid: (nodes[nid].z, nodes[nid].x, nodes[nid].y))
            bottom = sorted_nodes[:2]
            top = sorted_nodes[2:4]
            bottom.sort(key=lambda nid: getattr(nodes[nid], _width_axis))
            top.sort(key=lambda nid: getattr(nodes[nid], _width_axis))
            quad = [bottom[0], bottom[1], top[0], top[1]]

            # Total wall width = bottom edge length (full 3D length, so
            # the width is correct even for walls not aligned to a
            # global axis)
            bi, bj = nodes[quad[0]], nodes[quad[1]]
            wall_width = ((bj.x - bi.x) ** 2 + (bj.y - bi.y) ** 2 + (bj.z - bi.z) ** 2) ** 0.5
            if wall_width <= 0.0:
                continue
            per_fiber_width = [wall_width / n_fibers] * n_fibers

            elem_id = f"W{wall_idx + 1}"
            if material_type == "uniaxial":
                mesh_model.wall_elements[elem_id] = WallElement(
                    elem_id=elem_id,
                    elem_tag=next_tag,
                    node_ids=quad,
                    m=n_fibers,
                    thick=per_fiber_thick,
                    width=per_fiber_width,
                    fsam_material_names=[],
                    concrete_names=concrete_names,
                    steel_names=steel_names,
                    shear_name=shear_name,
                    rho=rho,
                    material_type="uniaxial",
                    element_type=elem_type,
                    CoR=cor,
                )
            else:
                mesh_model.wall_elements[elem_id] = WallElement(
                    elem_id=elem_id,
                    elem_tag=next_tag,
                    node_ids=quad,
                    m=n_fibers,
                    thick=per_fiber_thick,
                    width=per_fiber_width,
                    fsam_material_names=list(fiber_mats),
                    material_type="FSAM",
                    element_type=elem_type,
                    CoR=cor,
                )
            next_tag += 1
            wall_idx += 1

            # Mark the area inactive so shell creation skips it.
            area.inactive = True

            if self.config.get("verbose", False):
                print(
                    f"  [wall_elements] {elem_id}: {elem_type} from area '{aid}' "
                    f"(W={wall_width:.4g}, t={thickness:.4g}, m={n_fibers}, "
                    f"material={material_type})"
                )

    def _resolve_element_properties(
        self,
        mesh_model: MeshModel,
        model_data: SAPModelData,
    ) -> None:
        """Resolve per-element creation properties from config.

        Three-level resolution (highest priority first):
        1. per-ID overrides (``frame_overrides`` / area ID keys in
           ``shell_layers``)
        2. selection-based groups (``frame_groups`` / ``shell_layers``
           with ``selector`` dicts)
        3. role defaults (``element_strategies``)

        Populates ``mesh_model.frame_element_properties``,
        ``mesh_model.area_element_properties``,
        ``mesh_model.nd_materials``, and
        ``mesh_model.layered_shell_sections``.
        """
        config = self.config
        verbose = config.get("verbose", False)

        # ── Default element strategies per role ──────────────────
        role_defaults = config.get("element_strategies", {})

        # ── Resolve nD materials from config dicts ───────────────
        nd_mat_config = config.get("nd_materials", {})
        _nd_field_names = {f.name for f in fields(NDMaterial)}
        # Scale stress-valued fields from SI Pa to model units
        ssf = stress_scale_factor(model_data.units)
        for mat_name, mat_dict in nd_mat_config.items():
            invalid_keys = [k for k in mat_dict if k not in _nd_field_names]
            if invalid_keys:
                raise ValueError(
                    f"Invalid key(s) in nd_materials['{mat_name}']: "
                    f"{invalid_keys}.  Accepted fields: {sorted(_nd_field_names)}"
                )
            kwargs = {k: v for k, v in mat_dict.items() if k in _nd_field_names}
            # Scale stress-valued fields (SI Pa → model stress units)
            kwargs = scale_material_dict(kwargs, model_data.units, stress_scale=ssf)
            kwargs.setdefault("name", mat_name)
            mesh_model.nd_materials[mat_name] = NDMaterial(**kwargs)

        # ── Resolve SFI_MVLEM_3D wall elements from config ───────
        self._resolve_wall_elements(mesh_model, model_data)

        # ── Resolve layered shell sections from config dicts ─────
        shell_layers_config = config.get("shell_layers", {})
        # Build {group_key: [ShellFiberLayer, ...]} lookups
        _layer_stacks: dict[str, list[ShellFiberLayer]] = {}
        for group_key, group_dict in shell_layers_config.items():
            raw_layers = group_dict.get("layers", [])
            layers = []
            for raw in raw_layers:
                layers.append(
                    ShellFiberLayer(
                        thickness=raw.get("thickness", 0.1),
                        nd_material=raw.get("nd_material", ""),
                        n_ip=raw.get("n_ip", 4),
                    )
                )
            _layer_stacks[group_key] = layers
            if layers:
                mesh_model.layered_shell_sections[group_key] = LayeredShellSection(
                    name=group_key,
                    layers=layers,
                )
                # Also register under selector-matched SAP2000 section names
                # so the AnalysisBuilder can find them by sec_name at element
                # creation time.  Without this, all shell areas end up using
                # elastic ShellMITC4 because sec_name ("Shear Wall") never
                # matches the config group_key ("shear_walls").
                sel_dict = group_dict.get("selector", {})
                for sap_sec_name in sel_dict.get("sections") or []:
                    if sap_sec_name in mesh_model.layered_shell_sections:
                        warnings.warn(
                            f"Shell layer group '{group_key}': SAP section name "
                            f"'{sap_sec_name}' is already registered in "
                            f"layered_shell_sections (from group "
                            f"'{mesh_model.layered_shell_sections[sap_sec_name].name}'). "
                            f"Skipping duplicate — the first layer stack will be used."
                        )
                        continue
                    mesh_model.layered_shell_sections[sap_sec_name] = LayeredShellSection(
                        name=sap_sec_name,
                        layers=layers,
                    )

        # ── Pre-build Selection objects for frame groups ─────────
        frame_groups_config = config.get("frame_groups", {})
        frame_groups: list[tuple] = []  # [(Selection, FrameElementProperties), ...]
        for group_name, group_dict in frame_groups_config.items():
            sel_dict = group_dict.get("selector")
            if not sel_dict or not isinstance(sel_dict, dict):
                continue
            sel = Selection(
                element_types=sel_dict.get("element_types"),
                sections=sel_dict.get("sections") or sel_dict.get("section_name"),
                materials=sel_dict.get("materials"),
                groups=sel_dict.get("groups"),
                element_ids=sel_dict.get("element_ids"),
            )
            props = FrameElementProperties(
                element_type=group_dict.get("element", "elasticBeamColumn"),
                material_strategy=group_dict.get("material", "elastic"),
                integration_type=group_dict.get("integration"),
                num_integration_points=group_dict.get("num_int_pts", 0),
                hinge_params=group_dict.get("hinge_params"),
            )
            frame_groups.append((sel, props))

        # ── Resolve per-frame properties ─────────────────────────
        frame_overrides = config.get("frame_overrides", {})
        _frame_group_matched_ids: set = set()

        for eid, elem in mesh_model.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue

            # Level 1: per-ID override
            if eid in frame_overrides:
                od = frame_overrides[eid]
                props = FrameElementProperties(
                    element_type=od.get("element", "elasticBeamColumn"),
                    material_strategy=od.get("material", "elastic"),
                    integration_type=od.get("integration"),
                    num_integration_points=od.get("num_int_pts", 0),
                    hinge_params=od.get("hinge_params"),
                )
                mesh_model.frame_element_properties[eid] = props
                _frame_group_matched_ids.add(eid)
                if verbose:
                    print(f"  [elem_props] {eid}: per-ID → {props.element_type}")
                continue

            # Level 2: selection-based groups
            for sel_idx, (sel, sel_props) in enumerate(frame_groups):
                if sel._frame_matches(model_data, eid):
                    mesh_model.frame_element_properties[eid] = copy.deepcopy(sel_props)
                    _frame_group_matched_ids.add(eid)
                    if verbose:
                        print(f"  [elem_props] {eid}: group {sel_idx} → {sel_props.element_type}")
                    break

            if eid not in _frame_group_matched_ids:
                # Level 3: role default
                etype = mesh_model.frame_element_types.get(eid, "")
                if etype in role_defaults:
                    rd = role_defaults[etype]
                    props = FrameElementProperties(
                        element_type=rd.get("element", "elasticBeamColumn"),
                        material_strategy=rd.get("material", "elastic"),
                        integration_type=rd.get("integration"),
                        num_integration_points=rd.get("num_int_pts", 0),
                        hinge_params=rd.get("hinge_params"),
                    )
                    mesh_model.frame_element_properties[eid] = props
                else:
                    mesh_model.frame_element_properties[eid] = FrameElementProperties()

        # ── Pre-build Selection objects for area groups ──────────
        area_groups: list[tuple] = []  # [(group_key, Selection, AreaElementProperties), ...]
        area_overrides: dict = {}
        for group_key, group_dict in shell_layers_config.items():
            if group_key in mesh_model.area_elements:
                # Level 1: area ID as key in shell_layers
                area_overrides[group_key] = group_dict
            elif "selector" in group_dict:
                sel_dict = group_dict["selector"]
                sel = Selection(
                    element_types=sel_dict.get("element_types"),
                    sections=sel_dict.get("sections") or sel_dict.get("section_name"),
                    materials=sel_dict.get("materials"),
                    groups=sel_dict.get("groups"),
                    element_ids=sel_dict.get("element_ids"),
                )
                layer_stack = _layer_stacks.get(group_key, [])
                elem_type = group_dict.get("element", "ShellMITC4")
                props = AreaElementProperties(
                    element_type=elem_type if elem_type != "None" else None,
                    material_strategy=group_dict.get(
                        "material", "layered_rc" if layer_stack else "elastic"
                    ),
                    thickness=group_dict.get("thickness"),
                    layer_stack=layer_stack,
                    layered_section_group_key=group_key,
                )
                area_groups.append((group_key, sel, props))
            else:
                warnings.warn(
                    f"shell_layers key '{group_key}' is neither a valid area ID "
                    f"nor contains a 'selector' key — skipping"
                )

        # ── Resolve per-area properties ──────────────────────────
        _area_group_matched_ids: set = set()

        for aid, area in mesh_model.area_elements.items():
            if getattr(area, "inactive", False):
                continue

            # Level 1: per-ID override
            if aid in area_overrides:
                od = area_overrides[aid]
                layer_stack = _layer_stacks.get(aid, [])
                elem_type = od.get("element", "ShellMITC4")
                props = AreaElementProperties(
                    element_type=elem_type if elem_type != "None" else None,
                    material_strategy=od.get(
                        "material", "layered_rc" if layer_stack else "elastic"
                    ),
                    thickness=od.get("thickness"),
                    layer_stack=layer_stack,
                    layered_section_group_key=aid,
                )
                mesh_model.area_element_properties[aid] = props
                _area_group_matched_ids.add(aid)
                if verbose:
                    print(f"  [area_props] {aid}: per-ID → {props.element_type}")
                continue

            # Level 2: selection-based groups
            for group_name, sel, sel_props in area_groups:
                if sel._area_matches(model_data, aid):
                    mesh_model.area_element_properties[aid] = sel_props
                    _area_group_matched_ids.add(aid)
                    if verbose:
                        print(
                            f"  [area_props] {aid}: group '{group_name}' → {sel_props.element_type}"
                        )
                    break

            if aid not in _area_group_matched_ids:
                # Level 3: role default
                etype = mesh_model.area_element_types.get(aid, "")
                if etype in role_defaults:
                    rd = role_defaults[etype]
                    elem_type = rd.get("element", "ShellMITC4")
                    props = AreaElementProperties(
                        element_type=elem_type if elem_type != "None" else None,
                        material_strategy=rd.get("material", "elastic"),
                        thickness=rd.get("thickness"),
                    )
                    mesh_model.area_element_properties[aid] = props
                else:
                    mesh_model.area_element_properties[aid] = AreaElementProperties()

        if verbose:
            print(
                f"  Resolved element properties: "
                f"{len(mesh_model.frame_element_properties)} frames, "
                f"{len(mesh_model.area_element_properties)} areas"
            )


def preprocess_model(md, config: dict = None, selection: Optional["Selection"] = None):
    """Run the Preprocessor once and return the prepared MeshModel.

    This is the expensive topology step (frame splitting, area meshing,
    node merging, edge detection).  Call once, then use the returned
    ``MeshModel`` for all analysis cases.

    Args:
        md: The parsed ``SAPModelData`` to preprocess.
        config: Configuration dict passed to the ``Preprocessor``.
        selection: Optional :class:`Selection` designating loads‑only
            areas (no shell elements created for them).  Nodes
            referenced only by such areas are moved to
            ``MeshModel.orphan_nodes`` — they are kept for
            visualisation but not created in OpenSees.
    """
    from .preprocessor import Preprocessor

    if config is None:
        config = {}
    preprocessor = Preprocessor(config)
    return preprocessor.run(md, load_shell_selection=selection)
