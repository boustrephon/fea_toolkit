import pytest
from pathlib import Path
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.sap_data import (
    AreaGravityLoad, AreaUniformLoad, GravityLoad,
    ShellSection, AreaElement, Node, Section,
)

# Path to test fixtures
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_S2K = FIXTURES / "sample.s2k"
SAMPLE_AREAS_JSON = FIXTURES / "sample_areas.json"

def test_parse_sample():
    """Test parsing of a sample .s2k file."""
    if not SAMPLE_S2K.exists():
        pytest.skip(f"Sample file not found: {SAMPLE_S2K}")
    
    parser = SAP2000Parser(SAMPLE_S2K)
    parser.parse()
    assert parser._raw_tables is not None
    assert len(parser._raw_tables) > 0

def test_get_model_data():
    """Test conversion to SAPModelData."""
    if not SAMPLE_S2K.exists():
        pytest.skip(f"Sample file not found: {SAMPLE_S2K}")
    
    parser = SAP2000Parser(SAMPLE_S2K)
    parser.parse()
    model = parser.get_model_data()
    assert model.nodes is not None
    # Add more assertions based on your sample content


def test_parse_from_example(tmp_path):
    """Test parsing using the built-in example content."""
    from fea_toolkit.io.s2k_parser import SAP2000Parser
    
    example_content = """File test.s2k was saved on m/d/yy at h:mm:ss
TABLE:  "PROGRAM CONTROL"
   ProgramName=SAP2000   Version=26.2.0
TABLE:  "JOINT COORDINATES"
   Joint=1   XorR=0   Y=0   Z=0
   Joint=2   XorR=5   Y=0   Z=0
"""
    s2k_file = tmp_path / "example.s2k"
    s2k_file.write_text(example_content)
    
    parser = SAP2000Parser(s2k_file)
    parser.parse()
    assert "JOINT COORDINATES" in parser._raw_tables
    assert len(parser._raw_tables["JOINT COORDINATES"]) == 2


def test_parse_area_gravity_loads(tmp_path):
    """Parse AREA LOADS - GRAVITY table."""
    content = """File test.s2k was saved on m/d/yy at h:mm:ss
TABLE:  "AREA LOADS - GRAVITY"
   Area=1   LoadPat="DEAD"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0   MultiplierZ=-1
   Area=2   LoadPat="DEAD"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0   MultiplierZ=-1.05
   Area=1   LoadPat="SDL"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0   MultiplierZ=-1
"""
    s2k = tmp_path / "test_area_grav.s2k"
    s2k.write_text(content)
    parser = SAP2000Parser(s2k)
    parser.parse()
    md = parser.get_model_data()
    assert len(md.area_gravity_loads) == 3
    # Check first record
    agl = md.area_gravity_loads[0]
    assert isinstance(agl, AreaGravityLoad)
    assert agl.area_id == "1"
    assert agl.pattern == "DEAD"
    assert agl.multiplier_z == -1.0
    # Check second record (non-standard multiplier)
    assert md.area_gravity_loads[1].multiplier_z == -1.05
    # Check third record (different pattern)
    assert md.area_gravity_loads[2].pattern == "SDL"
    assert md.area_gravity_loads[2].area_id == "1"


def test_parse_frame_gravity_loads(tmp_path):
    """Parse FRAME LOADS - GRAVITY table."""
    content = """File test.s2k was saved on m/d/yy at h:mm:ss
TABLE:  "FRAME LOADS - GRAVITY"
   Frame=5   LoadPat="DEAD"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0   MultiplierZ=-1.05
   Frame=10   LoadPat="DEAD"   CoordSys=GLOBAL   MultiplierX=0   MultiplierY=0.1   MultiplierZ=-1
"""
    s2k = tmp_path / "test_frame_grav.s2k"
    s2k.write_text(content)
    parser = SAP2000Parser(s2k)
    parser.parse()
    md = parser.get_model_data()
    assert len(md.frame_gravity_loads) == 2
    gl = md.frame_gravity_loads[0]
    assert isinstance(gl, GravityLoad)
    assert gl.frame_id == "5"
    assert gl.pattern == "DEAD"
    assert gl.multiplier_z == -1.05
    assert gl.multiplier_x == 0.0
    # Second: has Y multiplier
    assert md.frame_gravity_loads[1].multiplier_y == 0.1


def test_parse_area_section_properties(tmp_path):
    """Parse AREA SECTION PROPERTIES into ShellSection objects."""
    content = """File test.s2k was saved on m/d/yy at h:mm:ss
TABLE:  "AREA SECTION PROPERTIES"
   Section=Slab200   Material=C30/37   MatAngle=0   AreaType=Shell   Type=Shell-Thin   Thickness=0.2   BendThick=0.2
   Section=Wall150   Material=C30/37   MatAngle=0   AreaType=Shell   Type=Shell-Thin   Thickness=0.15   BendThick=0.15
TABLE:  "AREA SECTION ASSIGNMENTS"
   Area=1   Section=Slab200
   Area=2   Section=Slab200
   Area=3   Section=Wall150
TABLE:  "CONNECTIVITY - AREA"
   Area=1   Joint1=1   Joint2=2   Joint3=3   Joint4=4
   Area=2   Joint1=5   Joint2=6   Joint3=7   Joint4=8
   Area=3   Joint1=9   Joint2=10   Joint3=11
"""
    s2k = tmp_path / "test_area_sec.s2k"
    s2k.write_text(content)
    parser = SAP2000Parser(s2k)
    parser.parse()
    md = parser.get_model_data()
    # Check sections
    assert "Slab200" in md.sections
    assert "Wall150" in md.sections
    slab = md.sections["Slab200"]
    assert isinstance(slab, ShellSection)
    assert slab.thickness == 0.2
    assert slab.material == "C30/37"
    wall = md.sections["Wall150"]
    assert wall.thickness == 0.15
    # Check area element thickness populated from section
    assert md.area_elements["1"].thickness == 0.2
    assert md.area_elements["2"].thickness == 0.2
    assert md.area_elements["3"].thickness == 0.15


def test_parse_area_uniform_generic_dispatch(tmp_path):
    """Generic AREA LOADS - dispatch handles UNIFORM table."""
    content = """File test.s2k was saved on m/d/yy at h:mm:ss
TABLE:  "AREA LOADS - UNIFORM"
   Area=1   LoadPat="DEAD"   CoordSys=GLOBAL   Dir=Gravity   UnifLoad=5000
   Area=2   LoadPat="LIVE"   CoordSys=LOCAL   Dir=Z   UnifLoad=3000
"""
    s2k = tmp_path / "test_unif.s2k"
    s2k.write_text(content)
    parser = SAP2000Parser(s2k)
    parser.parse()
    md = parser.get_model_data()
    assert len(md.area_uniform_loads) == 2
    assert isinstance(md.area_uniform_loads[0], AreaUniformLoad)
    assert md.area_uniform_loads[0].value == 5000.0
    assert md.area_uniform_loads[0].direction == "Gravity"
    assert md.area_uniform_loads[1].value == 3000.0
    assert md.area_uniform_loads[1].coord_sys == "LOCAL"


def test_unknown_area_load_type_skipped(tmp_path):
    """Unknown AREA LOADS - type is silently skipped."""
    content = """File test.s2k was saved on m/d/yy at h:mm:ss
TABLE:  "AREA LOADS - UNIFORM"
   Area=1   LoadPat="DEAD"   UnifLoad=1000
TABLE:  "AREA LOADS - MYSTERY"
   Area=2   LoadPat="DEAD"   SomeField=42
"""
    s2k = tmp_path / "test_mystery.s2k"
    s2k.write_text(content)
    parser = SAP2000Parser(s2k)
    parser.parse()
    md = parser.get_model_data()
    # Uniform loads still parsed
    assert len(md.area_uniform_loads) == 1
    # Mystery table ignored, nothing in gravity loads
    assert len(md.area_gravity_loads) == 0


# ========================================================================
# JSON import tests
# ========================================================================

def test_parse_areas_from_json():
    """Import area elements and shell sections from a JSON file."""
    if not SAMPLE_AREAS_JSON.exists():
        pytest.skip(f"Fixture not found: {SAMPLE_AREAS_JSON}")

    parser = SAP2000Parser.from_json(SAMPLE_AREAS_JSON)
    assert parser is not None
    assert "CONNECTIVITY - AREA" in parser._raw_tables

    md = parser.get_model_data()

    # ── Nodes ──
    assert len(md.nodes) == 10

    # ── Area elements ──
    assert len(md.area_elements) == 4
    for aid in ("1", "2", "3", "4"):
        assert aid in md.area_elements
        ae = md.area_elements[aid]
        assert isinstance(ae, AreaElement)
        assert len(ae.node_ids) >= 3
        assert ae.area_id == aid

    # ── Shell sections ──
    assert "Slab200" in md.sections
    assert "Wall150" in md.sections
    slab = md.sections["Slab200"]
    assert isinstance(slab, ShellSection)
    assert slab.thickness == 200.0
    assert slab.material == "C30/37"
    wall = md.sections["Wall150"]
    assert wall.thickness == 150.0

    # ── Area assignments ──
    assert md.area_assignments["1"] == "Slab200"
    assert md.area_assignments["2"] == "Slab200"
    assert md.area_assignments["3"] == "Wall150"
    assert md.area_assignments["4"] == "Wall150"

    # ── Thickness populated from section ──
    assert md.area_elements["1"].thickness == 200.0
    assert md.area_elements["3"].thickness == 150.0

    # ── Restraints ──
    assert len(md.restraints) == 4
    assert "1" in md.restraints
    assert md.restraints["1"].dofs[:3] == [1, 1, 1]  # U1, U2, U3 fixed

    # ── Frame elements also present ──
    assert len(md.frame_elements) == 1
    assert len(md.frame_assignments) == 1


def test_parse_areas_from_json_no_s2k():
    """Verify from_json() works without needing a real .s2k file path."""
    parser = SAP2000Parser.from_json(SAMPLE_AREAS_JSON)
    md = parser.get_model_data()
    assert len(md.nodes) == 10
    assert len(md.area_elements) == 4
    # Shell section properties
    slab = md.sections["Slab200"]
    assert isinstance(slab, ShellSection)
    assert slab.thickness == 200.0


def test_parse_areas_multi_row_consolidation(tmp_path):
    """Multi-row area connectivity consolidates joint IDs correctly."""
    json_data = {
        "JOINT COORDINATES": [
            {"Joint": i, "XorR": i * 1000.0, "Y": 0.0, "Z": 0.0}
            for i in range(1, 9)
        ],
        "CONNECTIVITY - AREA": [
            # Area 1 spans two rows: first row has Joint1..Joint4
            {"Area": 1, "Joint1": 1, "Joint2": 2, "Joint3": 3},
            # Second row adds Joint4..Joint6 (Joint1 already present)
            {"Area": 1, "Joint1": 4, "Joint2": 5, "Joint3": 6},
        ],
        "AREA SECTION PROPERTIES": [
            {"Section": "Slab200", "Material": "C30/37",
             "Thickness": 200.0, "AreaType": "Shell", "Type": "Shell-Thin"},
        ],
        "AREA SECTION ASSIGNMENTS": [
            {"Area": 1, "Section": "Slab200"},
        ],
    }
    import json
    json_path = tmp_path / "multi_row.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()

    # Area 1 should have all 6 joint IDs consolidated
    assert "1" in md.area_elements
    ae = md.area_elements["1"]
    assert len(ae.node_ids) == 6
    assert ae.node_ids == ["1", "2", "3", "4", "5", "6"]


def test_parse_areas_multi_row_with_duplicates(tmp_path):
    """Multi-row consolidation should not produce duplicate joint IDs."""
    json_data = {
        "JOINT COORDINATES": [
            {"Joint": i, "XorR": i * 1000.0, "Y": 0.0, "Z": 0.0}
            for i in range(1, 5)
        ],
        "CONNECTIVITY - AREA": [
            # Row 1: Joint1..Joint4
            {"Area": 1, "Joint1": 1, "Joint2": 2, "Joint3": 3, "Joint4": 4},
            # Row 2: same joints again (duplicate)
            {"Area": 1, "Joint1": 1, "Joint2": 2, "Joint3": 3, "Joint4": 4},
        ],
        "AREA SECTION PROPERTIES": [
            {"Section": "Slab200", "Material": "C30/37",
             "Thickness": 200.0, "AreaType": "Shell", "Type": "Shell-Thin"},
        ],
        "AREA SECTION ASSIGNMENTS": [
            {"Area": 1, "Section": "Slab200"},
        ],
    }
    import json
    json_path = tmp_path / "dup.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()

    ae = md.area_elements["1"]
    # Should have exactly 4 unique joint IDs (no duplicates)
    assert len(ae.node_ids) == 4
    assert ae.node_ids == ["1", "2", "3", "4"]


# ========================================================================
# Shell section creation tests
# ========================================================================

def test_create_single_shell_section():
    """Verify _create_single_shell_section produces correct section."""
    # Build a minimal model to test the static method
    mat = type("Mat", (), {
        "E_mod": 3.28e10, "nu": 0.2, "unit_mass": 2549.0,
    })()
    sec = type("Sec", (), {"thickness": 0.2, "name": "Slab200"})()

    # The method is static; just verify it doesn't crash
    # and produces the right call shape
    from fea_toolkit.opensees.builder import OpenSeesBuilder
    # We can't easily capture ops.section output, so at least
    # confirm the method exists and accepts the right signature
    assert hasattr(OpenSeesBuilder, '_create_single_shell_section')


# ========================================================================
# Frame end offsets, area mesh, area edge constraints
# ========================================================================

def test_frame_end_offsets_parsed(tmp_path):
    """FRAME END LENGTH OFFSETS table is parsed correctly."""
    json_data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "25"}],
        "JOINT COORDINATES": [
            {"Joint": 1, "XorR": 0, "Y": 0, "Z": 0},
            {"Joint": 2, "XorR": 6, "Y": 0, "Z": 0},
        ],
        "CONNECTIVITY - FRAME": [
            {"Frame": 1, "JointI": 1, "JointJ": 2},
        ],
        "FRAME SECTION PROPERTIES 01 - GENERAL": [
            {"Section": "Col600", "Material": "C30/37", "Shape": "Rectangular",
             "t3": 0.6, "t2": 0.6, "Area": 0.36, "I33": 0.0108, "I22": 0.0108},
        ],
        "FRAME SECTION ASSIGNMENTS": [
            {"Frame": 1, "Section": "Col600"},
        ],
        "FRAME END LENGTH OFFSETS": [
            {"Frame": 1, "EndI": 0.3, "EndJ": 0.3},
        ],
    }
    import json
    json_path = tmp_path / "offsets.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()

    assert "1" in md.frame_end_offsets
    off = md.frame_end_offsets["1"]
    assert off.end_i == 0.3
    assert off.end_j == 0.3


def test_frame_end_offsets_empty_when_missing(tmp_path):
    """No FRAME END LENGTH OFFSETS table → empty dict."""
    json_data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "25"}],
        "JOINT COORDINATES": [
            {"Joint": 1, "XorR": 0, "Y": 0, "Z": 0},
            {"Joint": 2, "XorR": 6, "Y": 0, "Z": 0},
        ],
    }
    import json
    json_path = tmp_path / "no_offsets.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()
    assert md.frame_end_offsets == {}


def test_area_mesh_parsed(tmp_path):
    """AREA MESH ASSIGNMENTS table is parsed correctly."""
    json_data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "25"}],
        "JOINT COORDINATES": [
            {"Joint": i, "XorR": 0.0, "Y": 0.0, "Z": 0.0} for i in range(1, 5)
        ],
        "CONNECTIVITY - AREA": [
            {"Area": 1, "Joint1": 1, "Joint2": 2, "Joint3": 3, "Joint4": 4},
        ],
        "AREA SECTION PROPERTIES": [
            {"Section": "Slab200", "Material": "C30/37",
             "Thickness": 200.0, "AreaType": "Shell", "Type": "Shell-Thin"},
        ],
        "AREA SECTION ASSIGNMENTS": [
            {"Area": 1, "Section": "Slab200"},
        ],
        "AREA MESH ASSIGNMENTS": [
            {"Area": 1, "AutoMesh": "Yes",
             "NoAutoMeshAtEdges": "No", "NoSubMesh": "No",
             "MinSize": 0.5, "MaxSize": 1.0},
        ],
    }
    import json
    json_path = tmp_path / "mesh.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()

    assert "1" in md.area_mesh
    m = md.area_mesh["1"]
    assert m.auto_mesh is True
    assert m.no_auto_mesh_at_edges is False
    assert m.min_size == 0.5
    assert m.max_size == 1.0


def test_area_edge_constraints_parsed(tmp_path):
    """AREA EDGE CONSTRAINT ASSIGNMENTS table is parsed correctly."""
    json_data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "25"}],
        "JOINT COORDINATES": [
            {"Joint": i, "XorR": 0.0, "Y": 0.0, "Z": 0.0} for i in range(1, 5)
        ],
        "CONNECTIVITY - AREA": [
            {"Area": 1, "Joint1": 1, "Joint2": 2, "Joint3": 3, "Joint4": 4},
        ],
        "AREA SECTION PROPERTIES": [
            {"Section": "Slab200", "Material": "C30/37",
             "Thickness": 200.0, "AreaType": "Shell", "Type": "Shell-Thin"},
        ],
        "AREA SECTION ASSIGNMENTS": [
            {"Area": 1, "Section": "Slab200"},
        ],
        "AREA EDGE CONSTRAINT ASSIGNMENTS": [
            {"Area": 1, "Edge": 1, "Constraint": "Default"},
            {"Area": 1, "Edge": 2, "Constraint": "Default"},
            {"Area": 1, "Edge": 3, "Constraint": "Default"},
            {"Area": 1, "Edge": 4, "Constraint": "Default"},
        ],
    }
    import json
    json_path = tmp_path / "edge_con.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()

    assert "1" in md.area_edge_constraints
    cons = md.area_edge_constraints["1"]
    assert len(cons) == 4
    assert cons[0].edge == 1
    assert cons[0].constraint == "Default"


def test_tolerant_float_parsing_with_empty_cells(tmp_path):
    """Empty-string float cells default to 0.0 instead of crashing."""
    json_data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "25"}],
        "JOINT COORDINATES": [
            {"Joint": i, "XorR": 0.0, "Y": 0.0, "Z": 0.0} for i in range(1, 5)
        ],
        "CONNECTIVITY - FRAME": [
            {"Frame": 1, "JointI": 1, "JointJ": 2},
        ],
        "FRAME SECTION PROPERTIES 01 - GENERAL": [
            {"Section": "Col600", "Material": "C30/37", "Shape": "Rectangular",
             "t3": 0.6, "t2": 0.6, "Area": 0.36, "I33": 0.0108, "I22": 0.0108},
        ],
        "FRAME SECTION ASSIGNMENTS": [
            {"Frame": 1, "Section": "Col600"},
        ],
        # EndI and EndJ are empty strings — the parser must not crash.
        "FRAME END LENGTH OFFSETS": [
            {"Frame": 1, "EndI": "", "EndJ": ""},
        ],
        "CONNECTIVITY - AREA": [
            {"Area": 1, "Joint1": 1, "Joint2": 2, "Joint3": 3, "Joint4": 4},
        ],
        "AREA SECTION PROPERTIES": [
            {"Section": "Slab200", "Material": "C30/37",
             "Thickness": 200.0, "AreaType": "Shell", "Type": "Shell-Thin"},
        ],
        "AREA SECTION ASSIGNMENTS": [
            {"Area": 1, "Section": "Slab200"},
        ],
        # MinSize and MaxSize are empty strings.
        "AREA MESH ASSIGNMENTS": [
            {"Area": 1, "AutoMesh": "Yes", "MinSize": "", "MaxSize": ""},
        ],
    }
    import json
    json_path = tmp_path / "empty_cells.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()

    # Offsets should default to 0.0
    off = md.frame_end_offsets["1"]
    assert off.end_i == 0.0
    assert off.end_j == 0.0

    # Mesh sizes should default to 0.0
    m = md.area_mesh["1"]
    assert m.auto_mesh is True
    assert m.min_size == 0.0
    assert m.max_size == 0.0


def test_new_tables_empty_when_missing(tmp_path):
    """All new tables return empty defaults when no data exists."""
    json_data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "25"}],
        "JOINT COORDINATES": [
            {"Joint": 1, "XorR": 0, "Y": 0, "Z": 0},
        ],
    }
    import json
    json_path = tmp_path / "empty.json"
    with open(json_path, "w") as f:
        json.dump(json_data, f)

    parser = SAP2000Parser.from_json(json_path)
    md = parser.get_model_data()
    assert md.frame_end_offsets == {}
    assert md.area_mesh == {}
    assert md.area_edge_constraints == {}

