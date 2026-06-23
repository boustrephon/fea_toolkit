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
        }
            'simplify_distributed_loads': False,
        }
        for key, default in defaults.items():
            if key not in self.config:
                self.config[key] = default

    # -------------------------------------------------------------------------
    # Main build method
    # -------------------------------------------------------------------------
    def build(self) -> None:
        """Build the complete OpenSees model in memory."""
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
        self._create_loads()   # placeholder
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
        return vec_x, vec_y, vec_z

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

    def _create_loads(self) -> None:
        """Create load patterns and apply loads (joint and distributed).

        After this method runs, ``self.load_totals`` is populated with the
        summed applied loads per pattern, keyed by pattern name::

            {
                "DEAD":  {"fx": ..., "fy": ..., "fz": ..., "mx": ..., "my": ..., "mz": ...},
                "WIND":  { ... },
            }

        Forces are in model force units (N, kN, kip, etc.); moments in
        moment units (N·m, kN·m, etc.).  These can be compared with the
        reactions returned by :meth:`run_static_analysis`.
        """
        if self.config['verbose']:
            print("Creating loads...")

        # Accumulators keyed by pattern *name* (not tag).
        joint_load_totals: Dict[str, Dict[str, float]] = {}
        frame_load_totals: Dict[str, Dict[str, float]] = {}

        # Determine which distributed loads to use (split or original)
        dist_loads = (self.split_dist_loads if self.split_dist_loads is not None
                    else self.model.frame_dist_loads)

        # Build pattern tags (one per unique load pattern name)
        pattern_tags = {}
        for i, (pattern_name, pattern) in enumerate(self.model.load_patterns.items(), start=1):
            ops.timeSeries('Linear', i)
            ops.pattern('Plain', i, i)
            pattern_tags[pattern_name] = i
            if self.config['verbose']:
                print(f"  Pattern '{pattern_name}' (tag={i})")

        # ------------------------------------------------------------------
        # Joint loads
        # ------------------------------------------------------------------
        for jl in self.model.joint_loads:
            pat_tag = pattern_tags.get(jl.pattern)
            if pat_tag is None:
                continue
            node = self._node_tag_from_id(jl.node_id)
            if node is None:
                continue
            ops.load(node, jl.fx, jl.fy, jl.fz, jl.mx, jl.my, jl.mz)

            # Accumulate totals by pattern *name*
            pname = jl.pattern
            if pname not in joint_load_totals:
                joint_load_totals[pname] = {k: 0.0 for k in
                                            ('fx','fy','fz','mx','my','mz')}
            for key in ('fx', 'fy', 'fz', 'mx', 'my', 'mz'):
                joint_load_totals[pname][key] += getattr(jl, key)

            if self.config['verbose']:
                print(f"    Joint load ({pat_tag}): node {node}: "
                      f"{jl.fx:,.1f} | {jl.fy:,.1f} | {jl.fz:,.1f} | "
                      f"{jl.mx:,.1f} | {jl.my:,.1f} | {jl.mz:,.1f}")

        # ------------------------------------------------------------------
        # Frame distributed loads
        # ------------------------------------------------------------------
        # Build frame_tag_map if not already built (from _create_elements)
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
            pat_tag = pattern_tags.get(ld.pattern)
            if pat_tag is None:
                if self.config['verbose']:
                    print(f"  Warning: pattern '{ld.pattern}' not found for element load")
                continue
            elem_tag = get_elem_tag(ld.frame_id)
            if elem_tag is None:
                if self.config['verbose']:
                    print(f"  Warning: element '{ld.frame_id}' not found or inactive")
                continue

            # Get element object (for local axes)
            if self.split_elements:
                elem = self.split_elements.get(ld.frame_id)
            else:
                elem = self.model.frame_elements.get(ld.frame_id)
            if elem is None:
                continue

            # Compute local axes
            try:
                vec_x, vec_y, vec_z = self._get_local_axes(elem)
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: could not compute local axes for element {ld.frame_id}: {e}")
                continue

            # Determine global load direction vector
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

            # Project intensities onto local axes
            wx_a = ld.val_a * np.dot(global_dir, vec_x)
            wy_a = ld.val_a * np.dot(global_dir, vec_y)
            wz_a = ld.val_a * np.dot(global_dir, vec_z)
            wx_b = ld.val_b * np.dot(global_dir, vec_x)
            wy_b = ld.val_b * np.dot(global_dir, vec_y)
            wz_b = ld.val_b * np.dot(global_dir, vec_z)

            # Element length
            if self._node_tag_from_id:
                coords_i = ops.nodeCoord(self._node_tag_from_id(elem.node_i))
                coords_j = ops.nodeCoord(self._node_tag_from_id(elem.node_j))
            else:
                coords_i = ops.nodeCoord(self.model.nodes[elem.node_i].node_tag)
                coords_j = ops.nodeCoord(self.model.nodes[elem.node_j].node_tag)
            length = np.linalg.norm(np.array(coords_j) - np.array(coords_i))
            if length < 1e-12:
                continue

            # Relative positions, clamped
            aOverL = max(0.0, min(1.0, ld.rdist_a))
            bOverL = max(0.0, min(1.0, ld.rdist_b))
            load_l = ld.dist_b - ld.dist_a

            # Accumulate total force per pattern name (local components)
            pname = ld.pattern
            if pname not in frame_load_totals:
                frame_load_totals[pname] = {k: 0.0 for k in
                                            ('fx','fy','fz','mx','my','mz')}
            # Total force resultant in each local direction
            f_loc = {
                'fx': 0.5 * (wx_a + wx_b) * load_l,
                'fy': 0.5 * (wy_a + wy_b) * load_l,
                'fz': 0.5 * (wz_a + wz_b) * load_l,
            }
            # Compute approximate fixed-end moments for the record
            span = bOverL - aOverL
            if span > 1e-12 and abs(load_l) > 1e-12:
                f_loc['mx'] = 0.0   # axial load → no moment
                f_loc['my'] = (wy_a + wy_b) * 0.5 * span * load_l * load_l / 12.0
                f_loc['mz'] = (wz_a + wz_b) * 0.5 * span * load_l * load_l / 12.0
            else:
                f_loc['mx'] = f_loc['my'] = f_loc['mz'] = 0.0

            for key, val in f_loc.items():
                frame_load_totals[pname][key] += val

            if self.config['verbose']:
                print(f"    Frame load ({pat_tag}): element {elem_tag}, "
                      f"fx={f_loc['fx']:,.1f}, fy={f_loc['fy']:,.1f}, "
                      f"fz={f_loc['fz']:,.1f} | {ld.frame_id}")

            # Determine load shape and use appropriate eleLoad command
            elem_type = self.config['element_type'].lower()
            supports_trapezoidal = elem_type in ('elasticbeamcolumn', 'forcebeamcolumn')

            if ld.load_type == 'Force':
                is_uniform = ld.shape == 'Uniform' or abs(ld.val_a - ld.val_b) < 1e-6

                if is_uniform:
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                elif supports_trapezoidal:
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, aOverL, bOverL, wy_b, wz_b, wx_b)
                else:
                    span_frac = bOverL - aOverL
                    wy_avg = (wy_a + wy_b) * 0.5 * span_frac
                    wz_avg = (wz_a + wz_b) * 0.5 * span_frac
                    wx_avg = (wx_a + wx_b) * 0.5 * span_frac
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_avg, wz_avg, wx_avg)

            elif ld.load_type == 'Moment':
                if self.config['verbose']:
                    print("  Warning: moment distributed loads not yet supported")

        # ------------------------------------------------------------------
        # Merge joint and frame totals into public attribute
        # ------------------------------------------------------------------
        all_patterns = set(joint_load_totals) | set(frame_load_totals)
        self.load_totals: Dict[str, Dict[str, float]] = {}
        for pname in all_patterns:
            self.load_totals[pname] = {k: 0.0 for k in
                                       ('fx','fy','fz','mx','my','mz')}
            self.load_totals[pname].update(joint_load_totals.get(pname, {}))
            self.load_totals[pname].update(frame_load_totals.get(pname, {}))

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
                            ) -> Dict[str, Any]:
        """Run a linear static analysis and return results.

        Args:
            odb_tag: If > 0 and opstool is installed, also save results via
                     opstool for richer post‑processing.
            extract_reactions: If True, compute nodal reactions at restrained
                               nodes and include them in the returned dict.

        Returns:
            Dictionary with keys:

            - ``'nodal_displacements'`` — dict of ``{node_tag: (dx, dy, dz)}``
            - ``'nodal_reactions'`` (if ``extract_reactions``) — dict of
              ``{node_tag: (fx, fy, fz, mx, my, mz)}``.
            - ``'load_totals'`` — the applied load totals per pattern (useful
              for equilibrium checks).
        """
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
            for node_id in self.model.nodes:
                tag = int(node_id)
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
                    tag = int(node_id)
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
