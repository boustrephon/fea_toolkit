"""SAP2000 table-coverage registry — detect tables the toolkit does not consume.

SAP2000 writes dozens of tables into every ``.s2k`` / ``.json`` export (54–66
distinct tables in the models inspected so far), while the toolkit reads only a
subset.  When SAP2000 adds a table, or when a model uses a table the toolkit
has never handled, nothing currently reports it — the parser reads everything
into ``SAP2000Parser.raw_tables`` and silently ignores what it does not know.

This module provides the missing triage.  Every table in a parsed model is
classified into exactly one bucket:

``handled``
    The toolkit reads it (see :data:`HANDLED_TABLES` /
    :data:`HANDLED_PREFIXES`).
``known-gap``
    Recognised as structurally relevant but **not yet parsed** or consumed —
    a *tracked* hole (see :data:`KNOWN_GAP_TABLES`).
``ignored``
    Deliberately not read (design-code preferences, output stations,
    display options, bookkeeping) — not a hole (see :data:`IGNORED_TABLES`
    / :data:`IGNORED_PREFIXES`).
``unhandled``
    **Unrecognised** — the interesting case.  Either SAP2000 introduced a new
    table or the toolkit has never seen this one.  This is what the coverage
    report exists to surface.

Typical use::

    from fea_toolkit.io.s2k_parser import SAP2000Parser
    from fea_toolkit.io.table_registry import table_coverage

    parser = SAP2000Parser("model.s2k").parse()
    coverage = table_coverage(parser.raw_tables)
    print(coverage.unhandled)   # {'SOME NEW TABLE': 12}

Command line::

    python -m fea_toolkit.io.table_registry model.s2k
    python -m fea_toolkit.io.table_registry model.s2k --json

The registry is deliberately a hand-maintained literal (not derived from the
parser's source at import time) so that it is reviewable and diff-able; a unit
test keeps it honest by asserting that every table name the parser reads is
listed here — see ``tests/test_table_registry.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "HANDLED_PREFIXES",
    "HANDLED_TABLES",
    "IGNORED_PREFIXES",
    "IGNORED_TABLES",
    "KNOWN_GAP_PREFIXES",
    "KNOWN_GAP_TABLES",
    "TableCoverage",
    "classify",
    "format_table_coverage",
    "main",
    "table_coverage",
    "unhandled_tables",
]


# ═══════════════════════════════════════════════════════════════════════
# Tables the toolkit reads
# ═══════════════════════════════════════════════════════════════════════

#: Exact table names the parser (or another toolkit module) consumes.
#: ``STORY DATA`` is read by :mod:`fea_toolkit.model.stories`, not by the
#: parser, but it is consumed by the toolkit, so it belongs here.
HANDLED_TABLES: frozenset[str] = frozenset(
    {
        # ── program / units ──
        "PROGRAM CONTROL",
        # ── nodes, restraints, constraints ──
        "JOINT COORDINATES",
        "JOINT RESTRAINT ASSIGNMENTS",
        "JOINT CONSTRAINT ASSIGNMENTS",
        "STORY DATA",  # consumed by model.stories (not the parser)
        "STORY",  # legacy story table, also read by model.stories
        # ── materials, sections, rebar ──
        "MATERIAL PROPERTIES 01 - GENERAL",
        "REBAR SIZES",
        "FRAME SECTION PROPERTIES 01 - GENERAL",
        "FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN",
        "FRAME SECTION PROPERTIES 03 - CONCRETE BEAM",
        "AREA SECTION PROPERTIES",
        "AREA SECTION PROPERTY DESIGN PARAMETERS",
        # ── connectivity + assignments ──
        "CONNECTIVITY - FRAME",
        "CONNECTIVITY - AREA",
        "FRAME SECTION ASSIGNMENTS",
        "AREA SECTION ASSIGNMENTS",
        "FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL",
        "GROUPS 1 - DEFINITIONS",
        "GROUPS 2 - ASSIGNMENTS",
        # ── insertion point, end offsets, releases, meshing ──
        "FRAME INSERTION POINT ASSIGNMENTS",
        "FRAME END OFFSET ASSIGNMENTS",
        "FRAME END LENGTH OFFSETS",
        "FRAME OFFSET ALONG LENGTH ASSIGNMENTS",
        "FRAME RELEASE ASSIGNMENTS 1 - GENERAL",
        "FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY",
        "FRAME RELEASE ASSIGNMENTS",
        "FRAME RELEASES",
        "FRAME AUTO MESH ASSIGNMENTS",
        "AREA MESH ASSIGNMENTS",
        "AREA AUTO MESH ASSIGNMENTS",
        "AREA EDGE CONSTRAINT ASSIGNMENTS",
        # ── loads ──
        "LOAD PATTERN DEFINITIONS",
        "LOAD CASE DEFINITIONS",
        "JOINT LOADS - FORCE",
        "FRAME LOADS - DISTRIBUTED",
        "FRAME LOADS - GRAVITY",
        "FRAME LOADS - OPEN STRUCTURE WIND",
        "AREA LOADS - UNIFORM",
        "AREA LOADS - UNIFORM TO FRAME",
        "AREA LOADS - GRAVITY",
        "MASS SOURCE",
        "MASSES 1 - MASS SOURCE",
        # ── auto load-pattern generators ──
        # The AUTO family is open-ended (its suffix names the design code), so
        # the members the toolkit consumes are registered individually rather
        # than matched by a broad ``AUTO`` prefix — that would swallow tables
        # the toolkit does *not* read (e.g. ``AUTO WAVE 3 - ...``), hiding them
        # from the coverage report.
        "AUTO SEISMIC - LOAD PATTERN",
        "AUTO WIND - CHINESE 2010",
        "AUTO WIND EXPOSURE FOR HORIZONTAL DIAPHRAGMS",
    }
)

#: Table-name *prefixes* the parser consumes by family (scanned via
#: ``str.startswith`` over ``raw_tables``).  Any table matching one of these is
#: treated as handled.
#:
#: Only families whose members are *all* consumed belong here.  ``AREA LOADS``
#: and ``AUTO`` were deliberately removed: their members are mixed (``AUTO
#: WAVE`` is not read) and are now registered individually in
#: :data:`HANDLED_TABLES`, so an unrecognised member surfaces as ``unhandled``.
HANDLED_PREFIXES: tuple[str, ...] = (
    "MATERIAL PROPERTIES",  # 01/02/03A/03B/03E/03F/03J/06/09 merged in _get_all_materials()
    "CONSTRAINT DEFINITIONS - ",  # BODY / DIAPHRAGM / EQUAL / BEAM / ROD / PLATE / WELD / LOCAL
    "CASE -",  # every load-case table, merged into LoadCase.case_data
)

# ═══════════════════════════════════════════════════════════════════════
# Tables deliberately not read (not holes)
# ═══════════════════════════════════════════════════════════════════════

#: Exact table names that are intentionally skipped, mapped to a short reason.
IGNORED_TABLES: dict[str, str] = {
    "METADATA": "parser-generated for JSON round-trip; never read back",
    "ACTIVE DEGREES OF FREEDOM": "analysis DOF configuration",
    "ANALYSIS OPTIONS": "solver options",
    "COORDINATE SYSTEMS": "named coordinate systems (only GLOBAL is used)",
    "FRAME DESIGN PROCEDURES": "design configuration",
    "FRAME LOAD TRANSFER OPTIONS": "load-transfer settings",
    "FRAME OUTPUT STATION ASSIGNMENTS": "output station locations",
    "FRAME SECTION PROPERTIES 13 - TIME DEPENDENT": "creep / shrinkage",
    "AREA SECTION PROPERTY - TIME DEPENDENT": "creep / shrinkage (area sections)",
    "GRID LINES": "grid geometry",
    "PROJECT INFORMATION": "project metadata",
    # Auto wave-loading characteristics: no ``LoadPat`` column, so it is not a
    # load-pattern generator (the toolkit has no wave-loading support).  Listed
    # explicitly so ``_get_load_patterns()`` can tell a deliberate skip from a
    # new, unhandled AUTO variant.
    "AUTO WAVE 3 - WAVE CHARACTERISTICS - GENERAL": "wave loading not supported",
}

#: Table-name prefixes deliberately skipped, mapped to a short reason.  These
#: are whole families (design-code data, display options, export bookkeeping)
#: that are never structurally relevant to the toolkit.
IGNORED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("PREFERENCES - ", "design-code preference values"),
    ("OVERWRITES - ", "design-code overwrite values"),
    ("OPTIONS - ", "display / output options"),
    ("FUNCTION - ", "SAP-side function definitions (the toolkit builds its own)"),
    ("DATABASE ", "export bookkeeping (documentation / format types)"),
)

# ═══════════════════════════════════════════════════════════════════════
# Recognised but not yet parsed (tracked holes)
# ═══════════════════════════════════════════════════════════════════════

#: Exact table names recognised as structurally relevant but not yet parsed or
#: consumed, mapped to a short reason.  These are *known* holes — tracked work,
#: not surprises.
KNOWN_GAP_TABLES: dict[str, str] = {
    "COMBINATION DEFINITIONS": "load combinations not parsed — see _pending_work.md P12",
    "SOLID PROPERTY DEFINITIONS": "solid (brick) elements not supported",
    "JOINT PATTERN DEFINITIONS": "joint patterns (thickness / offset overwrites) not consumed",
}

#: Prefixes for known-gap families.
KNOWN_GAP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("SECTION DESIGNER PROPERTIES", "SD section geometry not parsed"),
)

_KNOWN_GAP_PREFIX_KEYS = tuple(p for p, _ in KNOWN_GAP_PREFIXES)
_IGNORED_PREFIX_KEYS = tuple(p for p, _ in IGNORED_PREFIXES)


# ═══════════════════════════════════════════════════════════════════════
# Classification
# ═══════════════════════════════════════════════════════════════════════


def classify(table_name: str) -> str:
    """Classify one SAP2000 table name.

    Args:
        table_name: Table name as it appears in the ``.s2k`` / ``.json`` file
            (e.g. ``"COMBINATION DEFINITIONS"``).

    Returns:
        One of ``"handled"``, ``"known-gap"``, ``"ignored"`` or
        ``"unhandled"``.  ``"handled"`` takes precedence when a name matches
        more than one registry (a handled table is never a gap).
    """
    if table_name in HANDLED_TABLES or table_name.startswith(HANDLED_PREFIXES):
        return "handled"
    if table_name in KNOWN_GAP_TABLES or table_name.startswith(_KNOWN_GAP_PREFIX_KEYS):
        return "known-gap"
    if table_name in IGNORED_TABLES or table_name.startswith(_IGNORED_PREFIX_KEYS):
        return "ignored"
    return "unhandled"


def reason_for(table_name: str) -> Optional[str]:
    """Return the registry reason for an ignored / known-gap table, else ``None``."""
    if table_name in KNOWN_GAP_TABLES:
        return KNOWN_GAP_TABLES[table_name]
    if table_name in IGNORED_TABLES:
        return IGNORED_TABLES[table_name]
    for prefix, reason in KNOWN_GAP_PREFIXES:
        if table_name.startswith(prefix):
            return reason
    for prefix, reason in IGNORED_PREFIXES:
        if table_name.startswith(prefix):
            return reason
    return None


# ═══════════════════════════════════════════════════════════════════════
# Coverage result
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TableCoverage:
    """Triaged inventory of the tables present in a parsed model.

    Each mapping is ``{table_name: row_count}``.

    Attributes:
        handled: Tables the toolkit consumes.
        known_gaps: Recognised, structurally relevant, not yet parsed.
        ignored: Deliberately skipped (design / output / bookkeeping).
        unhandled: Unrecognised tables — the interesting output.
    """

    handled: dict[str, int] = field(default_factory=dict)
    known_gaps: dict[str, int] = field(default_factory=dict)
    ignored: dict[str, int] = field(default_factory=dict)
    unhandled: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        """True when no unrecognised (unhandled) tables are present."""
        return not self.unhandled

    @property
    def gaps(self) -> dict[str, int]:
        """``known_gaps`` merged with ``unhandled`` — every hole, tracked or not."""
        return {**self.known_gaps, **self.unhandled}

    def to_dict(self) -> dict[str, Any]:
        """Serialisable summary (counts plus the full name→rows mappings)."""
        return {
            "clean": self.clean,
            "counts": {
                "handled": len(self.handled),
                "known_gaps": len(self.known_gaps),
                "ignored": len(self.ignored),
                "unhandled": len(self.unhandled),
            },
            "handled": dict(self.handled),
            "known_gaps": {
                name: {"rows": rows, "reason": reason_for(name)}
                for name, rows in self.known_gaps.items()
            },
            "ignored": dict(self.ignored),
            "unhandled": dict(self.unhandled),
        }


def table_coverage(raw_tables: dict[str, list[dict[str, Any]]]) -> TableCoverage:
    """Triage every table in a parsed model.

    Args:
        raw_tables: The parser's ``raw_tables`` mapping (``{table: [rows]}``).
            Also accepts the ``SAPModelData``-style dict-like value; only the
            keys and the row count per key are used.

    Returns:
        A :class:`TableCoverage` with one bucket per table.
    """
    coverage = TableCoverage()
    for table_name, records in raw_tables.items():
        n_rows = len(records) if hasattr(records, "__len__") else 0
        bucket = classify(table_name)
        if bucket == "handled":
            coverage.handled[table_name] = n_rows
        elif bucket == "known-gap":
            coverage.known_gaps[table_name] = n_rows
        elif bucket == "ignored":
            coverage.ignored[table_name] = n_rows
        else:
            coverage.unhandled[table_name] = n_rows
    return coverage


def unhandled_tables(raw_tables: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Return only the unrecognised tables (``{name: rows}``).

    Convenience wrapper for the common "did anything unexpected appear?"
    check::

        assert not unhandled_tables(parser.raw_tables)
    """
    return table_coverage(raw_tables).unhandled


# ═══════════════════════════════════════════════════════════════════════
# Rendering + command line
# ═══════════════════════════════════════════════════════════════════════


def format_table_coverage(coverage: TableCoverage, *, show_ignored: bool = False) -> str:
    """Render a :class:`TableCoverage` as fixed-width text.

    Args:
        coverage: Coverage result to render.
        show_ignored: Also list the deliberately-ignored tables.  Off by
            default because that bucket is typically the largest and least
            interesting.

    Returns:
        A printable multi-line string.  The headline always reports whether
        the model is clean, then lists known gaps and unrecognised tables.
    """
    lines: list[str] = []
    n_unhandled = len(coverage.unhandled)
    if coverage.clean:
        lines.append("Table coverage: CLEAN — no unrecognised tables.")
    else:
        lines.append(
            f"Table coverage: {n_unhandled} UNHANDLED table(s) — "
            "SAP2000 may have added a table, or the toolkit has never seen it."
        )
    if not coverage.handled:
        lines.append(
            "  (No recognised SAP2000 tables — is this really an .s2k / SAP table export?)"
        )

    if coverage.known_gaps:
        lines.append("")
        lines.append(f"Known gaps ({len(coverage.known_gaps)}) — tracked, not parsed:")
        for name, rows in sorted(coverage.known_gaps.items()):
            lines.append(f"  {name:<52} {rows:>7} rows  — {reason_for(name)}")

    if coverage.unhandled:
        lines.append("")
        lines.append(f"Unhandled ({n_unhandled}) — unrecognised:")
        for name, rows in sorted(coverage.unhandled.items()):
            lines.append(f"  {name:<52} {rows:>7} rows")

    if show_ignored:
        lines.append("")
        lines.append(f"Ignored ({len(coverage.ignored)}) — deliberately skipped:")
        for name, rows in sorted(coverage.ignored.items()):
            lines.append(f"  {name:<52} {rows:>7} rows  — {reason_for(name)}")

    lines.append("")
    lines.append(
        f"Handled: {len(coverage.handled)}   "
        f"Known gaps: {len(coverage.known_gaps)}   "
        f"Ignored: {len(coverage.ignored)}   "
        f"Unhandled: {n_unhandled}"
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """Command-line entry point for the table-coverage report.

    Usage::

        python -m fea_toolkit.io.table_registry model.s2k
        python -m fea_toolkit.io.table_registry model.s2k --show-ignored
        python -m fea_toolkit.io.table_registry model.s2k --json
        python -m fea_toolkit.io.table_registry model.json --quiet

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` when no unrecognised tables are present,
        ``1`` when there are (so the command is usable as a CI gate), ``2``
        on a usage / file error.
    """
    ap = argparse.ArgumentParser(
        prog="fea_toolkit.io.table_registry",
        description="Report SAP2000 .s2k tables the toolkit does not consume.",
    )
    ap.add_argument("path", help="Path to the .s2k / .$2k / .json model file.")
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    ap.add_argument(
        "--show-ignored",
        action="store_true",
        help="Also list deliberately-ignored tables (default: hidden).",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Print nothing; rely on the exit code only (implies --json off).",
    )
    args = ap.parse_args(argv)

    from .s2k_parser import SAP2000Parser  # local import keeps this module light

    source = Path(args.path)
    if not source.exists():
        print(f"error: file not found: {source}", file=sys.stderr)
        return 2

    try:
        if source.suffix.lower() == ".json":
            parser = SAP2000Parser.from_json(source)
        else:
            parser = SAP2000Parser(source).parse()
        raw_tables = parser.raw_tables
    except Exception as exc:
        print(f"error: could not parse {source}: {exc}", file=sys.stderr)
        return 2

    coverage = table_coverage(raw_tables)

    if not args.quiet:
        if args.json:
            print(json.dumps(coverage.to_dict(), indent=2, sort_keys=True))
        else:
            print(format_table_coverage(coverage, show_ignored=args.show_ignored))

    return 0 if coverage.clean else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
