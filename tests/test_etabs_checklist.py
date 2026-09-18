"""Tests for the tagged checklist source and its generated editions."""

from __future__ import annotations

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "ETABS_checklist_src.md"
BUILD = ROOT / "docs" / "checklist" / "build.py"
TAG_VALUES = {
    "jurisdiction": {"general", "is", "gb", "hk"},
    "program": {"general", "etabs", "sap2000"},
}
SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
IS_REFS = ("IS 456", "IS 1893", "IS 875", "IS 800", "IS 1786", "IS 13920", "IS 16700")
HK_REFS = ("CoP for Structural Use of Concrete 2013", "BS 4449", "Wind Effects in Hong Kong")
GB_REFS = ("GB 50009", "GB 50010", "GB 50011", "GB 50017", "GB 50068", "JGJ", "HRB400")
FORBIDDEN_JURISDICTION = {
    "is": GB_REFS + HK_REFS,
    "gb": IS_REFS + HK_REFS,
    "hk": IS_REFS + GB_REFS,
}
ETABS_ONLY = (
    "Automatic Frame Subdivide",
    "Auto Line Constraints",
    "Define \u2192 Diaphragms",
    "Assign \u2192 Joint \u2192 Diaphragms",
    "Area Object Mesh Options",
    "Verify Analysis vs Design Section",
    "Frame Section List",
    "Area Section List",
    "Material List",
    "Mass Summary",
    "Story Drifts",
    "Story Stiffness",
)
SAP2000_ONLY = (
    "Generalized Displacements",
    "Generate Edge Constraints",
    "Automatic Frame Mesh",
    "Joint Constraints",
)
GENERAL_REFS = ("1.1", "6.1", "7.11")


def _load_build():
    spec = importlib.util.spec_from_file_location("etabs_checklist_build", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _text(filename):
    return (ROOT / "docs" / filename).read_text(encoding="utf-8")


def _checklist_rows(text):
    """Yield the cell lists of every row under a checklist (Ref) table."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if (
            line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and SEP_RE.match(lines[i + 1].strip())
        ):
            header = _cells(line)
            if header and header[0] == "Ref":
                j = i + 2
                while (
                    j < len(lines)
                    and lines[j].lstrip().startswith("|")
                    and not SEP_RE.match(lines[j].strip())
                ):
                    yield _cells(lines[j])
                    j += 1


def test_editions_match_source():
    for filename, target in build.OUTPUTS.items():
        assert _text(filename) == build.render(target), (
            f"{filename} is stale; run python docs/checklist/build.py"
        )


def test_all_phases_present():
    for filename in build.OUTPUTS:
        text = _text(filename)
        for number in range(1, 9):
            assert f"## Phase {number} " in text
        assert "## Reference List" in text
        assert "## Disclaimer and legal notices" in text


def test_jurisdiction_isolation():
    for filename, target in build.OUTPUTS.items():
        text = _text(filename)
        for needle in FORBIDDEN_JURISDICTION[target["jurisdiction"]]:
            assert needle not in text, f"{filename} must not reference {needle!r}"


def test_program_isolation():
    for filename, target in build.OUTPUTS.items():
        text = _text(filename)
        forbidden = ETABS_ONLY if target["program"] == "sap2000" else SAP2000_ONLY
        for needle in forbidden:
            assert needle not in text, f"{filename} must not reference {needle!r}"


def test_tag_columns_removed():
    for filename in build.OUTPUTS:
        text = _text(filename)
        assert "| Jurisdiction |" not in text
        assert "| Program |" not in text


def test_refs_unique_per_edition():
    for filename in build.OUTPUTS:
        refs = [row[0] for row in _checklist_rows(_text(filename))]
        assert refs, f"no checklist rows parsed for {filename}"
        assert len(refs) == len(set(refs)), f"duplicate ref in {filename}"


def test_source_tags_valid():
    for row in _checklist_rows(SRC.read_text(encoding="utf-8")):
        assert row[4].lower() in TAG_VALUES["jurisdiction"], f"bad jurisdiction tag {row[4]!r}"
        assert row[5].lower() in TAG_VALUES["program"], f"bad program tag {row[5]!r}"


def test_general_rows_shared_by_all_editions():
    for filename in build.OUTPUTS:
        text = _text(filename)
        for ref in GENERAL_REFS:
            assert f"| {ref} |" in text, f"{filename} missing shared ref {ref}"


def test_disclaimer_and_trademark_present():
    for filename, target in build.OUTPUTS.items():
        text = _text(filename)
        assert build.TRADEMARK[target["program"]] in text
        assert build.CODE_NOTICE[target["jurisdiction"]] in text
