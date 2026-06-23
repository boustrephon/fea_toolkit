# fea_toolkit/opensees/builder.py

"""Build an OpenSees model from SAPModelData.

    Examples of OpenSeesPy usage:
    https://github.com/AmirHosseinNamadchi/OpenSeesPy-Examples
"""

from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import json
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
# from ..model.geometry import split_elements


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
    def build(self, pattern_scales: Optional[Dict[str, float]] = None) -> None:
        """Build the complete OpenSees model in memory.

        Args:
            pattern_scales: Optional dict mapping pattern name → scale factor.
                If provided, only these patterns are created with the given
                scale.  If ``None`` (default), all patterns are applied with
                factor 1.0.
        """
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
            self.build(pattern_scales=pattern_scales)

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
