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


def fetched_ids() -> list[str]:
    """Return the locally-fetched LMSR-N record IDs (PEER-only excluded), in table order."""
    return [rid for rid in LMSR_N_IDS if rid not in PEER_ONLY]


def peer_only_ids() -> list[str]:
    """Return the PEER NGA-West2-only record IDs, in table order."""
    return [rid for rid in LMSR_N_IDS if rid in PEER_ONLY]
