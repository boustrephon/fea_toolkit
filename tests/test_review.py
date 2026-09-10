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
from fea_toolkit.model.review import main
from fea_toolkit.model.sap_data import (
    FRAME_RELEASE_DOF_LABELS,
    FrameElement,
    FrameRelease,
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
    0 % participation and every later mode clears a 1 % threshold.
    """
    return {
        "ok": True,
        "periods": [1.0 / (i + 1) for i in range(n_modes)],
        "mass_participation": [
            {
                "mode": i + 1,
                "period": 1.0 / (i + 1),
                "mx": float(i),
                "my": 0.0,
                "mz": 0.0,
            }
            for i in range(n_modes)
        ],
        "static": {
            "summed_reactions": {"fx": 0.0, "fy": 0.0, "fz": -10.0},
            "n_supports": 2,
            "patterns_applied": ["DEAD"],
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
        assert "mode 8:" in text  # no longer truncated to the first five
        assert "not shown" not in text

    def test_text_min_participation_filters(self, result):
        # Mode 1 has mx = 0 % -> dropped; modes 2..8 all clear 1 %.
        text = format_review_report(result, min_participation=1.0)
        assert "mode 1:" not in text
        assert "mode 2:" in text
        assert "mode 8:" in text
        assert "1 further mode(s) not shown" in text

    def test_text_max_modes_caps(self, result):
        text = format_review_report(result, max_modes=3)
        assert "mode 3:" in text
        assert "mode 4:" not in text
        assert "5 further mode(s) not shown" in text

    def test_text_filters_combine(self, result):
        # min_participation keeps modes 2..8, then max_modes keeps two.
        text = format_review_report(result, max_modes=2, min_participation=1.0)
        assert "mode 2:" in text
        assert "mode 3:" in text
        assert "mode 4:" not in text
        assert "6 further mode(s) not shown" in text

    def test_markdown_default_shows_every_mode(self, result):
        md_text = format_review_markdown(result)
        # mode 8 has T = 1/8 = 0.125 s -> its table row must be present.
        assert "| 8 | 0.1250 |" in md_text
        assert "not shown" not in md_text

    def test_markdown_honours_limits(self, result):
        md_text = format_review_markdown(result, max_modes=2, min_participation=1.0)
        # min_participation keeps modes 2..8, then max_modes keeps two.
        assert "| 2 | 0.5000 |" in md_text
        assert "| 3 | 0.3333 |" in md_text
        assert "| 4 | 0.2500 |" not in md_text  # beyond --max-modes
        assert "_6 further mode(s) not shown._" in md_text

    def test_cli_accepts_mode_display_flags(self, tmp_path):
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
        assert code == 0
        assert out_file.exists()
