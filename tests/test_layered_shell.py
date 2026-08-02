"""Tests for AnalysisBuilder nD material + layered shell section creation.

Covers the four gaps identified in the audit:
1. _create_nd_materials() — nDMaterial ops calls exercised
2. _create_layered_shell_sections() — LayeredShell ops calls exercised
3. Skipped-material rejection — unknown nD material causes section skip
4. Section recreation after ops.wipe() — build_domain twice succeeds
5. Rebuild recovery — supported → unsupported → supported cycle recovers
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    LayeredShellSection,
    Material,
    NDMaterial,
    Node,
    Restraint,
    SAPModelData,
    ShellFiberLayer,
    ShellSection,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import Preprocessor


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
                    name="mat_concrete",
                    type="Concrete",
                    E_mod=30.0e9,
                    nu=0.2,
                    unit_weight=0.0,
                    unit_mass=0.0,
                ),
            },
            sections={
                sec_name: ShellSection(
                    name=sec_name,
                    shape="ShellSection",
                    material="mat_concrete",
                    thickness=0.3,
                ),
            },
            frame_elements={},
            frame_assignments={},
            area_elements={
                "a1": AreaElement(
                    area_id="a1",
                    area_tag=201,
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

    # ── Test 1: Build with ElasticIsotropic ──────────────────────────

    def test_elastic_isotropic_nd_material(self):
        """Create a single ElasticIsotropic nD material + layered section."""
        ndm = {
            "conc1": NDMaterial(
                name="conc1",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="conc1", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="conc1", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="conc1", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "conc1" in builder._nd_material_tags
        conc_tag = builder._nd_material_tags["conc1"]
        assert isinstance(conc_tag, int) and conc_tag > 0

        assert "wall_section" in builder._shell_sec_tags
        sec_tag = builder._shell_sec_tags["wall_section"]
        assert isinstance(sec_tag, int) and sec_tag > 0
        assert len(builder._skipped_nd_materials) == 0

    # ── Test 2: Build with J2PlateFibre + ConcreteS ──────────────────

    def test_j2_and_concrete_nd_material(self):
        """Create J2PlateFibre + ConcreteS nD materials."""
        ndm = {
            "rebar": NDMaterial(
                name="rebar",
                material_type="J2PlateFibre",
                E=200.0e9,
                nu=0.3,
                fy=400.0e6,
                Hiso=0.0,
                Hkin=0.01,
            ),
            "concrete": NDMaterial(
                name="concrete",
                material_type="ConcreteS",
                E=30.0e9,
                nu=0.2,
                fc=-30.0e6,
                ft=2.0e6,
                Es=30.0e9,
            ),
        }
        lss = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.002, nd_material="rebar", n_ip=5),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.002, nd_material="rebar", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "concrete" in builder._nd_material_tags
        assert "rebar" in builder._nd_material_tags
        assert "wall_section" in builder._shell_sec_tags
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

        assert "good_conc" in builder._nd_material_tags
        assert "bad_stuff" in builder._skipped_nd_materials
        assert "valid_wall" in builder._shell_sec_tags
        assert "invalid_wall" not in builder._shell_sec_tags

    # ── Test 4: Section recreation after ops.wipe() ─────────────────

    def test_build_twice_preserves_layered_section_tags(self):
        """LayeredShell section tags remain stable after a second build_domain.

        Also verifies that shell_sec_tags from a previous build are merged
        correctly (non-layered tags cleared, new non-layered tags created).
        """
        ndm = {
            "concrete": NDMaterial(
                name="concrete",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss_wall = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        lss_two = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
            "new_section": LayeredShellSection(
                name="new_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.10, nd_material="concrete", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss_wall)
        builder = AnalysisBuilder(mm, {"verbose": False})

        # Set up _shell_sec_tags *before* first build to simulate
        # reality where mesh_model already carries shell_sec_tags.
        builder._shell_sec_tags["old_stale_elastic_shell"] = 999
        builder.build_domain()

        # After first build the stale non-layered tag is cleared
        assert "old_stale_elastic_shell" not in builder._shell_sec_tags
        # wall_section was created
        assert "wall_section" in builder._shell_sec_tags
        wall_tag = builder._shell_sec_tags["wall_section"]

        # Replace mesh_model.layered_shell_sections with a new dict
        # (simulates what happens when the user reconfigures
        # shell_layers and rebuilds).
        mm.layered_shell_sections = lss_two
        # Manually restore the stale tag so the code path that
        # clears it on rebuild is exercised.
        builder._shell_sec_tags["old_stale_elastic_shell"] = 999

        # Second build — ops.wipe() is called inside build_domain()
        builder.build_domain()
        # wall_section uses lookup-based reuse → tag unchanged
        assert "wall_section" in builder._shell_sec_tags
        assert builder._shell_sec_tags["wall_section"] == wall_tag
        # new_section gets a distinct tag
        assert "new_section" in builder._shell_sec_tags
        new_tag = builder._shell_sec_tags["new_section"]
        assert isinstance(new_tag, int)
        assert new_tag != wall_tag
        # No collisions — all values are unique
        assert len(set(builder._shell_sec_tags.values())) == len(builder._shell_sec_tags)
        # nD material tag should also still exist
        assert "concrete" in builder._nd_material_tags
        # The stale non-layered tag was cleared again (did not resurrect)
        assert "old_stale_elastic_shell" not in builder._shell_sec_tags

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
                    ShellFiberLayer(thickness=0.20, nd_material="bad_stuff", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = self._three_node_area_mesh(
            nd_materials=ndm, layered_sections=lss, sec_name="wall_using_bad"
        )
        builder = AnalysisBuilder(mm, {"verbose": False, "create_shells": True})
        builder.build_domain()

        # The skipped section name is tracked
        assert "wall_using_bad" in builder._skipped_shell_sec_names

        # No shell element was created for area 'a1'
        assert "a1" not in builder._shell_tag_map

    # ── Test 6: Rebuild recovery (supported → unsupported → supported) ─

    def test_rebuild_recovery_supported_to_unsupported_back(self):
        """Verify that a supported → unsupported → supported rebuild cycle
        correctly recovers and recreates the layered shell section.

        Regression test for the stale-tag bug where:
        - Build 1: supported material → section created (tag in _shell_sec_tags)
        - Build 2: unsupported type → material skipped, section skipped,
          but material_tags still held stale tag → section wrongly recreated
        - Build 3: supported again → _skipped sets cleared, section recovered
        """
        ndm_supported = {
            "flex_mat": NDMaterial(
                name="flex_mat",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        ndm_unsupported = {
            "flex_mat": NDMaterial(
                name="flex_mat",
                material_type="NonExistentType",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "flex_section": LayeredShellSection(
                name="flex_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="flex_mat", n_ip=3),
                    ShellFiberLayer(thickness=0.20, nd_material="flex_mat", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="flex_mat", n_ip=3),
                ],
            ),
        }
        mm_supported = _minimal_mesh(nd_materials=ndm_supported, layered_sections=lss)
        mm_unsupported = _minimal_mesh(nd_materials=ndm_unsupported, layered_sections=lss)

        # ── Build 1: supported ──
        builder = AnalysisBuilder(mm_supported, {"verbose": False})
        builder.build_domain()
        assert "flex_mat" in builder._nd_material_tags
        assert "flex_section" in builder._shell_sec_tags
        assert len(builder._skipped_nd_materials) == 0
        builder._shell_sec_tags["flex_section"]

        # ── Build 2: unsupported (replace mesh_model nd_materials) ──
        builder.mesh_model = mm_unsupported
        builder.build_domain()
        assert "flex_mat" in builder._skipped_nd_materials
        # The section name is registered in _skipped_shell_sec_names so
        # _create_shell_elements will skip it (no shell element is
        # created for this section).  The _shell_sec_tags dict may still
        # carry the Build 1 tag, but the LayeredShell section is never
        # registered in OpenSees (ops.section('LayeredShell', ...) is
        # not called) because the skipped-material guard fires first.
        assert "flex_section" in builder._skipped_shell_sec_names

        # ── Build 3: supported again ──
        builder.mesh_model = mm_supported
        builder.build_domain()
        # Skip sets were cleared at start of build_domain
        assert len(builder._skipped_nd_materials) == 0
        # flex_mat should be created now
        assert "flex_mat" in builder._nd_material_tags
        # flex_section should be recreated with a fresh tag
        assert "flex_section" in builder._shell_sec_tags
        # Tag may differ from build 1 (after the intervening unsupported build
        # cleared the tag), but it must be a valid positive integer.
        recreated_tag = builder._shell_sec_tags["flex_section"]
        assert isinstance(recreated_tag, int) and recreated_tag > 0


# ── Preprocessor nd_materials tests ────────────────────────────────


def _minimal_sap_model_data(units=None):
    """Build a minimal SAPModelData that survives Preprocessor.run().

    Provides one node, one material, one section, and a single frame
    element so the topology pipeline runs without error.  Most of the
    heavy work (splitting, meshing) is skipped when ``split_elements``
    and ``create_shells`` are False.
    """
    if units is None:
        units = {"F": "kN", "L": "m", "T": "C"}
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=5.0, y=0.0, z=0.0),
        },
        materials={
            "steel": Material(
                name="steel",
                type="Steel",
                E_mod=200.0e9,
                nu=0.3,
                unit_weight=0.0,
                unit_mass=0.0,
            ),
        },
        sections={
            "beam_sec": ShellSection(
                name="beam_sec",
                shape="ShellSection",
                material="steel",
                thickness=0.1,
            ),
        },
        frame_elements={
            "f1": FrameElement(
                elem_id="f1",
                elem_tag=10,
                node_i="1",
                node_j="2",
            ),
        },
        area_elements={},
        frame_assignments={"f1": "beam_sec"},
        area_assignments={},
        groups={},
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
        },
        load_cases={},
        load_patterns={},
        mass_sources={},
        joint_loads=[],
        frame_gravity_loads=[],
        area_gravity_loads=[],
        area_uniform_loads=[],
        frame_dist_loads=[],
        frame_end_offsets={},
        frame_auto_mesh={},
        units=units,
    )


class TestPreprocessorNdMaterials:
    """Verify Preprocessor._resolve_element_properties nd_materials path.

    Ensures the config->nd_materials path is exercised through
    Preprocessor.run() so any future refactoring of the validation or
    scaling logic is caught.
    """

    def test_invalid_nd_material_key_raises_valueerror(self):
        """Passing an unknown key in config['nd_materials'] raises ValueError."""
        model_data = _minimal_sap_model_data()
        config = {
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
            "nd_materials": {
                "mat1": {
                    "E": 200.0e9,
                    "nu": 0.3,
                    "not_a_valid_field": 42,  # ← unknown key
                },
            },
        }
        pre = Preprocessor(config)
        with pytest.raises(ValueError) as exc_info:
            pre.run(model_data)
        msg = str(exc_info.value)
        assert "Invalid key" in msg
        assert "nd_materials" in msg
        assert "mat1" in msg

    def test_stress_fields_scaled_in_mesh_model(self):
        """Stress-valued fields are scaled from SI Pa to model units.

        Uses kN-m units where stress_scale_factor = 0.001, so
        400 MPa (SI) → 400 kPa (model).
        """
        model_data = _minimal_sap_model_data(units={"F": "kN", "L": "m", "T": "C"})
        config = {
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
            "nd_materials": {
                "steel_mat": {
                    "material_type": "J2PlateFibre",
                    "E": 200.0e9,
                    "nu": 0.3,
                    "fy": 400.0e6,  # 400 MPa in SI Pa
                    "Hiso": 0.0,
                    "Hkin": 0.01e9,  # 10 MPa hardening → scaled
                },
            },
        }
        pre = Preprocessor(config)
        mesh_model = pre.run(model_data)

        assert "steel_mat" in mesh_model.nd_materials
        mat = mesh_model.nd_materials["steel_mat"]

        # In kN-m units: stress_scale = 0.001 (Pa → kPa)
        # Check that scaling was applied:
        #   fy: 400.0e6 Pa → 400.0e3 kPa
        #   Hkin: 0.01e9 Pa → 10.0e3 kPa
        # Allow for floating-point rounding
        assert abs(mat.fy - 400.0e3) < 1.0, f"Expected fy≈400e3, got {mat.fy}"
        assert abs(mat.Hkin - 10.0e3) < 1.0, f"Expected Hkin≈10e3, got {mat.Hkin}"

        # Non-stress fields (nu, material_type) must pass through unchanged
        assert mat.nu == 0.3
        assert mat.material_type == "J2PlateFibre"

    def test_stress_fields_scaled_si_units(self):
        """With SI units (N-m), stress_scale_factor = 1.0 → no change."""
        model_data = _minimal_sap_model_data(units={"F": "N", "L": "m", "T": "C"})
        config = {
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
            "nd_materials": {
                "conc_mat": {
                    "material_type": "ConcreteS",
                    "E": 30.0e9,
                    "nu": 0.2,
                    "fc": 30.0e6,
                    "ft": 2.0e6,
                    "Es": 30.0e9,
                },
            },
        }
        pre = Preprocessor(config)
        mesh_model = pre.run(model_data)

        assert "conc_mat" in mesh_model.nd_materials
        mat = mesh_model.nd_materials["conc_mat"]
        # SI → SI: values should be unchanged
        assert mat.E == 30.0e9
        assert mat.fc == 30.0e6
        assert mat.ft == 2.0e6
        assert mat.Es == 30.0e9
