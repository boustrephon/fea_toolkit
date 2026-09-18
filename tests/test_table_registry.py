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
from typing import Optional

import pytest

from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.io.table_registry import (
    HANDLED_PREFIXES,
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


def _class_string_constants(tree: ast.AST) -> dict[str, str]:
    """Map class-level string constants (``_AREA_LOADS_PREFIX``) to their values.

    The parser holds family prefixes as class attributes rather than inline
    literals, so a prefix scan must resolve ``self.<CONST>`` if the drift guard
    is to see which table families it touches.
    """
    constants: dict[str, str] = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            else:
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
    return constants


def _resolved_self_constant(node: ast.AST, self_constants: dict[str, str]) -> Optional[str]:
    """Resolve ``self.<NAME>`` to the class-level string constant it names."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return self_constants.get(node.attr)
    return None


def _local_prefix_scope(fn: ast.AST) -> dict[str, set[str]]:
    """Resolve names in *fn* to the string constants they carry.

    Two shapes are supported, both needed to see the ``CASE - RESPONSE
    SPECTRUM`` scan hidden behind a local variable:

    * a local constant assignment — ``handled_prefixes = ("CASE - ...",)``;
    * a comprehension / loop target iterating such a name —
      ``any(t.startswith(p) for p in handled_prefixes)``.

    Returns:
        Mapping from local name to its resolved string constants.
    """
    assigned: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        vals = _string_constants(value)
        if not vals:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assigned.setdefault(target.id, set()).update(vals)

    scope: dict[str, set[str]] = dict(assigned)
    for node in ast.walk(fn):
        if not isinstance(node, (ast.comprehension, ast.For)):
            continue
        target, iterable = node.target, node.iter
        if isinstance(target, ast.Name) and isinstance(iterable, ast.Name):
            scope[target.id] = assigned.get(iterable.id, set())
    return scope


def _startswith_prefixes(
    node: ast.Call,
    self_constants: dict[str, str],
    local_scope: dict[str, set[str]],
) -> set[str]:
    """Return the prefixes a ``startswith(...)`` call scans.

    Literal arguments (``.startswith("CASE -")``), class constants
    (``.startswith(self._AUTO_PREFIX)``), and local names — a bare constant or
    a comprehension / loop variable such as ``p`` from ``handled_prefixes`` —
    all count.
    """
    prefixes = _string_constants(node)
    for sub in ast.walk(node):
        resolved = _resolved_self_constant(sub, self_constants)
        if resolved is not None:
            prefixes.add(resolved)
        if isinstance(sub, ast.Name):
            prefixes |= local_scope.get(sub.id, set())
    return prefixes


def _sliced_table_prefix(node: ast.AST, self_constants: dict[str, str]) -> Optional[str]:
    """Return the prefix in ``<var>[len(self.<CONST>) :]``, else ``None``."""
    if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Slice):
        return None
    lower = node.slice.lower
    if (
        not isinstance(lower, ast.Call)
        or not isinstance(lower.func, ast.Name)
        or lower.func.id != "len"
        or len(lower.args) != 1
    ):
        return None
    return _resolved_self_constant(lower.args[0], self_constants)


def extract_parser_table_names() -> set[str]:
    """Return every table name the parser source reads (any dispatch style).

    Includes class-constant family prefixes such as ``self._AREA_LOADS_PREFIX``
    / ``self._AUTO_PREFIX`` and the concrete members the suffix-dispatch
    branches select from them (``AREA LOADS - GRAVITY`` …), so the drift guard
    covers those dispatch branches.  Use
    :func:`extract_parser_family_prefixes` to tell the two apart.
    """
    names, _ = _extract_parser_references()
    return names


def extract_parser_family_prefixes() -> set[str]:
    """Return the ``.startswith(...)`` prefixes — families, not table names.

    ``AREA LOADS - `` / ``AUTO`` are deliberately not registered as handled
    *prefix families* in :mod:`fea_toolkit.io.table_registry` — their members
    are mixed, so registration is per-member and an unrecognised member must
    surface as ``unhandled``.  The guard therefore checks these prefixes
    against the registered members rather than through ``classify()``.
    """
    _, prefixes = _extract_parser_references()
    return prefixes


def _extract_parser_references() -> tuple[set[str], set[str]]:
    """Return ``(table names, startswith family prefixes)`` read by the parser."""
    tree = ast.parse(PARSER_SRC.read_text(encoding="utf-8"))
    self_constants = _class_string_constants(tree)
    names: set[str] = set()
    prefixes: set[str] = set()

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

    # 3. Prefix-scan families: ``.startswith("PREFIX")`` and
    #    ``.startswith(self._SOME_PREFIX)`` inside a function that reads
    #    _raw_tables (scoping excludes unrelated startswith calls).
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _references_raw_tables(fn):
            continue
        local_scope = _local_prefix_scope(fn)
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
            ):
                prefixes |= _startswith_prefixes(node, self_constants, local_scope)

        # 3a. Suffix dispatch: ``<var> = table_name[len(self._PREFIX):]`` joined
        #     with the literals its branches compare against, e.g. "GRAVITY"
        #     under ``_AREA_LOADS_PREFIX`` -> "AREA LOADS - GRAVITY".
        slice_prefixes: dict[str, str] = {}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                prefix = _sliced_table_prefix(node.value, self_constants)
                if prefix is not None:
                    slice_prefixes[node.targets[0].id] = prefix
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
                slice_prefix = slice_prefixes.get(node.left.id)
                if slice_prefix is None:
                    continue
                for comparator in node.comparators:
                    names |= {slice_prefix + suffix for suffix in _string_constants(comparator)}

        # 4. Inline tuples iterated by a loop whose body reads _raw_tables
        #    (e.g. AREA MESH ASSIGNMENTS / AREA AUTO MESH ASSIGNMENTS).
        for node in ast.walk(fn):
            if isinstance(node, ast.For) and _references_raw_tables(node):
                names |= _string_constants(node.iter)

    # A family prefix is not a table name, but keeping it in ``names`` gives a
    # single inventory of what the parser's scans touch.  The drift guard
    # subtracts ``prefixes`` before applying ``classify()``, so a bare prefix
    # cannot mask a genuinely unregistered table read.
    names |= prefixes
    return names, prefixes


def test_extractor_finds_tables():
    """The AST extractor itself must find a sensible number of names."""
    names = extract_parser_table_names()
    assert len(names) > 25, f"extractor found only {len(names)} names: {sorted(names)}"


def test_extractor_resolves_class_constant_prefixes():
    """Family prefixes held as class attributes resolve to their values."""
    names = extract_parser_table_names()
    assert {"AREA LOADS - ", "AUTO"} <= names
    # … and the suffix-dispatch branches expand to the members they select.
    assert {
        "AREA LOADS - UNIFORM",
        "AREA LOADS - UNIFORM TO FRAME",
        "AREA LOADS - GRAVITY",
    } <= names


def test_extractor_resolves_local_prefix_iterables():
    """Family prefixes reached through a local iterable are resolved too.

    ``get_load_cases()`` skips already-handled tables with
    ``any(t.startswith(p) for p in handled_prefixes)`` where
    ``handled_prefixes = ("CASE - RESPONSE SPECTRUM",)``.  That prefix must
    appear in the inventory, otherwise a drift in that skip guard would go
    unnoticed by :func:`test_parser_table_names_are_registered`.
    """
    assert "CASE - RESPONSE SPECTRUM" in extract_parser_family_prefixes()
    assert "CASE - RESPONSE SPECTRUM" in extract_parser_table_names()


def test_parser_table_names_are_registered():
    """Every concrete table name the parser reads must be in the coverage registry."""
    family_prefixes = extract_parser_family_prefixes()
    unregistered = sorted(
        n for n in extract_parser_table_names() - family_prefixes if classify(n) == "unhandled"
    )
    assert not unregistered, (
        "These table names are read by s2k_parser.py but are absent from the "
        f"table_coverage registry: {unregistered}\n"
        "Add them to HANDLED_TABLES (or KNOWN_GAP_TABLES / IGNORED_TABLES) in "
        "src/fea_toolkit/io/table_registry.py."
    )


def test_family_prefixes_have_registered_members():
    """A scanned family prefix must match at least one registered member.

    ``AREA LOADS - `` / ``AUTO`` are families, not table names: the registry
    registers their members individually so an unrecognised member surfaces as
    ``unhandled`` instead of being swallowed by a broad prefix (see
    :mod:`fea_toolkit.io.table_registry`).  A prefix is therefore covered when
    it is a registered prefix family, when at least one handled member is
    registered, or when it is a *narrower* scan of a registered family
    (``CASE - RESPONSE SPECTRUM`` under the registered ``CASE -`` — the skip
    guard in ``get_load_cases()``).  A *new* dispatch branch is caught
    individually by the suffix expansion in :func:`extract_parser_table_names`.
    """
    uncovered = sorted(
        prefix
        for prefix in extract_parser_family_prefixes()
        if prefix not in HANDLED_PREFIXES
        and not any(name.startswith(prefix) for name in HANDLED_TABLES)
        and not any(prefix.startswith(handled) for handled in HANDLED_PREFIXES)
    )
    assert not uncovered, (
        "These startswith() prefixes scanned by s2k_parser.py have no registered "
        f"member in the table_coverage registry: {uncovered}"
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
        "COMBINATION DEFINITIONS",
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
    assert classify("JOINT PATTERN DEFINITIONS") == "known-gap"
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
        "SOLID PROPERTY DEFINITIONS": [{"Name": "C1"}],
        "SOMETHING NEW": [{}, {}, {}],
    }


def test_table_coverage_buckets():
    """Each table lands in the right bucket with its row count."""
    cov = table_coverage(_raw_tables())
    assert cov.handled == {"JOINT COORDINATES": 2}
    assert cov.ignored == {"METADATA": 1}
    assert cov.known_gaps == {"SOLID PROPERTY DEFINITIONS": 1}
    assert cov.unhandled == {"SOMETHING NEW": 3}


def test_unhandled_tables_convenience():
    """The convenience accessor returns only the unrecognised tables."""
    assert unhandled_tables(_raw_tables()) == {"SOMETHING NEW": 3}


def test_coverage_clean_and_gaps():
    """``clean`` tracks unhandled only; ``gaps`` merges known + unhandled."""
    cov = table_coverage(_raw_tables())
    assert cov.clean is False
    assert cov.gaps == {"SOLID PROPERTY DEFINITIONS": 1, "SOMETHING NEW": 3}

    clean = table_coverage({"JOINT COORDINATES": [{"Joint": 1}]})
    assert clean.clean is True
    assert clean.gaps == {}


def test_to_dict_shape():
    """``to_dict`` is JSON-serialisable and carries reasons for known gaps."""
    d = table_coverage(_raw_tables()).to_dict()
    assert d["clean"] is False
    assert d["counts"] == {"handled": 1, "known_gaps": 1, "ignored": 1, "unhandled": 1}
    assert d["known_gaps"]["SOLID PROPERTY DEFINITIONS"]["rows"] == 1
    assert d["known_gaps"]["SOLID PROPERTY DEFINITIONS"]["reason"]
    json.dumps(d)  # must not raise


def test_format_table_coverage_clean():
    """A clean coverage renders the CLEAN headline."""
    text = format_table_coverage(table_coverage({"JOINT COORDINATES": []}))
    assert "CLEAN" in text


def test_format_table_coverage_lists_unhandled():
    """Unrecognised tables are named in the rendered text."""
    text = format_table_coverage(table_coverage(_raw_tables()))
    assert "SOMETHING NEW" in text
    assert "SOLID PROPERTY DEFINITIONS" in text
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
