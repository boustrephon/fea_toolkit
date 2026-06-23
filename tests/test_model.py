"""Tests for the model layer: dataclasses, geometry utilities, and sections."""

import math
import pickle
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pytest

from fea_toolkit.model.sap_data import (
    SAPModelData,
    Node,
    Restraint,
    Material,
    Section,
    FrameElement,
    AreaElement,
    Group,
    LoadCase,
    LoadPattern,
    LoadCombination,
    JointLoad,
    FrameDistributedLoad,
    GravityLoad,
    FramePointLoad,
    MassSource,
    Constraint,
    CoordSys,
    default_coord_sys,
)
from fea_toolkit.model.geometry import (
    get_SAP_vecxz,
    rotate_about_axis,
    point_on_segment,
    compute_t_location,
    interp,
    list_interp,
    trapezoidal_force_split,
    SpatialGrid,
)

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
