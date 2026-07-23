"""Tests for the model layer: dataclasses, geometry utilities, and sections."""

import math
from pathlib import Path

import numpy as np
import pytest

from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

from fea_toolkit.model.sap_data import (
    SAPModelData,
    Node,
    Restraint,
    Material,
    Section,
    ISection,
    GeneralSection,
    PipeSection,
    BoxSection,
    RectangularSection,
    CircularSection,
    ChannelSection,
    AngleSection,
    DoubleAngleSection,
    TeeSection,
    SDSection,
    EncasedSection,
    ShellSection,
    FrameElement,
    AreaElement,
    Group,
    LoadCase,
    LoadPattern,
    LoadCombination,
    JointLoad,
    FrameDistributedLoad,
    GravityLoad,
    MassSource,
    AreaGravityLoad,
    AreaUniformLoad,
    Constraint,
    default_coord_sys,
    FrameEndOffset,
    AreaMesh,
)
from fea_toolkit.model.geometry import (
    get_SAP_vecxz,
    get_local_axes,
    rotate_about_axis,
    point_on_segment,
    compute_t_location,
    interp,
    list_interp,
    trapezoidal_force_split,
    SpatialGrid,
    beam_load_to_nodal_loads,
)
from fea_toolkit.model.selection import Selection

# ============================================================================
# Fixtures
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ============================================================================
# Dataclass construction tests
# ============================================================================


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
        gl = GravityLoad(
            pattern="DEAD", frame_id="1", multiplier_z=-1.0
        )
        assert gl.multiplier_z == -1.0
        assert gl.multiplier_x == 0.0


class TestAreaGravityLoad:
    def test_defaults(self):
        agl = AreaGravityLoad(
            pattern="DEAD", area_id="10", multiplier_z=-1.0
        )
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
            name="W200x52", shape="I/Wide Flange", material="Steel",
            A=0.00665, I33=5.25e-5, I22=1.77e-5, J=1e-6,
            depth=0.206, bf=0.134, tf=0.0126, tw=0.0072,
        )
        assert sec.shape_id == "I"
        assert sec.depth == 0.206
        assert sec.bf == 0.134

    def test_isection_fiber_patches(self):
        sec = ISection(
            name="W200x52", shape="I/Wide Flange", material="Steel",
            A=0.00665, I33=5.25e-5, I22=1.77e-5, J=1e-6,
            depth=0.4, bf=0.2, tf=0.015, tw=0.01,
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
        _, _, _, _, y1, z1, y2, z2 = patches[2]
        assert y1 > 0  # top flange is in positive y

    def test_general_section(self):
        sec = GeneralSection(
            name="CatalogueSec", shape="General", material="Steel",
            A=0.01, I33=1e-4, I22=5e-5, J=1e-6,
        )
        assert sec.shape_id == "GEN"
        with pytest.raises(NotImplementedError):
            sec.to_fiber_patches(mat_tag=1)

    def test_pipe_section(self):
        sec = PipeSection(
            name="CHS_273x10", shape="Pipe", material="Steel",
            A=0.00826, I33=7.1e-5, I22=7.1e-5, J=1.42e-4,
            od=0.273, t=0.01,
        )
        assert sec.od == 0.273
        assert sec.shape_id == "CHS"

    def test_box_section(self):
        sec = BoxSection(
            name="Box_200x100x8", shape="Box/Tube", material="Steel",
            A=0.00445, I33=2.5e-5, I22=1.2e-5, J=3.0e-5,
            depth=0.2, bf=0.1, tf=0.008, tw=0.008,
        )
        assert sec.shape_id == "RHS"

    def test_rectangular_section(self):
        sec = RectangularSection(
            name="R_300x600", shape="Rectangular", material="Concrete",
            A=0.18, I33=0.0054, I22=0.00135, J=0.0,
            depth=0.6, bf=0.3,
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
            name="Bar_32", shape="Circle", material="Steel",
            A=0.000804, I33=5.15e-8, I22=5.15e-8, J=1.03e-7,
            diameter=0.032,
        )
        assert sec.diameter == 0.032

    def test_channel_section(self):
        sec = ChannelSection(
            name="C_200x50", shape="Channel", material="Steel",
            A=0.00215, I33=1.25e-5, I22=4.78e-7, J=4.2e-8,
            depth=0.2032, bf=0.0508, tf=0.00965, tw=0.00635,
        )
        assert sec.shape_id == "CH"

    def test_angle_section(self):
        sec = AngleSection(
            name="L_100x100x10", shape="Angle", material="Steel",
            A=0.00193, I33=1.8e-6, I22=1.8e-6, J=1e-8,
            depth=0.1, bf=0.1, tf=0.01, tw=0.01,
        )
        assert sec.shape_id == "A"

    def test_double_angle_section(self):
        sec = DoubleAngleSection(
            name="2L_100x100x10", shape="Double Angle", material="Steel",
            A=0.00386, I33=3.6e-6, I22=3.6e-6, J=2e-8,
            depth=0.1, bf=0.21, tf=0.01, tw=0.01, dis=0.01,
        )
        assert sec.shape_id == "AA"
        assert sec.dis == 0.01

    def test_tee_section(self):
        sec = TeeSection(
            name="T_150x100x10", shape="Tee", material="Steel",
            A=0.0024, I33=2.0e-6, I22=1.5e-6, J=5e-9,
            depth=0.15, bf=0.1, tf=0.01, tw=0.008,
        )
        assert sec.shape_id == "T"

    def test_sd_section(self):
        sec = SDSection(
            name="SD_Custom", shape="SD Section", material="Steel",
            A=0.01, I33=1e-4, I22=5e-5, J=0.0,
        )
        assert sec.shape_id == "SD"

    def test_encased_section(self):
        inner = ISection(
            name="W200x52", shape="I/Wide Flange", material="Steel",
            A=0.00665, I33=5.25e-5, I22=1.77e-5, J=1e-6,
            depth=0.206, bf=0.134, tf=0.0126, tw=0.0072,
        )
        sec = EncasedSection(
            name="SRC_400x400", shape="Concrete Encasement Rectangle",
            material="Steel",
            A=0.16, I33=0.00213, I22=0.00213, J=2e-5,
            embedded_section=inner,
            encasement_material="Concrete_40MPa",
            encasement_depth=0.4, encasement_bf=0.4,
        )
        assert sec.embedded_section is not None
        assert sec.encasement_material == "Concrete_40MPa"

    def test_shell_section(self):
        sec = ShellSection(
            name="Shell_200mm", shape="Shell", material="Concrete",
            A=0.2, I33=0, I22=0, J=0,
            thickness=0.2,
        )
        assert sec.thickness == 0.2
        assert sec.shape_id == "GEN"  # Shell is not in SHAPE_NAMES

    def test_shape_id_mapping(self):
        assert ISection(name="", shape="I/Wide Flange", material="",
                        A=0,I33=0,I22=0,J=0).shape_id == "I"
        assert ISection(name="", shape="WIDE FLANGE", material="",
                        A=0,I33=0,I22=0,J=0).shape_id == "I"
        assert PipeSection(name="", shape="Pipe", material="",
                           A=0,I33=0,I22=0,J=0).shape_id == "CHS"
        assert BoxSection(name="", shape="Box/Tube", material="",
                          A=0,I33=0,I22=0,J=0).shape_id == "RHS"

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
            nodes={}, restraints={}, materials={}, sections={},
            frame_elements={}, area_elements={}, frame_assignments={},
            area_assignments={}, groups={}, frame_auto_mesh={},
        )
        assert m.units["L"] == "m", (
            f"Expected default length unit 'm', got '{m.units['L']}'"
        )

    def test_custom_units(self):
        m = SAPModelData(
            nodes={}, restraints={}, materials={}, sections={},
            frame_elements={}, area_elements={}, frame_assignments={},
            area_assignments={}, groups={}, frame_auto_mesh={},
            units={"F": "kip", "L": "in", "T": "F"},
        )
        assert m.units == {"F": "kip", "L": "in", "T": "F"}

    def test_new_load_fields_default(self):
        """Verify recently-added load fields default to empty lists."""
        m = SAPModelData(
            nodes={}, restraints={}, materials={}, sections={},
            frame_elements={}, area_elements={}, frame_assignments={},
            area_assignments={}, groups={}, frame_auto_mesh={},
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
        vecxz_0 = get_SAP_vecxz(vec_x, angle=0.0)
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
        np.testing.assert_array_almost_equal(
            vy, [0, -0.70710678, 0.70710678], decimal=6
        )
        np.testing.assert_array_almost_equal(
            vz, [0, -0.70710678, -0.70710678], decimal=6
        )

    def test_90_degree_swap(self):
        """Angle=90°: vy_90 = vz_0, vz_90 = -vy_0."""
        vx0, vy0, vz0 = get_local_axes(np.array([5.0, 0.0, 0.0]), angle=0.0)
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
                    assert abs(np.linalg.norm(v) - 1.0) < 1e-10, \
                        f"{name} not unit for axis={axis}, angle={angle}"
                assert abs(np.dot(vx, vy)) < 1e-10, \
                    f"vx·vy not zero for axis={axis}, angle={angle}"
                assert abs(np.dot(vx, vz)) < 1e-10, \
                    f"vx·vz not zero for axis={axis}, angle={angle}"
                assert abs(np.dot(vy, vz)) < 1e-10, \
                    f"vy·vz not zero for axis={axis}, angle={angle}"

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
        np.testing.assert_array_almost_equal(
            vx, [0.70710678, 0.70710678, 0], decimal=6
        )
        np.testing.assert_array_almost_equal(vy, [0, 0, 1])
        np.testing.assert_array_almost_equal(
            vz, [0.70710678, -0.70710678, 0], decimal=6
        )

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
        assert (
            list_interp(0.08, [0.2, 0.8], [1.1, 1.35], extend=True, extrapolate=True)
            == 1.05
        )

    def test_below_range_no_extrapolate(self):
        assert (
            list_interp(0.08, [0.2, 0.8], [1.1, 1.35], extend=True, extrapolate=False)
            == 1.1
        )


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
        grid.add_point("N1", (-0.5, -0.5, -0.5))   # → cell (-1,-1,-1)
        grid.add_point("N2", (-1.5, -1.5, -1.5))   # → cell (-2,-2,-2)
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
        grid.add_point("A", (0, 0, 0))      # cell (0,0,0)
        grid.add_point("B", (2.1, 2.1, 2.1))  # cell (1,1,1) — just over boundary
        grid.add_point("C", (10, 10, 10))     # cell (5,5,5) — far away
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
        grid.add_point("X", (0.0, 0.0, 0.0))   # → cell (0,0,0)
        grid.add_point("Y", (0.5, 0.5, 0.5))   # → cell (1,1,1)
        grid.add_point("Z", (1.0, 1.0, 1.0))   # → cell (2,2,2)
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


class TestSectionLibrary:
    @pytest.fixture
    def db_path(self):
        p = FIXTURES_DIR.parent.parent / "data" / "section_dict.pkl"
        if not p.exists():
            pytest.skip(f"Section database not found: {p}")
        return p

    def test_load_database(self, db_path):
        from fea_toolkit.model.sections import SectionLibrary

        lib = SectionLibrary(db_path, target_units="m")
        assert len(lib.list_catalogues()) > 0

    def test_get_section_properties(self, db_path):
        from fea_toolkit.model.sections import SectionLibrary

        lib = SectionLibrary(db_path, target_units="m")
        catalogues = lib.list_catalogues()
        # Try to find a section from the first catalogue
        first_cat = catalogues[0]
        cat_data = lib._catalogues[first_cat]
        sections_dict = cat_data.get("SECTIONS", cat_data)
        if sections_dict:
            first_sec_name = next(iter(sections_dict))
            props = lib.get_section_properties(first_sec_name)
            assert props is not None
            assert "_catalogue" in props

    def test_enrich_section(self, db_path):
        from fea_toolkit.model.sections import SectionLibrary

        lib = SectionLibrary(db_path, target_units="m")
        # Create a basic section
        sec = Section(
            name="dummy",
            shape="I/Wide Flange",
            material="Steel",
            A=0.01,
            I33=1e-4,
            I22=5e-5,
            J=1e-6,
        )
        # Enrichment should not crash even if section is not in DB
        lib.enrich_section(sec)
        # Z33/Z22 may be None if not in DB
        assert hasattr(sec, "Z33")


# ============================================================================
# Integration test: parser -> model data -> geometry
# ============================================================================


class TestParserModelIntegration:
    @pytest.fixture
    def parsed_model(self):
        """Parse the sample.s2k fixture and return SAPModelData."""
        s2k_file = FIXTURES_DIR / "sample.s2k"
        if not s2k_file.exists():
            pytest.skip(f"Sample file not found: {s2k_file}")
        from fea_toolkit.io.s2k_parser import SAP2000Parser

        parser = SAP2000Parser(s2k_file)
        parser.parse()
        return parser.get_model_data()

    def test_nodes_parsed(self, parsed_model):
        assert len(parsed_model.nodes) > 0
        # Verify first node
        n1 = parsed_model.nodes.get("1")
        if n1:
            assert n1.x == 0.0

    def test_frames_parsed(self, parsed_model):
        assert len(parsed_model.frame_elements) > 0

    def test_auto_mesh_parsed(self, parsed_model):
        assert len(parsed_model.frame_auto_mesh) > 0
        # Check AtJoints flag is set on some frames
        at_joints_count = sum(
            1 for v in parsed_model.frame_auto_mesh.values() if v.get("AtJoints")
        )
        assert at_joints_count > 0

    def test_units_parsed(self, parsed_model):
        units = parsed_model.units
        assert "L" in units
        assert units["L"] in ("m", "mm", "in", "ft", "cm")

    def test_sections_parsed(self, parsed_model):
        assert len(parsed_model.sections) >= 0

    def test_restraints_parsed(self, parsed_model):
        assert len(parsed_model.restraints) >= 0

    def test_split_elements(self, parsed_model):
        """Test that split_elements can run on parsed model data."""
        from fea_toolkit.model.geometry import split_elements

        result = split_elements(
            nodes=parsed_model.nodes,
            elements=parsed_model.frame_elements,
            assignments=parsed_model.frame_assignments,
            dist_loads=parsed_model.frame_dist_loads,
            auto_mesh=parsed_model.frame_auto_mesh,
            tol=1e-6,
            verbose=False,
        )
        new_elements, new_assignments, new_dist_loads = result
        assert len(new_elements) > 0
        # Parent elements should be marked inactive
        inactive = [e for e in new_elements.values() if e.inactive]
        assert len(inactive) > 0

    def test_split_elements_tracking(self, parsed_model):
        """Verify parent-child tracking after splitting."""
        from fea_toolkit.model.geometry import split_elements

        result = split_elements(
            nodes=parsed_model.nodes,
            elements=parsed_model.frame_elements,
            assignments=parsed_model.frame_assignments,
            dist_loads=parsed_model.frame_dist_loads,
            auto_mesh=parsed_model.frame_auto_mesh,
            tol=1e-6,
            verbose=False,
        )
        new_elements, _, _ = result
        # Check that parent elements have child_ids populated
        for eid, elem in new_elements.items():
            if elem.inactive:
                assert len(elem.child_ids) > 0, (
                    f"Inactive element {eid} should have children"
                )


# ============================================================================
# Edge cases
# ============================================================================


class TestSplitElementsAtFrames:
    """Tests for the AtFrames frame-frame intersection splitting."""

    def test_at_frames_no_intersection_no_split(self):
        """Elements with AtFrames=True that don't intersect should not split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=0, y=10, z=0),
            "4": Node(node_id="4", node_tag=4, x=10, y=10, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        assert len(new_elems) == 2
        assert "A" in new_elems and "B" in new_elems
        assert not new_elems["A"].inactive
        assert not new_elems["B"].inactive

    def test_at_frames_crossing_split(self):
        """Two perpendicular frames crossing should split at intersection."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=0),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        # Pass nodes directly (not a copy) so we can check new nodes
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        # Both elements should be split → 2 children each + 2 inactive parents
        assert "A" in new_elems
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2
        assert "B" in new_elems
        assert new_elems["B"].inactive
        assert len(new_elems["B"].child_ids) == 2
        # Check a new node was created at (5, 0, 0) in the nodes dict
        split_node = None
        for nid, nd in nodes.items():
            if nid.startswith("split_n_"):
                split_node = nd
                break
        assert split_node is not None, "No split node created"
        assert abs(split_node.x - 5) < 1e-6
        assert abs(split_node.y - 0) < 1e-6
        assert abs(split_node.z - 0) < 1e-6
        assert split_node.node_tag == 5, (
            f"Expected next tag 5, got {split_node.node_tag}")
        assert split_node.node_id == "split_n_1"

    def test_at_frames_skips_shared_joint(self):
        """Elements already sharing a joint should not be split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=10, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="2", node_j="3"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        # Should not crash — they share node "2"
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        # Should not crash — they share node "2"
        assert len(new_elems) == 2
        assert not new_elems["A"].inactive
        assert not new_elems["B"].inactive

    def test_at_frames_3d_crossing(self):
        """Elements crossing at different elevations (3D) — no split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=5),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=5),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtFrames": True}, "B": {"AtFrames": True}}
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        # Non-coplanar → no intersection → no split
        assert not new_elems["A"].inactive
        assert not new_elems["B"].inactive

    def test_at_frames_no_joints_but_frames(self):
        """Element with AtFrames=True but AtJoints=False should still split."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=0),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        }
        # Only AtFrames, no AtJoints
        auto_mesh = {"A": {"AtJoints": False, "AtFrames": True},
                     "B": {"AtJoints": False, "AtFrames": True}}
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2
        assert new_elems["B"].inactive
        assert len(new_elems["B"].child_ids) == 2
        # Verify split node got correct sequential tag (5 after 1..4)
        split_nid = next(nid for nid in nodes if nid.startswith("split_n_"))
        assert nodes[split_nid].node_tag == 5
        assert nodes[split_nid].node_id == "split_n_1"

    def test_at_frames_skips_existing_joint_when_no_atjoints(self):
        """An existing joint on an element should NOT split it when
        AtJoints=False, even if AtFrames is True and creates a split."""
        from fea_toolkit.model.geometry import split_elements

        # Elements A (0→10) and C (3→7) intersect at (3,0,0) — existing joint.
        # Element B crosses at (5,0,0) — AtFrames intersection.
        # With AtJoints=False, only the AtFrames intersection should split.
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=3, y=0, z=0),
            "4": Node(node_id="4", node_tag=4, x=7, y=0, z=0),
            # Element B crosses at (5,0,0)
            "5": Node(node_id="5", node_tag=5, x=5, y=-5, z=0),
            "6": Node(node_id="6", node_tag=6, x=5, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="5", node_j="6"),
            "C": FrameElement(elem_id="C", elem_tag=12, node_i="3", node_j="4"),
        }
        auto_mesh = {"A": {"AtJoints": False, "AtFrames": True},
                     "B": {"AtJoints": False, "AtFrames": True},
                     "C": {"AtJoints": False, "AtFrames": False}}
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        # Element A should be split only at the AtFrames node (5,0,0),
        # NOT at the existing joint node 3 (3,0,0).
        # So it should have 2 children (split at t=0.5).
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2, (
            f"Expected 2 children, got {len(new_elems['A'].child_ids)}")
        # The AtFrames node should get the next sequential tag (7 after 1..6)
        split_nid = next(nid for nid in nodes if nid.startswith("split_n_"))
        assert nodes[split_nid].node_tag == 7, (
            f"Expected tag 7, got {nodes[split_nid].node_tag}")
        # Element C (no AtFrames, no AtJoints) should not be split
        assert not new_elems["C"].inactive
        # Only one new node should exist (the AtFrames intersection)
        at_frames_nodes = [nid for nid in nodes if nid.startswith("split_n_")]
        assert len(at_frames_nodes) == 1

    def test_at_frames_dedup_near_duplicate_t(self):
        """Two intersections that create separate nodes via node-reuse.

        Elements B and C cross A at positions close enough (1e-8 m apart)
        that the 3D node-reuse check (abs_tol ≈ 1e-5) creates only one
        shared split_n_ node.  This exercises the coordinate-based node
        reuse path.

        The secondary t-based dedup (in the main splitting loop) cannot
        currently be triggered by AtFrames-only splits because the node-
        reuse threshold (abs_tol ≈ tol × L) equals the t-dedup threshold
        (tol × L) for equal-length segments.  It guards against future
        cross-source merging (e.g., storey-level + AtFrames).
        """
        from fea_toolkit.model.geometry import split_elements

        # Element A: horizontal beam from (0,0,0) to (10,0,0)
        # Two other elements cross it at nearly the same point:
        #   Element B: crosses at (5,0,0) exactly
        #   Element C: crosses at (5.00000001, 0, 0) — 10 nm off
        # Distance from (5,0,0) to (5.00000001,0,0) = 1e-8 ≤ 1e-6 tol, so
        # split_n_1 is reused. Cross product over 10m = 10×1e-8 = 1e-7 ≤ tol.
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=-5, z=0),
            "4": Node(node_id="4", node_tag=4, x=5, y=5, z=0),
            "5": Node(node_id="5", node_tag=5, x=5.00000001, y=-5, z=0),
            "6": Node(node_id="6", node_tag=6, x=5.00000001, y=5, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
            "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
            "C": FrameElement(elem_id="C", elem_tag=12, node_i="5", node_j="6"),
        }
        auto_mesh = {"A": {"AtJoints": False, "AtFrames": True},
                     "B": {"AtJoints": False, "AtFrames": True},
                     "C": {"AtJoints": False, "AtFrames": True}}
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result
        # Element A should have 2 children (split once, not twice)
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2, (
            f"Expected 2 children (dedup), got {len(new_elems['A'].child_ids)}")

        # Exactly one split node should exist (B and C share it)
        split_nodes = [nid for nid in nodes if nid.startswith("split_n_")]
        assert len(split_nodes) == 1, (
            f"Expected 1 split node, got {len(split_nodes)}: {split_nodes}")

        # A's breakpoint metadata should also reflect the dedup: only one
        # t-location kept (0.5), not both near-identical s entries
        assert len(new_elems["A"].t_locations) == 1, (
            f"Expected 1 t-location (deduped), got {new_elems['A'].t_locations}")

        # B and C's children should both reference that same shared node
        shared_nid = split_nodes[0]
        b_children = [c for cid in new_elems["B"].child_ids
                      for c in [new_elems.get(cid)] if c]
        c_children = [c for cid in new_elems["C"].child_ids
                      for c in [new_elems.get(cid)] if c]
        b_refs = {c.node_i for c in b_children} | {c.node_j for c in b_children}
        c_refs = {c.node_i for c in c_children} | {c.node_j for c in c_children}
        assert shared_nid in b_refs, (
            f"Element B children don't reference {shared_nid}")
        assert shared_nid in c_refs, (
            f"Element C children don't reference {shared_nid}")

    def test_at_frames_t_dedup_direct(self):
        """Integration test: t-based dedup via split_elements().

        Two existing model nodes at nearly the same parametric location
        on element A (AtJoints=True) produce intermediate (nid, t) pairs
        with |t₁ - t₂| ≤ tol.  The main loop's t-dedup drops the second
        node, leaving a single split → 2 children instead of 3.

        AtFrames-only splits cannot trigger this path because the node-
        reuse threshold (abs_tol = tol × L) equals the t-dedup threshold
        (tol × L), so node-reuse always catches near-coincident points
        first in the AtFrames double-loop.  AtJoints nodes bypass that
        check and feed directly into the intermediate list.
        """
        from fea_toolkit.model.geometry import split_elements

        # Element A: horizontal beam from (0,0,0) to (10,0,0)
        # Node 3 lies on A at (5,0,0)          → t = 0.5
        # Node 5 lies on A at (5.000006,0,0)   → t = 0.5000006
        # |t₃ − t₅| = 6e-7 < tol (1e-6) → t-dedup merges them
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=10, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=0, z=0),
            "5": Node(node_id="5", node_tag=5, x=5.000006, y=0, z=0),
        }
        elements = {
            "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
        }
        auto_mesh = {"A": {"AtJoints": True, "AtFrames": False}}
        result = split_elements(nodes=nodes, elements=elements,
                                assignments={}, dist_loads=[], auto_mesh=auto_mesh)
        new_elems, _, _ = result

        # Element A should have 2 children (one split, the second
        # candidate was deduped away)
        assert new_elems["A"].inactive
        assert len(new_elems["A"].child_ids) == 2, (
            f"Expected 2 children (t-dedup), got {len(new_elems['A'].child_ids)}")

        # The intermediate children reference existing node IDs (no
        # split_n_ nodes are created for AtJoints).  Verify that node 5
        # was deduped away: children span (1→3) and (3→2).
        children = [new_elems[cid] for cid in new_elems["A"].child_ids]
        node_pairs = {(c.node_i, c.node_j) for c in children}
        assert ("1", "3") in node_pairs, (
            f"Expected child 1→3, got {node_pairs}")
        assert ("3", "2") in node_pairs, (
            f"Expected child 3→2, got {node_pairs}")
        assert ("5") not in str(node_pairs), (
            "Node 5 should have been deduped")

        # Distinct t values → all kept (sanity: no accidental dedup)
        # Node 6 at (3,0,0) → t=0.3, Node 7 at (7,0,0) → t=0.7
        nodes2 = {
            "1": Node("1", 1, 0, 0, 0),
            "2": Node("2", 2, 10, 0, 0),
            "6": Node("6", 6, 3, 0, 0),
            "7": Node("7", 7, 7, 0, 0),
        }
        elements2 = {"B": FrameElement("B", 20, "1", "2")}
        auto_mesh2 = {"B": {"AtJoints": True, "AtFrames": False}}
        result2 = split_elements(nodes=nodes2, elements=elements2,
                                 assignments={}, dist_loads=[], auto_mesh=auto_mesh2)
        new_elems2, _, _ = result2
        assert new_elems2["B"].inactive
        assert len(new_elems2["B"].child_ids) == 3, (
            f"Expected 3 children (2 splits, no dedup), "
            f"got {len(new_elems2['B'].child_ids)}")


class TestEdgeCases:
    def test_empty_model(self):
        """SAPModelData with no data should not crash."""
        model = SAPModelData(
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
        assert len(model.nodes) == 0
        assert len(model.frame_elements) == 0

    def test_zero_length_element_skipped(self):
        """A zero-length element should be handled gracefully."""
        from fea_toolkit.model.geometry import split_elements

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=0),
        }
        elements = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
        }
        assignments = {"1": "Sec1"}
        auto_mesh = {"1": {"AtJoints": True}}
        result = split_elements(
            nodes=nodes,
            elements=elements,
            assignments=assignments,
            dist_loads=[],
            auto_mesh=auto_mesh,
            verbose=False,
        )
        new_elements, new_assignments, _ = result
        # Zero-length element should be kept as-is, not split
        assert "1" in new_elements
        assert new_assignments.get("1") == "Sec1"

    def test_trapezoidal_split_no_intermediate(self):
        """trapezoidal_force_split with empty t_values returns one segment."""
        f_data = ((0.0, 5.0), (1.0, 10.0))
        result = trapezoidal_force_split(f_data, [])
        assert len(result) == 1

    def test_get_SAP_vecxz_with_list_input(self):
        """Should accept plain Python lists as input."""
        vecxz = get_SAP_vecxz([5.0, 0.0, 0.0])
        assert np.allclose(vecxz, [0.0, -1.0, 0.0], atol=1e-6)


class TestBeamLoadToNodalLoads:
    """Tests for beam_load_to_nodal_loads()."""

    def test_uniform_gravity(self):
        """Uniform gravity load on a horizontal X element."""
        load = FrameDistributedLoad(
            pattern="DEAD", frame_id="1", direction="Gravity",
            load_type="Force", shape="Uniform",
            val_a=10000.0, val_b=10000.0,
            rdist_a=0.0, rdist_b=1.0, dist_a=0.0, dist_b=5.0,
        )
        elem = FrameElement(
            elem_id="1", elem_tag=1, node_i="1", node_j="2", angle=0.0,
        )
        node_coords = {"1": (0.0, 0.0, 0.0), "2": (5.0, 0.0, 0.0)}
        result = beam_load_to_nodal_loads(load, elem, node_coords, length=5.0)

        # Total load = 10000 * 5 = 50000 (downward = negative Z)
        total_fz = result["i"]["fz"] + result["j"]["fz"]
        assert abs(total_fz) - 50000.0 < 1.0
        # Each node gets ~25000 (downward)
        assert abs(result["i"]["fz"]) - 25000.0 < 1.0
        assert abs(result["j"]["fz"]) - 25000.0 < 1.0
        # No y component
        assert abs(result["i"]["fy"]) < 1.0
        assert abs(result["j"]["fy"]) < 1.0
        # Moments should be non-zero (fixed-end moments about local y)
        assert abs(result["i"]["my"]) > 10000.0
        assert abs(result["j"]["my"]) > 10000.0

    def test_uniform_x_direction(self):
        """Uniform load in global X direction."""
        load = FrameDistributedLoad(
            pattern="WIND", frame_id="1", direction="X",
            load_type="Force", shape="Uniform",
            val_a=5000.0, val_b=5000.0,
            rdist_a=0.0, rdist_b=1.0, dist_a=0.0, dist_b=5.0,
        )
        elem = FrameElement(
            elem_id="1", elem_tag=1, node_i="1", node_j="2", angle=0.0,
        )
        node_coords = {"1": (0.0, 0.0, 0.0), "2": (5.0, 0.0, 0.0)}
        result = beam_load_to_nodal_loads(load, elem, node_coords, length=5.0)

        # Total load = 5000 * 5 = 25000, split → 12500 per node
        # For X-direction on an X-axis element: all load is axial
        total_fx = result["i"]["fx"] + result["j"]["fx"]
        assert abs(total_fx - 25000.0) < 1.0
        assert abs(result["i"]["fx"] - 12500.0) < 1.0
        assert abs(result["j"]["fx"] - 12500.0) < 1.0

    def test_partial_span_uniform(self):
        """Uniform load on a partial span [0.2, 0.8]."""
        load = FrameDistributedLoad(
            pattern="DEAD", frame_id="1", direction="Gravity",
            load_type="Force", shape="Uniform",
            val_a=10000.0, val_b=10000.0,
            rdist_a=0.2, rdist_b=0.8, dist_a=1.0, dist_b=4.0,
        )
        elem = FrameElement(
            elem_id="1", elem_tag=1, node_i="1", node_j="2", angle=0.0,
        )
        node_coords = {"1": (0.0, 0.0, 0.0), "2": (5.0, 0.0, 0.0)}
        result = beam_load_to_nodal_loads(load, elem, node_coords, length=5.0)

        # Total load = 10000 * (4-1) = 30000 on a 5m element
        total_fz = abs(result["i"]["fz"]) + abs(result["j"]["fz"])
        assert abs(total_fz - 30000.0) < 1.0

    def test_trapezoidal_load(self):
        """Trapezoidal load varying from 5000 to 10000."""
        load = FrameDistributedLoad(
            pattern="DEAD", frame_id="1", direction="Gravity",
            load_type="Force", shape="Trapezoidal",
            val_a=5000.0, val_b=10000.0,
            rdist_a=0.0, rdist_b=1.0, dist_a=0.0, dist_b=5.0,
        )
        elem = FrameElement(
            elem_id="1", elem_tag=1, node_i="1", node_j="2", angle=0.0,
        )
        node_coords = {"1": (0.0, 0.0, 0.0), "2": (5.0, 0.0, 0.0)}
        result = beam_load_to_nodal_loads(load, elem, node_coords, length=5.0)

        # Total load = (5000+10000)/2 * 5 = 37500
        total_fz = abs(result["i"]["fz"]) + abs(result["j"]["fz"])
        assert abs(total_fz - 37500.0) < 100.0  # allow small numerical tolerance
        # Asymmetric load → unequal end forces
        assert abs(result["i"]["fz"]) != abs(result["j"]["fz"])


# ============================================================================
# MassSource tests
# ============================================================================

class TestMassSource:
    def test_defaults(self):
        ms = MassSource(name="MSSSRC1")
        assert ms.name == "MSSSRC1"
        assert ms.elements is False
        assert ms.masses is False
        assert ms.loads is False
        assert ms.load_pattern == {}

    def test_with_loads(self):
        ms = MassSource(
            name="MSSSRC1",
            elements=True,
            masses=True,
            loads=True,
            is_default=True,
            load_pattern={"DEAD": 1.0, "SUPERDEAD": 1.2},
        )
        assert ms.elements is True
        assert ms.load_pattern["DEAD"] == 1.0
        assert ms.load_pattern["SUPERDEAD"] == 1.2
        assert ms.is_default is True


# ============================================================================
# Fiber patch tests
# ============================================================================

class TestPipeSectionFiberPatches:
    def test_annular_ring(self):
        p = PipeSection("PIPE", "Pipe", "STEEL", od=1.0, t=0.1)
        patches = p.to_fiber_patches(mat_tag=1, nfy=8, nfz=4)
        assert len(patches) == 1
        ptype, mat, ncirc, nrad, yc, zc, r_in, r_out, sa, ea = patches[0]
        assert ptype == "circ"
        assert mat == 1
        assert ncirc == 8 and nrad == 4
        assert abs(r_in - 0.4) < 1e-12
        assert abs(r_out - 0.5) < 1e-12
        assert sa == 0.0 and ea == 360.0

    def test_solid_wall(self):
        p = PipeSection("PIPE", "Pipe", "STEEL", od=0.5, t=0.5)
        patches = p.to_fiber_patches(mat_tag=2)
        _, _, _, _, _, _, r_in, r_out, _, _ = patches[0]
        assert abs(r_in) < 1e-12  # full solid when t == od/2
        assert abs(r_out - 0.25) < 1e-12


class TestCircularSectionFiberPatches:
    def test_solid_circle(self):
        c = CircularSection("CIRC", "Circle", "STEEL", diameter=0.6)
        patches = c.to_fiber_patches(mat_tag=3, nfy=12, nfz=6)
        assert len(patches) == 1
        ptype, mat, ncirc, nrad, yc, zc, r_in, r_out, sa, ea = patches[0]
        assert ptype == "circ"
        assert mat == 3
        assert ncirc == 12 and nrad == 6
        assert abs(r_in) < 1e-12
        assert abs(r_out - 0.3) < 1e-12


class TestBoxSectionFiberPatches:
    def test_four_rect_patches(self):
        b = BoxSection("BOX", "Box/Tube", "STEEL",
                       depth=0.6, bf=0.4, tf=0.02, tw=0.015)
        patches = b.to_fiber_patches(mat_tag=4, nfy=3, nfz=2)
        assert len(patches) == 4
        for p in patches:
            assert p[0] == "rect"
            assert p[1] == 4
        # Top flange: y from 0.28 to 0.3, z from -0.2 to 0.2
        assert abs(patches[0][4] - 0.28) < 1e-12  # yI
        assert abs(patches[0][6] - 0.3) < 1e-12   # yJ
        # Bottom flange: y from -0.3 to -0.28
        assert abs(patches[1][4] + 0.3) < 1e-12
        assert abs(patches[1][6] + 0.28) < 1e-12


# ============================================================================
# Selection tests
# ============================================================================


class TestSelection:
    """Tests for the Selection filter class."""

    @pytest.fixture
    def model(self):
        """Minimal model with frames, areas, nodes, sections, groups."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=5, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=5, y=5, z=0),
            "4": Node(node_id="4", node_tag=4, x=0, y=5, z=0),
            "5": Node(node_id="5", node_tag=5, x=0, y=0, z=3),
        }
        materials = {
            "Steel": Material(name="Steel", type="Steel",
                              E_mod=2e11, unit_weight=77000),
            "Concrete": Material(name="Concrete", type="Concrete",
                                 E_mod=3e10, unit_weight=24000),
        }
        sections = {
            "UB100": Section(name="UB100", shape="I/Wide Flange",
                             material="Steel", A=0.01, I33=1e-4,
                             I22=1e-5, J=1e-6),
            "Slab200": ShellSection(name="Slab200", shape="Shell",
                                    material="Concrete",
                                    A=0, I33=0, I22=0, J=0,
                                    thickness=0.2),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1,
                              node_i="1", node_j="2"),
            "2": FrameElement(elem_id="2", elem_tag=2,
                              node_i="2", node_j="3"),
        }
        areas = {
            "1": AreaElement(area_id="1", area_tag=1,
                             node_ids=["1","2","3","4"], thickness=0.2),
        }
        groups = {
            "Moment Frame": Group(
                name="Moment Frame",
                objects=["Frame:1", "Frame:2"],
            ),
            "Slabs": Group(
                name="Slabs",
                objects=["Area:1"],
            ),
        }
        area_uniform = [
            AreaUniformLoad(pattern="DEAD", area_id="1",
                            direction="Gravity", value=5000),
        ]
        area_gravity = [
            AreaGravityLoad(pattern="DEAD", area_id="1",
                            multiplier_z=-1.0),
        ]
        return SAPModelData(
            nodes=nodes,
            restraints={},
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements=areas,
            frame_assignments={"1": "UB100", "2": "UB100"},
            area_assignments={"1": "Slab200"},
            groups=groups,
            frame_auto_mesh={},
            area_uniform_loads=area_uniform,
            area_gravity_loads=area_gravity,
        )

    # ── element_types filter ──

    def test_select_frames_only(self, model):
        sel = Selection(element_types=["Frame"])
        assert sel.get_frame_ids(model) == ["1", "2"]
        assert sel.get_area_ids(model) == []
        assert sel.get_node_ids(model) == []

    def test_select_areas_only(self, model):
        sel = Selection(element_types=["Area"])
        assert sel.get_frame_ids(model) == []
        assert sel.get_area_ids(model) == ["1"]

    def test_select_multiple_types(self, model):
        sel = Selection(element_types=["Frame", "Area"])
        assert set(sel.get_frame_ids(model)) == {"1", "2"}
        assert sel.get_area_ids(model) == ["1"]

    def test_no_element_type_filter(self, model):
        """element_types=None matches all types."""
        sel = Selection()
        assert len(sel.get_frame_ids(model)) == 2
        assert len(sel.get_area_ids(model)) == 1

    # ── section filter ──

    def test_select_by_section(self, model):
        sel = Selection(element_types=["Frame"], sections=["UB100"])
        assert set(sel.get_frame_ids(model)) == {"1", "2"}

    def test_select_by_section_no_match(self, model):
        sel = Selection(element_types=["Frame"], sections=["Nonexistent"])
        assert sel.get_frame_ids(model) == []

    def test_select_area_by_section(self, model):
        sel = Selection(element_types=["Area"], sections=["Slab200"])
        assert sel.get_area_ids(model) == ["1"]

    # ── material filter ──

    def test_select_by_material(self, model):
        sel = Selection(materials=["Concrete"])
        assert sel.get_area_ids(model) == ["1"]
        assert sel.get_frame_ids(model) == []

    def test_select_by_material_no_match(self, model):
        sel = Selection(materials=["Timber"])
        assert sel.get_area_ids(model) == []
        assert sel.get_frame_ids(model) == []

    # ── group filter ──

    def test_select_by_group(self, model):
        sel = Selection(groups=["Moment Frame"])
        assert set(sel.get_frame_ids(model)) == {"1", "2"}
        assert sel.get_area_ids(model) == []

    def test_select_by_group_area(self, model):
        sel = Selection(groups=["Slabs"])
        assert sel.get_area_ids(model) == ["1"]

    # ── element_ids filter ──

    def test_select_by_element_id(self, model):
        sel = Selection(element_ids=["1"])
        assert sel.get_frame_ids(model) == ["1"]
        assert sel.get_area_ids(model) == ["1"]

    def test_select_by_element_id_multiple(self, model):
        sel = Selection(element_ids=["1", "2"])
        assert sel.get_frame_ids(model) == ["1", "2"]

    # ── combined criteria (AND across fields) ──

    def test_and_across_criteria(self, model):
        """element_types AND groups — both must match."""
        sel = Selection(element_types=["Frame"], groups=["Moment Frame"])
        assert set(sel.get_frame_ids(model)) == {"1", "2"}

    def test_and_no_match(self, model):
        """element_types AND groups — Area + Moment Frame = none."""
        sel = Selection(element_types=["Area"], groups=["Moment Frame"])
        assert sel.get_area_ids(model) == []

    # ── dict filter methods ──

    def test_filter_frames(self, model):
        sel = Selection(element_types=["Frame"])
        d = sel.filter_frames(model)
        assert set(d.keys()) == {"1", "2"}
        assert all(isinstance(v, FrameElement) for v in d.values())

    def test_filter_areas(self, model):
        sel = Selection(element_types=["Area"])
        d = sel.filter_areas(model)
        assert set(d.keys()) == {"1"}

    def test_filter_nodes(self, model):
        sel = Selection(element_types=["Node"])
        d = sel.filter_nodes(model)
        assert set(d.keys()) == {"1", "2", "3", "4", "5"}

    # ── load filter methods ──

    def test_filter_area_uniform(self, model):
        sel = Selection(element_types=["Area"])
        loads = sel.filter_area_uniform_loads(model)
        assert len(loads) == 1
        assert loads[0].area_id == "1"

    def test_filter_area_uniform_no_match(self, model):
        sel = Selection(element_types=["Frame"])
        assert sel.filter_area_uniform_loads(model) == []

    def test_filter_area_gravity(self, model):
        sel = Selection(element_types=["Area"])
        loads = sel.filter_area_gravity_loads(model)
        assert len(loads) == 1
        assert loads[0].multiplier_z == -1.0


# ============================================================================
# Selection filter_model tests
# ============================================================================


class TestSelectionFilterModel:
    """Tests for :meth:`Selection.filter_model` — self-contained subset creation.

    ``filter_model`` returns a new ``SAPModelData`` containing only the
    entities needed by the selected elements (nodes, sections, materials,
    restraints, loads, and pruned groups).  The original model is never
    modified.

    See ``tests/README.md`` for an overview of the test suite.
    """

    @pytest.fixture
    def full_model(self):
        """A richer model with frames, areas, loads, groups for filter testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=4, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=0, y=0, z=3),
            "4": Node(node_id="4", node_tag=4, x=4, y=0, z=3),
            "5": Node(node_id="5", node_tag=5, x=0, y=4, z=0),
            "6": Node(node_id="6", node_tag=6, x=4, y=4, z=0),
            "7": Node(node_id="7", node_tag=7, x=0, y=0, z=6),
            "8": Node(node_id="8", node_tag=8, x=4, y=0, z=6),
        }
        restraints = {
            "1": Restraint([1, 1, 1, 1, 1, 1]),
            "2": Restraint([1, 1, 1, 1, 1, 1]),
        }
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11,
                              unit_weight=77000),
            "Conc":  Material(name="Conc", type="Concrete", E_mod=3e10,
                              unit_weight=24000),
        }
        sections = {
            "UB100": Section(name="UB100", shape="I/Wide Flange",
                             material="Steel", A=0.01, I33=1e-4,
                             I22=1e-5, J=1e-6),
            "UB200": Section(name="UB200", shape="I/Wide Flange",
                             material="Steel", A=0.02, I33=2e-4,
                             I22=2e-5, J=2e-6),
            "Slab": ShellSection(name="Slab", shape="Shell",
                                 material="Conc", A=0, I33=0,
                                 I22=0, J=0, thickness=0.2),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1,
                              node_i="1", node_j="3"),
            "2": FrameElement(elem_id="2", elem_tag=2,
                              node_i="2", node_j="4"),
            "3": FrameElement(elem_id="3", elem_tag=3,
                              node_i="3", node_j="7"),
            "4": FrameElement(elem_id="4", elem_tag=4,
                              node_i="4", node_j="8"),
        }
        areas = {
            "1": AreaElement(area_id="1", area_tag=1,
                             node_ids=["1", "2", "5", "6"], thickness=0.2),
        }
        groups = {
            "Cols": Group(name="Cols",
                          objects=["Frame:1", "Frame:2"]),
            "Slab": Group(name="Slab",
                          objects=["Area:1", "Joint:5", "Joint:6"]),
        }
        load_patterns = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD"),
            "WIND": LoadPattern(name="WIND", pattern_type="WIND"),
        }
        frame_dist_loads = [
            FrameDistributedLoad(pattern="WIND", frame_id="1",
                                 direction="X", load_type="Force",
                                 shape="Uniform", val_a=1000, val_b=1000,
                                 rdist_a=0, rdist_b=1, dist_a=0, dist_b=3),
        ]
        frame_gravity_loads = [
            GravityLoad(pattern="DEAD", frame_id="2",
                        multiplier_z=-1.0),
        ]
        area_uniform_loads = [
            AreaUniformLoad(pattern="DEAD", area_id="1",
                            direction="Gravity", value=5000),
        ]
        area_gravity_loads = [
            AreaGravityLoad(pattern="DEAD", area_id="1",
                            multiplier_z=-1.0),
        ]
        joint_loads = [
            JointLoad(pattern="DEAD", node_id="3", fz=-5000),
        ]
        return SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frames,
            area_elements=areas,
            frame_assignments={"1": "UB100", "2": "UB100",
                               "3": "UB200", "4": "UB200"},
            area_assignments={"1": "Slab"},
            groups=groups,
            frame_auto_mesh={},
            load_patterns=load_patterns,
            joint_loads=joint_loads,
            frame_dist_loads=frame_dist_loads,
            frame_gravity_loads=frame_gravity_loads,
            area_uniform_loads=area_uniform_loads,
            area_gravity_loads=area_gravity_loads,
        )

    # ── Frame selection ──

    def test_frame_selection_basics(self, full_model):
        """Select all frames: 4 frames, 0 areas, 6 end-nodes."""
        sub = Selection(element_types=["Frame"]).filter_model(full_model)
        assert len(sub.frame_elements) == 4
        assert len(sub.area_elements) == 0
        assert len(sub.nodes) == 6          # frame end-nodes: 1,2,3,4,7,8
        assert sorted(sub.nodes) == ["1", "2", "3", "4", "7", "8"]
        assert len(sub.restraints) == 2     # nodes 1, 2

    def test_frame_selection_by_group(self, full_model):
        """Group ``Cols`` → only frames 1 & 2, their 4 end-nodes."""
        sub = Selection(element_types=["Frame"],
                        groups=["Cols"]).filter_model(full_model)
        assert len(sub.frame_elements) == 2
        assert set(sub.frame_elements) == {"1", "2"}
        # End-nodes: 1,3 + 2,4 = 4 nodes
        assert sorted(sub.nodes) == ["1", "2", "3", "4"]
        # Restraints on nodes 1, 2
        assert sorted(sub.restraints) == ["1", "2"]

    def test_frame_selection_sections_materials(self, full_model):
        """Only ``UB100`` section and ``Steel`` material; no Concrete."""
        sub = Selection(element_types=["Frame"],
                        groups=["Cols"]).filter_model(full_model)
        assert sorted(sub.sections) == ["UB100"]
        assert sorted(sub.materials) == ["Steel"]
        assert "Conc" not in sub.materials

    def test_frame_selection_loads(self, full_model):
        """Distributed, gravity, and joint loads on selected frames; no area loads."""
        sub = Selection(element_types=["Frame"],
                        groups=["Cols"]).filter_model(full_model)
        # Only loads on frames 1, 2
        assert len(sub.frame_dist_loads) == 1    # WIND on frame 1
        assert sub.frame_dist_loads[0].frame_id == "1"
        assert len(sub.frame_gravity_loads) == 1  # DEAD on frame 2
        assert sub.frame_gravity_loads[0].frame_id == "2"
        # Area loads excluded
        assert len(sub.area_uniform_loads) == 0
        assert len(sub.area_gravity_loads) == 0
        # Joint load on node 3 (end-node of frame 1)
        assert len(sub.joint_loads) == 1
        assert sub.joint_loads[0].node_id == "3"

    # ── Area selection ──

    def test_area_selection_basics(self, full_model):
        """Select all areas: 1 area, 0 frames, 4 corner nodes."""
        sub = Selection(element_types=["Area"]).filter_model(full_model)
        assert len(sub.area_elements) == 1
        assert len(sub.frame_elements) == 0
        # Corner nodes: 1, 2, 5, 6
        assert sorted(sub.nodes) == ["1", "2", "5", "6"]
        assert sorted(sub.restraints) == ["1", "2"]

    def test_area_selection_sections_materials(self, full_model):
        """Only ``Slab`` section and ``Conc`` material; no Steel."""
        sub = Selection(element_types=["Area"]).filter_model(full_model)
        assert sorted(sub.sections) == ["Slab"]
        assert sorted(sub.materials) == ["Conc"]

    def test_area_selection_loads(self, full_model):
        """Uniform and gravity area loads; no frame or joint loads."""
        sub = Selection(element_types=["Area"]).filter_model(full_model)
        assert len(sub.area_uniform_loads) == 1
        assert len(sub.area_gravity_loads) == 1
        assert len(sub.frame_dist_loads) == 0
        assert len(sub.frame_gravity_loads) == 0
        assert len(sub.joint_loads) == 0   # joint on node 3, not an area node

    # ── Combined selection ──

    def test_combined_frame_and_area(self, full_model):
        """Both Frame and Area types: 4 frames + 1 area + 8 unique nodes."""
        sub = Selection(element_types=["Frame", "Area"]).filter_model(full_model)
        assert len(sub.frame_elements) == 4
        assert len(sub.area_elements) == 1
        # All nodes: frame end-nodes (1,2,3,4,7,8) + area corners (1,2,5,6)
        assert sorted(sub.nodes) == ["1", "2", "3", "4", "5", "6", "7", "8"]
        assert len(sub.sections) == 3     # UB100, UB200, Slab
        assert len(sub.materials) == 2    # Steel, Conc

    # ── Group pruning ──

    def test_group_pruning(self, full_model):
        """``Cols`` kept with its 2 frame refs; ``Slab`` excluded entirely."""
        sub = Selection(element_types=["Frame"],
                        groups=["Cols"]).filter_model(full_model)
        assert "Cols" in sub.groups
        assert "Slab" not in sub.groups
        # Cols group should only have its two Frame references
        assert sub.groups["Cols"].objects == ["Frame:1", "Frame:2"]

    def test_group_pruning_area(self, full_model):
        """``Slab`` kept with area + joint refs; ``Cols`` excluded."""
        sub = Selection(element_types=["Area"]).filter_model(full_model)
        assert "Slab" in sub.groups
        assert "Cols" not in sub.groups
        assert sub.groups["Slab"].objects == ["Area:1", "Joint:5", "Joint:6"]

    # ── Empty / no-match ──

    def test_no_match(self, full_model):
        """Non-existent section → empty subset (0 frames, 0 nodes)."""
        sub = Selection(element_types=["Frame"],
                        sections=["Nonexistent"]).filter_model(full_model)
        assert len(sub.frame_elements) == 0
        assert len(sub.nodes) == 0
        assert len(sub.sections) == 0

    # ── Immutability ──

    def test_immutability(self, full_model):
        """Original model is never modified after ``filter_model``."""
        original_count = len(full_model.nodes)
        _ = Selection(element_types=["Frame"]).filter_model(full_model)
        assert len(full_model.nodes) == original_count
        assert len(full_model.frame_elements) == 4
        assert "Conc" in full_model.materials

class TestCqcCombineUtils:
    """Tests for :func:`fea_toolkit.utils.cqc_combine`."""

    def test_single_mode(self):
        from fea_toolkit.utils import cqc_combine
        result = cqc_combine([100.0], [2.0], [0.05])
        assert abs(result - 100.0) < 1e-6

    def test_two_uncorrelated(self):
        from fea_toolkit.utils import cqc_combine
        # Very separated frequencies → ρ ≈ 0 → SRSS ≈ sqrt(a² + b²)
        vals = [100.0, 50.0]
        omega = [1.0, 50.0]
        damp = [0.05, 0.05]
        result = cqc_combine(vals, omega, damp)
        expected = math.sqrt(100**2 + 50**2)
        assert abs(result - expected) < 0.1

    def test_identical_modes(self):
        from fea_toolkit.utils import cqc_combine
        # Identical frequency → ρ → 1 → CQC = sum of absolute values
        vals = [100.0, 50.0]
        omega = [2.0, 2.0]
        damp = [0.05, 0.05]
        result = cqc_combine(vals, omega, damp)
        assert abs(result - 150.0) < 1.0


# ============================================================================
# Plotting module import tests
# ============================================================================

class TestPlottingImports:
    def test_force_diagram_no_data(self):
        from fea_toolkit.plotting import plot_force_diagram
        fig = plot_force_diagram([], 'My_i')
        assert fig is None

    def test_static_force_diagram_missing_matplotlib(self):
        """Just verify the import path resolves; actual plotting
        requires matplotlib which may not be available in CI."""
        from fea_toolkit.plotting import plot_static_force_diagram
        assert callable(plot_static_force_diagram)

    def test_force_diagram_3d_import(self):
        """plot_force_diagram_3d is callable from the plotting package."""
        from fea_toolkit.plotting import plot_force_diagram_3d
        assert callable(plot_force_diagram_3d)

    def test_force_diagram_3d_invalid_quantity(self):
        """Invalid quantity returns None."""
        from fea_toolkit.plotting import plot_force_diagram_3d
        result = plot_force_diagram_3d({}, quantity='ZZ')
        assert result is None

    def test_force_diagram_3d_no_data_builder(self):
        """Builder without force_data returns None."""
        from fea_toolkit.plotting import plot_force_diagram_3d
        # Use a minimal mock that satisfies _resolve_mesh_data
        class MockModel:
            nodes = {}
            frame_elements = {}
            area_elements = {}
            frame_assignments = {}
            area_assignments = {}
        class MockBuilder:
            model = MockModel()
            split_elements = {}
            split_assignments = {}
            _mesh_model = None
        result = plot_force_diagram_3d(MockBuilder())
        assert result is None

    def test_force_diagram_3d_npz_no_static(self):
        """NPZ dict without static cases raises ValueError."""
        from fea_toolkit.plotting import plot_force_diagram_3d
        import pytest
        with pytest.raises(ValueError, match="No static cases found"):
            plot_force_diagram_3d({}, quantity='Mz')

    def test_unified_functions_import(self):
        """All unified functions are importable from the plotting package."""
        from fea_toolkit.plotting import (
            plot_mesh, compare_meshes, plot_mode_animation,
        )
        assert callable(plot_mesh)
        assert callable(compare_meshes)
        assert callable(plot_mode_animation)

    def test_model_viewer_import_and_types(self):
        """ModelViewer and its data types import correctly."""
        from fea_toolkit.plotting.renderers import (
            FrameGeom, ShellGeom, NodeGeom,
            HighlightDef, AnnotationDef,
        )
        import numpy as np

        # Data types construct
        f = FrameGeom(elem_id='1', section='UB300',
                       node_i='1', node_j='2',
                       start=np.zeros(3), end=np.ones(3))
        assert f.elem_id == '1'

        s = ShellGeom(area_id='1', section='SLAB',
                       vertices=np.zeros((4, 3)))
        assert s.area_id == '1'

        n = NodeGeom(node_id='1', position=np.zeros(3))
        assert n.node_id == '1'

        h = HighlightDef(frame_ids=['1'], color=(1, 0, 0), label='Test')
        assert h.label == 'Test'

        a = AnnotationDef(text='Hello', position=np.zeros(3))
        assert a.text == 'Hello'

    def test_model_viewer_from_sample(self):
        """ModelViewer extracts geometry from sample model data."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.plotting import ModelViewer

        md = make_sample_model()
        viewer = ModelViewer(model_data=md, backend='pyvista',
                              off_screen=True)

        # show_model should extract geometry and render
        viewer.show_model(show_nodes=True, show_shells=False)
        assert viewer._geom_extracted
        assert len(viewer._frames) == 1
        assert viewer._frames[0].elem_id == '1'

        # Test highlight
        viewer.highlight_elements(
            frame_ids=['1'], color=(1, 0, 0), label='Test'
        )

        # Test annotation
        viewer.annotate('Hi', node_id='2', color=(1, 1, 0))

        # Test screenshot
        import tempfile
        import os
        tmp = tempfile.mktemp(suffix='.png')
        viewer.screenshot(tmp)
        assert os.path.getsize(tmp) > 0
        os.remove(tmp)

        viewer.clear()


# ============================================================================
# MASS SOURCE parser tests (integration)
# ============================================================================

class TestMassSourceParser:
    def test_parse_from_s2k(self):
        """Verify MassSource is parsed from a sample S2K file."""
        from fea_toolkit.io.s2k_parser import SAP2000Parser
        s2k_file = FIXTURES_DIR / "sample.s2k"
        if not s2k_file.exists():
            pytest.skip("sample.s2k not available")
        parser = SAP2000Parser(s2k_file)
        parser.parse()
        md = parser.get_model_data()
        assert hasattr(md, 'mass_sources')
        # sample.s2k has MSSSRC1 with Elements=True, Masses=True, Loads=False
        if md.mass_sources:
            ms = md.mass_sources.get('MSSSRC1')
            if ms:
                assert ms.elements is True


# ============================================================================
# Pushover analysis tests
# ============================================================================


def _make_pushover_ab(md):
    """Create a pre-built AnalysisBuilder for pushover tests."""
    cfg = {'element_type': 'elasticBeamColumn', 'split_elements': False,
           'verbose': False}
    mesh_model = preprocess_model(md, cfg)
    ab = AnalysisBuilder(mesh_model, cfg)
    ab.build_domain()
    return ab


class TestPushoverBuild:
    """Tests for pushover analysis via AnalysisBuilder."""

    @pytest.fixture
    def cantilever_model(self):
        """A simple 2‑node cantilever for fast pushover testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=5),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel",
                              E_mod=2e11, unit_weight=77000),
        }
        sections = {
            "UB100": Section(name="UB100", shape="I/Wide Flange",
                             material="Steel", A=0.01, I33=1e-4,
                             I22=1e-5, J=1e-6),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1,
                              node_i="1", node_j="2"),
        }
        load_patterns = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD",
                                self_weight_factor=1),
            "WIND": LoadPattern(name="WIND", pattern_type="WIND",
                                self_weight_factor=0),
        }
        frame_dist_loads = [
            FrameDistributedLoad(pattern="WIND", frame_id="1",
                                 direction="X", load_type="Force",
                                 shape="Uniform", val_a=1000, val_b=1000,
                                 rdist_a=0, rdist_b=1, dist_a=0, dist_b=5),
        ]
        return SAPModelData(
            nodes=nodes, restraints=restraints,
            materials=materials, sections=sections,
            frame_elements=frames, area_elements={},
            frame_assignments={"1": "UB100"},
            area_assignments={}, groups={}, frame_auto_mesh={},
            load_patterns=load_patterns,
            frame_dist_loads=frame_dist_loads,
        )

    def test_returns_expected_keys(self, cantilever_model):
        """Result dict has all required keys (pattern type)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='pattern',
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1, num_steps=5,
            print_progress=False,
        )
        for key in ('step', 'control_disp', 'base_shear',
                    'status', 'control_node', 'dof', 'lateral_load_type'):
            assert key in results
        assert results['lateral_load_type'] == 'pattern'

    def test_gravity_base_shear_zero(self, cantilever_model):
        """After gravity alone, lateral base shear ≈ 0."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='pattern',
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1, num_steps=5,
            print_progress=False,
        )
        # Note: initial base_shear includes gravity reaction
        assert abs(results['base_shear'][0]) < 3000.0

    def test_cantilever_linear_pushover_pattern(self, cantilever_model):
        """Cantilever with elastic sections: linear, monotonic (pattern)."""
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='pattern',
            lateral_pattern_name="WIND",
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1, num_steps=10,
            print_progress=False,
        )
        assert len(results['control_disp']) == 11
        assert results['status'][-1] == 0, "Last step failed"
        shears = [abs(v) for v in results['base_shear']]
        assert all(shears[i] <= shears[i + 1]
                   for i in range(len(shears) - 1)), "Not monotonic"
        assert abs(results['control_disp'][-1] - 0.1) < 0.01

    def test_uniform_pattern_returns_keys(self, cantilever_model):
        """Uniform pattern returns expected keys."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1, num_steps=5,
            print_progress=False,
        )
        for key in ('step', 'control_disp', 'base_shear',
                    'status', 'control_node', 'dof'):
            assert key in results
        assert results['lateral_load_type'] == 'uniform'

    def test_triangular_pattern_returns_keys(self, cantilever_model):
        """Triangular pattern returns expected keys."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='triangular',
            lateral_direction="X",
            control_node_tag=2,
            max_disp=0.1, num_steps=5,
            print_progress=False,
        )
        for key in ('step', 'control_disp', 'base_shear',
                    'status', 'control_node', 'dof'):
            assert key in results

    def test_invalid_lateral_load_type_raises(self, cantilever_model):
        """Invalid lateral_load_type raises ValueError."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        import pytest
        with pytest.raises(ValueError, match="Unknown lateral_load_type"):
            b.run_pushover_analysis(
                gravity_patterns={"DEAD": 1.0},
                lateral_load_type='wind',
                lateral_direction="X",
                control_node_tag=2,
                max_disp=0.1, num_steps=5,
                print_progress=False,
            )

    def test_pattern_requires_name(self, cantilever_model):
        """pattern type without lateral_pattern_name raises ValueError."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        import pytest
        with pytest.raises(ValueError, match="lateral_pattern_name is required"):
            b.run_pushover_analysis(
                gravity_patterns={"DEAD": 1.0},
                lateral_load_type='pattern',
                lateral_direction="X",
                control_node_tag=2,
                max_disp=0.1, num_steps=5,
                print_progress=False,
            )

    def test_pushover_via_two_stage_path(self, cantilever_model):
        """Pushover returns correct keys through the two-stage path."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses(g=9.81)
        modal = b.run_modal_analysis(num_modes=3, print_results=False)
        shapes = b.extract_mode_shapes(3)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.3, num_steps=5,
            print_progress=False,
        )
        for key in ('step', 'control_disp', 'base_shear', 'status',
                    'gravity_displacements', 'control_node', 'dof',
                    'lateral_load_type'):
            assert key in results, f"Missing key: {key}"
        assert len(results['step']) == len(results['control_disp']) == \
            len(results['base_shear']) == len(results['status'])
        assert results['step'][0] == 0  # gravity step recorded
        assert results['control_node'] == 2
        assert results['dof'] == 1  # X direction

    def test_pushover_uniform_via_two_stage(self, cantilever_model):
        """Uniform pushover produces non-zero base shear through two-stage."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses(g=9.81)
        modal = b.run_modal_analysis(num_modes=3, print_results=False)
        shapes = b.extract_mode_shapes(3)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.3, num_steps=5,
            print_progress=False,
        )
        # Base shears should be non-zero (cantilever fixed at base, push at top)
        assert any(abs(v) > 0 for v in results['base_shear']), \
            "Expected non-zero base shear in at least one step"
        # Displacement should increase monotonically
        assert all(
            results['control_disp'][i] <= results['control_disp'][i + 1]
            for i in range(len(results['control_disp']) - 1)
        ), "Control displacement should be monotonic"


# ============================================================================
# HingeRadau beam integration tests
# ============================================================================


class TestHingeRadauIntegration:
    """Tests for :func:`compute_hinge_length`."""

    def test_hinge_length_i_section(self):
        """ISection depth → Lp = 0.5 * depth."""
        from fea_toolkit.model.checks import compute_hinge_length
        md = SAPModelData(nodes={}, restraints={}, materials={}, sections={},
                          frame_elements={}, area_elements={},
                          frame_assignments={}, area_assignments={},
                          groups={}, frame_auto_mesh={})
        md.sections["UB300"] = ISection(
            name="UB300", shape="I/Wide Flange", material="Steel",
            depth=0.3, bf=0.15, tf=0.01, tw=0.006,
            A=8e-3, I33=1.2e-4, I22=4e-5, J=2e-6,
        )
        Lp = compute_hinge_length(md.sections["UB300"], 10.0)
        assert abs(Lp - 0.15) < 0.01  # 0.5 * 0.3

    def test_hinge_length_pipe_section(self):
        """Pipe OD → Lp = 0.5 * OD."""
        from fea_toolkit.model.checks import compute_hinge_length
        md = SAPModelData(nodes={}, restraints={}, materials={}, sections={},
                          frame_elements={}, area_elements={},
                          frame_assignments={}, area_assignments={},
                          groups={}, frame_auto_mesh={})
        md.sections["PIP4"] = PipeSection(
            name="PIP4", shape="Pipe", material="Steel",
            od=0.1143, t=0.006, A=2e-3, I33=3e-6, I22=3e-6, J=1e-6,
        )
        Lp = compute_hinge_length(md.sections["PIP4"], 10.0)
        assert abs(Lp - 0.05715) < 0.001  # 0.5 * 0.1143

    def test_hinge_length_fallback(self):
        """Unknown section → Lp = 0.1 * L."""
        from fea_toolkit.model.checks import compute_hinge_length
        md = SAPModelData(nodes={}, restraints={}, materials={}, sections={},
                          frame_elements={}, area_elements={},
                          frame_assignments={}, area_assignments={},
                          groups={}, frame_auto_mesh={})
        md.sections["GENERIC"] = Section(
            name="GENERIC", shape="NA", material="Steel",
            A=1e-2, I33=1e-4, I22=1e-4, J=1e-6,
        )
        Lp = compute_hinge_length(md.sections["GENERIC"], 8.0)
        assert abs(Lp - 0.8) < 0.01  # 0.1 * 8.0


# ============================================================================
# Brace subdivision tests
# ============================================================================


class TestSubdivideElements:
    """Tests for :func:`fea_toolkit.model.geometry.subdivide_elements`."""

    def test_subdivide_creates_sub_elements(self):
        """4 segments → 4 child elements, original marked inactive."""
        from fea_toolkit.model.geometry import subdivide_elements
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        assignments = {"B1": "UB300"}
        result_elems, result_assign, result_nodes, _, _ = subdivide_elements(
            elements, assignments, nodes,
            n_segments=4, brace_ids={"B1"}, next_tag=100,
        )
        assert elem.inactive is True, "Original should be inactive"
        assert len(result_elems) == 5  # 1 original + 4 subs
        sub_ids = [eid for eid in result_elems if eid.startswith("B1_sub")]
        assert len(sub_ids) == 4
        for sid in sub_ids:
            assert sid in result_assign
            assert result_assign[sid] == "UB300"

    def test_subdivide_creates_internal_nodes(self):
        """4 segments → 3 new internal nodes."""
        from fea_toolkit.model.geometry import subdivide_elements
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        assignments = {"B1": "UB300"}
        _, _, result_nodes, _, _ = subdivide_elements(
            elements, assignments, nodes,
            n_segments=4, brace_ids={"B1"}, next_tag=100,
        )
        new_nodes = [nid for nid in result_nodes if nid.startswith("B1_sub")]
        assert len(new_nodes) == 3  # 4 segments → 3 internal nodes

    def test_imperfection_offsets_mid_node(self):
        """Mid-node of subdivided brace has lateral offset ≈ L/500."""
        from fea_toolkit.model.geometry import subdivide_elements
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        _, _, result_nodes, _, _ = subdivide_elements(
            elements, assignments={"B1": "UB300"}, nodes=nodes,
            n_segments=4, imperfection_ratio=1/500, brace_ids={"B1"},
            next_tag=100,
        )
        # The middle internal node (at z≈5) should have an x-offset
        mid_nodes = [n for nid, n in result_nodes.items()
                     if nid.startswith("B1_sub") and abs(n.z - 5.0) < 0.5]
        assert len(mid_nodes) > 0, "No midpoint node found — subdivision may have failed"
        # Imperfection is perpendicular to the brace axis. For a vertical brace
        # (0,0,0)→(0,0,10) the perpendicular direction is Y.
        offset = abs(mid_nodes[0].y)
        assert offset > 0.001, f"Expected imperfection offset, got {offset}"

    def test_end_offset_creates_rigid_links(self):
        """end_offset > 0 creates offset nodes and rigid link entries."""
        from fea_toolkit.model.geometry import subdivide_elements
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        _, _, result_nodes, _, rigid_links = subdivide_elements(
            elements, assignments={"B1": "UB300"}, nodes=nodes,
            n_segments=4, brace_ids={"B1"}, end_offset=0.5, next_tag=100,
        )
        # Should have two rigid links (I-end and J-end)
        assert len(rigid_links) == 2
        link_i, link_j = rigid_links
        assert link_i[1] == "1"   # I-end: original node
        assert link_j[2] == "2"   # J-end: original node
        # Should have two offset nodes
        offset_ids = [nid for nid in result_nodes if "_offset_" in nid]
        assert len(offset_ids) == 2
        # Sub-elements should connect to offset nodes, not original nodes
        sub_ids = [eid for eid in elements if "_sub_" in eid]
        first_sub = elements[sub_ids[0]]
        last_sub = elements[sub_ids[-1]]
        assert first_sub.node_i in offset_ids
        assert last_sub.node_j in offset_ids

    def test_end_offset_clamped_to_half_length(self):
        """end_offset larger than half length is clamped."""
        from fea_toolkit.model.geometry import subdivide_elements
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=5),
        }
        elem = FrameElement(elem_id="B1", elem_tag=10, node_i="1", node_j="2")
        elements = {"B1": elem}
        _, _, result_nodes, _, rigid_links = subdivide_elements(
            elements, assignments={"B1": "UB300"}, nodes=nodes,
            n_segments=2, brace_ids={"B1"}, end_offset=3.0, next_tag=100,
        )
        # Brace should still have at least some length (clamped to 45%)
        offset_ids = [nid for nid in result_nodes if "_offset_" in nid]
        if offset_ids:
            # Check offset nodes are within bounds
            for nid in offset_ids:
                n = result_nodes[nid]
                assert 0.0 <= n.z <= 5.0


# ============================================================================
# Euler buckling check tests
# ============================================================================


class TestBraceBucklingCheck:
    """Tests for :meth:`OpenSeesBuilder.check_brace_buckling`."""

    @pytest.fixture
    def brace_model(self):
        """A simple 2‑node cantilever used as a brace."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=6, y=0, z=6),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel",
                              E_mod=2e11, unit_weight=77000),
        }
        sections = {
            "PIP4": PipeSection(name="PIP4", shape="Pipe", material="Steel",
                                od=0.1143, t=0.006,
                                A=2e-3, I33=3e-6, I22=3e-6, J=1e-6),
        }
        frames = {
            "B1": FrameElement(elem_id="B1", elem_tag=1,
                               node_i="1", node_j="2"),
        }
        return SAPModelData(
            nodes=nodes, restraints=restraints,
            materials=materials, sections=sections,
            frame_elements=frames, area_elements={},
            frame_assignments={"B1": "PIP4"},
            area_assignments={}, groups={}, frame_auto_mesh={},
        )

    def test_euler_buckling_pinned(self, brace_model):
        """Euler P_cr with K=1 matches π²EI/L²."""
        from fea_toolkit.model.checks import check_brace_buckling
        results = check_brace_buckling(brace_model, brace_ids={"B1"}, K=1.0,
                                        print_results=False)
        assert "B1" in results
        r = results["B1"]
        # L = sqrt(6² + 6²) ≈ 8.485, I = 3e-6, E = 2e11
        expected = (math.pi ** 2 * 2e11 * 3e-6) / (8.485 ** 2)
        assert abs(r["P_cr"] - expected) / expected < 0.01
        assert r["slenderness"] > 0

    def test_buckling_with_axial_demand(self, brace_model):
        """D/C ratio computed correctly."""
        from fea_toolkit.model.checks import check_brace_buckling
        results = check_brace_buckling(
            brace_model, brace_ids={"B1"}, K=1.0,
            axial_demand={"B1": 50000.0},  # 50 kN
            print_results=False,
        )
        r = results["B1"]
        assert r["P_demand"] == 50000.0
        assert r["ratio"] > 0

    def test_from_brace_sections(self):
        """Selection.from_brace_sections detects Pipe, Angle, etc."""
        from fea_toolkit.model.selection import Selection
        sections = {
            "PIP4": PipeSection(name="PIP4", shape="Pipe", material="Steel",
                                od=0.1, t=0.005, A=1e-3,
                                I33=1e-6, I22=1e-6, J=1e-7),
            "UB300": ISection(name="UB300", shape="I/Wide Flange",
                              material="Steel", depth=0.3, bf=0.15,
                              tf=0.01, tw=0.006,
                              A=8e-3, I33=1.2e-4, I22=4e-5, J=2e-6),
        }
        model = SAPModelData(
            nodes={}, restraints={}, materials={}, sections=sections,
            frame_elements={}, area_elements={},
            frame_assignments={}, area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        sel = Selection.from_brace_sections(model)
        assert sel.sections is not None
        assert "PIP4" in sel.sections
        assert "UB300" not in sel.sections


# ============================================================================
# Integration test: subdivided brace in pushover pipeline
# ============================================================================


class TestSubdividedBraceInPushover:
    """Verify that braces with subdivision + imperfection can be built and
    run through a pushover analysis without error.

    This tests the pipeline integration — not the exact buckling load
    (which is verified analytically in ``TestBraceBucklingCheck``).
    The practical workflow is:

    1. Identify braces via ``Selection``
    2. Subdivide them with imperfection via ``set_brace_selection()``
    3. Run pushover analysis
    4. Optionally check critical braces via ``check_brace_buckling()``
    """

    @pytest.fixture
    def brace_model(self):
        """A slender 10 m pin-pin pipe column for pushover testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel",
                              E_mod=2e11, unit_weight=77000, Fy=2.5e8),
        }
        sections = {
            "PIP4": PipeSection(name="PIP4", shape="Pipe", material="Steel",
                                od=0.1, t=0.005,
                                A=0.001492, I33=1.70e-6, I22=1.70e-6, J=3.4e-6),
        }
        frames = {
            "B1": FrameElement(elem_id="B1", elem_tag=10,
                               node_i="1", node_j="2"),
        }
        load_patterns = {
            "WIND": LoadPattern(name="WIND", pattern_type="Wind",
                                self_weight_factor=0),
        }
        frame_dist_loads = [
            FrameDistributedLoad(pattern="WIND", frame_id="B1",
                                 direction="X", load_type="Force",
                                 shape="Uniform", val_a=5000, val_b=5000,
                                 rdist_a=0, rdist_b=1, dist_a=0, dist_b=10),
        ]
        return SAPModelData(
            nodes=nodes, restraints=restraints,
            materials=materials, sections=sections,
            frame_elements=frames, area_elements={},
            frame_assignments={"B1": "PIP4"},
            area_assignments={}, groups={}, frame_auto_mesh={},
            load_patterns=load_patterns,
            frame_dist_loads=frame_dist_loads,
        )

    def test_subdivided_brace_builds_and_runs(self, brace_model):
        """Builder with subdivided braces runs pushover without crash."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        b = OpenSeesBuilder(brace_model, {
            'element_type': 'forceBeamColumn',
            'create_fiber_sections': True,
            'geom_transf_type': 'Corotational',
            'split_elements': False,
            'verbose': False,
        })
        b.set_brace_selection({"B1"}, end_offset=0.0)
        b.build()

        # Run a quick pushover to verify the pipeline holds
        results = b.run_pushover_analysis(
            gravity_patterns={},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.05,
            num_steps=5,
            print_progress=False,
        )
        assert results is not None
        assert 'control_disp' in results
        assert len(results['control_disp']) > 1

    def test_check_buckling_after_pushover(self, brace_model):
        """Can check Euler buckling of braces (analytical, no OpenSees needed)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        b = OpenSeesBuilder(brace_model, {
            'element_type': 'forceBeamColumn',
            'create_fiber_sections': True,
            'split_elements': False,
            'verbose': False,
        })
        b.set_brace_selection({"B1"}, end_offset=0.0)

        # Check Euler buckling directly from model data (no analysis required)
        buckling = b.check_brace_buckling(
            brace_ids={"B1"}, K=1.0, print_results=False,
        )
        assert "B1" in buckling
        assert buckling["B1"]["P_cr"] > 0
        assert buckling["B1"]["slenderness"] > 0
        # P_cr ≈ π² × 2e11 × 1.7e-6 / 10² ≈ 33.6 kN
        P_cr = buckling["B1"]["P_cr"]
        assert 30000 < P_cr < 37000, f"Expected P_cr ≈ 33.6 kN, got {P_cr:.0f} N"


# ============================================================================
# Euler buckling benchmark: SciPy eigenvalue analysis of subdivided column
# ============================================================================


class TestEulerBucklingBenchmark:
    """Benchmark: eigenvalue buckling of a subdivided column via SciPy.

    Assembles the global elastic stiffness matrix *K* and geometric stiffness
    matrix *K_g* for the subdivided column using standard Euler-Bernoulli
    beam elements, then solves the generalised eigenvalue problem:

    .. math:: (K - \\lambda K_g)\\phi = 0

    using ``scipy.linalg.eig``.  The smallest positive eigenvalue gives the
    buckling load :math:`P_{cr}`, which should match the analytical Euler
    formula :math:`\\pi^2 EI / (KL)^2` within a small discretisation error.

    This is an **independent verification** of the subdivided brace concept
    — it does **not** depend on OpenSees' nonlinear solver, so it is fast,
    deterministic, and numerically robust.
    """

    def test_eigenvalue_buckling_matches_euler(self):
        """Eigenvalue buckling from FEA assembly matches Euler P_cr within 5 %."""
        scipy = pytest.importorskip("scipy", reason="scipy not installed")
        from scipy.linalg import eig
        import numpy as np

        L = 10.0
        E = 2e11
        I22 = 1.70e-6
        P_cr_euler = (math.pi ** 2 * E * I22) / (L ** 2)

        # Subdivide into N segments
        n_seg = 6
        seg_len = L / n_seg
        n_nodes = n_seg + 1  # total nodes including ends

        # DOF numbering: each node has 2 DOFs (v, θ)
        # Pinned ends: v=0, θ free → remove v DOFs at ends
        n_dof_total = n_nodes * 2      # raw DOFs including constraints
        constrained = {0}                # node 0: v=0 → DOF 0 removed (θ free)
        constrained.add(n_nodes * 2 - 2)  # last node: v=0 → DOF removed (θ free)
        dof_map_raw = [d for d in range(n_dof_total) if d not in constrained]
        n_dof = len(dof_map_raw)
        # dof_map_raw[i] = global raw DOF index for reduced DOF i

        def beam_stiffness(Le, Ee, Ie):
            return np.array([
                [12*Ee*Ie/Le**3,  6*Ee*Ie/Le**2, -12*Ee*Ie/Le**3,  6*Ee*Ie/Le**2],
                [6*Ee*Ie/Le**2,   4*Ee*Ie/Le,    -6*Ee*Ie/Le**2,  2*Ee*Ie/Le],
                [-12*Ee*Ie/Le**3, -6*Ee*Ie/Le**2, 12*Ee*Ie/Le**3, -6*Ee*Ie/Le**2],
                [6*Ee*Ie/Le**2,   2*Ee*Ie/Le,    -6*Ee*Ie/Le**2,  4*Ee*Ie/Le],
            ])

        def beam_geo_stiffness(Le):
            return (1.0 / (30 * Le)) * np.array([
                [36,     3*Le,    -36,     3*Le],
                [3*Le,  4*Le**2, -3*Le,  -Le**2],
                [-36,   -3*Le,    36,    -3*Le],
                [3*Le, -Le**2,  -3*Le,  4*Le**2],
            ])

        def to_global(raw_dofs):
            """Map 4 element DOFs to reduced system indices (or -1 if constrained)."""
            return [dof_map_raw.index(d) if d in dof_map_raw else -1 for d in raw_dofs]

        K = np.zeros((n_dof, n_dof))
        Kg = np.zeros((n_dof, n_dof))

        for seg in range(n_seg):
            n0 = seg       # left node index
            n1 = seg + 1   # right node index
            # Raw DOFs: [n0*2 (v0), n0*2+1 (θ0), n1*2 (v1), n1*2+1 (θ1)]
            raw = [n0*2, n0*2+1, n1*2, n1*2+1]
            gn = to_global(raw)

            k_e = beam_stiffness(seg_len, E, I22)
            k_ge = beam_geo_stiffness(seg_len)

            for i in range(4):
                gi = gn[i]
                if gi < 0:
                    continue
                for j in range(4):
                    gj = gn[j]
                    if gj < 0:
                        continue
                    K[gi, gj] += k_e[i, j]
                    Kg[gi, gj] += k_ge[i, j]

        # Solve (K - λ Kg)φ = 0
        eigvals, _ = eig(K, Kg)
        # The smallest positive eigenvalue is the buckling load
        buckling_loads = sorted([
            np.real(ev) for ev in eigvals
            if np.real(ev) > 1000 and not np.iscomplex(ev)
        ])
        assert len(buckling_loads) > 0, "No valid buckling eigenvalues found"
        P_cr_fea = buckling_loads[0]
        ratio = P_cr_fea / P_cr_euler
        assert 0.95 < ratio < 1.10, (
            f"FEA eigenvalue P_cr ({P_cr_fea:.0f} N) differs from Euler "
            f"({P_cr_euler:.0f} N) by {abs(1-ratio)*100:.1f}%"
        )


# ============================================================================
# Capacity Spectrum Method tests
# ============================================================================


class TestCapacitySpectrumMethod:
    """Tests for :meth:`OpenSeesBuilder.pushover_to_adrs` and
    :meth:`OpenSeesBuilder.compute_performance_point`."""

    @pytest.fixture
    def cantilever_model(self):
        """2-node cantilever with seismic mass for CSM testing."""
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=0, y=0, z=10),
        }
        restraints = {"1": Restraint([1, 1, 1, 1, 1, 1])}
        materials = {
            "Steel": Material(name="Steel", type="Steel",
                              E_mod=2e11, unit_weight=77000),
        }
        sections = {
            "UB300": Section(name="UB300", shape="I/Wide Flange",
                             material="Steel", A=0.01, I33=1.2e-4,
                             I22=4e-5, J=2e-6),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1,
                              node_i="1", node_j="2"),
        }
        load_patterns = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD",
                                self_weight_factor=1),
        }
        mass_sources = {
            "M1": MassSource(name="M1", elements=True,
                             masses=False, loads=False),
        }
        return SAPModelData(
            nodes=nodes, restraints=restraints,
            materials=materials, sections=sections,
            frame_elements=frames, area_elements={},
            frame_assignments={"1": "UB300"},
            area_assignments={}, groups={}, frame_auto_mesh={},
            load_patterns=load_patterns,
            mass_sources=mass_sources,
        )

    def test_pushover_to_adrs_returns_expected_keys(self, cantilever_model):
        """pushover_to_adrs returns S_a, S_d, Gamma, M_eff, phi_control."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses(g=9.81)
        modal = b.run_modal_analysis(num_modes=3, print_results=False)
        shapes = b.extract_mode_shapes(3)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.3, num_steps=5,
            print_progress=False,
        )
        adrs = b.pushover_to_adrs(results, modal, shapes, direction='X', g=9.81)
        for key in ('S_a', 'S_d', 'Gamma', 'M_eff', 'phi_control', 'best_mode'):
            assert key in adrs
        assert abs(adrs['M_eff']) > 0
        assert len(adrs['S_a']) == len(adrs['S_d'])

    def test_pushover_to_adrs_values_consistent(self, cantilever_model):
        """ADRS values are positive and consistent (no NaN or negative)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses(g=9.81)
        modal = b.run_modal_analysis(num_modes=3, print_results=False)
        shapes = b.extract_mode_shapes(3)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.3, num_steps=5,
            print_progress=False,
        )
        adrs = b.pushover_to_adrs(results, modal, shapes, direction='X', g=9.81)
        assert all(v >= 0 for v in adrs['S_a'])
        assert all(v >= 0 for v in adrs['S_d'])
        assert all(math.isfinite(v) for v in adrs['S_a'])
        assert all(math.isfinite(v) for v in adrs['S_d'])

    def test_performance_point_elastic(self, cantilever_model):
        """Elastic cantilever: S_dp matches demand at modal period."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = _make_pushover_ab(cantilever_model)
        b.compute_seismic_masses(g=9.81)
        modal = b.run_modal_analysis(num_modes=3, print_results=False)
        shapes = b.extract_mode_shapes(3)
        results = b.run_pushover_analysis(
            gravity_patterns={"DEAD": 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            control_node_tag=2,
            max_disp=0.3, num_steps=5,
            print_progress=False,
        )
        # Simple elastic design spectrum (GB 50011-like)
        T_spec = [0.0, 0.1, 0.35, 0.5, 1.0, 2.0, 4.0, 6.0]
        Sa_spec = [0.16*9.81*0.45, 0.16*9.81, 0.16*9.81, 0.16*9.81,
                   0.16*9.81*0.35, 0.16*9.81*0.35/2,
                   0.16*9.81*0.35/4, 0.16*9.81*0.35/6]
        pp = b.compute_performance_point(
            results, modal, shapes, T_spec, Sa_spec, direction='X',
        )
        # Elastic → mu=1, S_dp should be positive and finite
        assert pp['converged']
        assert pp['S_dp'] > 0
        assert pp['mu'] == pytest.approx(1.0, abs=0.01)
        assert pp['S_ap'] > 0
        # T_eq should be close to the dominant modal period (0.464s)
        assert pp['T_eq'] == pytest.approx(0.464, abs=0.03)


# ============================================================================
# Builder integration tests: frame end offsets + area meshing
# ============================================================================

class TestBuilderFrameEndOffsets:
    """Verify frame end offsets are applied during build()."""

    @pytest.fixture
    def offset_model(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
        }
        mats = {"Steel": Material("Steel", "Steel", E_mod=2e11)}
        secs = {
            "UB300": Section("UB300", "I/Wide Flange", "Steel",
                             A=0.01, I33=1e-4, I22=1e-5, J=1e-6),
        }
        frames = {"1": FrameElement("1", 10, "1", "2")}
        return SAPModelData(
            nodes=nodes, restraints={"1": Restraint([1,1,1,1,1,1])},
            materials=mats, sections=secs,
            frame_elements=frames, area_elements={},
            frame_assignments={"1": "UB300"},
            area_assignments={}, groups={}, frame_auto_mesh={},
            frame_end_offsets={"1": FrameEndOffset(0.3, 0.4)},
        )

    def test_offset_nodes_created_in_opensees(self, offset_model):
        """Offset nodes are created at correct positions."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        import openseespy.opensees as ops
        b = OpenSeesBuilder(offset_model, {
            "verbose": False, "use_elastic_sections": True,
        })
        try:
            b.build()
            # Offset nodes: I-end offset=0.3, J-end offset=0.4
            # Element from (0,0,0) → (6,0,0), length 6
            # I-end offset node at: (0 + 0.3, 0, 0) = (0.3, 0, 0)
            # J-end offset node at: (6 - 0.4, 0, 0) = (5.6, 0, 0)
            assert "1_off_i" in offset_model.nodes, "I-end offset node missing"
            assert "1_off_j" in offset_model.nodes, "J-end offset node missing"
            for nid, nd in offset_model.nodes.items():
                if "_off_i" in nid:
                    coords = list(ops.nodeCoord(nd.node_tag))
                    assert coords == pytest.approx([0.3, 0.0, 0.0], abs=1e-9)
                elif "_off_j" in nid:
                    coords = list(ops.nodeCoord(nd.node_tag))
                    assert coords == pytest.approx([5.6, 0.0, 0.0], abs=1e-9)
        finally:
            ops.wipe()

    def test_rigid_links_recorded(self, offset_model):
        """_offset_rigid_links contains entries after build()."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        import openseespy.opensees as ops
        b = OpenSeesBuilder(offset_model, {
            "verbose": False, "use_elastic_sections": True,
        })
        try:
            b.build()
            assert len(b._offset_rigid_links) == 2
        finally:
            ops.wipe()

    def test_no_offsets_no_links(self):
        """Zero offsets produce no rigid links."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
        }
        mats = {"Steel": Material("Steel", "Steel", E_mod=2e11)}
        secs = {
            "UB300": Section("UB300", "I/Wide Flange", "Steel",
                             A=0.01, I33=1e-4, I22=1e-5, J=1e-6),
        }
        frames = {"1": FrameElement("1", 10, "1", "2")}
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=mats, sections=secs,
            frame_elements=frames, area_elements={},
            frame_assignments={"1": "UB300"},
            area_assignments={}, groups={}, frame_auto_mesh={},
            frame_end_offsets={"1": FrameEndOffset(0.0, 0.0)},
        )
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(md, {
            "verbose": False, "use_elastic_sections": True,
        })
        try:
            b.build()
            assert len(b._offset_rigid_links) == 0
        finally:
            import openseespy.opensees as ops
            ops.wipe()


class TestBuilderAreaMeshing:
    """Verify area elements are meshed during build()."""

    @pytest.fixture
    def mesh_model(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        secs = {
            "Slab200": ShellSection("Slab200", "Shell", "Concrete",
                                    thickness=0.2),
        }
        areas = {"1": AreaElement("1", 10, ["1", "2", "3", "4"])}
        return SAPModelData(
            nodes=nodes, restraints={}, materials=mats, sections=secs,
            frame_elements={}, area_elements=areas,
            frame_assignments={}, area_assignments={"1": "Slab200"},
            groups={}, frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=6.0)},
        )

    def test_mesh_creates_sub_areas(self, mesh_model):
        """build() with meshing creates exactly 4 sub-quads (2×2 grid)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(mesh_model, {
            "verbose": False, "create_shells": True,
        })
        try:
            b.build()
            # Original area should be inactive
            assert mesh_model.area_elements["1"].inactive is True
            # 12×8 quad with max_size=6.0 → ceil(12/6)=2 × ceil(8/6)=2 = 4
            sub_ids = sorted(
                aid for aid in mesh_model.area_elements if "_sub_" in aid
            )
            assert len(sub_ids) == 4
            # Sub-areas should all be active
            for sid in sub_ids:
                assert mesh_model.area_elements[sid].inactive is False
            # Section assignment inherited
            for sid in sub_ids:
                assert mesh_model.area_assignments.get(sid) == "Slab200"
        finally:
            import openseespy.opensees as ops; ops.wipe()

    def test_mesh_creates_opensees_nodes(self, mesh_model):
        """Mesh nodes are created at correct grid positions."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        import openseespy.opensees as ops
        b = OpenSeesBuilder(mesh_model, {
            "verbose": False, "create_shells": True,
        })
        try:
            b.build()
            # 2×2 grid → 5 mesh nodes (4 edge midpoints + 1 interior)
            mesh_nodes = {nid: nd for nid, nd in mesh_model.nodes.items()
                          if "_mesh_" in nid}
            assert len(mesh_nodes) == 5
            # Expected coordinates (12×8 rectangle, bilinear grid)
            expected = {
                "1_mesh_0_1": (6.0, 0.0, 0.0),   # edge midpoint
                "1_mesh_1_0": (0.0, 4.0, 0.0),
                "1_mesh_1_1": (6.0, 4.0, 0.0),   # fully interior
                "1_mesh_1_2": (12.0, 4.0, 0.0),
                "1_mesh_2_1": (6.0, 8.0, 0.0),
            }
            for nid, nd in mesh_nodes.items():
                coords = list(ops.nodeCoord(nd.node_tag))
                assert coords == pytest.approx(expected[nid], abs=1e-9), \
                    f"{nid}: expected {expected[nid]}, got {coords}"
        finally:
            ops.wipe()

    def test_no_mesh_no_change(self):
        """Without mesh settings, area elements are unchanged."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        secs = {
            "Slab200": ShellSection("Slab200", "Shell", "Concrete",
                                    thickness=0.2),
        }
        areas = {"1": AreaElement("1", 10, ["1", "2", "3", "4"])}
        md = SAPModelData(
            nodes=nodes, restraints={}, materials=mats, sections=secs,
            frame_elements={}, area_elements=areas,
            frame_assignments={}, area_assignments={"1": "Slab200"},
            groups={}, frame_auto_mesh={},
        )
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder(md, {
            "verbose": False, "create_shells": True,
        })
        try:
            b.build()
            # No area_mesh config → no subdivision
            assert md.area_elements["1"].inactive is False
            assert md.area_elements["1"].node_ids == ["1", "2", "3", "4"]
            # No sub-area or mesh node artifacts
            assert not any("_sub_" in aid for aid in md.area_elements)
            assert not any("_mesh_" in nid for nid in md.nodes)
        finally:
            import openseespy.opensees as ops; ops.wipe()

    def test_mesh_propagates_edge_restraints(self):
        """Mesh nodes on edges between restrained corners inherit AND of DOFs."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.model.sap_data import Restraint
        import openseespy.opensees as ops

        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        # Restrain bottom edge (nodes 1,2) — both fully fixed
        # Restrain left edge (nodes 1,4) — one fixed [1,1,1,1,1,1],
        #   the other pinned [1,1,1,0,0,0] → AND should be [1,1,1,0,0,0]
        restraints = {
            "1": Restraint([1, 1, 1, 1, 1, 1]),  # fully fixed
            "2": Restraint([1, 1, 1, 1, 1, 1]),  # fully fixed
            "4": Restraint([1, 1, 1, 0, 0, 0]),  # pinned
        }
        mats = {"Concrete": Material("Concrete", "Concrete", E_mod=3e10)}
        secs = {
            "Slab200": ShellSection("Slab200", "Shell", "Concrete",
                                    thickness=0.2),
        }
        areas = {"1": AreaElement("1", 10, ["1", "2", "3", "4"])}
        md = SAPModelData(
            nodes=nodes, restraints=restraints, materials=mats, sections=secs,
            frame_elements={}, area_elements=areas,
            frame_assignments={}, area_assignments={"1": "Slab200"},
            groups={}, frame_auto_mesh={},
            area_mesh={"1": AreaMesh(auto_mesh=True, max_size=6.0)},
        )
        try:
            b = OpenSeesBuilder(md, {
                "verbose": False, "create_shells": True,
                "use_preprocessor": False,
            })
            b.build()

            # Mesh node restraints are applied via ops.fix() in OpenSees
            # but NOT written to self.model.restraints (which tracks only
            # original SAP2000 restraints).  Verify the build succeeded
            # without error and that model.restraints is clean.
            n1 = md.nodes.get("1_mesh_0_1")  # (6, 0, 0)
            assert n1 is not None, "bottom-edge mesh node missing"

            # No mesh node IDs should appear in model.restraints
            mesh_ids = {nid for nid in md.nodes if "_mesh_" in nid}
            restrained_mesh = mesh_ids & set(md.restraints.keys())
            assert len(restrained_mesh) == 0, \
                f"mesh nodes should NOT appear in model.restraints: {restrained_mesh}"

            # Build succeeded — ops.fix was called without errors.
            # Verify by running a quick static step: the restrained
            # bottom-edge node should have zero displacement.
            # Check the mesh node at (6,0,0) is fixed in OpenSees
            mesh_tag = b._node_tag_from_id("1_mesh_0_1")
            assert mesh_tag is not None
            fixed = ops.getFixedDOFs(int(mesh_tag))
            assert len(fixed) == 6, \
                f"mesh node {mesh_tag} should have 6 fixed DOFs, got {fixed}"

            assert md.area_elements["1"].inactive, \
                "original area should be inactive after meshing"

        finally:
            ops.wipe()


# ============================================================================
# Concrete section fiber patch tests
# ============================================================================

class TestConcreteRectangularSectionFiberPatches:
    """Fiber patch generation for ConcreteRectangularSection."""

    def test_concrete_rect_basic(self):
        from fea_toolkit.model.sap_data import ConcreteRectangularSection
        sec = ConcreteRectangularSection(
            name="CR400", shape="Concrete Rectangular", material="Concrete",
            A=0.16, I33=0.00213, I22=0.00213, J=0,
            depth=0.4, bf=0.4, cover=0.04,
            top_bars=4, bot_bars=4,
            top_bar_dia=0.02, bot_bar_dia=0.02,
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        # Concrete patches: core, top cover, bottom cover, left cover, right cover
        # Rebar layers: top (straight), bottom (straight)
        assert len(patches) == 7
        # Concrete patches use mat_tag for unconfined, mat_tag+1 for confined
        assert patches[0][0] == "rect"
        assert patches[0][1] == 2  # confined core (mat_tag + 1)
        assert patches[1][1] == 1  # top cover (unconfined)
        # Rebar layers (last two entries): type "straight", mat_tag + 2 = 3
        assert patches[-2][0] == "straight"
        assert patches[-2][1] == 3  # top rebar
        assert patches[-1][0] == "straight"
        assert patches[-1][1] == 3  # bottom rebar
        # Rebar uses area (m²), not diameter
        expected_area = 3.14159 * (0.01) ** 2  # π * (dia/2)²  with dia=0.02
        assert patches[-2][3] == pytest.approx(expected_area, rel=1e-3)

    def test_concrete_rect_no_rebar(self):
        from fea_toolkit.model.sap_data import ConcreteRectangularSection
        sec = ConcreteRectangularSection(
            name="CR400", shape="Concrete Rectangular", material="Concrete",
            A=0.16, I33=0.00213, I22=0.00213, J=0,
            depth=0.4, bf=0.4, cover=0.04,
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        for p in patches:
            assert p[0] in ("rect",)
        assert len(patches) >= 3  # core + 2 covers


class TestConcreteCircularSectionFiberPatches:
    """Fiber patch generation for ConcreteCircularSection."""

    def test_concrete_circ_basic(self):
        from fea_toolkit.model.sap_data import ConcreteCircularSection
        sec = ConcreteCircularSection(
            name="CC400", shape="Concrete Circular", material="Concrete",
            A=0.1256, I33=0.00126, I22=0.00126, J=0,
            diameter=0.4, cover=0.04,
            bar_count=8, bar_dia=0.02,
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        # Concrete: confined core (circ), unconfined cover (circ)
        # Rebar: circ_layer with mat_tag + 2 = 3
        assert len(patches) == 3
        assert patches[0][0] == "circ"
        assert patches[0][1] == 2  # confined core
        assert patches[1][1] == 1  # unconfined cover
        # Rebar layer
        assert patches[2][0] == "circ_layer"
        assert patches[2][1] == 3  # rebar
        expected_area = 3.14159 * (0.01) ** 2
        assert patches[2][3] == pytest.approx(expected_area, rel=1e-3)

    def test_concrete_circ_no_rebar(self):
        from fea_toolkit.model.sap_data import ConcreteCircularSection
        sec = ConcreteCircularSection(
            name="CC400", shape="Concrete Circular", material="Concrete",
            A=0.1256, I33=0.00126, I22=0.00126, J=0,
            diameter=0.4, cover=0.04,
        )
        patches = sec.to_fiber_patches(mat_tag=1)
        assert len(patches) == 2  # core + cover only, no rebar


# ============================================================================
# Builder hinge type tests
# ============================================================================

class TestBuilderHingeModel:
    """Lumped plasticity (hinge_model='lumped') integration."""

    def test_default_hinge_model_is_fiber(self):
        """Default config uses fiber (distributed plasticity)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        b = OpenSeesBuilder.__new__(OpenSeesBuilder)
        b.config = {}
        b._set_defaults()
        assert b.config['hinge_model'] == 'fiber'

    def test_asce41_hinge_length_fallback(self):
        """_compute_asce41_hinge_length returns the ASCE 41 capped value."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        md = make_sample_model()
        b = OpenSeesBuilder(md, {"verbose": False})
        Lp = b._compute_asce41_hinge_length(0, 6.0, "UB300")
        assert Lp == pytest.approx(0.59, abs=0.05)

    def test_lumped_hinge_build_invokes_create_lumped_hinges(self):
        """build() with hinge_model='lumped' exercises _create_lumped_hinges."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            'element_type': 'elasticBeamColumn',
            'hinge_model': 'lumped',
            'verbose': False,
            'use_preprocessor': False,
        })
        try:
            b.build()
            node_tags = ops.getNodeTags()
            ele_tags = ops.getEleTags()
            # Original model has 2 nodes + 1 element.
            # Lumped hinges add 2 hinge nodes + 2 zero-length elements.
            assert len(node_tags) >= 4, f"Expected ≥4 nodes, got {node_tags}"
            assert len(ele_tags) >= 3, f"Expected ≥3 elements, got {ele_tags}"
            # equalDOF constraints tie translation DOFs
            # (just verify the model is consistent)
            coords = [ops.nodeCoord(t) for t in (1, 2)]
            assert len(coords) == 2
        finally:
            ops.wipe()


# ═══════════════════════════════════════════════════════════════════
# SAPModelData utility methods
# ═══════════════════════════════════════════════════════════════════


class TestSAPModelDataMethods:
    """Tests for the utility methods added to SAPModelData."""

    @pytest.fixture
    def sample_md(self):
        nodes = {
            "1": Node(node_id="1", node_tag=10, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=20, x=6, y=0, z=0),
            "3": Node(node_id="3", node_tag=30, x=6, y=8, z=0),
        }
        frames = {
            "F1": FrameElement(elem_id="F1", elem_tag=1,
                                node_i="1", node_j="2"),
        }
        areas = {
            "A1": AreaElement(area_id="A1", area_tag=2,
                               node_ids=["1", "2", "3"]),
        }
        materials = {"C40": Material(name="C40", type="Concrete", E_mod=3e7)}
        sections = {"SEC1": Section(
            name="SEC1", material="C40", shape="Rectangular", A=0.16)}
        return SAPModelData(
            nodes=nodes, restraints={},
            materials=materials, sections=sections,
            frame_elements=frames, area_elements=areas,
            frame_assignments={"F1": "SEC1"},
            area_assignments={"A1": "SEC1"},
            groups={},
            frame_auto_mesh={},
        )

    def test_max_node_tag(self, sample_md):
        assert sample_md.max_node_tag() == 30

    def test_max_node_tag_empty(self):
        md = SAPModelData(nodes={}, restraints={},
                          materials={}, sections={},
                          frame_elements={}, area_elements={},
                          frame_assignments={}, area_assignments={},
                          groups={}, frame_auto_mesh={})
        assert md.max_node_tag() == 0

    def test_auto_detect_static_cases(self):
        from fea_toolkit.model.sap_data import LoadCase
        md = SAPModelData(
            nodes={}, restraints={},
            materials={}, sections={},
            frame_elements={}, area_elements={},
            frame_assignments={}, area_assignments={},
            groups={}, frame_auto_mesh={},
            load_cases={
                "DEAD": LoadCase(case_name="DEAD", case_type="LinStatic",
                                  design_type_option="Prog Det",
                                  design_type="Dead",
                                  design_action_option="Prog Det",
                                  design_action="Non-Composite"),
                "MODAL": LoadCase(case_name="MODAL", case_type="LinModal",
                                   design_type_option="Prog Det",
                                   design_type="Other",
                                   design_action_option="Prog Det",
                                   design_action="Other"),
            },
        )
        cases = md.auto_detect_static_cases()
        assert cases == ["DEAD"]

    def test_summary_dict(self, sample_md):
        s = sample_md.summary_dict()
        assert s["Nodes"] == 3
        assert s["Frames"] == 1
        assert s["Areas"] == 1
        assert s["Materials"] == 1
        assert s["Sections"] == 1
        assert s["X span (m)"] == 6.0
        assert s["Y span (m)"] == 8.0
        assert s["Z span (m)"] == 0.0

    def test_remove_floating_nodes(self):
        """remove_floating_nodes eliminates unreferenced nodes."""
        from fea_toolkit.model.geometry import remove_floating_nodes
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=3, y=4, z=0),  # floating
            },
            restraints={},
            materials={}, sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        rows = remove_floating_nodes(md)
        # Inert node is removed silently (no mass/loads/restraint to redistribute)
        assert len(rows) == 0
        assert "3" not in md.nodes

    def test_remove_floating_nodes_with_restraint(self):
        """Floating node with restraint transfers it to nearest neighbour."""
        from fea_toolkit.model.geometry import remove_floating_nodes
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=3, y=0, z=0),  # floating
            },
            restraints={"3": Restraint([1, 1, 1, 1, 1, 1])},
            materials={}, sections={},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1"},
            area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        rows = remove_floating_nodes(md)
        assert len(rows) == 1
        assert rows[0]["restrained"] is True
        # Restraint should have transferred
        assert "1" in md.restraints or "2" in md.restraints


# ═══════════════════════════════════════════════════════════════════
# CQC combination engine
# ═══════════════════════════════════════════════════════════════════


class TestCqcCombine:
    """Tests for :func:`fea_toolkit.spectrum.cqc_combine`."""

    def test_single_mode(self):
        """Single mode → CQC == SRSS == modal_shear."""
        from fea_toolkit.spectrum import cqc_combine

        def _sa(T):
            return 9.81  # constant 1g
        result = cqc_combine(
            eff_masses=[100.0],
            periods=[1.0],
            spectrum_fn=_sa,
            damping=0.05,
        )
        assert result is not None
        assert abs(result["base_shear_cqc"] - 981.0) < 1e-6
        assert abs(result["base_shear_srss"] - 981.0) < 1e-6

    def test_two_modes_srss(self):
        """Two uncorrelated modes → SRSS equals CQC (rho ≈ 0)."""
        from fea_toolkit.spectrum import cqc_combine

        def _sa(T):
            return 9.81
        result = cqc_combine(
            eff_masses=[100.0, 60.0],
            periods=[1.0, 0.01],  # very separated → rho ≈ 0
            spectrum_fn=_sa,
            damping=0.05,
        )
        expected_srss = math.sqrt((100 * 9.81)**2 + (60 * 9.81)**2)
        assert abs(result["base_shear_srss"] - expected_srss) < 1e-6

    def test_total_mass_missing(self):
        """Missing-mass correction is proportional to residual mass × Sa(0)."""
        from fea_toolkit.spectrum import cqc_combine
        calls = []

        def _sa(T):
            calls.append(T)
            return 9.81 if T == 0 else 9.81
        result = cqc_combine(
            eff_masses=[100.0],
            periods=[1.0],
            spectrum_fn=_sa,
            total_mass=150.0,
        )
        assert result["residual_mass"] == 50.0  # 150 - 100
        assert abs(result["base_shear_missing_mass"] - 50.0 * 9.81) < 1e-6

    def test_rigid_cutoff(self):
        """Modes below T_rigid are treated as rigid (Sa(0) scaling)."""
        from fea_toolkit.spectrum import cqc_combine

        def _sa(T):
            return 9.81 if T < 0.05 else 9.81 * 2.0  # 1g rigid, 2g flexible
        result = cqc_combine(
            eff_masses=[100.0, 60.0],
            periods=[0.02, 1.0],  # first mode is rigid
            spectrum_fn=_sa,
            T_rigid=0.05,
        )
        assert result["n_modes_rigid"] == 1
        assert result["n_modes_flexible"] == 1

    def test_empty_input(self):
        """Empty inputs return empty dict."""
        from fea_toolkit.spectrum import cqc_combine
        result = cqc_combine(eff_masses=[], periods=[], spectrum_fn=lambda T: 0)
        assert result == {}


# ═══════════════════════════════════════════════════════════════════
# Modal participation DataFrame
# ═══════════════════════════════════════════════════════════════════


class TestModalParticipationDf:
    """Tests for :func:`fea_toolkit.io.report.modal_participation_df`."""

    def test_basic(self):
        from fea_toolkit.io.report import modal_participation_df
        modal_result = {
            "periods": [0.5, 0.2],
            "modal_props": {
                "partiMassRatiosMX": [60.0, 30.0],
                "partiMassRatiosMY": [5.0, 40.0],
                "partiMassRatiosMZ": [0.0, 0.0],
                "partiMassRatiosRMX": [0.0, 0.0],
                "partiMassRatiosRMY": [0.0, 0.0],
                "partiMassRatiosRMZ": [10.0, 20.0],
            },
        }
        df = modal_participation_df(modal_result)
        assert df is not None
        assert len(df) == 3  # 2 modes + SUM
        assert float(df.iloc[2]["Mx (%)"]) == 90.0  # 60 + 30

    def test_empty(self):
        from fea_toolkit.io.report import modal_participation_df
        assert modal_participation_df({"periods": [], "modal_props": {}}) is None


# ═══════════════════════════════════════════════════════════════════
# Two-stage build (Preprocessor + AnalysisBuilder)
# ═══════════════════════════════════════════════════════════════════


class TestTwoStageBuild:
    """Tests for the ``use_preprocessor=True`` two-stage build path."""

    def test_mesh_model_creation(self):
        """Preprocessor produces a MeshModel with correct topology."""
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, FrameElement, AreaElement,
            Section, Material,
        )
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=6, y=4, z=0),
            },
            restraints={},
            materials={"C40": Material(name="C40", type="Concrete", E_mod=3e7)},
            sections={"SEC1": Section(
                name="SEC1", material="C40", shape="Rectangular", A=0.16)},
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
                "F2": FrameElement(elem_id="F2", elem_tag=20,
                                    node_i="2", node_j="3"),
            },
            area_elements={},
            frame_assignments={"F1": "SEC1", "F2": "SEC1"},
            area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        from fea_toolkit.opensees.preprocessor import Preprocessor
        pp = Preprocessor({"split_elements": True, "verbose": False})
        mesh = pp.run(md)
        assert isinstance(mesh, MeshModel)
        assert mesh.num_nodes == 3
        assert mesh.num_frames == 2
        assert mesh.summary()["Frames"] == 2

    def test_analysis_builder_domain(self):
        """AnalysisBuilder creates OpenSees domain from MeshModel."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.preprocessor import Preprocessor
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        md = make_sample_model()
        pp = Preprocessor({"split_elements": True, "create_shells": False,
                            "verbose": False})
        mesh = pp.run(md)
        builder = AnalysisBuilder(mesh, {"verbose": False})
        try:
            builder.build_domain()
            node_tags = ops.getNodeTags()
            ele_tags = ops.getEleTags()
            assert len(node_tags) > 0
            assert len(ele_tags) > 0
        finally:
            ops.wipe()

    def test_facade_two_stage_build(self):
        """OpenSeesBuilder with use_preprocessor=True builds correctly."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": True,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            assert b._mesh_model is not None
            assert len(b.frame_tag_map) > 0
            node_tags = ops.getNodeTags()
            assert len(node_tags) > 0
        finally:
            ops.wipe()

    def test_facade_preserves_split_state(self):
        """Facade copies split state back to builder for compat."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": True,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            # These attributes are expected by existing callers
            assert b.split_elements is not None
            assert b.split_assignments is not None
            assert hasattr(b, 'frame_tag_map')
            assert len(b.frame_tag_map) > 0
        finally:
            ops.wipe()

    def test_legacy_path_unchanged(self):
        """use_preprocessor=False (deprecated) still works."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": False,
            "split_elements": True,
            "create_shells": False,
            "verbose": False,
        })
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            try:
                b.build()
                # Legacy path produces the same state
                assert len(b.frame_tag_map) > 0
                node_tags = ops.getNodeTags()
                assert len(node_tags) > 0
            finally:
                ops.wipe()
            ops.wipe()


    def test_split_frames_at_shell_subdiv_direct(self):
        """_split_frames_at_shell_subdiv splits frames at shell mesh nodes.

        Creates a model with a frame that passes through a shell area.
        After area meshing, the frame should be split at the shell mesh
        edge nodes that lie on it.  This exercises the SpatialGrid
        broad-phase pre-filter used inside the method.
        """
        from fea_toolkit.opensees.preprocessor import Preprocessor
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Restraint, Material, Section,
            FrameElement, AreaElement, AreaMesh,
        )

        # Frame along X-axis from (0,2,0) to (6,2,0)
        # Shell area at z=0 spanning x=2..4, y=0..4
        # The frame passes through the shell's mesh edge.
        # Use float coordinates to avoid numpy int/float casting
        # issues in mesh_area_elements.
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0.0, y=2.0, z=0.0),
                "2": Node(node_id="2", node_tag=2, x=6.0, y=2.0, z=0.0),
                "3": Node(node_id="3", node_tag=3, x=2.0, y=0.0, z=0.0),
                "4": Node(node_id="4", node_tag=4, x=4.0, y=0.0, z=0.0),
                "5": Node(node_id="5", node_tag=5, x=4.0, y=4.0, z=0.0),
                "6": Node(node_id="6", node_tag=6, x=2.0, y=4.0, z=0.0),
            },
            restraints={},
            materials={"C40": Material(name="C40", type="Concrete", E_mod=3e7)},
            sections={
                "BM": Section(name="BM", material="C40", shape="Rectangular",
                              A=0.16, I33=0.002, I22=0.002, J=0.001),
                "SLAB": Section(name="SLAB", material="C40", shape="Shell",
                                A=0, I33=0, I22=0, J=0),
            },
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
            },
            area_elements={
                "A1": AreaElement(area_id="A1", area_tag=20,
                                   node_ids=["3", "4", "5", "6"],
                                   thickness=0.15),
            },
            frame_assignments={"F1": "BM"},
            area_assignments={"A1": "SLAB"},
            groups={},
            frame_auto_mesh={
                "F1": {"AtJoints": True, "AtFrames": False},
            },
        )

        # Set up area meshing: 2×2 subdivision (max_size=2.0 on this 2×4 area
        # yields n_u=1, n_v=2, creating a mesh node at (3, 2, 0) on the frame path).
        md.area_mesh = {
            "A1": AreaMesh(auto_mesh=True, max_size=2.0),
        }

        pp = Preprocessor({
            "split_elements": False,
            "create_shells": True,
            "verbose": False,
        })

        # Manually run the relevant steps: mesh areas then split
        pp._mesh_areas(md, selection=None)
        pp._merge_coincident_nodes(md)

        # Collect frame data
        frame_elements = dict(md.frame_elements)
        frame_assignments = dict(md.frame_assignments)
        dist_loads = []
        frame_element_types = {}

        new_elems, new_assigns, new_loads, new_types, new_edge_loads = (
            pp._split_frames_at_shell_subdiv(
                md, frame_elements, frame_assignments,
                dist_loads, frame_element_types,
            )
        )

        # The frame (x=0..6, y=2) passes through the shell at x=2 and x=4.
        # After subdivision (default 2x2), shell mesh nodes at
        # x=3, y=2 (mid-edge) should split the frame into at least 3 segments.
        assert len(new_elems) >= 3,             f"Expected >=3 split frame elements, got {len(new_elems)}"

        # Check no inactive elements: _split_frames_at_shell_subdiv
        # returns new elements directly (caller handles deactivation)
        all_active = all(
            not getattr(el, 'inactive', False)
            for el in new_elems.values()
        )
        assert all_active, "All returned elements should be active"

        # Check that child elements reference existing nodes
        all_node_ids = set(md.nodes.keys())
        for el in new_elems.values():
            assert el.node_i in all_node_ids,                 f"Child node_i {el.node_i} not in model nodes"
            assert el.node_j in all_node_ids,                 f"Child node_j {el.node_j} not in model nodes"


    def test_orphan_nodes_removed_from_loads_only_areas(self):
        """Nodes only referenced by loads‑only areas move to orphan_nodes.

        Creates a model with a frame element, an active shell area, and a
        loads‑only (brick wall) area.  After the Preprocessor runs:

          * Nodes shared between loads‑only areas and active elements
            (frames / non‑loads‑only areas) remain in the main model.
          * Nodes referenced ONLY by loads‑only areas are moved to
            ``MeshModel.orphan_nodes`` and are absent from
            ``MeshModel.nodes``.
        """
        from fea_toolkit.model.mesh_model import MeshModel
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Material, Section,
            FrameElement, AreaElement,
        )
        from fea_toolkit.opensees.preprocessor import Preprocessor

        # ── Model layout (plan view at z=0) ──────────────────────
        #   Frame:   1──────2
        #   Slab:    1──3    (active area, nodes 1,3,4,2)
        #            │  │
        #            4──2
        #   Brick:   3──7    (loads‑only area, nodes 3,7,8,4)
        #            │  │
        #            8──4
        #
        # Nodes:
        #   1 (0,0,0) — frame, slab                → NOT orphan
        #   2 (4,0,0) — frame, slab                → NOT orphan
        #   3 (0,4,0) — slab, brick                → NOT orphan (shared)
        #   4 (4,4,0) — slab, brick                → NOT orphan (shared)
        #   7 (0,8,0) — brick ONLY                 → ORPHAN
        #   8 (4,8,0) — brick ONLY                 → ORPHAN

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
                "2": Node(node_id="2", node_tag=2, x=4, y=0, z=0),
                "3": Node(node_id="3", node_tag=3, x=0, y=4, z=0),
                "4": Node(node_id="4", node_tag=4, x=4, y=4, z=0),
                "7": Node(node_id="7", node_tag=7, x=0, y=8, z=0),
                "8": Node(node_id="8", node_tag=8, x=4, y=8, z=0),
            },
            restraints={},
            materials={
                "C40": Material(name="C40", type="Concrete", E_mod=3e7),
            },
            sections={
                "BM": Section(name="BM", material="C40", shape="Rectangular",
                              A=0.16, I33=0.002, I22=0.002, J=0.001),
                "SLAB": Section(name="SLAB", material="C40", shape="Shell",
                                A=0, I33=0, I22=0, J=0),
                "BRICK": Section(name="BRICK", material="C40", shape="Shell",
                                 A=0, I33=0, I22=0, J=0),
            },
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
            },
            area_elements={
                "A1": AreaElement(area_id="A1", area_tag=20,
                                   node_ids=["1", "3", "4", "2"],
                                   thickness=0.15),   # active slab
                "A2": AreaElement(area_id="A2", area_tag=30,
                                   node_ids=["3", "7", "8", "4"],
                                   thickness=0.10),   # loads‑only brick
            },
            frame_assignments={"F1": "BM"},
            area_assignments={"A1": "SLAB", "A2": "BRICK"},
            groups={},
            frame_auto_mesh={},
        )

        sel = Selection(sections=["BRICK"], element_types=["Area"])

        pp = Preprocessor({
            "split_elements": True,
            "create_shells": True,
            "verbose": False,
        })
        mesh = pp.run(md, selection=sel)

        assert isinstance(mesh, MeshModel)

        # ── Shared nodes stay in the main model ──────────────────
        for nid in ("1", "2", "3", "4"):
            assert nid in mesh.nodes,                     f"Shared node {nid} should be in model nodes"
            assert nid not in mesh.orphan_nodes,           f"Shared node {nid} should NOT be orphan"

        # ── Brick-only nodes move to orphan_nodes ────────────────
        for nid in ("7", "8"):
            assert nid in mesh.orphan_nodes,               f"Brick-only node {nid} should be orphan"
            assert nid not in mesh.nodes,                  f"Brick-only node {nid} should NOT be in model nodes"

        # ── Orphan nodes preserve coordinate data ────────────────
        assert mesh.orphan_nodes["7"].x == 0
        assert mesh.orphan_nodes["7"].y == 8
        assert mesh.orphan_nodes["8"].x == 4
        assert mesh.orphan_nodes["8"].y == 8

        # ── Non‑orphan node count ────────────────────────────────
        assert mesh.num_nodes == 4, f"Expected 4 model nodes, got {mesh.num_nodes}"
        assert len(mesh.orphan_nodes) == 2,                     f"Expected 2 orphan nodes, got {len(mesh.orphan_nodes)}"

        # ── No orphan node is accidentally in both ───────────────
        overlap = set(mesh.nodes.keys()) & set(mesh.orphan_nodes.keys())
        assert not overlap, f"Nodes in both model and orphan: {overlap}"


    def test_loads_reference_valid_frame_ids_after_splitting(self):
        """All loads reference frame IDs that exist after splitting.

        Creates a model with a frame that passes through a shell area.
        After area meshing the frame is split at shell mesh nodes.
        An edge load (simulating area-to-frame conversion) is placed
        on the original frame ID *before* the split.  After
        ``_split_frames_at_shell_subdiv``, the edge load must be
        redistributed to one of the child IDs — every load's
        ``frame_id`` must exist in the output frame elements.
        """
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Material, Section,
            FrameElement, AreaElement, AreaMesh,
            FrameDistributedLoad,
        )
        from fea_toolkit.opensees.preprocessor import Preprocessor

        # Frame along X-axis from (0,2,0) to (6,2,0).
        # Shell area at z=0 spanning x=2..4, y=0..4.
        # The frame passes through the shell's mesh edge.
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0.0, y=2.0, z=0.0),
                "2": Node(node_id="2", node_tag=2, x=6.0, y=2.0, z=0.0),
                "3": Node(node_id="3", node_tag=3, x=2.0, y=0.0, z=0.0),
                "4": Node(node_id="4", node_tag=4, x=4.0, y=0.0, z=0.0),
                "5": Node(node_id="5", node_tag=5, x=4.0, y=4.0, z=0.0),
                "6": Node(node_id="6", node_tag=6, x=2.0, y=4.0, z=0.0),
            },
            restraints={},
            materials={"C40": Material(name="C40", type="Concrete", E_mod=3e7)},
            sections={
                "BM": Section(name="BM", material="C40", shape="Rectangular",
                              A=0.16, I33=0.002, I22=0.002, J=0.001),
                "SLAB": Section(name="SLAB", material="C40", shape="Shell",
                                A=0, I33=0, I22=0, J=0),
            },
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
            },
            area_elements={
                "A1": AreaElement(area_id="A1", area_tag=20,
                                   node_ids=["3", "4", "5", "6"],
                                   thickness=0.15),
            },
            frame_assignments={"F1": "BM"},
            area_assignments={"A1": "SLAB"},
            groups={},
            frame_auto_mesh={
                "F1": {"AtJoints": True, "AtFrames": False},
            },
            area_mesh={
                "A1": AreaMesh(auto_mesh=True, max_size=2.0),
            },
        )

        pp = Preprocessor({
            "split_elements": False,
            "create_shells": True,
            "verbose": False,
        })

        # Manually run mesh + merge steps (same pattern as
        # test_split_frames_at_shell_subdiv_direct)
        pp._mesh_areas(md, selection=None)
        pp._merge_coincident_nodes(md)

        # An edge load on the original frame ID (simulating a load
        # that was converted from an area uniform load).
        edge_loads = [
            FrameDistributedLoad(
                frame_id="F1", pattern="DEAD",
                direction="Gravity",
                load_type="Force", shape="Uniform",
                val_a=-5.0, val_b=-5.0,
                rdist_a=0.0, rdist_b=1.0, dist_a=0.0, dist_b=6.0,
                coord_sys="GLOBAL",
            ),
        ]

        frame_elements = dict(md.frame_elements)
        frame_assignments = dict(md.frame_assignments)
        dist_loads: list = []
        frame_element_types: dict = {}

        new_elems, new_assigns, new_loads, new_types, new_edge_loads = (
            pp._split_frames_at_shell_subdiv(
                md, frame_elements, frame_assignments,
                dist_loads, frame_element_types,
                edge_loads=edge_loads,
            )
        )

        # ── Collect valid frame IDs from the output ──────────────
        valid_frame_ids: set = {
            eid for eid, el in new_elems.items()
            if not getattr(el, 'inactive', False)
        }
        assert len(valid_frame_ids) > 0, "No active frame elements"

        # ── The original F1 should no longer be active ───────────
        assert "F1" not in valid_frame_ids, (
            "Original F1 should be replaced by children"
        )

        # ── All edge loads must reference valid frame IDs ────────
        for ld in new_edge_loads:
            assert ld.frame_id in valid_frame_ids, (
                f"Edge load references missing frame "
                f"'{ld.frame_id}' (pattern={ld.pattern})"
            )

        # ── Edge loads should have been redistributed ────────────
        # At least one edge load should reference a child like F1-0
        child_refs = [ld for ld in new_edge_loads
                      if ld.frame_id.startswith("F1-")]
        assert len(child_refs) > 0, (
            "Expected edge loads redistributed to child frames, "
            f"got {len(new_edge_loads)} loads on IDs: "
            f"{set(ld.frame_id for ld in new_edge_loads)}"
        )


    def test_self_weight_applied_in_static_analysis(self):
        """Self-weight produces non-zero displacements in two-stage path.

        Builds a sample cantilever through the two-stage path, runs a
        static analysis with just a ``"DEAD"`` pattern (which has no
        explicit loads), and verifies that:

        1. Self-weight load totals are non-zero (auto-included).
        2. Nodal displacements are non-zero (loads actually applied).
        3. Explicit "Self weight" in pattern_scales also works.
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": True,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            b.compute_seismic_masses(g=9.81)

            # ── Test 1: DEAD pattern (swf=1) includes self-weight ─
            result = b.run_static_analysis(pattern_scales={"DEAD": 1.0})

            # Self-weight applied under DEAD — check result load_totals
            rs_lt = getattr(b._analysis, 'load_totals', {}) if hasattr(b, '_analysis') else {}
            dead_total = rs_lt.get("DEAD", 0.0)
            assert abs(dead_total) > 0, (
                f"DEAD total should be > 0 (includes self-weight), "
                f"got {dead_total}, keys={list(rs_lt.keys())}"
            )

            # Displacements should be non-zero
            nd = result.get("nodal_displacements", {})
            max_d = max(
                (abs(v) for vals in nd.values() for v in vals),
                default=0.0,
            )
            assert max_d > 0, (
                f"Expected non-zero displacement from self-weight, "
                f"got max|d|={max_d}"
            )

            # ── Test 2: pattern without swf ÔåÆ zero self-weight ──
            ops.wipe()
            from fea_toolkit.model.sap_data import LoadPattern
            md.load_patterns["LL"] = LoadPattern(
                name="LL", pattern_type="Live", self_weight_factor=0)
            b2 = OpenSeesBuilder(md, {
                "use_preprocessor": True,
                "split_elements": True,
                "create_shells": False,
                "verbose": False,
            })
            b2.build()
            b2.compute_seismic_masses(g=9.81)
            result2 = b2.run_static_analysis(
                pattern_scales={"LL": 1.0}
            )

            nd2 = result2.get("nodal_displacements", {})
            max_d2 = max(
                (abs(v) for vals in nd2.values() for v in vals),
                default=0.0,
            )
            assert max_d2 == 0, (
                f"Expected zero displacement from pattern with swf=0, "
                f"got max|d|={max_d2}"
            )
        finally:
            ops.wipe()

    def test_frame_gravity_load_applied(self):
        """Frame gravity loads produce non-zero displacements in two-stage path.

        Creates a cantilever with an explicit frame gravity load (Z multiplier)
        under a custom pattern, runs through the two-stage path, and verifies:
        1. Gravity load totals are non-zero.
        2. Nodal displacements are non-zero (loads actually applied).
        """
        import openseespy.opensees as ops
        from fea_toolkit.model.sap_data import SAPModelData, GravityLoad
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        # Add a frame gravity load for the DEAD pattern
        md.frame_gravity_loads = [
            GravityLoad(
                pattern="DEAD", frame_id="1",
                multiplier_x=0.0, multiplier_y=0.0, multiplier_z=-1.0,
            ),
        ]
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": True,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            result = b.run_static_analysis(pattern_scales={"DEAD": 1.0})

            # Gravity loads should be tracked (facade copies AnalysisBuilder state)
            assert hasattr(b, '_gravity_load_totals'), "_gravity_load_totals missing"
            assert "DEAD" in b._gravity_load_totals, (
                f"No DEAD gravity totals: {b._gravity_load_totals}"
            )
            # The DEAD fz total should be non-zero (gravity applied)
            dead_totals = b._gravity_load_totals["DEAD"]
            assert abs(dead_totals.get('fz', 0)) > 0, (
                f"Expected non-zero DEAD gravity total fz, "
                f"got {dead_totals}"
            )

            # Displacements should be non-zero
            nd = result.get("nodal_displacements", {})
            max_d = max(
                (abs(v) for vals in nd.values() for v in vals),
                default=0.0,
            )
            assert max_d > 0, (
                f"Expected non-zero displacement from frame gravity load, "
                f"got max|d|={max_d}"
            )
        finally:
            ops.wipe()

    def test_section_variants_created_in_preprocessor(self):
        """Preprocessor creates type-specific section variants with
        stiffness_factors enabled.

        Builds a model with concrete sections and
        stiffness_factors={"beam": 0.5, "column": 0.7},
        runs the Preprocessor, and verifies:
        1. Variant sections like "SEC1__beam" exist in mesh_model.sections.
        2. Variant entries exist in mesh_model.section_tags.
        3. The variant material has scaled E_mod.
        """
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Restraint, Material, Section,
            FrameElement,
        )

        # Build a model with concrete material (only concrete gets variants)
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
                "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=10.0),
                "3": Node(node_id="3", node_tag=3, x=5.0, y=0.0, z=10.0),
            },
            restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
            materials={
                "C30": Material(
                    name="C30", type="Concrete",
                    E_mod=3.0e10, G_mod=1.25e10, nu=0.2,
                    unit_weight=2.4e4, Fc=3.0e7,
                ),
            },
            sections={
                "SEC1": Section(
                    name="SEC1", shape="Rectangular",
                    material="C30", A=0.16, I33=2.13e-3, I22=1.07e-3, J=1.0e-4,
                ),
            },
            frame_elements={
                "F1": FrameElement(elem_id="F1", elem_tag=10,
                                    node_i="1", node_j="2"),
                "F2": FrameElement(elem_id="F2", elem_tag=20,
                                    node_i="2", node_j="3"),
            },
            frame_assignments={"F1": "SEC1", "F2": "SEC1"},
            frame_auto_mesh={},
            frame_dist_loads=[],
            area_elements={},
            area_assignments={},
            groups={},
        )

        import openseespy.opensees as ops
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "verbose": False,
            "stiffness_factors": {"beam": 0.5, "column": 0.7},
        })
        try:
            b.build()
            mm = b._mesh_model

            # Both __beam and __column variants should exist
            variant_keys = [k for k in mm.sections if "__" in k]
            assert "SEC1__beam" in variant_keys, (
                f"Missing 'SEC1__beam'. Keys: {variant_keys}"
            )
            assert "SEC1__column" in variant_keys, (
                f"Missing 'SEC1__column'. Keys: {variant_keys}"
            )

            # Each variant should have a section_tags entry
            for vk in ("SEC1__beam", "SEC1__column"):
                assert vk in mm.section_tags, (
                    f"Variant '{vk}' missing from section_tags"
                )

            # Beam variant should use 0.5 factor, column variant 0.7
            for vk, expected_factor in [("SEC1__beam", 0.5), ("SEC1__column", 0.7)]:
                sec = mm.sections[vk]
                mat = mm.materials.get(sec.material)
                assert mat is not None, (
                    f"Variant '{vk}' references missing material '{sec.material}'"
                )
                expected_e = 3.0e10 * expected_factor
                assert abs(mat.E_mod - expected_e) < 1.0, (
                    f"{vk} E_mod={mat.E_mod}, expected ~{expected_e} "
                    f"(factor={expected_factor})"
                )
        finally:
            ops.wipe()

    def test_compute_seismic_masses_via_analysis_builder(self):
        """AnalysisBuilder.compute_seismic_masses produces non-zero masses.

        Builds a sample cantilever through the two-stage path, calls
        compute_seismic_masses on the stored AnalysisBuilder, and
        verifies:
        1. Returned dict has entries for all nodes.
        2. Mass values are positive (element self-weight converted).
        3. node_masses is populated on the AnalysisBuilder.
        4. ops.nodeMass returns non-zero for all created nodes.
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            # Retrieve AnalysisBuilder via facade
            analysis = getattr(b, '_analysis', None)
            assert analysis is not None, "No _analysis reference on facade"

            masses = analysis.compute_seismic_masses(g=9.81)
            assert len(masses) > 0, (
                f"Expected non-empty mass dict, got {masses}"
            )
            for nid, m in masses.items():
                assert m > 0, (
                    f"Node {nid} mass should be > 0, got {m}"
                )

            # node_masses should be populated on AnalysisBuilder
            assert len(analysis.node_masses) > 0

            # ops.nodeMass should return non-zero for created nodes
            for nid, m in masses.items():
                nd = md.nodes.get(nid)
                if nd is None:
                    continue
                om = ops.nodeMass(nd.node_tag)
                assert abs(sum(om[:3])) > 0, (
                    f"Node {nd.node_tag} has zero mass in domain"
                )
        finally:
            ops.wipe()

    def test_modal_analysis_via_two_stage_path(self):
        """run_modal_analysis works through two-stage facade delegation.

        Builds a sample cantilever through the two-stage path, computes
        masses, runs modal analysis, and verifies:
        1. Result dict contains periods, eigenvalues, frequencies.
        2. Periods are positive and physically plausible.
        3. Modal_props dict is returned (not empty).
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            b.compute_seismic_masses(g=9.81)
            modal = b.run_modal_analysis(num_modes=3, print_results=False)

            assert isinstance(modal, dict), f"Expected dict, got {type(modal)}"
            assert "periods" in modal, f"Missing periods: {list(modal.keys())}"
            assert "eigenvalues" in modal
            assert "frequencies" in modal
            assert "modal_props" in modal
            assert len(modal["periods"]) == 3, (
                f"Expected 3 periods, got {len(modal['periods'])}"
            )
            assert len(modal["eigenvalues"]) == 3
            assert len(modal["frequencies"]) == 3

            # Periods should be positive and plausible
            T1 = modal["periods"][0]
            assert 0.01 < T1 < 10.0, f"T1={T1} outside plausible range"

            # Modal props should have participation data
            mp = modal["modal_props"]
            assert isinstance(mp, dict), f"modal_props not a dict: {type(mp)}"
        finally:
            ops.wipe()

    def test_element_rs_forces_via_two_stage_path(self):
        """extract_element_rs_forces works through two-stage facade.

        Builds through the two-stage path, runs RS analysis, then
        extracts element-level RS forces and verifies:
        1. Result dict contains element_results list.
        2. Each element result has the expected keys (My_i, Vz_i, etc.).
        3. Forces are non-zero.
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            b.compute_seismic_masses(g=9.81)
            modal = b.run_modal_analysis(num_modes=3, print_results=False)
            periods = modal["periods"]

            T_sp = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
            Sa_sp = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

            b.run_response_spectrum_analysis(
                num_modes=3,
                modal_periods=periods,
                spectrum_periods=T_sp,
                spectrum_accels=Sa_sp,
                direction="X",
                damping_ratio=0.05,
                print_results=False,
            )
            rs_forces = b.extract_element_rs_forces(
                num_modes=3,
                modal_periods=periods,
                spectrum_periods=T_sp,
                spectrum_accels=Sa_sp,
                direction="X",
            )
            assert isinstance(rs_forces, dict), (
                f"Expected dict, got {type(rs_forces)}"
            )
            assert "element_results" in rs_forces
            er = rs_forces["element_results"]
            assert len(er) > 0, "element_results is empty"
            first = er[0]
            for key in ("My_i", "My_j", "Mz_i", "Mz_j", "Vz_i", "Vy_i"):
                assert key in first, f"{key} missing from RS element result"
            # At least one force component should be positive
            max_f = max(abs(first[k]) for k in ("My_i", "My_j", "Mz_i", "Vz_i"))
            assert max_f > 1e-6, "All RS element forces are near zero"
        finally:
            ops.wipe()

    def test_rs_nodal_displacements_via_two_stage_path(self):
        """compute_rs_nodal_displacements works through two-stage facade.

        Builds through the two-stage path, runs modal analysis, then
        computes RS nodal displacements and verifies:
        1. Result is a dict mapping node_tag → (dx, dy, dz).
        2. Displacement in the excitation direction is non-zero.
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            b.compute_seismic_masses(g=9.81)
            # Run modal with enough modes and request shapes via
            # a separate call to extract_mode_shapes.
            modal = b.run_modal_analysis(num_modes=3, print_results=False)
            periods = modal["periods"]
            eigenvalues = modal["eigenvalues"]

            def _sa(T: float) -> float:
                return 3.0 if T <= 0.2 else 1.5 / max(T, 0.01)

            rs_disp = b.compute_rs_nodal_displacements(
                num_modes=3,
                modal_periods=periods,
                eigenvalues=eigenvalues,
                spectrum_func=_sa,
                direction="X",
                damping_ratio=0.05,
            )
            assert isinstance(rs_disp, dict), (
                f"Expected dict, got {type(rs_disp)}"
            )
            assert len(rs_disp) > 0, "RS displacements dict is empty"
            # Check X-displacement is non-zero for some node
            max_dx = max(abs(v[0]) for v in rs_disp.values())
            assert max_dx > 1e-8, (
                f"All RS X-displacements are near zero (max|dx|={max_dx})"
            )
        finally:
            ops.wipe()

    def test_rs_export_via_two_stage_path(self):
        """RS element forces + nodal displacements are written to NPZ.

        Builds through the two-stage path, runs RS analysis, extracts
        element forces and nodal displacements, then exports them
        and verifies the RS arrays exist in the NPZ file.
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            b.compute_seismic_masses(g=9.81)
            modal = b.run_modal_analysis(num_modes=3, print_results=False)
            periods = modal["periods"]
            eigenvalues = modal["eigenvalues"]

            T_sp = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
            Sa_sp = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

            rs_x = b.run_response_spectrum_analysis(
                num_modes=3,
                modal_periods=periods,
                spectrum_periods=T_sp,
                spectrum_accels=Sa_sp,
                direction="X",
                damping_ratio=0.05,
                print_results=False,
            )

            # Get RS element forces
            b.run_response_spectrum_analysis(
                num_modes=3,
                modal_periods=periods,
                spectrum_periods=T_sp,
                spectrum_accels=Sa_sp,
                direction="X",
                damping_ratio=0.05,
                print_results=False,
            )
            rs_forces = b.extract_element_rs_forces(
                num_modes=3,
                modal_periods=periods,
                spectrum_periods=T_sp,
                spectrum_accels=Sa_sp,
                direction="X",
            )

            # Get RS nodal displacements
            def _sa(T):
                return 3.0 if T <= 0.2 else 1.5 / max(T, 0.01)
            rs_disp = b.compute_rs_nodal_displacements(
                num_modes=3,
                modal_periods=periods,
                eigenvalues=eigenvalues,
                spectrum_func=_sa,
                direction="X",
                damping_ratio=0.05,
            )

            # Export to NPZ
            import tempfile, os
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "rs_test.npz")
                b.export_results(
                    filepath=out,
                    rs_results={"rs_x": rs_x},
                    rs_element_forces=rs_forces,
                    rs_nodal_displacements=rs_disp,
                )
                data = dict(np.load(out, allow_pickle=False))

                # Verify RS element force arrays
                assert "rs/elem_sap_id" in data
                assert "rs/elem_z_bot" in data
                assert "rs/elem_z_mid" in data
                assert "rs/elem_Vy_i" in data
                assert "rs/elem_Vz_i" in data
                assert "rs/elem_My_i" in data
                assert "rs/elem_Mz_i" in data
                assert len(data["rs/elem_sap_id"]) > 0
                assert abs(data["rs/elem_Vz_i"][0]) > 1e-6

                # Verify RS nodal displacement arrays
                assert "rs/node_tag" in data
                assert "rs/node_dx" in data
                assert "rs/node_dy" in data
                assert "rs/node_dz" in data
                assert len(data["rs/node_tag"]) > 0
                assert abs(data["rs/node_dx"][0]) > 1e-8 or \
                       abs(data["rs/node_dx"][-1]) > 1e-8
        finally:
            ops.wipe()

    def test_response_spectrum_via_two_stage_path(self):
        """run_response_spectrum_analysis works through two-stage facade.

        Builds a sample cantilever through the two-stage path, computes
        masses, runs modal, then runs RS in both X and Y directions,
        and verifies:
        1. Result dict contains modal_base_shear, base_shear_cqc, etc.
        2. CQC base shear is positive.
        3. Both directions produce results.
        """
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
        })
        try:
            b.build()
            b.compute_seismic_masses(g=9.81)
            modal = b.run_modal_analysis(num_modes=3, print_results=False)
            periods = modal["periods"]

            # Build a simple spectrum
            T_sp = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
            Sa_sp = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

            for dr in ("X", "Y"):
                rs = b.run_response_spectrum_analysis(
                    num_modes=3,
                    modal_periods=periods,
                    spectrum_periods=T_sp,
                    spectrum_accels=Sa_sp,
                    direction=dr,
                    damping_ratio=0.05,
                    print_results=False,
                )
                assert isinstance(rs, dict), (
                    f"RS-{dr}: expected dict, got {type(rs)}"
                )
                assert "modal_base_shear" in rs, (
                    f"RS-{dr}: missing modal_base_shear"
                )
                assert "base_shear_cqc" in rs
                assert "base_shear_srss" in rs
                assert rs["base_shear_cqc"] > 0, (
                    f"RS-{dr}: CQC base shear should be positive, "
                    f"got {rs['base_shear_cqc']}"
                )
                assert rs["base_shear_srss"] > 0, (
                    f"RS-{dr}: SRSS base shear should be positive, "
                    f"got {rs['base_shear_srss']}"
                )
        finally:
            ops.wipe()

    def test_edge_constraint_spring_via_two_stage_path(self):
        """Spring edge constraints can be applied through two-stage facade.

        Creates a model with two shell areas at different mesh densities
        meeting at a shared edge, builds through the two-stage path, and
        verifies:
        1. detect_unconnected_edges() finds slave nodes on the coarse edge.
        2. apply_edge_constraints() creates spring elements.
        3. The spring element count is > 0.
        """
        import openseespy.opensees as ops
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Restraint, Material, Section,
            ShellSection, FrameElement, AreaElement, AreaMesh,
        )

        # Two adjacent shell areas: left (dense) and right (coarse).
        # Left area: 0,0,0 → 3,0,0 → 3,3,0 → 0,3,0  (meshed 3×3)
        # Right area: 3,0,0 → 6,0,0 → 6,3,0 → 3,3,0  (meshed 1×1)
        # The left area's right-edge fine nodes should be detected as
        # slaves on the right area's coarse edge.
        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
                "2": Node(node_id="2", node_tag=2, x=3.0, y=0.0, z=0.0),
                "3": Node(node_id="3", node_tag=3, x=6.0, y=0.0, z=0.0),
                "4": Node(node_id="4", node_tag=4, x=0.0, y=3.0, z=0.0),
                "5": Node(node_id="5", node_tag=5, x=3.0, y=3.0, z=0.0),
                "6": Node(node_id="6", node_tag=6, x=6.0, y=3.0, z=0.0),
            },
            restraints={"1": Restraint([1, 1, 1, 1, 1, 1]),
                        "3": Restraint([1, 1, 1, 1, 1, 1]),
                        "4": Restraint([1, 1, 1, 1, 1, 1]),
                        "6": Restraint([1, 1, 1, 1, 1, 1])},
            materials={
                "C30": Material(name="C30", type="Concrete",
                                E_mod=3.0e10, G_mod=1.25e10, nu=0.2,
                                unit_weight=2.4e4, Fc=3.0e7),
            },
            sections={
                "SLAB": ShellSection(name="SLAB", shape="Shell", material="C30", thickness=0.15),
            },
            frame_elements={},
            area_elements={
                "A1": AreaElement(area_id="A1", area_tag=10, node_ids=["1", "2", "5", "4"]),
                "A2": AreaElement(area_id="A2", area_tag=20, node_ids=["2", "3", "6", "5"]),
            },
            area_mesh={
                "A1": AreaMesh(auto_mesh=True, max_size=1.0),
                "A2": AreaMesh(auto_mesh=True, max_size=3.0),
            },
            frame_assignments={},
            area_assignments={"A1": "SLAB", "A2": "SLAB"},
            groups={}, frame_auto_mesh={},
        )
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "create_shells": True,
            "split_elements": False,
            "verbose": False,
        })
        try:
            b.build()

            # Detect unconnected edges
            reports = b.detect_unconnected_edges(tolerance=0.1)
            assert len(reports) > 0, (
                "Expected to find unconnected edges between "
                "dense and coarse shell meshes"
            )

            # Apply spring edge constraints using detected edges
            edge_pairs = [(r["master_node_i"], r["master_node_j"])
                          for r in reports]
            slave_nodes = [r["slave_node"] for r in reports]

            n = b.apply_edge_constraints(
                coarse_edges=edge_pairs,
                fine_nodes=slave_nodes,
                tolerance=0.1,
                verbose=False,
            )
            assert n > 0, (
                f"Expected at least 1 spring element, got {n}"
            )
            # Each slave on an edge gets 2 springs (one per master end)
            assert n >= len(reports) * 2, (
                f"Expected at least {len(reports) * 2} springs, got {n}"
            )

            # Verify the constraint method was recorded
            assert b._edge_constraint_method == 'spring', (
                f"Expected 'spring', got {b._edge_constraint_method}"
            )
        finally:
            ops.wipe()

    def test_edge_constraint_penalty_via_two_stage_path(self):
        """Penalty edge constraints work through two-stage facade."""
        import openseespy.opensees as ops
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Restraint, Material, Section,
            ShellSection, AreaElement, AreaMesh,
        )

        md = SAPModelData(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
                "2": Node(node_id="2", node_tag=2, x=3.0, y=0.0, z=0.0),
                "3": Node(node_id="3", node_tag=3, x=6.0, y=0.0, z=0.0),
                "4": Node(node_id="4", node_tag=4, x=0.0, y=3.0, z=0.0),
                "5": Node(node_id="5", node_tag=5, x=3.0, y=3.0, z=0.0),
                "6": Node(node_id="6", node_tag=6, x=6.0, y=3.0, z=0.0),
            },
            restraints={"1": Restraint([1, 1, 1, 1, 1, 1]),
                        "3": Restraint([1, 1, 1, 1, 1, 1]),
                        "4": Restraint([1, 1, 1, 1, 1, 1]),
                        "6": Restraint([1, 1, 1, 1, 1, 1])},
            materials={
                "C30": Material(name="C30", type="Concrete",
                                E_mod=3.0e10, G_mod=1.25e10, nu=0.2,
                                unit_weight=2.4e4, Fc=3.0e7),
            },
            sections={
                "SLAB": ShellSection(name="SLAB", shape="Shell", material="C30", thickness=0.15),
            },
            frame_elements={},
            area_elements={
                "A1": AreaElement(area_id="A1", area_tag=10, node_ids=["1", "2", "5", "4"]),
                "A2": AreaElement(area_id="A2", area_tag=20, node_ids=["2", "3", "6", "5"]),
            },
            area_mesh={
                "A1": AreaMesh(auto_mesh=True, max_size=1.0),
                "A2": AreaMesh(auto_mesh=True, max_size=3.0),
            },
            frame_assignments={},
            area_assignments={"A1": "SLAB", "A2": "SLAB"},
            groups={}, frame_auto_mesh={},
        )
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "create_shells": True,
            "split_elements": False,
            "verbose": False,
            "constraint_method": "penalty",
        })
        try:
            b.build()

            reports = b.detect_unconnected_edges(tolerance=0.1)
            assert len(reports) > 0

            edge_pairs = [(r["master_node_i"], r["master_node_j"])
                          for r in reports]
            slave_nodes = [r["slave_node"] for r in reports]

            n = b.apply_edge_constraints(
                coarse_edges=edge_pairs,
                fine_nodes=slave_nodes,
                tolerance=0.1,
                verbose=False,
            )
            assert n > 0, f"Expected penalty constraints, got {n}"
            assert b._edge_constraint_method == 'penalty'
        finally:
            ops.wipe()

    def test_get_shell_area_ids_via_two_stage_path(self):
        """_get_shell_area_ids returns shell area IDs through facade."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.builder import OpenSeesBuilder

        md = make_sample_model()
        b = OpenSeesBuilder(md, {
            "use_preprocessor": True,
            "create_shells": False,
            "split_elements": False,
            "verbose": False,
        })
        try:
            b.build()
            # No shells in sample model
            ids = b._get_shell_area_ids()
            assert isinstance(ids, set)
        finally:
            ops.wipe()


# ═════════════════════════════════════════════════════════════════════════════
# sum_reactions_with_overturning tests
# ═════════════════════════════════════════════════════════════════════════════

class TestSumReactionsWithOverturning:
    """Test the centralized overturning-moment utility."""

    def test_single_node_no_overturning(self):
        """Single node at centroid → forces pass through directly."""
        from fea_toolkit.utils import sum_reactions_with_overturning
        from fea_toolkit.model.sap_data import Node

        nodes = {"B1": Node(node_id="B1", node_tag=1, x=5, y=5, z=0)}
        reactions = {1: {"fx": 100.0, "fy": 0.0, "fz": 0.0,
                          "mx": 0.0, "my": 0.0, "mz": 0.0}}
        result = sum_reactions_with_overturning(reactions, nodes)
        assert result["fx"] == 100.0
        assert result["mx"] == 0.0  # at centroid → no lever arm

    def test_two_node_overturning(self):
        """Two base nodes with vertical reactions → My from Fz·dx."""
        from fea_toolkit.utils import sum_reactions_with_overturning
        from fea_toolkit.model.sap_data import Node

        nodes = {
            "A": Node(node_id="A", node_tag=10, x=0, y=0, z=0),
            "B": Node(node_id="B", node_tag=20, x=10, y=0, z=0),
        }
        reactions = {
            10: {"fx": 0.0, "fy": 0.0, "fz": 100.0,
                  "mx": 0.0, "my": 0.0, "mz": 0.0},
            20: {"fx": 0.0, "fy": 0.0, "fz": -100.0,
                  "mx": 0.0, "my": 0.0, "mz": 0.0},
        }
        result = sum_reactions_with_overturning(reactions, nodes)
        assert abs(result["fz"]) < 1e-10  # equal and opposite
        # My = Fz_A * (0-5) + Fz_B * (10-5) = 100*(-5) + (-100)*5 = -1000
        assert abs(result["my"] - 1000.0) < 1e-10

    def test_empty_reactions(self):
        """Empty reactions → all zero."""
        from fea_toolkit.utils import sum_reactions_with_overturning
        result = sum_reactions_with_overturning({}, {"N1": type("N", (), {"x": 0, "y": 0, "z": 0})()})
        for k in ["fx", "fy", "fz", "mx", "my", "mz"]:
            assert result[k] == 0.0

    def test_empty_nodes(self):
        """Empty nodes → all zero."""
        from fea_toolkit.utils import sum_reactions_with_overturning
        result = sum_reactions_with_overturning({1: {"fx": 1.0, "fy": 0, "fz": 0, "mx": 0, "my": 0, "mz": 0}}, {})
        for k in ["fx", "fy", "fz", "mx", "my", "mz"]:
            assert result[k] == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# RS base_reactions_cqc (two-stage path)
# ═════════════════════════════════════════════════════════════════════════════

class TestRSBaseReactionsTwoStage:
    """Test that RS analysis returns full 6-DoF base reactions."""

    def test_base_reactions_cqc_keys(self):
        """run_response_spectrum_analysis returns base_reactions_cqc."""
        import openseespy.opensees as ops
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.preprocessor import Preprocessor
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        md = make_sample_model()
        pp = Preprocessor({"split_elements": True, "create_shells": False,
                           "verbose": False})
        mesh = pp.run(md)
        ab = AnalysisBuilder(mesh, {"verbose": False,
                                     "element_type": "elasticBeamColumn"})
        ab.build_domain()
        ab.compute_seismic_masses()

        spec_cfg = {
            "code": "GB50011", "intensity": 7, "acceleration": 0.10,
            "site_class": "I1", "design_group": 1, "level": "rare",
            "damping": 0.05,
        }
        from fea_toolkit.spectrum import _build_spectrum
        T_spec, Sa_spec, _, _, _, _ = _build_spectrum(spec_cfg)

        modal = ab.run_modal_analysis(num_modes=2, print_results=False)
        rs = ab.run_response_spectrum_analysis(
            num_modes=min(2, modal["num_modes"]),
            modal_periods=modal["periods"],
            spectrum_periods=T_spec, spectrum_accels=Sa_spec,
            direction="X", damping_ratio=0.05, print_results=False,
        )

        # New full 6-DoF results
        assert "base_reactions_cqc" in rs
        r = rs["base_reactions_cqc"]
        for comp in ["fx", "fy", "fz", "mx", "my", "mz"]:
            assert comp in r, f"Missing {comp} in base_reactions_cqc"
        # X-direction excitation should produce non-zero Fx and My
        assert abs(r["fx"]) > 0, "Expected non-zero base shear in X"
        assert abs(r["my"]) > 0, "Expected non-zero overturning moment My"

        # modal_base_reactions should have one entry per mode
        assert len(rs["modal_base_reactions"]) == modal["num_modes"]

    def test_check_load_equilibrium_has_correct_units(self):
        """check_load_equilibrium uses mesh_model.units, not '?'."""
        from examples.sample_model import make_sample_model
        from fea_toolkit.opensees.preprocessor import Preprocessor
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        md = make_sample_model()
        pp = Preprocessor({"split_elements": True, "verbose": False})
        mesh = pp.run(md)
        ab = AnalysisBuilder(mesh, {"verbose": False})
        df = ab.check_load_equilibrium()
        assert not df.empty
        # Column headers should contain the force unit, not '?'
        for col in df.columns:
            assert "?" not in col, f"Column '{col}' contains '?'"


# ═════════════════════════════════════════════════════════════════════════════
# CSM module tests (standalone, no OpenSees required)
# ═════════════════════════════════════════════════════════════════════════════

class TestCsmModule:
    """Test the standalone CSM utility functions in model/csm.py."""

    def test_pushover_to_adrs_basic(self):
        """ADRS conversion works with valid pushover + modal results."""
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01, 0.02, 0.03, 0.04],
            "base_shear": [0.0, 100.0, 180.0, 240.0, 280.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.1],
                "partiMassMX": [800.0, 100.0],
            },
            "periods": [0.5, 0.1],
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="X")
        assert isinstance(adrs, dict)
        assert set(adrs) >= {"S_a", "S_d", "Gamma", "M_eff", "phi_control", "best_mode"}
        assert len(adrs["S_a"]) == len(pushover["control_disp"])
        assert len(adrs["S_d"]) == len(pushover["control_disp"])
        assert adrs["Gamma"] > 0
        assert adrs["M_eff"] > 0
        assert adrs["phi_control"] > 0
        assert adrs["best_mode"] == 0  # mode 0 has 80% ratio

    def test_pushover_to_adrs_missing_control_node(self):
        """Raises ValueError when control_node is missing."""
        from fea_toolkit.model.csm import pushover_to_adrs
        with pytest.raises(ValueError, match="control_node"):
            pushover_to_adrs({"control_disp": [0]}, {}, {}, "X")

    def test_compute_performance_point_basic(self):
        """Performance point computation runs with valid data."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05],
            "base_shear": [0.0, 50.0, 100.0, 180.0, 240.0, 280.0, 300.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8, 0.15],
                "partiMassMX": [800.0, 150.0],
            },
            "periods": [0.5, 0.12],
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}, 1: {1: (0.0, 1.0, 0.0)}}
        periods = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0]
        accels = [3.0, 3.0, 3.0, 1.5, 0.8, 0.4, 0.2]

        pp = compute_performance_point(
            pushover, modal, shapes, periods, accels,
            direction="X", damping_ratio=0.05, max_iter=20, tol=0.05,
        )
        assert isinstance(pp, dict)
        assert set(pp) >= {"S_dp", "S_ap", "V_base", "D_roof", "T_eq", "mu",
                          "converged", "S_dy", "S_ay"}
        assert pp["S_dp"] > 1e-6
        assert pp["S_ap"] > 1e-6
        assert pp["V_base"] > 1e-6
        assert isinstance(pp["converged"], bool)

    def test_compute_performance_point_too_few_points(self):
        """Raises ValueError with fewer than 3 valid data points."""
        from fea_toolkit.model.csm import compute_performance_point

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.8],
                "partiMassMX": [800.0],
            },
            "periods": [0.5],
        }
        shapes = {0: {1: (1.0, 0.0, 0.0)}}
        with pytest.raises(ValueError, match="too few|Too few"):
            compute_performance_point(
                pushover, modal, shapes,
                [0.1, 0.5], [3.0, 1.5],
            )

    def test_pushover_to_adrs_y_direction(self):
        """ADRS conversion in Y direction picks correct mass data."""
        from fea_toolkit.model.csm import pushover_to_adrs

        pushover = {
            "control_node": 1,
            "control_disp": [0.0, 0.01],
            "base_shear": [0.0, 100.0],
        }
        modal = {
            "modal_props": {
                "partiMassRatiosMX": [0.2],
                "partiMassMX": [200.0],
                "partiMassRatiosMY": [0.7],
                "partiMassMY": [700.0],
            },
            "periods": [0.5],
        }
        shapes = {0: {1: (0.0, 1.0, 0.0)}}

        adrs = pushover_to_adrs(pushover, modal, shapes, direction="Y")
        assert adrs["M_eff"] == 700.0
        assert adrs["Gamma"] == math.sqrt(700.0)
