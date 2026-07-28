"""Tests for AnalysisBuilder nD material + layered shell section creation.

Covers the four gaps identified in the audit:
1. _create_nd_materials() — nDMaterial ops calls exercised
2. _create_layered_shell_sections() — LayeredShell ops calls exercised
3. Skipped-material rejection — unknown nD material causes section skip
4. Section recreation after ops.wipe() — build_domain twice succeeds
"""

import pytest
import openseespy.opensees as ops

from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    NDMaterial,
    ShellFiberLayer,
    LayeredShellSection,
    ShellSection,
    Material,
    AreaElement,
    Node,
)


def _minimal_mesh(nd_materials=None, layered_sections=None):
    """Build a MeshModel with just enough data to reach the nD/layered paths.

    Args:
        nd_materials: ``{name: NDMaterial}`` or None (skips nD step).
        layered_sections: ``{name: LayeredShellSection}`` or None.
    """
    return MeshModel(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        },
        materials={},
        sections={},
        frame_elements={},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        material_tags={},
        section_tags={},
        shell_sec_tags={},
        shell_sec_variants={},
        frame_element_types={},
        area_element_types={},
        offset_rigid_links=[],
        edge_constraint_args=[],
        edge_loads_from_areas=[],
        loads_only_area_ids=set(),
        base_z=0.0,
        units={"F": "N", "L": "m", "T": "C"},
        nd_materials=nd_materials or {},
        layered_shell_sections=layered_sections or {},
    )


class TestLayeredShellBuild:
    """Verify AnalysisBuilder builds nD materials and layered shell sections."""

    def teardown_method(self):
        ops.wipe()

    def _three_node_area_mesh(self, nd_materials, layered_sections, sec_name):
        """Build a MeshModel with a triangular area assigned to *sec_name*."""
        return MeshModel(
            nodes={
                "n1": Node(node_id="n1", node_tag=101, x=0.0, y=0.0, z=0.0),
                "n2": Node(node_id="n2", node_tag=102, x=4.0, y=0.0, z=0.0),
                "n3": Node(node_id="n3", node_tag=103, x=0.0, y=4.0, z=0.0),
            },
            materials={
                "mat_concrete": Material(
                    name="mat_concrete", type="Concrete",
                    E_mod=30.0e9, nu=0.2,
                    unit_weight=0.0, unit_mass=0.0,
                ),
            },
            sections={
                sec_name: ShellSection(
                    name=sec_name, shape="ShellSection",
                    material="mat_concrete", thickness=0.3,
                ),
            },
            frame_elements={},
            frame_assignments={},
            area_elements={
                "a1": AreaElement(
                    area_id="a1", area_tag=201,
                    node_ids=["n1", "n2", "n3"],
                    thickness=0.3,
                ),
            },
            area_assignments={"a1": sec_name},
            frame_dist_loads=[],
            material_tags={},
            section_tags={},
            shell_sec_tags={},
            shell_sec_variants={},
            frame_element_types={},
            area_element_types={},
            offset_rigid_links=[],
            edge_constraint_args=[],
            edge_loads_from_areas=[],
            loads_only_area_ids=set(),
            base_z=0.0,
            units={"F": "N", "L": "m", "T": "C"},
            nd_materials=nd_materials or {},
            layered_shell_sections=layered_sections or {},
        )

    # ── Test 1: _create_nd_materials with ElasticIsotropic ──────────

    def test_nd_materials_are_populated_in_build_domain(self):
        """ElasticIsotropic nD material is created when build_domain runs."""
        ndm = {
            "concrete": NDMaterial(
                name="concrete",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "concrete" in builder.material_tags
        tag = builder.material_tags["concrete"]
        assert isinstance(tag, int) and tag > 0
        # The material was created (no skipped materials for a known type)
        assert len(builder._skipped_nd_materials) == 0

    # ── Test 2: _create_layered_shell_sections ──────────────────────

    def test_layered_shell_section_created_via_build_domain(self):
        """LayeredShell section referencing valid nD material is created by build_domain."""
        ndm = {
            "concrete": NDMaterial(
                name="concrete",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "wall_section" in builder._shell_sec_tags
        sec_tag = builder._shell_sec_tags["wall_section"]
        assert isinstance(sec_tag, int) and sec_tag > 0
        # no skipped materials
        assert len(builder._skipped_nd_materials) == 0

    # ── Test 3: Skipped-material rejection ──────────────────────────

    def test_skipped_material_rejects_dependent_section(self):
        """LayeredShell referencing a skipped/unknown nD material is rejected."""
        ndm = {
            "bad_stuff": NDMaterial(
                name="bad_stuff",
                material_type="NonExistentType",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_using_bad": LayeredShellSection(
                name="wall_using_bad",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="bad_stuff", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        # The unknown material was skipped
        assert "bad_stuff" in builder._skipped_nd_materials
        assert "bad_stuff" not in builder.material_tags

        # The section referencing it should NOT be created
        assert "wall_using_bad" not in builder._shell_sec_tags

    def test_skipped_material_preserves_valid_section(self):
        """Valid sections survive alongside rejected ones."""
        ndm = {
            "good_conc": NDMaterial(
                name="good_conc",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
            "bad_stuff": NDMaterial(
                name="bad_stuff",
                material_type="UnknownType",
                E=10.0e9,
                nu=0.3,
            ),
        }
        lss = {
            "valid_wall": LayeredShellSection(
                name="valid_wall",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="good_conc", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="good_conc", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="good_conc", n_ip=3),
                ],
            ),
            "invalid_wall": LayeredShellSection(
                name="invalid_wall",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="bad_stuff", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        # Valid section is created
        assert "valid_wall" in builder._shell_sec_tags
        # Invalid section is rejected
        assert "invalid_wall" not in builder._shell_sec_tags
        # The bad material was tracked
        assert "bad_stuff" in builder._skipped_nd_materials

    # ── Test 4: Section recreation after ops.wipe() ─────────────────

    def test_layered_sections_recreated_after_wipe(self):
        """Calling build_domain() twice recreates LayeredShell sections."""
        ndm = {
            "concrete": NDMaterial(
                name="concrete",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})

        # First build — creates everything fresh
        builder.build_domain()
        assert "wall_section" in builder._shell_sec_tags
        first_tag = builder._shell_sec_tags["wall_section"]
        assert isinstance(first_tag, int)

        # Second build — ops.wipe() is called inside build_domain()
        builder.build_domain()
        assert "wall_section" in builder._shell_sec_tags
        second_tag = builder._shell_sec_tags["wall_section"]
        # The tag should be stable (same after wipe + recreate)
        assert second_tag == first_tag
        # nD material tag should also still exist
        assert "concrete" in builder.material_tags

    # ── Test 5: Elastic fallback + section-tag collision ─────────────

    def test_skipped_layered_section_creates_no_shell_element(self):
        """Areas referencing a skipped layered section produce no shell elements.

        Covers both:
        - Elastic fallback path (section name in _skipped_shell_sec_names)
        - Section-tag collision path (no tag assigned to the skipped section)
        """
        ndm = {
            "bad_stuff": NDMaterial(
                name="bad_stuff",
                material_type="NonExistentType",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_using_bad": LayeredShellSection(
                name="wall_using_bad",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = self._three_node_area_mesh(nd_materials=ndm, layered_sections=lss,
                                        sec_name="wall_using_bad")
        builder = AnalysisBuilder(mm, {"verbose": False,
                                        "create_shells": True})
        builder.build_domain()

        # The skipped section name is tracked
        assert "wall_using_bad" in builder._skipped_shell_sec_names

        # No shell element was created for area 'a1'
        assert "a1" not in builder._shell_tag_map
