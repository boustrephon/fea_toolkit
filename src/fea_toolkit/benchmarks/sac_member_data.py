"""Transcribed member-backbone data for the Ohtori et al. (2004) SAC models.

This module transcribes the ``mat_tbl`` member-backbone tables from the
benchmark's structural-data files, reproduced verbatim (N-m units):

* ``local/257/_extract/struct3.m``   ``mat_tbl`` lines 139–148
* ``local/257/_extract/struct9.m``   ``mat_tbl`` lines 175–196
* ``local/257/_extract/struct20.m``  ``mat_tbl`` lines 224–254

The sources live under the gitignored ``local/`` tree (so they are **not**
committed); this module is the committed, checkable transcription.

Each ``mat_tbl`` row is ``(EI1, EI2, EI3, EA, GA, d1, d2)`` with ``type = 1``
(concentrated plasticity).  The member moment-curvature law is trilinear:

* ``My = EI1 * d1`` (yield moment), for curvature ``<= d1``;
* slope ``EI2`` from ``d1`` to ``d2``;
* slope ``EI3`` beyond ``d2``.

``d1`` / ``d2`` are break curvatures (1/m).  ``GA = 8.8960e15`` is a rigid-shear
placeholder (shear deformation neglected); ``EA = 1.0e10`` marks a rigid-axial
member (floor diaphragm / leaning-column link bay).  The splice rows
(material 12+ in the 9- and 20-story tables) are the source's column-splice
weighted averages ``(6/13)*a + (7/13)*b``.
"""

from typing import NamedTuple


class MemberBackbone(NamedTuple):
    """One ``mat_tbl`` row: trilinear flexural backbone + axial/shear rigidity."""

    ei1: float
    ei2: float
    ei3: float
    ea: float
    ga: float
    d1: float
    d2: float


def _splice(a: MemberBackbone, b: MemberBackbone) -> MemberBackbone:
    """Column-splice row: ``(6/13)*a + (7/13)*b`` (the ``mat_tbl`` splice rows)."""
    wa, wb = 6.0 / 13.0, 7.0 / 13.0
    return MemberBackbone(*(wa * x + wb * y for x, y in zip(a, b)))


# ═══════════════════════════════════════════════════════════════
# 3-story — struct3.m, mat_tbl lines 139–148 (no splice rows)
# ═══════════════════════════════════════════════════════════════
MAT_TBL_3: dict[int, MemberBackbone] = {
    1: MemberBackbone(2.8295e8, 2.5519e8, 8.4885e6, 9.7518e9, 8.8960e15, 9.2366e-3, 9.7244e-3),
    2: MemberBackbone(3.6034e8, 3.2899e8, 1.0810e7, 1.1790e10, 8.8960e15, 8.9803e-3, 9.4488e-3),
    3: MemberBackbone(4.9100e8, 4.7068e8, 1.4730e7, 1.0e10, 8.8960e15, 4.4633e-3, 4.6850e-3),
    4: MemberBackbone(4.1028e8, 3.7595e8, 1.2308e7, 1.0e10, 8.8960e15, 4.8652e-3, 5.1181e-3),
    5: MemberBackbone(1.5229e8, 1.3940e8, 4.5688e6, 1.0e10, 8.8960e15, 6.1373e-3, 6.4567e-3),
    6: MemberBackbone(
        1.0070e7, 9.0411e6, 3.0209e5, 2.5798e9, 8.8960e15, 1.9665e-2, 2.0709e-2
    ),  # leaning column
    7: MemberBackbone(
        7.0155e7, 6.3451e7, 2.1046e6, 1.0e10, 8.8960e15, 7.1809e-3, 7.5591e-3
    ),  # rigid-axial link bay
}


# ═══════════════════════════════════════════════════════════════
# 9-story — struct9.m, mat_tbl lines 175–196
# ═══════════════════════════════════════════════════════════════
_MAT_TBL_9_BASE: dict[int, MemberBackbone] = {
    1: MemberBackbone(4.5272e8, 4.0971e8, 1.3582e7, 1.4060e10, 8.8960e15, 1.0051e-2, 1.0580e-2),
    2: MemberBackbone(6.8324e8, 6.1831e8, 2.0497e7, 1.8962e10, 8.8960e15, 9.5008e-3, 1.0001e-2),
    3: MemberBackbone(5.9836e8, 5.4151e8, 1.7951e7, 1.7285e10, 8.8960e15, 9.6708e-3, 1.0180e-2),
    4: MemberBackbone(3.1957e8, 2.8920e8, 9.5870e6, 1.0745e10, 8.8960e15, 1.0485e-2, 1.1037e-2),
    5: MemberBackbone(2.8295e8, 2.5607e8, 8.4885e6, 9.7518e9, 8.8960e15, 1.0641e-2, 1.1201e-2),
    6: MemberBackbone(2.5049e8, 2.2211e8, 7.5148e6, 8.8360e9, 8.8960e15, 1.0761e-2, 1.1339e-2),
    7: MemberBackbone(8.1140e8, 6.8151e8, 2.4342e7, 6.0629e9, 8.8960e15, 4.0610e-3, 4.2913e-3),
    8: MemberBackbone(6.4912e8, 5.5829e8, 1.9474e7, 5.1212e9, 8.8960e15, 4.1408e-3, 4.3701e-3),
    9: MemberBackbone(3.3205e8, 2.8596e8, 9.9615e6, 3.7539e9, 8.8960e15, 4.9618e-3, 5.2362e-3),
    10: MemberBackbone(2.3718e8, 2.2226e8, 7.1154e6, 3.1992e9, 8.8960e15, 5.4325e-3, 5.7087e-3),
    11: MemberBackbone(1.5229e8, 1.3940e8, 4.5688e6, 2.5929e9, 8.8960e15, 6.1373e-3, 6.4567e-3),
}

MAT_TBL_9: dict[int, MemberBackbone] = {
    **_MAT_TBL_9_BASE,
    # Splice rows 12–16 (column splices): (6/13)*lower + (7/13)*upper.
    12: _splice(_MAT_TBL_9_BASE[2], _MAT_TBL_9_BASE[3]),  # (6/13)*m2 + (7/13)*m3
    13: _splice(_MAT_TBL_9_BASE[1], _MAT_TBL_9_BASE[4]),  # (6/13)*m1 + (7/13)*m4
    14: _splice(_MAT_TBL_9_BASE[3], _MAT_TBL_9_BASE[1]),  # (6/13)*m3 + (7/13)*m1
    15: _splice(_MAT_TBL_9_BASE[4], _MAT_TBL_9_BASE[5]),  # (6/13)*m4 + (7/13)*m5
    16: _splice(_MAT_TBL_9_BASE[5], _MAT_TBL_9_BASE[6]),  # (6/13)*m5 + (7/13)*m6
}


# ═══════════════════════════════════════════════════════════════
# 20-story — struct20.m, mat_tbl lines 224–254
# ═══════════════════════════════════════════════════════════════
_MAT_TBL_20_BASE: dict[int, MemberBackbone] = {
    1: MemberBackbone(2.4959e8, 2.2584e8, 7.4866e6, 1.3416e10, 8.8960e15, 1.2659e-2, 1.3325e-2),
    2: MemberBackbone(9.9037e8, 8.9628e8, 2.9710e7, 1.2693e10, 8.8960e15, 6.3675e-3, 6.7026e-3),
    3: MemberBackbone(1.8176e8, 1.6450e8, 5.4532e6, 8.8686e9, 8.8960e15, 1.2090e-2, 1.2726e-2),
    4: MemberBackbone(1.5305e8, 1.3847e8, 4.5904e6, 7.2239e9, 8.8960e15, 1.1899e-2, 1.2525e-2),
    5: MemberBackbone(6.3667e8, 5.7617e8, 1.9099e7, 8.6687e9, 8.8960e15, 6.5645e-3, 6.9100e-3),
    6: MemberBackbone(5.2099e8, 4.7145e8, 1.5629e7, 7.2626e9, 8.8960e15, 6.6336e-3, 6.9828e-3),
    7: MemberBackbone(1.2076e8, 1.0927e8, 3.6222e6, 5.5147e9, 8.8960e15, 1.1708e-2, 1.2324e-2),
    8: MemberBackbone(3.3456e8, 3.0277e8, 1.0036e7, 4.9664e9, 8.8960e15, 6.8374e-3, 7.1972e-3),
    9: MemberBackbone(2.9462e8, 2.6660e8, 8.8380e6, 4.4375e9, 8.8960e15, 6.8621e-3, 7.2233e-3),
    10: MemberBackbone(8.4639e7, 7.6629e7, 2.5401e6, 3.7410e9, 8.8960e15, 1.1518e-2, 1.2124e-2),
    11: MemberBackbone(1.9724e8, 1.7849e8, 5.9170e6, 3.1863e9, 8.8960e15, 7.0212e-3, 7.3908e-3),
    12: MemberBackbone(3.3207e8, 2.8596e8, 9.9615e6, 3.7539e9, 8.8960e15, 4.9618e-3, 5.2362e-3),
    13: MemberBackbone(3.7201e8, 3.5399e8, 1.1160e7, 4.0892e9, 8.8960e15, 4.9116e-3, 5.1575e-3),
    14: MemberBackbone(2.3719e8, 2.2226e8, 7.1154e6, 3.1992e9, 8.8960e15, 5.4325e-3, 5.7087e-3),
    15: MemberBackbone(1.2900e8, 1.2362e8, 3.8698e6, 2.3478e9, 8.8960e15, 6.2635e-3, 6.5748e-3),
    16: MemberBackbone(8.1893e7, 7.1509e7, 2.4567e6, 1.8963e9, 8.8960e15, 7.0934e-3, 7.4803e-3),
}

MAT_TBL_20: dict[int, MemberBackbone] = {
    **_MAT_TBL_20_BASE,
    # Splice rows 17–25 (column splices): (6/13)*lower + (7/13)*upper.
    17: _splice(_MAT_TBL_20_BASE[1], _MAT_TBL_20_BASE[3]),  # (6/13)*m1 + (7/13)*m3
    18: _splice(_MAT_TBL_20_BASE[3], _MAT_TBL_20_BASE[4]),  # (6/13)*m3 + (7/13)*m4
    19: _splice(_MAT_TBL_20_BASE[2], _MAT_TBL_20_BASE[5]),  # (6/13)*m2 + (7/13)*m5
    20: _splice(_MAT_TBL_20_BASE[5], _MAT_TBL_20_BASE[6]),  # (6/13)*m5 + (7/13)*m6
    21: _splice(_MAT_TBL_20_BASE[4], _MAT_TBL_20_BASE[7]),  # (6/13)*m4 + (7/13)*m7
    22: _splice(_MAT_TBL_20_BASE[6], _MAT_TBL_20_BASE[8]),  # (6/13)*m6 + (7/13)*m8
    23: _splice(_MAT_TBL_20_BASE[8], _MAT_TBL_20_BASE[9]),  # (6/13)*m8 + (7/13)*m9
    24: _splice(_MAT_TBL_20_BASE[7], _MAT_TBL_20_BASE[10]),  # (6/13)*m7 + (7/13)*m10
    25: _splice(_MAT_TBL_20_BASE[9], _MAT_TBL_20_BASE[11]),  # (6/13)*m9 + (7/13)*m11
}


# ═══════════════════════════════════════════════════════════════
# Member → material assignment (element_tbl(:,3))
# ═══════════════════════════════════════════════════════════════
# element_tbl(:,3) maps each member to a ``Mat_no``.  Each inner list is one
# floor's members in bay order (columns first, then beams), matching the
# source's per-floor comment rows.  The last column is the leaning (gravity)
# column (struct3); the last beam of each floor is the rigid-axial link bay
# (struct3 material 7).  The source also tags those link-bay beams with an
# element type flag (3 or 5) in ``element_tbl(:,4)`` — not transcribed here.

COLUMN_MATERIALS_3: list[list[int]] = [
    [1, 2, 2, 1, 6],  # 1st floor
    [1, 2, 2, 1, 6],  # 2nd floor
    [1, 2, 2, 1, 6],  # 3rd floor
]

BEAM_MATERIALS_3: list[list[int]] = [
    [3, 3, 3, 7],  # 2nd floor
    [4, 4, 4, 7],  # 3rd floor
    [5, 5, 5, 7],  # roof
]

COLUMN_MATERIALS_9: list[list[int]] = [
    [1, 2, 2, 2, 2, 1],  # basement
    [1, 2, 2, 2, 2, 1],  # 1st floor
    [1, 12, 12, 12, 12, 1],  # 2nd floor
    [1, 3, 3, 3, 3, 1],  # 3rd floor
    [13, 14, 14, 14, 14, 13],  # 4th floor
    [4, 1, 1, 1, 1, 4],  # 5th floor
    [15, 13, 13, 13, 13, 15],  # 6th floor
    [5, 4, 4, 4, 4, 5],  # 7th floor
    [16, 15, 15, 15, 15, 16],  # 8th floor
    [6, 5, 5, 5, 5, 6],  # 9th floor
]

BEAM_MATERIALS_9: list[list[int]] = [
    [7, 7, 7, 7, 7],  # 1st floor
    [7, 7, 7, 7, 7],  # 2nd floor
    [7, 7, 7, 7, 7],  # 3rd floor
    [8, 8, 8, 8, 8],  # 4th floor
    [8, 8, 8, 8, 8],  # 5th floor
    [8, 8, 8, 8, 8],  # 6th floor
    [8, 8, 8, 8, 8],  # 7th floor
    [9, 9, 9, 9, 9],  # 8th floor
    [10, 10, 10, 10, 10],  # 9th floor
    [11, 11, 11, 11, 11],  # roof
]

COLUMN_MATERIALS_20: list[list[int]] = [
    [1, 2, 2, 2, 2, 1],  # 1st basement
    [1, 2, 2, 2, 2, 1],  # 2nd basement
    [1, 2, 2, 2, 2, 1],  # 1st floor
    [17, 2, 2, 2, 2, 17],  # 2nd floor
    [3, 2, 2, 2, 2, 3],  # 3rd floor
    [3, 2, 2, 2, 2, 3],  # 4th floor
    [18, 19, 19, 19, 19, 18],  # 5th floor
    [4, 5, 5, 5, 5, 4],  # 6th floor
    [4, 5, 5, 5, 5, 4],  # 7th floor
    [4, 5, 5, 5, 5, 4],  # 8th floor
    [4, 5, 5, 5, 5, 4],  # 9th floor
    [4, 5, 5, 5, 5, 4],  # 10th floor
    [4, 20, 20, 20, 20, 4],  # 11th floor
    [4, 6, 6, 6, 6, 4],  # 12th floor
    [4, 6, 6, 6, 6, 4],  # 13th floor
    [21, 22, 22, 22, 22, 21],  # 14th floor
    [7, 8, 8, 8, 8, 7],  # 15th floor
    [7, 8, 8, 8, 8, 7],  # 16th floor
    [7, 23, 23, 23, 23, 7],  # 17th floor
    [7, 9, 9, 9, 9, 7],  # 18th floor
    [24, 25, 25, 25, 25, 24],  # 19th floor
    [10, 11, 11, 11, 11, 10],  # 20th floor
]

BEAM_MATERIALS_20: list[list[int]] = [
    [12, 12, 12, 12, 12],  # 1st basement
    [12, 12, 12, 12, 12],  # 1st floor
    [12, 12, 12, 12, 12],  # 2nd floor
    [12, 12, 12, 12, 12],  # 3rd floor
    [12, 12, 12, 12, 12],  # 4th floor
    [12, 12, 12, 12, 12],  # 5th floor
    [13, 13, 13, 13, 13],  # 6th floor
    [13, 13, 13, 13, 13],  # 7th floor
    [13, 13, 13, 13, 13],  # 8th floor
    [13, 13, 13, 13, 13],  # 9th floor
    [13, 13, 13, 13, 13],  # 10th floor
    [13, 13, 13, 13, 13],  # 11th floor
    [12, 12, 12, 12, 12],  # 12th floor
    [12, 12, 12, 12, 12],  # 13th floor
    [12, 12, 12, 12, 12],  # 14th floor
    [8, 8, 8, 8, 8],  # 15th floor
    [8, 8, 8, 8, 8],  # 16th floor
    [8, 8, 8, 8, 8],  # 17th floor
    [14, 14, 14, 14, 14],  # 18th floor
    [14, 14, 14, 14, 14],  # 19th floor
    [15, 15, 15, 15, 15],  # 20th floor
    [16, 16, 16, 16, 16],  # roof
]


# ── Building index ──
MAT_TBLS: dict[int, dict[int, MemberBackbone]] = {
    3: MAT_TBL_3,
    9: MAT_TBL_9,
    20: MAT_TBL_20,
}

#: ``element_tbl(:,3)`` member → ``Mat_no`` per building, keyed like ``MAT_TBLS``:
#: ``COLUMN_MATERIALS[b][storey][line]`` and ``BEAM_MATERIALS[b][level][bay]``.
COLUMN_MATERIALS: dict[int, list[list[int]]] = {
    3: COLUMN_MATERIALS_3,
    9: COLUMN_MATERIALS_9,
    20: COLUMN_MATERIALS_20,
}

BEAM_MATERIALS: dict[int, list[list[int]]] = {
    3: BEAM_MATERIALS_3,
    9: BEAM_MATERIALS_9,
    20: BEAM_MATERIALS_20,
}


# ── Mat_no → display section name (for visualisation) ──
# Each backbone (Mat_no) maps to the physical W/box section it represents, so
# the visualiser can draw the cross-section shape.  Splice rows (weighted
# blends of two sections) map to their *below-splice* section.  The 3-story
# leaning column (mat 6) is a conceptual aggregate of the gravity framing and
# maps to a representative gravity section.  Display-only: the analysis
# properties stay on the backbone (``MAT_TBL_*``).
MAT_TO_SECTION_3: dict[int, str] = {
    1: "W14X257",
    2: "W14X311",
    3: "W33X118",
    4: "W30X116",
    5: "W24X68",
    6: "W14X82",  # leaning column (representative gravity section)
    7: "W21X44",  # rigid-axial link bay
}

MAT_TO_SECTION_9: dict[int, str] = {
    1: "W14X370",
    2: "W14X500",
    3: "W14X455",
    4: "W14X283",
    5: "W14X257",
    6: "W14X233",
    7: "W36X160",
    8: "W36X135",
    9: "W30X99",
    10: "W27X84",
    11: "W24X68",
    # splice rows 12-16 → below-splice section
    12: "W14X500",
    13: "W14X370",
    14: "W14X455",
    15: "W14X283",
    16: "W14X257",
}

MAT_TO_SECTION_20: dict[int, str] = {
    1: "15X15X2.00",
    2: "W24X335",
    3: "15X15X1.25",
    4: "15X15X1.00",
    5: "W24X229",
    6: "W24X192",
    7: "15X15X0.75",
    8: "W24X131",
    9: "W24X117",
    10: "15X15X0.50",
    11: "W24X84",
    12: "W30X99",
    13: "W30X108",
    14: "W27X84",
    15: "W24X62",
    16: "W21X50",
    # splice rows 17-25 → below-splice section
    17: "15X15X2.00",
    18: "15X15X1.25",
    19: "W24X335",
    20: "W24X229",
    21: "15X15X1.00",
    22: "W24X192",
    23: "W24X131",
    24: "15X15X0.75",
    25: "W24X117",
}

#: Mat_no → section name per building, keyed like ``MAT_TBLS``.
MAT_TO_SECTION: dict[int, dict[int, str]] = {
    3: MAT_TO_SECTION_3,
    9: MAT_TO_SECTION_9,
    20: MAT_TO_SECTION_20,
}

__all__ = [
    "BEAM_MATERIALS",
    "BEAM_MATERIALS_3",
    "BEAM_MATERIALS_9",
    "BEAM_MATERIALS_20",
    "COLUMN_MATERIALS",
    "COLUMN_MATERIALS_3",
    "COLUMN_MATERIALS_9",
    "COLUMN_MATERIALS_20",
    "MAT_TBLS",
    "MAT_TBL_3",
    "MAT_TBL_9",
    "MAT_TBL_20",
    "MAT_TO_SECTION",
    "MAT_TO_SECTION_3",
    "MAT_TO_SECTION_9",
    "MAT_TO_SECTION_20",
    "MemberBackbone",
]
