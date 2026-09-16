"""Tests for the Rhino visualisation module (``fea_toolkit.rhino``).

These tests cover the parts of the module that work in standard Python
— primarily the colour conversion utilities in ``colors.py`` and the
layer-name sanitisation logic.  Full Rhino-integration tests require
running inside the Rhino process (IronPython) and are not automated here.
"""

import numpy as np
import pytest

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

        assert color_from_name(0) == (0, 0, 0)  # Black
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
        from fea_toolkit.model.sap_data import SAPModelData
        from fea_toolkit.rhino.groups import create_sap_groups

        md = SAPModelData(
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
        with pytest.raises(RuntimeError, match="Rhino modules"):
            create_sap_groups(md, [], [], [])


# ====================================================================
# importer.py — standalone error message
# ====================================================================


class TestRhinoImporterNoRhino:
    """Verify RuntimeError is raised when Rhino is unavailable."""

    def test_init_raises(self):
        from fea_toolkit.model.sap_data import SAPModelData
        from fea_toolkit.rhino.importer import RhinoImporter

        md = SAPModelData(
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
        with pytest.raises(RuntimeError, match="Rhino"):
            RhinoImporter(md)


# ====================================================================
# geometry profile points — pure-math tests (no Rhino import needed)
# ====================================================================


class TestProfilePoints:
    """Profile functions return correct (x,y) point sequences.

    These are inline copies of the functions in geometry.py to avoid
    the Rhino import requirement.  Keep them in sync.
    """

    @staticmethod
    def _signed_area(pts):
        """Positive = CCW, negative = CW."""
        n = len(pts)
        return (
            sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
            / 2.0
        )

    def _rect(self, depth, bf):
        h, w = depth / 2.0, bf / 2.0
        return [(-w, -h), (-w, h), (w, h), (w, -h)]

    def _i(self, depth, bf, tf, tw):
        h = depth / 2.0
        w = bf / 2.0
        wi = tw / 2.0
        return [
            (-wi, -h),
            (-wi, -h + tf),
            (-w, -h + tf),
            (-w, h - tf),
            (-wi, h - tf),
            (-wi, h),
            (wi, h),
            (wi, h - tf),
            (w, h - tf),
            (w, -h + tf),
            (wi, -h + tf),
            (wi, -h),
        ]

    def _box(self, depth, bf, tf, tw):
        h = depth / 2.0
        wi = tw / 2.0
        return [(-wi, -h), (-wi, h), (wi, h), (wi, -h)]

    def _channel(self, depth, bf, tf, tw):
        h = depth / 2.0
        w = bf / 2.0
        fi = h - tf
        wi = tw / 2.0
        return [
            (-w, -h),
            (-w, h),
            (w, h),
            (w, fi),
            (wi, fi),
            (wi, -fi),
            (w, -fi),
            (w, -h),
        ]

    def test_rect_count(self):
        pts = self._rect(0.4, 0.2)
        assert len(pts) == 4

    def test_rect_winding_cw(self):
        assert self._signed_area(self._rect(0.4, 0.2)) < 0

    def test_i_count(self):
        pts = self._i(0.3, 0.15, 0.01, 0.006)
        assert len(pts) == 12

    def test_i_winding_cw(self):
        assert self._signed_area(self._i(0.3, 0.15, 0.01, 0.006)) < 0

    def test_i_dimensions(self):
        depth, bf = 0.3, 0.15
        pts = self._i(depth, bf, 0.01, 0.006)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert abs(max(xs) - bf / 2) < 1e-10
        assert abs(min(xs) + bf / 2) < 1e-10
        assert abs(max(ys) - depth / 2) < 1e-10
        assert abs(min(ys) + depth / 2) < 1e-10

    def test_box_count(self):
        pts = self._box(0.3, 0.2, 0.01, 0.006)
        assert len(pts) == 4

    def test_box_winding_cw(self):
        assert self._signed_area(self._box(0.3, 0.2, 0.01, 0.006)) < 0

    def test_channel_count(self):
        pts = self._channel(0.3, 0.15, 0.01, 0.006)
        assert len(pts) == 8

    def test_channel_winding_cw(self):
        assert self._signed_area(self._channel(0.3, 0.15, 0.01, 0.006)) < 0


# ====================================================================
# _local_axes (Rhino) vs get_local_axes (model) — orientation parity
# ====================================================================
#
# ``fea_toolkit.rhino.geometry`` re-implements the SAP2000/OpenSees local-axis
# convention for Rhino display.  Two copies of one convention can drift apart
# silently, so the tests below drive the *Rhino* implementation through a
# minimal stand-in for the Rhino geometry API and compare it with the model's
# ``get_local_axes`` — the implementation the OpenSees builder actually uses.


class _FakeVector3d:
    """Minimal ``Rhino.Geometry.Vector3d`` stand-in.

    ``fea_toolkit.rhino.geometry`` imports the Rhino API at module scope and
    falls back to ``rg = None`` outside Rhino, so exercising ``_local_axes``
    needs a vector type supplying exactly the surface it touches:
    ``.X/.Y/.Z``, ``Length``, ``Unitize()``, dot product via ``*``, scaling in
    both orders, subtraction, negation and ``CrossProduct``.
    """

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self._v = np.array([float(x), float(y), float(z)])

    @property
    def X(self):
        return float(self._v[0])

    @property
    def Y(self):
        return float(self._v[1])

    @property
    def Z(self):
        return float(self._v[2])

    @property
    def Length(self):
        return float(np.linalg.norm(self._v))

    def Unitize(self):
        norm = np.linalg.norm(self._v)
        if norm:
            self._v = self._v / norm

    def __mul__(self, other):
        if isinstance(other, _FakeVector3d):
            return float(np.dot(self._v, other._v))
        return _FakeVector3d(*(self._v * float(other)))

    __rmul__ = __mul__

    def __sub__(self, other):
        return _FakeVector3d(*(self._v - other._v))

    def __neg__(self):
        return _FakeVector3d(*(-self._v))

    @staticmethod
    def CrossProduct(a, b):
        return _FakeVector3d(*np.cross(a._v, b._v))

    def to_array(self):
        """Return a copy of the backing ``(3,)`` array."""
        return self._v.copy()


class _FakePoint3d:
    """Minimal ``Rhino.Geometry.Point3d`` stand-in."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X, self.Y, self.Z = float(x), float(y), float(z)


class _FakeRhinoGeometry:
    """Namespace matching ``import Rhino.Geometry as rg``."""

    Vector3d = _FakeVector3d
    Point3d = _FakePoint3d


@pytest.fixture
def rhino_local_axes(monkeypatch):
    """Bind ``rhino.geometry._local_axes`` to the fake Rhino geometry API.

    Returns:
        Callable ``(p_i, p_j, angle=0.0)`` → ``(vx, vy, vz)`` numpy arrays,
        or ``None`` for a zero-length member.
    """
    from fea_toolkit.rhino import geometry

    monkeypatch.setattr(geometry, "rg", _FakeRhinoGeometry())

    def _axes(p_i, p_j, angle=0.0):
        result = geometry._local_axes(_FakePoint3d(*p_i), _FakePoint3d(*p_j), angle)
        if result is None:
            return None
        return tuple(v.to_array() for v in result)

    return _axes


#: ``(axis, angle)`` pairs on which both implementations must agree.
#:
#: Includes the cases the two used to get wrong: a vertical member with a
#: rotation angle (the model used to ignore ``angle``) and *near*-vertical
#: members (whose default vecxz was not orthogonal to the axis).
_AGREEING_CASES = [
    ((5.0, 0.0, 0.0), 0.0),
    ((0.0, 4.0, 0.0), 0.0),
    ((0.0, 0.0, 5.0), 0.0),
    ((0.0, 0.0, -5.0), 0.0),
    ((1.0, 1.0, 0.0), 0.0),
    ((1.0, 2.0, 3.0), 0.0),
    ((5.0, 0.0, 0.0), 45.0),
    ((5.0, 0.0, 0.0), 90.0),
    ((5.0, 0.0, 0.0), 180.0),
    ((3.0, 4.0, 0.0), -45.0),
    ((0.0, 4.0, 0.0), 30.0),
    ((0.0, 4.0, 0.0), -30.0),
    ((1.0, 2.0, 3.0), 30.0),
    ((1.0, -2.0, 3.0), 15.0),
    # Vertical member + rotation (angle must not be discarded).
    ((0.0, 0.0, 5.0), 30.0),
    ((0.0, 0.0, 5.0), 45.0),
    ((0.0, 0.0, 5.0), 90.0),
    ((0.0, 0.0, -5.0), 30.0),
    ((0.0, 0.0, -5.0), -45.0),
    # Near-vertical: default vecxz must be orthogonalised, not returned raw.
    ((0.0, 0.02, 5.0), 0.0),
    ((0.02, 0.0, 5.0), 30.0),
    ((0.01, -0.01, 5.0), 0.0),
    ((0.0, -0.02, -5.0), 0.0),
]


class TestLocalAxesVsModel:
    """``rhino.geometry._local_axes`` must reproduce ``get_local_axes``."""

    @pytest.mark.parametrize("axis,angle", _AGREEING_CASES)
    def test_rhino_axes_match_model(self, rhino_local_axes, axis, angle):
        """Both implementations return the same orthonormal triplet."""
        from fea_toolkit.model.geometry import get_local_axes

        got = rhino_local_axes((0.0, 0.0, 0.0), axis, angle)
        want = get_local_axes(np.array(axis), angle=angle)
        for name, got_i, want_i in zip("xyz", got, want):
            np.testing.assert_allclose(got_i, want_i, atol=1e-9, err_msg=f"{name}-axis")

    def test_rhino_axes_are_orthonormal(self, rhino_local_axes):
        """The triplets are unit-length and mutually perpendicular."""
        for axis, angle in _AGREEING_CASES:
            vx, vy, vz = rhino_local_axes((0.0, 0.0, 0.0), axis, angle)
            for vector in (vx, vy, vz):
                assert abs(np.linalg.norm(vector) - 1.0) < 1e-9
            assert abs(np.dot(vx, vy)) < 1e-9
            assert abs(np.dot(vx, vz)) < 1e-9
            assert abs(np.dot(vy, vz)) < 1e-9

    def test_zero_length_member_returns_none(self, rhino_local_axes):
        """A zero-length member yields ``None`` (the model raises instead)."""
        assert rhino_local_axes((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)) is None
