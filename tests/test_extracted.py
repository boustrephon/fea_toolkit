"""Tests for fea_toolkit.spectrum and fea_toolkit.utils modules."""
import sys
import math
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import numpy as np
from fea_toolkit.spectrum import _gb50011_spectrum, _build_spectrum, _interp_sa
from fea_toolkit.utils import deep_merge, infer_loads, build_gravity_patterns, pick_wind
import copy
import pytest
from fea_toolkit.model.sap_data import Node, FrameElement, AreaElement


# ── Spectrum tests ─────────────────────────────────────────────────────

def test_gb50011_spectrum_zero_period():
    """At T=0, the spectrum should return 0.45 × α_max × g."""
    Sa = _gb50011_spectrum([0.0], alpha_max=0.5, tg=0.35)
    expected = 0.45 * 0.5 * 9.81
    assert abs(Sa[0] - expected) < 1e-10, f"{Sa[0]} != {expected}"


def test_gb50011_spectrum_plateau():
    """At T=tg, the spectrum should return η₂ × α_max × g."""
    Sa = _gb50011_spectrum([0.35], alpha_max=0.5, tg=0.35, eta2=1.0)
    expected = 1.0 * 0.5 * 9.81
    assert abs(Sa[0] - expected) < 1e-10, f"{Sa[0]} != {expected}"


def test_gb50011_spectrum_descending():
    """At T=5*tg, the spectrum should be on the descending branch."""
    Sa = _gb50011_spectrum([1.75], alpha_max=0.5, tg=0.35)
    # Should be less than plateau value
    plateau = 1.0 * 0.5 * 9.81
    assert Sa[0] < plateau, f"{Sa[0]} not less than plateau {plateau}"


def test_build_spectrum_defaults():
    """_build_spectrum with minimal config should return a reasonable spectrum."""
    cfg = {"intensity": 7, "acceleration": 0.10, "site_class": "I1",
           "level": "rare", "damping": 0.05}
    T, Sa, amax, tg, zeta, label = _build_spectrum(cfg)
    assert len(T) == 300
    assert len(Sa) == 300
    assert amax == 0.50  # rare for VII
    assert tg == 0.25    # I1
    assert zeta == 0.05
    assert "Rare" in label


def test_build_spectrum_frequent():
    """Frequent level should use the frequent alpha_max."""
    cfg = {"intensity": 8, "acceleration": 0.20, "site_class": "II",
           "level": "frequent", "damping": 0.03}
    _, _, amax, _, _, label = _build_spectrum(cfg)
    assert amax == 0.16  # frequent for VIII
    assert "Frequent" in label


def test_interp_sa():
    """Interpolation should return known values at input points."""
    T = [0.0, 0.5, 1.0]
    Sa = [0.0, 1.0, 2.0]
    result = _interp_sa([0.25, 0.75], T, Sa)
    expected = np.interp([0.25, 0.75], T, Sa)
    np.testing.assert_array_almost_equal(result, expected)


# ── Utils tests ────────────────────────────────────────────────────────

def test_deep_merge_scalar():
    """Scalar overrides should replace base values."""
    base = {"a": 1, "b": 2}
    result = deep_merge(base, {"b": 3})
    assert result == {"a": 1, "b": 3}


def test_deep_merge_nested():
    """Nested dicts should be merged recursively."""
    base = {"a": {"x": 1, "y": 2}}
    result = deep_merge(base, {"a": {"y": 99}})
    assert result == {"a": {"x": 1, "y": 99}}


def test_deep_merge_none_removes():
    """A None value should remove the key."""
    base = {"a": 1, "b": 2}
    result = deep_merge(base, {"a": None})
    assert "a" not in result
    assert result == {"b": 2}


def test_infer_loads_empty():
    """Empty raw_tables should return empty categories."""
    result = infer_loads({})
    assert result == {"dead": [], "live": [], "wind": [], "quake": []}


def test_infer_loads_basic():
    """Raw tables with LOAD PATTERN DEFINITIONS should be parsed."""
    raw = {
        "LOAD PATTERN DEFINITIONS": [
            {"LoadPat": "DEAD", "DesignType": "Dead"},
            {"LoadPat": "LL", "DesignType": "Live"},
            {"LoadPat": "WINDX", "DesignType": "Wind"},
            {"LoadPat": "QX", "DesignType": "Quake"},
        ]
    }
    result = infer_loads(raw)
    assert result["dead"] == ["DEAD"]
    assert result["live"] == ["LL"]
    assert result["wind"] == ["WINDX"]
    assert result["quake"] == ["QX"]


def test_build_gravity_patterns():
    """Dead loads get 1.0, Live loads get 0.5."""
    inferred = {"dead": ["DEAD", "SDL"], "live": ["LL"], "wind": [], "quake": []}
    result = build_gravity_patterns(inferred)
    assert result == {"DEAD": 1.0, "SDL": 1.0, "LL": 0.5}


def test_pick_wind():
    """Should match axis and sign in wind pattern names."""
    inferred = {"wind": ["Wind +X", "Wind -X", "Wind +Y"], "dead": [], "live": [], "quake": []}
    result = pick_wind(inferred, "+X")
    assert result == {"Wind +X": 1.0}
    result2 = pick_wind(inferred, "-X")
    assert result2 == {"Wind -X": 1.0}
    result3 = pick_wind(inferred, "+Y")
    assert result3 == {"Wind +Y": 1.0}


# ── compute_flag_parts tests ──────────────────────────────────────────

from fea_toolkit.utils import compute_flag_parts


def test_flag_trapezoid_opposite_signs():
    """Fi*Fj < 0 → single quad trapezoid."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([5.0, 0.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=10.0, Fj=-5.0, scale=0.1))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 4
    np.testing.assert_array_almost_equal(verts[0], pt1)
    np.testing.assert_array_almost_equal(verts[1], pt2)
    np.testing.assert_array_almost_equal(verts[2], pt2 + [0, 0, 0.5])
    np.testing.assert_array_almost_equal(verts[3], pt1 + [0, 0, 1.0])
    assert col_val == 10.0


def test_flag_zero_crossing_same_sign():
    """Fi*Fj > 0 → two triangles crossing at zero."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([5.0, 0.0, 0.0])
    vn = np.array([0.0, 1.0, 0.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=10.0, Fj=5.0, scale=0.1))
    assert len(parts) == 2
    vcp = np.array([5.0 * 10.0 / 15.0, 0, 0])
    v1, c1 = parts[0]
    assert len(v1) == 3
    np.testing.assert_array_almost_equal(v1[0], pt1)
    np.testing.assert_array_almost_equal(v1[1], pt1 + vcp)
    np.testing.assert_array_almost_equal(v1[2], pt1 + [0, 1.0, 0])
    assert c1 == 10.0
    v2, c2 = parts[1]
    assert len(v2) == 3
    np.testing.assert_array_almost_equal(v2[0], pt1 + vcp)
    np.testing.assert_array_almost_equal(v2[1], pt2)
    np.testing.assert_array_almost_equal(v2[2], pt2 + [0, -0.5, 0])
    assert c2 == 5.0


def test_flag_both_negative_zero_crossing():
    """Both negative → two triangles."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([4.0, 0.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=-40.0, Fj=-10.0, scale=0.1))
    assert len(parts) == 2
    v1, c1 = parts[0]
    np.testing.assert_array_almost_equal(v1[2], pt1 + [0, 0, -4])
    assert c1 == -40.0
    v2, c2 = parts[1]
    np.testing.assert_array_almost_equal(v2[2], pt2 + [0, 0, 1])
    assert c2 == -10.0


def test_flag_trapezoid_left_negative_right_positive():
    """Fi<0, Fj>0 → trapezoid, both offsets in -vn."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([3.0, 0.0, 0.0])
    vn = np.array([0.0, 1.0, 0.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=-8.0, Fj=4.0, scale=0.5))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 4
    np.testing.assert_array_almost_equal(verts[3], pt1 + [0, -4, 0])
    np.testing.assert_array_almost_equal(verts[2], pt2 + [0, -2, 0])
    assert col_val == -8.0


def test_flag_zero_at_one_end():
    """Fi=0, Fj non-zero → single triangle."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([2.0, 0.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=0.0, Fj=10.0, scale=0.1))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 3
    np.testing.assert_array_almost_equal(verts[0], pt1)
    np.testing.assert_array_almost_equal(verts[2], pt2 + [0, 0, -1.0])
    assert col_val == 10.0


def test_flag_zero_at_both_ends():
    """Fi=0, Fj=0 → no parts yielded."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([2.0, 0.0, 0.0])
    vn = np.array([0.0, 1.0, 0.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=0.0, Fj=0.0, scale=1.0))
    assert len(parts) == 0


def test_flag_3d_diagonal_member():
    """Non-axis-aligned member — basic geometry test."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([3.0, 4.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=10.0, Fj=-10.0, scale=0.5))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 4
    np.testing.assert_array_almost_equal(verts[0], pt1)
    np.testing.assert_array_almost_equal(verts[1], pt2)
    np.testing.assert_array_almost_equal(verts[2], pt2 + [0, 0, 5])
    np.testing.assert_array_almost_equal(verts[3], pt1 + [0, 0, 5])


# ============================================================================
# Frame end offset tests
# ============================================================================

class TestApplyFrameEndOffsets:
    """Tests for geometry.apply_frame_end_offsets()."""

    def _make_elements(self):
        from fea_toolkit.model.sap_data import FrameEndOffset
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 6.0, 0.0, 0.0),
        }
        elements = {
            "1": FrameElement("1", 10, "1", "2"),
        }
        assignments = {"1": "Col600"}
        return elements, assignments, nodes

    def test_no_offsets_does_nothing(self):
        """Zero offsets → areas, nodes, assignments unchanged."""
        from fea_toolkit.model.geometry import apply_frame_end_offsets
        from fea_toolkit.model.sap_data import FrameEndOffset
        elems, assign, nodes = self._make_elements()
        orig_elems = copy.deepcopy(elems)
        orig_assign = copy.deepcopy(assign)
        orig_nodes = copy.deepcopy(nodes)
        offsets = {"1": FrameEndOffset(0.0, 0.0)}
        elems, assign, nodes, ntag, links = apply_frame_end_offsets(
            elems, assign, nodes, offsets
        )
        assert len(links) == 0
        assert ntag == 1, "next_tag should not advance"
        assert elems == orig_elems, "elements dict mutated"
        assert assign == orig_assign, "assignments dict mutated"
        assert nodes == orig_nodes, "nodes dict mutated"
        assert elems["1"].node_i == "1"
        assert elems["1"].node_j == "2"

    def test_i_end_offset_creates_rigid_link(self):
        """Offset at I-end creates one rigid link and shortens element."""
        from fea_toolkit.model.geometry import apply_frame_end_offsets
        from fea_toolkit.model.sap_data import FrameEndOffset
        elems, assign, nodes = self._make_elements()
        offsets = {"1": FrameEndOffset(0.3, 0.0)}
        elems, assign, nodes, ntag, links = apply_frame_end_offsets(
            elems, assign, nodes, offsets
        )
        assert len(links) == 1
        assert "1_off_i" in nodes
        # I-end offset → element rewired to offset node
        assert elems["1"].node_i == "1_off_i"
        assert links[0][1] == "1"
        assert links[0][2] == "1_off_i"
        # J-end has no offset → keeps original node
        assert elems["1"].node_j == "2"
        # No duplicate node at J-end
        j_off_ids = [nid for nid in nodes if "_off_j" in nid]
        assert len(j_off_ids) == 0

    def test_both_ends_offset(self):
        """Both-end offsets create two rigid links."""
        from fea_toolkit.model.geometry import apply_frame_end_offsets
        from fea_toolkit.model.sap_data import FrameEndOffset
        elems, assign, nodes = self._make_elements()
        offsets = {"1": FrameEndOffset(0.2, 0.4)}
        elems, assign, nodes, ntag, links = apply_frame_end_offsets(
            elems, assign, nodes, offsets
        )
        assert len(links) == 2
        assert "1_off_i" in nodes
        assert "1_off_j" in nodes

    def test_offset_clamped_to_half_length(self):
        """Excessive offset is clamped so the elastic portion doesn't vanish."""
        from fea_toolkit.model.geometry import apply_frame_end_offsets
        from fea_toolkit.model.sap_data import FrameEndOffset
        elems, assign, nodes = self._make_elements()
        offsets = {"1": FrameEndOffset(5.0, 5.0)}
        elems, assign, nodes, ntag, links = apply_frame_end_offsets(
            elems, assign, nodes, offsets
        )
        assert len(links) == 2
        ni = nodes[elems["1"].node_i]
        nj = nodes[elems["1"].node_j]
        remaining = np.linalg.norm(
            np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        )
        # 6 m element – each end clamped to 6 × 0.45 = 2.7 m → 0.6 m left
        assert remaining == pytest.approx(0.6)

    def test_missing_element_skipped(self):
        """Offset for a non-existent element is silently skipped."""
        from fea_toolkit.model.geometry import apply_frame_end_offsets
        from fea_toolkit.model.sap_data import FrameEndOffset
        elems, assign, nodes = self._make_elements()
        offsets = {"99": FrameEndOffset(0.3, 0.0)}
        elems, assign, nodes, ntag, links = apply_frame_end_offsets(
            elems, assign, nodes, offsets
        )
        assert len(links) == 0


# ============================================================================
# Area meshing tests
# ============================================================================

class TestMeshAreaElements:
    """Tests for geometry.mesh_area_elements()."""

    def _make_quad_model(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 12.0, 0.0, 0.0),
            "3": Node("3", 3, 12.0, 8.0, 0.0),
            "4": Node("4", 4, 0.0, 8.0, 0.0),
        }
        areas = {
            "1": AreaElement("1", 10, ["1", "2", "3", "4"]),
        }
        assignments = {"1": "Slab200"}
        return areas, assignments, nodes

    def test_no_mesh_no_change(self):
        """No mesh settings → areas, nodes, assignments are unchanged."""
        from fea_toolkit.model.geometry import mesh_area_elements
        areas, assign, nodes = self._make_quad_model()
        orig_areas = copy.deepcopy(areas)
        orig_nodes = copy.deepcopy(nodes)
        orig_assign = copy.deepcopy(assign)
        areas, assign, nodes, ntag = mesh_area_elements(
            areas, assign, nodes, {}
        )
        assert areas == orig_areas, "areas dict mutated"
        assert nodes == orig_nodes, "nodes dict mutated"
        assert assign == orig_assign, "assignments dict mutated"
        assert ntag == 1, "next_tag should remain default 1"

    def test_mesh_creates_sub_areas(self):
        """2x2 subdivision produces 4 sub-quads and 1 interior node."""
        from fea_toolkit.model.geometry import mesh_area_elements
        from fea_toolkit.model.sap_data import AreaMesh
        areas, assign, nodes = self._make_quad_model()
        mesh = {"1": AreaMesh(auto_mesh=True, max_size=6.0)}
        areas, assign, nodes, ntag = mesh_area_elements(
            areas, assign, nodes, mesh, next_tag=100
        )
        sub_ids = [aid for aid in areas if aid != "1"]
        assert len(sub_ids) == 4  # ceil(12/6)=2 × ceil(8/6)=2 = 4
        assert areas["1"].inactive is True
        assert "1_mesh_1_1" in nodes  # fully interior node

    def test_mesh_preserves_section_assignment(self):
        """Sub-areas inherit the section from the parent."""
        from fea_toolkit.model.geometry import mesh_area_elements
        from fea_toolkit.model.sap_data import AreaMesh
        areas, assign, nodes = self._make_quad_model()
        mesh = {"1": AreaMesh(auto_mesh=True, max_size=6.0)}
        areas, assign, nodes, ntag = mesh_area_elements(
            areas, assign, nodes, mesh, next_tag=100
        )
        for aid in areas:
            if aid != "1":
                assert assign.get(aid) == "Slab200"

    def test_no_subdivision_if_max_size_too_large(self):
        """max_size > element dimension → areas, nodes, assignments unchanged."""
        from fea_toolkit.model.geometry import mesh_area_elements
        from fea_toolkit.model.sap_data import AreaMesh
        areas, assign, nodes = self._make_quad_model()
        orig_areas = copy.deepcopy(areas)
        orig_nodes = copy.deepcopy(nodes)
        orig_assign = copy.deepcopy(assign)
        mesh = {"1": AreaMesh(auto_mesh=True, max_size=100.0)}
        areas, assign, nodes, ntag = mesh_area_elements(
            areas, assign, nodes, mesh, next_tag=100
        )
        assert areas == orig_areas, "areas dict mutated"
        assert nodes == orig_nodes, "nodes dict mutated"
        assert assign == orig_assign, "assignments dict mutated"

    def test_mesh_auto_mesh_false_skipped(self):
        """auto_mesh=False → areas, nodes, assignments unchanged."""
        from fea_toolkit.model.geometry import mesh_area_elements
        from fea_toolkit.model.sap_data import AreaMesh
        areas, assign, nodes = self._make_quad_model()
        orig_areas = copy.deepcopy(areas)
        orig_nodes = copy.deepcopy(nodes)
        orig_assign = copy.deepcopy(assign)
        mesh = {"1": AreaMesh(auto_mesh=False, max_size=1.0)}
        areas, assign, nodes, ntag = mesh_area_elements(
            areas, assign, nodes, mesh, next_tag=100
        )
        assert areas == orig_areas, "areas dict mutated"
        assert nodes == orig_nodes, "nodes dict mutated"
        assert assign == orig_assign, "assignments dict mutated"


# ============================================================================
# Confinement model tests
# ============================================================================

class TestManderConfinement:
    """Tests for model.confinement.mander_confined()."""

    def test_unconfined_when_no_spacing(self):
        """Zero spacing returns unconfined properties."""
        from fea_toolkit.model.confinement import ConfinementData, mander_confined
        data = ConfinementData(fc=30e6, tie_diameter=0.01, tie_spacing=0,
                               tie_fy=400e6, core_bc=0.3, core_dc=0.3)
        result = mander_confined(data)
        assert result.fcc == 30e6
        assert result.ecc == 0.002
        assert result.ecu == 0.004
        assert result.ke == 0.0

    def test_rectangular_standard(self):
        """Rectangular perimeter hoop produces reasonable ke and fcc."""
        from fea_toolkit.model.confinement import ConfinementData, mander_confined
        data = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
            tie_fy=400e6, core_bc=0.4, core_dc=0.4,
            long_diameter=0.02, long_count_x=3, long_count_y=3,
            tie_config="standard",
        )
        result = mander_confined(data)
        assert result.fcc > 30e6
        assert result.ecc > 0.002
        assert result.ke > 0
        assert result.rho_s > 0

    def test_cross_tie_explicit_counts(self):
        """Cross-tie with explicit count fields affects Ash_x and Ash_y."""
        from fea_toolkit.model.confinement import ConfinementData, mander_confined
        plain = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
            tie_fy=400e6, core_bc=0.4, core_dc=0.4,
            long_diameter=0.02, long_count_x=3, long_count_y=3,
            tie_config="standard",
        )
        r1 = mander_confined(plain)

        tied = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
            tie_fy=400e6, core_bc=0.4, core_dc=0.4,
            long_diameter=0.02, long_count_x=3, long_count_y=3,
            tie_config="cross_tie",
            cross_tie_count_x=2, cross_tie_count_y=2,
        )
        r2 = mander_confined(tied)
        assert r2.rho_s > r1.rho_s
        assert r2.fcc > r1.fcc

    def test_spiral_ke_uses_rho_cc(self):
        """Circular spiral ke uses rho_cc not rho_s."""
        from fea_toolkit.model.confinement import ConfinementData, mander_confined
        data = ConfinementData(
            fc=30e6, tie_diameter=0.012, tie_spacing=0.05,
            tie_fy=400e6, core_bc=0.35, core_dc=0.35,
            long_diameter=0.02, long_count_x=4, long_count_y=4,
            tie_config="spiral",
        )
        result = mander_confined(data)
        assert result.fcc > 30e6
        assert result.ke > 0
        if result.ke > 0:
            assert abs(result.ke - 1.0/(1.0 - result.rho_s)) > 0.001, (
                "ke appears to use rho_s denominator instead of rho_cc")

    def test_ecu_with_eps_su(self):
        """ecu formula uses eps_su and confined strength fcc."""
        from fea_toolkit.model.confinement import ConfinementData, mander_confined
        low_eps = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
            tie_fy=400e6, core_bc=0.4, core_dc=0.4,
            long_diameter=0.02, long_count_x=3, long_count_y=3,
            tie_config="standard", eps_su=0.05,
        )
        r_low = mander_confined(low_eps)

        high_eps = ConfinementData(
            fc=30e6, tie_diameter=0.01, tie_spacing=0.1,
            tie_fy=400e6, core_bc=0.4, core_dc=0.4,
            long_diameter=0.02, long_count_x=3, long_count_y=3,
            tie_config="standard", eps_su=0.15,
        )
        r_high = mander_confined(high_eps)
        assert r_high.ecu > r_low.ecu
        assert r_low.ecu > 0.004
        assert r_high.ecu <= 0.025


# ============================================================================
# Tcl export tests
# ============================================================================

class TestTclExport:
    """Tests for export_model_to_tcl with fiber sections."""

    def _make_rc_model(self):
        """Build minimal SAPModelData with one RC column section."""
        from fea_toolkit.model.sap_data import (
            SAPModelData, Material, ConcreteRectangularSection,
            Node, FrameElement,
        )
        mat = Material(
            name="C30", type="Concrete", Fc=30e6, E_mod=25e9,
        )
        sec = ConcreteRectangularSection(
            name="Col400", shape="Concrete Rectangular", material="C30",
            A=0.16, I33=0.002133, I22=0.002133, J=0.0036,
            depth=0.4, bf=0.4, cover=0.04,
            top_bars=4, bot_bars=4, top_bar_dia=0.02, bot_bar_dia=0.02,
        )
        return SAPModelData(
            nodes={
                "1": Node("1", 1, 0, 0, 0),
                "2": Node("2", 2, 0, 0, 3),
            },
            restraints={},
            materials={"C30": mat},
            sections={"Col400": sec},
            frame_elements={
                "1": FrameElement("1", 10, "1", "2"),
            },
            area_elements={},
            frame_assignments={"1": "Col400"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            units={"F": "N", "L": "m", "T": "C"},
        )

    def test_export_fiber_sections_have_braces(self):
        """Fiber sections in exported Tcl have brace-delimited blocks."""
        import tempfile, os
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        md = self._make_rc_model()
        config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl",
                                          delete=False) as f:
            path = f.name
        try:
            OpenSeesBuilder.export_model_to_tcl(md, path, config=config)
            with open(path) as f:
                tcl = f.read()
        finally:
            os.unlink(path)

        assert "section Fiber " in tcl
        fiber_blocks = 0
        in_fiber = False
        depth = 0
        for line in tcl.split("\n"):
            stripped = line.strip()
            if stripped.startswith("section Fiber"):
                in_fiber = True
            if in_fiber:
                depth += stripped.count("{")
                depth -= stripped.count("}")
                if depth == 0 and in_fiber:
                    fiber_blocks += 1
                    in_fiber = False
        assert fiber_blocks >= 1, "No complete fiber section block found"

    def test_export_no_elastic_for_fiber_sections(self):
        """No section Elastic emitted for RC sections with fiber sections."""
        import tempfile, os
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        md = self._make_rc_model()
        config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl",
                                          delete=False) as f:
            path = f.name
        try:
            OpenSeesBuilder.export_model_to_tcl(md, path, config=config)
            with open(path) as f:
                tcl = f.read()
        finally:
            os.unlink(path)

        elastic_lines = [l for l in tcl.split("\n")
                         if l.strip().startswith("section Elastic")]
        assert len(elastic_lines) == 0, (
            f"Expected no section Elastic, found {len(elastic_lines)}")

    def test_export_force_beam_column_for_fiber(self):
        """Frame elements use forceBeamColumn for fiber sections."""
        import tempfile, os
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        md = self._make_rc_model()
        config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl",
                                          delete=False) as f:
            path = f.name
        try:
            OpenSeesBuilder.export_model_to_tcl(md, path, config=config)
            with open(path) as f:
                tcl = f.read()
        finally:
            os.unlink(path)

        assert "forceBeamColumn" in tcl, (
            "Expected forceBeamColumn element in Tcl output")
        assert "beamIntegration Lobatto" in tcl, (
            "Expected beamIntegration Lobatto in Tcl output")
        assert "elasticBeamColumn" not in tcl, (
            "Unexpected elasticBeamColumn (should be forceBeamColumn)")

    def test_export_without_fiber_uses_elastic(self):
        """Without create_fiber_sections, elasticBeamColumn is used."""
        import tempfile, os
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        md = self._make_rc_model()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl",
                                          delete=False) as f:
            path = f.name
        try:
            OpenSeesBuilder.export_model_to_tcl(md, path, config=None)
            with open(path) as f:
                tcl = f.read()
        finally:
            os.unlink(path)

        assert "elasticBeamColumn" in tcl, (
            "Expected elasticBeamColumn for non-fibre export")
        assert "section Elastic" in tcl, (
            "Expected section Elastic for non-fibre export")
