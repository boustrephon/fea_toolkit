# fea_toolkit/opensees/builder.py

"""Build an OpenSees model from SAPModelData."""

from typing import Dict, Any, Optional, List
import numpy as np

import openseespy.opensees as ops

from ..model.sap_data import SAPModelData
from ..model.geometry import get_SAP_vecxz, rotate_about_axis
from ..model.sap_data import Section
from ..model.geometry import split_elements

class OpenSeesBuilder:
    """Construct an OpenSees model from a SAPModelData instance.

    Usage:
        config = {
            'element_type': 'forceBeamColumn',   # 'elasticBeamColumn', 'dispBeamColumn', 'nonlinearBeamColumn'
            'num_int_pts': 3,
            'use_elastic_sections': True,
            'create_fiber_sections': False,
            'split_elements': True,
            'verbose': False,
        }
        builder = OpenSeesBuilder(model_data, config)
        builder.build()
        builder.write_script("output.tcl")
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
        self.config = config or {}
        self._set_defaults()
        self._transf_tags: Dict[int, int] = {}   # elem_id -> transf_tag

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

    # =========================================================================
    # Main build method
    # =========================================================================
    def build(self) -> None:
        """Build the complete OpenSees model in memory."""
        if self.config['verbose']:
            print("Building OpenSees model...")
            print(f"  Element type: {self.config['element_type']}")
            print(f"  Integration points: {self.config['num_int_pts']}")
            print(f"  Split elements: {self.config['split_elements']}")

        ops.wipe()
        ops.model('basic', '-ndm', 3, '-ndf', 6)

        self._create_nodes()
        self._apply_restraints()
        self._create_materials()
        self._create_sections()

        # After nodes are created, but before building elements
        if self.config['split_elements']:
            # We need the raw node coordinates; convert from Node objects to dict
            nodes_raw = {nid: {'x': node.x, 'y': node.y, 'z': node.z} for nid, node in self.model.nodes.items()}
            # Convert frame_elements to the expected dict format
            elements_raw = {}
            for fid, fe in self.model.frame_elements.items():
                elements_raw[fid] = {
                    'id': int(fe.id),
                    'i': fe.node_i,
                    'j': fe.node_j,
                    'angle': fe.angle,
                }
            auto_mesh = self.model.frame_auto_mesh
            new_elements, new_assignments, new_dist_loads = split_elements(
                nodes_raw, elements_raw, self.model.frame_assignments,
                {},   # dist_loads placeholder
                auto_mesh,
                verbose=self.config['verbose']
            )
            # Update the model data with split elements
            # This will affect how elements are created later
            # You can store these in the builder or modify self.model
            self.split_elements = new_elements   # store for later use
            self.split_assignments = new_assignments

        self._create_elements()
        self._create_loads()
        # Additional setup (analysis, recorders) can be added later

        if self.config['verbose']:
            print("Model building complete.")

    # =========================================================================
    # Node creation
    # =========================================================================
    def _create_nodes(self) -> None:
        """Create OpenSees nodes from model_data.nodes."""
        if self.config['verbose']:
            print("Creating nodes...")
        for node_id, node in self.model.nodes.items():
            try:
                ops.node(int(node.id), node.x, node.y, node.z)
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: Could not create node {node.id}: {e}")

    # =========================================================================
    # Boundary conditions
    # =========================================================================
    def _apply_restraints(self) -> None:
        """Apply fixities from model_data.restraints."""
        if self.config['verbose']:
            print("Applying restraints...")
        for node_id, restraint in self.model.restraints.items():
            try:
                ops.fix(int(node_id), *restraint.dofs[:6])
            except Exception as e:
                if self.config['verbose']:
                    print(f"  Warning: Could not fix node {node_id}: {e}")

    # =========================================================================
    # Materials (if needed for fiber sections, otherwise elastic sections use E,G)
    # =========================================================================
    def _create_materials(self) -> None:
        """Create OpenSees materials (steel, concrete) for nonlinear analysis."""
        if not self.config['create_fiber_sections']:
            return
        if self.config['verbose']:
            print("Creating materials...")
        # For each material in self.model.materials, create e.g.:
        # ops.uniaxialMaterial('Steel01', matTag, Fy, E, b)
        # Placeholder: add logic later
        pass

    # =========================================================================
    # Sections
    # =========================================================================
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
        """Create one OpenSees section."""
        if self.config['use_elastic_sections']:
            # Get material properties (E, G)
            mat = self.model.materials.get(sec.material)
            if mat is None:
                E = 2.1e11   # default steel
                G = 8.077e10
            else:
                E = mat.E_mod if mat.E_mod > 0 else 2.1e11
                G = mat.G_mod if mat.G_mod > 0 else E / (2 * (1 + (mat.nu if mat.nu > 0 else 0.3)))
            ops.section('Elastic', tag, E, sec.A, sec.I33, sec.I22, G, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic)")

        elif self.config['create_fiber_sections'] and sec.shape in ('Pipe', 'Box/Tube'):
            # Create fiber section (simplified)
            ops.section('Fiber', tag, '-GJ', sec.J)
            # Add fibers (placeholder)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Fiber) – not fully implemented")
        else:
            # Fallback to elastic
            mat = self.model.materials.get(sec.material)
            E = mat.E_mod if mat else 2.1e11
            G = mat.G_mod if mat else E / (2 * 0.3)
            ops.section('Elastic', tag, E, sec.A, sec.I33, sec.I22, G, sec.J)
            if self.config['verbose']:
                print(f"  Section {tag}: {sec.name} (Elastic fallback)")

    # =========================================================================
    # Elements
    # =========================================================================
    def _create_elements(self) -> None:
        """Create OpenSees frame elements."""
        if self.config['verbose']:
            print("Creating elements...")
        # Optional: split elements at intermediate nodes
        elements = self.model.frame_elements
        assignments = self.model.frame_assignments
        if self.config['split_elements']:
            # Use the geometry.split_elements function (to be implemented)
            # For now, assume elements are not split.
            # You will need to import split_elements from model.geometry
            pass

        for elem_id, elem in elements.items():
            sec_name = assignments.get(elem_id)
            if not sec_name or sec_name not in self.section_tags:
                if self.config['verbose']:
                    print(f"  Skipping element {elem_id}: no valid section")
                continue

            node_i = int(elem.node_i)
            node_j = int(elem.node_j)
            sec_tag = self.section_tags[sec_name]

            # Create geometric transformation and element
            self._add_beam_column(node_i, node_j, sec_tag, int(elem.id), elem.angle)

    def _add_beam_column(self, node_i: int, node_j: int, sec_tag: int, elem_tag: int, angle_deg: float) -> None:
        """Create a beam‑column element with appropriate geometric transformation."""
        # Get coordinates
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
            ops.element('elasticBeamColumn', elem_tag, node_i, node_j, sec_tag, transf_tag)
        elif elem_type == 'forcebeamcolumn':
            int_tag = elem_tag
            npts = self.config['num_int_pts']
            ops.beamIntegration('Lobatto', int_tag, sec_tag, npts)
            ops.element('forceBeamColumn', elem_tag, node_i, node_j, transf_tag, int_tag)
        elif elem_type == 'dispbeamcolumn':
            int_tag = elem_tag
            npts = self.config['num_int_pts']
            ops.beamIntegration('Lobatto', int_tag, sec_tag, npts)
            ops.element('dispBeamColumn', elem_tag, node_i, node_j, transf_tag, int_tag)
        elif elem_type == 'nonlinearbeamcolumn':
            npts = self.config['num_int_pts']
            ops.element('nonlinearBeamColumn', elem_tag, node_i, node_j, npts, sec_tag, transf_tag)
        else:
            raise ValueError(f"Unsupported element_type: {elem_type}")

        if self.config['verbose']:
            print(f"  Element {elem_tag}: {node_i} -> {node_j}")

    # =========================================================================
    # Loads (placeholders)
    # =========================================================================
    def _create_loads(self) -> None:
        """Create load patterns, nodal loads, element loads."""
        if self.config['verbose']:
            print("Creating loads... (placeholder)")
        # Later: parse model_data.dist_loads, conc_loads, etc.
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

    # =========================================================================
    # Analysis execution
    # =========================================================================
    def run_analysis(self) -> Dict[str, Any]:
        """Run the analysis and return results.

        Returns:
            Dictionary with results (displacements, forces, etc.).
        """
        if self.config['verbose']:
            print("Running analysis... (placeholder)")
        # Use opstool to extract results
        # For now, return empty dict
        return {}

