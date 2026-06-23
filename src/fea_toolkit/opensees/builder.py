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
        # Get material properties, using E_mod, G_mod as per user's naming
        mat = self.model.materials.get(sec.material)
        if mat is None:
            E_mod = 2.1e11   # default steel in Pa
            G_mod = 8.077e10
        else:
            # E_mod = mat.E_mod if hasattr(mat, 'E_mod') else mat.E
            # G_mod = mat.G_mod if hasattr(mat, 'G_mod') else mat.G
            E_mod = mat.E_mod
            G_mod = mat.G_mod
            if G_mod == 0 and E_mod > 0:
                nu = mat.nu if mat.nu > 0 else 0.3
                G_mod = E_mod / (2 * (1 + nu))

        if self.config['use_elastic_sections']:
            ops.section('Elastic', tag, E_mod, sec.A, sec.I33, sec.I22, G_mod, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic)")
        elif self.config['create_fiber_sections'] and sec.shape in ('Pipe', 'Box/Tube'):
            # Simplified fiber section placeholder
            ops.section('Fiber', tag, '-GJ', sec.J)
            # Add fibers (placeholder)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Fiber) – not fully implemented")
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

        # Create geometric transformation (linear or PDelta)
        transf_tag = elem_tag
        ops.geomTransf('Linear', transf_tag, *vecxz)

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
        """Create load patterns and apply loads (joint and distributed)."""
        if self.config['verbose']:
            print("Creating loads...")
        joint_load_sums: Dict[int, Dict[str, float]] = {}
        frame_load_sums: Dict[int, Dict[str, float]] = {}

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

        # Apply joint loads
        for jl in self.model.joint_loads:
            pat_tag = pattern_tags.get(jl.pattern)
            if pat_tag is None:
                continue
            # Convert string node ID to numeric tag
            node = self._node_tag_from_id(jl.node_id)
            if node is None:
                continue
            ops.load(node, jl.fx, jl.fy, jl.fz, jl.mx, jl.my, jl.mz)
            
            if self.config['verbose']:
                load_sum = joint_load_sums.get(pat_tag,{})
                for key in ('fx', 'fy', 'fz', 'mx', 'my', 'mz'):
                    load_sum[key] = load_sum.get(key,0.0) + getattr(jl, key)
                joint_load_sums[pat_tag] = load_sum
                print(f"    Joint load ({pat_tag}): node {node}: {jl.fx:,.1f} | {jl.fy:,.1f} | {jl.fz:,.1f} | {jl.mx:,.1f} | {jl.my:,.1f} | {jl.mz:,.1f}")
        
        if self.config['verbose']:
            for pat_tag, load_sum in joint_load_sums.items():
                print(f"  Pattern {pat_tag}: {' | '.join([f'{key} = {val:,.1f}' for key, val in load_sum.items()])}")

        # Helper to resolve string frame ID to numeric tag

        # Build frame_tag_map if not already built (from _create_elements)
        if not hasattr(self, 'frame_tag_map'):
            elements = self.split_elements if self.split_elements else self.model.frame_elements
            self.frame_tag_map = {
                eid: elem.elem_tag
                for eid, elem in elements.items()
                if not elem.inactive
            }

        # def get_elem_tag(frame_id: str) -> Optional[int]:
        #     # If split elements exist, use them; else original
        #     if self.split_elements:
        #         elem = self.split_elements.get(frame_id)
        #         if elem and not elem.inactive:
        #             return elem.elem_tag
        #     else:
        #         elem = self.model.frame_elements.get(frame_id)
        #         if elem:
        #             return elem.elem_tag
        #     return None

        def get_elem_tag(frame_id: str) -> Optional[int]:
            return self.frame_tag_map.get(frame_id)

        # Apply distributed frame loads
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
                # Global Z downward (force is positive downward in SAP2000)
                # For gravity, load value is positive downward (negative Z)
                global_dir = np.array([0.0, 0.0, -1.0])
            elif ld.direction == 'X':
                global_dir = np.array([1.0, 0.0, 0.0])
            elif ld.direction == 'Y':
                global_dir = np.array([0.0, 1.0, 0.0])
            elif ld.direction == 'Z':
                global_dir = np.array([0.0, 0.0, 1.0])
            else:
                # Unknown direction – default to gravity
                global_dir = np.array([0.0, 0.0, -1.0])

            # For uniform/linear loads, the load intensity is force per length along the element.
            # We need components along local y and z.
            # The load vector is global_dir * intensity (but intensity is given as val_a, val_b)
            # We'll compute the components at start and end.
            # For simplicity, we'll assume the load direction is constant along the element.
            # So we project global_dir onto local y and z.
            wx_a = ld.val_a * np.dot(global_dir, vec_x)
            wy_a = ld.val_a * np.dot(global_dir, vec_y)
            wz_a = ld.val_a * np.dot(global_dir, vec_z)
            wx_b = ld.val_b * np.dot(global_dir, vec_x)
            wy_b = ld.val_b * np.dot(global_dir, vec_y)
            wz_b = ld.val_b * np.dot(global_dir, vec_z)

            # Normalize the load span (aOverL, bOverL) – for full length loads they are 0 and 1.
            # The dist_a and dist_b are absolute distances from start, but we need fractions.
            # We have the element length from the nodes.
            if self._node_tag_from_id: 
                coords_i = ops.nodeCoord(self._node_tag_from_id(elem.node_i))
                coords_j = ops.nodeCoord(self._node_tag_from_id(elem.node_j))
            else:
                coords_i = ops.nodeCoord(self.model.nodes[elem.node_i].node_tag)
                coords_j = ops.nodeCoord(self.model.nodes[elem.node_j].node_tag)
            length = np.linalg.norm(np.array(coords_j) - np.array(coords_i))
            if length < 1e-12:
                continue
            # aOverL = ld.dist_a / length
            # bOverL = ld.dist_b / length
            aOverL = ld.rdist_a
            bOverL = ld.rdist_b
            # Clamp to [0,1]
            aOverL = max(0.0, min(1.0, aOverL))
            bOverL = max(0.0, min(1.0, bOverL))

            load_l = ld.dist_b - ld.dist_a
            load_dict = {'X': 0.5 *(wx_a + wx_b) * load_l, 'Y': 0.5 * (wy_a + wy_b) * load_l, 'Z': 0.5 * (wz_a + wz_b) * load_l}
            
            if self.config['verbose']:
                frame_load_ptn_sums = frame_load_sums.get(pat_tag, {})
                for key, value in load_dict.items():
                    frame_load_ptn_sums[key] = frame_load_ptn_sums.get(key, 0.0) + value
                frame_load_sums[pat_tag] = frame_load_ptn_sums
                

            # Determine load shape and use appropriate eleLoad command.
            # elasticBeamColumn and forceBeamColumn support the 8-argument
            # trapezoidal form (wy1, wz1, wx1, aOverL, bOverL, wy2, wz2, wx2).
            # dispBeamColumn and nonlinearBeamColumn only support the 3-argument
            # uniform form (wy, wz, wx), so trapezoidal loads must be decomposed.
            elem_type = self.config['element_type'].lower()
            supports_trapezoidal = elem_type in ('elasticbeamcolumn', 'forcebeamcolumn')

            if ld.load_type == 'Force':
                is_uniform = ld.shape == 'Uniform' or abs(ld.val_a - ld.val_b) < 1e-6

                if is_uniform:
                    # Uniform: use 3‑argument form (Wy, Wz, Wx)
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a)
                    if self.config['verbose']:
                        print(f"    Uniform load ({pat_tag}): element {elem_tag}, Wy={wy_a:.3f}, Wz={wz_a:.3f}, Wx={wx_a:.3f} | {ld.frame_id}")

                elif supports_trapezoidal:
                    # Linear or trapezoidal: use 8‑argument form
                    # Args: Wy1 Wz1 Wx1 aOverL bOverL Wy2 Wz2 Wx2
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_a, wz_a, wx_a, aOverL, bOverL, wy_b, wz_b, wx_b)
                    if self.config['verbose']:
                        print(f"    Linear/Trapezoidal load ({pat_tag}): element {elem_tag}, "
                              f"Wy1={wy_a:.3f}, Wz1={wz_a:.3f}, Wx1={wx_a:.3f}, "
                              f"Wy2={wy_b:.3f}, Wz2={wz_b:.3f}, Wx2={wx_b:.3f} | {ld.frame_id}")

                else:
                    # dispBeamColumn / nonlinearBeamColumn: only support uniform
                    # loads over the full element span [0, 1]. To conserve the
                    # total force resultant, scale the average intensity by the
                    # fraction of the element that is actually loaded.
                    span_frac = bOverL - aOverL
                    wy_avg = (wy_a + wy_b) * 0.5 * span_frac
                    wz_avg = (wz_a + wz_b) * 0.5 * span_frac
                    wx_avg = (wx_a + wx_b) * 0.5 * span_frac
                    ops.eleLoad('-ele', elem_tag, '-type', '-beamUniform',
                                wy_avg, wz_avg, wx_avg)
                    if self.config['verbose']:
                        print(f"    Trapezoidal load decomposed to uniform ({pat_tag}): "
                              f"element {elem_tag}, Wy_avg={wy_avg:.3f}, "
                              f"Wz_avg={wz_avg:.3f}, Wx_avg={wx_avg:.3f}, "
                              f"span_frac={span_frac:.3f} | {ld.frame_id}")
            elif ld.load_type == 'Moment':
                # For moment loads, similar but with Mx, My, Mz – not implemented here
                if self.config['verbose']:
                    print("  Warning: moment distributed loads not yet supported")
            else:
                if self.config['verbose']:
                    print(f"  Warning: unknown load type '{ld.load_type}'")

        if self.config['verbose']:
            for pat_tag, load_sum in frame_load_sums.items():
                print(f"  Pattern {pat_tag}: {' | '.join([f'{key} = {val:,.1f}' for key, val in load_sum.items()])}")


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
                            # load_combo: Optional[int] = None
                            ) -> Dict[str, Any]:
        """Run a linear static analysis and return results.

        Returns:
            Dictionary with nodal displacements, reaction forces, etc.
        """
        unit_L = self.units['L']
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
        results = {}
        if OPSTOOL_AVAILABLE and odb_tag > 0:
            # Use opstool for easy extraction
            opst.post.CreateODB(odb_tag=1)
            opst.post.save_model_data(odb_tag=1)
            nodes_df = opst.post.get_model_data(data_type='Nodal', odb_tag=1)
            if nodes_df is not None:
                results['nodal_displacements'] = nodes_df.to_dict()
        else:
            # Manual extraction: get nodal displacements
            displacements = {}
            for i, node_id in enumerate(self.model.nodes.keys()):
                disp_list = []
                tag = int(node_id)
                disp = ops.nodeDisp(tag)
                if isinstance(disp, np.ndarray):
                    disp_list = disp.tolist() if hasattr(disp, 'tolist') else disp
                elif isinstance(disp, list):
                    disp_list = disp
                # Ensure 3 components (dx, dy, dz)
                if self.config['verbose'] and i < 5:
                    print(f"Displacements ({unit_L}) for node {tag} (type: {type(disp)}): {disp_list}")
                if len(disp_list) >= 3:
                    displacements[tag] = (disp_list[0], disp_list[1], disp_list[2])
                else:
                    displacements[tag] = (disp_list[0], disp_list[1] if len(disp_list)>1 else 0.0, 0.0)
                results['nodal_displacements'] = displacements

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
