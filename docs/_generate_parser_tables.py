#!/usr/bin/env python3
"""Regenerate the registry block in ``docs/parser_coverage.md``.

The table-coverage registry lives in
``src/fea_toolkit/io/table_registry.py`` and is the single source of truth for
which SAP2000 tables the toolkit handles, ignores, or has a known gap on.
This script renders that registry into a delimited block in the coverage doc
so the two cannot drift apart.

Usage:
    python docs/_generate_parser_tables.py           # rewrite the block
    python docs/_generate_parser_tables.py --check   # exit 1 when stale

``--check`` is exercised by ``tests/test_table_registry.py`` so a registry
edit that is not reflected in the docs fails CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).parent
REPO = DOCS_DIR.parent
DOC_PATH = DOCS_DIR / "parser_coverage.md"
BEGIN = "<!-- BEGIN GENERATED: registry -->"
END = "<!-- END GENERATED: registry -->"

sys.path.insert(0, str(REPO / "src"))


def render_block() -> str:
    """Render the registry as a Markdown block (including the markers)."""
    from fea_toolkit.io.table_registry import (
        HANDLED_PREFIXES,
        HANDLED_TABLES,
        IGNORED_PREFIXES,
        IGNORED_TABLES,
        KNOWN_GAP_PREFIXES,
        KNOWN_GAP_TABLES,
    )

    lines: list[str] = [
        BEGIN,
        "",
        "*Generated from `src/fea_toolkit/io/table_registry.py` — do not edit by "
        "hand.  Regenerate with `python docs/_generate_parser_tables.py`.*",
        "",
        f"### Handled ({len(HANDLED_TABLES)} exact names)",
        "",
    ]
    lines += [f"- `{name}`" for name in sorted(HANDLED_TABLES)]

    lines += ["", "### Handled prefix families", ""]
    lines += [f"- `{prefix}*`" for prefix in HANDLED_PREFIXES]

    lines += ["", f"### Known gaps ({len(KNOWN_GAP_TABLES)} exact names)", ""]
    if KNOWN_GAP_TABLES:
        lines += [f"- `{name}` — {reason}" for name, reason in sorted(KNOWN_GAP_TABLES.items())]
    else:
        lines.append("- *(none)*")
    lines += ["", "### Known-gap prefix families", ""]
    if KNOWN_GAP_PREFIXES:
        lines += [f"- `{prefix}*` — {reason}" for prefix, reason in KNOWN_GAP_PREFIXES]
    else:
        lines.append("- *(none)*")

    lines += ["", f"### Ignored ({len(IGNORED_TABLES)} exact names)", ""]
    lines += [f"- `{name}` — {reason}" for name, reason in sorted(IGNORED_TABLES.items())]
    lines += ["", "### Ignored prefix families", ""]
    lines += [f"- `{prefix}*` — {reason}" for prefix, reason in IGNORED_PREFIXES]

    lines += ["", END]
    return "\n".join(lines)


def _replace_block(text: str, block: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 or end == -1:
        raise SystemExit(
            f"error: markers {BEGIN!r} / {END!r} not found in {DOC_PATH}. "
            "Add them before running this generator."
        )
    end += len(END)
    return text[:start] + block + text[end:]


def main(argv: list[str] | None = None) -> int:
    """Rewrite (or check) the generated block in ``docs/parser_coverage.md``."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 (without writing) when the block is out of date.",
    )
    args = ap.parse_args(argv)

    current = DOC_PATH.read_text(encoding="utf-8")
    updated = _replace_block(current, render_block())

    if args.check:
        if current != updated:
            print(
                f"{DOC_PATH.name} is out of date — run `python docs/_generate_parser_tables.py`.",
                file=sys.stderr,
            )
            return 1
        print(f"{DOC_PATH.name} registry block is up to date.")
        return 0

    DOC_PATH.write_text(updated, encoding="utf-8")
    print(f"✔ Updated {DOC_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
