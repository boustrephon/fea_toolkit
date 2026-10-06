"""Tests for :mod:`fea_toolkit.model.section_profiles`.

The section-profile outlines are pure geometry — no Rhino / PyVista import —
and are shared by the Rhino and PyVista renderers, so they are exercised here
directly rather than through either backend.  The Rhino-specific wrappers
around these outlines stay in ``tests/test_rhino.py``.
"""


class TestProfilePoints:
    """Profile functions return correct (x,y) point sequences.

    The outlines live in :mod:`fea_toolkit.model.section_profiles` (shared by
    Rhino and PyVista) so these test that single source of truth directly.
    """

    @staticmethod
    def _signed_area(pts):
        """Positive = CCW, negative = CW."""
        n = len(pts)
        return (
            sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
            / 2.0
        )

    def test_rect_count(self):
        from fea_toolkit.model.section_profiles import profile_rect

        assert len(profile_rect(0.4, 0.2)) == 4

    def test_rect_winding_cw(self):
        from fea_toolkit.model.section_profiles import profile_rect

        assert self._signed_area(profile_rect(0.4, 0.2)) < 0

    def test_i_count(self):
        from fea_toolkit.model.section_profiles import profile_i

        assert len(profile_i(0.3, 0.15, 0.01, 0.006)) == 12

    def test_i_is_ccw(self):
        from fea_toolkit.model.section_profiles import profile_i

        assert self._signed_area(profile_i(0.3, 0.15, 0.01, 0.006)) > 0

    def test_i_dimensions(self):
        from fea_toolkit.model.section_profiles import profile_i

        depth, bf = 0.3, 0.15
        pts = profile_i(depth, bf, 0.01, 0.006)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert abs(max(xs) - bf / 2) < 1e-10
        assert abs(min(xs) + bf / 2) < 1e-10
        assert abs(max(ys) - depth / 2) < 1e-10
        assert abs(min(ys) + depth / 2) < 1e-10

    def test_box_count(self):
        from fea_toolkit.model.section_profiles import profile_box

        outer, inner = profile_box(0.3, 0.2, 0.01, 0.006)
        assert len(outer) == 4
        assert len(inner) == 4

    def test_box_is_ccw(self):
        from fea_toolkit.model.section_profiles import profile_box

        outer, _inner = profile_box(0.3, 0.2, 0.01, 0.006)
        assert self._signed_area(outer) > 0

    def test_channel_count(self):
        from fea_toolkit.model.section_profiles import profile_channel

        assert len(profile_channel(0.3, 0.15, 0.01, 0.006)) == 8

    def test_channel_winding_cw(self):
        from fea_toolkit.model.section_profiles import profile_channel

        assert self._signed_area(profile_channel(0.3, 0.15, 0.01, 0.006)) < 0


class TestSectionProfileOutline:
    """``section_profile_outline`` dispatches a section to its polygon/circle."""

    def test_rectangular_section_is_a_polygon(self):
        from fea_toolkit.model.sap_data import RectangularSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = RectangularSection("R", "Rectangular", "C30", depth=0.4, bf=0.2)
        kind, pts = section_profile_outline(sec)
        assert kind == "polygon"
        assert len(pts) == 4

    def test_circular_section_is_a_circle(self):
        from fea_toolkit.model.sap_data import CircularSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = CircularSection("C", "Circle", "C30", diameter=0.3)
        kind, radius = section_profile_outline(sec)
        assert kind == "circle"
        assert radius == 0.15

    def test_pipe_section_is_a_circle(self):
        from fea_toolkit.model.sap_data import PipeSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = PipeSection("P", "Pipe", "STEEL", od=0.2, t=0.01)
        kind, radius = section_profile_outline(sec)
        assert kind == "circle"
        assert radius == 0.1

    def test_zero_dimension_section_falls_back_to_a_square(self):
        from fea_toolkit.model.sap_data import RectangularSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = RectangularSection("R", "Rectangular", "C30", depth=0.0, bf=0.0, A=0.25)
        kind, pts = section_profile_outline(sec)
        assert kind == "polygon"
        assert len(pts) == 4  # sqrt(0.25) = 0.5 square


class TestSectionPlates:
    """``section_plates`` decomposes a section into its open plate mid-lines."""

    def test_i_section_is_three_plates(self):
        from fea_toolkit.model.sap_data import ISection
        from fea_toolkit.model.section_profiles import section_plates

        sec = ISection("I", "I/Wide Flange", "S", depth=0.3, bf=0.15, tf=0.01, tw=0.006)
        plates = section_plates(sec)
        assert len(plates) == 3
        assert all(len(poly) == 2 for poly in plates)  # each a straight edge

    def test_channel_is_three_plates(self):
        from fea_toolkit.model.sap_data import ChannelSection
        from fea_toolkit.model.section_profiles import section_plates

        sec = ChannelSection("CH", "Channel", "S", depth=0.3, bf=0.15, tf=0.01, tw=0.006)
        assert len(section_plates(sec)) == 3

    def test_box_is_four_plates(self):
        from fea_toolkit.model.sap_data import BoxSection
        from fea_toolkit.model.section_profiles import section_plates

        sec = BoxSection("B", "Box", "S", depth=0.3, bf=0.2, tf=0.01, tw=0.006)
        assert len(section_plates(sec)) == 4

    def test_rect_is_four_open_sides(self):
        from fea_toolkit.model.sap_data import RectangularSection
        from fea_toolkit.model.section_profiles import section_plates

        sec = RectangularSection("R", "Rectangular", "C30", depth=0.4, bf=0.2)
        plates = section_plates(sec)
        assert len(plates) == 4
        assert all(len(poly) == 2 for poly in plates)

    def test_circular_sections_are_one_open_tube(self):
        from fea_toolkit.model.sap_data import CircularSection, PipeSection
        from fea_toolkit.model.section_profiles import section_plates

        for sec in (
            CircularSection("C", "Circle", "C30", diameter=0.3),
            PipeSection("P", "Pipe", "S", od=0.2, t=0.01),
        ):
            plates = section_plates(sec, circle_segments=12)
            assert len(plates) == 1
            assert len(plates[0]) == 12
