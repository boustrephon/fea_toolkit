"""SAFE (``.f2k`` / ``.$sf``) table-coverage triage.

Mirrors :mod:`fea_toolkit.io.table_registry` for SAFE's two export formats —
the ``.f2k`` tabular text export and the ``.$sf`` native database.  The
SAP2000 registry's :func:`~fea_toolkit.io.table_registry.classify` is keyed to
SAP2000 table names, so SAFE keeps its own hand-maintained sets here while
reusing the shared :class:`~fea_toolkit.io.table_registry.TableCoverage` result
type.
"""

from __future__ import annotations

from typing import Any

from .table_registry import TableCoverage

__all__ = [
    "SAFE_DB_HANDLED_PREFIXES",
    "SAFE_DB_HANDLED_TABLES",
    "SAFE_DB_IGNORED_TABLES",
    "SAFE_HANDLED_PREFIXES",
    "SAFE_HANDLED_TABLES",
    "SAFE_IGNORED_PREFIXES",
    "SAFE_IGNORED_TABLES",
    "safe_classify",
    "safe_db_classify",
    "safe_db_table_coverage",
    "safe_db_unhandled_tables",
    "safe_table_coverage",
    "safe_unhandled_tables",
]

#: Exact table names the SAFE ``.f2k`` parser consumes.
SAFE_HANDLED_TABLES: frozenset[str] = frozenset(
    {
        "PROGRAM CONTROL",
        "TENDON SECTION PROPERTIES",
        "AREA SECTION PROPERTY DEFINITIONS - SUMMARY",
        "SLAB PROPERTY DEFINITIONS",
        "DECK PROPERTY DEFINITIONS",
        "WALL PROPERTY DEFINITIONS - SPECIFIED",
        "LOAD PATTERN DEFINITIONS",
        "GROUP DEFINITIONS",
        "MASS SOURCE DEFINITION",
        "LOAD COMBINATION DEFINITIONS",
        "POINT OBJECT CONNECTIVITY",
        "NULL LINE OBJECT CONNECTIVITY",
        "FLOOR OBJECT CONNECTIVITY",
        "WALL OBJECT CONNECTIVITY",
        "STRIP OBJECT CONNECTIVITY",
        "JOINT ASSIGNMENTS - SPRINGS",
        "FRAME ASSIGNMENTS - SECTION PROPERTIES",
        "FRAME ASSIGNMENTS - LINE SPRINGS",
        "FRAME ASSIGNMENTS - FRAME AUTO MESH OPTIONS",
        "AREA ASSIGNMENTS - SECTION PROPERTIES",
        "AREA ASSIGNMENTS - AREA SPRINGS",
        "AREA ASSIGNMENTS - FLOOR AUTO MESH OPTIONS",
        "AREA LOAD ASSIGNMENTS - UNIFORM",
        "ANALYSIS OPTIONS - AUTOMATIC MESH SETTINGS FOR FLOORS",
        "ANALYSIS OPTIONS - AUTOMATIC RECTANGULAR MESH OPTIONS FOR WALLS",
    }
)

#: Table-name *prefixes* the SAFE ``.f2k`` parser consumes by family.
SAFE_HANDLED_PREFIXES: tuple[str, ...] = (
    "MATERIAL PROPERTIES",
    "FRAME SECTION PROPERTY DEFINITIONS",
    "SPRING PROPERTY DEFINITIONS",
    "LOAD CASE DEFINITIONS",
)

#: Exact ``.f2k`` table names deliberately skipped (design / bookkeeping / display).
SAFE_IGNORED_TABLES: frozenset[str] = frozenset(
    {
        "METADATA",
        "GRID DEFINITIONS - GENERAL",
        "GRID DEFINITIONS - GRID LINES",
        "MASS SUMMARY BY GROUP",
        "REINFORCING BAR SIZES",
        "LINK PROPERTY DEFINITIONS - SUMMARY",
        "LINK PROPERTY DEFINITIONS - LINEAR",
        "DIMENSION LINE OBJECT GEOMETRY",
        "JOINT ASSIGNMENTS - SUMMARY",
        "JOINT ASSIGNMENTS - FLOOR MESHING OPTION",
        "FRAME ASSIGNMENTS - SUMMARY",
        "FRAME ASSIGNMENTS - END LENGTH OFFSETS",
        "FRAME ASSIGNMENTS - INSERTION POINT",
        "FRAME ASSIGNMENTS - OUTPUT STATIONS",
        "AREA ASSIGNMENTS - SUMMARY",
        "AREA ASSIGNMENTS - INSERTION POINT",
        "AREA ASSIGNMENTS - WALL AUTO MESH OPTIONS",
        "AREA ASSIGNMENTS - AUTO EDGE CONSTRAINTS",
        "ANALYSIS MODELING OPTIONS",
        "MISCELLANEOUS OPTIONS",
        "PREFERENCES - TOLERANCE",
        "PREFERENCES - GRAPHICS",
        "PROJECT INFORMATION",
        "MATERIAL LIST BY OBJECT TYPE",
        "MATERIAL LIST BY SECTION PROPERTY",
    }
)

#: Prefixes of SAFE ``.f2k`` tables deliberately skipped.
SAFE_IGNORED_PREFIXES: tuple[str, ...] = (
    "ANALYSIS OPTIONS - SAPFIRE",
    "ANALYSIS OPTIONS - DESIGN AND RESPONSE RECOVERY",
    "ANALYSIS OPTIONS - CRACKING",
    "OPTIONS - COLORS",
)


def _classify(
    table_name: str,
    handled: frozenset[str],
    ignored: frozenset[str],
    handled_prefixes: tuple[str, ...],
    ignored_prefixes: tuple[str, ...],
) -> str:
    if table_name in handled:
        return "handled"
    if table_name in ignored:
        return "ignored"
    for prefix in handled_prefixes:
        if table_name.startswith(prefix):
            return "handled"
    for prefix in ignored_prefixes:
        if table_name.startswith(prefix):
            return "ignored"
    return "unhandled"


def safe_classify(table_name: str) -> str:
    """Classify one SAFE ``.f2k`` table name.

    Returns:
        ``"handled"``, ``"ignored"`` or ``"unhandled"``.
    """
    return _classify(
        table_name,
        SAFE_HANDLED_TABLES,
        SAFE_IGNORED_TABLES,
        SAFE_HANDLED_PREFIXES,
        SAFE_IGNORED_PREFIXES,
    )


def safe_table_coverage(raw_tables: dict[str, list[dict[str, Any]]]) -> TableCoverage:
    """Triage every table in a parsed SAFE model into a :class:`TableCoverage`."""
    coverage = TableCoverage()
    for table_name, records in raw_tables.items():
        n_rows = len(records) if hasattr(records, "__len__") else 0
        bucket = safe_classify(table_name)
        if bucket == "handled":
            coverage.handled[table_name] = n_rows
        elif bucket == "ignored":
            coverage.ignored[table_name] = n_rows
        else:
            coverage.unhandled[table_name] = n_rows
    return coverage


def safe_unhandled_tables(raw_tables: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Return only the unrecognised SAFE tables (``{name: rows}``)."""
    return {
        name: len(rows) for name, rows in raw_tables.items() if safe_classify(name) == "unhandled"
    }


# ═══════════════════════════════════════════════════════════════════
# SAFE native ``.$sf`` database tables
# ═══════════════════════════════════════════════════════════════════

SAFE_DB_HANDLED_TABLES: frozenset[str] = frozenset(
    {
        "PROGRAM CONTROL",
        "SLAB PROPERTY ASSIGNMENTS",
        "WALL PROPERTIES",
        "BEAM PROPERTY ASSIGNMENTS",
        "LINE SPRING ASSIGNMENTS",
        "TENDON PROPERTIES",
        "LOAD PATTERNS",
        "LOAD COMBINATIONS",
        "MASS SOURCE",
        "POINT RESTRAINT ASSIGNMENTS",
        "GROUP DEFINITIONS",
        "GROUP ASSIGNMENTS",
        "OBJECT GEOMETRY - POINT COORDINATES",
        "OBJECT GEOMETRY - LINES 01 - GENERAL",
        "OBJECT GEOMETRY - AREAS 01 - GENERAL",
        "OBJECT GEOMETRY - DESIGN STRIPS",
        "LOAD ASSIGNMENTS - SURFACE LOADS",
        "LOAD ASSIGNMENTS - LINE OBJECTS - DISTRIBUTED LOADS",
        "LOAD ASSIGNMENTS - POINT LOADS",
        "PUNCHING SHEAR DESIGN OVERWRITES 01 - GENERAL",
        "AUTOMATIC SLAB MESH OPTIONS",
    }
)

SAFE_DB_HANDLED_PREFIXES: tuple[str, ...] = (
    "MATERIAL PROPERTIES",
    "SLAB PROPERTIES",
    "BEAM PROPERTIES",
    "COLUMN PROPERTIES",
    "SPRING PROPERTIES",
    "LOAD CASES",
)

SAFE_DB_IGNORED_TABLES: frozenset[str] = frozenset(
    {
        "METADATA",
        "ADVANCED MODELING OPTIONS",
        "ADVANCED SAPFIRE OPTIONS",
        "BEAM DESIGN OVERWRITES",
        "BEAM INSERTION POINT",
        "COORDINATE SYSTEMS",
        "CRACKING ANALYSIS REINFORCEMENT",
        "DESIGN PREFERENCES 01 - RESISTANCE FACTORS",
        "DESIGN PREFERENCES 02 - REBAR COVER - SLABS",
        "DESIGN PREFERENCES 03 - REBAR COVER - BEAMS",
        "DESIGN PREFERENCES 04 - PRESTRESS DATA",
        "OBJECT GEOMETRY - DIMENSION LINES",
        "PROJECT INFORMATION",
        "REINFORCING BAR SIZES",
        "SLAB DESIGN OVERWRITES 01 - STRIP BASED",
        "SLAB DESIGN OVERWRITES 02 - FINITE ELEMENT BASED",
        "SLAB LINE RELEASES",
        "SOIL PROPERTIES",
    }
)


def safe_db_classify(table_name: str) -> str:
    """Classify one SAFE ``.$sf`` table name."""
    return _classify(
        table_name,
        SAFE_DB_HANDLED_TABLES,
        SAFE_DB_IGNORED_TABLES,
        SAFE_DB_HANDLED_PREFIXES,
        (),
    )


def safe_db_table_coverage(raw_tables: dict[str, list[dict[str, Any]]]) -> TableCoverage:
    """Triage every table in a parsed SAFE ``.$sf`` model."""
    coverage = TableCoverage()
    for table_name, records in raw_tables.items():
        n_rows = len(records) if hasattr(records, "__len__") else 0
        bucket = safe_db_classify(table_name)
        if bucket == "handled":
            coverage.handled[table_name] = n_rows
        elif bucket == "ignored":
            coverage.ignored[table_name] = n_rows
        else:
            coverage.unhandled[table_name] = n_rows
    return coverage


def safe_db_unhandled_tables(raw_tables: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Return only the unrecognised SAFE ``.$sf`` tables (``{name: rows}``)."""
    return {
        name: len(rows)
        for name, rows in raw_tables.items()
        if safe_db_classify(name) == "unhandled"
    }
