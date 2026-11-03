"""Shared tokeniser for CSI's ``TABLE: "…"`` text exports.

SAP2000 (``.s2k`` / ``.$2k``) and SAFE (``.f2k``) write their text models in
the same line grammar::

    TABLE:  "PROGRAM CONTROL"
       ProgramName=SAFE   Version=23.1.0   CurrUnits="kN, m, C"

    TABLE:  "FLOOR OBJECT CONNECTIVITY"
       "Unique Name"=863   UniquePt1=25   UniquePt2=24   Perimeter=3.9 _

A ``Key=Value`` pair is space-separated; a *quoted* value keeps its spaces
(``CurrUnits="kN, m, C"``), and a trailing ``_`` continues the record onto the
next physical line.  Two dialect differences matter and are both handled here:

* SAP2000 keys are single tokens (``Joint=1``); SAFE also writes **quoted
  multi-word keys** (``"Section Property"=…``, ``"Unique Name"=…``,
  ``"Maximum Mesh Size"=…``) which a bare ``\\w+`` key pattern would silently
  drop.
* Values are coerced to ``int`` / ``float`` when numeric, ``"Yes"/"No"`` to
  ``bool`` and ``"None"`` to ``None``, matching the original SAP2000 parser.

This is the single shared implementation used by
:mod:`fea_toolkit.io.s2k_parser` and :mod:`fea_toolkit.io.f2k_parser` so the
two cannot drift apart.  It imports nothing from ``opensees`` and nothing
optional.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Union

__all__ = ["parse_table_file", "read_file_with_encodings"]

#: Matches a ``TABLE: "NAME"`` header line.
_TABLE_RE = re.compile(r'^TABLE:\s+"([^"]+)"')

#: Matches one ``key=value`` pair.  The key is either a quoted string (SAFE
#: writes multi-word keys such as ``"Section Property"`` / ``"Unique Name"`` /
#: ``"Maximum Mesh Size"`` in quotes) or a bare non-space non-``=`` token
#: (SAP2000 / ETABS write single-token keys, and SAFE's bare keys may carry a
#: ``?`` such as ``Orthotropic?``).  The value is a quoted string (spaces
#: preserved) or a bare non-space token.
_KV_PAIR_RE = re.compile(r'("[^"]*"|[^\s=]+)=("[^"]*"|[^\s]+)')

#: Metadata header written on the first line of every CSI text export.
_METADATA_RE = re.compile(r"^File\s+(.+?)\s+was saved on\s+(.+?)\s+at\s+(.+?)$")


def _coerce(value: str) -> Any:
    """Coerce a raw token to int / float / bool / None / str.

    Identical to the historical SAP2000 parser's conversion: numeric strings
    become ``int`` then ``float``; ``Yes``/``No`` become ``bool``; ``None``
    becomes ``None``; anything else stays a string.
    """
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    upper = value.upper()
    if upper == "YES":
        return True
    if upper == "NO":
        return False
    if upper == "NONE":
        return None
    return value


def read_file_with_encodings(path: Union[str, Path]) -> str:
    """Read a CSI text export, trying several common encodings."""
    p = Path(path)
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace")


def parse_table_file(file_content: str) -> dict[str, list[dict[str, Any]]]:
    """Parse a CSI ``TABLE: "…"`` text export into ``{table_name: [row, …]}``.

    Args:
        file_content: Full text of the export.

    Returns:
        Dict mapping table names to lists of row dicts.  The first-line
        ``File … was saved on …`` header is captured as a single-row
        ``"METADATA"`` table (``FileName`` / ``DateSaved``) exactly as the
        original SAP2000 parser did.
    """
    lines = file_content.strip().splitlines()
    result: dict[str, list[dict[str, Any]]] = {}

    if lines:
        first = lines[0].strip()
        meta = _METADATA_RE.match(first)
        if meta:
            file_path = meta.group(1)
            date_str = meta.group(2)
            time_str = meta.group(3)
            formatted = ""
            if date_str != "m/d/yy" and time_str != "h:mm:ss":
                try:
                    parts = date_str.split("/")
                    if len(parts) == 3:
                        month, day, year = (int(p) for p in parts)
                        if year < 100:
                            year += 2000
                        hms = time_str.split(":")
                        if len(hms) == 3:
                            hour, minute, second = (int(t) for t in hms)
                            formatted = (
                                f"{year:04d}/{month:02d}/{day:02d} "
                                f"{hour:02d}:{minute:02d}:{second:02d}"
                            )
                except (ValueError, IndexError):
                    formatted = ""
            result["METADATA"] = [{"FileName": file_path, "DateSaved": formatted}]
        lines = lines[1:]

    current_table: Union[str, None] = None
    current_record: dict[str, Any] = {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        table_match = _TABLE_RE.match(line)
        if table_match:
            if current_record and current_table:
                result.setdefault(current_table, []).append(current_record)
                current_record = {}
            current_table = table_match.group(1)
            continue

        if current_table is None:
            continue

        ends_with_continuation = line.endswith("_")
        if ends_with_continuation:
            line = line[:-1].strip()

        for match in _KV_PAIR_RE.finditer(line):
            key = match.group(1)
            value = match.group(2)
            if key.startswith('"') and key.endswith('"'):
                key = key[1:-1]
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            current_record[key] = _coerce(value)

        if not ends_with_continuation and current_record:
            result.setdefault(current_table, []).append(current_record)
            current_record = {}

    if current_record and current_table:
        result.setdefault(current_table, []).append(current_record)

    return result
