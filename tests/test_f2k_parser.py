"""Tests for the SAFE ``.f2k`` parser (:mod:`fea_toolkit.io.f2k_parser`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fea_toolkit.io.f2k_parser import SAFEF2KParser
from fea_toolkit.io.model_loader import load_model_data
from fea_toolkit.model.sap_data import (
    ConcreteRectangularSection,
    DeckSection,
    ShellSection,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.f2k"

# Real SAFE models (client data — never committed).  Optional spot-checks that
# skip when the files are absent.
_REAL_F2K = (
    "/Users/andrew/Library/Mobile Documents/com~apple~CloudDocs/Work/"
    "Reference/Analysis/CSi SAFE/SAFE Model/Trial v1.0.f2k"
)


@pytest.fixture(scope="module")
def model():
    return SAFEF2KParser(FIXTURE).parse().get_model_data()


def test_program_units(model):
    assert model.units == {"F": "kN", "L": "m", "T": "C"}


def test_nodes_from_points(model):
    assert len(model.nodes) == 8
    assert model.nodes["1"].x == 0.0 and model.nodes["1"].z == 0.0
    assert model.nodes["6"].z == 3.0


def test_floor_and_wall_areas(model):
    assert len(model.area_elements) == 2
    assert model.area_elements["100"].node_ids == ["1", "2", "3", "4"]
    assert model.area_roles == {"100": "floor", "200": "wall"}


def test_multi_record_polygon_merge(tmp_path):
    """A floor polygon with >4 corners spans two ``Order`` continuation rows.

    Regression: the parser used to read only ``UniquePt1..4`` and let the
    ``Order=2`` row overwrite the first, leaving 1–2 node slivers where the
    column-zone pentagon/hexagon slabs should be.
    """
    src = tmp_path / "polygon.f2k"
    src.write_text(
        "File polygon.f2k was saved on 1/1/24 at 12:00:00\n"
        'TABLE:  "FLOOR OBJECT CONNECTIVITY"\n'
        '   "Unique Name"=1   UniquePt1=1   UniquePt2=2   UniquePt3=3   UniquePt4=4'
        "   Perimeter=16   Area=16   Order=1\n"
        '   "Unique Name"=1   UniquePt1=5   UniquePt2=6   Order=2\n'
        'TABLE:  "WALL OBJECT CONNECTIVITY"\n'
        "   UniqueName=10   UniquePt1=1   UniquePt2=6   UniquePt3=5   UniquePt4=4"
        "   Perimeter=14   Area=12\n",
        encoding="utf-8",
    )
    md = SAFEF2KParser(src).parse().get_model_data()
    assert md.area_elements["1"].node_ids == ["1", "2", "3", "4", "5", "6"]
    assert md.area_elements["10"].node_ids == ["1", "6", "5", "4"]
    assert md.area_roles == {"1": "floor", "10": "wall"}


def test_null_lines_become_frames(model):
    assert set(model.frame_elements) == {"10", "11"}
    assert model.frame_elements["10"].node_i == "1"
    assert model.frame_elements["10"].node_j == "2"


def test_slab_wall_deck_sections(model):
    slab = model.sections["200mm SLAB"]
    assert isinstance(slab, ShellSection) and slab.thickness == pytest.approx(0.2)
    wall = model.sections["250mm WALL"]
    assert isinstance(wall, ShellSection) and wall.thickness == pytest.approx(0.25)
    deck = model.sections["Deck1"]
    assert isinstance(deck, DeckSection)
    assert deck.deck_material == "S355" and deck.deck_depth == pytest.approx(0.075)


def test_frame_section(model):
    beam = model.sections["ConcBm"]
    assert isinstance(beam, ConcreteRectangularSection)
    assert beam.depth == pytest.approx(0.5) and beam.bf == pytest.approx(0.375)


def test_material_properties(model):
    mat = model.materials["C35/45"]
    assert mat.type == "Concrete"
    assert mat.E_mod == pytest.approx(26399999.999999996)
    assert mat.Fc == pytest.approx(34999.99999999999)


def test_frame_assignments_skip_none(model):
    assert model.frame_assignments == {"10": "ConcBm"}
    assert model.area_assignments == {"100": "200mm SLAB", "200": "250mm WALL"}


def test_springs(model):
    assert list(model.point_springs) == ["X-dir Side Friction"]
    assert model.point_spring_assignments == {"1": "X-dir Side Friction"}
    assert list(model.line_springs) == ["LSpr1"]
    assert model.line_spring_assignments == {"11": "LSpr1"}
    assert model.line_springs["LSpr1"].vertical_stiffness == pytest.approx(1500)
    assert list(model.area_springs) == ["ASpr1"]
    assert model.area_spring_assignments == {"100": "ASpr1"}
    assert model.area_springs["ASpr1"].stiffness["U3"] == pytest.approx(1500)


def test_tendons_and_strips(model):
    assert model.tendons["Tendon1"].strand_area == pytest.approx(9.870948e-05)
    assert list(model.design_strips) == ["CSA1"]
    assert model.design_strips["CSA1"].layer == "A"
    assert len(model.design_strips["CSA1"].segments) == 1


def test_loads_cases_combos(model):
    assert len(model.area_uniform_loads) == 1
    load = model.area_uniform_loads[0]
    assert load.pattern == "Live" and load.area_id == "100" and load.value == 5.0
    assert set(model.load_cases) == {"Dead", "Live"}
    assert len(model.load_combinations["Comb1"].entries) == 2


def test_floor_mesh_options(model):
    assert model.floor_mesh_options["mesh_option"] == "General"
    assert model.floor_mesh_options["max_size"] == pytest.approx(0.25)
    assert model.floor_mesh_options["per_area"]["100"] == {
        "mesh_option": "General",
        "add_restraints": False,
    }
    assert model.floor_mesh_options["wall_per_area"]["200"] == {
        "mesh_option": "Auto Rectangular",
        "add_restraints": True,
    }
    assert model.floor_mesh_options["auto_edge_constraints"] == {"100": True, "200": True}
    assert model.floor_mesh_options["include_in_mesh"] == {"1": True, "2": False}


def test_model_loader_routes_f2k(tmp_path):
    target = tmp_path / "copy.f2k"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    md = load_model_data(target)
    assert len(md.nodes) == 8
    assert len(md.area_elements) == 2


def test_table_coverage_clean():
    coverage = SAFEF2KParser(FIXTURE).parse().table_coverage()
    assert coverage.clean
    assert "FLOOR OBJECT CONNECTIVITY" in coverage.handled


def test_codec_round_trip(model):
    from fea_toolkit.io.model_codec import dict_to_model, model_to_dict

    restored = dict_to_model(model_to_dict(model))
    assert restored.nodes == model.nodes
    assert restored.sections == model.sections  # DeckSection round-trips exactly
    assert restored.area_roles == model.area_roles
    assert restored.point_springs == model.point_springs
    assert restored.design_strips == model.design_strips
    assert restored.floor_mesh_options == model.floor_mesh_options


@pytest.mark.skipif(not Path(_REAL_F2K).exists(), reason="real SAFE model not present")
def test_real_f2k_loads():
    md = SAFEF2KParser(_REAL_F2K).parse().get_model_data()
    assert len(md.nodes) > 0
    assert len(md.area_elements) > 0
    assert md.floor_mesh_options.get("max_size") is not None
