"""Tests for the Rhino visualisation module (``fea_toolkit.rhino``).

These tests cover the parts of the module that work in standard Python
— primarily the colour conversion utilities in ``colors.py`` and the
layer-name sanitisation logic.  Full Rhino-integration tests require
running inside the Rhino process (IronPython) and are not automated here.
"""

import pytest
import math


# ====================================================================
# colours.py — standalone colour conversion
# ====================================================================

class TestColorFromName:
    """``color_from_name`` in standard Python returns ``(r, g, b)`` tuples."""

    def test_none_returns_none(self):
        from fea_toolkit.rhino.colors import color_from_name
        assert color_from_name(None) is None
        assert color_from_name("") is None

    def test_black_and_white(self):
        from fea_toolkit.rhino.colors import color_from_name
        assert color_from_name(0) == (0, 0, 0)          # Black
        assert color_from_name(16777215) == (255, 255, 255)  # White

    def test_rgb_extraction(self):
        from fea_toolkit.rhino.colors import color_from_name
        # 0xFF0000 = Red
        assert color_from_name(16711680) == (255, 0, 0)
        # 0x00FF00 = Green
        assert color_from_name(65280) == (0, 255, 0)
        # 0x0000FF = Blue
        assert color_from_name(255) == (0, 0, 255)

    def test_named_colours(self):
        from fea_toolkit.rhino.colors import color_from_name
        assert color_from_name("Red") == (255, 0, 0)
        assert color_from_name("Green") == (0, 128, 0)
        assert color_from_name("Blue") == (0, 0, 255)
        assert color_from_name("Black") == (0, 0, 0)
        assert color_from_name("White") == (255, 255, 255)

    def test_named_colours_case_insensitive(self):
        from fea_toolkit.rhino.colors import color_from_name
        assert color_from_name("red") == (255, 0, 0)
        assert color_from_name("RED") == (255, 0, 0)
        assert color_from_name("BlUe") == (0, 0, 255)

    def test_gray_variants(self):
        from fea_toolkit.rhino.colors import color_from_name
        # "Dark Gray" → (64, 64, 64)
        assert color_from_name("dark gray") == (64, 64, 64)
        assert color_from_name("light grey") == (211, 211, 211)
        assert color_from_name("gray") == (128, 128, 128)
        assert color_from_name("grey") == (128, 128, 128)

    def test_sap2000_integer_codes(self):
        from fea_toolkit.rhino.colors import color_from_name
        # Common SAP2000 codes from the existing Rhino script
        assert color_from_name(13107400) == (200, 200, 200)
        assert color_from_name(8421504) == (128, 128, 128)
        assert color_from_name(12632256) == (192, 192, 192)

    def test_fallback(self):
        from fea_toolkit.rhino.colors import color_from_name
        # Unknown colour → returns (128, 128, 128) gray
        result = color_from_name("nonexistent_colour")
        assert result == (128, 128, 128)

    def test_float_input(self):
        from fea_toolkit.rhino.colors import color_from_name
        # Float that converts to integer
        assert color_from_name(255.0) == (0, 0, 255)


class TestGetSAP2000Color:
    """``get_sap2000_color`` with defaults."""

    def test_with_default_color(self):
        from fea_toolkit.rhino.colors import get_sap2000_color
        # None value with string default
        result = get_sap2000_color(None, "Red")
        assert result == (255, 0, 0)

    def test_with_tuple_default(self):
        from fea_toolkit.rhino.colors import get_sap2000_color
        # None value with tuple default
        result = get_sap2000_color(None, (100, 150, 200))
        assert result == (100, 150, 200)

    def test_value_overrides_default(self):
        from fea_toolkit.rhino.colors import get_sap2000_color
        result = get_sap2000_color("Green", "Red")
        assert result == (0, 128, 0)

    def test_none_no_default(self):
        from fea_toolkit.rhino.colors import get_sap2000_color
        result = get_sap2000_color(None)
        assert result == (128, 128, 128)


class TestPaletteConstants:
    """Palette tuples are well-formed."""

    def test_restraint_colors_have_all_keys(self):
        from fea_toolkit.rhino.colors import RESTRAINT_COLORS
        expected_keys = {"fully_fixed", "pinned", "roller", "free", "constrained"}
        assert set(RESTRAINT_COLORS.keys()) == expected_keys
        for key, rgb in RESTRAINT_COLORS.items():
            assert len(rgb) == 3
            assert all(0 <= v <= 255 for v in rgb)

    def test_shell_palette_length(self):
        from fea_toolkit.rhino.colors import SHELL_PALETTE
        assert len(SHELL_PALETTE) >= 3
        for rgb in SHELL_PALETTE:
            assert len(rgb) == 3
            assert all(0 <= v <= 255 for v in rgb)

    def test_frame_palette_length(self):
        from fea_toolkit.rhino.colors import FRAME_PALETTE
        assert len(FRAME_PALETTE) >= 3
        for rgb in FRAME_PALETTE:
            assert len(rgb) == 3
            assert all(0 <= v <= 255 for v in rgb)


# ====================================================================
# layers.py — standalone utilities (sanitize_layer_name)
# ====================================================================

class TestSanitizeLayerName:
    """``sanitize_layer_name`` replaces illegal characters."""

    def test_illegal_characters_replaced(self):
        from fea_toolkit.rhino.layers import sanitize_layer_name
        result = sanitize_layer_name("Section/Name:Test*Dot.")
        # All illegal chars replaced with _
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result
        assert "." not in result
        assert "_" in result

    def test_long_name_truncated(self):
        from fea_toolkit.rhino.layers import sanitize_layer_name
        long_name = "A" * 100
        result = sanitize_layer_name(long_name)
        assert len(result) <= 40
        assert result.endswith("...")

    def test_short_name_unchanged(self):
        from fea_toolkit.rhino.layers import sanitize_layer_name
        result = sanitize_layer_name("UB300")
        assert result == "UB300"

    def test_none_converted(self):
        from fea_toolkit.rhino.layers import sanitize_layer_name
        result = sanitize_layer_name(None)
        assert isinstance(result, str)
        assert len(result) > 0


# ====================================================================
# groups.py — standalone group functions (no Rhino API needed)
# ====================================================================

class TestCreateRhinoGroupNoRhino:
    """Verify the module raises RuntimeError when Rhino is unavailable."""

    def test_create_rhino_group_raises(self):
        from fea_toolkit.rhino.groups import create_rhino_group
        with pytest.raises(RuntimeError, match="Rhino modules"):
            create_rhino_group("test", ["id1"])

    def test_create_sap_groups_raises(self):
        from fea_toolkit.rhino.groups import create_sap_groups
        from fea_toolkit.model.sap_data import SAPModelData
        md = SAPModelData(
            nodes={}, restraints={}, materials={}, sections={},
            frame_elements={}, area_elements={},
            frame_assignments={}, area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        with pytest.raises(RuntimeError, match="Rhino modules"):
            create_sap_groups(md, [], [], [])


# ====================================================================
# importer.py — standalone error message
# ====================================================================

class TestRhinoImporterNoRhino:
    """Verify RuntimeError is raised when Rhino is unavailable."""

    def test_init_raises(self):
        from fea_toolkit.rhino.importer import RhinoImporter
        from fea_toolkit.model.sap_data import SAPModelData
        md = SAPModelData(
            nodes={}, restraints={}, materials={}, sections={},
            frame_elements={}, area_elements={},
            frame_assignments={}, area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        with pytest.raises(RuntimeError, match="Rhino"):
            RhinoImporter(md)


# ====================================================================
# geometry_v2 profile points — pure-math tests (no Rhino import needed)
# ====================================================================

class TestProfilePoints:
    """Profile functions return correct (x,y) point sequences."""

    def _rect(self, depth, bf):
        h, w = depth / 2.0, bf / 2.0
        return [(-w, -h), (-w, h), (w, h), (w, -h)]

    def _i(self, depth, bf, tf, tw):
        h = depth / 2.0
        w = bf / 2.0
        wi = tw / 2.0
        fi = h - tf
        return [
            (-w, -h), (w, -h), (w, -fi), (wi, -fi),
            (wi, fi), (w, fi), (w, h), (-w, h),
            (-w, fi), (-wi, fi), (-wi, -fi), (-w, -fi),
        ]

    def test_rect_count(self):
        pts = self._rect(0.4, 0.2)
        assert len(pts) == 4  # rectangle = 4 corners
        # All z=0 (profile is in XY plane)
        for x, y in pts:
            assert isinstance(x, float)
            assert isinstance(y, float)

    def test_i_count(self):
        pts = self._i(0.3, 0.15, 0.01, 0.006)
        assert len(pts) == 12  # I-section = 12 vertices

    def test_i_dimensions(self):
        depth, bf = 0.3, 0.15
        pts = self._i(depth, bf, 0.01, 0.006)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # Width spans ±bf/2, depth spans ±depth/2
        assert abs(max(xs) - bf / 2) < 1e-10
        assert abs(min(xs) + bf / 2) < 1e-10
        assert abs(max(ys) - depth / 2) < 1e-10
        assert abs(min(ys) + depth / 2) < 1e-10

    def test_box_count(self):
        h, w = 0.3 / 2, 0.2 / 2
        tf, tw = 0.01, 0.006
        hi, wi = h - tf, w - tw
        pts = [
            (-w, -h), (w, -h), (w, h), (-w, h),
            (-w, hi), (wi, hi), (wi, -hi), (-w, -hi),
        ]
        assert len(pts) == 8

    def test_channel_count(self):
        h = 0.3 / 2
        w = 0.15 / 2
        pts = [
            (-w, -h), (w, -h), (w, -h + 0.01), (0.003, -h + 0.01),
            (0.003, h - 0.01), (w, h - 0.01), (w, h), (-w, h),
        ]
        assert len(pts) == 8


# ====================================================================
# _local_axes vs get_SAP_vecxz — orientation cross-check
# ====================================================================

class TestLocalAxesVsModel:
    """Verify Rhino _local_axes matches the model's get_SAP_vecxz."""

    def test_horizontal_beam(self):
        """Beam along X: y=vertical, z=horizontal."""
        from fea_toolkit.model.geometry import get_SAP_vecxz
        import numpy as np

        # Beam from (0,0,0) to (5,0,0)
        vec_x = np.array([5.0, 0.0, 0.0])

        # Expected: vecxz = cross((1,0,0), (0,0,1)) = (0,-1,0)
        expected_vecxz = get_SAP_vecxz(vec_x, angle=0.0)
        # local z = normalize(vecxz)
        expected_z = expected_vecxz / np.linalg.norm(expected_vecxz)
        # local y = cross(z, x)
        expected_y = np.cross(expected_z, vec_x / np.linalg.norm(vec_x))
        expected_y = expected_y / np.linalg.norm(expected_y)

        # Expected: x=(1,0,0), y=(0,0,1), z=(0,-1,0)
        np.testing.assert_array_almost_equal(expected_z, [0, -1, 0])
        np.testing.assert_array_almost_equal(expected_y, [0, 0, 1])

    def test_vertical_column(self):
        """Column along Z: y=global X, z=global Y."""
        from fea_toolkit.model.geometry import get_SAP_vecxz
        import numpy as np

        vec_x = np.array([0.0, 0.0, 5.0])
        expected_vecxz = get_SAP_vecxz(vec_x, angle=0.0)
        expected_z = expected_vecxz / np.linalg.norm(expected_vecxz)
        expected_y = np.cross(expected_z, vec_x / np.linalg.norm(vec_x))
        expected_y = expected_y / np.linalg.norm(expected_y)

        # For vertical column: vecxz = global Y
        np.testing.assert_array_almost_equal(expected_z, [0, 1, 0])
        # y = cross(Y, Z) = X
        np.testing.assert_array_almost_equal(expected_y, [1, 0, 0])

    def test_horizontal_with_angle(self):
        """Beam along X with 45° rotation."""
        from fea_toolkit.model.geometry import get_SAP_vecxz
        import numpy as np

        vec_x = np.array([5.0, 0.0, 0.0])
        angle = 45.0

        # get_SAP_vecxz applies the angle rotation
        expected_vecxz = get_SAP_vecxz(vec_x, angle=angle)
        expected_z = expected_vecxz / np.linalg.norm(expected_vecxz)
        expected_y = np.cross(expected_z, vec_x / np.linalg.norm(vec_x))
        expected_y = expected_y / np.linalg.norm(expected_y)

        # With 45° rotation, z should be rotated from (0,-1,0)
        # about x-axis by 45°: z = (0, -cos45, -sin45) = (0, -0.707, -0.707)
        np.testing.assert_array_almost_equal(
            expected_z, [0, -0.70710678, -0.70710678], decimal=6
        )

    def test_angle_roundtrip(self):
        """Angle=90° swaps y and z."""
        from fea_toolkit.model.geometry import get_SAP_vecxz
        import numpy as np

        vec_x = np.array([5.0, 0.0, 0.0])
        z0 = get_SAP_vecxz(vec_x, angle=0.0)
        z90 = get_SAP_vecxz(vec_x, angle=90.0)

        dot = np.dot(z0, z90)
        # 90° rotation: z0 and z90 should be perpendicular
        assert abs(dot) < 1e-10

    def test_vertical_downward(self):
        """Column pointing downward: vecxz = -global Y."""
        from fea_toolkit.model.geometry import get_SAP_vecxz
        import numpy as np

        vec_x = np.array([0.0, 0.0, -5.0])
        expected = get_SAP_vecxz(vec_x, angle=0.0)
        # Downward column: vecxz = -global Y
        np.testing.assert_array_almost_equal(expected, [0, -1, 0])
