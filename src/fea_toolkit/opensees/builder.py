# fea_toolkit/opensees/builder.py

"""Build an OpenSees model from SAPModelData.

    Examples of OpenSeesPy usage:
    https://github.com/AmirHosseinNamadchi/OpenSeesPy-Examples
"""

from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import json
import math
import numpy as np

import openseespy.opensees as ops
try:
    import opstool as opst
    OPSTOOL_AVAILABLE = True
except ImportError:
    OPSTOOL_AVAILABLE = False

from ..model.sap_data import SAPModelData
from ..model.geometry import get_SAP_vecxz, global_to_local_distributed_load, rotate_about_axis
from ..model.sap_data import Section, FrameElement, FrameDistributedLoad
from ..model.sap_data import GravityLoad, AreaGravityLoad
from ..model.geometry import convert_area_loads_to_edge_loads
from ..model.selection import Selection


class OpenSeesBuilder:
    """Construct an OpenSees model from a SAPModelData instance.

    Usage:
        config = {
            'element_type': 'forceBeamColumn',
            'num_int_pts': 3,
            'use_elastic_sections': True,
            'create_fiber_sections': False,
            'split_elements': True,
            'geom_transf_type': 'Linear',   # 'Linear', 'PDelta', or 'Corotational'
            'verbose': False,
        }
        builder = OpenSeesBuilder(model_data, config)
        builder.build()
        # builder.write_script("output.tcl")
        results = builder.run_analysis()
    """

    def __init__(self, model_data: SAPModelData, config: Optional[Dict[str, Any]] = None):
        """Initialise the builder.

        Args:
            model_data: SAPModelData instance (from parser.get_model_data()).
            config: Dictionary with keys:
                - element_type (str): 'elasticBeamColumn', 'forceBeamColumn', 'dispBeamColumn', 'nonlinearBeamColumn'
                - num_int_pts (int): Number of integration points (default 3)
                - use_elastic_sections (bool): If True, create elastic sections (default True)
                - create_fiber_sections (bool): If True, create fiber sections (default False)
                - split_elements (bool): If True, split elements at intermediate nodes (default True)
                - geom_transf_type (str): Geometric transformation — 'Linear', 'PDelta', or 'Corotational'
                  (default 'Linear').  Note: 'Corotational' does NOT support eleLoad in 3D;
                  use beam_load_to_nodal_loads() in those cases (see fea_toolkit.model.geometry).
                - verbose (bool): Print progress (default False)
        """
        self.model = model_data
        self.units = model_data.units
        self.config = config or {}
        self._set_defaults()
        self.split_elements: Optional[Dict[str, FrameElement]] = None
        self.split_assignments: Optional[Dict[str, str]] = None
        self.split_dist_loads: Optional[List[FrameDistributedLoad]] = None
        # self._transf_tags: Dict[int, int] = {}   # elem_id -> transf_tag

    def _set_defaults(self) -> None:
        """Set default configuration values."""
        defaults = {
            'element_type': 'elasticBeamColumn',
            'num_int_pts': 3,
            'use_elastic_sections': True,
            'create_fiber_sections': False,
            'split_elements': True,
            'verbose': False,
            'geom_transf_type': 'Linear',
            'simplify_distributed_loads': False,
        }
        for key, default in defaults.items():
            if key not in self.config:
                self.config[key] = default

    # -------------------------------------------------------------------------
    # Main build method
    # -------------------------------------------------------------------------
    def build(self,
              pattern_scales: Optional[Dict[str, float]] = None,
              selection: Optional[Selection] = None,
              ) -> None:
        """Build the complete OpenSees model in memory.

        Args:
            pattern_scales: Optional dict mapping pattern name → scale factor.
                If provided, only these patterns are created with the given
                scale.  If ``None`` (default), all patterns are applied with
                factor 1.0.
            selection: Optional :class:`Selection` to control which area loads
                are converted to equivalent frame edge loads.  ``None`` means
                all area loads are converted.
        """
        # Persist selection so re-builds (e.g. from run_static_analysis)
        # don't lose it.
        if selection is not None:
            self._area_selection = selection

        if self.config['verbose']:
            print("Building OpenSees model...")
            print(f"  Element type: {self.config['element_type']}")
            # print(f"  Integration points: {self.config['num_int_pts']}")
            print(f"  Split elements: {self.config['split_elements']}")

        ops.wipe()
        ops.model('basic', '-ndm', 3, '-ndf', 6)

        self._create_nodes()
        self._apply_restraints()
        self._create_materials()
        self._create_sections()

        # Element splitting (if enabled)
        if self.config['split_elements']:
            self._split_elements()
        
        # Convert area uniform loads to equivalent frame edge loads
        self._convert_area_loads(selection=selection)

        self._create_elements()
        self._create_loads(pattern_scales=pattern_scales)
        self._setup_recorders()  # optional

        if self.config['verbose']:
            print("Model building complete.")

    # -------------------------------------------------------------------------
    # Node creation
    # -------------------------------------------------------------------------
    def _create_nodes(self) -> None:
        """Create OpenSees nodes from model_data.nodes."""
        if self.config['verbose']:
            print("Creating nodes...")
        for node in self.model.nodes.values():
            ops.node(node.node_tag, node.x, node.y, node.z)

    # =========================================================================
    # Boundary conditions
    # =========================================================================
    def _apply_restraints(self) -> None:
        if self.config['verbose']:
            print("Applying restraints...")
        for node_id, restraint in self.model.restraints.items():
            tag = self.model.nodes[node_id].node_tag
            ops.fix(tag, *restraint.dofs[:6])

    # -------------------------------------------------------------------------
    # Materials (placeholder)
    # -------------------------------------------------------------------------
    def _create_materials(self) -> None:
        """Create OpenSees materials (steel, concrete) for nonlinear analysis."""
        if not self.config['create_fiber_sections']:
            return
        if self.config['verbose']:
            print("Creating materials... (placeholder)")
        # For each material in self.model.materials, create e.g.:
        # ops.uniaxialMaterial('Steel01', matTag, Fy, E, b)
        # TODO: Placeholder: add logic later
        pass

    # -------------------------------------------------------------------------
    # Sections
    # -------------------------------------------------------------------------
    def _create_sections(self) -> None:
        """Create OpenSees sections (elastic or fiber)."""
        if self.config['verbose']:
            print("Creating sections...")
        # Map section name -> tag
        self.section_tags: Dict[str, int] = {}
        tag = 1
        for sec_name, sec in self.model.sections.items():
            self.section_tags[sec_name] = tag
            self._create_single_section(sec, tag)
            tag += 1

    def _create_single_section(self, sec: Section, tag: int) -> None:
        """Create one OpenSees section (elastic or fiber)."""
        # Get material properties
        mat = self.model.materials.get(sec.material)
        if mat is None:
            E_mod = 2.1e11   # default steel in Pa
            G_mod = 8.077e10
        else:
            E_mod = mat.E_mod
            G_mod = mat.G_mod
            if G_mod == 0 and E_mod > 0:
                nu = mat.nu if mat.nu > 0 else 0.3
                G_mod = E_mod / (2 * (1 + nu))

        if self.config['use_elastic_sections']:
            ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic)")

        elif self.config['create_fiber_sections']:
            ops.section('Fiber', tag, '-GJ', sec.J)
            try:
                patches = sec.to_fiber_patches(mat_tag=tag)
                for patch_args in patches:
                    ops.patch(*patch_args)
                if self.config['verbose']:
                    print(f"  Section {tag}: {sec.name} (Fiber, {len(patches)} patches)")
            except NotImplementedError as exc:
                if self.config['verbose']:
                    print(f"  Section {tag}: {sec.name} — {exc}")

        else:
            # Fallback to elastic
            ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic fallback)")

    # -------------------------------------------------------------------------
    # Element splitting
    # -------------------------------------------------------------------------
    def _split_elements(self) -> None:
        """Perform element splitting using geometry.split_elements."""
        from ..model.geometry import split_elements

        # Call split_elements with the model data
        new_elements, new_assignments, new_dist_loads = split_elements(
            self.model.nodes,
            self.model.frame_elements,
            self.model.frame_assignments,
            getattr(self.model, 'frame_dist_loads', []),   # pass the list of distributed loads
            getattr(self.model, 'frame_auto_mesh', {}),
            tol=1E-6,
            verbose=self.config['verbose']
        )
        self.split_elements = new_elements
        self.split_assignments = new_assignments
        self.split_dist_loads = new_dist_loads   # store for later use in _create_loads

    # -------------------------------------------------------------------------
    # Elements
    # -------------------------------------------------------------------------

    def _get_local_axes(self, elem: FrameElement) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return local y and z unit vectors for a frame element."""
        #  Could use lookup table - maybe faster
        if self._node_tag_from_id:
            coords_i = ops.nodeCoord(self._node_tag_from_id(elem.node_i))
            coords_j = ops.nodeCoord(self._node_tag_from_id(elem.node_j))
        else:
            coords_i = ops.nodeCoord(self.model.nodes[elem.node_i].node_tag)
            coords_j = ops.nodeCoord(self.model.nodes[elem.node_j].node_tag)
        vec_x = np.array(coords_j) - np.array(coords_i)
        length = np.linalg.norm(vec_x)
        if length < 1e-12:
            raise ValueError(f"Zero length element {elem.elem_id} (tag {elem.elem_tag}) between nodes {elem.node_i} and {elem.node_j}")
        vec_x_norm = vec_x / length
        vecxz = get_SAP_vecxz(vec_x_norm, elem.angle)
        # local z is vecxz
        vec_z = vecxz / np.linalg.norm(vecxz)
        # local y = cross(vec_z, vec_x)  (right‑handed)
        vec_y = np.cross(vec_z, vec_x_norm)
        vec_y = vec_y / np.linalg.norm(vec_y)
        return vec_x_norm, vec_y, vec_z

    def _global_to_local(self, elem: FrameElement, vec: np.ndarray) -> np.ndarray:
        """Transform a vector from global to local coordinates."""
        vec_x, vec_y, vec_z = self._get_local_axes(elem)
        
        # Create the transformation matrix (3x3)
        T = np.vstack([vec_x, vec_y, vec_z])
        
        # Use matrix multiplication (@ operator) to transform the vector
        return T @ vec

    def _create_elements(self) -> None:
        """Create OpenSees frame elements, using split elements if available."""
        if self.config['verbose']:
            print("Creating elements...")

        # Choose source
        if self.split_elements:
            elements = self.split_elements
            assignments = self.split_assignments
        else:
            elements = self.model.frame_elements
            assignments = self.model.frame_assignments

        # Build frame_tag_map for loads (and for element creation if needed)
        self.frame_tag_map = {
            eid: elem.elem_tag
            for eid, elem in elements.items()
            if not elem.inactive
        }

        for elem_id, elem in elements.items():
            if elem.inactive:
                continue
            
            # Get section name from assignments
            sec_name = '' if (assignments is None) else assignments.get(elem_id)
            if (not sec_name) or (sec_name not in self.section_tags):
                if self.config['verbose']:
                    print(f"  Skipping element {elem_id}: no valid section")
                continue
            if self._node_tag_from_id: 
                node_i_tag = self._node_tag_from_id(elem.node_i)
                node_j_tag = self._node_tag_from_id(elem.node_j)
            else:
                node_i_tag = self.model.nodes[elem.node_i].node_tag
                node_j_tag = self.model.nodes[elem.node_j].node_tag
            angle = elem.angle
            sec_tag = self.section_tags[sec_name]
            # Use element tag from elem['id']
            elem_tag = elem.elem_tag

            if (node_i_tag is not None) and (node_j_tag is not None):
                self._add_beam_column(node_i_tag, node_j_tag, sec_tag, elem_tag, angle)

    def _add_beam_column(self, node_i: int, node_j: int, sec_tag: int,
                         elem_tag: int, angle_deg: float) -> None:
        """Create a beam‑column element with geometric transformation."""

        coords_i = ops.nodeCoord(node_i)
        coords_j = ops.nodeCoord(node_j)
        vec_x = np.array(coords_j) - np.array(coords_i)
        length = np.linalg.norm(vec_x)
        if length < 1e-12:
            raise ValueError(f"Zero length element {elem_tag} between nodes {node_i} and {node_j}")

        # Determine orientation vector vecxz
        vec_x_norm = vec_x / length
        vecxz = get_SAP_vecxz(vec_x_norm, angle_deg)

        # Create geometric transformation
        transf_type = self.config.get('geom_transf_type', 'Linear')
        if transf_type == 'Corotational' and self.config['verbose']:
            print("  Warning: Corotational geomTransf does NOT support eleLoad "
                  "in 3D. Use beam_load_to_nodal_loads() instead "
                  "(see fea_toolkit.model.geometry).")
        transf_tag = elem_tag
        ops.geomTransf(transf_type, transf_tag, *vecxz)

        # Create element based on type
        elem_type = self.config['element_type'].lower()
        if elem_type == 'elasticbeamcolumn':
            ops.element('elasticBeamColumn', elem_tag, node_i, node_j,
                        sec_tag, transf_tag)
        elif elem_type == 'forcebeamcolumn':
            int_tag = elem_tag
            npts = self.config['num_int_pts']
            ops.beamIntegration('Lobatto', int_tag, sec_tag, npts)
            ops.element('forceBeamColumn', elem_tag, node_i, node_j,
                        transf_tag, int_tag)
        elif elem_type == 'dispbeamcolumn':
            int_tag = elem_tag
            npts = self.config['num_int_pts']
            ops.beamIntegration('Lobatto', int_tag, sec_tag, npts)
            ops.element('dispBeamColumn', elem_tag, node_i, node_j,
                        transf_tag, int_tag)
        elif elem_type == 'nonlinearbeamcolumn':
            npts = self.config['num_int_pts']
            ops.element('nonlinearBeamColumn', elem_tag, node_i, node_j,
                        npts, sec_tag, transf_tag)
        else:
            raise ValueError(f"Unsupported element_type: {elem_type}")

        if self.config['verbose']:
            print(f"  Element {elem_tag}: {node_i} -> {node_j}")

    # -------------------------------------------------------------------------
    # Loads
    # -------------------------------------------------------------------------
    def _node_tag_from_id(self, node_id: str) -> Optional[int]:
        """Return numeric tag for a node, or None if not found."""
        node = self.model.nodes.get(node_id)
        if node:
            return node.node_tag
        return None

    def _create_loads(self, pattern_scales: Optional[Dict[str, float]] = None) -> None:
        """Create load patterns and apply loads (joint and distributed).

        Args:
            pattern_scales: If provided, only create patterns listed in this
                dict, applying the given scale factor to every load in that
                pattern.  If ``None`` (default), all patterns are created with
                factor 1.0.
        """
        if self.config['verbose']:
            print("Creating loads...")

        # Resolve which patterns to activate
        all_patterns = self.model.load_patterns
        if pattern_scales is not None:
            active = {name: pattern_scales.get(name, 0.0)
                      for name in all_patterns if name in pattern_scales}
        else:
            active = {name: 1.0 for name in all_patterns}

        # Accumulators keyed by pattern *name*
        joint_load_totals: Dict[str, Dict[str, float]] = {}
        frame_load_totals: Dict[str, Dict[str, float]] = {}
        sw_load_totals: Dict[str, Dict[str, float]] = {}

        # Determine which distributed loads to use (split or original)
        dist_loads = (self.split_dist_loads if self.split_dist_loads is not None
                    else self.model.frame_dist_loads)

        # Merge in edge loads converted from area uniform loads
        edge_loads = getattr(self, 'edge_loads_from_areas', [])
        if edge_loads:
            dist_loads = list(dist_loads) + list(edge_loads)

        # Build pattern tags (one per unique load pattern name)
        pattern_tags = {}
        for i, (pattern_name, scale) in enumerate(active.items(), start=1):
            if abs(scale) < 1e-12:
                continue
            ops.timeSeries('Linear', i)
            ops.pattern('Plain', i, i)
            pattern_tags[pattern_name] = i
            if self.config['verbose']:
                print(f"  Pattern '{pattern_name}' (tag={i}, scale={scale})")

        # ------------------------------------------------------------------
        # Joint loads (scaled)
        # ------------------------------------------------------------------
        for jl in self.model.joint_loads:
            scale = active.get(jl.pattern, 0.0)
            if abs(scale) < 1e-12:
                continue
            pat_tag = pattern_tags.get(jl.pattern)
            if pat_tag is None:
                continue
            node = self._node_tag_from_id(jl.node_id)
            if node is None:
                continue
            ops.load(node, jl.fx * scale, jl.fy * scale, jl.fz * scale,
                     jl.mx * scale, jl.my * scale, jl.mz * scale)

            pname = jl.pattern
            if pname not in joint_load_totals:
                joint_load_totals[pname] = {k: 0.0 for k in
                                            ('fx','fy','fz','mx','my','mz')}
            for key in ('fx', 'fy', 'fz', 'mx', 'my', 'mz'):
                joint_load_totals[pname][key] += getattr(jl, key) * scale

            if self.config['verbose']:
                print(f"    Joint load ({pat_tag}): node {node}, scale={scale}: "
                      f"{jl.fx*scale:,.1f} | {jl.fy*scale:,.1f} | {jl.fz*scale:,.1f} | "
                      f"{jl.mx*scale:,.1f} | {jl.my*scale:,.1f} | {jl.mz*scale:,.1f}")

        # ------------------------------------------------------------------
        # Frame distributed loads (scaled)
        # ------------------------------------------------------------------
        if not hasattr(self, 'frame_tag_map'):
            elements = self.split_elements if self.split_elements else self.model.frame_elements
            self.frame_tag_map = {
                eid: elem.elem_tag
                for eid, elem in elements.items()
                if not elem.inactive
            }

        def get_elem_tag(frame_id: str) -> Optional[int]:
            return self.frame_tag_map.get(frame_id)

        for ld in dist_loads:
            scale = active.get(ld.pattern, 0.0)
            if abs(scale) < 1e-12:
                continue
            pat_tag = pattern_tags.get(ld.pattern)
            if pat_tag is None:
                continue
            elem_tag = get_elem_tag(ld.frame_id)
            if elem_tag is None:
                if self.config['verbose']:
                    print(f"  Warning: element '{ld.frame_id}' not found or inactive")
                continue

            if self.split_elements:
                elem = self.split_elements.get(ld.frame_id)
            else:
                elem = self.model.frame_elements.get(ld.frame_id)
            if elem is None:
                continue

            try:
                vec_x, vec_y, vec_z = self._get_local_axes(elem)
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: could not compute local axes for element {ld.frame_id}: {e}")
                continue

            if ld.direction == 'Gravity':
                global_dir = np.array([0.0, 0.0, -1.0])
            elif ld.direction == 'X':
                global_dir = np.array([1.0, 0.0, 0.0])
            elif ld.direction == 'Y':
                global_dir = np.array([0.0, 1.0, 0.0])
            elif ld.direction == 'Z':
                global_dir = np.array([0.0, 0.0, 1.0])
            elif ld.direction == 'LocalX':
                global_dir = vec_x
            elif ld.direction == 'LocalY':
                global_dir = vec_y
            elif ld.direction == 'LocalZ':
                global_dir = vec_z
            else:
                global_dir = np.array([0.0, 0.0, -1.0])

            wx_a = ld.val_a * scale * np.dot(global_dir, vec_x)
            wy_a = ld.val_a * scale * np.dot(global_dir, vec_y)
            wz_a = ld.val_a * scale * np.dot(global_dir, vec_z)
            wx_b = ld.val_b * scale * np.dot(global_dir, vec_x)
            wy_b = ld.val_b * scale * np.dot(global_dir, vec_y)
            wz_b = ld.val_b * scale * np.dot(global_dir, vec_z)

            if self._node_tag_from_id:
                coords_i = ops.nodeCoord(self._node_tag_from_id(elem.node_i))
                coords_j = ops.nodeCoord(self._node_tag_from_id(elem.node_j))
            else:
                coords_i = ops.nodeCoord(self.model.nodes[elem.node_i].node_tag)
                coords_j = ops.nodeCoord(self.model.nodes[elem.node_j].node_tag)
            length = np.linalg.norm(np.array(coords_j) - np.array(coords_i))
            if length < 1e-12:
                continue

            aOverL = max(0.0, min(1.0, ld.rdist_a))
            bOverL = max(0.0, min(1.0, ld.rdist_b))
            load_l = ld.dist_b - ld.dist_a

            # Accumulate totals in GLOBAL coordinates
            pname = ld.pattern
            if pname not in frame_load_totals:
                frame_load_totals[pname] = {k: 0.0 for k in
                                            ('fx','fy','fz','mx','my','mz')}
            # Build local-to-global transformation matrix
            T = np.column_stack([vec_x, vec_y, vec_z])
            # Total force in local coordinates
            f_local = np.array([
                0.5 * (wx_a + wx_b) * load_l,
                0.5 * (wy_a + wy_b) * load_l,
                0.5 * (wz_a + wz_b) * load_l,
            ])
            f_global = T @ f_local
            f_loc = {
                'fx': f_global[0], 'fy': f_global[1], 'fz': f_global[2],
            }
            # Approximate fixed-end moments in local coordinates
            span = bOverL - aOverL
            if span > 1e-12 and abs(load_l) > 1e-12:
                m_local = np.array([
                    0.0,
                    (wy_a + wy_b) * 0.5 * span * load_l * load_l / 12.0,
                    (wz_a + wz_b) * 0.5 * span * load_l * load_l / 12.0,
                ])
            else:
                m_local = np.zeros(3)
            m_global = T @ m_local
            f_loc.update({'mx': m_global[0], 'my': m_global[1], 'mz': m_global[2]})

            for key, val in f_loc.items():
                frame_load_totals[pname][key] += val

            if self.config['verbose']:
                print(f"    Frame load ({pat_tag}): element {elem_tag}, "
                      f"fx={f_loc['fx']:,.1f}, fy={f_loc['fy']:,.1f}, "
                      f"fz={f_loc['fz']:,.1f} | {ld.frame_id}")

            # Apply eleLoad
            # NOTE: The 8‑argument form (wy1, wz1, wx1, aL, bL, wy2, wz2, wx2)
            # is broken in OpenSeesPy 3.8.0.0 — the end values (wy2 etc.) are
            # silently ignored.  We therefore decompose non‑uniform loads into
            # N partial‑span uniform segments using the working 5‑argument form
            # (wy, wz, wx, aL, bL), which preserves both total force and moment
            # distribution.
            if ld.load_type == 'Force':
                is_uniform = abs(ld.val_a - ld.val_b) < 1e-6
                is_full_span = abs(aOverL) < 1e-12 and abs(bOverL - 1.0) < 1e-12

                if is_uniform and is_full_span:
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                elif is_uniform:
                    # Uniform on a partial span → 5‑argument form
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, aOverL, bOverL)
                else:
                    # Trapezoidal/linear → decompose into N partial‑span
                    # uniform segments using the working 5‑argument form.
                    # This preserves both the total force AND the moment
                    # distribution, unlike a single uniform average.
                    N = 4  # segments — more = better moment accuracy
                    span_frac = bOverL - aOverL
                    for i in range(N):
                        seg_a = aOverL + i * span_frac / N
                        seg_b = aOverL + (i + 1) * span_frac / N
                        # Mid‑point intensity of this segment
                        xi = (i + 0.5) / N
                        wy_mid = wy_a + (wy_b - wy_a) * xi
                        wz_mid = wz_a + (wz_b - wz_a) * xi
                        wx_mid = wx_a + (wx_b - wx_a) * xi
                        ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                    wy_mid, wz_mid, wx_mid, seg_a, seg_b)

            elif ld.load_type == 'Moment':
                if self.config['verbose']:
                    print("  Warning: moment distributed loads not yet supported")

        # ------------------------------------------------------------------
        # Self-weight for patterns with SelfWtMult != 0
        # ------------------------------------------------------------------
        def _add_sw(pname: str, node_tag: int, fz_val: float) -> None:
            """Apply a nodal load for self-weight and track it."""
            ops.load(node_tag, 0.0, 0.0, fz_val, 0.0, 0.0, 0.0)
            if pname not in sw_load_totals:
                sw_load_totals[pname] = {k: 0.0 for k in
                                         ('fx','fy','fz','mx','my','mz')}
            sw_load_totals[pname]['fz'] += fz_val

        for pname, scale in active.items():
            if abs(scale) < 1e-12:
                continue
            pat = all_patterns.get(pname)
            if pat is None or abs(pat.self_weight_factor) < 1e-12:
                continue
            sw_factor = pat.self_weight_factor * scale

            if self.config['verbose']:
                print(f"  Self-weight for '{pname}' (factor={sw_factor:.4f})")

            elements = (self.split_elements if self.split_elements
                        else self.model.frame_elements)
            for eid, elem in elements.items():
                if elem.inactive:
                    continue
                sec_name = self.model.frame_assignments.get(eid)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue

                # Element length
                ni = self.model.nodes.get(elem.node_i)
                nj = self.model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                L = np.linalg.norm([
                    nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
                ])
                if L < 1e-12:
                    continue

                # Weight = volume × unit_weight × self_weight_factor
                weight = sec.A * mat.unit_weight * L * sw_factor

                # Half to each end node (gravity downward = negative Z)
                tag_i = self._node_tag_from_id(elem.node_i)
                tag_j = self._node_tag_from_id(elem.node_j)
                if tag_i is not None:
                    _add_sw(pname, tag_i, -weight * 0.5)
                if tag_j is not None:
                    _add_sw(pname, tag_j, -weight * 0.5)

            # ── Area element self-weight ──
            for aid, area_elem in self.model.area_elements.items():
                sec_name = self.model.area_assignments.get(aid)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue
                thickness = area_elem.thickness
                if thickness < 1e-12:
                    continue

                # Polygon area via Newell's method
                pts = []
                for nid in area_elem.node_ids:
                    nd = self.model.nodes.get(nid)
                    if nd is None:
                        break
                    pts.append((nd.x, nd.y, nd.z))
                if len(pts) < 3:
                    continue
                nx = ny = nz = 0.0
                for i in range(len(pts)):
                    x1, y1, z1 = pts[i]
                    x2, y2, z2 = pts[(i + 1) % len(pts)]
                    nx += (y1 - y2) * (z1 + z2)
                    ny += (z1 - z2) * (x1 + x2)
                    nz += (x1 - x2) * (y1 + y2)
                area_mag = 0.5 * np.sqrt(nx*nx + ny*ny + nz*nz)
                if area_mag < 1e-12:
                    continue

                # Self-weight (always downward = negative Z)
                fz_total = thickness * mat.unit_weight * area_mag * sw_factor
                n_corners = len(area_elem.node_ids)
                for nid in area_elem.node_ids:
                    tag = self._node_tag_from_id(nid)
                    if tag is not None:
                        _add_sw(pname, tag, -fz_total / n_corners)

        # ------------------------------------------------------------------
        # FRAME LOADS - GRAVITY (explicit multipliers on self-weight)
        # ------------------------------------------------------------------
        def _add_gravity(pname: str, node_tag: int, fx: float, fy: float, fz: float) -> None:
            """Apply a nodal force from a gravity load and track it."""
            ops.load(node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            if pname not in sw_load_totals:
                sw_load_totals[pname] = {k: 0.0 for k in
                                         ('fx','fy','fz','mx','my','mz')}
            sw_load_totals[pname]['fx'] += fx
            sw_load_totals[pname]['fy'] += fy
            sw_load_totals[pname]['fz'] += fz

        for pname, scale in active.items():
            if abs(scale) < 1e-12:
                continue
            if pname not in all_patterns:
                continue
            pat_tag = pattern_tags.get(pname)
            if pat_tag is None:
                continue

            # ── Frame gravity loads ──
            elements = (self.split_elements if self.split_elements
                        else self.model.frame_elements)
            for gl in self.model.frame_gravity_loads:
                if gl.pattern != pname:
                    continue
                elem = elements.get(gl.frame_id)
                if elem is None or elem.inactive:
                    continue
                sec_name = self.model.frame_assignments.get(gl.frame_id)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue

                ni = self.model.nodes.get(elem.node_i)
                nj = self.model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                L = np.linalg.norm([
                    nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
                ])
                if L < 1e-12:
                    continue

                # Force = volume × unit_weight × multiplier × scale
                sw_per_len = sec.A * mat.unit_weight
                fx = sw_per_len * L * gl.multiplier_x * scale * 0.5
                fy = sw_per_len * L * gl.multiplier_y * scale * 0.5
                fz = sw_per_len * L * gl.multiplier_z * scale * 0.5

                tag_i = self._node_tag_from_id(elem.node_i)
                tag_j = self._node_tag_from_id(elem.node_j)
                if tag_i is not None:
                    _add_gravity(pname, tag_i, fx, fy, fz)
                if tag_j is not None:
                    _add_gravity(pname, tag_j, fx, fy, fz)

            # ── Area gravity loads ──
            for agl in self.model.area_gravity_loads:
                if agl.pattern != pname:
                    continue
                area_elem = self.model.area_elements.get(agl.area_id)
                if area_elem is None:
                    continue

                # Get section + material for density
                sec_name = self.model.area_assignments.get(agl.area_id)
                if not sec_name:
                    continue
                sec = self.model.sections.get(sec_name)
                if sec is None:
                    continue
                mat = self.model.materials.get(sec.material)
                if mat is None or abs(mat.unit_weight) < 1e-12:
                    continue
                thickness = area_elem.thickness
                if thickness < 1e-12:
                    continue

                # Compute polygon area (shoelace formula on XY projection)
                pts = []
                for nid in area_elem.node_ids:
                    nd = self.model.nodes.get(nid)
                    if nd is None:
                        break
                    pts.append((nd.x, nd.y, nd.z))
                if len(pts) < 3:
                    continue

                # 3D polygon area via Newell's method
                nx = ny = nz = 0.0
                for i in range(len(pts)):
                    x1, y1, z1 = pts[i]
                    x2, y2, z2 = pts[(i + 1) % len(pts)]
                    nx += (y1 - y2) * (z1 + z2)
                    ny += (z1 - z2) * (x1 + x2)
                    nz += (x1 - x2) * (y1 + y2)
                area_mag = 0.5 * np.sqrt(nx*nx + ny*ny + nz*nz)
                if area_mag < 1e-12:
                    continue

                # Force = area × thickness × unit_weight × multiplier × scale
                sw_per_area = thickness * mat.unit_weight
                total_fx = sw_per_area * area_mag * agl.multiplier_x * scale
                total_fy = sw_per_area * area_mag * agl.multiplier_y * scale
                total_fz = sw_per_area * area_mag * agl.multiplier_z * scale

                n_corners = len(area_elem.node_ids)
                for nid in area_elem.node_ids:
                    tag = self._node_tag_from_id(nid)
                    if tag is not None:
                        _add_gravity(pname, tag,
                                     total_fx / n_corners,
                                     total_fy / n_corners,
                                     total_fz / n_corners)

        # ------------------------------------------------------------------
        # Merge all totals into public attribute
        # ------------------------------------------------------------------
        all_ptns = set(joint_load_totals) | set(frame_load_totals) | set(sw_load_totals)
        self.load_totals: Dict[str, Dict[str, float]] = {}
        for pname in all_ptns:
            self.load_totals[pname] = {k: 0.0 for k in
                                       ('fx','fy','fz','mx','my','mz')}
            for key in self.load_totals[pname]:
                self.load_totals[pname][key] += joint_load_totals.get(pname, {}).get(key, 0.0)
                self.load_totals[pname][key] += frame_load_totals.get(pname, {}).get(key, 0.0)
                self.load_totals[pname][key] += sw_load_totals.get(pname, {}).get(key, 0.0)

        if self.config['verbose']:
            print("\n  --- Load totals per pattern ---")
            for pname, totals in self.load_totals.items():
                parts = [f"{k} = {v:,.1f}" for k, v in totals.items()]
                print(f"  {pname}: {' | '.join(parts)}")
            print()


    # =========================================================================
    # Script writing
    # =========================================================================
    def write_script(self, file_path: str) -> None:
        """Write the OpenSees model as a Tcl/Python script.

        Note: This is a placeholder – you can implement by serialising
        the operations or using opstool's export functionality.
        """
        if self.config['verbose']:
            print(f"Writing script to {file_path}... (placeholder)")

    # -------------------------------------------------------------------------
    # Recorders
    # -------------------------------------------------------------------------
    def _setup_recorders(self) -> None:
        """Set up recorders for output (optional)."""
        if self.config['verbose']:
            print("Setting up recorders... (optional)")
        # ops.recorder('Node', '-file', 'displacements.out', '-node', 1, '-dof', 1, 2, 3, 'disp')

    # -------------------------------------------------------------------------
    # Area load → edge load conversion
    # -------------------------------------------------------------------------
    def _convert_area_loads(self,
                            selection: Optional[Selection] = None,
                            ) -> None:
        """Convert area uniform loads to equivalent frame edge loads.

        Args:
            selection: Optional :class:`Selection` to restrict which areas
                are converted.  Only area uniform loads on areas matching
                the selection will be converted.  ``None`` means all
                (unless a previous call persisted a selection via
                :attr:`_area_selection`).
        """
        # Fall back to persisted selection if none provided
        if selection is None:
            selection = getattr(self, '_area_selection', None)

        if not self.model.area_uniform_loads:
            self.edge_loads_from_areas = []
            return

        # Filter by selection if provided
        area_loads: List = self.model.area_uniform_loads
        area_elements = self.model.area_elements
        if selection is not None:
            sel_area_ids = set(selection.get_area_ids(self.model))
            area_loads = [ld for ld in area_loads if ld.area_id in sel_area_ids]
            area_elements = {
                aid: ae for aid, ae in area_elements.items()
                if aid in sel_area_ids
            }
            if not area_loads:
                if self.config['verbose']:
                    print("  No area uniform loads match the selection")
                self.edge_loads_from_areas = []
                return

        # Use split elements if available, else originals
        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)

        edge_loads = convert_area_loads_to_edge_loads(
            self.model.nodes,
            area_elements,
            elements,
            area_loads,
        )
        self.edge_loads_from_areas = edge_loads
        if self.config['verbose']:
            print(f"  Converted {len(area_loads)} area loads "
                  f"into {len(edge_loads)} frame edge loads")

    # -------------------------------------------------------------------------
    # Analysis
    # -------------------------------------------------------------------------
    def run_static_analysis(self, 
                            odb_tag:int = 0,
                            extract_reactions: bool = True,
                            pattern_scales: Optional[Dict[str, float]] = None,
                            ) -> Dict[str, Any]:
        """Run a linear static analysis and return results.

        Args:
            odb_tag: If > 0 and opstool is installed, also save results via
                     opstool for richer post‑processing.
            extract_reactions: If True, compute nodal reactions at restrained
                               nodes and include them in the returned dict.
            pattern_scales: Optional dict of ``{pattern_name: scale_factor}``.
                If provided, the model is rebuilt with only those patterns
                active at the given scales.  If ``None`` (default), the
                existing model (as built) is analysed.

        Returns:
            Dictionary with keys:

            - ``'nodal_displacements'`` — dict of ``{node_tag: (dx, dy, dz)}``
            - ``'nodal_reactions'`` (if ``extract_reactions``) — dict of
              ``{node_tag: (fx, fy, fz, mx, my, mz)}``.
            - ``'summed_reactions'`` — single summed force/moment vector
            - ``'load_totals'`` — the applied load totals per pattern
        """
        # Rebuild with different patterns if requested
        if pattern_scales is not None:
            # Re-use any persisted selection from the original build
            sel = getattr(self, '_area_selection', None)
            self.build(pattern_scales=pattern_scales, selection=sel)

        unit_L = self.units['L']
        unit_F = self.units.get('F', 'N')
        if self.config['verbose']:
            print("Running analysis...")

        # Define analysis parameters
        ops.constraints('Plain')
        ops.numberer('RCM')
        ops.system('BandGeneral')
        ops.test('NormDispIncr', 1e-6, 10)
        ops.algorithm('Newton')
        ops.integrator('LoadControl', 1.0)
        ops.analysis('Static')

        # Perform analysis
        ok = ops.analyze(1)
        if ok != 0:
            print("Analysis failed!")
            return {}

        # Extract results
        results: Dict[str, Any] = {}

        # --- Nodal displacements ---
        if OPSTOOL_AVAILABLE and odb_tag > 0:
            opst.post.CreateODB(odb_tag=1)
            opst.post.save_model_data(odb_tag=1)
            nodes_df = opst.post.get_model_data(data_type='Nodal', odb_tag=1)
            if nodes_df is not None:
                results['nodal_displacements'] = nodes_df.to_dict()
        else:
            displacements = {}
            for node_id, node in self.model.nodes.items():
                tag = node.node_tag
                try:
                    disp = ops.nodeDisp(tag)
                    if isinstance(disp, np.ndarray):
                        disp_list = disp.tolist()
                    elif isinstance(disp, (list, tuple)):
                        disp_list = list(disp)
                    elif isinstance(disp, (int, float)):
                        disp_list = [float(disp)]
                    else:
                        continue
                    # Pad to at least 3 components
                    while len(disp_list) < 3:
                        disp_list.append(0.0)
                    displacements[tag] = (disp_list[0], disp_list[1], disp_list[2])
                except Exception:
                    pass
            if displacements:
                results['nodal_displacements'] = displacements

        # --- Nodal reactions ---
        if extract_reactions:
            try:
                ops.reactions()
                reactions = {}
                for node_id, restraint in self.model.restraints.items():
                    node = self.model.nodes.get(node_id)
                    if node is None:
                        continue
                    tag = node.node_tag
                    rx = ops.nodeReaction(tag, 1) if restraint.dofs[0] else 0.0
                    ry = ops.nodeReaction(tag, 2) if restraint.dofs[1] else 0.0
                    rz = ops.nodeReaction(tag, 3) if restraint.dofs[2] else 0.0
                    rmx = ops.nodeReaction(tag, 4) if restraint.dofs[3] else 0.0
                    rmy = ops.nodeReaction(tag, 5) if restraint.dofs[4] else 0.0
                    rmz = ops.nodeReaction(tag, 6) if restraint.dofs[5] else 0.0
                    reactions[tag] = (rx, ry, rz, rmx, rmy, rmz)

                if reactions:
                    results['nodal_reactions'] = reactions
                    # Also compute summed reactions for equilibrium check
                    summed = {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
                              'mx': 0.0, 'my': 0.0, 'mz': 0.0}
                    for r in reactions.values():
                        summed['fx'] += r[0]
                        summed['fy'] += r[1]
                        summed['fz'] += r[2]
                        summed['mx'] += r[3]
                        summed['my'] += r[4]
                        summed['mz'] += r[5]
                    results['summed_reactions'] = summed
                    if self.config['verbose']:
                        print(f"\n  Summed reactions ({unit_F}, {unit_F}·{unit_L}):")
                        print(f"    Fx = {summed['fx']:+.3f}  Fy = {summed['fy']:+.3f}  Fz = {summed['fz']:+.3f}")
                        print(f"    Mx = {summed['mx']:+.3f}  My = {summed['my']:+.3f}  Mz = {summed['mz']:+.3f}")
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: could not extract reactions: {e}")

        # --- Applied load totals (for comparison) ---
        if hasattr(self, 'load_totals'):
            results['load_totals'] = self.load_totals
            if self.config['verbose']:
                unit_F = self.units.get('F', 'N')
                print(f"\n  Applied load totals per pattern ({unit_F}):")
                for pname, totals in self.load_totals.items():
                    print(f"    {pname}: Fx={totals['fx']:+.3f}  Fy={totals['fy']:+.3f}  Fz={totals['fz']:+.3f}")

        if self.config['verbose']:
            print("Analysis complete.")
        return results

    def export_split_model(self, output_path: Path) -> None:
        """Export split elements and assignments to a JSON file."""
        if not self.split_elements:
            print("No split elements available. Run build() with split_elements=True first.")
            return
        data = {
            "nodes": {nid: {"tag": node.node_tag, "x": node.x, "y": node.y, "z": node.z} for nid, node in self.model.nodes.items()},
            "split_elements": self.split_elements,
            "split_assignments": self.split_assignments,
            "original_assignments": self.model.frame_assignments,
            "split_loads": self.split_dist_loads,
        }
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # =========================================================================
    # Static element forces (after run_static_analysis)
    # =========================================================================
    def extract_static_element_forces(self) -> Dict[int, Dict[str, float]]:
        """Extract element end forces in the **global** coordinate system.

        Must be called **after** :meth:`run_static_analysis`.

        Returns:
            Dict mapping ``elem_tag`` → dict with keys ``'Fx'``, ``'Fy'``,
            ``'Fz'``, ``'Mx'``, ``'My'``, ``'Mz'`` (global forces at the
            I‑end of the element) and ``'Fx_j'``, ``'Fy_j'``, ``'Fz_j'``,
            ``'Mx_j'``, ``'My_j'``, ``'Mz_j'`` (J‑end).
        """
        elements = (self.split_elements if self.split_elements
                    else self.model.frame_elements)
        results = {}
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            tag = elem.elem_tag
            try:
                f = ops.eleResponse(tag, 'forces')
            except Exception:
                continue
            results[tag] = {
                'Fx': f[0], 'Fy': f[1], 'Fz': f[2],
                'Mx': f[3], 'My': f[4], 'Mz': f[5],
                'Fx_j': f[6], 'Fy_j': f[7], 'Fz_j': f[8],
                'Mx_j': f[9], 'My_j': f[10], 'Mz_j': f[11],
            }
        return results

    # =========================================================================
    # Seismic masses (based on MASS SOURCE definition)
    # =========================================================================
    def compute_seismic_masses(self, g: float = 9.81) -> Dict[str, float]:
        """Compute lumped nodal masses from the model's MASS SOURCE entries.

        The MASS SOURCE table in SAP2000 controls how masses are derived:

        * ``Elements=True``  — element self‑weight is converted to mass.
        * ``Masses=True``    — any explicit lumped masses are included
          (not yet supported — no SAP2000 example available).
        * ``Loads=True``     — loads from the specified load pattern(s) are
          converted to mass (total force / g).

        All mass contributions are lumped to nodes and assigned via
        ``ops.mass(node, m, m, m, 0, 0, 0)``.

        Args:
            g: Gravitational acceleration (m/s²).  Default 9.81.

        Returns:
            Dictionary mapping node ID → total lumped mass (tonnes).
        """
        if self.config['verbose']:
            print("Computing seismic masses from MASS SOURCE...")

        node_mass: Dict[str, float] = {}
        all_patterns = self.model.load_patterns
        all_materials = self.model.materials

        # Determine which elements / assignments to use (split or original)
        if self.split_elements:
            elements = self.split_elements
            assignments = self.split_assignments
            dist_loads = self.split_dist_loads
        else:
            elements = self.model.frame_elements
            assignments = self.model.frame_assignments
            dist_loads = self.model.frame_dist_loads

        for ms in self.model.mass_sources.values():
            # --- Element self‑weight mass ---
            if ms.elements:
                for eid, elem in elements.items():
                    if getattr(elem, 'inactive', False):
                        continue
                    sec_name = assignments.get(eid) if assignments else None
                    if not sec_name or sec_name not in self.model.sections:
                        continue
                    sec = self.model.sections[sec_name]
                    mat = all_materials.get(sec.material)
                    if mat is None or mat.unit_weight == 0:
                        continue
                    ni = self.model.nodes.get(elem.node_i)
                    nj = self.model.nodes.get(elem.node_j)
                    if ni is None or nj is None:
                        continue
                    L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                    if L < 1e-12:
                        continue
                    # Weight = volume × unit_weight
                    weight = sec.A * mat.unit_weight * L
                    mass = weight / g
                    node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
                    node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

            # --- Load‑based mass ---
            if ms.loads and ms.load_pattern:
                for lp_name, mult in ms.load_pattern.items():
                    if abs(mult) < 1e-12:
                        continue
                    # Distributed loads in this pattern → mass
                    for ld in dist_loads or []:
                        if ld.pattern != lp_name:
                            continue
                        elem = elements.get(ld.frame_id)
                        if elem is None or getattr(elem, 'inactive', False):
                            continue
                        ni = self.model.nodes.get(elem.node_i)
                        nj = self.model.nodes.get(elem.node_j)
                        if ni is None or nj is None:
                            continue
                        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                        if L < 1e-12:
                            continue
                        # Total load = average intensity × loaded length
                        load_len = ld.dist_b - ld.dist_a
                        avg = (ld.val_a + ld.val_b) * 0.5
                        total_force = avg * load_len * mult
                        mass = total_force / g
                        node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
                        node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

                    # Joint loads in this pattern → mass
                    for jl in self.model.joint_loads or []:
                        if jl.pattern != lp_name:
                            continue
                        # Use vertical component (F3) as the load magnitude
                        total_force = abs(jl.fz) * mult
                        mass = total_force / g
                        node_mass[jl.node_id] = node_mass.get(jl.node_id, 0.0) + mass

        # Assign masses to OpenSees nodes
        for nid, m in node_mass.items():
            tag = self._node_tag_from_id(nid)
            if tag is not None:
                if m > 0:
                    ops.mass(tag, m, m, m, 0, 0, 0)
                else:
                    ops.mass(tag, 1e-6, 1e-6, 1e-6, 0, 0, 0)

        self.node_masses = node_mass

        if self.config['verbose']:
            total = sum(node_mass.values())
            print(f"  Total seismic mass: {total:.2f} tonnes")
            print(f"  Total seismic weight: {total * g / 1000:.2f} MN")

        return node_mass

    # =========================================================================
    # Modal analysis
    # =========================================================================
    def run_modal_analysis(self, num_modes: int = 30,
                           print_results: bool = True) -> Dict[str, Any]:
        """Run eigenvalue / modal analysis and return results.

        Requires that seismic masses have been assigned (call
        :meth:`compute_seismic_masses` first) and the stiffness model has been
        built (call :meth:`build` first).

        Args:
            num_modes: Number of eigenvalues to solve for.
            print_results: If True, print a modal properties table.

        Returns:
            Dictionary with keys:

            * ``'eigenvalues'`` — list of eigenvalues (ω²).
            * ``'periods'`` — list of natural periods (s).
            * ``'frequencies'`` — list of natural frequencies (Hz).
            * ``'modal_props'`` — the full ``ops.modalProperties()`` dict.
            * ``'num_modes'`` — number of converged modes.
        """
        if self.config['verbose']:
            print(f"Running modal analysis for {num_modes} modes...")

        # Run eigenvalue analysis
        eigenvals_all = ops.eigen('-fullGenLapack', num_modes)
        eigenvals = [ev for ev in eigenvals_all if ev > 1e-12]
        n_modes = len(eigenvals)
        if n_modes < num_modes:
            if self.config['verbose']:
                print(f"  Warning: only {n_modes} positive eigenvalues out of "
                      f"{num_modes}.  Proceeding with {n_modes} modes.")

        periods = [2.0 * math.pi / math.sqrt(ev) for ev in eigenvals]
        frequencies = [math.sqrt(ev) / (2.0 * math.pi) for ev in eigenvals]

        # Get modal properties via built-in command
        try:
            modal_props = ops.modalProperties('-return', '-unorm')
        except Exception:
            modal_props = {}

        results = {
            'eigenvalues': eigenvals,
            'periods': periods,
            'frequencies': frequencies,
            'modal_props': modal_props,
            'num_modes': n_modes,
        }

        if print_results:
            print("\n===== MODAL ANALYSIS =====")
            # Try to get participation info from modalProperties
            if modal_props:
                try:
                    total_mass = modal_props.get('totalFreeMass', [0])[0]
                    print(f"Total translational mass (free DOFs): {total_mass:.2f} tonnes")
                    print()
                    header = (f"{'Mode':>5} {'Freq(Hz)':>10} {'Period(s)':>10} "
                              f"{'Mx(t)':>12} {'My(t)':>12} {'Mz(t)':>12} "
                              f"{'%X':>7} {'%Y':>7} {'%Z':>7}")
                    print(header)
                    print("-" * len(header))
                    for i in range(n_modes):
                        mx = modal_props.get('partiMassMX', [0]*n_modes)[i]
                        my = modal_props.get('partiMassMY', [0]*n_modes)[i]
                        mz = modal_props.get('partiMassMZ', [0]*n_modes)[i]
                        rx = modal_props.get('partiMassRatiosMX', [0]*n_modes)[i]
                        ry = modal_props.get('partiMassRatiosMY', [0]*n_modes)[i]
                        rz = modal_props.get('partiMassRatiosMZ', [0]*n_modes)[i]
                        print(f"{i+1:5d} {frequencies[i]:10.4f} {periods[i]:10.4f} "
                              f"{mx:12.2f} {my:12.2f} {mz:12.2f} "
                              f"{rx:6.2f}% {ry:6.2f}% {rz:6.2f}%")
                except Exception:
                    # Fallback if modalProperties keys not available
                    pass
            else:
                # Simple fallback
                print(f"{'Mode':>5} {'Period(s)':>10} {'Freq(Hz)':>10}")
                print("-" * 30)
                for i in range(n_modes):
                    print(f"{i+1:5d} {periods[i]:10.4f} {frequencies[i]:10.4f}")

            if periods:
                print(f"\nFirst 5 periods (s):")
                for i, T in enumerate(periods[:5]):
                    print(f"  Mode {i+1}: T = {T:.4f} s  f = {frequencies[i]:.4f} Hz")

        return results

    # =========================================================================
    # Response spectrum analysis
    # =========================================================================
    def run_response_spectrum_analysis(
        self,
        num_modes: int,
        modal_periods: List[float],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> Dict[str, Any]:
        """Run a response‑spectrum analysis using CQC modal combination.

        This performs a mode‑by‑mode response spectrum analysis using
        OpenSees' ``responseSpectrumAnalysis`` command, then combines
        results with the Complete Quadratic Combination (CQC) rule.

        Args:
            num_modes: Number of modes to include.
            modal_periods: Natural periods of each mode (s) — from
                           :meth:`run_modal_analysis`.
            spectrum_periods: Period axis of the response spectrum (s).
            spectrum_accels: Spectral acceleration values (in **m/s²**)
                             corresponding to ``spectrum_periods``.
            direction: Excitation direction — ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation (default 0.05).
            print_results: If True, print a summary table.

        Returns:
            Dictionary with keys:

            * ``'modal_base_shear'`` — list of base shear per mode (kN).
            * ``'base_shear_cqc'`` — CQC‑combined base shear (kN).
            * ``'base_shear_srss'`` — SRSS‑combined base shear (kN).
            * ``'modal_periods'`` — the input modal periods.
        """
        if self.config['verbose']:
            print(f"Running response spectrum analysis (dir={direction})...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        # Define the spectrum as a time series (use a high tag to avoid
        # conflicts with load‑pattern time series)
        SPECTRUM_TS_TAG = 9999
        ops.timeSeries('Path', SPECTRUM_TS_TAG,
                       '-time', *spectrum_periods,
                       '-values', *spectrum_accels)

        # Mode-by-mode analysis — extract base shear from element forces
        modal_base_shear = []
        modal_base_moment = []
        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]

        # Find base elements — those attached to fully‑fixed nodes
        fixed_nodes = {nid for nid, r in self.model.restraints.items()
                       if all(d == 1 for d in r.dofs)}

        # Determine which elements to use (split or original)
        if self.split_elements:
            elements = self.split_elements
        else:
            elements = self.model.frame_elements

        base_elements = []
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            if elem.node_i in fixed_nodes:
                base_elements.append((elem.elem_tag, 'i', elem.node_j))
            elif elem.node_j in fixed_nodes:
                base_elements.append((elem.elem_tag, 'j', elem.node_i))

        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, '-mode', mode)

            # Sum global forces at base element ends
            v_base = 0.0
            m_base = 0.0
            dof_map = {'X': (0, 4), 'Y': (1, 5), 'Z': (2, 3)}
            f_idx, m_idx = dof_map[direction]

            for eid, end, _ in base_elements:
                try:
                    # 'forces' returns global element forces
                    # [Fx,Fy,Fz,Mx,My,Mz] at I-end then J-end
                    forces = ops.eleResponse(eid, 'forces')
                except Exception:
                    continue
                if end == 'i':
                    v_base += forces[f_idx]
                    m_base += forces[m_idx]
                else:
                    v_base += forces[f_idx + 6]
                    m_base += forces[m_idx + 6]

            modal_base_shear.append(v_base)
            modal_base_moment.append(m_base)

        # CQC combination
        base_shear_cqc = self._cqc_combine(modal_base_shear, omega, damp_ratios)
        base_shear_srss = math.sqrt(sum(v * v for v in modal_base_shear))
        base_moment_cqc = self._cqc_combine(modal_base_moment, omega, damp_ratios)
        base_moment_srss = math.sqrt(sum(m * m for m in modal_base_moment))

        result = {
            'modal_base_shear': modal_base_shear,
            'modal_base_moment': modal_base_moment,
            'base_shear_cqc': base_shear_cqc,
            'base_shear_srss': base_shear_srss,
            'base_moment_cqc': base_moment_cqc,
            'base_moment_srss': base_moment_srss,
            'modal_periods': modal_periods,
        }

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM ({direction}) =====")
            print(f"{'Mode':>5} {'Period(s)':>10} {'Shear (kN)':>14} {'Moment (kN·m)':>16}")
            print("-" * 48)
            for i, (T, v, m) in enumerate(zip(modal_periods[:num_modes],
                                                modal_base_shear,
                                                modal_base_moment)):
                print(f"{i+1:5d} {T:10.4f} {v:14.2f} {m:16.2f}")
            print("-" * 48)
            print(f"{'CQC':>5} {'':>10} {base_shear_cqc:14.2f} {base_moment_cqc:16.2f}")
            print(f"{'SRSS':>5} {'':>10} {base_shear_srss:14.2f} {base_moment_srss:16.2f}")
            print()

        return result

    # -------------------------------------------------------------------------
    # CQC combination helper
    # -------------------------------------------------------------------------
    @staticmethod
    def _cqc_combine(modal_values: List[float],
                     omega: List[float],
                     damp_ratios: List[float]) -> float:
        """Complete Quadratic Combination of modal results."""
        n = len(modal_values)
        if n == 0:
            return 0.0
        if n == 1:
            return abs(modal_values[0])
        total = 0.0
        for i in range(n):
            for j in range(n):
                di = damp_ratios[i]
                dj = damp_ratios[j]
                bij = omega[i] / omega[j] if omega[j] > 0 else 1.0
                rho = (
                    8.0 * math.sqrt(di * dj) * (di + bij * dj) * (bij ** 1.5)
                ) / (
                    (1.0 - bij ** 2.0) ** 2.0
                    + 4.0 * di * dj * bij * (1.0 + bij ** 2.0)
                    + 4.0 * (di ** 2.0 + dj ** 2.0) * bij ** 2.0
                )
                total += modal_values[i] * modal_values[j] * rho
        return math.sqrt(total)

    # =========================================================================
    # Element-level RS forces (per element, CQC-combined)
    # =========================================================================
    def extract_element_rs_forces(
        self,
        num_modes: int,
        modal_periods: List[float],
        spectrum_periods: List[float],
        spectrum_accels: List[float],
        direction: str = 'X',
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> Dict[str, Any]:
        """Run RS analysis and return CQC‑combined element forces sorted by height.

        For each element this returns the CQC‑combined moments (My_i, My_j,
        Mz_i, Mz_j) and the corresponding shears derived from the moment
        gradient (Vy = dMz/dx, Vz = dMy/dx).

        Args:
            Same as :meth:`run_response_spectrum_analysis`.

        Returns:
            Dictionary with keys:

            * ``'element_results'`` — list of dicts sorted by elevation, each
              containing ``elem_id``, ``z_bot``, ``z_mid``, ``Vy_i``, ``Vy_j``,
              ``Vz_i``, ``Vz_j``, ``My_i``, ``My_j``, ``Mz_i``, ``Mz_j``.
            * ``'modal_periods'``, ``'omega'`` — for diagnostics.
        """
        if self.config['verbose']:
            print("Extracting element RS forces...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]

        # The spectrum time series must already exist (created by a prior
        # call to :meth:`run_response_spectrum_analysis`).
        SPECTRUM_TS_TAG = 9999

        # Determine elements (skip inactive)
        if self.split_elements:
            elements_dict = self.split_elements
        else:
            elements_dict = self.model.frame_elements

        # Pre-compute element info + storage
        elem_data = {}  # eid -> {z_bot, z_mid, My_i[], My_j[], Mz_i[], Mz_j[]}
        for eid, elem in elements_dict.items():
            if getattr(elem, 'inactive', False):
                continue
            ni = self.model.nodes.get(elem.node_i)
            nj = self.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            z_i, z_j = ni.z, nj.z
            if z_i > z_j:
                z_i, z_j = z_j, z_i
            elem_data[eid] = {
                'tag': elem.elem_tag,
                'z_bot': z_i,
                'z_mid': (z_i + z_j) * 0.5,
                'My_i': [], 'My_j': [], 'Mz_i': [], 'Mz_j': [],
            }

        # Mode-by-mode extraction
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, '-mode', mode)
            for eid, ed in elem_data.items():
                try:
                    forces = ops.eleResponse(ed['tag'], 'forces')
                except Exception:
                    forces = [0.0] * 12
                ed['My_i'].append(forces[4])
                ed['My_j'].append(forces[10])
                ed['Mz_i'].append(forces[5])
                ed['Mz_j'].append(forces[11])

        # CQC combine per element and compute shears
        element_results = []
        for eid, ed in elem_data.items():
            ne = len(ed['My_i'])
            # Only combine as many modes as we have data for
            n_use = min(ne, num_modes)
            o_use = omega[:n_use]
            d_use = damp_ratios[:n_use]

            My_i = self._cqc_combine(ed['My_i'][:n_use], o_use, d_use)
            My_j = self._cqc_combine(ed['My_j'][:n_use], o_use, d_use)
            Mz_i = self._cqc_combine(ed['Mz_i'][:n_use], o_use, d_use)
            Mz_j = self._cqc_combine(ed['Mz_j'][:n_use], o_use, d_use)

            # Element length
            elem = elements_dict.get(eid)
            if elem:
                ni = self.model.nodes.get(elem.node_i)
                nj = self.model.nodes.get(elem.node_j)
                if ni and nj:
                    L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
                else:
                    L = 1.0
            else:
                L = 1.0

            # Shear from moment gradient
            Vy_i = (Mz_i - Mz_j) / L if L > 1e-12 else 0.0
            Vy_j = Vy_i
            Vz_i = (My_i - My_j) / L if L > 1e-12 else 0.0
            Vz_j = Vz_i

            element_results.append({
                'elem_id': eid,
                'z_bot': ed['z_bot'],
                'z_mid': ed['z_mid'],
                'Vy_i': Vy_i, 'Vy_j': Vy_j,
                'Vz_i': Vz_i, 'Vz_j': Vz_j,
                'My_i': My_i, 'My_j': My_j,
                'Mz_i': Mz_i, 'Mz_j': Mz_j,
            })

        # Sort by height
        element_results.sort(key=lambda r: r['z_mid'])

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM RESULTS ({direction} only, CQC) FOR ALL ELEMENTS =====")
            header = (f"{'Elem':>30} {'Z_bot(m)':>10} {'Z_mid(m)':>10} {'End':>5} "
                      f"{'Vy (kN)':>12} {'Vz (kN)':>12} {'My (kN·m)':>12} {'Mz (kN·m)':>12}")
            print(header)
            print("-" * len(header))
            for r in element_results:
                eid_str = f"{r['elem_id']:30s}"
                print(f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'I':>5} "
                      f"{r['Vy_i']:12.2f} {r['Vz_i']:12.2f} {r['My_i']:12.2f} {r['Mz_i']:12.2f}")
                print(f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'J':>5} "
                      f"{r['Vy_j']:12.2f} {r['Vz_j']:12.2f} {r['My_j']:12.2f} {r['Mz_j']:12.2f}")

        return {
            'element_results': element_results,
            'modal_periods': modal_periods,
            'omega': omega,
        }

    # =========================================================================
    # RS nodal displacements (from mode‑shape combination)
    # =========================================================================
    def compute_rs_nodal_displacements(
        self,
        num_modes: int,
        modal_periods: List[float],
        eigenvalues: List[float],
        spectrum_func,
        direction: str = 'X',
        damping_ratio: float = 0.05,
    ) -> Dict[int, Tuple[float, float, float]]:
        """Compute CQC‑combined peak nodal displacements from RS analysis.

        Uses mode‑shape superposition rather than re‑running the RS analysis:

            u_m = Γ_m · φ_m · Sa_m / ω²_m

        then CQC across modes.

        Args:
            num_modes: Number of modes.
            modal_periods: Natural periods of each mode (s).
            eigenvalues: Eigenvalues (ω²) from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²**.
            direction: Excitation direction ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.

        Returns:
            Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model length units.
        """
        dof = {'X': 1, 'Y': 2, 'Z': 3}[direction]
        dof_idx = dof - 1  # 0‑based for the result tuple

        # Get participation factors from modalProperties
        try:
            mp = ops.modalProperties('-return', '-unorm')
        except Exception:
            mp = {}
        gamma_all = mp.get('partiFactorMX' if direction == 'X'
                           else 'partiFactorMY' if direction == 'Y'
                           else 'partiFactorMZ',
                           [0.0] * num_modes)

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp = [damping_ratio] * num_modes

        # Collect node tags
        node_tags = list(ops.getNodeTags())

        # Store per-mode displacement contributions: per_mode[tag][d] = [val_mode0, ...]
        per_mode = {tag: {d: [] for d in range(3)} for tag in node_tags}

        for m in range(num_modes):
            if eigenvalues[m] <= 1e-12 or omega[m] <= 1e-12:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            T = modal_periods[m]
            Sa = spectrum_func(T)           # m/s²
            factor = gamma_all[m] * Sa / (omega[m] ** 2)

            if abs(factor) < 1e-15:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            for tag in node_tags:
                phi = ops.nodeEigenvector(tag, m + 1, dof)
                per_mode[tag][dof_idx].append(phi * factor)
                # Off‑direction DOFs get zero (unidirectional excitation)
                for d in range(3):
                    if d != dof_idx:
                        per_mode[tag][d].append(0.0)

        # CQC combine per node
        cqc_result = {}
        for tag in node_tags:
            vals = tuple(
                self._cqc_combine(per_mode[tag][d], omega, damp)
                for d in range(3)
            )
            cqc_result[tag] = vals

        return cqc_result
    def add_missing_mass_correction(
        self,
        rs_results: Dict[str, Any],
        modal_results: Dict[str, Any],
        spectrum_func,
        g: float = 9.81,
        T_short: float = 0.01,
    ) -> Dict[str, Any]:
        """Compute missing mass (rigid) contribution to base shear and moment.

        The rigid response captures the portion of the total mass that is not
        activated by the computed modes (residual mass = total − ΣM_eff).

        Args:
            rs_results: Output from :meth:`run_response_spectrum_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²** (not g) for
                           a given period ``T`` (s).
            g: Gravitational acceleration (m/s²).
            T_short: Period at which to evaluate the rigid response (s).

        Returns:
            Dictionary with keys:

            * ``'V_missing_X'`` — missing base shear in X (kN).
            * ``'V_missing_Y'`` — missing base shear in Y (kN).
            * ``'M_missing_YY'`` — missing base moment about Y (kN·m).
            * ``'M_missing_XX'`` — missing base moment about X (kN·m).
            * ``'residual_mass_X'`` — residual mass in X (t).
            * ``'residual_mass_Y'`` — residual mass in Y (t).
            * ``'h_cm'`` — centre of mass height (m).
            * ``'Sa_short'`` — spectral acceleration at T_short (m/s²).
        """
        mp = modal_results.get('modal_props', {})
        total_free_mass = mp.get('totalFreeMass', [0.0])[0]

        # Sum effective modal masses from modal_props
        num_modes = modal_results['num_modes']
        sum_meff_X = sum(mp.get('partiMassMX', [0.0] * num_modes)[:num_modes])
        sum_meff_Y = sum(mp.get('partiMassMY', [0.0] * num_modes)[:num_modes])

        residual_mass_X = max(0.0, total_free_mass - sum_meff_X)
        residual_mass_Y = max(0.0, total_free_mass - sum_meff_Y)

        # Spectral acceleration at short period
        Sa_short = spectrum_func(T_short)

        # Missing base shear
        V_missing_X = residual_mass_X * Sa_short
        V_missing_Y = residual_mass_Y * Sa_short

        # Centre of mass height
        total_mass = 0.0
        total_mass_z = 0.0
        try:
            for node_tag in ops.getNodeTags():
                mass_x = ops.nodeMass(node_tag)[0]
                if mass_x > 0:
                    z = ops.nodeCoord(node_tag)[2]
                    total_mass += mass_x
                    total_mass_z += mass_x * z
        except Exception:
            pass
        h_cm = total_mass_z / total_mass if total_mass > 0 else 0.0

        # Missing base moment
        M_missing_YY = residual_mass_X * Sa_short * h_cm
        M_missing_XX = residual_mass_Y * Sa_short * h_cm

        return {
            'V_missing_X': V_missing_X,
            'V_missing_Y': V_missing_Y,
            'M_missing_YY': M_missing_YY,
            'M_missing_XX': M_missing_XX,
            'residual_mass_X': residual_mass_X,
            'residual_mass_Y': residual_mass_Y,
            'h_cm': h_cm,
            'T_short': T_short,
            'Sa_short': Sa_short,
        }
