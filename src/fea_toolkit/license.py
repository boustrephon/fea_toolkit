"""Licence and third-party notice text for packaged builds.

A Nuitka build bundles ``fea_toolkit`` (GPL-3.0-or-later) together with the
third-party libraries it links, and — in the ``--with-opensees`` variant — the
OpenSees/OpenSeesPy runtime.  This module centralises the short-form licence
text that the command-line entry points print under ``--license``, and names the
full texts (``LICENSE``, ``OpenSees-COPYRIGHT.txt``, ``THIRD-PARTY-NOTICES.md``)
that are bundled beside the binary.

The module imports only the standard library, so ``--license`` works even on a
bare install with no ``openseespy`` or ``pyvista`` present.  :func:`print_license`
detects at runtime whether OpenSeesPy is actually bundled and adjusts the
summary accordingly, so a "separate" build does not claim to embed OpenSees.
"""

from __future__ import annotations

import argparse
import importlib.util
from typing import Optional

# ── Short-form licence summary (assembled from parts) ──
_HEADER = """\
{header}
Copyright (C) the fea_toolkit authors.
Licence: GNU General Public License v3 or later (GPL-3.0-or-later).
Full text: LICENSE (bundled alongside this program).
"""

_OPENSEES_EMBEDDED = """\
This build embeds OpenSees and OpenSeesPy:

  * OpenSees (c) 1999-2020 The Regents of the University of California.
    Free for research, education and internal use.  Redistribution of a
    product that incorporates OpenSees requires a licence from the
    University of California Office of Technology Licensing
    (see OpenSees-COPYRIGHT.txt, bundled alongside this program).

  * OpenSeesPy -- free for research, education and internal use.  Commercial
    redistribution (an application or cloud-based service that uses
    "import openseespy") requires a licence from Oregon State University
    (Dr. Minjie Zhu, zhum@oregonstate.edu).
"""

_OPENSEES_NOT_EMBEDDED = """\
This build does not embed OpenSees.  Analysis requires a separately-installed
OpenSees (or OpenSeesPy) that you have licensed yourself.
"""

_TAIL = """\
Full third-party notices: THIRD-PARTY-NOTICES.md (bundled alongside this program).

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.
"""


def bundled_with_opensees() -> bool:
    """Return True when the OpenSeesPy runtime is bundled in this build.

    In the "separate" Nuitka build ``openseespy`` is excluded with
    ``--nofollow-import-to``, so :func:`importlib.util.find_spec` returns
    ``None``; in the licensed build it resolves to a real spec.
    """
    return importlib.util.find_spec("openseespy") is not None


def license_summary(version: Optional[str] = None, with_opensees: Optional[bool] = None) -> str:
    """Return the short-form licence text printed by ``--license``.

    Args:
        version: Optional ``fea_toolkit`` version string; omitted when ``None``.
        with_opensees: Optional override; when ``None`` it is detected at
            runtime via :func:`bundled_with_opensees`.

    Returns:
        The multi-line licence and third-party notice summary.
    """
    header = f"fea_toolkit {version}" if version else "fea_toolkit"
    if with_opensees is None:
        with_opensees = bundled_with_opensees()
    section = _OPENSEES_EMBEDDED if with_opensees else _OPENSEES_NOT_EMBEDDED
    return "\n".join((_HEADER.format(header=header), section, _TAIL))


def print_license(version: Optional[str] = None, with_opensees: Optional[bool] = None) -> None:
    """Print the licence summary to stdout.

    Args:
        version: Optional ``fea_toolkit`` version string; omitted when ``None``.
        with_opensees: Optional override; detected at runtime when ``None``.
    """
    print(license_summary(version, with_opensees))


def add_license_argument(parser: argparse.ArgumentParser) -> None:
    """Add a ``--license`` flag to an :class:`argparse.ArgumentParser`.

    The flag is registered as ``store_true``; the caller checks ``args.license``
    after parsing and calls :func:`print_license` before exiting.  This keeps the
    helper dependency-free and usable by every CLI entry point (``python -m
    fea_toolkit``, ``fea-review``, ``fea-tables``, ``fea-gui``).

    Args:
        parser: The parser to register the flag on.
    """
    parser.add_argument(
        "--license",
        action="store_true",
        help="Show the licence and third-party notices and exit.",
    )
