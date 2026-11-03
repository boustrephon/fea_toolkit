"""IK-2005 generic-frame collapse benchmark — the LMSR-N ground-motion manifest.

Single source of truth for the LMSR-N record set used by the IK-2005
generic-frame collapse benchmark (``docs/ik2005_generic_frame_benchmark.md``):
the 40 record IDs of IK-2005 Table 4.2 (Medina 2002), the 7 of them that only
exist in PEER NGA-West2, and the USGS multi-component channel selection.

The module itself imports no ``ops`` (pure data).  Importing it through
``fea_toolkit.benchmarks`` still loads that package's ``__init__`` (which
imports OpenSees via :mod:`fea_toolkit.benchmarks.sac`) — the collapse/IDA
runners that consume this manifest are OpenSees-dependent regardless.
"""

import os
from pathlib import Path

#: The 40 LMSR-N record IDs (IK-2005 Table 4.2, Medina 2002) in table order.
LMSR_N_IDS: list[str] = [
    # Imperial Valley 1979 (Mw 6.5)
    "IV79cal",
    "IV79chi",
    "IV79cmp",
    "IV79e01",
    "IV79e12",
    "IV79e13",
    "IV79nil",
    "IV79pls",
    "IV79qkp",
    "IV79wsm",
    # Loma Prieta 1989 (Mw 6.9)
    "LP89agw",
    "LP89cap",
    "LP89g03",
    "LP89g04",
    "LP89gmr",
    "LP89hch",
    "LP89hda",
    "LP89hvr",
    "LP89sjw",
    "LP89slc",
    "LP89svl",
    # Northridge 1994 (Mw 6.7)
    "NR94cen",
    "NR94cnp",
    "NR94far",
    "NR94fle",
    "NR94glp",
    "NR94hol",
    "NR94lh1",
    "NR94lv2",
    "NR94lv6",
    "NR94nya",
    "NR94pic",
    "NR94stc",
    "NR94stn",
    "NR94ver",
    # San Fernando 1971 (Mw 6.6)
    "SF71pel",
    # Superstition Hills 1987 (Mw 6.7)
    "SH87bra",
    "SH87icc",
    "SH87pls",
    "SH87wsm",
]

#: Records that only exist in PEER NGA-West2 (absent from CESMD and the USGS
#: NSMP open archives) — skipped until a PEER account fetches them as ``.AT2``.
PEER_ONLY: set[str] = {
    "LP89agw",  # Agnews State Hospital (CSMIP)
    "LP89sjw",  # Salinas - John & Work (CSMIP)
    "NR94lv6",  # Leona Valley #6 (CDMG)
    "NR94pic",  # LA - Pico & Sentous (CSMIP)
    "SH87bra",  # Brawley (USGS NSMN)
    "SH87icc",  # El Centro Imp. Co. Cent (USGS NSMN)
    "SH87pls",  # Plaster City (USGS NSMN)
}

#: USGS multi-component mirrors: the horizontal channel whose PGA matches IK-2005
#: Table 4.2 (Medina's per-station component).  CESMD records are single-file.
USGS_CHANNELS: dict[str, str] = {
    "IV79cal": "a",
    "IV79chi": "a",
    "IV79cmp": "a",
    "IV79e01": "a",
    "IV79e12": "a",
    "IV79e13": "a",
    "IV79nil": "a",
    "IV79pls": "a",
    "IV79qkp": "a",
    "IV79wsm": "a",
    "LP89hch": "c",
    "LP89hda": "c",
    "LP89slc": "c",
    "LP89svl": "c",
}


#: Environment variable overriding the ground-motion mirror location.  Tests set
#: this to a ``tmp_path`` so they never read or write the repository's real
#: ``data/ground_motion`` mirror; users may point it at their own mirror.
GROUND_MOTION_DIR_ENV = "FEA_TOOLKIT_GROUND_MOTION_DIR"


def ground_motion_dir() -> Path:
    """The ground-motion mirror root.

    Honors :data:`GROUND_MOTION_DIR_ENV` when set; otherwise the repository's
    ``data/ground_motion`` directory (derived from this file's location, never
    the working directory).
    """
    override = os.environ.get(GROUND_MOTION_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent.parent / "data" / "ground_motion"


def peer_only_present() -> list[str]:
    """Return the PEER-only IDs whose waveform is already in the mirror.

    PEER NGA-West2 records are downloaded as ``<record_id>.AT2`` (or ``.csv``)
    into ``data/ground_motion/{local,public}/`` (see :func:`ground_motion_dir`).
    This returns, in table order, the :data:`PEER_ONLY` IDs for which such a file
    exists — the 40-record re-run happens automatically once all seven are
    dropped in.
    """
    root = ground_motion_dir()
    present = {
        rid
        for rid in PEER_ONLY
        for sub in ("local", "public")
        for ext in (".AT2", ".csv")
        if (root / sub / f"{rid}{ext}").exists()
    }
    return [rid for rid in LMSR_N_IDS if rid in present]


def fetched_ids() -> list[str]:
    """Return the LMSR-N record IDs available to the runner, in table order.

    The 33 open-archive records (CESMD + USGS NSMP) are always included; a
    PEER-only record joins once its ``.AT2`` / ``.csv`` waveform is present in
    the mirror (see :func:`peer_only_present`).  So this returns 33 today and 40
    once the 7 NGA-West2 records are fetched.
    """
    present = set(peer_only_present())
    return [rid for rid in LMSR_N_IDS if rid not in PEER_ONLY or rid in present]


def peer_only_ids() -> list[str]:
    """Return the PEER NGA-West2-only record IDs, in table order."""
    return [rid for rid in LMSR_N_IDS if rid in PEER_ONLY]
