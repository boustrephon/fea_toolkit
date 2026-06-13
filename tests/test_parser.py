import pytest
from pathlib import Path
from fea_toolkit.io.s2k_parser import SAP2000Parser

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

