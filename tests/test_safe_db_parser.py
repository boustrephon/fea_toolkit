"""Tests for the SAFE ``.$sf`` native-database parser.

Covers :mod:`fea_toolkit.io.safe_db_parser`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fea_toolkit.io.model_loader import load_model_data
from fea_toolkit.io.safe_db_parser import SAFEDBParser
from fea_toolkit.model.sap_data import ConcreteRectangularSection, ShellSection

FIXTURE = Path(__file__).parent / "fixtures" / "sample.$sf"

_REAL_SF = (
    "/Users/andrew/Library/Mobile Documents/com~apple~CloudDocs/Work/"
    "Reference/Analysis/CSi SAFE/SAFE Model/B3.$sf"
)


@pytest.fixture(scope="module")
def model():
    return SAFEDBParser(FIXTURE).parse().get_model_data()


def test_program_units(model):
    assert model.units == {"F": "N", "L": "mm", "T": "C"}


def test_nodes(model):
    assert len(model.nodes) == 8
    assert model.nodes["1"].x == 0.0 and model.nodes["2"].x == 4000.0


def test_multi_line_area_polygon_merged(model):
    assert len(model.area_elements) == 3
    assert model.area_elements["1"].node_ids == ["1", "2", "3", "4"]
    # Area 3 spans two physical rows (8 corners) and must be merged.
    assert model.area_elements["3"].node_ids == ["1", "2", "3", "4", "8", "7", "6", "5"]


def test_openings(model):
    assert model.area_elements["3"].opening is True
    assert model.area_elements["1"].opening is False


def test_area_assignments(model):
    assert model.area_assignments == {"1": "SLAB2", "2": "D675"}


def test_sections(model):
    slab = model.sections["SLAB2"]
    assert isinstance(slab, ShellSection) and slab.thickness == 250.0
    drop = model.sections["D675"]
    assert isinstance(drop, ShellSection) and drop.thickness == 675.0
    wall = model.sections["BW800"]
    assert isinstance(wall, ShellSection) and wall.thickness == 800.0
    beam = model.sections["1500x675"]
    assert isinstance(beam, ConcreteRectangularSection)
    assert beam.depth == 675.0 and beam.bf == 1500.0
    assert pytest.approx(675.0 * 1500.0) == beam.A


def test_materials(model):
    conc = model.materials["C45"]
    assert conc.E_mod == pytest.approx(26400) and conc.Fc == pytest.approx(45)
    rebar = model.materials["CSA-G30.18Gr400"]
    assert rebar.Fy == pytest.approx(400)


def test_frames_and_beams(model):
    assert set(model.frame_elements) == {"10", "20"}
    assert model.frame_assignments == {"10": "1500x675"}


def test_restraints(model):
    assert set(model.restraints) == {"4", "5"}
    assert model.restraints["4"].dofs == [1, 1, 1, 0, 0, 0]


def test_springs(model):
    assert list(model.point_springs) == ["PSPR1"]
    assert list(model.line_springs) == ["SUPPORT"]
    assert model.line_spring_assignments == {"20": "SUPPORT"}
    assert model.line_springs["SUPPORT"].vertical_stiffness == pytest.approx(1e7)


def test_tendons_strips_punching(model):
    assert model.tendons["TENDON1"].strand_area == pytest.approx(98.70948)
    strip = model.design_strips["CSA1"]
    assert [end["side"] for end in strip.ends] == ["A", "B"]
    assert strip.ends[0]["width_left"] == pytest.approx(500)
    assert "4" in model.punching_shear
    assert model.punching_shear["4"]["Check"] == "Program Determined"


def test_loads(model):
    assert len(model.area_uniform_loads) == 1
    assert model.area_uniform_loads[0].value == pytest.approx(0.005)
    assert len(model.frame_dist_loads) == 1
    assert model.frame_dist_loads[0].val_a == pytest.approx(39)
    assert len(model.joint_loads) == 1
    assert model.joint_loads[0].fz == pytest.approx(-50780)


def test_load_cases_and_combos(model):
    assert set(model.load_cases) == {"DL", "LL"}
    assert model.load_cases["DL"].case_type == "LinStatic"
    assert len(model.load_combinations["GRAVITY ULS"].entries) == 2


def test_groups(model):
    assert set(model.groups) == {"All", "BP OUTLINE"}
    assert model.groups["BP OUTLINE"].objects == ["Line:10"]


def test_floor_mesh_options(model):
    assert model.floor_mesh_options["mesh_option"] == "Rectangular"
    assert model.floor_mesh_options["max_size"] == pytest.approx(1200)


def test_model_loader_routes_sf(tmp_path):
    target = tmp_path / "copy.$sf"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    md = load_model_data(target)
    assert len(md.nodes) == 8
    assert len(md.area_elements) == 3


def test_table_coverage_clean():
    coverage = SAFEDBParser(FIXTURE).parse().table_coverage()
    assert coverage.clean
    assert "OBJECT GEOMETRY - AREAS 01 - GENERAL" in coverage.handled


def test_codec_round_trip(model):
    from fea_toolkit.io.model_codec import dict_to_model, model_to_dict

    restored = dict_to_model(model_to_dict(model))
    assert restored.nodes == model.nodes
    assert restored.sections == model.sections
    assert restored.design_strips == model.design_strips  # includes the new ends field
    assert restored.punching_shear == model.punching_shear
    assert restored.floor_mesh_options == model.floor_mesh_options


@pytest.mark.skipif(not Path(_REAL_SF).exists(), reason="real SAFE model not present")
def test_real_sf_loads():
    md = SAFEDBParser(_REAL_SF).parse().get_model_data()
    assert len(md.nodes) > 0
    assert len(md.area_elements) > 0
    assert len(md.design_strips) > 0
    assert len(md.punching_shear) > 0
