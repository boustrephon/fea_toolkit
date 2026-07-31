"""Tests for rebar material support in RC fibre sections.

Covers the three-level Steel02 rebar resolution used by both the
``AnalysisBuilder`` and the ``export_mesh_model_to_tcl`` Tcl export:

1. **Config override** — ``rebar_Fy_override`` / ``rebar_Es_override``
   are authored in SI (Pa) and scaled to model units.
2. **SAP2000 rebar lookup** — ``ConcreteRectangularSection.rebar_material``
   is looked up in the model materials; its ``Fy`` / ``E_mod`` are used
   in model units.
3. **Framework defaults** — ``DEFAULT_FY_REBAR_PA`` / ``DEFAULT_E_S_PA``
   scaled to model units.

Also verifies the S2K parser reads the rebar tables (``REBAR SIZES``,
``FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN``) into section fields.
"""

from typing import Dict

import pytest

from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    Node,
    Material,
    ConcreteRectangularSection,
    FrameElement,
    DEFAULT_FY_REBAR_PA,
    DEFAULT_E_S_PA,
)
from fea_toolkit.utils import stress_scale_factor


# ═══════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════

_MODEL_UNITS = {"F": "N", "L": "m", "T": "C"}

_REBAR_NAME = "A615Gr60"


def _make_mesh(rebar_material: str) -> MeshModel:
    """Minimal single-column MeshModel with an RC fibre section."""
    materials: Dict[str, Material] = {
        "CONC": Material(
            name="CONC", type="Concrete",
            Fc=4.0e7, eFc=5.2e7, E_mod=3.0e10,
        ),
        _REBAR_NAME: Material(
            name=_REBAR_NAME, type="Rebar",
            Fy=413685.0, E_mod=199947978.8,
        ),
    }
    sections = {
        "RC_COL": ConcreteRectangularSection(
            name="RC_COL", shape="Rectangular", material="CONC",
            depth=0.3, bf=0.3, cover=0.04,
            top_bars=4, bot_bars=4,
            top_bar_dia=0.0286, bot_bar_dia=0.0286,
            rebar_material=rebar_material or None,
        ),
    }
    nodes = {
        "N1": Node(node_id="N1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "N2": Node(node_id="N2", node_tag=2, x=0.0, y=0.0, z=3.0),
    }
    frames = {"E1": FrameElement(elem_id="E1", node_i="N1", node_j="N2", elem_tag=1)}
    return MeshModel(
        nodes=nodes,
        frame_elements=frames,
        frame_assignments={"E1": "RC_COL"},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        materials=materials,
        sections=sections,
        material_tags={"CONC": 1, _REBAR_NAME: 2},
        section_tags={"RC_COL": 3},
        units=_MODEL_UNITS,
    )


def _export_tcl(mm: MeshModel, tmp_path, config: dict) -> str:
    """Export a MeshModel to Tcl and return the file contents."""
    from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl

    tcl_path = str(tmp_path / "rebar_test.tcl")
    cfg = {"create_fiber_sections": True}
    cfg.update(config)
    export_mesh_model_to_tcl(mm, tcl_path, config=cfg)
    with open(tcl_path, "r") as f:
        return f.read()


def _steel02_fy_es(content: str) -> tuple:
    """Extract (Fy, Es) from the first Steel02 line in Tcl output."""
    for line in content.splitlines():
        if "uniaxialMaterial Steel02" in line:
            parts = line.split()
            # uniaxialMaterial Steel02 tag Fy Es b R0 cR1 cR2
            return float(parts[3]), float(parts[4])
    raise AssertionError("No 'uniaxialMaterial Steel02' line in Tcl output")


# ═══════════════════════════════════════════════════════════════
# Parser: rebar tables -> section fields
# ═══════════════════════════════════════════════════════════════

class TestParserRebarTables:
    """S2K rebar tables are parsed into section and material data."""

    _S2K = """\
TABLE: "PROGRAM CONTROL"
   CurrUnits="N, m, C"
TABLE: "MATERIAL PROPERTIES 01 - GENERAL"
   Material=CONC   Type="Concrete"
   Material=A615Gr60   Type="Rebar"
TABLE: "MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES"
   Material=CONC   E1=30000000
   Material=A615Gr60   E1=199947978.8
TABLE: "FRAME SECTION PROPERTIES 01 - GENERAL"
   SectionName=RC_COL   Shape=Rectangular   Material=CONC   Area=0.09   I33=0.000675   I22=0.000675   t3=0.3   t2=0.3
TABLE: "FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN"
   SectionName=RC_COL   RebarMatL=A615Gr60   BarSizeL="#9"   Cover=0.04
TABLE: "REBAR SIZES"
   RebarID="#9"   Diameter=0.0286
"""

    def test_parser_reads_rebar_tables(self, tmp_path):
        """RebarMatL wired to section, diameter resolved from REBAR SIZES."""
        from fea_toolkit.io.s2k_parser import SAP2000Parser

        s2k_path = tmp_path / "rebar_col.s2k"
        s2k_path.write_text(self._S2K)

        parser = SAP2000Parser(str(s2k_path))
        parser.parse()
        md = parser.get_model_data()

        sec = md.sections["RC_COL"]
        assert isinstance(sec, ConcreteRectangularSection)
        assert sec.rebar_material == "A615Gr60", \
            f"Expected RebarMatL wired, got {sec.rebar_material!r}"
        assert sec.top_bar_dia == pytest.approx(0.0286), \
            f"Top bar diameter from REBAR SIZES, got {sec.top_bar_dia}"
        assert sec.bot_bar_dia == pytest.approx(0.0286), \
            f"Bot bar diameter from REBAR SIZES, got {sec.bot_bar_dia}"
        assert "A615Gr60" in md.materials, \
            "Rebar material missing from model materials"


# ═══════════════════════════════════════════════════════════════
# Tcl export: rebar material resolution
# ═══════════════════════════════════════════════════════════════

class TestTclRebarResolution:
    """Steel02 rebar Fy/Es resolution in export_mesh_model_to_tcl."""

    def test_rebar_material_lookup(self, tmp_path):
        """Section rebar_material is looked up; Fy/E_mod pass through."""
        content = _export_tcl(_make_mesh(_REBAR_NAME), tmp_path, {})
        fy, es = _steel02_fy_es(content)
        assert fy == pytest.approx(413685.0)
        assert es == pytest.approx(199947978.8)

    def test_fallback_to_framework_defaults(self, tmp_path):
        """No rebar material -> scaled DEFAULT_FY_REBAR_PA / DEFAULT_E_S_PA."""
        content = _export_tcl(_make_mesh(""), tmp_path, {})
        fy, es = _steel02_fy_es(content)
        ssf = stress_scale_factor(_MODEL_UNITS)
        assert fy == pytest.approx(DEFAULT_FY_REBAR_PA * ssf)
        assert es == pytest.approx(DEFAULT_E_S_PA * ssf)
        # N/m model: scale factor is 1.0, so defaults land at 4e8 / 2e11
        assert fy == pytest.approx(4.0e8)
        assert es == pytest.approx(2.0e11)

    def test_config_override_precedes_lookup(self, tmp_path):
        """rebar_Fy_override / rebar_Es_override (Pa) win over lookup."""
        content = _export_tcl(
            _make_mesh(_REBAR_NAME),
            tmp_path,
            {"rebar_Fy_override": 500.0e6, "rebar_Es_override": 210.0e9},
        )
        fy, es = _steel02_fy_es(content)
        assert fy == pytest.approx(500.0e6)
        assert es == pytest.approx(210.0e9)