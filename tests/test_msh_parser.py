"""Tests for the Gmsh ``.msh`` ASCII parser (:mod:`fea_toolkit.io.msh_parser`).

The parser is pure Python and must not require Rhino or OpenSees, so these
tests run headlessly.  A small synthetic MSH 4.1 file exercises the Gmsh
"all node tags, then all coordinates" node layout plus quad/triangle element
blocks and physical-group resolution.
"""

import pytest

from fea_toolkit.io.msh_parser import MSHElement, MSHMesh, parse_msh_text, read_msh

MINIMAL_MSH = """\
$MeshFormat
4.1 0 8
$EndMeshFormat
$PhysicalNames
2
2 1 "surface_a"
2 2 "surface_b"
$EndPhysicalNames
$Entities
4 0 2 0
1 0 0 0 0
2 1 0 0 0
3 1 1 0 0
4 0 1 0 0
1 0 0 0 1 1 0 1 1
2 0 0 0 1 1 0 1 2
$EndEntities
$Nodes
4 4 1 4
0 1 0 1
1
0 0 0
0 2 0 1
2
1 0 0
0 3 0 1
3
1 1 0
0 4 0 1
4
0 1 0
$EndNodes
$Elements
2 2 1 2
2 1 3 1
1 1 2 3 4
2 2 2 1
2 2 3 4
$EndElements
"""


class TestParseMshText:
    def test_parses_nodes_physical_names_and_points(self):
        mesh = parse_msh_text(MINIMAL_MSH)
        assert mesh.version == "4.1"
        assert mesh.nodes == {
            1: (0.0, 0.0, 0.0),
            2: (1.0, 0.0, 0.0),
            3: (1.0, 1.0, 0.0),
            4: (0.0, 1.0, 0.0),
        }
        assert mesh.physical_names == {1: "surface_a", 2: "surface_b"}
        assert mesh.points == {
            1: (0.0, 0.0, 0.0),
            2: (1.0, 0.0, 0.0),
            3: (1.0, 1.0, 0.0),
            4: (0.0, 1.0, 0.0),
        }

    def test_elements_carry_dim_type_and_connectivity(self):
        mesh = parse_msh_text(MINIMAL_MSH)
        quad, tri = mesh.elements
        assert quad.dim == 2 and quad.tag == 1 and quad.etype == 3
        assert quad.node_tags == (1, 2, 3, 4)
        assert quad.physical_tags == (1,)
        assert quad.name == "quadrangle"
        assert tri.dim == 2 and tri.tag == 2 and tri.etype == 2
        assert tri.node_tags == (2, 3, 4)
        assert tri.physical_tags == (2,)

    def test_group_elements_partitions_by_physical_name(self):
        mesh = parse_msh_text(MINIMAL_MSH)
        groups = mesh.group_elements()
        assert set(groups) == {"surface_a", "surface_b"}
        assert [e.tag for e in groups["surface_a"]] == [1]
        assert [e.tag for e in groups["surface_b"]] == [2]

    def test_quoted_physical_name_with_spaces(self):
        text = MINIMAL_MSH.replace('2 1 "surface_a"', '2 1 "surface one"', 1)
        mesh = parse_msh_text(text)
        assert mesh.physical_names[1] == "surface one"


class TestUngrouped:
    def test_elements_without_physical_tags_go_to_ungrouped(self):
        mesh = MSHMesh(
            version="4.1",
            nodes={1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0)},
            elements=[MSHElement(dim=2, tag=1, etype=2, node_tags=(1, 2, 3))],
        )
        groups = mesh.group_elements()
        assert list(groups) == ["Ungrouped"]
        assert groups["Ungrouped"][0].tag == 1


class TestErrors:
    def test_binary_filetype_raises(self):
        text = MINIMAL_MSH.replace("4.1 0 8", "4.1 1 8", 1)
        with pytest.raises(ValueError, match="binary"):
            parse_msh_text(text)

    def test_legacy_version_raises(self):
        text = MINIMAL_MSH.replace("4.1 0 8", "2.2 0 8", 1)
        with pytest.raises(ValueError, match=r"only 4\.1"):
            parse_msh_text(text)

    def test_unsupported_4_0_version_raises(self):
        text = MINIMAL_MSH.replace("4.1 0 8", "4.0 0 8", 1)
        with pytest.raises(ValueError, match=r"only 4\.1"):
            parse_msh_text(text)

    def test_malformed_mesh_format_raises(self):
        text = MINIMAL_MSH.replace("4.1 0 8", "4.1", 1)
        with pytest.raises(ValueError, match=r"malformed \$MeshFormat"):
            parse_msh_text(text)

    def test_nodes_block_header_too_short_raises(self):
        text = "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\n1 1 1 2\n0 1\n1\n0 0 0\n$EndNodes\n"
        with pytest.raises(ValueError, match=r"\$Nodes"):
            parse_msh_text(text)

    def test_nodes_premature_end_raises(self):
        text = "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\n1 1 1 1\n0 1 0 1\n1\n$EndNodes\n"
        with pytest.raises(ValueError, match=r"\$Nodes"):
            parse_msh_text(text)

    def test_elements_block_header_too_short_raises(self):
        text = "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Elements\n1 1 1 2\n2 1\n$EndElements\n"
        with pytest.raises(ValueError, match=r"\$Elements"):
            parse_msh_text(text)

    def test_elements_premature_end_raises(self):
        text = (
            "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n"
            "$Elements\n"
            "1 2 1 2\n"
            "2 1 3 2\n"
            "1 1 2 3 4\n"
            "$EndElements\n"
        )
        with pytest.raises(ValueError, match=r"\$Elements"):
            parse_msh_text(text)

    def test_missing_mesh_format_raises(self):
        with pytest.raises(ValueError, match="missing \\$MeshFormat"):
            parse_msh_text("$Nodes\n$EndNodes\n")

    def test_unsupported_element_type_raises(self):
        text = MINIMAL_MSH.replace("2 1 3 1", "2 1 999 1", 1)
        with pytest.raises(ValueError, match="unsupported Gmsh element type 999"):
            parse_msh_text(text)

    def test_mesh_format_two_fields_raises(self):
        text = MINIMAL_MSH.replace("4.1 0 8", "4.1 0", 1)
        with pytest.raises(ValueError, match=r"malformed \$MeshFormat"):
            parse_msh_text(text)

    def test_element_record_missing_node_tags_raises(self):
        # etype 3 (quadrangle) needs 4 node tags; provide only 2.
        text = (
            "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n"
            "$Elements\n"
            "1 1 1 1\n"
            "2 1 3 1\n"
            "1 1 2\n"
            "$EndElements\n"
        )
        with pytest.raises(ValueError, match=r"\$Elements"):
            parse_msh_text(text)

    def test_nodes_missing_final_block_raises(self):
        # Section header declares 2 entity blocks but only 1 is present.
        text = (
            "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\n2 1 1 1\n0 1 0 1\n1\n0 0 0\n$EndNodes\n"
        )
        with pytest.raises(ValueError, match=r"\$Nodes"):
            parse_msh_text(text)

    def test_nodes_record_total_mismatch_raises(self):
        # Section header declares 1 node but the block provides 2.
        text = (
            "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n"
            "$Nodes\n"
            "1 1 1 2\n"
            "0 1 0 2\n"
            "1\n"
            "0 0 0\n"
            "2\n"
            "1 0 0\n"
            "$EndNodes\n"
        )
        with pytest.raises(ValueError, match=r"\$Nodes"):
            parse_msh_text(text)

    def test_elements_missing_final_block_raises(self):
        # Section header declares 2 entity blocks but only 1 is present.
        text = (
            "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n"
            "$Elements\n"
            "2 1 1 2\n"
            "2 1 3 1\n"
            "1 1 2 3 4\n"
            "$EndElements\n"
        )
        with pytest.raises(ValueError, match=r"\$Elements"):
            parse_msh_text(text)

    def test_nodes_coordinate_record_too_short_raises(self):
        # A coordinate record with fewer than 3 values must be rejected.
        text = "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\n1 1 1 1\n0 1 0 1\n1\n0 0\n$EndNodes\n"
        with pytest.raises(ValueError, match=r"\$Nodes"):
            parse_msh_text(text)


def test_read_msh_roundtrip(tmp_path):
    path = tmp_path / "sample.msh"
    path.write_text(MINIMAL_MSH, encoding="utf-8")
    mesh = read_msh(path)
    assert len(mesh.elements) == 2
    assert set(mesh.group_elements()) == {"surface_a", "surface_b"}
