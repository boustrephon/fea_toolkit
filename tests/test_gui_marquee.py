"""Screen-space marquee geometry predicates (Qt- and VTK-free)."""

from fea_toolkit.gui.controllers.marquee import (
    point_in_rect,
    polygon_hits_rect,
    segment_hits_rect,
)


def test_point_inside_and_on_the_edge():
    assert point_in_rect(5, 5, 0, 0, 10, 10) is True
    assert point_in_rect(0, 10, 0, 0, 10, 10) is True  # corner is inside


def test_point_outside():
    assert point_in_rect(11, 5, 0, 0, 10, 10) is False
    assert point_in_rect(5, -1, 0, 0, 10, 10) is False


def test_point_normalises_reversed_corners():
    assert point_in_rect(3, 4, 10, 10, 0, 0) is True


def test_segment_with_an_endpoint_inside_hits():
    assert segment_hits_rect(5, 5, 20, 20, 0, 0, 10, 10) is True


def test_segment_spanning_the_box_with_both_ends_out_hits():
    """A vertical column crossing the box must be selected, even though its
    endpoints are both outside."""
    assert segment_hits_rect(5, -20, 5, 20, 0, 0, 10, 10) is True


def test_segment_skirting_the_box_misses():
    assert segment_hits_rect(-5, -5, 15, -5, 0, 0, 10, 10) is False


def test_segment_crossing_a_single_edge_hits():
    assert segment_hits_rect(-5, 5, 5, 5, 0, 0, 10, 10) is True


def test_collinear_segment_along_the_edge_hits():
    assert segment_hits_rect(-1, 0, 11, 0, 0, 0, 10, 10) is True


def test_polygon_hits_by_vertex_or_edge():
    # a quad with one vertex inside
    assert polygon_hits_rect([(5, 5), (20, 5), (20, 20), (5, 20)], 0, 0, 10, 10) is True
    # a quad whose edge crosses the box (no vertex inside)
    assert polygon_hits_rect([(5, -5), (15, 5), (15, -5), (5, -5)], 0, 0, 10, 10) is True


def test_polygon_fully_outside_misses():
    assert polygon_hits_rect([(20, 20), (30, 20), (30, 30), (20, 30)], 0, 0, 10, 10) is False
