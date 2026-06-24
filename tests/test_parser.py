import pytest
from pathlib import Path
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.sap_data import (
    AreaGravityLoad, AreaUniformLoad, GravityLoad,
    ShellSection,
)

# Path to a minimal test .s2k file (you can create one or use an existing small model)
SAMPLE_S2K = Path(__file__).parent / "fixtures" / "sample.s2k"

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

