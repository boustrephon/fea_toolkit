"""Tests for the SAP2000 model review module (``fea_toolkit.model.review``)."""

from pathlib import Path

import pytest

from fea_toolkit import SAP2000Parser
from fea_toolkit.model import (
    format_review_markdown,
    format_review_report,
    review_model,
    review_s2k_file,
)
from fea_toolkit.model.review import (
    _constraint_summary,
    _format_table,
    _mass_unit_label,
    _reaction_table_rows,
    _response_spectrum_mode_rows,
    _response_spectrum_rows,
    main,
)
from fea_toolkit.model.sap_data import (
    FRAME_RELEASE_DOF_LABELS,
    Constraint,
    FrameElement,
    FrameRelease,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _parse(name: str) -> SAPModelData:
    """Parse a test fixture and return its model data."""
    parser = SAP2000Parser(FIXTURES_DIR / name)
    parser.parse()
    return parser.get_model_data()


def _synthetic(nodes, frames, restraints, assignments=None) -> SAPModelData:
    """Build a minimal SAPModelData around the supplied nodes/frames.

    A concrete ``"S"`` section (backed by material ``"M"``) is defined so
    the default ``"S"`` frame assignment resolves to a real section — the
    integrity review treats assignment values that reference undefined
    sections as blocking.
    """
    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials={"M": Material(name="M", type="Steel", E_mod=2.0e11)},
        sections={"S": Section(name="S", shape="I/Wide Flange", material="M")},
        frame_elements=frames,
        area_elements={},
        frame_assignments=(dict.fromkeys(frames, "S") if assignments is None else assignments),
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )


@pytest.fixture(scope="module")
def sample_review():
    """Review of the ``sample.s2k`` fixture (clean, connected model)."""
    md = _parse("sample.s2k")
    return md, review_model(md, file=FIXTURES_DIR / "sample.s2k")


@pytest.fixture(scope="module")
def clean_model_file():
    """Path to a minimal, purpose-built clean ``.s2k`` fixture.

    ``clean_model.s2k`` is a two-node, fixed-base single frame whose
    material/section are both defined and referenced, so the review finds
    no blocking issues.  It is deliberately independent of ``sample.s2k``
    so CLI exit-code checks do not depend on the larger model's review
    status.
    """
    path = FIXTURES_DIR / "clean_model.s2k"
    assert review_s2k_file(path)["ok"] is True
    return path


# ═══════════════════════════════════════════════════════════════════
# Inventory
# ═══════════════════════════════════════════════════════════════════


class TestInventory:
    def test_sample_counts(self, sample_review):
        _md, result = sample_review
        inv = result["inventory"]
        assert inv["nodes"] == 28
        assert inv["frame_elements"] == 49
        assert inv["restraints"] == 2
        assert inv["area_elements"] == 0
        assert inv["frame_section_assignments"] == 49
        assert result["ok"] is True

    def test_result_has_all_sections(self, sample_review):
        _md, result = sample_review
        for key in (
            "file",
            "units",
            "inventory",
            "breakdown",
            "bounds",
            "connectivity",
            "releases",
            "integrity",
            "observations",
            "analysis",
            "ok",
        ):
            assert key in result
        assert result["analysis"] is None

    def test_breakdown_material_and_section_types(self, sample_review):
        _md, result = sample_review
        breakdown = result["breakdown"]
        assert breakdown["material_types"]
        assert breakdown["section_types"]
        assert breakdown["restraint_dof_patterns"]

    def test_bounds_present(self, sample_review):
        _md, result = sample_review
        bounds = result["bounds"]
        assert bounds is not None
        assert bounds["x_span"] >= 0.0


# ═══════════════════════════════════════════════════════════════════
# Connectivity
# ═══════════════════════════════════════════════════════════════════


class TestConnectivity:
    def test_sample_is_single_connected_component(self, sample_review):
        _md, result = sample_review
        conn = result["connectivity"]
        assert conn["n_components"] == 1
        assert conn["floating_components"] == []
        assert conn["orphan_nodes"] == []

    def test_fixture_with_orphans(self):
        md = _parse("sample_2.s2k")
        result = review_model(md)
        assert len(result["connectivity"]["orphan_nodes"]) == 4
        assert result["connectivity"]["n_components"] == 5
        assert result["ok"] is False

    def test_floating_substructure_detected(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 1.0, 0.0, 0.0),
            "3": Node("3", 3, 0.0, 0.0, 5.0),
            "4": Node("4", 4, 1.0, 0.0, 5.0),
        }
        frames = {
            "1": FrameElement("1", 1, "1", "2"),
            "2": FrameElement("2", 2, "3", "4"),
        }
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        assert result["connectivity"]["n_components"] == 2
        floating = result["connectivity"]["floating_components"]
        assert len(floating) == 1
        assert sorted(floating[0]["nodes"]) == ["3", "4"]
        assert floating[0]["n_frames"] == 1
        assert result["ok"] is False

    def test_orphan_node_detected(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 1.0, 0.0, 0.0),
            "5": Node("5", 5, 9.0, 9.0, 9.0),
        }
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        orphans = result["connectivity"]["orphan_nodes"]
        assert len(orphans) == 1
        assert orphans[0]["node_id"] == "5"
        assert result["ok"] is False

    def test_supported_connected_model_is_clean(self):
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 1.0, 0.0, 0.0),
        }
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        assert result["connectivity"]["n_components"] == 1
        assert result["ok"] is True


# ═══════════════════════════════════════════════════════════════════
# Element releases
# ═══════════════════════════════════════════════════════════════════


class TestReleases:
    def test_frame_release_labels(self):
        rel = FrameRelease(frame_id="7", end_i=[1, 0, 0, 0, 0, 0])
        assert rel.released_labels("I") == ["P"]
        assert rel.released_labels("j") == []
        assert rel.has_releases is True
        assert FRAME_RELEASE_DOF_LABELS == ("P", "V2", "V3", "T", "M2", "M3")

    def test_no_release_by_default(self):
        assert FrameRelease(frame_id="1").has_releases is False

    def test_parser_merges_i_and_j_rows(self):
        parser = SAP2000Parser(FIXTURES_DIR / "sample.s2k")
        parser._raw_tables = {
            "FRAME RELEASE ASSIGNMENTS 1 - GENERAL": [
                {
                    "Frame": 1,
                    "PI": "No",
                    "V2I": "No",
                    "V3I": "No",
                    "TI": "No",
                    "M2I": "Yes",
                    "M3I": "Yes",
                },
                {
                    "Frame": 1,
                    "PJ": False,
                    "V2J": False,
                    "V3J": False,
                    "TJ": False,
                    "M2J": True,
                    "M3J": True,
                },
                {
                    "Frame": 2,
                    "PI": "No",
                    "V2I": "No",
                    "V3I": "No",
                    "TI": "No",
                    "M2I": "No",
                    "M3I": "No",
                    "PJ": "No",
                    "V2J": "No",
                    "V3J": "No",
                    "TJ": "No",
                    "M2J": "No",
                    "M3J": "No",
                },
            ]
        }
        releases = parser._get_frame_releases()
        assert set(releases) == {"1"}  # frame 2 has no releases -> dropped
        rel = releases["1"]
        assert rel.end_i == [0, 0, 0, 0, 1, 1]
        assert rel.end_j == [0, 0, 0, 0, 1, 1]
        assert rel.released_labels("I") == ["M2", "M3"]

    def test_sample_has_no_releases(self, sample_review):
        _md, result = sample_review
        assert result["releases"]["n_frames_with_releases"] == 0


# ═══════════════════════════════════════════════════════════════════
# Integrity
# ═══════════════════════════════════════════════════════════════════


class TestIntegrity:
    def test_missing_node_reference(self):
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0)}
        frames = {"1": FrameElement("1", 1, "1", "99")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        assert result["integrity"]["counts"]["missing_node_refs"] == 1
        assert result["ok"] is False

    def test_zero_length_element(self):
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 0.0, 0.0, 0.0)}
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        assert result["integrity"]["counts"]["zero_length_elements"] == 1
        assert result["ok"] is False

    def test_unassigned_frame(self):
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 1.0, 0.0, 0.0)}
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])}, assignments={})
        result = review_model(md)
        assert result["integrity"]["counts"]["unassigned_frames"] == 1
        assert result["ok"] is False

    def test_duplicate_element(self):
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 1.0, 0.0, 0.0)}
        frames = {
            "1": FrameElement("1", 1, "1", "2"),
            "2": FrameElement("2", 2, "2", "1"),
        }
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        assert result["integrity"]["counts"]["duplicate_elements"] == 1

    def test_counts_key_present(self, sample_review):
        _md, result = sample_review
        assert "counts" in result["integrity"]
        assert set(result["integrity"]["counts"]) >= {
            "missing_node_refs",
            "zero_length_elements",
            "duplicate_elements",
        }


# ═══════════════════════════════════════════════════════════════════
# Observations
# ═══════════════════════════════════════════════════════════════════


class TestObservations:
    def test_translation_only_supports_flagged(self):
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 1.0, 0.0, 0.0)}
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 0, 0, 0])})
        result = review_model(md)
        assert result["observations"]["all_translation_only"] is True
        assert result["observations"]["all_fully_fixed"] is False

    def test_fully_fixed_supports_flagged(self):
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 1.0, 0.0, 0.0)}
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 1, 1, 1])})
        result = review_model(md)
        assert result["observations"]["all_fully_fixed"] is True
        assert result["observations"]["all_translation_only"] is False


# ═══════════════════════════════════════════════════════════════════
# Mass source reporting
# ═══════════════════════════════════════════════════════════════════


class TestMassSource:
    def _model_with_mass_source(self) -> SAPModelData:
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 0.0, 0.0, 3.0)}
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 1, 1, 1])})
        md.mass_sources = {
            "MS": MassSource(
                name="MS",
                elements=True,
                loads=True,
                is_default=True,
                load_pattern={"DEAD": 1.0, "SDL": 0.5},
            )
        }
        return md

    def test_mass_source_patterns_in_observations(self):
        result = review_model(self._model_with_mass_source())
        ms = result["observations"]["mass_source"]
        assert ms["name"] == "MS"
        assert ms["load_patterns"] == {"DEAD": 1.0, "SDL": 0.5}
        assert ms["n_patterns"] == 2

    def test_formatters_report_mass_source_patterns(self):
        result = review_model(self._model_with_mass_source())
        text = format_review_report(result)
        assert "Mass source load patterns: DEAD x1, SDL x0.5" in text
        md_text = format_review_markdown(result)
        assert "Mass source load patterns:" in md_text
        assert "`DEAD` \u00d71" in md_text


# ═══════════════════════════════════════════════════════════════════
# Joint constraints
# ═══════════════════════════════════════════════════════════════════


class TestConstraints:
    def _model_with_constraints(self) -> SAPModelData:
        nodes = {"1": Node("1", 1, 0.0, 0.0, 0.0), "2": Node("2", 2, 0.0, 0.0, 3.0)}
        frames = {"1": FrameElement("1", 1, "1", "2")}
        md = _synthetic(nodes, frames, {"1": Restraint([1, 1, 1, 1, 1, 1])})
        md.constraints = {
            "DZ": Constraint("DZ", "DIAPHRAGM", constraint_data={"Axis": "Z"}),
            "DX": Constraint("DX", "DIAPHRAGM", constraint_data={"Axis": "X"}),
            "B": Constraint("B", "BODY"),
            "E": Constraint("E", "EQUAL"),
        }
        md.constraint_assignments = {"1": "DZ", "2": "DX"}
        return md

    def test_z_axis_diaphragm_supported_x_axis_not(self):
        summary = _constraint_summary(self._model_with_constraints())
        # BODY and Z-axis DIAPHRAGM are supported; X-axis DIAPHRAGM and EQUAL
        # are not.
        assert summary["n_supported"] == 2
        assert summary["by_type"] == {"DIAPHRAGM": 2, "BODY": 1, "EQUAL": 1}
        assert summary["supported"] == {"DIAPHRAGM": 1, "BODY": 1}
        assert [row["name"] for row in summary["unsupported"]] == ["DX", "E"]
        assert all(row["type"] in ("DIAPHRAGM", "EQUAL") for row in summary["unsupported"])

    def test_formatters_report_toolkit_support(self):
        result = review_model(self._model_with_constraints())
        text = format_review_report(result)
        assert "Supported by toolkit" in text
        assert "Applied to OpenSees" not in text
        assert "NOT APPLIED" not in text
        assert "parsed but not supported by the toolkit" in text

        md_text = format_review_markdown(result)
        assert "| Type | Count | Supported by toolkit |" in md_text
        assert "Applied to OpenSees" not in md_text
        assert "not supported** by the toolkit" in md_text
        # The mixed DIAPHRAGM row is reported as partial, not falsely supported.
        assert "| DIAPHRAGM | 2 | Partial |" in md_text
        assert "| BODY | 1 | Supported by toolkit |" in md_text
        assert "| EQUAL | 1 | No |" in md_text


# ═══════════════════════════════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════════════════════════════


class TestFormatters:
    def test_text_report(self, sample_review):
        _md, result = sample_review
        text = format_review_report(result)
        assert "SAP2000 MODEL REVIEW" in text
        assert "Inventory" in text
        assert "Connectivity" in text

    def test_markdown_report(self, sample_review):
        _md, result = sample_review
        md_text = format_review_markdown(result)
        assert md_text.startswith("# SAP2000 Model Review")
        assert "## Inventory" in md_text
        assert "## Connectivity" in md_text


# ═══════════════════════════════════════════════════════════════════
# review_s2k_file + CLI
# ═══════════════════════════════════════════════════════════════════


class TestReviewS2kFileAndCli:
    def test_review_s2k_file(self):
        result = review_s2k_file(FIXTURES_DIR / "sample.s2k")
        assert result["ok"] is True
        assert result["inventory"]["nodes"] == 28
        assert str(result["file"]).endswith("sample.s2k")

    def test_cli_clean_exit_code(self, capsys):
        code = main([str(FIXTURES_DIR / "sample.s2k")])
        out = capsys.readouterr().out
        assert code == 0
        assert "SAP2000 MODEL REVIEW" in out

    def test_cli_issues_exit_code(self):
        code = main([str(FIXTURES_DIR / "sample_2.s2k")])
        assert code == 1

    def test_cli_missing_file(self, capsys):
        code = main([str(FIXTURES_DIR / "does_not_exist.s2k")])
        err = capsys.readouterr().err
        assert code == 2
        assert "not found" in err

    def test_cli_writes_markdown(self, tmp_path):
        out_file = tmp_path / "review.md"
        code = main(
            [
                str(FIXTURES_DIR / "sample.s2k"),
                "--format",
                "markdown",
                "--out",
                str(out_file),
            ]
        )
        assert code == 0
        assert out_file.read_text(encoding="utf-8").startswith("# SAP2000 Model Review")


# ═══════════════════════════════════════════════════════════════════
# Modal display limits (--max-modes / --min-participation)
# ═══════════════════════════════════════════════════════════════════


def _fake_analysis(n_modes: int = 8) -> dict:
    """A synthetic ``analysis`` block — no OpenSees required.

    Mode ``i`` (1-based) carries ``mx = i - 1`` percent, so mode 1 sits at
    0 % participation and every later mode clears a 1 % threshold.  The
    rotational ratios carry distinctive constants (``Rx = 10 + i`` etc.)
    so the 6-DOF columns are unambiguous in the rendered tables.
    """
    return {
        "ok": True,
        "periods": [1.0 / (i + 1) for i in range(n_modes)],
        "mass_participation": [
            {
                "mode": i + 1,
                "period": 1.0 / (i + 1),
                "frequency": float(i + 1),
                "mx": float(i),
                "my": 0.0,
                "mz": 0.0,
                "rx": 10.0 + i,
                "ry": 20.0 + i,
                "rz": 30.0 + i,
            }
            for i in range(n_modes)
        ],
        "static": {
            "summed_reactions": {"fx": 0.0, "fy": 0.0, "fz": -10.0},
            "n_supports": 2,
            "patterns_applied": ["DEAD"],
        },
        "mass_source": {
            "name": "MS",
            "from_elements": True,
            "from_masses": False,
            "from_loads": True,
            "load_patterns": {"DEAD": 1.0, "SDL": 0.5},
            "total_mass": 100.0,
            "total_weight": 980.665,
            "gravity": 9.80665,
            "n_nodes_with_mass": 2,
            "components": {"elements": 60.0, "masses": 0.0, "loads": 40.0},
        },
    }


class TestModeDisplay:
    """Both formatters show every mode by default, and can be limited."""

    @pytest.fixture
    def result(self, sample_review):
        _md, base = sample_review
        out = dict(base)
        out["analysis"] = _fake_analysis(8)
        return out

    def test_text_default_shows_every_mode(self, result):
        text = format_review_report(result)
        assert "0.1250" in text  # mode 8, T = 1/8 s — no longer truncated
        assert "not shown" not in text

    def test_text_includes_rotational_columns(self, result):
        text = format_review_report(result)
        for header in ("Mx (%)", "My (%)", "Mz (%)", "Rx (%)", "Ry (%)", "Rz (%)"):
            assert header in text
        # A final SUM row carries the cumulative participation (all modes).
        assert "SUM" in text
        assert "28.00" in text  # sum(0..7) in the Mx (%) column
        assert "108.00" in text  # sum(10..17) in the Rx (%) column

    def test_markdown_includes_sum_row(self, result):
        md_text = format_review_markdown(result)
        assert "SUM" in md_text
        assert "108.00" in md_text

    def test_text_reactions_table(self, result):
        text = format_review_report(result)
        assert "Summed (all supports)" in text
        # Translational and rotational components carry their own units.
        assert "Fz (" in text and "Mz (" in text

    def test_markdown_reactions_table(self, result):
        md_text = format_review_markdown(result)
        assert "Summed (all supports)" in md_text

    def test_text_mass_source_totals(self, result):
        text = format_review_report(result)
        assert "Seismic mass (mass source 'MS'" in text
        assert "patterns: DEAD x1, SDL x0.5" in text
        assert "100.000" in text  # total_mass
        assert "980.7" in text  # total_weight

    def test_markdown_mass_source_totals(self, result):
        md_text = format_review_markdown(result)
        assert "Seismic mass (mass source **MS**" in md_text
        assert "`DEAD` \u00d71" in md_text
        assert "980.7" in md_text

    def test_text_mass_source_breakdown(self, result):
        """The breakdown shows each component as a mass and a weight."""
        text = format_review_report(result)
        assert "Self-weight (material density)" in text
        assert "Node masses" in text
        assert "Load patterns" in text
        # 60 t x 9.80665 = 588.4 kN, 40 t x 9.80665 = 392.3 kN
        assert "588.4" in text
        assert "392.3" in text

    def test_markdown_mass_source_breakdown(self, result):
        md_text = format_review_markdown(result)
        assert "Self-weight (material density)" in md_text
        assert "588.4" in md_text
        assert "392.3" in md_text

    def test_mass_components_absent_is_graceful(self, result):
        """A mass_source block without ``components`` still renders its total."""
        analysis = dict(result["analysis"])
        mass = dict(analysis["mass_source"])
        mass.pop("components", None)
        analysis["mass_source"] = mass
        out = dict(result)
        out["analysis"] = analysis

        text = format_review_report(out)
        assert "Seismic mass (mass source 'MS'" in text
        assert "Self-weight (material density)" not in text
        md_text = format_review_markdown(out)
        assert "Self-weight (material density)" not in md_text

    def test_text_min_participation_filters(self, result):
        # Mode 1 has mx = 0 % -> dropped; modes 2..8 all clear 1 %.
        text = format_review_report(result, min_participation=1.0)
        assert "1.0000" not in text  # mode 1 period
        assert "0.5000" in text  # mode 2 period
        assert "0.1250" in text  # mode 8 period
        assert "1 further mode(s) not shown" in text

    def test_text_max_modes_caps(self, result):
        text = format_review_report(result, max_modes=3)
        assert "0.3333" in text  # mode 3 period
        assert "0.2500" not in text  # mode 4 period beyond the cap
        assert "5 further mode(s) not shown" in text

    def test_text_filters_combine(self, result):
        # min_participation keeps modes 2..8, then max_modes keeps two.
        text = format_review_report(result, max_modes=2, min_participation=1.0)
        assert "0.5000" in text
        assert "0.3333" in text
        assert "0.2500" not in text  # beyond --max-modes
        assert "6 further mode(s) not shown" in text

    def test_markdown_default_shows_every_mode(self, result):
        md_text = format_review_markdown(result)
        assert "0.1250" in md_text  # mode 8, T = 1/8 s
        assert "not shown" not in md_text

    def test_markdown_honours_limits(self, result):
        md_text = format_review_markdown(result, max_modes=2, min_participation=1.0)
        # min_participation keeps modes 2..8, then max_modes keeps two.
        assert "0.5000" in md_text
        assert "0.3333" in md_text
        assert "0.2500" not in md_text  # beyond --max-modes
        assert "_6 further mode(s) not shown._" in md_text

    def test_cli_accepts_mode_display_flags(self, tmp_path):
        """The mode-display flags must parse without a usage error.

        Exit code ``2`` is the CLI's usage / file-error code, so this test
        only requires that argument parsing succeeded — ``sample.s2k`` may
        legitimately gain blocking issues later and return ``1``.
        """
        out_file = tmp_path / "review.md"
        code = main(
            [
                str(FIXTURES_DIR / "sample.s2k"),
                "--format",
                "markdown",
                "--min-participation",
                "1",
                "--max-modes",
                "3",
                "--out",
                str(out_file),
            ]
        )
        assert code in (0, 1)  # not 2 -> no usage / argument-parsing failure
        assert out_file.exists()

    def test_cli_mode_display_flags_clean_model_exit_code(self, clean_model_file, tmp_path):
        """The same flags exit ``0`` for a guaranteed-clean model fixture."""
        out_file = tmp_path / "review.md"
        code = main(
            [
                str(clean_model_file),
                "--format",
                "markdown",
                "--min-participation",
                "1",
                "--max-modes",
                "3",
                "--out",
                str(out_file),
            ]
        )
        assert code == 0
        assert out_file.read_text(encoding="utf-8").startswith("# SAP2000 Model Review")


# ═══════════════════════════════════════════════════════════════════
# Table formatting (tabulate-backed, with a dependency-free fallback)
# ═══════════════════════════════════════════════════════════════════


def _no_tabulate_import(monkeypatch) -> None:
    """Force ``import tabulate`` to fail (exercise the fallback paths)."""
    import builtins

    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "tabulate":
            raise ImportError("simulated: tabulate not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)


class TestFormatTable:
    def test_empty_rows_render_empty(self):
        assert _format_table([]) == ""

    def test_rows_render_values(self):
        text = _format_table([{"A": "x", "B": "1"}, {"A": "yy", "B": "2"}])
        for token in ("A", "B", "x", "yy"):
            assert token in text

    def test_grid_format_when_tabulate_available(self):
        pytest.importorskip("tabulate")
        text = _format_table([{"A": "1", "B": "2"}], tablefmt="grid")
        assert "+" in text and "|" in text

    def test_markdown_fallback_is_a_pipe_table(self, monkeypatch):
        _no_tabulate_import(monkeypatch)
        lines = _format_table([{"A": "1", "B": "2"}], tablefmt="github").splitlines()
        assert lines[0] == "| A | B |"
        assert lines[1] == "|---|---|"
        assert lines[2] == "| 1 | 2 |"

    def test_fixed_width_fallback_when_no_tabulate(self, monkeypatch):
        _no_tabulate_import(monkeypatch)
        text = _format_table([{"A": "1", "Long": "2"}])
        assert "A" in text and "Long" in text
        assert "|" not in text  # not a pipe table


class TestReviewTableHelpers:
    def test_mass_unit_label_known_pairs(self):
        assert _mass_unit_label({"F": "KN", "L": "m"}) == "t"
        assert _mass_unit_label({"F": "N", "L": "m"}) == "kg"

    def test_mass_unit_label_fallback(self):
        assert _mass_unit_label({"F": "kN", "L": "in"}) == "kN\u00b7s\u00b2/in"

    def test_reaction_table_units(self):
        rows = _reaction_table_rows(
            {"fx": 1.0, "fy": 2.0, "fz": -3.0, "mx": 0.0, "my": 0.0, "mz": 4.0},
            "kN",
            "m",
        )
        assert len(rows) == 1
        assert rows[0]["Reaction"] == "Summed (all supports)"
        assert "Fz (kN)" in rows[0]
        assert "Mz (kN\u00b7m)" in rows[0]


# ═══════════════════════════════════════════════════════════════════
# Solver-free self-weight check
# ═══════════════════════════════════════════════════════════════════


class TestSelfWeight:
    def test_absent_by_default(self, sample_review):
        _md, result = sample_review
        assert result["self_weight"] is None

    def test_expected_weight_reported(self):
        md = _parse("clean_model.s2k")
        sw = review_model(md, self_weight=True)["self_weight"]
        assert sw is not None
        assert sw["expected"] > 0
        assert sw["by_section"]
        # Solver-free: applied / discrepancy / passed stay unset.
        assert sw["applied"] is None
        assert sw["discrepancy"] is None
        assert sw["passed"] is None

    def test_formatters_render_self_weight(self):
        md = _parse("clean_model.s2k")
        result = review_model(md, self_weight=True)
        assert "Self-weight" in format_review_report(result)
        assert "## Self-weight" in format_review_markdown(result)

    def test_section_table_has_total_row(self):
        md = _parse("clean_model.s2k")
        result = review_model(md, self_weight=True)
        expected = f"{result['self_weight']['expected']:.1f}"
        text = format_review_report(result)
        assert "Total" in text and expected in text
        md_text = format_review_markdown(result)
        assert "| Total" in md_text and expected in md_text

    def _two_material_model(self) -> SAPModelData:
        """Two frames on different materials, for the by-material table."""
        nodes = {
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 0.0, 0.0, 2.0),
            "3": Node("3", 3, 0.0, 0.0, 4.0),
        }
        frames = {
            "F1": FrameElement("F1", 1, "1", "2"),
            "F2": FrameElement("F2", 2, "2", "3"),
        }
        return SAPModelData(
            nodes=nodes,
            restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
            materials={
                "STEEL": Material(name="STEEL", type="Steel", E_mod=2.0e11, unit_weight=7850.0),
                "ALU": Material(name="ALU", type="Aluminium", E_mod=7.0e10, unit_weight=2700.0),
            },
            sections={
                "S1": Section(name="S1", shape="I/Wide Flange", material="STEEL", A=0.01),
                "S2": Section(name="S2", shape="Pipe", material="ALU", A=0.02),
            },
            frame_elements=frames,
            area_elements={},
            frame_assignments={"F1": "S1", "F2": "S2"},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
        )

    def test_material_weights_aggregate_sections(self):
        md = self._two_material_model()
        sw = review_model(md, self_weight=True)["self_weight"]
        # F1: A 0.01 m² × 7850 N/m³ × 2 m = 157 N
        # F2: A 0.02 m² × 2700 N/m³ × 2 m = 108 N
        assert sw["by_material"]["STEEL"] == pytest.approx(157.0)
        assert sw["by_material"]["ALU"] == pytest.approx(108.0)
        # Grouping must conserve the total.
        assert sum(sw["by_material"].values()) == pytest.approx(sw["expected"])
        assert sum(sw["by_section"].values()) == pytest.approx(sw["expected"])

    def test_formatters_render_material_table(self):
        md = self._two_material_model()
        result = review_model(md, self_weight=True)
        text = format_review_report(result)
        assert "By material:" in text
        assert "STEEL" in text and "ALU" in text
        md_text = format_review_markdown(result)
        assert "**By material**" in md_text
        assert "| STEEL" in md_text


# ═══════════════════════════════════════════════════════════════════
# Brace buckling check (skipped when no braces are present)
# ═══════════════════════════════════════════════════════════════════


class TestBraceBuckling:
    def test_absent_by_default(self, sample_review):
        _md, result = sample_review
        assert result["brace_buckling"] is None

    def test_skipped_when_no_braces(self):
        md = _parse("clean_model.s2k")
        block = review_model(md, brace_buckling=True)["brace_buckling"]
        assert block["detected"] is False
        assert block["members"] == {}

    def test_detected_when_braces_present(self, sample_review):
        md, _result = sample_review
        block = review_model(md, brace_buckling=True, brace_k=0.8)["brace_buckling"]
        assert block["detected"] is True
        assert block["k_factor"] == 0.8
        assert block["members"]
        member = next(iter(block["members"].values()))
        assert member["P_cr"] > 0
        assert member["slenderness"] > 0

    def test_text_shows_no_brace_note(self):
        md = _parse("clean_model.s2k")
        text = format_review_report(review_model(md, brace_buckling=True))
        assert "No brace sections found in model." in text

    def test_markdown_shows_no_brace_note(self):
        md = _parse("clean_model.s2k")
        md_text = format_review_markdown(review_model(md, brace_buckling=True))
        assert "_No brace sections found in model._" in md_text

    def test_num_braces_caps_rows(self, sample_review):
        md, _result = sample_review
        result = review_model(md, brace_buckling=True)
        total = len(result["brace_buckling"]["members"])
        assert total > 2
        text = format_review_report(result, num_braces=2)
        assert f"{total - 2} further brace(s) not shown" in text

    def test_cli_brace_and_self_weight_flags(self, tmp_path):
        out_file = tmp_path / "review.md"
        code = main(
            [
                str(FIXTURES_DIR / "sample.s2k"),
                "--brace-buckling",
                "--k-factor",
                "0.7",
                "--num-braces",
                "2",
                "--self-weight",
                "--format",
                "markdown",
                "--out",
                str(out_file),
            ]
        )
        assert code in (0, 1)  # parsed successfully (2 would be a usage error)
        text = out_file.read_text(encoding="utf-8")
        assert "## Self-weight" in text
        assert "## Brace buckling" in text


# ═══════════════════════════════════════════════════════════════════
# Response-spectrum table rendering — solver-free
# ═══════════════════════════════════════════════════════════════════


class TestResponseSpectrumFormatting:
    """Rendering of the response-spectrum tables (no OpenSees required)."""

    @staticmethod
    def _block() -> dict:
        """A fabricated ``response_spectrum`` block with two directions."""
        return {
            "spectrum": {
                "code": "GB50011",
                "label": "Rare",
                "level": "rare",
                "intensity": 7,
                "site_class": "II",
                "damping": 0.05,
                "n_modes": 2,
                "directions": ["X", "Y"],
            },
            "directions": {
                "X": {
                    "base_shear_cqc": 120.5,
                    "base_shear_srss": 118.0,
                    "base_moment_cqc": 900.0,
                    "base_moment_srss": 880.0,
                    "roof_disp_cqc": 0.0123,
                    "roof_disp_srss": 0.0130,
                    "modal_base_shear": [100.0, 20.5],
                    "modal_base_moment": [800.0, 100.0],
                },
                "Y": {
                    "base_shear_cqc": 60.0,
                    "base_shear_srss": 58.0,
                    "base_moment_cqc": 400.0,
                    "base_moment_srss": 390.0,
                    "roof_disp_cqc": 0.0060,
                    "roof_disp_srss": 0.0065,
                    "modal_base_shear": [55.0, 5.0],
                    "modal_base_moment": [300.0, 25.0],
                },
            },
        }

    def test_direction_rows_cover_every_direction(self):
        rows = _response_spectrum_rows(self._block(), "kN", "m")
        assert [r["Direction"] for r in rows] == ["X", "Y"]
        assert rows[0]["V CQC (kN)"] == "120.5"
        assert rows[0]["M SRSS (kN·m)"] == "880.0"
        assert rows[1]["Roof CQC (m)"] == "0.00600"

    def test_mode_rows_align_with_modal_pass(self):
        analysis = {
            "mass_participation": [
                {"mode": 1, "period": 0.5},
                {"mode": 2, "period": 0.25},
            ]
        }
        rows = _response_spectrum_mode_rows(self._block(), analysis, "kN")
        # Per-mode rows first, then the single combined-summary footer row.
        assert [r["Mode"] for r in rows] == ["1", "2", "CQC"]
        assert rows[0]["Period (s)"] == "0.5000"
        assert rows[0]["V X (kN)"] == "100.0"
        assert rows[1]["V Y (kN)"] == "5.0"

    def test_mode_rows_footer_uses_active_combination(self):
        """The footer carries only the active rule, titled with that rule."""
        analysis = {"mass_participation": [{"mode": 1, "period": 0.5}]}

        # Default (no ``combination`` key) is CQC.
        rows = _response_spectrum_mode_rows(self._block(), analysis, "kN")
        assert rows[-1]["Mode"] == "CQC"
        assert rows[-1]["Period (s)"] == "\u2014"
        assert rows[-1]["V X (kN)"] == "120.5"
        assert rows[-1]["V Y (kN)"] == "60.0"

        srss_block = self._block()
        srss_block["combination"] = "srss"
        rows = _response_spectrum_mode_rows(srss_block, analysis, "kN")
        assert rows[-1]["Mode"] == "SRSS"
        assert rows[-1]["V X (kN)"] == "118.0"
        assert rows[-1]["V Y (kN)"] == "58.0"

    def test_missing_block_renders_nothing(self):
        assert _response_spectrum_rows({}, "kN", "m") == []
        assert _response_spectrum_mode_rows({}, {}, "kN") == []


# ═══════════════════════════════════════════════════════════════════
# Analysis-phase checks (load verification / wind) — require OpenSees
# ═══════════════════════════════════════════════════════════════════


def _wipe() -> None:
    from openseespy.opensees import wipe

    wipe()


class TestAnalysisChecks:
    def test_run_review_analysis_exposes_optional_keys(self):
        pytest.importorskip("openseespy.opensees")
        from fea_toolkit.opensees.analysis_builder import run_review_analysis

        md = _parse("sample.s2k")
        try:
            result = run_review_analysis(md, {"num_modes": 2, "load_verify": True})
        finally:
            _wipe()
        assert "load_verification" in result
        assert "wind" in result
        # sample.s2k defines a DEAD pattern -> one verification row.
        assert result["load_verification"]
        assert result["load_verification"][0]["Load Pattern"] == "DEAD"

    def test_no_pattern_model_reports_empty_verification(self):
        pytest.importorskip("openseespy.opensees")
        from fea_toolkit.opensees.analysis_builder import run_review_analysis

        md = _parse("clean_model.s2k")
        try:
            result = run_review_analysis(md, {"num_modes": 2, "load_verify": True})
        finally:
            _wipe()
        assert result["load_verification"] == []
        assert "load_verification_error" not in result

    def test_response_spectrum_pass_exposes_directions(self):
        pytest.importorskip("openseespy.opensees")
        from fea_toolkit.opensees.analysis_builder import run_review_analysis

        md = _parse("sample.s2k")
        try:
            result = run_review_analysis(
                md,
                {"num_modes": 4, "response_spectrum": True, "spectrum": {"intensity": 7}},
            )
        finally:
            _wipe()

        assert result["ok"]
        assert result["response_spectrum_error"] is None
        rs = result["response_spectrum"]
        assert rs["spectrum"]["code"] == "GB50011"
        assert rs["spectrum"]["level"] == "rare"
        assert rs["spectrum"]["intensity"] == 7
        assert rs["spectrum"]["site_class"] == "II"
        # The default direction set is X and Y.
        assert set(rs["directions"]) == {"X", "Y"}
        n_modes = rs["spectrum"]["n_modes"]
        assert n_modes >= 1
        for data in rs["directions"].values():
            for key in (
                "base_shear_cqc",
                "base_shear_srss",
                "base_moment_cqc",
                "base_moment_srss",
                "roof_disp_cqc",
                "roof_disp_srss",
            ):
                assert key in data
            # One per-mode entry per mode included in the pass.
            assert len(data["modal_base_shear"]) == n_modes
            assert len(data["modal_base_moment"]) == n_modes
        # The static pass still runs after the RS pass.
        assert result["static"]["patterns_applied"] == ["DEAD"]

    def test_response_spectrum_config_is_respected(self):
        pytest.importorskip("openseespy.opensees")
        from fea_toolkit.opensees.analysis_builder import run_review_analysis

        md = _parse("sample.s2k")
        try:
            result = run_review_analysis(
                md,
                {
                    "num_modes": 3,
                    "response_spectrum": True,
                    "spectrum": {
                        "level": "frequent",
                        "intensity": 8,
                        "site_class": "III",
                        "damping": 0.02,
                        "directions": ["X"],
                    },
                },
            )
        finally:
            _wipe()

        rs = result["response_spectrum"]
        assert rs["spectrum"]["level"] == "frequent"
        assert rs["spectrum"]["intensity"] == 8
        assert rs["spectrum"]["site_class"] == "III"
        assert rs["spectrum"]["damping"] == pytest.approx(0.02)
        assert list(rs["directions"]) == ["X"]

    def test_response_spectrum_failure_is_captured(self):
        pytest.importorskip("openseespy.opensees")
        from fea_toolkit.opensees.analysis_builder import run_review_analysis

        md = _parse("sample.s2k")
        try:
            # An unknown excitation direction makes the RS pass raise — the
            # failure must be captured, never propagated.
            result = run_review_analysis(
                md,
                {
                    "num_modes": 2,
                    "response_spectrum": True,
                    "spectrum": {"directions": ["Q"]},
                },
            )
        finally:
            _wipe()

        assert result["response_spectrum"] is None
        assert result["response_spectrum_error"]
        # The modal / static pass still completes.
        assert result["ok"]
        assert result["static"]["patterns_applied"] == ["DEAD"]

    def test_review_model_threads_response_spectrum(self):
        pytest.importorskip("openseespy.opensees")
        md = _parse("sample.s2k")
        try:
            result = review_model(
                md,
                include_analysis=True,
                analysis_config={"num_modes": 3, "response_spectrum": True},
            )
        finally:
            _wipe()

        assert result["analysis"]["response_spectrum"]
        text = format_review_report(result)
        assert "-- Response spectrum --" in text
        assert "Per-mode base shear:" in text
        md_text = format_review_markdown(result)
        assert "### Response spectrum" in md_text
        assert "**Per-mode base shear**" in md_text

    def test_cli_response_spectrum_flag(self, tmp_path):
        pytest.importorskip("openseespy.opensees")
        out_file = tmp_path / "review.md"
        code = main(
            [
                str(FIXTURES_DIR / "sample.s2k"),
                "--response-spectrum",
                "--num-modes",
                "3",
                "--spectrum-intensity",
                "6",
                "--format",
                "markdown",
                "--out",
                str(out_file),
            ]
        )
        assert code in (0, 1)  # parsed successfully (2 would be a usage error)
        text = out_file.read_text(encoding="utf-8")
        assert "### Response spectrum" in text
        assert "intensity **6**" in text

    def test_review_model_threads_analysis_checks(self):
        pytest.importorskip("openseespy.opensees")
        md = _parse("sample.s2k")
        try:
            result = review_model(
                md,
                include_analysis=True,
                analysis_config={"num_modes": 2, "load_verify": True, "wind_check": True},
            )
        finally:
            _wipe()
        analysis = result["analysis"]
        assert analysis["load_verification"]
        # Structured wind-sanity data (rendered as a table by the formatters).
        assert isinstance(analysis["wind"], dict)
        assert analysis["wind"]["rows"]
        # 6-DOF participation is present on every modal row.
        for row in analysis["mass_participation"]:
            assert {"mx", "my", "mz", "rx", "ry", "rz"} <= set(row)
        # Both formatters surface the analysis-phase tables.
        text = format_review_report(result)
        assert "Load verification" in text
        assert "Wind sanity check" in text
        # The wind table states where its numbers come from.
        assert "Basis:" in text
        md_text = format_review_markdown(result)
        assert "### Load verification" in md_text
        assert "### Wind sanity check" in md_text
        assert "Basis:" in md_text


# ═══════════════════════════════════════════════════════════════════
# NPZ export (geometry, and optionally modal + static results)
# ═══════════════════════════════════════════════════════════════════


class TestNpzExport:
    def test_absent_by_default(self, sample_review):
        _md, result = sample_review
        assert result["npz"] is None
        assert result["npz_error"] is None
        assert result["npz_contents"] is None

    def test_geometry_only_export(self, tmp_path, sample_review):
        md, _result = sample_review
        npz_path = tmp_path / "geometry.npz"
        result = review_model(md, export_npz=npz_path)
        assert result["npz_error"] is None
        assert result["npz"] is not None
        assert npz_path.exists()

        from fea_toolkit.io.npz_reader import read_results_npz

        data = read_results_npz(str(npz_path))
        assert "node_tag" in data
        assert len(data["node_tag"]) > 0
        # Geometry-only: no analysis results recorded.
        assert list(data["analysis_types"]) == []

    def test_analysis_export_includes_results(self, tmp_path):
        pytest.importorskip("openseespy.opensees")
        md = _parse("sample.s2k")
        npz_path = tmp_path / "results.npz"
        try:
            result = review_model(
                md,
                include_analysis=True,
                analysis_config={"num_modes": 2},
                export_npz=npz_path,
            )
        finally:
            _wipe()
        assert result["npz_error"] is None
        assert npz_path.exists()

        from fea_toolkit.io.npz_reader import read_results_npz

        data = read_results_npz(str(npz_path))
        analysis_types = {str(t) for t in data["analysis_types"]}
        assert {"static", "modal"} <= analysis_types
        assert "modal/period" in data
        assert list(data["static_case_labels"]) == ["DEAD"]

    def test_response_spectrum_export_includes_rs(self, tmp_path):
        """The unified archive carries the canonical ``rs/*`` block."""
        pytest.importorskip("openseespy.opensees")
        md = _parse("sample.s2k")
        npz_path = tmp_path / "rs.npz"
        try:
            result = review_model(
                md,
                include_analysis=True,
                analysis_config={"num_modes": 3, "response_spectrum": True},
                export_npz=npz_path,
            )
        finally:
            _wipe()
        assert result["npz_error"] is None
        assert npz_path.exists()

        from fea_toolkit.io.npz_reader import read_results_npz

        data = read_results_npz(str(npz_path))
        analysis_types = {str(t) for t in data["analysis_types"]}
        assert {"static", "modal", "rs"} <= analysis_types
        # Canonical RS block: per-mode shear + combined shear/moment/roof.
        for key in (
            "rs/period",
            "rs/v_base_x",
            "rs/v_base_y",
            "rs/v_cqc_x",
            "rs/v_srss_y",
            "rs/m_cqc_x",
            "rs/m_srss_y",
            "rs/roof_disp_cqc_x",
            "rs/roof_disp_srss_y",
            # Single-direction CQC displacement field (Rhino RS overlay).
            "rs/node_tag",
            "rs/node_dx",
        ):
            assert key in data, key
        # Visualiser row-alignment / parent-collapse keys survive the export.
        assert "modal/node_tag" in data
        assert "frame_parent_node_i" in data
        assert data["rs/node_tag"].shape == data["rs/node_dx"].shape
        # ``rs/period`` is trimmed to the modes the RS pass actually used, so
        # it stays aligned with the per-mode shear arrays.
        n_modes = int(result["analysis"]["response_spectrum"]["spectrum"]["n_modes"])
        assert data["rs/period"].shape == (n_modes,)
        assert data["rs/v_base_x"].shape == (n_modes,)
        # The archive manifest reports the RS analysis.
        assert "rs" in result["npz_contents"]["analysis_types"]

    def test_geometry_manifest(self, tmp_path, sample_review):
        md, _result = sample_review
        result = review_model(md, export_npz=tmp_path / "geometry.npz")
        contents = result["npz_contents"]
        assert contents["geometry"]["present"] is True
        assert contents["geometry"]["n_nodes"] > 0
        assert contents["analysis_types"] == []
        assert contents["static_cases"] == []
        assert contents["n_modes"] == 0
        assert contents["n_arrays"] > 0
        text = format_review_report(result)
        assert "NPZ archive contents" in text
        assert "none (geometry only)" in text

    def test_analysis_manifest(self, tmp_path):
        pytest.importorskip("openseespy.opensees")
        md = _parse("sample.s2k")
        try:
            result = review_model(
                md,
                include_analysis=True,
                analysis_config={"num_modes": 2},
                export_npz=tmp_path / "results.npz",
            )
        finally:
            _wipe()
        contents = result["npz_contents"]
        assert {"static", "modal"} <= set(contents["analysis_types"])
        assert contents["static_cases"] == ["DEAD"]
        assert contents["n_modes"] >= 1
        md_text = format_review_markdown(result)
        assert "## NPZ archive" in md_text
        assert "static [DEAD]" in md_text
        assert "modal (" in md_text

    def test_formatter_reports_npz(self, tmp_path, sample_review):
        md, _result = sample_review
        npz_path = tmp_path / "geometry.npz"
        result = review_model(md, export_npz=npz_path)
        assert "NPZ" in format_review_report(result)
        assert "**NPZ:**" in format_review_markdown(result)

    def test_cli_npz_flag_writes_geometry(self, tmp_path):
        out_file = tmp_path / "review.md"
        npz_path = tmp_path / "cli.npz"
        code = main(
            [
                str(FIXTURES_DIR / "clean_model.s2k"),
                "--npz",
                str(npz_path),
                "--format",
                "markdown",
                "--out",
                str(out_file),
            ]
        )
        assert code == 0
        assert npz_path.exists()
        assert "**NPZ:**" in out_file.read_text(encoding="utf-8")
