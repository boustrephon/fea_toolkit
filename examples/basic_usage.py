#!/usr/bin/env python
"""Example: Parse a SAP2000 .S2K file, enrich sections, and print summary.

Run this script from the project root (where the 'fea_toolkit/' folder lives):
    $ python examples/basic_usage.py
"""

import sys
from pathlib import Path

# Add project root to Python path so that 'fea_toolkit' can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.sections import SectionLibrary
from fea_toolkit.opensees.builder import OpenSeesBuilder


def main():
    # Paths relative to project root (assuming script is run from there)
    project_root = Path(__file__).parent.parent
    s2k_file = project_root / "tests" / "fixtures" / "sample.s2k"
    section_db_path = project_root / "data" / "section_dict.pkl"

    # Check if files exist
    if not s2k_file.exists():
        print(f"Error: Sample file not found at {s2k_file}")
        print("Please create a sample.s2k file in tests/fixtures/")
        return
    if not section_db_path.exists():
        print(f"Error: Section database not found at {section_db_path}")
        print("Please place section_dict.pkl in the data/ folder at project root.")
        return

    # Parse the model
    parser = SAP2000Parser(s2k_file)
    parser.parse()
    model_data = parser.get_model_data()
    print(f"Model units: {model_data.units}")
    print(f"Nodes: {len(model_data.nodes)}")
    print(f"Frames: {len(model_data.frame_elements)}")
    print(f"Sections: {len(model_data.sections)}")

    # Enrich sections if database available
    if section_db_path.exists():
        section_db = SectionLibrary(section_db_path, target_units=model_data.units['L'])
        for sec in model_data.sections.values():
            section_db.enrich_section(sec)
            if sec.Z33:
                print(f"  {sec.name}: Z33 = {sec.Z33:.2e} (in {model_data.units}³)")
            else:
                print(f"  {sec.name}: no manufacturer data found")
    else:
        print("Skipping section enrichment (no database)")


    # Optionally build OpenSees model

    config = {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': True,
    }
    builder = OpenSeesBuilder(model_data, config)
    builder.build()
    
    split_model_path = s2k_file.with_suffix(".split.json")
    builder.export_split_model(split_model_path)

    odb_tag = 0
    results = builder.run_static_analysis(odb_tag=odb_tag)
    disp = results.get('nodal_displacements',{})
    unit_L = model_data.units['L']
    if unit_L == 'm':
        unit_L2, scale = 'mm', 1000.0
    elif unit_L == 'ft':
        unit_L2, scale = 'in', 12.0
    else:
        unit_L2, scale = unit_L, 1.0
    print("Displacements (first 5 nodes):")

    if disp is None:
        print("  No displacement data.")
    elif hasattr(disp, 'iterrows'):  # pandas DataFrame
        print(f'Data in dataframe, shape: {disp.shape}')
        for idx, row in disp.iterrows():
            if idx >= 5: 
                break
            node_tag = row.get('nodeTag', idx)
            dx = row.get('dx', 0.0)
            dy = row.get('dy', 0.0)
            dz = row.get('dz', 0.0)
            print(f"  Node {node_tag}: dx={dx:.3f} {unit_L}, dy={dy:.3f} {unit_L}, dz={dz:.3f} {unit_L}")
    elif odb_tag > 0 and isinstance(disp, dict):
        print(f'Data in dictionary, items: {len(disp.keys())}; length: {len(disp["data"])}')
        # _ = [print(f'*** {k} ***: {v}') for i, (k, v) in enumerate(disp.items()) if i < 10]
        # print()
        # _ = [print(f'=== {k} ===: {v}') for i, (k, v) in enumerate(disp['coords'].items()) if i < 10]
        # print()
        for i, (node_tag, d) in enumerate(zip(disp['coords']['nodeTags']['data'], disp['data'])):
            if i >= 5: 
                break
            if isinstance(d, dict):
                dx = d.get('dx', 0)
                dy = d.get('dy', 0)
                dz = d.get('dz', 0)
            elif isinstance(d, (tuple, list)):
                dx = d[0] if len(d) > 0 else 0
                dy = d[1] if len(d) > 1 else 0
                dz = d[2] if len(d) > 2 else 0
            else:
                continue
            print(f"  Node {node_tag:3d}: dx={dx * scale:6.1f} {unit_L2}, dy={dy * scale:6.1f} {unit_L2}, dz={dz * scale:6.1f} {unit_L2}")
            # print(f"  Node {node_tag}: dx={dx} | dy={dy} | dz={dz}")
    elif isinstance(disp, dict):
        print(f'Data in dictionary, items: {len(disp.keys())}')
        print("Displacements (first 5 nodes):")
        for i, (node_tag, (dx, dy, dz)) in enumerate(disp.items()):
            if i >= 5: 
                break
            print(f"  Node {node_tag:3d}: dx={dx * scale:6.1f} {unit_L2}, dy={dy * scale:6.1f} {unit_L2}, dz={dz * scale:6.1f} {unit_L2}")
    else:
        print(f"  Unexpected displacement format: {type(disp)}")

    print("Results keys:", results.keys())

if __name__ == "__main__":
    main()

    import opstool.vis.pyvista as opsvis
    plotter = opsvis.plot_model(show_node_numbering=True, show_ele_numbering=True)
    plotter.show()
    plotter.close()

