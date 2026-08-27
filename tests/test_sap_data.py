"""Tests for fea_toolkit.model.sap_data — dataclasses and section dispatch."""

"""Tests for the model layer: dataclasses, geometry utilities, and sections."""

import math
from pathlib import Path

import numpy as np
import pytest

from fea_toolkit.model.geometry import (
    SpatialGrid,
    compute_t_location,
    get_local_axes,
    get_SAP_vecxz,
    interp,
    list_interp,
    point_on_segment,
    rotate_about_axis,
    trapezoidal_force_split,
)
from fea_toolkit.model.sap_data import (
    AngleSection,
    AreaElement,
    AreaGravityLoad,
    BoxSection,
    ChannelSection,
    CircularSection,
    Constraint,
    DoubleAngleSection,
    EncasedSection,
    FrameDistributedLoad,
    FrameElement,
    GeneralSection,
    GravityLoad,
    Group,
    ISection,
    JointLoad,
    LoadCase,
    LoadCombination,
    LoadPattern,
    MassSource,
    Material,
    Node,
    PipeSection,
    RectangularSection,
    Restraint,
    SAPModelData,
    SDSection,
    Section,
    ShellSection,
    TeeSection,
    default_coord_sys,
    patterns_from_case,
)

# ============================================================================
# Fixtures
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ============================================================================
# Dataclass construction tests
# ============================================================================


# ═══════════════════════════════════════════════════════════════════
# SAP2000 data model
# ═══════════════════════════════════════════════════════════════════


class TestNode:
    def test_defaults(self):
        n = Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0)
        assert n.node_id == "1"
        assert n.node_tag == 1
        assert n.x == 0.0
        assert n.y == 0.0
        assert n.z == 0.0
        assert n.is_special is False

    def test_special_flag(self):
        n = Node(node_id="5", node_tag=5, x=1.0, y=2.0, z=3.0, is_special=True)
        assert n.is_special is True


class TestRestraint:
    def test_defaults(self):
        r = Restraint(dofs=[1, 1, 1, 0, 0, 0])
        assert r.dofs == [1, 1, 1, 0, 0, 0]

    def test_pinned(self):
        r = Restraint(dofs=[1, 1, 1, 1, 1, 1])
        assert all(d == 1 for d in r.dofs)

    def test_free(self):
        r = Restraint(dofs=[0, 0, 0, 0, 0, 0])
        assert all(d == 0 for d in r.dofs)


class TestMaterial:
    def test_defaults(self):
        m = Material(name="Steel_A992", type="Steel")
        assert m.name == "Steel_A992"
        assert m.type == "Steel"
        assert m.E_mod == 0.0
        assert m.Fy is None

    def test_with_properties(self):
        m = Material(
            name="Concrete_40MPa",
            type="Concrete",
            E_mod=3.28e10,
            nu=0.2,
            Fc=4e7,
            unit_weight=24000.0,
        )
        assert m.E_mod == 3.28e10
        assert m.Fc == 4e7
        assert m.Fy is None

    def test_shear_modulus_uses_g_mod(self):
        m = Material(name="Steel", type="Steel", E_mod=200e9, G_mod=80e9, nu=0.3)
        assert m.shear_modulus() == 80e9

    def test_shear_modulus_derives_from_nu(self):
        m = Material(name="Steel", type="Steel", E_mod=200e9, nu=0.2)
        assert m.shear_modulus() == pytest.approx(200e9 / (2.0 * (1.0 + 0.2)))

    def test_shear_modulus_default_nu_concrete(self):
        # Concrete ν default 0.2 → G = E / 2.4
        m = Material(name="Conc", type="Concrete", E_mod=30e9)
        assert m.shear_modulus() == pytest.approx(30e9 / 2.4)

    def test_shear_modulus_default_nu_steel(self):
        # Steel ν default 0.3 → G = E / 2.6
        m = Material(name="Steel", type="Steel", E_mod=200e9)
        assert m.shear_modulus() == pytest.approx(200e9 / 2.6)

    def test_shear_modulus_e_override(self):
        # Caller-supplied E is used when E_mod is missing on the material.
        m = Material(name="Steel", type="Steel", E_mod=0.0)
        assert m.shear_modulus(E_mod=200e9) == pytest.approx(200e9 / 2.6)


class TestSection:
    def test_defaults(self):
        s = Section(
            name="W200x52",
            shape="I/Wide Flange",
            material="Steel_A992",
            A=0.00665,
            I33=5.25e-5,
            I22=1.77e-5,
            J=1.0e-6,
        )
        assert s.name == "W200x52"
        assert s.Z33 is None
        assert s.manufacturer is None

    def test_with_plastic_moduli(self):
        s = Section(
            name="W200x52",
            shape="I/Wide Flange",
            material="Steel_A992",
            A=0.00665,
            I33=5.25e-5,
            I22=1.77e-5,
            J=1.0e-6,
            Z33=1.2e-4,
            Z22=6.0e-5,
        )
        assert s.Z33 == 1.2e-4
        assert s.Z22 == 6.0e-5


class TestFrameElement:
    def test_defaults(self):
        fe = FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2")
        assert fe.elem_id == "1"
        assert fe.node_i == "1"
        assert fe.node_j == "2"
        assert fe.angle == 0.0
        assert fe.inactive is False
        assert fe.parent_id is None
        assert fe.child_ids == []
        assert fe.t_locations == []

    def test_parent_child(self):
        fe = FrameElement(
            elem_id="10",
            elem_tag=10,
            node_i="5",
            node_j="8",
            parent_id="2",
            child_ids=["10-0", "10-1"],
            t_locations=[0.3, 0.7],
        )
        assert fe.parent_id == "2"
        assert len(fe.child_ids) == 2
        assert fe.t_locations == [0.3, 0.7]


class TestAreaElement:
    def test_defaults(self):
        ae = AreaElement(area_id="1", area_tag=1, node_ids=["1", "2", "3"])
        assert len(ae.node_ids) == 3
        assert ae.thickness == 0.0


class TestLoadPattern:
    def test_defaults(self):
        lp = LoadPattern(name="DEAD", pattern_type="DEAD")
        assert lp.self_weight_factor == 0.0


class TestJointLoad:
    def test_defaults(self):
        jl = JointLoad(pattern="DEAD", node_id="1", fx=1000.0, fz=-9800.0)
        assert jl.fx == 1000.0
        assert jl.fz == -9800.0
        assert jl.fy == 0.0
        assert jl.coord_sys == "GLOBAL"


class TestFrameDistributedLoad:
    def test_defaults(self):
        fdl = FrameDistributedLoad(
            pattern="WIND",
            frame_id="25",
            direction="X",
            load_type="Force",
            shape="Uniform",
            val_a=5000.0,
            val_b=5000.0,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=5.0,
        )
        assert fdl.pattern == "WIND"
        assert fdl.val_a == 5000.0
        assert fdl.rdist_a == 0.0
        assert fdl.rdist_b == 1.0

    def test_trapezoidal(self):
        fdl = FrameDistributedLoad(
            pattern="WIND",
            frame_id="25",
            direction="X",
            load_type="Force",
            shape="Trapezoidal",
            val_a=3000.0,
            val_b=8000.0,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=5.0,
        )
        assert fdl.shape == "Trapezoidal"


class TestGravityLoad:
    def test_defaults(self):
        gl = GravityLoad(pattern="DEAD", frame_id="1", multiplier_z=-1.0)
        assert gl.multiplier_z == -1.0
        assert gl.multiplier_x == 0.0


class TestAreaGravityLoad:
    def test_defaults(self):
        agl = AreaGravityLoad(pattern="DEAD", area_id="10", multiplier_z=-1.0)
        assert agl.pattern == "DEAD"
        assert agl.area_id == "10"
        assert agl.multiplier_z == -1.0
        assert agl.multiplier_x == 0.0
        assert agl.coord_sys == "GLOBAL"

    def test_all_multipliers(self):
        agl = AreaGravityLoad(
            pattern="QUAKE",
            area_id="5",
            multiplier_x=0.3,
            multiplier_y=0.3,
            multiplier_z=-1.0,
            coord_sys="LOCAL",
        )
        assert agl.multiplier_x == 0.3
        assert agl.multiplier_y == 0.3
        assert agl.multiplier_z == -1.0
        assert agl.coord_sys == "LOCAL"


class TestMassSource:
    def test_defaults(self):
        ms = MassSource(name="MASS1", elements=True, masses=True, loads=True)
        assert ms.elements is True
        assert ms.masses is True
        assert ms.loads is True


class TestCoordSys:
    def test_default(self):
        assert default_coord_sys.name == "GLOBAL"
        assert default_coord_sys.coord_type == "Cartesian"


class TestConstraint:
    def test_defaults(self):
        c = Constraint(name="BODY1", constraint_type="BODY")
        assert c.coord_sys == "GLOBAL"


class TestLoadCase:
    def test_defaults(self):
        lc = LoadCase(
            case_name="DEAD",
            case_type="Linear Static",
            design_type_option="Prog Det",
            design_type="DEAD",
            design_action_option="Prog Det",
            design_action="Non-Composite",
        )
        assert lc.run_case is False

    def test_patterns_from_case_skips_malformed_entries(self):
        """Non-dict assignment entries are filtered without erroring."""
        lc = LoadCase(
            case_name="EQLX",
            case_type="Linear Static",
            design_type_option="Prog Det",
            design_type="QUAKE",
            design_action_option="Prog Det",
            design_action="Non-Composite",
            case_data={
                "CASE - STATIC 1 - LOAD ASSIGNMENTS": [
                    {"LoadName": "DEAD", "LoadSF": 1.0},
                    "malformed",
                    42,
                    {"LoadSF": 0.5},  # no LoadName -> skipped
                ]
            },
        )
        assert patterns_from_case(lc) == {"DEAD": 1.0}

    def test_patterns_from_case_empty_when_all_malformed(self):
        """A list of only malformed entries yields an empty mapping."""
        lc = LoadCase(
            case_name="EQLX",
            case_type="Linear Static",
            design_type_option="Prog Det",
            design_type="QUAKE",
            design_action_option="Prog Det",
            design_action="Non-Composite",
            case_data={"CASE - STATIC 1 - LOAD ASSIGNMENTS": ["bad", 123]},
        )
        assert patterns_from_case(lc) == {}


class TestLoadCombination:
    def test_with_cases(self):
        lc = LoadCombination(
            name="1.2DL+1.6LL",
            combo_type="Strength",
            cases={"DEAD": 1.2, "LIVE": 1.6},
        )
        assert lc.cases["DEAD"] == 1.2
        assert lc.cases["LIVE"] == 1.6


class TestGroup:
    def test_defaults(self):
        g = Group(name="COLUMNS")
        assert g.objects == []

    def test_with_objects(self):
        g = Group(name="COLUMNS", objects=["Frame:1", "Frame:2"])
        assert len(g.objects) == 2


class TestSectionSubclasses:
    """Tests for the section type hierarchy."""

    def test_isection_creation(self):
        sec = ISection(
            name="W200x52",
            shape="I/Wide Flange",
            material="Steel",
            A=0.00665,
            I33=5.25e-5,
            I22=1.77e-5,
            J=1e-6,
            depth=0.206,
            bf=0.134,
            tf=0.0126,
            tw=0.0072,
        )
        assert sec.shape_id == "I"
        assert sec.depth == 0.206
        assert sec.bf == 0.134

    def test_isection_fiber_patches(self):
        sec = ISection(
            name="W200x52",
            shape="I/Wide Flange",
            material="Steel",
            A=0.00665,
            I33=5.25e-5,
            I22=1.77e-5,
            J=1e-6,
            depth=0.4,
            bf=0.2,
            tf=0.015,
            tw=0.01,
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        assert len(patches) == 3  # bottom flange, web, top flange
        # Check bottom flange
        assert patches[0][0] == "rect"
        assert patches[0][1] == 1  # mat_tag
        # Check web
        assert patches[1][0] == "rect"
        assert patches[1][3] == 4  # nfz
        # Verify y-coordinates are ordered
        _, _, _, _, y1, _z1, _y2, _z2 = patches[2]
        assert y1 > 0  # top flange is in positive y

    def test_general_section(self):
        sec = GeneralSection(
            name="CatalogueSec",
            shape="General",
            material="Steel",
            A=0.01,
            I33=1e-4,
            I22=5e-5,
            J=1e-6,
        )
        assert sec.shape_id == "GEN"
        with pytest.raises(NotImplementedError):
            sec.to_fiber_patches(mat_tag=1)

    def test_pipe_section(self):
        sec = PipeSection(
            name="CHS_273x10",
            shape="Pipe",
            material="Steel",
            A=0.00826,
            I33=7.1e-5,
            I22=7.1e-5,
            J=1.42e-4,
            od=0.273,
            t=0.01,
        )
        assert sec.od == 0.273
        assert sec.shape_id == "CHS"

    def test_box_section(self):
        sec = BoxSection(
            name="Box_200x100x8",
            shape="Box/Tube",
            material="Steel",
            A=0.00445,
            I33=2.5e-5,
            I22=1.2e-5,
            J=3.0e-5,
            depth=0.2,
            bf=0.1,
            tf=0.008,
            tw=0.008,
        )
        assert sec.shape_id == "RHS"

    def test_rectangular_section(self):
        sec = RectangularSection(
            name="R_300x600",
            shape="Rectangular",
            material="Concrete",
            A=0.18,
            I33=0.0054,
            I22=0.00135,
            J=0.0,
            depth=0.6,
            bf=0.3,
        )
        patches = sec.to_fiber_patches(mat_tag=2)
        # Now returns: confined core + 4 cover patches + 2 steel layers
        assert len(patches) == 7
        # Core uses mat_tag+1 (confined concrete)
        assert patches[0][1] == 3
        # Cover uses mat_tag (unconfined concrete)
        assert patches[1][1] == 2
        # Steel layers use mat_tag+2
        assert patches[5][1] == 4

    def test_circular_section(self):
        sec = CircularSection(
            name="Bar_32",
            shape="Circle",
            material="Steel",
            A=0.000804,
            I33=5.15e-8,
            I22=5.15e-8,
            J=1.03e-7,
            diameter=0.032,
        )
        assert sec.diameter == 0.032

    def test_channel_section(self):
        sec = ChannelSection(
            name="C_200x50",
            shape="Channel",
            material="Steel",
            A=0.00215,
            I33=1.25e-5,
            I22=4.78e-7,
            J=4.2e-8,
            depth=0.2032,
            bf=0.0508,
            tf=0.00965,
            tw=0.00635,
        )
        assert sec.shape_id == "CH"

    def test_angle_section(self):
        sec = AngleSection(
            name="L_100x100x10",
            shape="Angle",
            material="Steel",
            A=0.00193,
            I33=1.8e-6,
            I22=1.8e-6,
            J=1e-8,
            depth=0.1,
            bf=0.1,
            tf=0.01,
            tw=0.01,
        )
        assert sec.shape_id == "A"

    def test_double_angle_section(self):
        sec = DoubleAngleSection(
            name="2L_100x100x10",
            shape="Double Angle",
            material="Steel",
            A=0.00386,
            I33=3.6e-6,
            I22=3.6e-6,
            J=2e-8,
            depth=0.1,
            bf=0.21,
            tf=0.01,
            tw=0.01,
            dis=0.01,
        )
        assert sec.shape_id == "AA"
        assert sec.dis == 0.01

    def test_tee_section(self):
        sec = TeeSection(
            name="T_150x100x10",
            shape="Tee",
            material="Steel",
            A=0.0024,
            I33=2.0e-6,
            I22=1.5e-6,
            J=5e-9,
            depth=0.15,
            bf=0.1,
            tf=0.01,
            tw=0.008,
        )
        assert sec.shape_id == "T"

    def test_sd_section(self):
        sec = SDSection(
            name="SD_Custom",
            shape="SD Section",
            material="Steel",
            A=0.01,
            I33=1e-4,
            I22=5e-5,
            J=0.0,
        )
        assert sec.shape_id == "SD"

    def test_encased_section(self):
        inner = ISection(
            name="W200x52",
            shape="I/Wide Flange",
            material="Steel",
            A=0.00665,
            I33=5.25e-5,
            I22=1.77e-5,
            J=1e-6,
            depth=0.206,
            bf=0.134,
            tf=0.0126,
            tw=0.0072,
        )
        sec = EncasedSection(
            name="SRC_400x400",
            shape="Concrete Encasement Rectangle",
            material="Steel",
            A=0.16,
            I33=0.00213,
            I22=0.00213,
            J=2e-5,
            embedded_section=inner,
            encasement_material="Concrete_40MPa",
            encasement_depth=0.4,
            encasement_bf=0.4,
        )
        assert sec.embedded_section is not None
        assert sec.encasement_material == "Concrete_40MPa"

    def test_shell_section(self):
        sec = ShellSection(
            name="Shell_200mm",
            shape="Shell",
            material="Concrete",
            A=0.2,
            I33=0,
            I22=0,
            J=0,
            thickness=0.2,
        )
        assert sec.thickness == 0.2
        assert sec.shape_id == "GEN"  # Shell is not in SHAPE_NAMES

    def test_shape_id_mapping(self):
        assert (
            ISection(name="", shape="I/Wide Flange", material="", A=0, I33=0, I22=0, J=0).shape_id
            == "I"
        )
        assert (
            ISection(name="", shape="WIDE FLANGE", material="", A=0, I33=0, I22=0, J=0).shape_id
            == "I"
        )
        assert (
            PipeSection(name="", shape="Pipe", material="", A=0, I33=0, I22=0, J=0).shape_id
            == "CHS"
        )
        assert (
            BoxSection(name="", shape="Box/Tube", material="", A=0, I33=0, I22=0, J=0).shape_id
            == "RHS"
        )

    def test_base_section_raises(self):
        """Base Section.to_fiber_patches() should raise NotImplementedError."""
        sec = Section(name="", shape="", material="", A=0, I33=0, I22=0, J=0)
        with pytest.raises(NotImplementedError):
            sec.to_fiber_patches(mat_tag=1)


class TestSAPModelData:
    @pytest.fixture
    def minimal_model(self):
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=5.0, y=0.0, z=0.0),
        }
        sections = {
            "W200x52": Section(
                name="W200x52",
                shape="I/Wide Flange",
                material="Steel",
                A=0.00665,
                I33=5.25e-5,
                I22=1.77e-5,
                J=1e-6,
            ),
        }
        return SAPModelData(
            nodes=nodes,
            restraints={},
            materials={},
            sections=sections,
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )

    def test_minimal_creation(self, minimal_model):
        assert len(minimal_model.nodes) == 2
        assert len(minimal_model.sections) == 1
        assert minimal_model.units == {"F": "N", "L": "m", "T": "C"}

    def test_default_units(self):
        """Default length unit should be meters."""
        m = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        assert m.units["L"] == "m", f"Expected default length unit 'm', got '{m.units['L']}'"

    def test_custom_units(self):
        m = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            units={"F": "kip", "L": "in", "T": "F"},
        )
        assert m.units == {"F": "kip", "L": "in", "T": "F"}

    def test_new_load_fields_default(self):
        """Verify recently-added load fields default to empty lists."""
        m = SAPModelData(
            nodes={},
            restraints={},
            materials={},
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )
        assert m.area_gravity_loads == []
        assert m.frame_gravity_loads == []
        assert m.area_uniform_loads == []


# ============================================================================
# Geometry utility tests
# ============================================================================


class TestGetSAPVecxz:
    def test_horizontal_element(self):
        """A horizontal element along X: vecxz = cross(X, Z) = (0, -1, 0)."""
        vec_x = np.array([5.0, 0.0, 0.0])
        vecxz = get_SAP_vecxz(vec_x, angle=0.0)
        # cross([1,0,0], [0,0,1]) = [0, -1, 0]
        expected = np.array([0.0, -1.0, 0.0])
        assert np.allclose(vecxz, expected, atol=1e-6)

    def test_vertical_element(self):
        """A vertical element along Z should have vecxz = (0, 1, 0) or (0, -1, 0)."""
        vec_x = np.array([0.0, 0.0, 10.0])
        vecxz = get_SAP_vecxz(vec_x, angle=0.0)
        expected = np.array([0.0, 1.0, 0.0])  # global Y
        assert np.allclose(vecxz, expected, atol=1e-6)

    def test_with_angle(self):
        """Rotation should change vecxz."""
        vec_x = np.array([5.0, 0.0, 0.0])
        vecxz_90 = get_SAP_vecxz(vec_x, angle=90.0)
        # With 90° rotation about X, vecxz should become (0, 0, -1)
        expected = np.array([0.0, 0.0, -1.0])
        assert np.allclose(vecxz_90, expected, atol=1e-6)

    def test_zero_length_raises(self):
        vec_x = np.array([0.0, 0.0, 0.0])
        with pytest.raises(ValueError, match="zero length"):
            get_SAP_vecxz(vec_x)


class TestGetLocalAxes:
    """Direct tests for get_local_axes(axis, angle) → (vx, vy, vz)."""

    def test_horizontal_beam(self):
        """Beam along X: vx=(1,0,0), vy=(0,0,1), vz=(0,-1,0)."""
        vx, vy, vz = get_local_axes(np.array([5.0, 0.0, 0.0]))
        np.testing.assert_array_almost_equal(vx, [1, 0, 0])
        np.testing.assert_array_almost_equal(vy, [0, 0, 1])
        np.testing.assert_array_almost_equal(vz, [0, -1, 0])

    def test_vertical_column(self):
        """Column along +Z: vx=(0,0,1), vy=(1,0,0), vz=(0,1,0)."""
        vx, vy, vz = get_local_axes(np.array([0.0, 0.0, 5.0]))
        np.testing.assert_array_almost_equal(vx, [0, 0, 1])
        np.testing.assert_array_almost_equal(vy, [1, 0, 0])
        np.testing.assert_array_almost_equal(vz, [0, 1, 0])

    def test_vertical_downward(self):
        """Column along -Z: vx=(0,0,-1), vy=(1,0,0), vz=(0,-1,0)."""
        vx, vy, vz = get_local_axes(np.array([0.0, 0.0, -5.0]))
        np.testing.assert_array_almost_equal(vx, [0, 0, -1])
        np.testing.assert_array_almost_equal(vy, [1, 0, 0])
        np.testing.assert_array_almost_equal(vz, [0, -1, 0])

    def test_with_angle(self):
        """Beam along X with 45° rotation: vy rotated from (0,0,1) about x."""
        vx, vy, vz = get_local_axes(np.array([5.0, 0.0, 0.0]), angle=45.0)
        np.testing.assert_array_almost_equal(vx, [1, 0, 0])
        np.testing.assert_array_almost_equal(vy, [0, -0.70710678, 0.70710678], decimal=6)
        np.testing.assert_array_almost_equal(vz, [0, -0.70710678, -0.70710678], decimal=6)

    def test_90_degree_swap(self):
        """Angle=90°: vy_90 = vz_0, vz_90 = -vy_0."""
        _vx0, vy0, vz0 = get_local_axes(np.array([5.0, 0.0, 0.0]), angle=0.0)
        vx, vy, vz = get_local_axes(np.array([5.0, 0.0, 0.0]), angle=90.0)
        np.testing.assert_array_almost_equal(vx, [1, 0, 0])
        np.testing.assert_array_almost_equal(vy, vz0)
        np.testing.assert_array_almost_equal(vz, -vy0)

    def test_orthonormal(self):
        """vx, vy, vz are always orthonormal (unit-length + orthogonal)."""
        vectors = [
            np.array([5.0, 0.0, 0.0]),
            np.array([0.0, 0.0, 10.0]),
            np.array([3.0, 4.0, 0.0]),
            np.array([1.0, 2.0, 3.0]),
            np.array([0.0, -5.0, 0.0]),
        ]
        for axis in vectors:
            for angle in (0.0, 30.0, -45.0, 90.0):
                vx, vy, vz = get_local_axes(axis, angle=angle)
                for name, v in [("vx", vx), ("vy", vy), ("vz", vz)]:
                    assert abs(np.linalg.norm(v) - 1.0) < 1e-10, (
                        f"{name} not unit for axis={axis}, angle={angle}"
                    )
                assert abs(np.dot(vx, vy)) < 1e-10, f"vx·vy not zero for axis={axis}, angle={angle}"
                assert abs(np.dot(vx, vz)) < 1e-10, f"vx·vz not zero for axis={axis}, angle={angle}"
                assert abs(np.dot(vy, vz)) < 1e-10, f"vy·vz not zero for axis={axis}, angle={angle}"

    def test_zero_length_raises(self):
        with pytest.raises(ValueError, match="zero length"):
            get_local_axes(np.array([0.0, 0.0, 0.0]))

    def test_beam_along_y(self):
        """Beam along Y: vx=(0,1,0), vy=(0,0,1), vz=(1,0,0)."""
        vx, vy, vz = get_local_axes(np.array([0.0, 4.0, 0.0]))
        np.testing.assert_array_almost_equal(vx, [0, 1, 0])
        np.testing.assert_array_almost_equal(vy, [0, 0, 1])
        np.testing.assert_array_almost_equal(vz, [1, 0, 0])

    def test_beam_diagonal_xy(self):
        """Diagonal in XY: vx=(0.707,0.707,0), vy=(0,0,1), vz=(0.707,-0.707,0)."""
        vx, vy, vz = get_local_axes(np.array([1.0, 1.0, 0.0]))
        np.testing.assert_array_almost_equal(vx, [0.70710678, 0.70710678, 0], decimal=6)
        np.testing.assert_array_almost_equal(vy, [0, 0, 1])
        np.testing.assert_array_almost_equal(vz, [0.70710678, -0.70710678, 0], decimal=6)

    def test_returns_numpy_arrays(self):
        """Result components are numpy arrays, not lists."""
        vx, vy, vz = get_local_axes([3.0, 0.0, 0.0])
        assert isinstance(vx, np.ndarray)
        assert isinstance(vy, np.ndarray)
        assert isinstance(vz, np.ndarray)

    def test_accepts_list_input(self):
        """Should accept plain Python lists."""
        vx, vy, vz = get_local_axes([5.0, 0.0, 0.0])
        np.testing.assert_array_almost_equal(vx, [1, 0, 0])
        np.testing.assert_array_almost_equal(vy, [0, 0, 1])
        np.testing.assert_array_almost_equal(vz, [0, -1, 0])


class TestRotateAboutAxis:
    def test_rotate_90_about_z(self):
        v = np.array([1.0, 0.0, 0.0])
        axis = np.array([0.0, 0.0, 1.0])
        result = rotate_about_axis(v, axis, math.pi / 2)
        expected = np.array([0.0, 1.0, 0.0])
        assert np.allclose(result, expected, atol=1e-6)


class TestPointOnSegment:
    def test_on_segment(self):
        assert point_on_segment([2.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])

    def test_on_endpoint(self):
        assert point_on_segment([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])

    def test_not_on_segment(self):
        assert not point_on_segment([10.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])

    def test_off_line(self):
        assert not point_on_segment([2.0, 1.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])


class TestComputeTLocation:
    def test_at_start(self):
        t = compute_t_location([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])
        assert t == 0.0

    def test_at_end(self):
        t = compute_t_location([5.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])
        assert t == 1.0

    def test_midpoint(self):
        t = compute_t_location([2.5, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])
        assert t == 0.5

    def test_off_segment_clamped(self):
        t = compute_t_location([10.0, 0.0, 0.0], [0.0, 0.0, 0.0], [5.0, 0.0, 0.0])
        assert t == 1.0


class TestInterp:
    def test_exact_match(self):
        assert interp(0.7, 0.7, 1.9, 1.4, 2.3) == 1.4

    def test_midpoint(self):
        result = interp(1.3, 0.7, 1.9, 1.4, 2.3)
        expected = 1.4 + (1.3 - 0.7) / (1.9 - 0.7) * (2.3 - 1.4)
        assert result == expected

    def test_example_from_docstring(self):
        assert interp(1.5, 0.7, 1.9, 1.4, 2.3) == 2.0

    def test_round_example(self):
        assert round(interp(2.3, 0.7, 1.9, 1.4, 2.3), 6) == 2.6

    def test_below_range(self):
        assert interp(-0.1, 0.7, 1.9, 1.4, 2.3) == 0.8

    def test_none_values(self):
        assert interp(0.5, 0.0, 1.0, None, 5.0) is None


class TestListInterp:
    def test_exact_match(self):
        assert list_interp(0.5, [0.2, 0.8, 1.1], [1.1, 1.35, 1.4]) == 1.225

    def test_below_range_extend_false(self):
        assert list_interp(0.08, [0.2, 0.8], [1.1, 1.35]) == 0

    def test_below_range_extrapolate(self):
        assert list_interp(0.08, [0.2, 0.8], [1.1, 1.35], extend=True, extrapolate=True) == 1.05

    def test_below_range_no_extrapolate(self):
        assert list_interp(0.08, [0.2, 0.8], [1.1, 1.35], extend=True, extrapolate=False) == 1.1


class TestTrapezoidalForceSplit:
    def test_no_split(self):
        """If no t-values (besides 0 and 1), return full segment."""
        f_data = ((0.2, 1.2), (0.8, 5.1))
        result = trapezoidal_force_split(f_data, [])
        assert len(result) == 1
        # The returned segment should cover the full [0,1]
        seg = result[0]
        assert len(seg) == 2

    def test_split_at_midpoint(self):
        """Splitting a uniform load at t=0.5."""
        f_data = ((0.0, 10.0), (1.0, 10.0))
        result = trapezoidal_force_split(f_data, [0.5])
        assert len(result) == 2
        # Both halves should still have force=10
        for seg in result:
            assert abs(seg[0][1] - 10.0) < 1e-9 or abs(seg[1][1] - 10.0) < 1e-9

    def test_example_from_docstring(self):
        f_data = ((0.2, 1.2), (0.8, 5.1))
        t_values = [0.1, 0.5, 0.75, 0.95]
        result = trapezoidal_force_split(f_data, t_values)
        assert len(result) == 5  # n+1 segments for n t-values
        # Check specific segment (middle)
        # Segment covering 0.1-0.5 with force 1.2-3.15 transitioning
        assert abs(result[1][0][0] - 0.25) < 1e-9
        assert abs(result[1][1][1] - 3.15) < 1e-9


class TestSpatialGrid:
    """Unit tests for SpatialGrid — spatial indexing utility."""

    def test_add_and_query(self):
        grid = SpatialGrid(cell_size=1.0)
        grid.add_point("A", (0.5, 0.5, 0.5))
        grid.add_point("B", (1.5, 1.5, 1.5))
        results = grid.points_in_bbox((0, 0, 0), (2, 2, 2))
        assert len(results) == 2

    def test_empty_bbox(self):
        grid = SpatialGrid(cell_size=1.0)
        results = grid.points_in_bbox((10, 10, 10), (11, 11, 11))
        assert len(results) == 0

    def test_cell_boundary_negative_coords(self):
        """Points with negative coordinates map to correct cells.

        Note: SpatialGrid does cell-level pre-filtering, so a query
        returns all points from cells overlapping the bbox, even if
        the point itself is outside the bbox.  This test verifies
        the cell mapping is correct.
        """
        grid = SpatialGrid(cell_size=1.0)
        grid.add_point("N1", (-0.5, -0.5, -0.5))  # → cell (-1,-1,-1)
        grid.add_point("N2", (-1.5, -1.5, -1.5))  # → cell (-2,-2,-2)
        # Query covering cells (-1,-1,-1): includes N1 + possibly N2
        r1 = grid.points_in_bbox((-1, -1, -1), (0, 0, 0))
        ids_1 = {p[0] for p in r1}
        assert "N1" in ids_1  # N1 is in overlapping cell (-1,-1,-1)
        # Query covering cells (-2,-2,-2): includes N2
        r2 = grid.points_in_bbox((-2, -2, -2), (-1, -1, -1))
        ids_2 = {p[0] for p in r2}
        assert "N2" in ids_2  # N2 is in overlapping cell (-2,-2,-2)

    def test_multi_cell_query(self):
        """Bbox spanning multiple cells returns points from all touched cells."""
        grid = SpatialGrid(cell_size=2.0)  # 2m cells
        grid.add_point("A", (0, 0, 0))  # cell (0,0,0)
        grid.add_point("B", (2.1, 2.1, 2.1))  # cell (1,1,1) — just over boundary
        grid.add_point("C", (10, 10, 10))  # cell (5,5,5) — far away
        results = grid.points_in_bbox((-1, -1, -1), (5, 5, 5))
        assert len(results) == 2
        ids = {r[0] for r in results}
        assert ids == {"A", "B"}

    def test_cell_size_consistency(self):
        """Cell boundary at multiples of cell_size is deterministic.

        SpatialGrid is a cell-level pre-filter — a query returns all
        points from overlapping cells, even if the point itself lies
        outside the query bbox.  The caller (e.g. point_on_segment)
        is responsible for exact filtering.
        """
        grid = SpatialGrid(cell_size=0.5)
        # Exactly on cell boundaries
        grid.add_point("X", (0.0, 0.0, 0.0))  # → cell (0,0,0)
        grid.add_point("Y", (0.5, 0.5, 0.5))  # → cell (1,1,1)
        grid.add_point("Z", (1.0, 1.0, 1.0))  # → cell (2,2,2)
        # All three should be in distinct cells
        r_all = grid.points_in_bbox((-0.1, -0.1, -0.1), (1.1, 1.1, 1.1))
        assert len(r_all) == 3
        # Query covering cells (0,0,0) and (1,1,1) — returns X and Y
        # (X is outside the bbox but in an overlapping cell)
        r_xy = grid.points_in_bbox((0.25, 0.25, 0.25), (0.75, 0.75, 0.75))
        ids = {p[0] for p in r_xy}
        assert "Y" in ids  # Y is inside the bbox

    def test_default_cell_size(self):
        """Default cell_size is 1.0."""
        grid = SpatialGrid()
        assert grid.cell_size == 1.0

    def test_custom_cell_size(self):
        """Custom cell_size is stored and affects cell mapping."""
        grid = SpatialGrid(cell_size=5.0)
        grid.add_point("P", (12.0, 12.0, 12.0))  # cell (2,2,2)
        results = grid.points_in_bbox((10, 10, 10), (15, 15, 15))
        assert len(results) == 1
        assert results[0][0] == "P"

    def test_large_model_extent(self):
        """Grid with large cell_size still works (simulating large model)."""
        grid = SpatialGrid(cell_size=10.0)
        for i in range(5):
            grid.add_point(f"N{i}", (i * 10.0, 0, 0))
        # Query that spans all cells
        results = grid.points_in_bbox((-1, -1, -1), (50, 1, 1))
        assert len(results) == 5


# ============================================================================
# SectionLibrary tests (requires fixture data file)
# ============================================================================
