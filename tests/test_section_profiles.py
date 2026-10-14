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

    def test_concrete_rectangular_section_is_a_rectangle(self):
        from fea_toolkit.model.sap_data import ConcreteRectangularSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = ConcreteRectangularSection("CB", "Rectangular", "CONC", depth=0.4, bf=0.2)
        kind, pts = section_profile_outline(sec)
        assert kind == "polygon"
        assert len(pts) == 4
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # Depth 0.4 on the y axis, breadth 0.2 on the x axis — a rectangle, not
        # the sqrt(A) square the zero-dimension fallback would produce.
        assert max(xs) == 0.1 and min(xs) == -0.1
        assert max(ys) == 0.2 and min(ys) == -0.2

    def test_circular_section_is_a_circle(self):
        from fea_toolkit.model.sap_data import CircularSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = CircularSection("C", "Circle", "C30", diameter=0.3)
        kind, radius = section_profile_outline(sec)
        assert kind == "circle"
        assert radius == 0.15

    def test_concrete_circular_section_is_a_circle(self):
        from fea_toolkit.model.sap_data import ConcreteCircularSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = ConcreteCircularSection("CC", "Concrete Circular", "CONC", diameter=0.3)
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

    def test_sd_section_uses_its_polygon(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        # A 0.4 (local 2) × 0.2 (local 3) rectangle, corners in (y, z).
        sec = SDSection(
            name="SD",
            shape="SD Section",
            material="CONC",
            polygons=[("CONC", [(-0.2, -0.1), (-0.2, 0.1), (0.2, 0.1), (0.2, -0.1)])],
        )
        kind, pts = section_profile_outline(sec)
        assert kind == "polygon"
        assert len(pts) == 4
        xs = [p[0] for p in pts]  # x = local 3 (width)
        ys = [p[1] for p in pts]  # y = local 2 (depth)
        assert abs(max(xs) - 0.1) < 1e-9 and abs(min(xs) + 0.1) < 1e-9
        assert abs(max(ys) - 0.2) < 1e-9 and abs(min(ys) + 0.2) < 1e-9

    def test_sd_section_centres_on_its_centroid(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        # Rectangle offset so its (y, z) centroid is (0.5, 0.25), not the origin.
        sec = SDSection(
            name="SD",
            shape="SD Section",
            material="CONC",
            polygons=[("CONC", [(0.0, 0.0), (0.0, 0.5), (1.0, 0.5), (1.0, 0.0)])],
        )
        _kind, pts = section_profile_outline(sec)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        assert abs(max(xs) - 0.25) < 1e-9 and abs(min(xs) + 0.25) < 1e-9
        assert abs(max(ys) - 0.5) < 1e-9 and abs(min(ys) + 0.5) < 1e-9

    def test_sd_section_with_a_hole(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        outer = [(-0.5, -0.5), (-0.5, 0.5), (0.5, 0.5), (0.5, -0.5)]
        inner = [(-0.25, -0.25), (-0.25, 0.25), (0.25, 0.25), (0.25, -0.25)]
        sec = SDSection(
            name="SD",
            shape="SD Section",
            material="CONC",
            polygons=[("CONC", outer), ("VOID", inner)],
        )
        kind, value = section_profile_outline(sec)
        assert kind == "polygon"
        assert isinstance(value, tuple)
        outer_pts, inner_pts = value
        assert len(outer_pts) == 4
        assert len(inner_pts) == 4

    def test_sd_section_with_two_holes(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        outer = [(-0.5, -0.5), (-0.5, 0.5), (0.5, 0.5), (0.5, -0.5)]
        hole1 = [(-0.4, -0.4), (-0.4, -0.1), (-0.1, -0.1), (-0.1, -0.4)]
        hole2 = [(0.1, 0.1), (0.1, 0.4), (0.4, 0.4), (0.4, 0.1)]
        sec = SDSection(
            name="SD",
            shape="SD Section",
            material="CONC",
            polygons=[("C", outer), ("V", hole1), ("V", hole2)],
        )
        kind, value = section_profile_outline(sec)
        assert kind == "regions"
        assert len(value) == 1
        outer_pts, holes = value[0]
        assert len(outer_pts) == 4
        assert len(holes) == 2

    def test_sd_section_with_two_disjoint_outers(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        left = [(-0.4, -0.1), (-0.4, 0.1), (-0.2, 0.1), (-0.2, -0.1)]
        right = [(0.2, -0.1), (0.2, 0.1), (0.4, 0.1), (0.4, -0.1)]
        sec = SDSection(
            name="SD",
            shape="SD Section",
            material="CONC",
            polygons=[("C", left), ("C", right)],
        )
        kind, value = section_profile_outline(sec)
        assert kind == "regions"
        assert len(value) == 2
        for outer_pts, holes in value:
            assert len(outer_pts) == 4
            assert holes == []

    def test_sd_section_with_no_polygons_falls_back_to_a_square(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_profile_outline

        sec = SDSection(name="SD", shape="SD Section", material="CONC", A=0.25)
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

    def test_concrete_rect_is_four_open_sides(self):
        from fea_toolkit.model.sap_data import ConcreteRectangularSection
        from fea_toolkit.model.section_profiles import section_plates

        sec = ConcreteRectangularSection("CB", "Rectangular", "CONC", depth=0.4, bf=0.2)
        plates = section_plates(sec)
        assert len(plates) == 4
        assert all(len(poly) == 2 for poly in plates)
        xs = [p[0] for poly in plates for p in poly]
        ys = [p[1] for poly in plates for p in poly]
        assert max(xs) == 0.1 and min(xs) == -0.1  # breadth 0.2
        assert max(ys) == 0.2 and min(ys) == -0.2  # depth 0.4

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

    def test_sd_section_is_its_perimeter_edges(self):
        from fea_toolkit.model.sap_data import SDSection
        from fea_toolkit.model.section_profiles import section_plates

        sec = SDSection(
            name="SD",
            shape="SD Section",
            material="CONC",
            polygons=[("CONC", [(-0.2, -0.1), (-0.2, 0.1), (0.2, 0.1), (0.2, -0.1)])],
        )
        plates = section_plates(sec)
        assert len(plates) == 4
        assert all(len(poly) == 2 for poly in plates)
