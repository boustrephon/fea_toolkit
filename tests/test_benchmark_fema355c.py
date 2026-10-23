"""FEMA 355C unified generator — LA schedules, geometry, 4+2 gravity columns."""

import pytest

from fea_toolkit.benchmarks import fema355c as f
from fea_toolkit.io.model_codec import json_to_model, model_to_json
from fea_toolkit.model.sap_data import BoxSection


@pytest.mark.parametrize(
    "stories,direction,nodes,elements,releases",
    [
        (3, "2d", 20, 27, 3),
        (3, "3d", 140, 279, 24),
        (9, "2d", 66, 110, 10),
        (9, "3d", 396, 960, 40),
        (20, "2d", 138, 242, 0),
        (20, "3d", 966, 2178, 0),
    ],
)
def test_la_model_counts(stories, direction, nodes, elements, releases):
    md = f.build_fema355c("LA", stories, "pre", direction)
    assert len(md.nodes) == nodes
    assert len(md.frame_elements) == elements
    assert len(md.frame_releases) == releases


@pytest.mark.parametrize(
    "stories,direction",
    [(3, "2d"), (3, "3d"), (9, "2d"), (9, "3d"), (20, "2d"), (20, "3d")],
)
def test_la_codec_roundtrip(stories, direction):
    md = f.build_fema355c("LA", stories, "pre", direction)
    rt = json_to_model(model_to_json(md))
    assert len(rt.nodes) == len(md.nodes)
    assert len(rt.frame_elements) == len(md.frame_elements)
    assert len(rt.frame_releases) == len(md.frame_releases)


def test_20story_box_corner_columns():
    """LA 20 has built-up 15x15 box columns at the four corners."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    boxes = [s for s in md.sections.values() if isinstance(s, BoxSection)]
    assert boxes
    assert all(s.name.startswith("15X15") for s in boxes)


def test_20story_gravity_columns_are_4_plus_2():
    """The six penthouse columns split 4 corner (W14X550…) + 2 middle (W21X50…)."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    bay = f.BAY_20
    # Locate the story-1 columns by base-node position (penthouse C3-D5 =
    # NS lines 2-3, EW lines 2-4).
    base = {}
    for nid, nd in md.nodes.items():
        if nd.z == 0.0:
            base[(round(nd.x / bay), round(nd.y / bay))] = nid
    col_at = {}
    for eid, el in md.frame_elements.items():
        if el.node_i in base.values():
            pos = next(p for p, nid in base.items() if nid == el.node_i)
            col_at[pos] = md.frame_assignments[eid]
    assert col_at[(2, 2)] == "W14X550"  # C3 corner
    assert col_at[(2, 4)] == "W14X550"  # C5 corner
    assert col_at[(3, 2)] == "W14X550"  # D3 corner
    assert col_at[(3, 4)] == "W14X550"  # D5 corner
    assert col_at[(2, 3)] == "W21X50"  # C4 middle
    assert col_at[(3, 3)] == "W21X50"  # D4 middle


def test_20story_no_interior_columns_outside_penthouse():
    """20-story interior is column-free outside the penthouse footprint."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    bay = f.BAY_20
    base = {}
    for nid, nd in md.nodes.items():
        if nd.z == 0.0:
            base[(round(nd.x / bay), round(nd.y / bay))] = nid
    for (i, k), nid in base.items():
        # interior (not on the perimeter): only the penthouse footprint has columns
        on_perim = i in (0, 5) or k in (0, 6)
        in_pent = 2 <= i <= 3 and 2 <= k <= 4
        has_col = any(e.node_i == nid for e in md.frame_elements.values())
        if not on_perim:
            assert has_col == in_pent


def test_w14x108_typo_fixed():
    """The source's W14X108 is emitted as W14X109 (standard AISC section)."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    assert "W14X109" in md.sections
    assert "W14X108" not in md.sections


def test_3story_geometry_matches_benchmark():
    """4 bays x 3 stories @ 30 ft / 13 ft (matches struct3.m)."""
    md = f.build_fema355c("LA", 3, "pre", "2d")
    xs = {n.x for n in md.nodes.values()}
    zs = {n.z for n in md.nodes.values()}
    assert len(xs) == 5
    assert len(zs) == 4
    assert max(xs) == pytest.approx(4 * f.BAY_30)
    assert max(zs) == pytest.approx(3 * f.H13)


def test_20story_geometry_matches_benchmark():
    """5 bays x 22 levels @ 20 ft; heights 12/12/18 ft then 13 ft x 19."""
    md = f.build_fema355c("LA", 20, "pre", "2d")
    zs = sorted({n.z for n in md.nodes.values()})
    assert len(zs) == 23
    assert zs[1] == pytest.approx(f.H12)
    assert zs[2] == pytest.approx(2 * f.H12)
    assert zs[3] == pytest.approx(2 * f.H12 + f.H18)
    assert max(zs) == pytest.approx(2 * f.H12 + f.H18 + 19 * f.H13)
    assert max(n.x for n in md.nodes.values()) == pytest.approx(5 * f.BAY_20)


# ── Full registry coverage (LA / Seattle / Boston, incl. redesigns) ──


@pytest.mark.parametrize("region,stories,design", sorted(f._SCHEDULES))
def test_every_schedule_builds(region, stories, design):
    """Every encoded schedule builds in both directions with non-empty models."""
    for direction in ("2d", "3d"):
        md = f.build_fema355c(region, stories, design, direction)
        assert md.nodes
        assert md.frame_elements
        assert md.sections


def test_boston_20_has_next_to_exterior_columns():
    """Boston 20 uses a third (W36) column tier next to the corner columns."""
    md = f.build_fema355c("BO", 20, "pre", "3d")
    sections = set(md.frame_assignments.values())
    assert "W36X194" in sections  # next-to-exterior (pre)
    assert "W36X260" in sections  # interior
    assert "W14X370" in sections  # exterior corner


def test_seattle_9_pinned_both_ends():
    """Seattle 9 has the short bay pinned at both ends (4-bay arrangement)."""
    md = f.build_fema355c("SE", 9, "pre", "3d")
    both = [r for r in md.frame_releases.values() if any(r.end_i)]
    assert both
    for rel in both:
        assert rel.end_i[5] == 1  # M3 released at I
        assert rel.end_j[5] == 1  # M3 released at J


def test_seattle_20_single_gravity_column_group():
    """Seattle 20 uses one (not 4+2) gravity column group below the penthouse."""
    md = f.build_fema355c("SE", 20, "pre", "3d")
    assert "W14X426" in md.sections  # single gravity column group (pre)


def test_r2_redesign_15ft_bays():
    """R2-LA9 uses 15 ft bays: 10 x 10 bays, 9 moment bays per face."""
    md = f.build_fema355c("LA", 9, "r2", "3d")
    xs = {n.x for n in md.nodes.values()}
    ys = {n.y for n in md.nodes.values()}
    assert len(xs) == 11  # 10 bays + 1
    assert len(ys) == 11
    assert max(xs) == pytest.approx(10 * f.BAY_15)


def test_r3_redesign_three_moment_bays():
    """R3-LA9 keeps 5 bays but only 3 moment bays per face (2 gravity bays)."""
    md = f.build_fema355c("LA", 9, "r3", "2d")
    # 5 bays, 6 column lines; the last 2 bays are gravity (released).
    releases = list(md.frame_releases.values())
    assert len(releases) == 20  # 2 gravity bays x 10 levels
