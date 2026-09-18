"""Tests for the tagged ETABS checklist source and its generated editions."""

from __future__ import annotations

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "ETABS_checklist_src.md"
BUILD = ROOT / "docs" / "checklist" / "build.py"
JURISDICTIONS = ("is", "gb", "hk")
TAGS = {"general", "is", "gb", "hk"}
SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
IS_REFS = ("IS 456", "IS 1893", "IS 875", "IS 800", "IS 1786", "IS 13920", "IS 16700")
HK_REFS = ("CoP for Structural Use of Concrete 2013", "BS 4449", "Wind Effects in Hong Kong")
GB_REFS = ("GB 50009", "GB 50010", "GB 50011", "GB 50017", "GB 50068", "JGJ", "HRB400")
FORBIDDEN = {
    "is": (GB_REFS + HK_REFS),
    "gb": (IS_REFS + HK_REFS),
    "hk": (IS_REFS + GB_REFS),
}
GENERAL_REFS = ("3.19", "6.1", "8.6")


def _load_build():
    spec = importlib.util.spec_from_file_location("etabs_checklist_build", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _path(key):
    return ROOT / "docs" / f"ETABS_checklist_{key}.md"


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
    for key, (filename, display) in build.JURISDICTIONS.items():
        expected = build.render(key, display)
        actual = (ROOT / "docs" / filename).read_text(encoding="utf-8")
        assert actual == expected, f"{filename} is stale; run python docs/checklist/build.py"


def test_all_phases_present():
    for key in JURISDICTIONS:
        text = _path(key).read_text(encoding="utf-8")
        for number in range(1, 9):
            assert f"## Phase {number} " in text
        assert "## Reference List" in text


def test_no_forbidden_codes():
    for key, needles in FORBIDDEN.items():
        text = _path(key).read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{key} edition must not reference {needle!r}"


def test_jurisdiction_column_removed():
    for key in JURISDICTIONS:
        assert "| Jurisdiction |" not in _path(key).read_text(encoding="utf-8")


def test_refs_unique_per_edition():
    for key in JURISDICTIONS:
        refs = [row[0] for row in _checklist_rows(_path(key).read_text(encoding="utf-8"))]
        assert refs, f"no checklist rows parsed for {key}"
        assert len(refs) == len(set(refs)), f"duplicate ref in {key}"


def test_source_tags_valid():
    for row in _checklist_rows(SRC.read_text(encoding="utf-8")):
        assert row[-1].lower() in TAGS, f"bad jurisdiction tag {row[-1]!r}"


def test_general_rows_shared_by_all_editions():
    for key in JURISDICTIONS:
        text = _path(key).read_text(encoding="utf-8")
        for ref in GENERAL_REFS:
            assert f"| {ref} |" in text, f"{key} missing shared ref {ref}"
