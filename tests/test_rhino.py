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
