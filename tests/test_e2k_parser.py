"""Tests for the ETABS ``.E2K`` / ``.$ET`` parser (M0–M4).

Covers the tokeniser / section dispatch and the deferred-geometry resolution:
storey elevations, ``(point, storey)`` node materialisation, line/area
connectivity with storey drops, composite instance ids and integer tags, plus
the diaphragm-constraint wiring (``$ DIAPHRAGM NAMES`` + ``DIAPH`` assigns).

The fixture ``tests/fixtures/clean_model.e2k`` is a minimal two-storey model:
two columns (drop 1), one beam (drop 0) and one 4-corner slab.  The companion
``tests/fixtures/diaphragm_model.e2k`` adds rigid / semi-rigid / disconnected
diaphragm assignments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fea_toolkit.io.e2k_parser import (
    ETABSParser,
    _compose_id,
    _find_profile,
    _gather,
    _get_diaphragm_names,
    _get_frame_sections,
    _get_materials,
    _line_split,
    _load_catalogues,
    _normalise_catalogue_key,
    _parse_sections,
    _split_id,
)
from fea_toolkit.model.sap_data import (
    AngleSection,
    AreaUniformLoad,
    BoxSection,
    ChannelSection,
    CircularSection,
    ConcreteCircularSection,
    ConcreteRectangularSection,
    Constraint,
    DoubleAngleSection,
    FrameDistributedLoad,
    GeneralSection,
    ISection,
    JointLoad,
    LoadCombination,
    PipeSection,
    ShellSection,
    TeeSection,
)

FIXTURE = Path(__file__).parent / "fixtures" / "clean_model.e2k"
DIAPHRAGM_FIXTURE = Path(__file__).parent / "fixtures" / "diaphragm_model.e2k"


# ── Tokeniser ───────────────────────────────────────────────────────


def test_line_split_preserves_quoted_strings():
    tokens = _line_split('  POINTASSIGN  "1"  "BASE"  RESTRAINT "UX UY UZ"  ')
    assert tokens == ["POINTASSIGN", '"1"', '"BASE"', "RESTRAINT", '"UX UY UZ"']


def test_gather_groups_keywords_with_values():
    groups = _gather(_line_split('LINE  "C1"  COLUMN  "1"  "1"  1'))
    assert groups == [
        ("LINE", ["C1"]),
        ("COLUMN", ["1", "1", 1]),
    ]


def test_gather_quoted_ids_stay_strings():
    groups = _gather(_line_split('POINT "1"  0 0'))
    assert groups == [("POINT", ["1", 0, 0])]


def test_parse_sections_ignores_log_and_file_header():
    sections = _parse_sections(
        "$ File C:\\x.EDB saved 1/1/24 12:00:00\n"
        "$ CONTROLS\n"
        '  UNITS  "KN"  "M"\n'
        "$ LOG\n"
        "  ETABS junk text\n"
        "$ END OF MODEL FILE\n"
    )
    assert set(sections) == {"CONTROLS"}
    assert sections["CONTROLS"] == [[("UNITS", ["KN", "M"])]]


# ── Composite ids ───────────────────────────────────────────────────


def test_compose_id_round_trips():
    for name, storey in [("B1", "ROOF"), ("1", "BASE"), ("a@b", "c\\d")]:
        assert _split_id(_compose_id(name, storey)) == (name, storey)


def test_split_id_rejects_malformed():
    with pytest.raises(ValueError):
        _split_id("no-separator")


# ── Geometry resolution ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def model():
    return ETABSParser(FIXTURE).parse().get_model_data()


def test_units(model):
    assert model.units == {"F": "KN", "L": "M", "T": "C"}


def test_storey_elevations(model):
    # Two stories: ROOF (height 3) above BASE (elev 0).
    assert model.nodes["1@BASE"].z == pytest.approx(0.0)
    assert model.nodes["1@ROOF"].z == pytest.approx(3.0)


def test_nodes_materialised(model):
    # 2 assigned base points + 4 roof-level points (via elements) = 6 nodes.
    assert len(model.nodes) == 6
    assert "1@BASE" in model.nodes
    assert "2@BASE" in model.nodes
    assert "1@ROOF" in model.nodes
    assert "2@ROOF" in model.nodes
    assert "3@ROOF" in model.nodes
    assert "4@ROOF" in model.nodes


def test_node_tags_are_sequential_and_unique(model):
    tags = [n.node_tag for n in model.nodes.values()]
    assert sorted(tags) == list(range(1, len(tags) + 1))


def test_column_drop_spans_two_storeys(model):
    c = model.frame_elements["C1@ROOF"]
    assert c.node_i == "1@BASE"
    assert c.node_j == "1@ROOF"


def test_beam_stays_on_one_storey(model):
    b = model.frame_elements["B1@ROOF"]
    assert b.node_i == "1@ROOF"
    assert b.node_j == "2@ROOF"


def test_frame_section_assignments(model):
    assert model.frame_assignments["C1@ROOF"] == "Col"
    assert model.frame_assignments["B1@ROOF"] == "Beam"


def test_area_element_materialised(model):
    assert len(model.area_elements) == 1
    area = model.area_elements["S1@ROOF"]
    assert area.node_ids == ["1@ROOF", "2@ROOF", "3@ROOF", "4@ROOF"]
    assert model.area_assignments["S1@ROOF"] == "Slab"


def test_get_model_data_requires_parse():
    parser = ETABSParser(FIXTURE)
    with pytest.raises(RuntimeError):
        parser.get_model_data()


# ── M2: materials, sections, restraints ─────────────────────────────


def test_materials_merged_across_lines(model):
    steel = model.materials["STEEL"]
    assert steel.type == "Steel"
    assert steel.E_mod == pytest.approx(2.0e8)
    assert steel.nu == pytest.approx(0.3)
    assert steel.Fy == pytest.approx(345000)
    assert steel.Fu == pytest.approx(448000)
    assert steel.G_mod > 0  # derived from E and nu

    conc = model.materials["CONC"]
    assert conc.type == "Concrete"
    assert conc.Fc == pytest.approx(28000)
    assert conc.E_mod == pytest.approx(2.5e7)


def test_concrete_frame_sections(model):
    col = model.sections["Col"]
    assert isinstance(col, ConcreteRectangularSection)
    assert col.depth == pytest.approx(0.4)
    assert col.bf == pytest.approx(0.4)
    assert col.cover == pytest.approx(0.04)
    assert col.material == "CONC"
    # Derived elastic properties from D × B (E2K omits Area / I33 / I22 / J).
    assert pytest.approx(0.4 * 0.4) == col.A
    assert pytest.approx(0.4 * 0.4**3 / 12) == col.I33
    assert pytest.approx(0.4 * 0.4**3 / 12) == col.I22
    assert pytest.approx(0.4 * 0.4**2 / 6) == col.Z33
    assert col.J > 0  # solid-rectangle torsional constant

    beam = model.sections["Beam"]
    assert isinstance(beam, ConcreteRectangularSection)
    assert beam.depth == pytest.approx(0.5)
    assert beam.bf == pytest.approx(0.3)
    assert pytest.approx(0.5 * 0.3) == beam.A
    assert pytest.approx(0.3 * 0.5**3 / 12) == beam.I33  # strong axis
    assert pytest.approx(0.5 * 0.3**3 / 12) == beam.I22  # weak axis
    assert beam.J > 0


def test_solid_rect_props_square_torsion():
    from fea_toolkit.io.e2k_parser import _solid_rect_props

    props = _solid_rect_props(0.4, 0.4)
    assert props["A"] == pytest.approx(0.16)
    assert props["I33"] == pytest.approx(props["I22"])
    # Exact square torsional constant ≈ 0.1406·a⁴ (within 0.2 %).
    assert props["J"] == pytest.approx(0.1406 * 0.4**4, rel=3e-3)


def test_shell_section_and_area_thickness(model):
    slab = model.sections["Slab"]
    assert isinstance(slab, ShellSection)
    assert slab.thickness == pytest.approx(0.2)
    # Area thickness is populated from the assigned shell section.
    assert model.area_elements["S1@ROOF"].thickness == pytest.approx(0.2)


def test_restraints_from_point_assigns(model):
    assert model.restraints["1@BASE"].dofs == [1, 1, 1, 0, 0, 0]
    assert model.restraints["2@BASE"].dofs == [1, 1, 1, 0, 0, 0]


def test_profile_section_placeholder_without_catalogue(tmp_path):
    parser = ETABSParser(FIXTURE, section_db_path=tmp_path / "missing.pkl")
    md = parser.parse().get_model_data()
    assert isinstance(md.sections["W4X13"], GeneralSection)
    assert md.sections["W4X13"].shape == "W4X13"


def test_profile_section_resolved_with_catalogue():
    db_path = Path(__file__).parent.parent / "data" / "section_dict.pkl"
    if not db_path.exists():
        pytest.skip("Section database not found")
    md = ETABSParser(FIXTURE, section_db_path=db_path).parse().get_model_data()
    w = md.sections["W4X13"]
    assert isinstance(w, ISection)
    assert w.depth == pytest.approx(4.16 * 0.0254, rel=1e-4)  # 4.16 in → m
    assert w.material == "STEEL"
    assert w.A > 0


# ── M2 hardening: parametric shape dispatch & FILE-scoped catalogue ──

_SECTIONS_SNIPPET = """$ MATERIAL PROPERTIES
  MATERIAL  "STEEL"  M 7.85  W 77.0  TYPE "ISOTROPIC"  E 2.0E+08  U 0.3  A 1.17E-05
  MATERIAL  "STEEL"  DESIGNTYPE "STEEL"  FY 345000  FU 448000
  MATERIAL  "CONC"  M 2.4  W 23.5  TYPE "ISOTROPIC"  E 2.5E+07  U 0.2  A 9.9E-06
  MATERIAL  "CONC"  DESIGNTYPE "CONCRETE"  FC 28000

$ FRAME SECTIONS
  FRAMESECTION  "BeamI"  MATERIAL "STEEL"  SHAPE "I/Wide Flange"  D 0.3  B 0.2  TF 0.01  TW 0.006
  FRAMESECTION  "Chan"   MATERIAL "STEEL"  SHAPE "Channel"        D 0.3  B 0.15 TF 0.01  TW 0.008
  FRAMESECTION  "Pipe"   MATERIAL "STEEL"  SHAPE "Pipe"           D 0.3  T 0.01
  FRAMESECTION  "Box"    MATERIAL "STEEL"  SHAPE "Box/Tube"       D 0.2  B 0.2  TF 0.01  TW 0.01
  FRAMESECTION  "Ang"    MATERIAL "STEEL"  SHAPE "Angle"          D 0.1  B 0.1  TF 0.008 TW 0.008
  FRAMESECTION  "Dang"   MATERIAL "STEEL"  SHAPE "Double Angle"   D 0.1  B 0.1  TF 0.008 TW 0.008 DIS 0.01
  FRAMESECTION  "Tee"    MATERIAL "STEEL"  SHAPE "Tee"            D 0.2  B 0.2  TF 0.01  TW 0.01
  FRAMESECTION  "Rod"    MATERIAL "STEEL"  SHAPE "Circle"         D 0.2
  FRAMESECTION  "ColC"   MATERIAL "CONC"   SHAPE "Concrete Circular"  D 0.4
"""


@pytest.fixture(scope="module")
def parametric_sections():
    sections = _parse_sections(_SECTIONS_SNIPPET)
    materials = _get_materials(sections)
    return _get_frame_sections(sections, materials, "m", {})


def test_parametric_dispatch_produces_typed_sections(parametric_sections):
    assert isinstance(parametric_sections["BeamI"], ISection)
    assert isinstance(parametric_sections["Chan"], ChannelSection)
    assert isinstance(parametric_sections["Pipe"], PipeSection)
    assert isinstance(parametric_sections["Box"], BoxSection)
    assert isinstance(parametric_sections["Ang"], AngleSection)
    assert isinstance(parametric_sections["Dang"], DoubleAngleSection)
    assert isinstance(parametric_sections["Tee"], TeeSection)
    assert isinstance(parametric_sections["Rod"], CircularSection)
    assert isinstance(parametric_sections["ColC"], ConcreteCircularSection)


def test_parametric_i_section_properties(parametric_sections):
    sec = parametric_sections["BeamI"]
    assert sec.depth == pytest.approx(0.3)
    assert sec.bf == pytest.approx(0.2)
    assert sec.tf == pytest.approx(0.01)
    assert sec.tw == pytest.approx(0.006)
    assert sec.A > 0
    assert sec.I33 > sec.I22  # strong axis > weak axis


def test_parametric_pipe_maps_d_and_t(parametric_sections):
    sec = parametric_sections["Pipe"]
    assert sec.od == pytest.approx(0.3)
    assert sec.t == pytest.approx(0.01)
    assert sec.A > 0


def test_parametric_concrete_circle(parametric_sections):
    sec = parametric_sections["ColC"]
    assert isinstance(sec, ConcreteCircularSection)
    assert sec.diameter == pytest.approx(0.4)


def test_general_section_reads_explicit_properties():
    snippet = (
        "$ MATERIAL PROPERTIES\n"
        '  MATERIAL  "STEEL"  M 7.85  W 77.0  TYPE "ISOTROPIC"  E 2.0E+08  U 0.3  A 1.17E-05\n'
        '  MATERIAL  "STEEL"  DESIGNTYPE "STEEL"  FY 345000  FU 448000\n'
        "$ FRAME SECTIONS\n"
        '  FRAMESECTION  "Gen"  MATERIAL "STEEL"  SHAPE "General"  AREA 0.06  I33 1.0E-04  I22 2.0E-04  TORSION 3.0E-04  Z33 4.0E-03  Z22 5.0E-03\n'
    )
    sections = _parse_sections(snippet)
    materials = _get_materials(sections)
    sec = _get_frame_sections(sections, materials, "m", {})["Gen"]
    assert isinstance(sec, GeneralSection)
    assert pytest.approx(0.06) == sec.A
    assert pytest.approx(1.0e-4) == sec.I33
    assert pytest.approx(2.0e-4) == sec.I22
    assert pytest.approx(3.0e-4) == sec.J
    assert pytest.approx(4.0e-3) == sec.Z33
    assert pytest.approx(5.0e-3) == sec.Z22


def test_normalise_catalogue_key_strips_edition():
    assert _normalise_catalogue_key("AISC14") == "aisc"
    assert _normalise_catalogue_key("AISC") == "aisc"
    assert _normalise_catalogue_key("CAT_B") == "cat_b"


def test_find_profile_scopes_by_file():
    catalogues = {
        "CAT_A": {"SECTIONS": {"S1": {"SECTION_TYPE": "STEEL_I_SECTION", "A": 1.0}}},
        "CAT_B": {"SECTIONS": {"S1": {"SECTION_TYPE": "STEEL_PIPE", "A": 2.0}}},
    }
    cat_data, props = _find_profile(catalogues, "S1", file_name="CAT_B.PRO")
    assert cat_data is catalogues["CAT_B"]
    assert props["A"] == 2.0
    # Without a FILE it falls back to the first catalogue that holds the name.
    cat_data, props = _find_profile(catalogues, "S1")
    assert cat_data is catalogues["CAT_A"]


def test_file_scoped_resolution_through_parser(tmp_path):
    import pickle

    catalogues = {
        "CAT_A": {
            "SECTIONS": {
                "S1": {
                    "SECTION_TYPE": "STEEL_I_SECTION",
                    "D": 0.1,
                    "BF": 0.1,
                    "TF": 0.01,
                    "TW": 0.005,
                    "A": 1.0e-3,
                    "I33": 1.0e-6,
                    "I22": 1.0e-7,
                    "J": 1.0e-8,
                    "UNITS": "m",
                },
            }
        },
        "CAT_B": {
            "SECTIONS": {
                "S1": {
                    "SECTION_TYPE": "STEEL_PIPE",
                    "OD": 0.2,
                    "TDES": 0.01,
                    "A": 6.0e-3,
                    "I33": 3.0e-5,
                    "I22": 3.0e-5,
                    "J": 6.0e-5,
                    "UNITS": "m",
                },
            }
        },
    }
    db = tmp_path / "sections.pkl"
    with open(db, "wb") as fh:
        pickle.dump(catalogues, fh)

    snippet = (
        "$ MATERIAL PROPERTIES\n"
        '  MATERIAL  "STEEL"  M 7.85  W 77.0  TYPE "ISOTROPIC"  E 2.0E+08  U 0.3  A 1.17E-05\n'
        '  MATERIAL  "STEEL"  DESIGNTYPE "STEEL"  FY 345000  FU 448000\n'
        "$ FRAME SECTIONS\n"
        '  FRAMESECTION  "S1"  MATERIAL "STEEL"  SHAPE "S1"  FILE "CAT_B.PRO"\n'
    )
    sections = _parse_sections(snippet)
    materials = _get_materials(sections)
    got = _get_frame_sections(sections, materials, "m", _load_catalogues(db))
    assert isinstance(got["S1"], PipeSection)  # CAT_B wins over CAT_A
    assert got["S1"].od == pytest.approx(0.2)


# ── M3: loads & combinations ────────────────────────────────────────


def test_load_cases_and_patterns(model):
    dead = model.load_patterns["DEAD"]
    assert dead.pattern_type == "DEAD"
    assert dead.self_weight_factor == pytest.approx(1.0)

    live = model.load_patterns["LIVE"]
    assert live.pattern_type == "LIVE"
    assert live.self_weight_factor == pytest.approx(0.0)

    assert model.load_cases["DEAD"].design_type == "DEAD"
    assert model.load_cases["DEAD"].case_type == "Linear Static"

    wind = model.load_patterns["WX"]
    assert wind.pattern_type == "WIND"
    assert wind.auto_data["WIND"]["code"] == "EUROCODE1 2005"
    assert wind.auto_data["WIND"]["VB"] == 35


def test_load_combinations(model):
    combo = model.load_combinations["DCON1"]
    assert isinstance(combo, LoadCombination)
    assert combo.combo_type == "Linear Add"
    assert combo.design["Design"] == "CONCRETE"
    assert [(e.name, e.factor, e.kind) for e in combo.entries] == [
        ("DEAD", 1.4, "case"),
        ("LIVE", 1.6, "case"),
    ]


def test_area_uniform_loads(model):
    dead = [load for load in model.area_uniform_loads if load.pattern == "DEAD"]
    assert len(dead) == 1
    assert isinstance(dead[0], AreaUniformLoad)
    assert dead[0].area_id == "S1@ROOF"
    assert dead[0].direction == "Gravity"
    assert dead[0].value == pytest.approx(1.8)


def test_joint_loads(model):
    loads = model.joint_loads
    assert len(loads) == 1
    assert isinstance(loads[0], JointLoad)
    assert loads[0].node_id == "3@ROOF"
    assert loads[0].pattern == "LIVE"
    assert loads[0].fx == pytest.approx(10)
    assert loads[0].fz == pytest.approx(-5)


def test_frame_distributed_loads(model):
    loads = {load.pattern: load for load in model.frame_dist_loads}
    assert set(loads) == {"DEAD", "LIVE"}

    uniform = loads["DEAD"]
    assert isinstance(uniform, FrameDistributedLoad)
    assert uniform.frame_id == "B1@ROOF"
    assert uniform.shape == "Uniform"
    assert uniform.val_a == pytest.approx(0.7)

    linear = loads["LIVE"]
    assert linear.shape == "Linear"  # RDSTART 0, RDEND 1
    assert linear.val_a == pytest.approx(1.0)
    assert linear.val_b == pytest.approx(2.0)
    assert linear.direction == "Gravity"


# ── M4: diaphragm constraints ───────────────────────────────────────


def test_get_diaphragm_names():
    sections = _parse_sections(
        '$ DIAPHRAGM NAMES\n  DIAPHRAGM "D1"    TYPE RIGID\n  DIAPHRAGM "D2"    TYPE SEMIRIGID\n'
    )
    assert _get_diaphragm_names(sections) == {"D1": "RIGID", "D2": "SEMIRIGID"}


def test_no_diaphragms_yield_empty_constraints(model):
    """A model with no DIAPHRAGM NAMES / DIAPH carries no constraints."""
    assert model.constraints == {}
    assert model.constraint_assignments == {}


@pytest.fixture(scope="module")
def diaphragm_model():
    return ETABSParser(DIAPHRAGM_FIXTURE).parse().get_model_data()


def test_rigid_diaphragm_constraint_definition(diaphragm_model):
    con = diaphragm_model.constraints["D1@ROOF"]
    assert isinstance(con, Constraint)
    assert con.constraint_type == "DIAPHRAGM"
    assert con.coord_sys == "GLOBAL"
    assert con.constraint_data["Axis"] == "Z"
    assert con.constraint_data["Rigidity"] == "RIGID"
    assert con.constraint_data["Diaphragm"] == "D1"


def test_rigid_diaphragm_assignments_exclude_disconnected(diaphragm_model):
    assigned = {nid for nid, c in diaphragm_model.constraint_assignments.items() if c == "D1@ROOF"}
    # All roof nodes except point 5 (DIAPH "DISCONNECTED") participate.
    assert assigned == {"1@ROOF", "2@ROOF", "3@ROOF", "4@ROOF"}
    assert "5@ROOF" in diaphragm_model.nodes
    assert "5@ROOF" not in assigned


def test_semirigid_diaphragm_records_constraint_without_assignments(diaphragm_model):
    con = diaphragm_model.constraints["D2@BASE"]
    assert con.constraint_type == "DIAPHRAGM"
    assert con.constraint_data["Rigidity"] == "SEMIRIGID"
    # SEMIRIGID defines a grouping, not a rigid MPC — no joint assignments.
    assert "D2@BASE" not in diaphragm_model.constraint_assignments.values()


# ── Cardinal point (insertion) + frame angle (matches S2K) ───────────


def test_frame_angle_and_cardinal_point(diaphragm_model):
    c1 = diaphragm_model.frame_elements["C1@ROOF"]
    assert c1.angle == pytest.approx(0.0)
    assert c1.cardinal_point == 10  # default centroid when absent

    c2 = diaphragm_model.frame_elements["C2@ROOF"]
    assert c2.angle == pytest.approx(30.0)

    b1 = diaphragm_model.frame_elements["B1@ROOF"]
    assert b1.cardinal_point == 8  # top centre


def test_cardinal_point_offset_becomes_frame_end_offset(diaphragm_model):
    # Beam 0.5 × 0.3 with CARDINALPT 8 (top centre) → off_y = -D/2 = -0.25.
    off = diaphragm_model.frame_end_offsets["B1@ROOF"]
    assert off.off_y_i == pytest.approx(-0.25)
    assert off.off_y_j == pytest.approx(-0.25)
    assert off.off_z_i == pytest.approx(0.0)
    assert off.off_z_j == pytest.approx(0.0)

    # Column C1 (default centroid) yields no offset record.
    assert "C1@ROOF" not in diaphragm_model.frame_end_offsets
