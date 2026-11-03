"""FEMA P-695 (ATC-63) far-field ground-motion record set — the manifest.

Single source of truth for the 22-record (44-component) FEMA P-695 far-field
set (FEMA 2009, Appendix A, Tables A-4A / A-4C) — the standard collapse-margin
(CMR) assessment set: 14 events (1971–1999, M 6.5–7.6), sites ≥ 10 km from
fault rupture, NEHRP site C/D.

Record Sequence Numbers (RSNs) and the two component file names are transcribed
from Table A-4C so a PEER NGA-West2 download can be driven directly (search by
RSN).  The module imports no ``ops`` (pure data).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FemaP695Record:
    """One FEMA P-695 far-field record (a station, with two components)."""

    seq: int  # 1..22 (Table A-4A order)
    rsn: int  # PEER NGA-West2 record sequence number
    event: str
    year: int
    magnitude: float
    station: str
    comp1: str  # component-1 PEER NGA file name
    comp2: str  # component-2 PEER NGA file name
    pga_max_g: float  # max of the two components (Table A-4C)


FAR_FIELD: tuple[FemaP695Record, ...] = (
    FemaP695Record(
        1,
        953,
        "Northridge",
        1994,
        6.7,
        "Beverly Hills - Mulhol",
        "NORTHR/MUL009",
        "NORTHR/MUL279",
        0.52,
    ),
    FemaP695Record(
        2,
        960,
        "Northridge",
        1994,
        6.7,
        "Canyon Country-WLC",
        "NORTHR/LOS000",
        "NORTHR/LOS270",
        0.48,
    ),
    FemaP695Record(
        3, 1602, "Duzce, Turkey", 1999, 7.1, "Bolu", "DUZCE/BOL000", "DUZCE/BOL090", 0.82
    ),
    FemaP695Record(
        4, 1787, "Hector Mine", 1999, 7.1, "Hector", "HECTOR/HEC000", "HECTOR/HEC090", 0.34
    ),
    FemaP695Record(
        5, 169, "Imperial Valley", 1979, 6.5, "Delta", "IMPVALL/H-DLT262", "IMPVALL/H-DLT352", 0.35
    ),
    FemaP695Record(
        6,
        174,
        "Imperial Valley",
        1979,
        6.5,
        "El Centro Array #11",
        "IMPVALL/H-E11140",
        "IMPVALL/H-E11230",
        0.38,
    ),
    FemaP695Record(
        7, 1111, "Kobe, Japan", 1995, 6.9, "Nishi-Akashi", "KOBE/NIS000", "KOBE/NIS090", 0.51
    ),
    FemaP695Record(
        8, 1116, "Kobe, Japan", 1995, 6.9, "Shin-Osaka", "KOBE/SHI000", "KOBE/SHI090", 0.24
    ),
    FemaP695Record(
        9, 1158, "Kocaeli, Turkey", 1999, 7.5, "Duzce", "KOCAELI/DZC180", "KOCAELI/DZC270", 0.36
    ),
    FemaP695Record(
        10, 1148, "Kocaeli, Turkey", 1999, 7.5, "Arcelik", "KOCAELI/ARC000", "KOCAELI/ARC090", 0.22
    ),
    FemaP695Record(
        11,
        900,
        "Landers",
        1992,
        7.3,
        "Yermo Fire Station",
        "LANDERS/YER270",
        "LANDERS/YER360",
        0.24,
    ),
    FemaP695Record(
        12, 848, "Landers", 1992, 7.3, "Coolwater", "LANDERS/CLW-LN", "LANDERS/CLW-TR", 0.42
    ),
    FemaP695Record(
        13, 752, "Loma Prieta", 1989, 6.9, "Capitola", "LOMAP/CAP000", "LOMAP/CAP090", 0.53
    ),
    FemaP695Record(
        14, 767, "Loma Prieta", 1989, 6.9, "Gilroy Array #3", "LOMAP/G03000", "LOMAP/G03090", 0.56
    ),
    FemaP695Record(
        15, 1633, "Manjil, Iran", 1990, 7.4, "Abbar", "MANJIL/ABBAR--L", "MANJIL/ABBAR--T", 0.51
    ),
    FemaP695Record(
        16,
        721,
        "Superstition Hills",
        1987,
        6.5,
        "El Centro Imp. Co.",
        "SUPERST/B-ICC000",
        "SUPERST/B-ICC090",
        0.36,
    ),
    FemaP695Record(
        17,
        725,
        "Superstition Hills",
        1987,
        6.5,
        "Poe Road (temp)",
        "SUPERST/B-POE270",
        "SUPERST/B-POE360",
        0.45,
    ),
    FemaP695Record(
        18,
        829,
        "Cape Mendocino",
        1992,
        7.0,
        "Rio Dell Overpass",
        "CAPEMEND/RIO270",
        "CAPEMEND/RIO360",
        0.55,
    ),
    FemaP695Record(
        19, 1244, "Chi-Chi, Taiwan", 1999, 7.6, "CHY101", "CHICHI/CHY101-E", "CHICHI/CHY101-N", 0.44
    ),
    FemaP695Record(
        20, 1485, "Chi-Chi, Taiwan", 1999, 7.6, "TCU045", "CHICHI/TCU045-E", "CHICHI/TCU045-N", 0.51
    ),
    FemaP695Record(
        21,
        68,
        "San Fernando",
        1971,
        6.6,
        "LA - Hollywood Stor",
        "SFERN/PEL090",
        "SFERN/PEL180",
        0.21,
    ),
    FemaP695Record(
        22, 125, "Friuli, Italy", 1976, 6.5, "Tolmezzo", "FRIULI/A-TMZ000", "FRIULI/A-TMZ270", 0.35
    ),
)


def far_field_rsns() -> list[int]:
    """The 22 PEER NGA-West2 RSNs, in table order."""
    return [r.rsn for r in FAR_FIELD]
