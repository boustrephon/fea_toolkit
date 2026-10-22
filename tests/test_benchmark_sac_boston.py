"""FEMA 355C Boston benchmark models — build, codec round-trip, gravity-bay release."""

import pytest

from fea_toolkit.benchmarks import sac_boston as f
from fea_toolkit.io.model_codec import json_to_model, model_to_json


@pytest.mark.parametrize(
    "builder,nodes,elements,releases",
    [
        (f.boston3_2d, 20, 27, 3),
        (f.boston3_3d, 140, 279, 6),
        (f.boston9_2d, 66, 110, 10),
        (f.boston9_3d, 396, 960, 20),
    ],
)
def test_model_counts(builder, nodes, elements, releases):
    md = builder()
    assert len(md.nodes) == nodes
    assert len(md.frame_elements) == elements
    assert len(md.frame_releases) == releases


@pytest.mark.parametrize("builder", [f.boston3_2d, f.boston3_3d, f.boston9_2d, f.boston9_3d])
def test_codec_roundtrip(builder):
    md = builder()
    rt = json_to_model(model_to_json(md))
    assert len(rt.nodes) == len(md.nodes)
    assert len(rt.frame_elements) == len(md.frame_elements)
    assert len(rt.frame_releases) == len(md.frame_releases)


def test_gravity_bay_released_for_major_moment():
    """The gravity-bay beam is released for M3 at its J (gravity-column) end."""
    md = f.boston3_2d()
    assert md.frame_releases
    for rel in md.frame_releases.values():
        assert rel.end_j[5] == 1  # M3 released at J
        assert not any(rel.end_i)  # I end fully connected
        assert rel.released_labels("J") == ["M3"]


def test_3story_geometry_matches_benchmark():
    """4 bays x 3 stories: 20 nodes / 27 elements (matches struct3.m)."""
    md = f.boston3_2d()
    xs = {n.x for n in md.nodes.values()}
    zs = {n.z for n in md.nodes.values()}
    assert len(xs) == 5  # 5 column lines A-E
    assert len(zs) == 4  # 4 levels
    assert max(xs) == pytest.approx(4 * f.BAY)
    assert max(zs) == pytest.approx(3 * f.H13)


def test_9story_has_basement_and_ground():
    """Basement (pinned) + ground (restrained) + 8 stories @ 13 ft."""
    md = f.boston9_2d()
    zs = sorted({n.z for n in md.nodes.values()})
    assert zs[0] == 0.0  # B-1 base
    assert zs[1] == pytest.approx(f.H12)  # ground
    assert zs[2] == pytest.approx(f.H12 + f.H18)  # 1st floor
    assert len(zs) == 11  # B-1 .. 9th (roof)


def test_3story_moment_frame_columns():
    """Grids A/D exterior (W14x74), B/C interior (W14x99), E gravity (W12x58)."""
    md = f.boston3_2d()
    # Columns are built line-major: line i's 3 stories occupy tags 3*i+1..3*i+3,
    # so the ground story (j=0) of each line is tag 3*i+1.
    ground_cols = {3 * i + 1: md.frame_assignments[str(3 * i + 1)] for i in range(5)}
    assert ground_cols == {
        1: "W14X74",
        4: "W14X99",
        7: "W14X99",
        10: "W14X74",
        13: "W12X58",
    }


def test_snapshots_writable(tmp_path):
    paths = f.write_snapshots(dest=tmp_path)
    assert len(paths) == 4
    assert all(p.exists() for p in paths)


def test_seismic_mass_lumping_2d_matches_benchmark():
    """L1 = 4.7835e5 kg/half-building -> edge 59793.75, interior 119587.5 kg."""
    masses = [4.7835e5, 4.7835e5, 5.1779e5]
    md = f.boston3_2d(masses=masses)
    edge = 4.7835e5 / 8  # half-bay tributary over 4 bays
    interior = 4.7835e5 / 4  # full-bay tributary over 4 bays
    l1 = {k: v for k, v in md.added_masses.items() if md.nodes[k].z == pytest.approx(f.H13)}
    assert sorted(l1.values()) == pytest.approx([edge, edge, interior, interior, interior])
    assert sum(md.added_masses.values()) == pytest.approx(sum(masses))


def test_seismic_mass_lumping_3d_conserves_total():
    masses = [4.7835e5, 4.7835e5, 5.1779e5]
    md = f.boston3_3d(masses=masses)
    assert sum(md.added_masses.values()) == pytest.approx(sum(masses))


def test_default_seismic_masses_are_fema355c_appendix_b():
    """Default masses are the FEMA 355C Appendix B values (whole building)."""
    md3 = f.boston3_3d()
    assert sum(md3.added_masses.values()) == pytest.approx(sum(f.MASS_3STORY))
    md9 = f.boston9_3d()
    assert sum(md9.added_masses.values()) == pytest.approx(sum(f.MASS_9STORY))
    # 2D frames carry half the building (one perimeter frame).
    assert sum(f.boston3_2d().added_masses.values()) == pytest.approx(sum(f.MASS_3STORY) / 2.0)
    assert sum(f.boston9_2d().added_masses.values()) == pytest.approx(sum(f.MASS_9STORY) / 2.0)


# ── Gravity loads (DEAD / LIVE nodal loads) ───────────────────────────────


def _level_loads(md, z):
    """Return (dead_total, live_total) fz at nodes whose elevation == z."""
    dead = live = 0.0
    for jl in md.joint_loads:
        if md.nodes[jl.node_id].z != pytest.approx(z):
            continue
        if jl.pattern == "DEAD":
            dead += jl.fz
        elif jl.pattern == "LIVE":
            live += jl.fz
    return dead, live


def test_gravity_loads_2d_totals_match_fema_pressures():
    """One perimeter frame carries half the building; DEAD roof uses 83 psf."""
    md = f.boston3_2d()
    area = (4 * f.BAY) * (6 * f.BAY / 2.0)  # frame tributary area (m²)
    dead1, live1 = _level_loads(md, f.H13)  # floor
    dead3, live3 = _level_loads(md, 3 * f.H13)  # roof
    assert dead1 == pytest.approx(-f.DEAD_FLOOR_PA * area)
    assert live1 == pytest.approx(-f.LIVE_PA * area)
    assert dead3 == pytest.approx(-f.DEAD_ROOF_PA * area)
    assert live3 == pytest.approx(-f.LIVE_PA * area)


def test_gravity_loads_3d_totals_match_fema_pressures():
    """Full building carries the whole footprint."""
    md = f.boston3_3d()
    area = (4 * f.BAY) * (6 * f.BAY)  # full plan area (m²)
    dead1, live1 = _level_loads(md, f.H13)
    assert dead1 == pytest.approx(-f.DEAD_FLOOR_PA * area)
    assert live1 == pytest.approx(-f.LIVE_PA * area)


@pytest.mark.parametrize("builder", [f.boston3_2d, f.boston3_3d, f.boston9_2d, f.boston9_3d])
def test_gravity_loads_use_dead_and_live_patterns_downward(builder):
    md = builder()
    assert {jl.pattern for jl in md.joint_loads} == {"DEAD", "LIVE"}
    assert all(jl.fz < 0 for jl in md.joint_loads)  # downward (global -Z)
    assert all(jl.fx == 0 and jl.fy == 0 for jl in md.joint_loads)


def test_gravity_load_tributary_2d_edge_is_half_interior():
    """At a floor level, edge columns carry half the interior tributary."""
    md = f.boston3_2d()
    level1 = [jl for jl in md.joint_loads if md.nodes[jl.node_id].z == pytest.approx(f.H13)]
    dead_by_node = {}
    for jl in level1:
        if jl.pattern == "DEAD":
            dead_by_node[jl.node_id] = jl.fz
    xs = {nid: md.nodes[nid].x for nid in dead_by_node}
    edge = min(dead_by_node, key=lambda n: xs[n])
    interior = max(dead_by_node, key=lambda n: abs(dead_by_node[n]))
    assert abs(dead_by_node[edge]) == pytest.approx(abs(dead_by_node[interior]) / 2.0)


# ── Floor panels (loads-only visualisation surfaces, 3D) ─────────────────


def test_floor_panels_3d_per_bay_per_level():
    md = f.boston3_3d()
    assert len(md.area_elements) == 4 * 6 * 3  # one panel per bay per level
    assert md.area_assignments == {}  # loads-only: no section -> no shell
    for ae in md.area_elements.values():
        assert len(ae.node_ids) == 4
        zs = {md.nodes[n].z for n in ae.node_ids}
        assert len(zs) == 1  # each panel is flat (a single floor level)


def test_floor_panels_9story_count():
    md = f.boston9_3d()
    assert len(md.area_elements) == 5 * 5 * 10


def test_floor_panels_2d_are_absent():
    assert f.boston3_2d().area_elements == {}
    assert f.boston9_2d().area_elements == {}


def test_new_fields_roundtrip():
    md = f.boston3_3d()
    rt = json_to_model(model_to_json(md))
    assert len(rt.joint_loads) == len(md.joint_loads)
    assert len(rt.area_elements) == len(md.area_elements)
    assert rt.area_elements["F1"].node_ids == md.area_elements["F1"].node_ids
