# fea_toolkit/opensees/builder.py

"""Build an OpenSees model from SAPModelData."""

from typing import Dict, Any, Optional, Tuple
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
from ..model.geometry import get_SAP_vecxz, rotate_about_axis
from ..model.sap_data import Section
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
        self.split_elements: Dict[str, Dict] = {}
        self.split_assignments: Dict[str, str] = {}
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
        for node_id, node in self.model.nodes.items():
            ops.node(int(node.id), node.x, node.y, node.z)

    # =========================================================================
    # Boundary conditions
    # =========================================================================
    def _apply_restraints(self) -> None:
        if self.config['verbose']:
            print("Applying restraints...")
        for node_id, restraint in self.model.restraints.items():
            ops.fix(int(node_id), *restraint.dofs[:6])

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
        # Placeholder: add logic later
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
            E_mod = mat.E_mod if hasattr(mat, 'E_mod') else mat.E
            G_mod = mat.G_mod if hasattr(mat, 'G_mod') else mat.G
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

        # Convert nodes to raw dict format expected by split_elements
        nodes_raw = {nid: {'x': node.x, 'y': node.y, 'z': node.z}
                     for nid, node in self.model.nodes.items()}
        # Convert frame_elements to raw dict
        elements_raw = {}
        for fid, fe in self.model.frame_elements.items():
            elements_raw[fid] = {
                'id': int(fe.id),
                'i': fe.node_i,
                'j': fe.node_j,
                'angle': fe.angle,
            }
        # Use auto_mesh (if present, otherwise empty)
        auto_mesh = getattr(self.model, 'frame_auto_mesh', {})
        # Call split_elements (currently only splits at joints)
        new_elements, new_assignments, _ = split_elements(
            nodes_raw, elements_raw,
            self.model.frame_assignments,
            {},   # dist_loads not yet implemented
            auto_mesh,
            verbose=self.config['verbose']
        )
        self.split_elements = new_elements
        self.split_assignments = new_assignments

    # -------------------------------------------------------------------------
    # Elements
    # -------------------------------------------------------------------------
    def _create_elements(self) -> None:
        """Create OpenSees frame elements, using split elements if available."""
        if self.config['verbose']:
            print("Creating elements...")

        # Choose source: split elements or original
        if self.split_elements:
            elements = self.split_elements
            assignments = self.split_assignments
        else:
            elements = self.model.frame_elements
            assignments = self.model.frame_assignments

        for elem_id, elem in elements.items():
            # Get section name from assignments
            sec_name = assignments.get(elem_id)
            if not sec_name or sec_name not in self.section_tags:
                if self.config['verbose']:
                    print(f"  Skipping element {elem_id}: no valid section")
                continue

            node_i = int(elem['i'])
            node_j = int(elem['j'])
            angle = elem.get('angle', 0.0)
            sec_tag = self.section_tags[sec_name]
            # Use element tag from elem['id']
            elem_tag = int(elem['id'])

            self._add_beam_column(node_i, node_j, sec_tag, elem_tag, angle)

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
    # Loads (placeholder)
    # -------------------------------------------------------------------------
    def _create_loads(self) -> None:
        """Create load patterns (currently placeholder)."""
        if self.config['verbose']:
            print("Creating loads... (gravity dummy)")
        
        unit_L = self.units['L']
        # unit_M = self.units['M']
        # unit_F = self.units['F']

        ops.timeSeries('Linear', 1)
        ops.pattern('Plain', 1, 1)
        # Apply self‑weight (acceleration = 9.81 m/s²) – convert units if needed
        gravity_dict = {
            'm': 9.81,    # m/s²
            'cm': 981.0,  # cm/s²
            'mm': 9810.0, # mm/s²
            'in': 32.2,   # in/s²
            'ft': 386.4   # ft/s²
        }
        g = gravity_dict[unit_L]
        for node_id in self.model.nodes.keys():
            ops.load(int(node_id), 0, 0, -g, 0, 0, 0)

        # Later: parse model_data.dist_loads, conc_loads, etc.
        # Example: create a dummy pattern for gravity
        # ops.timeSeries('Linear', 1)
        # ops.pattern('Plain', 1, 1)
        # ops.load(1, 0, 0, -9.81, 0, 0, 0)   # etc.
        pass

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
            "nodes": {nid: {"x": node.x, "y": node.y, "z": node.z} for nid, node in self.model.nodes.items()},
            "split_elements": self.split_elements,
            "split_assignments": self.split_assignments,
            "original_assignments": self.model.frame_assignments,
        }
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
