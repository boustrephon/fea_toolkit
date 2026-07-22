"""Model preprocessor — topology mutations producing a ``MeshModel``.

The :class:`Preprocessor` consumes a ``SAPModelData`` instance and produces a
:class:`~fea_toolkit.model.mesh_model.MeshModel` — a frozen, serialisable
topology with all splitting, meshing, subdividing, and constraint detection
already applied.  No OpenSees domain objects are created.
"""

from typing import Dict, Any, Optional, List, Set, Tuple
from pathlib import Path
import copy
import warnings

from ..model.sap_data import (
    SAPModelData, Node, FrameElement, FrameDistributedLoad, AreaElement,
    Restraint,
)
from ..model.geometry import (
    split_elements,
    apply_frame_end_offsets,
    mesh_area_elements,
    subdivide_area_mesh,
    find_constraint_edges,
    warn_frame_overlaps,
    convert_area_loads_to_edge_loads,
    find_wall_nodes_inside_slabs,
    split_slabs_at_wall_intersections,
    print_wall_inside_slab_report,
)
from ..model.selection import Selection
from ..model.mesh_model import MeshModel


class Preprocessor:
    """Prepare model topology for OpenSees analysis.

    Performs all geometry mutations (splitting, meshing, subdividing,
    constraint detection) as pure data operations.  The output is a
    ``MeshModel`` that can be consumed by the
    :class:`~fea_toolkit.opensees.analysis_builder.AnalysisBuilder`
    or by the Tcl export path.

    Args:
        config: Builder configuration dict (same keys as
            :class:`~fea_toolkit.opensees.builder.OpenSeesBuilder`).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    # ── Public API ────────────────────────────────────────────────

    def run(self,
            model_data: SAPModelData,
            selection: Optional[Selection] = None,
            ) -> MeshModel:
        """Run the full topology preparation pipeline.

        Operates on a **copy** of *model_data* so the original is untouched.

        Args:
            model_data: Parsed SAP2000 model data.
            selection: Optional :class:`Selection` designating loads‑only
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
        frame_element_types: Dict[str, str] = {}
        if self.config.get('stiffness_factors'):
            for eid, elem in md.frame_elements.items():
                if getattr(elem, 'inactive', False):
                    continue
                frame_element_types[eid] = self._classify_element_type(elem, is_area=False, nodes=md.nodes)

        # ── 2. Element splitting ─────────────────────────────────
        split_dist_loads: List[FrameDistributedLoad] = []
        if self.config.get('split_elements', True):
            new_elems, new_assigns, split_dist_loads = split_elements(
                md.nodes, md.frame_elements, md.frame_assignments,
                getattr(md, 'frame_dist_loads', []),
                getattr(md, 'frame_auto_mesh', {}),
                tol=1e-6,
                verbose=self.config.get('verbose', False),
            )
        else:
            new_elems = md.frame_elements
            new_assigns = md.frame_assignments
            split_dist_loads = getattr(md, 'frame_dist_loads', [])

        # Propagate element types to split children
        if self.config.get('stiffness_factors'):
            for child_id, child_elem in new_elems.items():
                parent_id = getattr(child_elem, 'parent_id', None)
                if parent_id and parent_id in frame_element_types:
                    frame_element_types[child_id] = frame_element_types[parent_id]

        # ── 3. Frame end offsets ─────────────────────────────────
        offset_rigid_links: List[tuple] = []
        if md.frame_end_offsets:
            from ..model.geometry import apply_frame_end_offsets
            max_node_tag = max((n.node_tag for n in md.nodes.values()), default=0)
            new_elems, new_assigns, md.nodes, _next_tag, offset_rigid_links = (
                apply_frame_end_offsets(
                    new_elems, new_assigns, md.nodes,
                    md.frame_end_offsets,
                    next_tag=max_node_tag + 1,
                )
            )

        # ── 3b. Split areas at frame edges ────────────────────────
        # Ensures slab mesh nodes coincide with frame split points.
        # Only needed when shells are created — when create_shells=False
        # the areas are loads-only and their loads are converted to frame
        # edge loads in step 4.  Unconditional splitting would create
        # orphan _af_ nodes with no element connectivity → singular matrix.
        create_shells = self.config.get('create_shells', False)
        if create_shells and self.config.get('split_areas_at_frame_edges', True):
            from ..model.geometry import split_areas_at_frame_edges
            max_tag = max(
                (ae.area_tag for ae in md.area_elements.values()), default=0
            )
            max_ntag = max(
                (nd.node_tag for nd in md.nodes.values()), default=0
            )
            md.area_elements, md.area_assignments, md.nodes, _ = (
                split_areas_at_frame_edges(
                    md.area_elements, md.area_assignments, md.nodes,
                    new_elems,
                    next_tag=max(max_tag, max_ntag) + 1,
                    groups=getattr(md, 'groups', None),
                )
            )

        # ── 4. Convert area loads to frame edge loads ────────────
        edge_loads_from_areas: list = []
        loads_only_area_ids: Set[str] = set()
        # create_shells already fetched at step 3b, reuse
        if selection is not None:
            edge_loads_from_areas = self._convert_area_loads(
                md, selection, new_elems,
            )
            loads_only_area_ids = set(selection.get_area_ids(md))
        elif not create_shells:
            # No shell mode + no selection → convert all area loads
            edge_loads_from_areas = self._convert_area_loads(
                md, selection, new_elems,
            )
        # else: create_shells=True + selection=None → all areas become shells
        #       → no load conversion (handled by shell elements)

        # ── 5. Mesh area elements ─────────────────────────────────
        if create_shells:
            self._mesh_areas(md, selection=selection)
            self._merge_coincident_nodes(md)

            # Split frames at shell mesh nodes.
            # Only splits frames whose root parent has AtJoints=True
            # in the original SAP2000 auto-mesh data.
            if self.config.get('split_frames_at_shell_nodes', True):
                _before = len(new_elems)
                new_elems, new_assigns, split_dist_loads, frame_element_types, edge_loads_from_areas = (
                    self._split_frames_at_shell_subdiv(
                        md, new_elems, new_assigns, split_dist_loads,
                        frame_element_types,
                        edge_loads=edge_loads_from_areas,
                        exclude_area_ids=loads_only_area_ids,
                    )
                )
                _after = len(new_elems)
                if _after > _before:
                    print(f"    _split_frames_at_shell_subdiv: "
                          f"split {_after - _before} new frame(s) "
                          f"({_before} → {_after})")
                self._merge_coincident_nodes(md)

            # N×N shell subdivision (opt-in, additional refinement)
            self._subdivide_shells_in_model_data(md)

            if self.config.get('subdivide_shells', 0):
                # Second round of frame splitting for sub-division nodes
                new_elems, new_assigns, split_dist_loads, frame_element_types, edge_loads_from_areas = (
                    self._split_frames_at_shell_subdiv(
                        md, new_elems, new_assigns, split_dist_loads,
                        frame_element_types,
                        edge_loads=edge_loads_from_areas,
                        exclude_area_ids=loads_only_area_ids,
                    )
                )
                self._merge_coincident_nodes(md)

            # Classify areas after meshing
            area_element_types: Dict[str, str] = {}
            if self.config.get('stiffness_factors'):
                for aid, area in md.area_elements.items():
                    if getattr(area, 'inactive', False):
                        continue
                    area_element_types[aid] = self._classify_element_type(
                        area, is_area=True, nodes=md.nodes)
        else:
            area_element_types = {}

        # ── 6. Detect constraint edges (opt-in diagnostic) ──────
        raw_edges = []
        if self.config.get('detect_constraint_edges', False):
            raw_edges = self._detect_constraint_edges(md, new_elems, new_assigns)

        # ── 7. Detect diaphragm levels ───────────────────────────
        diaphragm_levels = self._detect_diaphragm_levels(md)

        # ── 8. Build MeshModel ───────────────────────────────────
        base_z = min((nd.z for nd in md.nodes.values()), default=None)

        # Pre-assign material and section tags so AnalysisBuilder
        # does not need to auto-assign them (deterministic recreation).
        material_tags: Dict[str, int] = {}
        for i, mat_name in enumerate(md.materials.keys(), start=1):
            material_tags[mat_name] = i

        section_tags: Dict[str, int] = {}
        for i, sec_name in enumerate(md.sections.keys(), start=len(material_tags) + 1):
            section_tags[sec_name] = i

        # ── 8b. Create type-specific section variants (stiffness factors) ──
        # For each (section, element_type) pair where the type factor != 1.0,
        # create a variant section in md.sections with a modified material E_mod.
        # The AnalysisBuilder._add_beam_column looks for variant_key in section_tags.
        _sf = self.config.get('stiffness_factors')
        if _sf and frame_element_types:
            # Determine which types have non-unity factors
            variant_types = {etype for etype in ('beam', 'column', 'brace', 'wall', 'slab')
                             if _sf.get(etype, 1.0) != 1.0}
            # Collect which sections need which variant
            _needed: Dict[str, set] = {}  # sec_name → set of etypes
            for eid, etype in frame_element_types.items():
                if etype in variant_types:
                    sec_name = new_assigns.get(eid, '')
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
                    if base_mat is None or base_mat.type.lower() != 'concrete':
                        if self.config.get('verbose', False):
                            print(f"  ⚠ Section '{sec_name}' material "
                                  f"'{base_mat_name}' is not concrete — "
                                  f"skipping stiffness factor for {etype}")
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
        referenced: Set[str] = set()
        for fe in new_elems.values():
            if not getattr(fe, 'inactive', False):
                referenced.add(fe.node_i)
                referenced.add(fe.node_j)
        for aid, ae in md.area_elements.items():
            if getattr(ae, 'inactive', False):
                continue
            if aid in loads_only_area_ids:
                continue  # these areas have no shells, skip their nodes
            referenced.update(ae.node_ids)
        orphan_nodes: Dict[str, Node] = {}
        for nid in list(md.nodes.keys()):
            if nid not in referenced:
                orphan_nodes[nid] = md.nodes.pop(nid)
        if orphan_nodes:
            pass  # kept for visualisation

        mesh_model = MeshModel(
            nodes=md.nodes,
            frame_elements=new_elems,
            frame_assignments=new_assigns,
            area_elements=md.area_elements,
            area_assignments=md.area_assignments,
            frame_dist_loads=split_dist_loads,
            edge_loads_from_areas=edge_loads_from_areas,
            edge_constraint_pairs=raw_edges,
            diaphragm_levels=diaphragm_levels,
            offset_rigid_links=offset_rigid_links,
            frame_element_types=frame_element_types,
            area_element_types=area_element_types,
            materials=md.materials,
            sections=md.sections,
            groups=getattr(md, 'groups', {}),
            restraints=md.restraints,
            base_z=base_z,
            units=md.units,
            model_name=getattr(md, 'name', ''),
            material_tags=material_tags,
            section_tags=section_tags,
            loads_only_area_ids=loads_only_area_ids,
            orphan_nodes=orphan_nodes,
            # Pass through load collections for AnalysisBuilder consumption
            joint_loads=getattr(md, 'joint_loads', []),
            frame_gravity_loads=getattr(md, 'frame_gravity_loads', []),
            area_gravity_loads=getattr(md, 'area_gravity_loads', []),
            area_uniform_loads=getattr(md, 'area_uniform_loads', []),
            mass_sources=getattr(md, 'mass_sources', {}),
        )

        return mesh_model

    # ── Internal helpers ──────────────────────────────────────────

    def _classify_element_type(self, elem, is_area: bool = False,
                                nodes: Optional[Dict[str, Node]] = None) -> str:
        """Classify a frame or area element into a structural type.

        This is the **geometry signal** for element classification.
        The companion **section-type signal** is
        ``Selection.from_brace_sections()``.  Both are documented in
        ``docs/element_classification.md``.

        Classification rules
        --------------------
        **Area elements:**
          ``'slab'`` if all corner Z-coordinates are within 0.02 length
          units of each other (horizontal); ``'wall'`` otherwise.

        **Frame elements (geometry-based):**
          * ``'column'`` if the vertical span (dz) exceeds 4\u00d7 the
            horizontal span (\u221a(dx\u00b2 + dy\u00b2)).  Near-vertical.
          * ``'brace'`` if BOTH horizontal span and vertical span exceed
            0.01 length units (diagonal).
          * ``'beam'`` otherwise (horizontal or near-horizontal).

        The geometry-only ``'brace'`` label is a **hint** \u2014 the final
        brace decision for pushover also requires the section to be a
        recognised brace shape (Pipe, Angle, etc.).  This two-signal
        approach avoids false positives (e.g. a horizontal pipe handrail
        classified as a brace).

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

        if is_area:
            zs = []
            for nid in elem.node_ids:
                nd = nodes.get(nid)
                if nd is None:
                    return 'unknown'
                zs.append(nd.z)
            return 'slab' if max(zs) - min(zs) < 0.02 else 'wall'

        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            return 'unknown'

        dz = abs(ni.z - nj.z)
        dx = abs(ni.x - nj.x)
        dy = abs(ni.y - nj.y)
        dh = (dx**2 + dy**2)**0.5

        if dz > 4.0 * max(dh, 0.01):
            return 'column'
        if dh > 0.01 and dz > 0.01:
            return 'brace'
        return 'beam'

    def _convert_area_loads(self, md, selection, frame_elements) -> list:
        """Convert area uniform loads to frame edge loads."""
        sel_area_ids: Set[str] = set()
        if selection is not None:
            sel_area_ids = set(selection.get_area_ids(md))

        area_loads = getattr(md, 'area_uniform_loads', [])
        if selection is not None:
            area_loads = [ld for ld in area_loads if ld.area_id in sel_area_ids]
        if not area_loads:
            return []

        # When selection is provided, only include matching areas;
        # otherwise include all area elements referenced by the loads.
        load_area_ids = set(ld.area_id for ld in area_loads)
        if selection is not None:
            area_filter = {aid: ae for aid, ae in md.area_elements.items()
                           if aid in sel_area_ids}
        else:
            area_filter = {aid: ae for aid, ae in md.area_elements.items()
                           if aid in load_area_ids}

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
        if self.config.get('detect_wall_slab_intersections', True):
            ws_findings = find_wall_nodes_inside_slabs(
                md.area_elements, md.area_assignments, md.nodes,
            )
            if ws_findings:
                print_wall_inside_slab_report(ws_findings)

                # Optional auto-split
                if self.config.get('split_slabs_at_walls', False):
                    max_tag = max(
                        (ae.area_tag for ae in md.area_elements.values()),
                        default=0,
                    )
                    max_ntag = max(
                        (nd.node_tag for nd in md.nodes.values()), default=0,
                    )
                    next_tag = max(max_tag, max_ntag) + 1
                    areas_ws, assign_ws, nodes_ws, _ = (
                        split_slabs_at_wall_intersections(
                            md.area_elements, md.area_assignments,
                            md.nodes, next_tag=next_tag,
                            groups=getattr(md, 'groups', None),
                        )
                    )
                    md.area_elements = areas_ws
                    md.area_assignments = assign_ws
                    md.nodes = nodes_ws
                    print(f"    → Split at {len(ws_findings)} wall intersection(s)")

        # ── Area meshing ─────────────────────────────────────────
        area_mesh = getattr(md, 'area_mesh', {})
        if not area_mesh:
            return

        # Exclude loads-only areas
        if selection is not None:
            loads_only = set(selection.get_area_ids(md))
            mesh_filtered = {aid: m for aid, m in area_mesh.items()
                             if aid not in loads_only}
        else:
            mesh_filtered = area_mesh

        if not mesh_filtered:
            return

        max_elem_tag = max(
            (ae.area_tag for ae in md.area_elements.values()), default=0
        )
        max_node_tag = max(
            (nd.node_tag for nd in md.nodes.values()), default=0
        )
        next_tag = max(max_elem_tag, max_node_tag) + 1

        areas, assignments, nodes, _ = mesh_area_elements(
            md.area_elements, md.area_assignments, md.nodes,
            mesh_filtered, next_tag=next_tag,
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

        mesh_nodes = {nid: nd for nid, nd in md.nodes.items()
                      if "_mesh_" in nid}
        if not mesh_nodes:
            return

        # Protected nodes: original SAP nodes + frame element nodes
        protected: set = set()
        for fid, fe in md.frame_elements.items():
            if not getattr(fe, 'inactive', False):
                protected.add(fe.node_i)
                protected.add(fe.node_j)
        for ae in md.area_elements.values():
            for nid in ae.node_ids:
                if "_mesh_" not in nid:
                    protected.add(nid)
        for eid, offset in getattr(md, 'frame_end_offsets', {}).items():
            if hasattr(offset, 'node_i') and offset.node_i:
                protected.add(offset.node_i)
            if hasattr(offset, 'node_j') and offset.node_j:
                protected.add(offset.node_j)

        # Group ALL nodes by rounded coordinates (not just mesh nodes)
        coord_map: Dict[str, List[str]] = defaultdict(list)
        for nid, nd in md.nodes.items():
            key = f"{nd.x:.4f}_{nd.y:.4f}_{nd.z:.4f}"
            coord_map[key].append(nid)

        remap: Dict[str, str] = {}
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
        subdivide_cfg = self.config.get('subdivide_shells', 0)
        if not subdivide_cfg:
            return

        n = subdivide_cfg if isinstance(subdivide_cfg, int) else subdivide_cfg.get('n', 2)
        if n < 2:
            return

        # Determine which areas to subdivide
        area_ids = set(md.area_elements.keys())
        if isinstance(subdivide_cfg, dict) and 'selection' in subdivide_cfg:
            sel = subdivide_cfg['selection']
            if sel is not None:
                area_ids = set(sel.get_area_ids(md))

        for sid in sorted(area_ids, key=lambda x: (0, int(x)) if x.isdigit() else (1, x)):
            ae = md.area_elements.get(sid)
            if ae is None or getattr(ae, 'inactive', False):
                continue
            if len(ae.node_ids) < 4:
                continue

            max_tag = max(
                (nd.node_tag for nd in md.nodes.values()), default=0
            )
            next_tag = max_tag + 1

            sub_areas, sub_assigns, sub_nodes, _ = subdivide_area_mesh(
                {sid: ae},
                {sid: md.area_assignments.get(sid, '')},
                md.nodes,
                n=n,
                next_tag=next_tag,
                groups=md.groups if hasattr(md, 'groups') else {},
            )
            md.area_elements.update(sub_areas)
            md.area_assignments.update(sub_assigns)
            md.nodes.update(sub_nodes)

    def _split_frames_at_shell_subdiv(self, md, frame_elements, frame_assignments,
                                       dist_loads, frame_element_types,
                                       edge_loads=None,
                                       exclude_area_ids=None):
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
        from ..model.geometry import point_on_segment, compute_t_location, SpatialGrid
        from collections import defaultdict

        # Build a lookup of root parent → AtJoints flag
        auto_mesh = getattr(md, 'frame_auto_mesh', {})
        _at_joints: Dict[str, bool] = {}
        for eid in frame_elements:
            # Root parent is the original SAP ID (before any "-" suffixes)
            root_id = eid.split("-")[0]
            if root_id not in _at_joints:
                am = auto_mesh.get(root_id, {})
                _at_joints[root_id] = am.get('AtJoints', False) if isinstance(am, dict) else False

        # Collect all sub-node coordinates from active area elements,
        # excluding loads-only areas that won't produce shells.
        exclude_ids = exclude_area_ids or set()
        sub_nodes: Dict[str, Node] = {}
        for ae in md.area_elements.values():
            if getattr(ae, 'inactive', False):
                continue
            if ae.area_id in exclude_ids:
                continue
            for nid in ae.node_ids:
                nd = md.nodes.get(nid)
                if nd is not None:
                    sub_nodes[nid] = nd

        if not sub_nodes:
            return frame_elements, frame_assignments, dist_loads, frame_element_types, (edge_loads or [])

        # Build spatial grid of sub-nodes
        sub_coords: Dict[str, tuple] = {
            nid: (nd.x, nd.y, nd.z) for nid, nd in sub_nodes.items()
        }
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

        new_frames: Dict[str, FrameElement] = {}
        new_assigns: Dict[str, str] = {}
        new_loads: list = []
        new_edge_loads: list = []
        _edge_loads_in = edge_loads or []
        # Track which frame IDs were split (to exclude original loads)
        _split_frame_ids: set = set()
        # Track new child types
        updated_types: Dict[str, str] = dict(frame_element_types)

        _split_count = 0
        _at_joints_count = sum(1 for v in _at_joints.values() if v)

        for eid, elem in frame_elements.items():
            if getattr(elem, 'inactive', False):
                continue
            # Skip frames whose root parent doesn't have AtJoints=True
            root_id = eid.split("-")[0]
            if not _at_joints.get(root_id, False):
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
                continue

            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
                continue

            p1 = (ni.x, ni.y, ni.z)
            p2 = (nj.x, nj.y, nj.z)

            # Find sub-nodes on this segment using spatial grid pre-filter
            tol = 1e-4
            mins = (min(p1[0], p2[0]) - tol,
                    min(p1[1], p2[1]) - tol,
                    min(p1[2], p2[2]) - tol)
            maxs = (max(p1[0], p2[0]) + tol,
                    max(p1[1], p2[1]) + tol,
                    max(p1[2], p2[2]) + tol)
            candidates = grid.points_in_bbox(mins, maxs)

            t_values: List[float] = []
            for snid, scoord in candidates:
                if snid == elem.node_i or snid == elem.node_j:
                    continue
                if point_on_segment(scoord, p1, p2, tol=tol):
                    t = compute_t_location(scoord, p1, p2)
                    if 0 < t < 1:
                        t_values.append((t, snid))

            if not t_values:
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
                continue

            _split_count += 1

            t_values.sort(key=lambda x: x[0])

            # Collect child element IDs (sequential numbering)
            child_ids: List[str] = []
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
                new_assigns[child_id] = frame_assignments.get(eid, '')
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
            new_assigns[child_id] = frame_assignments.get(eid, '')
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
            if not getattr(elem, 'inactive', False):
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
        for ld in dist_loads:
            if ld.frame_id in _split_frame_ids:
                continue  # already redistributed to children
            if ld.frame_id not in {l.frame_id for l in new_loads}:
                new_loads.append(ld)
        for ld in _edge_loads_in:
            if ld.frame_id in _split_frame_ids:
                continue  # already redistributed to children
            if ld.frame_id not in {l.frame_id for l in new_edge_loads}:
                new_edge_loads.append(ld)

        _mesh_ids = len([n for n in sub_nodes if '_mesh_' in n])
        print(f"    [_split_frames_at_shell_subdiv] "
              f"{len(sub_nodes)} total sub-nodes ({_mesh_ids} mesh-created), "
              f"{_at_joints_count} frames with AtJoints=True, "
              f"{_split_count} frames split into children",
              flush=True)
        return new_frames, new_assigns, new_loads, updated_types, new_edge_loads

    def _detect_constraint_edges(self, md, frame_elements, frame_assignments) -> list:
        """Detect constraint edges where coarse and fine meshes meet."""
        verbose = self.config.get('verbose', False)
        exclude_types = self.config.get('exclude_types', frozenset({'brick'}))
        raw_edges = find_constraint_edges(
            md.area_elements, md.area_assignments, md.nodes,
            frame_elements=frame_elements,
            frame_assignments=frame_assignments,
            exclude_types=exclude_types,
            verbose=verbose,
        )
        return raw_edges

    def _detect_diaphragm_levels(self, md) -> List[float]:
        """Detect storey levels from horizontal area elements."""
        levels: Set[float] = set()
        z_tol = 0.5
        for ae in md.area_elements.values():
            if getattr(ae, 'inactive', False):
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
        return sorted(levels)
