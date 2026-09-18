"""Generate the per-jurisdiction ETABS checklist documents.

Reads the single tagged source (``docs/ETABS_checklist_src.md``) and writes one
markdown document per jurisdiction, dropping the trailing ``Jurisdiction``
column and keeping only the rows applicable to that jurisdiction.  Tables
without a ``Jurisdiction`` column (for example the phase summary) pass through
unchanged.  The ``{jurisdiction}`` placeholder is replaced with the display
name for the target.

Run from the repository root::

    python docs/checklist/build.py            # regenerate the outputs
    python docs/checklist/build.py --check     # exit non-zero if outputs are stale
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "ETABS_checklist_src.md"
GENERAL = "general"
JURISDICTIONS = {
    "is": ("ETABS_checklist_is.md", "Indian codes (IS)"),
    "gb": ("ETABS_checklist_gb.md", "Mainland China (GB / JGJ)"),
    "hk": ("ETABS_checklist_hk.md", "Hong Kong (Codes of Practice)"),
}
SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")


def _cells(line: str) -> list[str]:
    """Split one markdown table row into stripped cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(jurisdiction: str, display: str) -> str:
    """Return the markdown document for ``jurisdiction`` from the tagged source.

    Args:
        jurisdiction: The tag to keep (``"is"``, ``"gb"`` or ``"hk"``).
        display: Human-readable jurisdiction name substituted for the
            ``{jurisdiction}`` placeholder.

    Returns:
        The rendered markdown document as a string.
    """
    lines = SRC.read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("|") and i + 1 < n and SEP_RE.match(lines[i + 1].strip()):
            header = _cells(line)
            if header and header[-1].lower() == "jurisdiction":
                out.append("| " + " | ".join(header[:-1]) + " |")
                out.append("|" + "---|" * (len(header) - 1))
                i += 2
                while (
                    i < n
                    and lines[i].lstrip().startswith("|")
                    and not SEP_RE.match(lines[i].strip())
                ):
                    row = _cells(lines[i])
                    if row[-1].lower() in (GENERAL, jurisdiction):
                        out.append("| " + " | ".join(row[:-1]) + " |")
                    i += 1
                continue
            out.append(line)
            out.append(lines[i + 1])
            i += 2
            while i < n and lines[i].lstrip().startswith("|"):
                out.append(lines[i])
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out).replace("{jurisdiction}", display)


def main(argv=None) -> int:
    """Regenerate (or --check) the per-jurisdiction documents."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if outputs are stale")
    args = parser.parse_args(argv)
    stale: list[str] = []
    for key, (filename, display) in JURISDICTIONS.items():
        text = render(key, display)
        path = ROOT / "docs" / filename
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != text:
                stale.append(filename)
        elif not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print("wrote", path.relative_to(ROOT))
    if args.check and stale:
        print("stale outputs: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
