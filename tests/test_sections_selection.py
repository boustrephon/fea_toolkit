"""Tests for sections, SectionLibrary and Selection filtering."""

"""Tests for the model layer: dataclasses, geometry utilities, and sections."""

from pathlib import Path

import pytest

from fea_toolkit.model.geometry import (
    beam_load_to_nodal_loads,
)
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    AngleSection,
    AreaElement,
    AreaGravityLoad,
    AreaUniformLoad,
    BoxSection,
    ChannelSection,
    CircularSection,
    DoubleAngleSection,
    FrameDistributedLoad,
    FrameElement,
    GravityLoad,
    Group,
    JointLoad,
    LoadPattern,
    MassSource,
    Material,
    Node,
    PipeSection,
    Restraint,
    SAPModelData,
    Section,
    ShellSection,
    TeeSection,
)
from fea_toolkit.model.selection import Selection
from fea_toolkit.model.stories import StoryLevel

# ============================================================================
# Fixtures
# ============================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ============================================================================
# Dataclass construction tests
# ============================================================================


# ═══════════════════════════════════════════════════════════════════
# Sections and selection
# ═══════════════════════════════════════════════════════════════════


class TestBeamLoadToNodalLoads:
    """Tests for beam_load_to_nodal_loads()."""

    def test_uniform_gravity(self):
        """Uniform gravity load on a horizontal X element."""
        load = FrameDistributedLoad(
            pattern="DEAD",
            frame_id="1",
            direction="Gravity",
            load_type="Force",
            shape="Uniform",
            val_a=10000.0,
            val_b=10000.0,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=5.0,
        )
        elem = FrameElement(
            elem_id="1",
            elem_tag=1,
            node_i="1",
            node_j="2",
            angle=0.0,
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
            pattern="WIND",
            frame_id="1",
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
        elem = FrameElement(
            elem_id="1",
            elem_tag=1,
            node_i="1",
            node_j="2",
            angle=0.0,
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
            pattern="DEAD",
            frame_id="1",
            direction="Gravity",
            load_type="Force",
            shape="Uniform",
            val_a=10000.0,
            val_b=10000.0,
            rdist_a=0.2,
            rdist_b=0.8,
            dist_a=1.0,
            dist_b=4.0,
        )
        elem = FrameElement(
            elem_id="1",
            elem_tag=1,
            node_i="1",
            node_j="2",
            angle=0.0,
        )
        node_coords = {"1": (0.0, 0.0, 0.0), "2": (5.0, 0.0, 0.0)}
        result = beam_load_to_nodal_loads(load, elem, node_coords, length=5.0)

        # Total load = 10000 * (4-1) = 30000 on a 5m element
        total_fz = abs(result["i"]["fz"]) + abs(result["j"]["fz"])
        assert abs(total_fz - 30000.0) < 1.0

    def test_trapezoidal_load(self):
        """Trapezoidal load varying from 5000 to 10000."""
        load = FrameDistributedLoad(
            pattern="DEAD",
            frame_id="1",
            direction="Gravity",
            load_type="Force",
            shape="Trapezoidal",
            val_a=5000.0,
            val_b=10000.0,
            rdist_a=0.0,
            rdist_b=1.0,
            dist_a=0.0,
            dist_b=5.0,
        )
        elem = FrameElement(
            elem_id="1",
            elem_tag=1,
            node_i="1",
            node_j="2",
            angle=0.0,
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


class TestMassSource2:
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
        ptype, mat, ncirc, nrad, _yc, _zc, r_in, r_out, sa, ea = patches[0]
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
        ptype, mat, ncirc, nrad, _yc, _zc, r_in, r_out, _sa, _ea = patches[0]
        assert ptype == "circ"
        assert mat == 3
        assert ncirc == 12 and nrad == 6
        assert abs(r_in) < 1e-12
        assert abs(r_out - 0.3) < 1e-12


class TestBoxSectionFiberPatches:
    def test_four_rect_patches(self):
        b = BoxSection("BOX", "Box/Tube", "STEEL", depth=0.6, bf=0.4, tf=0.02, tw=0.015)
        patches = b.to_fiber_patches(mat_tag=4, nfy=3, nfz=2)
        assert len(patches) == 4
        for p in patches:
            assert p[0] == "rect"
            assert p[1] == 4
        # Top flange: y from 0.28 to 0.3, z from -0.2 to 0.2
        assert abs(patches[0][4] - 0.28) < 1e-12  # yI
        assert abs(patches[0][6] - 0.3) < 1e-12  # yJ
        # Bottom flange: y from -0.3 to -0.28
        assert abs(patches[1][4] + 0.3) < 1e-12
        assert abs(patches[1][6] + 0.28) < 1e-12


def _assert_rect_patch_geometry(patches, expected_area, mat_tag, nfy, nfz):
    """Shared assertions for centroid-shifted rectangular fiber patches."""
    total_area = 0.0
    first_y = 0.0
    first_z = 0.0
    for p in patches:
        assert p[0] == "rect"
        assert p[1] == mat_tag
        assert p[2] == nfy
        assert p[3] == nfz
        y1, z1, y2, z2 = p[4], p[5], p[6], p[7]
        area = abs(y2 - y1) * abs(z2 - z1)
        total_area += area
        first_y += area * (y1 + y2) / 2.0
        first_z += area * (z1 + z2) / 2.0
    assert total_area == pytest.approx(expected_area, rel=1e-9)
    assert first_y == pytest.approx(0.0, abs=1e-12)
    assert first_z == pytest.approx(0.0, abs=1e-12)


class TestChannelSectionFiberPatches:
    """Centroid-shifted 3-rect fiber patches for ChannelSection."""

    def test_three_rect_patches_centered_on_centroid(self):

        sec = ChannelSection(
            name="CH",
            shape="Channel",
            material="STEEL",
            A=0.00264,
            depth=0.2,
            bf=0.06,
            tf=0.01,
            tw=0.008,
        )
        patches = sec.to_fiber_patches(mat_tag=5, nfy=3, nfz=2)
        assert len(patches) == 3
        _assert_rect_patch_geometry(patches, expected_area=0.00264, mat_tag=5, nfy=3, nfz=2)
        # Bottom flange -> web -> top flange in y (channel is symmetric about y).
        assert patches[0][6] == pytest.approx(-0.09)  # web y1 (bottom flange y2)
        assert patches[1][4] == pytest.approx(-0.09)  # web y1
        assert patches[1][6] == pytest.approx(0.09)  # web y2
        assert patches[2][4] == pytest.approx(0.09)  # top flange y1
        assert (patches[1][7] - patches[1][5]) == pytest.approx(0.008)  # web z-width = tw
        assert (patches[2][7] - patches[2][5]) == pytest.approx(0.06)  # flange z-width = B


class TestTeeSectionFiberPatches:
    """Centroid-shifted 2-rect fiber patches for TeeSection."""

    def test_two_rect_patches_centered_on_centroid(self):

        sec = TeeSection(
            name="T",
            shape="Tee",
            material="STEEL",
            A=0.00308,
            depth=0.2,
            bf=0.1,
            tf=0.012,
            tw=0.01,
        )
        patches = sec.to_fiber_patches(mat_tag=6, nfy=3, nfz=2)
        assert len(patches) == 2
        _assert_rect_patch_geometry(patches, expected_area=0.00308, mat_tag=6, nfy=3, nfz=2)
        # Flange on top (positive y after centroid shift), stem below.
        assert patches[0][4] >= patches[1][6] - 1e-12  # flange y1 at/above stem y2
        assert (patches[0][7] - patches[0][5]) == pytest.approx(0.1)  # flange z-width = B
        assert (patches[1][7] - patches[1][5]) == pytest.approx(0.01)  # stem z-width = tw


class TestAngleSectionFiberPatches:
    """Centroid-shifted 2-rect fiber patches for AngleSection."""

    def test_two_rect_patches_centered_on_centroid(self):

        sec = AngleSection(
            name="L",
            shape="Angle",
            material="STEEL",
            A=0.0019,
            depth=0.1,
            bf=0.1,
            tf=0.01,
            tw=0.01,
        )
        patches = sec.to_fiber_patches(mat_tag=7, nfy=3, nfz=2)
        assert len(patches) == 2
        _assert_rect_patch_geometry(patches, expected_area=0.0019, mat_tag=7, nfy=3, nfz=2)
        # Vertical leg is tw thick in z; horizontal leg is tf thick in y.
        assert (patches[0][7] - patches[0][5]) == pytest.approx(0.01)  # vertical leg z-width = tw
        assert (patches[1][6] - patches[1][4]) == pytest.approx(0.01)  # horizontal leg y-width = tf


class TestDoubleAngleSectionFiberPatches:
    """Centroid-shifted 4-rect fiber patches for DoubleAngleSection."""

    def test_four_rect_patches_centered_on_centroid(self):

        sec = DoubleAngleSection(
            name="2L",
            shape="Double Angle",
            material="STEEL",
            A=0.004,
            depth=0.1,
            bf=0.23,
            tf=0.01,
            tw=0.01,
            dis=0.01,
        )
        patches = sec.to_fiber_patches(mat_tag=8, nfy=3, nfz=2)
        assert len(patches) == 4
        _assert_rect_patch_geometry(patches, expected_area=0.004, mat_tag=8, nfy=3, nfz=2)
        # z-symmetric assembly: left and right legs mirror about z = 0.
        assert patches[1][5] == pytest.approx(-0.115)  # left horizontal leg z1 (tip)
        assert patches[3][7] == pytest.approx(0.115)  # right horizontal leg z2 (tip)
        assert patches[1][7] == pytest.approx(-0.015)  # left horizontal leg z2
        assert patches[3][5] == pytest.approx(0.015)  # right horizontal leg z1


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
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000),
            "Concrete": Material(name="Concrete", type="Concrete", E_mod=3e10, unit_weight=24000),
        }
        sections = {
            "UB100": Section(
                name="UB100",
                shape="I/Wide Flange",
                material="Steel",
                A=0.01,
                I33=1e-4,
                I22=1e-5,
                J=1e-6,
            ),
            "Slab200": ShellSection(
                name="Slab200",
                shape="Shell",
                material="Concrete",
                A=0,
                I33=0,
                I22=0,
                J=0,
                thickness=0.2,
            ),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="3"),
        }
        areas = {
            "1": AreaElement(area_id="1", area_tag=1, node_ids=["1", "2", "3", "4"], thickness=0.2),
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
            AreaUniformLoad(pattern="DEAD", area_id="1", direction="Gravity", value=5000),
        ]
        area_gravity = [
            AreaGravityLoad(pattern="DEAD", area_id="1", multiplier_z=-1.0),
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
# Selection → MeshModel resolution tests
# ============================================================================


class TestSelectionMeshModel:
    """Tests for :meth:`Selection.resolve_to_mesh_sets` — resolving a
    Selection against a MeshModel with elevation_range and story filters."""

    @pytest.fixture
    def mesh_model(self):
        """Minimal MeshModel with frames, areas, nodes, sections, groups.

        Nodes:
          1: (0, 0, 0)   2: (6, 0, 0)    3: (6, 0, 3)    4: (0, 0, 3)
          5: (0, 0, 6)   6: (6, 0, 6)    7: (0, 0, 9)    8: (6, 0, 9)

        Frames:
          Frame "1": nodes 1→2  at z=0          (mid-height Z = 0)
          Frame "2": nodes 3→4  at z=3          (mid-height Z = 3)
          Frame "3": nodes 5→6  at z=6          (mid-height Z = 6)
          Frame "4": nodes 7→8  at z=9          (mid-height Z = 9)

        Areas:
          Area "1": nodes 1-2-3-4 at z=0…3    (centroid Z ≈ 1.5)
        """
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0, y=0, z=0),
            "2": Node(node_id="2", node_tag=2, x=6, y=0, z=0),
            "3": Node(node_id="3", node_tag=3, x=6, y=0, z=3),
            "4": Node(node_id="4", node_tag=4, x=0, y=0, z=3),
            "5": Node(node_id="5", node_tag=5, x=0, y=0, z=6),
            "6": Node(node_id="6", node_tag=6, x=6, y=0, z=6),
            "7": Node(node_id="7", node_tag=7, x=0, y=0, z=9),
            "8": Node(node_id="8", node_tag=8, x=6, y=0, z=9),
        }
        materials = {
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000),
            "Concrete": Material(name="Concrete", type="Concrete", E_mod=3e10, unit_weight=24000),
        }
        sections = {
            "UB100": Section(
                name="UB100",
                shape="I/Wide Flange",
                material="Steel",
                A=0.01,
                I33=1e-4,
                I22=1e-5,
                J=1e-6,
            ),
            "Slab": ShellSection(
                name="Slab",
                shape="Shell",
                material="Concrete",
                A=0,
                I33=0,
                I22=0,
                J=0,
                thickness=0.2,
            ),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="3", node_j="4"),
            "3": FrameElement(elem_id="3", elem_tag=3, node_i="5", node_j="6"),
            "4": FrameElement(elem_id="4", elem_tag=4, node_i="7", node_j="8"),
        }
        areas = {
            "1": AreaElement(
                area_id="1", area_tag=10, node_ids=["1", "2", "3", "4"], thickness=0.2
            ),
        }
        groups = {
            "Cols": Group(name="Cols", objects=["Frame:1", "Frame:2", "Frame:3", "Frame:4"]),
        }
        return MeshModel(
            nodes=nodes,
            frame_elements=frames,
            frame_assignments={"1": "UB100", "2": "UB100", "3": "UB100", "4": "UB100"},
            area_elements=areas,
            area_assignments={"1": "Slab"},
            frame_dist_loads=[],
            materials=materials,
            sections=sections,
            groups=groups,
        )

    # ── elevation_range ──

    def test_elevation_range_filters_frames(self, mesh_model):
        """elevation_range=(0, 3) returns frames at Z=0 and Z=3 only."""
        sel = Selection(element_types=["Frame"], elevation_range=(0.0, 3.0))
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == {"1", "2"}
        assert area_ids == set()

    def test_elevation_range_middle_storey(self, mesh_model):
        """elevation_range=(3, 6) returns frame at Z=3 and Z=6 (mid-heights
        3.0 and 6.0 both satisfy 3 ≤ z ≤ 6)."""
        sel = Selection(element_types=["Frame"], elevation_range=(3.0, 6.0))
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == {"2", "3"}
        assert area_ids == set()

    def test_elevation_range_upper_storey(self, mesh_model):
        """elevation_range=(9, 12) returns only frame at Z=9."""
        sel = Selection(element_types=["Frame"], elevation_range=(9.0, 12.0))
        frame_ids, _ = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == {"4"}

    def test_elevation_range_no_match(self, mesh_model):
        """elevation_range=(100, 200) returns empty."""
        sel = Selection(element_types=["Frame"], elevation_range=(100.0, 200.0))
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == set()
        assert area_ids == set()

    def test_elevation_range_areas(self, mesh_model):
        """Area with centroid Z≈1.5 matches elevation_range=(0, 2)."""
        sel = Selection(element_types=["Area"], elevation_range=(0.0, 2.0))
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == set()
        assert area_ids == {"1"}

    def test_elevation_range_areas_outside(self, mesh_model):
        """Area with centroid Z≈1.5 does NOT match elevation_range=(5, 10)."""
        sel = Selection(element_types=["Area"], elevation_range=(5.0, 10.0))
        _, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert area_ids == set()

    def test_elevation_range_no_element_type_filter(self, mesh_model):
        """No element_types filter → matches both frames and areas."""
        sel = Selection(elevation_range=(0.0, 3.0))
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == {"1", "2"}
        assert area_ids == {"1"}

    # ── story filter ──

    def test_story_filter_basic(self, mesh_model):
        """story=['Storey 1'] at elevation 0 → matches frames at Z=0."""
        stories = [
            StoryLevel(name="Storey 1", elevation=0.0, method="manual", confidence="high"),
        ]
        sel = Selection(element_types=["Frame"], story=["Storey 1"])
        frame_ids, area_ids = sel.resolve_to_mesh_sets(
            mesh_model,
            storey_data=stories,
        )
        assert frame_ids == {"1"}
        assert area_ids == set()

    def test_story_filter_multiple_storeys(self, mesh_model):
        """story=['Storey 1', 'Storey 2'] → frames at Z=0 and Z=3."""
        stories = [
            StoryLevel(name="Storey 1", elevation=0.0, method="manual", confidence="high"),
            StoryLevel(name="Storey 2", elevation=3.0, method="manual", confidence="high"),
        ]
        sel = Selection(element_types=["Frame"], story=["Storey 1", "Storey 2"])
        frame_ids, _ = sel.resolve_to_mesh_sets(
            mesh_model,
            storey_data=stories,
        )
        assert frame_ids == {"1", "2"}

    def test_story_filter_custom_tolerance(self, mesh_model):
        """With tolerance=0.1, frame at Z=3 matches Storey 2 at Z=3.0."""
        stories = [
            StoryLevel(name="Storey 2", elevation=3.0, method="manual", confidence="high"),
        ]
        sel = Selection(element_types=["Frame"], story=["Storey 2"])
        frame_ids, _ = sel.resolve_to_mesh_sets(
            mesh_model,
            storey_data=stories,
            story_z_tolerance=0.1,
        )
        assert frame_ids == {"2"}

    def test_story_filter_raises_without_storey_data(self, mesh_model):
        """story filter without storey_data raises ValueError."""
        sel = Selection(element_types=["Frame"], story=["Storey 1"])
        with pytest.raises(ValueError, match="storey_data"):
            sel.resolve_to_mesh_sets(mesh_model)

    def test_story_filter_area(self, mesh_model):
        """Area at centroid Z≈1.5 matches Storey 1 at Z=1.5 with tolerance."""
        stories = [
            StoryLevel(name="Storey 1", elevation=1.5, method="manual", confidence="medium"),
        ]
        sel = Selection(element_types=["Area"], story=["Storey 1"])
        _, area_ids = sel.resolve_to_mesh_sets(
            mesh_model,
            storey_data=stories,
            story_z_tolerance=0.1,
        )
        assert area_ids == {"1"}

    def test_story_filter_no_match(self, mesh_model):
        """Non-existent storey name returns empty."""
        stories = [
            StoryLevel(name="Roof", elevation=9.0, method="manual", confidence="high"),
        ]
        sel = Selection(element_types=["Frame"], story=["Basement"])
        frame_ids, _ = sel.resolve_to_mesh_sets(
            mesh_model,
            storey_data=stories,
        )
        assert frame_ids == set()

    # ── missing geometry (z_mid is None) ──

    def test_elevation_filter_excludes_frame_with_missing_nodes(self, mesh_model):
        """A frame with a dangling node reference is excluded when the
        elevation/story filters are active (z_mid is None)."""
        del mesh_model.nodes["2"]  # frame "1" (1→2) loses its far node
        sel = Selection(element_types=["Frame"], elevation_range=(0.0, 3.0))
        frame_ids, _ = sel.resolve_to_mesh_sets(mesh_model)
        # Frame "1" would be in range but has no computable z_mid → excluded
        assert frame_ids == {"2"}

    def test_elevation_filter_excludes_area_with_missing_nodes(self, mesh_model):
        """An area with no resolvable vertex nodes is excluded (z_mid None)."""
        mesh_model.area_elements["1"].node_ids = ["999"]
        sel = Selection(element_types=["Area"], elevation_range=(0.0, 2.0))
        _, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        # Area "1" would be in range but has no computable z_mid → excluded
        assert area_ids == set()

    # ── combined filters ──

    def test_elevation_range_and_element_type(self, mesh_model):
        """Elevation + element_type = Frame → only frames in range."""
        sel = Selection(element_types=["Frame"], elevation_range=(3.0, 9.0))
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == {"2", "3", "4"}
        assert area_ids == set()

    def test_elevation_and_story(self, mesh_model):
        """Both elevation_range and story must match (AND logic)."""
        stories = [
            StoryLevel(name="Storey 2", elevation=3.0, method="manual", confidence="high"),
        ]
        # elevation_range=(0, 2) ∩ story=["Storey 2"] (Z=3) → no match
        sel = Selection(
            element_types=["Frame"],
            elevation_range=(0.0, 2.0),
            story=["Storey 2"],
        )
        frame_ids, _ = sel.resolve_to_mesh_sets(
            mesh_model,
            storey_data=stories,
        )
        assert frame_ids == set()

    # ── None defaults ──

    def test_default_none_behaviour(self, mesh_model):
        """All-None Selection returns all active elements."""
        sel = Selection()
        frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)
        assert frame_ids == {"1", "2", "3", "4"}
        assert area_ids == {"1"}

    # ── inactive element skipping ──

    def test_inactive_elements_skipped(self, mesh_model):
        """Inactive elements are excluded from results."""
        mesh_model.frame_elements["1"].inactive = True
        sel = Selection(element_types=["Frame"])
        frame_ids, _ = sel.resolve_to_mesh_sets(mesh_model)
        assert "1" not in frame_ids
        assert frame_ids == {"2", "3", "4"}


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
            "Steel": Material(name="Steel", type="Steel", E_mod=2e11, unit_weight=77000),
            "Conc": Material(name="Conc", type="Concrete", E_mod=3e10, unit_weight=24000),
        }
        sections = {
            "UB100": Section(
                name="UB100",
                shape="I/Wide Flange",
                material="Steel",
                A=0.01,
                I33=1e-4,
                I22=1e-5,
                J=1e-6,
            ),
            "UB200": Section(
                name="UB200",
                shape="I/Wide Flange",
                material="Steel",
                A=0.02,
                I33=2e-4,
                I22=2e-5,
                J=2e-6,
            ),
            "Slab": ShellSection(
                name="Slab", shape="Shell", material="Conc", A=0, I33=0, I22=0, J=0, thickness=0.2
            ),
        }
        frames = {
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
            "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="7"),
            "4": FrameElement(elem_id="4", elem_tag=4, node_i="4", node_j="8"),
        }
        areas = {
            "1": AreaElement(area_id="1", area_tag=1, node_ids=["1", "2", "5", "6"], thickness=0.2),
        }
        groups = {
            "Cols": Group(name="Cols", objects=["Frame:1", "Frame:2"]),
            "Slab": Group(name="Slab", objects=["Area:1", "Joint:5", "Joint:6"]),
        }
        load_patterns = {
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD"),
            "WIND": LoadPattern(name="WIND", pattern_type="WIND"),
        }
        frame_dist_loads = [
            FrameDistributedLoad(
                pattern="WIND",
                frame_id="1",
                direction="X",
                load_type="Force",
                shape="Uniform",
                val_a=1000,
                val_b=1000,
                rdist_a=0,
                rdist_b=1,
                dist_a=0,
                dist_b=3,
            ),
        ]
        frame_gravity_loads = [
            GravityLoad(pattern="DEAD", frame_id="2", multiplier_z=-1.0),
        ]
        area_uniform_loads = [
            AreaUniformLoad(pattern="DEAD", area_id="1", direction="Gravity", value=5000),
        ]
        area_gravity_loads = [
            AreaGravityLoad(pattern="DEAD", area_id="1", multiplier_z=-1.0),
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
            frame_assignments={"1": "UB100", "2": "UB100", "3": "UB200", "4": "UB200"},
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
        assert len(sub.nodes) == 6  # frame end-nodes: 1,2,3,4,7,8
        assert sorted(sub.nodes) == ["1", "2", "3", "4", "7", "8"]
        assert len(sub.restraints) == 2  # nodes 1, 2

    def test_frame_selection_by_group(self, full_model):
        """Group ``Cols`` → only frames 1 & 2, their 4 end-nodes."""
        sub = Selection(element_types=["Frame"], groups=["Cols"]).filter_model(full_model)
        assert len(sub.frame_elements) == 2
        assert set(sub.frame_elements) == {"1", "2"}
        # End-nodes: 1,3 + 2,4 = 4 nodes
        assert sorted(sub.nodes) == ["1", "2", "3", "4"]
        # Restraints on nodes 1, 2
        assert sorted(sub.restraints) == ["1", "2"]

    def test_frame_selection_sections_materials(self, full_model):
        """Only ``UB100`` section and ``Steel`` material; no Concrete."""
        sub = Selection(element_types=["Frame"], groups=["Cols"]).filter_model(full_model)
        assert sorted(sub.sections) == ["UB100"]
        assert sorted(sub.materials) == ["Steel"]
        assert "Conc" not in sub.materials

    def test_frame_selection_loads(self, full_model):
        """Distributed, gravity, and joint loads on selected frames; no area loads."""
        sub = Selection(element_types=["Frame"], groups=["Cols"]).filter_model(full_model)
        # Only loads on frames 1, 2
        assert len(sub.frame_dist_loads) == 1  # WIND on frame 1
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
        assert len(sub.joint_loads) == 0  # joint on node 3, not an area node

    # ── Combined selection ──

    def test_combined_frame_and_area(self, full_model):
        """Both Frame and Area types: 4 frames + 1 area + 8 unique nodes."""
        sub = Selection(element_types=["Frame", "Area"]).filter_model(full_model)
        assert len(sub.frame_elements) == 4
        assert len(sub.area_elements) == 1
        # All nodes: frame end-nodes (1,2,3,4,7,8) + area corners (1,2,5,6)
        assert sorted(sub.nodes) == ["1", "2", "3", "4", "5", "6", "7", "8"]
        assert len(sub.sections) == 3  # UB100, UB200, Slab
        assert len(sub.materials) == 2  # Steel, Conc

    # ── Group pruning ──

    def test_group_pruning(self, full_model):
        """``Cols`` kept with its 2 frame refs; ``Slab`` excluded entirely."""
        sub = Selection(element_types=["Frame"], groups=["Cols"]).filter_model(full_model)
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
        sub = Selection(element_types=["Frame"], sections=["Nonexistent"]).filter_model(full_model)
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
