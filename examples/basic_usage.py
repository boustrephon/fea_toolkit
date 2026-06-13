#!/usr/bin/env python
"""Example: Parse a SAP2000 .S2K file, enrich sections, and print summary.

Run this script from the project root (where the 'fea_toolkit/' folder lives):
    $ python examples/basic_usage.py
"""

import sys
from pathlib import Path

# Add project root to Python path so that 'fea_toolkit' can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.sections import SectionLibrary


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

    # Enrich sections with manufacturer data
    section_db = SectionLibrary(section_db_path, target_units=model_data.units)
    for sec in model_data.sections.values():
        section_db.enrich_section(sec)
        if sec.Z33:
            print(f"  {sec.name}: Z33 = {sec.Z33:.2e} (in {model_data.units}³)")
        else:
            print(f"  {sec.name}: no manufacturer data found")


if __name__ == "__main__":
    main()

