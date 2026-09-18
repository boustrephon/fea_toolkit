"""Generate the per-program, per-jurisdiction model-review checklist editions.

Reads the single tagged source (``docs/ETABS_checklist_src.md``) and writes one
markdown document per entry in :data:`OUTPUTS`.  A filterable table is any
markdown table whose header contains one or more tag-dimension columns
(``Jurisdiction`` and/or ``Program``); only rows whose tag matches the target
for every such column are kept, and those columns are dropped from the output.
Tables without a tag column pass through unchanged, and ``{placeholder}``
tokens are substituted per edition.

Run from the repository root::

    python docs/checklist/build.py            # regenerate the editions
    python docs/checklist/build.py --check     # exit non-zero if any is stale
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "ETABS_checklist_src.md"
GENERAL = "general"
TAG_DIMS = ("jurisdiction", "program")

OUTPUTS: dict[str, dict[str, str]] = {
    "ETABS_checklist_is.md": {"jurisdiction": "is", "program": "etabs"},
    "ETABS_checklist_gb.md": {"jurisdiction": "gb", "program": "etabs"},
    "ETABS_checklist_hk.md": {"jurisdiction": "hk", "program": "etabs"},
    "SAP2000_checklist_gb.md": {"jurisdiction": "gb", "program": "sap2000"},
    "SAP2000_checklist_hk.md": {"jurisdiction": "hk", "program": "sap2000"},
}

DISPLAY = {
    "jurisdiction": {
        "is": "India (IS)",
        "gb": "Mainland China (GB / JGJ)",
        "hk": "Hong Kong (Codes of Practice)",
    },
    "program": {"etabs": "ETABS", "sap2000": "SAP2000"},
}

TRADEMARK = {
    "etabs": "**ETABS® and CSi® are registered trademarks of Computers and Structures, Inc. (CSI).**",
    "sap2000": "**SAP2000® and CSi® are registered trademarks of Computers and Structures, Inc. (CSI).**",
}

CODE_NOTICE = {
    "is": "For Indian projects, verify the IS references (IS 456:2000, IS 1893 (Part 1): 2016, IS 875 Parts 1-5, IS 800:2007, IS 1786:2008, IS 13920:2016, IS 16700:2017) against the current BIS editions.",
    "gb": "For Mainland China projects, verify the GB / JGJ references (GB 50009-2012, GB 50010-2010 (2015), GB 50011-2010 (2016), GB 50017-2017, GB 50068-2018, GB 50007-2011, GB 50223-2008, GB 18306-2015, JGJ 3-2010) against the current MOHURD editions; provincial or municipal supplements may also apply.",
    "hk": "For Hong Kong projects, verify the Code of Practice references (Structural Use of Concrete 2013, Structural Use of Steel 2011, Dead and Imposed Loads 2011, Wind Effects 2019) against the current Buildings Department editions and any relevant Practice Notes (PNAPs).",
}

SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")


def _cells(line: str) -> list[str]:
    """Split one markdown table row into stripped cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(target: dict[str, str]) -> str:
    """Return the markdown edition for the ``target`` tag combination.

    Args:
        target: Mapping of tag dimension to the value to keep, e.g.
            ``{"jurisdiction": "gb", "program": "sap2000"}``.

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
            dim_cols = [(k, c.lower()) for k, c in enumerate(header) if c.lower() in TAG_DIMS]
            if dim_cols:
                drop = {k for k, _ in dim_cols}
                keep = [k for k in range(len(header)) if k not in drop]
                out.append("| " + " | ".join(header[k] for k in keep) + " |")
                out.append("|" + "---|" * len(keep))
                i += 2
                while (
                    i < n
                    and lines[i].lstrip().startswith("|")
                    and not SEP_RE.match(lines[i].strip())
                ):
                    row = _cells(lines[i])
                    if all(row[k].lower() in (GENERAL, target[dim]) for k, dim in dim_cols):
                        out.append("| " + " | ".join(row[k] for k in keep) + " |")
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
    text = "\n".join(out)
    substitutions = {
        "{jurisdiction}": DISPLAY["jurisdiction"][target["jurisdiction"]],
        "{program}": DISPLAY["program"][target["program"]],
        "{trademark}": TRADEMARK[target["program"]],
        "{code_notice}": CODE_NOTICE[target["jurisdiction"]],
    }
    for key, value in substitutions.items():
        text = text.replace(key, value)
    return text


def main(argv=None) -> int:
    """Regenerate (or --check) every edition listed in :data:`OUTPUTS`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if any output is stale")
    args = parser.parse_args(argv)
    stale: list[str] = []
    for filename, target in OUTPUTS.items():
        text = render(target)
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
