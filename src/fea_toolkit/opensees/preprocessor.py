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
                areas (no shell elements created for them).

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
            new_elems, new_assigns, md.nodes, offset_rigid_links = (
                apply_frame_end_offsets(
                    md.nodes, new_elems, new_assigns,
                    md.frame_end_offsets,
                )
            )

        # ── 4. Convert area loads to frame edge loads ────────────
        edge_loads_from_areas: list = []
        create_shells = self.config.get('create_shells', False)
        if selection is not None:
            edge_loads_from_areas = self._convert_area_loads(
                md, selection, new_elems,
            )
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

            # N×N shell subdivision
            self._subdivide_shells_in_model_data(md)

            # Split frames at shell sub-division nodes
            if self.config.get('subdivide_shells', 0):
                new_elems, new_assigns, split_dist_loads, frame_element_types = (
                    self._split_frames_at_shell_subdiv(
                        md, new_elems, new_assigns, split_dist_loads,
                        frame_element_types,
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

        # ── 6. Detect constraint edges ───────────────────────────
        raw_edges = self._detect_constraint_edges(md, new_elems, new_assigns)

        # ── 7. Detect diaphragm levels ───────────────────────────
        diaphragm_levels = self._detect_diaphragm_levels(md)

        # ── 8. Build MeshModel ───────────────────────────────────
        base_z = min((nd.z for nd in md.nodes.values()), default=None)

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
        )

        return mesh_model

    # ── Internal helpers ──────────────────────────────────────────

    def _classify_element_type(self, elem, is_area: bool = False,
                                nodes: Optional[Dict[str, Node]] = None) -> str:
        """Classify a frame or area element into a structural type.

        * Area elements: ``'slab'`` if horizontal (all corner Z within
          0.02 units), ``'wall'`` otherwise.
        * Frame elements: ``'column'`` if vertical span exceeds 4× the
          horizontal span; ``'brace'`` if diagonal; ``'beam'`` otherwise.
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

        return convert_area_loads_to_edge_loads(
            md.nodes,
            {aid: ae for aid, ae in md.area_elements.items()
             if aid in sel_area_ids},
            frame_elements,
            area_loads,
        )

    def _mesh_areas(self, md, selection=None):
        """Subdivide area elements per AREA MESH ASSIGNMENTS.

        Pure data operation — no OpenSees calls.
        """
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

        # Group mesh nodes by rounded coordinates
        coord_map: Dict[str, List[str]] = defaultdict(list)
        for nid, nd in mesh_nodes.items():
            key = f"{nd.x:.4f}_{nd.y:.4f}_{nd.z:.4f}"
            coord_map[key].append(nid)

        remap: Dict[str, str] = {}
        for key, ids in coord_map.items():
            if len(ids) < 2:
                continue
            survivor = None
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
                    continue
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

        for sid in sorted(area_ids, key=lambda x: int(x) if x.isdigit() else x):
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
                md.groups if hasattr(md, 'groups') else {},
                n_u=n, n_v=n,
                next_tag=next_tag,
            )
            md.area_elements.update(sub_areas)
            md.area_assignments.update(sub_assigns)
            md.nodes.update(sub_nodes)

    def _split_frames_at_shell_subdiv(self, md, frame_elements, frame_assignments,
                                       dist_loads, frame_element_types):
        """Split frame elements at shell subdivision edge nodes."""
        from ..model.geometry import point_on_segment, compute_t_location

        # Collect all sub-node coordinates from subdivided areas
        sub_nodes: Dict[str, Node] = {}
        for ae in md.area_elements.values():
            if not getattr(ae, 'inactive', False):
                for nid in ae.node_ids:
                    nd = md.nodes.get(nid)
                    if nd is not None:
                        sub_nodes[nid] = nd

        if not sub_nodes:
            return frame_elements, frame_assignments, dist_loads, frame_element_types

        # Build spatial index of sub-nodes
        sub_coords: Dict[str, tuple] = {
            nid: (nd.x, nd.y, nd.z) for nid, nd in sub_nodes.items()
        }

        new_frames: Dict[str, FrameElement] = {}
        new_assigns: Dict[str, str] = {}
        new_loads: list = []
        # Track new child types
        updated_types: Dict[str, str] = dict(frame_element_types)

        for eid, elem in frame_elements.items():
            if getattr(elem, 'inactive', False):
                continue

            ni = md.nodes.get(elem.node_i)
            nj = md.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
                continue

            p1 = (ni.x, ni.y, ni.z)
            p2 = (nj.x, nj.y, nj.z)

            # Find sub-nodes on this segment
            t_values: List[float] = []
            for snid, scoord in sub_coords.items():
                if point_on_segment(p1, p2, scoord, tol=1e-4):
                    t = compute_t_location(p1, p2, scoord)
                    if 0 < t < 1:
                        t_values.append((t, snid))

            if not t_values:
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
                continue

            t_values.sort(key=lambda x: x[0])

            # Collect child element IDs
            child_ids: List[str] = []
            prev_nid = elem.node_i
            for t, snid in t_values:
                child_id = f"{eid}_{snid}"
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
            child_id = f"{eid}_last"
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

            # Redistribute distributed loads
            elem_loads = [ld for ld in dist_loads if ld.frame_id == eid]
            for ld in elem_loads:
                span = ld.rdist_b - ld.rdist_a
                for i, cid in enumerate(child_ids):
                    child = new_frames[cid]
                    seg_len = 1.0 / len(child_ids)
                    a_local = i * seg_len
                    b_local = (i + 1) * seg_len
                    new_ld = FrameDistributedLoad(
                        frame_id=cid,
                        pattern=ld.pattern,
                        direction=ld.direction,
                        val_a=ld.val_a,
                        val_b=ld.val_b,
                        rdist_a=max(0.0, ld.rdist_a + a_local * span),
                        rdist_b=min(1.0, ld.rdist_a + b_local * span),
                        coord_sys=ld.coord_sys,
                    )
                    new_loads.append(new_ld)

            # Mark original as inactive
            elem.inactive = True

        # Include non-split frames and remaining loads
        for eid, elem in frame_elements.items():
            if not getattr(elem, 'inactive', False):
                new_frames[eid] = elem
                new_assigns[eid] = frame_assignments.get(eid, '')
        for ld in dist_loads:
            if ld.frame_id not in {l.frame_id for l in new_loads}:
                new_loads.append(ld)

        return new_frames, new_assigns, new_loads, updated_types

    def _detect_constraint_edges(self, md, frame_elements, frame_assignments) -> list:
        """Detect constraint edges where coarse and fine meshes meet.

        Returns a list of raw edge findings from ``find_constraint_edges()``.
        """
        raw_edges = find_constraint_edges(
            md.area_elements, md.area_assignments, md.nodes,
            frame_elements=frame_elements,
            frame_assignments=frame_assignments,
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
