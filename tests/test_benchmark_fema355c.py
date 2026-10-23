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


def test_3story_ew_moment_frame_centered_and_rotated():
    """E-W moment frames sit at grids 2-5 (bays 2-3/3-4/4-5), strong-axis E-W.

    The moment-connected girders on grid lines A and E occupy bays 2→3, 3→4,
    4→5; the released gravity beams occupy 1→2, 5→6, 6→7.  The moment-frame
    columns (A2-A5, E2-E5) and the east gravity columns (A6, E6) are rotated
    90° (strong-axis E-W); A6/E6 carry the gravity-column section.  The N-S
    leaning columns E1/E7 are also rotated 90° (weak-axis in the N-S frame).
    """
    md = f.build_fema355c("LA", 3, "pre", "3d")
    # Moment bays (girder, no release) on grid A at level 02F.
    for eid in ("B-A2-A3-02F", "B-A3-A4-02F", "B-A4-A5-02F"):
        assert eid in md.frame_elements
        assert eid not in md.frame_releases
        assert md.frame_assignments[eid] == "W33X118"  # girder, level 1
    # Gravity bays (released) on grid A at level 02F.
    for eid in ("B-A1-A2-02F", "B-A5-A6-02F", "B-A6-A7-02F"):
        assert eid in md.frame_releases
    # Moment-frame columns rotated 90° (strong-axis E-W).
    for col in ("C-A2-01F", "C-A3-01F", "C-A4-01F", "C-A5-01F", "C-E2-01F", "C-E5-01F"):
        assert md.frame_elements[col].angle == 90.0
        assert md.frame_assignments[col] == "W14X311"  # interior perimeter column
    # A6/E6 are gravity columns (W14X68), also rotated 90°.
    for col in ("C-A6-01F", "C-E6-01F"):
        assert md.frame_elements[col].angle == 90.0
        assert md.frame_assignments[col] == "W14X68"
    # Corner columns: A1/A7 (moment end of the N-S frames) stay unrotated; the
    # leaning columns E1/E7 are rotated 90° (weak-axis in the N-S frame).
    for col in ("C-A1-01F", "C-A7-01F"):
        assert md.frame_elements[col].angle == 0.0
    for col in ("C-E1-01F", "C-E7-01F"):
        assert md.frame_elements[col].angle == 90.0
        assert md.frame_assignments[col] == "W14X257"  # exterior column section


def test_9story_corner_pinned_beams_and_rotated_columns():
    """9-story LA/Boston: last beam (clockwise) pinned at the corner; E-W cols rotated.

    On the north face (grid F) bay 1-2 is pinned at the F1 end and bay 5-6 is
    moment-connected; on the west face (grid 1) bay A-B is pinned at the A1 end
    and bay E-F is moment-connected.  The E-W frame columns (A1-A5, F2-F6) are
    rotated 90° (strong-axis E-W).
    """
    md = f.build_fema355c("LA", 9, "pre", "3d")
    # Grid F (north face).
    assert "B-F5-F6-01F" not in md.frame_releases  # moment-connected
    rel = md.frame_releases["B-F1-F2-01F"]
    assert rel.end_i[5] == 1 and rel.end_j[5] == 0  # M3 released at the F1 end
    # Grid 1 (west face).
    assert "B-E1-F1-01F" not in md.frame_releases  # moment-connected
    rel = md.frame_releases["B-A1-B1-01F"]
    assert rel.end_i[5] == 1 and rel.end_j[5] == 0  # M3 released at the A1 end
    # E-W frame columns rotated 90° (strong-axis E-W).
    for col in ("C-A1-01F", "C-A5-01F", "C-F2-01F", "C-F6-01F"):
        assert md.frame_elements[col].angle == 90.0
    # Corner columns moment-connected to the N-S frames stay unrotated.
    for col in ("C-A6-01F", "C-F1-01F"):
        assert md.frame_elements[col].angle == 0.0


def test_20story_ew_frame_columns_rotated():
    """20-story E-W face columns A2-A6 / F2-F6 are rotated 90° (strong-axis E-W)."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    for col in ("C-A2-01F", "C-A6-01F", "C-F2-01F", "C-F6-01F"):
        assert md.frame_elements[col].angle == 90.0
    # Corner columns stay unrotated (strong-axis N-S).
    for col in ("C-A1-01F", "C-A7-01F", "C-F1-01F", "C-F7-01F"):
        assert md.frame_elements[col].angle == 0.0


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


@pytest.mark.parametrize("region,stories,design", f.schedules())
def test_every_schedule_builds(region, stories, design):
    """Every encoded schedule builds in both directions with non-empty models."""
    for direction in ("2d", "3d"):
        md = f.build_fema355c(region, stories, design, direction)
        assert md.nodes
        assert md.frame_elements
        assert md.sections


def test_schedules_lists_the_22_variants_in_display_order():
    """``schedules()`` is the chooser's source of truth: 22 triples, stable order."""
    triples = f.schedules()
    assert len(triples) == 22
    assert len(set(triples)) == 22
    assert triples[0] == ("LA", 3, "pre")  # region, then stories, then design
    assert triples[1] == ("LA", 3, "post")
    assert ("SE", 9, "rbs") in triples
    assert ("LA", 9, "r2") in triples
    assert ("BO", 20, "post") in triples


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


# ── Location-based identifiers ────────────────────────────────────────────


def test_2d_node_ids_are_grid_and_level():
    """2D node IDs are ``{grid}-{level}``: A-01F at the base, E-RF at the roof."""
    md = f.build_fema355c("LA", 3, "pre", "2d")
    assert "A-01F" in md.nodes
    assert md.nodes["A-01F"].z == 0.0
    assert "E-RF" in md.nodes
    assert md.nodes["E-RF"].z == pytest.approx(3 * f.H13)


def test_3d_node_ids_carry_both_grid_lines():
    """3D node IDs are ``{NS}{EW}-{level}`` (e.g. A1-01F, E7-RF)."""
    md = f.build_fema355c("LA", 3, "pre", "3d")
    assert "A1-01F" in md.nodes
    assert md.nodes["A1-01F"].x == 0.0 and md.nodes["A1-01F"].y == 0.0
    assert "E7-RF" in md.nodes


def test_basement_levels_label_deepest_first():
    """20-story labels the basements 02B (deepest) and 01B, ground 01F."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    assert md.nodes["A1-02B"].z == 0.0
    assert md.nodes["A1-01B"].z == pytest.approx(f.H12)
    assert md.nodes["A1-01F"].z == pytest.approx(2 * f.H12)


def test_frame_and_panel_ids_are_location_based():
    """Columns/beams/panels are keyed off their grid lines and level."""
    md2 = f.build_fema355c("LA", 3, "pre", "2d")
    assert "C-A-01F" in md2.frame_elements  # column at grid A, story 01F→02F
    assert "B-A-B-02F" in md2.frame_elements  # beam A–B at level 02F
    md3 = f.build_fema355c("LA", 3, "pre", "3d")
    assert "P-A1-02F" in md3.area_elements  # floor panel at bay A1, level 02F


def test_integer_tags_stay_sequential():
    """The OpenSees ``*_tag`` integers are unchanged: dense 1..N per entity."""
    md = f.build_fema355c("LA", 20, "pre", "3d")
    node_tags = sorted(n.node_tag for n in md.nodes.values())
    assert node_tags == list(range(1, len(md.nodes) + 1))
    elem_tags = sorted(e.elem_tag for e in md.frame_elements.values())
    assert elem_tags == list(range(1, len(md.frame_elements) + 1))
    area_tags = sorted(a.area_tag for a in md.area_elements.values())
    assert area_tags == list(range(1, len(md.area_elements) + 1))


def test_boston_3_2d_gravity_column_is_w12x58():
    """BO 3 2D gravity bay uses the "others" gravity column, not a moment column."""
    for design, moment_col in (("pre", "W14X99"), ("post", "W14X145")):
        md = f.build_fema355c("BO", 3, design, "2d")
        secs = {
            md.frame_assignments[e.elem_id]
            for e in md.frame_elements.values()
            if e.node_i == "E-01F"
        }
        assert secs == {"W12X58"}
        assert moment_col not in secs


def test_2d_gravity_column_prefers_the_others_tier():
    """The 2D gravity bay resolves gravity_col_other (LA/BO) or gravity_col (SE)."""
    la = f.build_fema355c("LA", 3, "pre", "2d")
    la_secs = {
        la.frame_assignments[e.elem_id] for e in la.frame_elements.values() if e.node_i == "E-01F"
    }
    assert la_secs == {"W14X68"}  # "others"; the below-penthouse W14X82 is no longer used

    se = f.build_fema355c("SE", 3, "pre", "2d")
    se_secs = {
        se.frame_assignments[e.elem_id] for e in se.frame_elements.values() if e.node_i == "E-01F"
    }
    assert se_secs == {"W10X77"}  # single gravity group (no "others" tier)

    r3 = f.build_fema355c("LA", 9, "r3", "2d")
    r3_secs = {
        r3.frame_assignments[e.elem_id]
        for e in r3.frame_elements.values()
        if e.node_i in ("E-01B", "F-01B")
    }
    assert r3_secs == {"W14X193"}  # "others", not gravity_col (W14X211)

    r2 = f.build_fema355c("LA", 9, "r2", "2d")
    r2_secs = {
        r2.frame_assignments[e.elem_id] for e in r2.frame_elements.values() if e.node_i == "K-01B"
    }
    assert r2_secs == {"W14X193"}  # "others", not gravity_col (W14X211)
