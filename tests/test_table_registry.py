"""Tests for the SAP2000 table-coverage registry (:mod:`fea_toolkit.io.table_registry`).

Two of these are *drift guards*:

* :func:`test_parser_table_names_are_registered` extracts every table name the
  parser reads (via AST) and asserts each is registered.  This is the same
  class of bug that let the parser read the cardinal point from the wrong
  table for several releases — a handler referencing an unknown table name
  reads nothing, silently.
* :func:`test_fixtures_have_no_unexpected_unhandled_tables` parses the
  committed fixtures and asserts the registry covers them, so a table the
  toolkit has never seen cannot creep into a fixture unnoticed.
"""

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.io.table_registry import (
    HANDLED_TABLES,
    TableCoverage,
    classify,
    format_table_coverage,
    table_coverage,
    unhandled_tables,
)
from fea_toolkit.io.table_registry import (
    main as tables_main,
)

FIXTURES = Path(__file__).parent / "fixtures"
PARSER_SRC = Path(__file__).resolve().parent.parent / "src" / "fea_toolkit" / "io" / "s2k_parser.py"


# ══════════════════════════════════════════════════════════════════════
# Drift guard A — every table the parser reads is registered
# ══════════════════════════════════════════════════════════════════════


def _string_constants(node: ast.AST) -> set[str]:
    return {
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    }


def _is_raw_tables(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_raw_tables"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _references_raw_tables(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Attribute) and sub.attr == "_raw_tables" for sub in ast.walk(node)
    )


def extract_parser_table_names() -> set[str]:
    """Return every table name the parser source reads (any dispatch style)."""
    tree = ast.parse(PARSER_SRC.read_text(encoding="utf-8"))
    names: set[str] = set()

    # 1. Variant tuples: class/module assignments named *_TABLE_NAMES.
    #    (Name + tuple/list value are both required so an unrelated
    #    ``table_regex = re.compile(...)`` assignment is not mistaken for one.)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.upper().endswith("TABLE_NAMES")
                    and isinstance(node.value, (ast.Tuple, ast.List))
                ):
                    names |= _string_constants(node.value)

    # 2. Direct reads: self._raw_tables.get("NAME") / self._raw_tables["NAME"].
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_raw_tables(node.func.value)
        ):
            names |= _string_constants(node)
        if isinstance(node, ast.Subscript) and _is_raw_tables(node.value):
            names |= _string_constants(node.slice)

    # 3. Prefix-scan families: .startswith("PREFIX") inside a function that
    #    reads _raw_tables (scoping excludes unrelated startswith calls).
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _references_raw_tables(fn):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
            ):
                names |= _string_constants(node)
        # 4. Inline tuples iterated by a loop whose body reads _raw_tables
        #    (e.g. AREA MESH ASSIGNMENTS / AREA AUTO MESH ASSIGNMENTS).
        for node in ast.walk(fn):
            if isinstance(node, ast.For) and _references_raw_tables(node):
                names |= _string_constants(node.iter)

    return names


def test_extractor_finds_tables():
    """The AST extractor itself must find a sensible number of names."""
    names = extract_parser_table_names()
    assert len(names) > 25, f"extractor found only {len(names)} names: {sorted(names)}"


def test_parser_table_names_are_registered():
    """Every table name the parser reads must be in the coverage registry."""
    unregistered = sorted(n for n in extract_parser_table_names() if classify(n) == "unhandled")
    assert not unregistered, (
        "These table names are read by s2k_parser.py but are absent from the "
        f"table_coverage registry: {unregistered}\n"
        "Add them to HANDLED_TABLES (or KNOWN_GAP_TABLES / IGNORED_TABLES) in "
        "src/fea_toolkit/io/table_registry.py."
    )


def test_handled_tables_are_upper_case():
    """HANDLED_TABLES entries are canonical SAP2000 (upper-case) table names."""
    assert "JOINT COORDINATES" in HANDLED_TABLES
    assert "FRAME SECTION PROPERTIES 01 - GENERAL" in HANDLED_TABLES
    assert all(name == name.upper() for name in HANDLED_TABLES)


# ══════════════════════════════════════════════════════════════════════
# Classification
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "name",
    [
        "JOINT COORDINATES",
        "FRAME SECTION PROPERTIES 01 - GENERAL",
        "MATERIAL PROPERTIES 03A - STEEL DATA",  # prefix family
        "CONSTRAINT DEFINITIONS - DIAPHRAGM",  # prefix family
        "AREA LOADS - GRAVITY",  # prefix family
        "CASE - MODAL 1 - GENERAL",  # prefix family
        "AUTO SEISMIC - LOAD PATTERN",  # prefix family
    ],
)
def test_classify_handled(name):
    """Exact names and prefix families both classify as handled."""
    assert classify(name) == "handled"


def test_classify_known_gap():
    """Recognised-but-unparsed tables classify as known-gap."""
    assert classify("COMBINATION DEFINITIONS") == "known-gap"
    assert classify("SECTION DESIGNER PROPERTIES 16 - SHAPE POLYGON") == "known-gap"


def test_classify_ignored():
    """Design / bookkeeping tables classify as ignored."""
    assert classify("PREFERENCES - STEEL DESIGN - AISC 360-16") == "ignored"
    assert classify("METADATA") == "ignored"


def test_classify_unhandled():
    """Anything unrecognised is the interesting bucket."""
    assert classify("SOME BRAND NEW TABLE") == "unhandled"
    assert classify("") == "unhandled"


# ══════════════════════════════════════════════════════════════════════
# Coverage result
# ══════════════════════════════════════════════════════════════════════


def _raw_tables():
    return {
        "JOINT COORDINATES": [{"Joint": 1}, {"Joint": 2}],
        "METADATA": [{"FileName": "x"}],
        "COMBINATION DEFINITIONS": [{"Name": "C1"}],
        "SOMETHING NEW": [{}, {}, {}],
    }


def test_table_coverage_buckets():
    """Each table lands in the right bucket with its row count."""
    cov = table_coverage(_raw_tables())
    assert cov.handled == {"JOINT COORDINATES": 2}
    assert cov.ignored == {"METADATA": 1}
    assert cov.known_gaps == {"COMBINATION DEFINITIONS": 1}
    assert cov.unhandled == {"SOMETHING NEW": 3}


def test_unhandled_tables_convenience():
    """The convenience accessor returns only the unrecognised tables."""
    assert unhandled_tables(_raw_tables()) == {"SOMETHING NEW": 3}


def test_coverage_clean_and_gaps():
    """``clean`` tracks unhandled only; ``gaps`` merges known + unhandled."""
    cov = table_coverage(_raw_tables())
    assert cov.clean is False
    assert cov.gaps == {"COMBINATION DEFINITIONS": 1, "SOMETHING NEW": 3}

    clean = table_coverage({"JOINT COORDINATES": [{"Joint": 1}]})
    assert clean.clean is True
    assert clean.gaps == {}


def test_to_dict_shape():
    """``to_dict`` is JSON-serialisable and carries reasons for known gaps."""
    d = table_coverage(_raw_tables()).to_dict()
    assert d["clean"] is False
    assert d["counts"] == {"handled": 1, "known_gaps": 1, "ignored": 1, "unhandled": 1}
    assert d["known_gaps"]["COMBINATION DEFINITIONS"]["rows"] == 1
    assert d["known_gaps"]["COMBINATION DEFINITIONS"]["reason"]
    json.dumps(d)  # must not raise


def test_format_table_coverage_clean():
    """A clean coverage renders the CLEAN headline."""
    text = format_table_coverage(table_coverage({"JOINT COORDINATES": []}))
    assert "CLEAN" in text


def test_format_table_coverage_lists_unhandled():
    """Unrecognised tables are named in the rendered text."""
    text = format_table_coverage(table_coverage(_raw_tables()))
    assert "SOMETHING NEW" in text
    assert "COMBINATION DEFINITIONS" in text
    assert "METADATA" not in text  # ignored hidden by default

    with_ignored = format_table_coverage(table_coverage(_raw_tables()), show_ignored=True)
    assert "METADATA" in with_ignored


# ══════════════════════════════════════════════════════════════════════
# Parser integration
# ══════════════════════════════════════════════════════════════════════

_NOVEL_TABLE_S2K = (
    # The parser always consumes the first line as the "File ... was saved on"
    # metadata line, so a synthetic fixture must start with one.
    "File C:\\Models\\novel.s2k was saved on 1/2/26 at 3:04:05 PM\n"
    'TABLE:  "PROGRAM CONTROL"\n'
    "   ProgramName=SAP2000   Version=26\n"
    'TABLE:  "JOINT COORDINATES"\n'
    "   Joint=1   XorR=0   Y=0   Z=0\n"
    'TABLE:  "BRAND NEW TABLE"\n'
    "   Foo=1   Bar=2\n"
)


def test_parse_warn_unhandled_logs(tmp_path, caplog):
    """parse(warn_unhandled=True) logs the unrecognised table."""
    path = tmp_path / "novel.s2k"
    path.write_text(_NOVEL_TABLE_S2K, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        SAP2000Parser(path).parse(warn_unhandled=True)
    assert any("BRAND NEW TABLE" in record.getMessage() for record in caplog.records)


def test_parse_warn_unhandled_silent_by_default(tmp_path, caplog):
    """No warning is emitted unless explicitly requested."""
    path = tmp_path / "novel.s2k"
    path.write_text(_NOVEL_TABLE_S2K, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        SAP2000Parser(path).parse()
    assert not caplog.records


def test_parser_table_coverage_method(tmp_path):
    """The parser exposes the same coverage as the module function."""
    path = tmp_path / "novel.s2k"
    path.write_text(_NOVEL_TABLE_S2K, encoding="utf-8")
    parser = SAP2000Parser(path).parse()
    cov = parser.table_coverage()
    assert isinstance(cov, TableCoverage)
    assert set(cov.unhandled) == {"BRAND NEW TABLE"}
    assert cov.handled == {"PROGRAM CONTROL": 1, "JOINT COORDINATES": 1}


def test_parser_table_coverage_requires_parse():
    """Calling table_coverage() before parse() is a clear error."""
    parser = SAP2000Parser("does-not-exist.s2k")
    with pytest.raises(RuntimeError, match="No model data loaded"):
        parser.table_coverage()


# ══════════════════════════════════════════════════════════════════════
# Drift guard B — fixtures stay within the registry
# ══════════════════════════════════════════════════════════════════════

# ``sample.split.json`` is deliberately excluded: it is a toolkit-internal
# split-model dump (``nodes`` / ``split_elements`` / ...), not an SAP2000
# table export, so table coverage does not apply to it.
_FIXTURE_MODELS = sorted(
    p
    for p in list(FIXTURES.glob("*.json")) + list(FIXTURES.glob("*.s2k"))
    if p.name != "sample.split.json"
)


@pytest.mark.parametrize("fixture", _FIXTURE_MODELS, ids=lambda p: p.name)
def test_fixtures_have_no_unexpected_unhandled_tables(fixture):
    """Every table in a committed fixture is known to the registry."""
    parser = (
        SAP2000Parser.from_json(fixture)
        if fixture.suffix == ".json"
        else SAP2000Parser(fixture).parse()
    )
    cov = table_coverage(parser.raw_tables)
    assert cov.unhandled == {}, (
        f"{fixture.name} contains tables the registry does not know: "
        f"{sorted(cov.unhandled)}.  Either register them in "
        "io/table_registry.py or confirm the fixture is not an SAP table export."
    )


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def _write_json(tmp_path, extra: dict, name: str) -> Path:
    data = {
        "PROGRAM CONTROL": [{"ProgramName": "SAP2000", "Version": "26"}],
        "JOINT COORDINATES": [{"Joint": 1, "XorR": 0, "Y": 0, "Z": 0}],
    }
    data.update(extra)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_clean_exits_zero(tmp_path, capsys):
    """No unrecognised tables → exit 0."""
    path = _write_json(tmp_path, {}, "clean.json")
    assert tables_main([str(path)]) == 0
    assert "CLEAN" in capsys.readouterr().out


def test_cli_unhandled_exits_one(tmp_path, capsys):
    """An unrecognised table → exit 1 (usable as a CI gate)."""
    path = _write_json(tmp_path, {"SOMETHING NEW": [{"A": 1}]}, "novel.json")
    assert tables_main([str(path)]) == 1
    assert "SOMETHING NEW" in capsys.readouterr().out


def test_cli_json_output(tmp_path, capsys):
    """--json emits machine-readable output."""
    path = _write_json(tmp_path, {"SOMETHING NEW": [{"A": 1}]}, "novel.json")
    tables_main([str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["unhandled"] == {"SOMETHING NEW": 1}


def test_cli_quiet(tmp_path, capsys):
    """--quiet suppresses output but keeps the exit code."""
    path = _write_json(tmp_path, {"SOMETHING NEW": [{"A": 1}]}, "novel.json")
    assert tables_main([str(path), "--quiet"]) == 1
    assert capsys.readouterr().out == ""


def test_cli_missing_file(tmp_path, capsys):
    """A missing file is a usage error (exit 2)."""
    assert tables_main([str(tmp_path / "nope.s2k")]) == 2
    assert "not found" in capsys.readouterr().err


# ══════════════════════════════════════════════════════════════════════
# Drift guard C — the docs registry block matches the code
# ══════════════════════════════════════════════════════════════════════

_DOCS_GENERATOR = Path(__file__).resolve().parent.parent / "docs" / "_generate_parser_tables.py"


def test_docs_registry_block_is_current():
    """docs/parser_coverage.md's generated registry block matches the registry."""
    proc = subprocess.run(
        [sys.executable, str(_DOCS_GENERATOR), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "docs/parser_coverage.md is out of date with io/table_registry.py — "
        "run `python docs/_generate_parser_tables.py`.\n"
        f"{proc.stdout}{proc.stderr}"
    )
