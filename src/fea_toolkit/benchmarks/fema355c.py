"""FEMA 355C / SAC Phase 2 model buildings — unified generator (LA, Seattle, Boston).

Builds the full FEMA 355C Appendix B suite as ``SAPModelData`` models: three
regions (``LA``, ``SE``, ``BO``) x three heights (3-, 9-, 20-story) x the
design variants (``pre``, ``post``, Seattle ``rbs``, LA9 redesigns
``r1``/``r2``/``r3``).

Member schedules, doubler plates, cover plates and RBS geometry are
transcribed from FEMA 355C Tables B-1 ... B-9 (see
``docs/fema355c_schedules.md``).  Section properties are AISC13 catalogue
values converted to SI, sourced from ``data/section_dict.pkl`` and hardcoded
here so the module is self-contained; the box corner columns (LA 20-story,
15x15x t) are computed from the square-box formulas.  ``E = 200 GPa``, steel.

Units are SI (N, m, kg); materials are authored in SI (Pa) and used as-is
(see the material-property convention in ``.clinerules`` section 4.6).

Approximations (documented, not fabrications):

* Column splices (6 ft above the floor line) are approximated level-by-level
  (the below-splice section is used for the whole story).
* The penthouse is not modelled structurally (mass only).
* Dead (96/83 psf) and reduced live (20 psf) loads are nodal ``joint_loads``
  by tributary area; steel self-weight (13 psf) rides on material density.
* 3D builders emit one loads-only floor panel per bay per level.

Identifiers are location-based rather than sequential: nodes are
``{grid}-{level}`` (e.g. ``A1-02F``, ``C3-RF``); columns are
``C-{grid}-{level}``, beams ``B-{grid}-{grid}-{level}`` and floor panels
``P-{grid}-{level}``, keyed off their grid lines and lower level.  Grid lines
letter ``A``… along N-S and number ``1``… along E-W; levels run ``02B`` /
``01B`` (deepest basement first), then ``01F``… for the ground and upper
floors, and ``RF`` for the roof.  Integer OpenSees tags (``*_tag``) are
unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..io.model_codec import model_to_json
from ..model.sap_data import (
    AreaElement,
    BoxSection,
    FrameElement,
    FrameRelease,
    ISection,
    JointLoad,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
)
from ..model.steel_sections import (
    BOX_SECTIONS as _BOX_SECTIONS,
)
from ..model.steel_sections import (
    W_SECTIONS as _SECTIONS,
)

# ── Unit constants ───────────────────────────────────────────────────────
FT = 0.3048  # m per foot
BAY_30 = 30.0 * FT  # 9.144 m  (3- and 9-story)
BAY_20 = 20.0 * FT  # 6.096 m  (20-story)
BAY_15 = 15.0 * FT  # 4.572 m  (R2-LA9 redesign)
H12 = 12.0 * FT  # 3.6576 m  (basement)
H13 = 13.0 * FT  # 3.9624 m  (typical story)
H18 = 18.0 * FT  # 5.4864 m  (ground floor)

E_STEEL = 2.0e11  # Pa
G_STEEL = 7.7e10  # Pa

# ── Gravity load pressures (FEMA 355C Appendix B.1) ──────────────────────
PSF_TO_PA = 47.88025898  # N/m² per lb/ft²
DEAD_FLOOR_PA = 96.0 * PSF_TO_PA  # 4596.5 N/m²  (floor dead, for weight)
DEAD_ROOF_PA = 83.0 * PSF_TO_PA  # 3974.1 N/m²  (roof dead, excl. penthouse)
LIVE_PA = 20.0 * PSF_TO_PA  # 957.6 N/m²  (reduced live, floor + roof)

# ── Seismic masses (FEMA 355C Appendix B; whole building, kg) ────────────
# 1 kip-sec²/ft = 14593.9 kg.  Identical for all three regions.
MASS_3STORY: list[float] = [956339.0, 956339.0, 1034708.0]  # 65.53, 65.53, 70.90
MASS_9STORY: list[float] = [
    965000.0,  # ground (66.0, from Ohtori et al. 2004)
    1007563.0,  # floor 2 (69.04)
    990342.0,
    990342.0,
    990342.0,
    990342.0,
    990342.0,
    990342.0,
    990342.0,  # floors 3-9 (67.86)
    1066814.0,  # roof (73.10)
]
MASS_20STORY: list[float] = [563762.0] + [551066.0] * 18 + [584632.0]  # 38.63, 37.76x18, 40.06

# ── Steel grades (Pa) ────────────────────────────────────────────────────
KSI_TO_PA = 6.894757e6
F36 = 36.0 * KSI_TO_PA  # 248.2 MPa  (LA beams/girders)
F50 = 50.0 * KSI_TO_PA  # 344.7 MPa  (LA columns; all Seattle/Boston members)

#: Yield strength per region: ``(beam_fy, column_fy)`` in Pa.
_STEEL_FY = {"LA": (F36, F50), "SE": (F50, F50), "BO": (F50, F50)}


# ── Geometry (Figure B-1; identical across regions) ──────────────────────
# ``heights`` is the per-story height list (m) — one entry per level, bottom
# to top, *including* basement levels; ``base`` selects the restraint
# prescription (and thus the basement count, see ``_basement_count``).
# ``penthouse`` is a (grids, lines) footprint string.
#
# Level-index convention used throughout the builders: ``n_levels =
# len(heights)`` is the number of *levels* (3-story → 3; 9-story → 10 incl.
# its one basement; 20-story → 22 incl. its two basements), NOT the story
# count.  Node level ``j`` runs 0 (base) .. ``n_levels`` (roof).
_GEOM = {
    3: {
        "ns_bays": 4,
        "ew_bays": 6,
        "bay": BAY_30,
        "heights": [H13, H13, H13],
        "base": "fixed",
        "penthouse": "C3-D5",
    },
    9: {
        "ns_bays": 5,
        "ew_bays": 5,
        "bay": BAY_30,
        "heights": [H12, H18] + [H13] * 8,
        "base": "pinned_ground",
        "penthouse": "C3-E4",
    },
    20: {
        "ns_bays": 5,
        "ew_bays": 6,
        "bay": BAY_20,
        "heights": [H12, H12, H18] + [H13] * 19,
        "base": "pinned_ground_2basement",
        "penthouse": "C3-D5",
    },
}

# ── Moment-frame layout per (region, stories) ────────────────────────────
# ``ns_bays_moment`` / ``ew_bays_moment`` = full moment bays per perimeter
# face; ``ew_moment_start`` = the 0-based E-W bay index where the E-W moment
# frame begins (0 = grid line 1; the 3-story frames sit at grids 2-5, one bay
# in from grid line 1).  ``pinned`` closes the short bay that completes each
# face (``None`` = all moment; ``"corner"`` = last beam pinned at the corner
# column, LA/Boston 9-story 4.5-bay arrangement; ``"both"`` = pinned at both
# ends, Seattle 9-story 4-bay arrangement).  ``box_corners`` = LA 20-story box
# corner columns.  ``col_rotation`` classifies the perimeter-column strong-axis
# orientation so ``_build_3d`` dispatches on it directly instead of inferring
# the layout: ``"centered"`` = 3-story centred E-W frame + leaning columns,
# ``"pinned_corner"`` = 9-story short bay pinned at the corner, ``"perimeter"``
# = 20-story all-perimeter moment-resisting, ``None`` = no special E-W
# orientation (the R2/R3 redesigns).
_MR = {
    ("LA", 3): {
        "ns_bays_moment": 3,
        "ew_bays_moment": 3,
        "ew_moment_start": 1,
        "pinned": None,
        "box_corners": False,
        "col_rotation": "centered",
    },
    ("LA", 9): {
        "ns_bays_moment": 4,
        "ew_bays_moment": 4,
        "pinned": "corner",
        "box_corners": False,
        "col_rotation": "pinned_corner",
    },
    ("LA", 20): {
        "ns_bays_moment": 5,
        "ew_bays_moment": 6,
        "pinned": None,
        "box_corners": True,
        "col_rotation": "perimeter",
    },
    ("SE", 3): {
        "ns_bays_moment": 3,
        "ew_bays_moment": 3,
        "ew_moment_start": 1,
        "pinned": None,
        "box_corners": False,
        "col_rotation": "centered",
    },
    ("SE", 9): {
        "ns_bays_moment": 4,
        "ew_bays_moment": 4,
        "pinned": "both",
        "box_corners": False,
        "col_rotation": "pinned_corner",
    },
    ("SE", 20): {
        "ns_bays_moment": 5,
        "ew_bays_moment": 6,
        "pinned": None,
        "box_corners": False,
        "col_rotation": "perimeter",
    },
    ("BO", 3): {
        "ns_bays_moment": 3,
        "ew_bays_moment": 3,
        "ew_moment_start": 1,
        "pinned": None,
        "box_corners": False,
        "col_rotation": "centered",
    },
    ("BO", 9): {
        "ns_bays_moment": 4,
        "ew_bays_moment": 4,
        "pinned": "corner",
        "box_corners": False,
        "col_rotation": "pinned_corner",
    },
    ("BO", 20): {
        "ns_bays_moment": 5,
        "ew_bays_moment": 6,
        "pinned": None,
        "box_corners": False,
        "col_rotation": "perimeter",
    },
    # R3-LA9 redesign: 3 moment bays + 2 gravity bays per face (6 connections).
    ("LA", 9, "r3"): {
        "ns_bays_moment": 3,
        "ew_bays_moment": 3,
        "pinned": None,
        "box_corners": False,
        "col_rotation": None,
    },
}

# ── Member schedules per (region, stories, design) ───────────────────────
# Each schedule holds per-level lists (index 0 = lowest level) of section
# names, doubler-plate thicknesses, and (for post-Northridge) cover plates.
_SCHEDULES = {
    # ── Los Angeles ─────────────────────────────────────────────────────
    ("LA", 3, "pre"): {
        "col_ext": ["W14X257"] * 3,
        "col_int": ["W14X311"] * 3,
        "girder": ["W33X118", "W30X116", "W24X68"],
        "gravity_col": ["W14X82"] * 3,  # below penthouse
        "gravity_col_other": ["W14X68"] * 3,
        "beam": ["W18X35", "W18X35", "W16X26"],
        "doubler": (0.0, 0.0),
    },
    ("LA", 3, "post"): {
        "col_ext": ["W14X257"] * 3,
        "col_int": ["W14X311"] * 3,
        "girder": ["W30X116", "W30X116", "W24X62"],
        "gravity_col": ["W14X82"] * 3,
        "gravity_col_other": ["W14X68"] * 3,
        "beam": ["W16X26", "W16X26", "W14X22"],
        "doubler": (0.0, 0.0),
        "cover_plates": {
            "W30X116": {"top": (9.0, 0.75), "bottom": (12.0, 0.75)},
            "W24X62": {"top": (5.0, 0.75), "bottom": (9.0, 0.5)},
        },
    },
    ("LA", 9, "pre"): {
        "col_ext": [
            "W14X370",
            "W14X370",
            "W14X370",
            "W14X370",
            "W14X370",
            "W14X283",
            "W14X283",
            "W14X257",
            "W14X257",
            "W14X233",
        ],
        "col_int": [
            "W14X500",
            "W14X500",
            "W14X500",
            "W14X455",
            "W14X455",
            "W14X370",
            "W14X370",
            "W14X283",
            "W14X283",
            "W14X257",
        ],
        "girder": [
            "W36X160",
            "W36X160",
            "W36X160",
            "W36X135",
            "W36X135",
            "W36X135",
            "W36X135",
            "W30X99",
            "W27X84",
            "W24X68",
        ],
        "gravity_col": [
            "W14X211",
            "W14X211",
            "W14X211",
            "W14X159",
            "W14X159",
            "W14X120",
            "W14X120",
            "W14X90",
            "W14X90",
            "W14X61",
        ],
        "gravity_col_other": [
            "W14X193",
            "W14X193",
            "W14X193",
            "W14X145",
            "W14X145",
            "W14X109",
            "W14X109",
            "W14X82",
            "W14X82",
            "W14X48",
        ],
        "beam": [
            "W21X44",
            "W18X35",
            "W18X35",
            "W18X35",
            "W18X35",
            "W18X35",
            "W18X35",
            "W18X35",
            "W18X35",
            "W16X26",
        ],
        "doubler": (0.0, 0.0),
    },
    ("LA", 9, "post"): {
        "col_ext": [
            "W14X370",
            "W14X370",
            "W14X370",
            "W14X370",
            "W14X370",
            "W14X283",
            "W14X283",
            "W14X257",
            "W14X257",
            "W14X233",
        ],
        "col_int": [
            "W14X500",
            "W14X500",
            "W14X500",
            "W14X455",
            "W14X455",
            "W14X370",
            "W14X370",
            "W14X283",
            "W14X283",
            "W14X257",
        ],
        "girder": [
            "W36X150",
            "W36X150",
            "W36X150",
            "W33X141",
            "W33X141",
            "W33X141",
            "W33X130",
            "W27X102",
            "W27X94",
            "W24X62",
        ],
        "gravity_col": [
            "W14X211",
            "W14X211",
            "W14X211",
            "W14X159",
            "W14X159",
            "W14X120",
            "W14X120",
            "W14X90",
            "W14X90",
            "W14X61",
        ],
        "gravity_col_other": [
            "W14X193",
            "W14X193",
            "W14X193",
            "W14X145",
            "W14X145",
            "W14X109",
            "W14X109",
            "W14X82",
            "W14X82",
            "W14X48",
        ],
        "beam": [
            "W18X35",
            "W16X26",
            "W16X26",
            "W16X26",
            "W16X26",
            "W16X26",
            "W16X26",
            "W16X26",
            "W16X26",
            "W14X22",
        ],
        "doubler": (0.0, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 0.0]),
        "cover_plates": {
            "W36X150": {"top": (10.0, 1.0), "bottom": (14.0, 0.75)},
            "W33X141": {"top": (9.0, 1.0), "bottom": (13.0, 0.75)},
            "W33X130": {"top": (9.0, 1.0), "bottom": (13.0, 0.75)},
            "W27X102": {"top": (8.5, 0.75), "bottom": (12.0, 0.625)},
            "W27X94": {"top": (8.0, 0.75), "bottom": (11.0, 0.625)},
            "W24X62": {"top": (5.0, 0.75), "bottom": (9.0, 0.5)},
        },
    },
    ("LA", 20, "pre"): {
        "col_ext": ["15X15X2.00"] * 4
        + ["15X15X1.25"] * 3
        + ["15X15X1.00"] * 9
        + ["15X15X0.75"] * 5
        + ["15X15X0.50"],
        "col_int": ["W24X335"] * 7
        + ["W24X229"] * 6
        + ["W24X192"] * 3
        + ["W24X131"] * 3
        + ["W24X117"] * 2
        + ["W24X84"],
        "girder": ["W14X22"]
        + ["W30X99"] * 5
        + ["W30X108"] * 6
        + ["W30X99"] * 6
        + ["W27X84"] * 2
        + ["W24X62"]
        + ["W21X50"],
        # Two gravity-column groups below the penthouse (TR132 "40 feet span"
        # = 4 corner columns, "20 feet span" = 2 middle columns).  W14X109 is
        # the source's "W14X108" (typo, fixed).
        "gravity_col_40": ["W14X550"] * 3
        + ["W14X455"] * 3
        + ["W14X370"] * 3
        + ["W14X311"] * 3
        + ["W14X257"] * 3
        + ["W14X176"] * 3
        + ["W14X109"] * 3
        + ["W14X43"],
        "gravity_col_20": ["W21X50", "W24X68"] + ["W21X50"] * 19 + ["W21X44"],
        "beam": ["W14X22", "W16X26"] + ["W14X22"] * 19 + ["W12X16"],
        "doubler": (0.0, [0.0] * 15 + [0.625] * 5 + [0.0] * 2),
    },
    ("LA", 20, "post"): {
        "col_ext": ["15X15X2.00"] * 4
        + ["15X15X1.25"] * 3
        + ["15X15X1.00"] * 9
        + ["15X15X0.75"] * 5
        + ["15X15X0.50"],
        "col_int": ["W24X335"] * 7
        + ["W24X279"] * 6
        + ["W24X229"] * 3
        + ["W24X162"] * 3
        + ["W24X117"] * 2
        + ["W24X94"],
        "girder": ["W14X22"]
        + ["W30X99"] * 5
        + ["W30X108"] * 6
        + ["W30X99"] * 6
        + ["W27X84"] * 2
        + ["W24X62"]
        + ["W21X50"],
        "gravity_col_40": ["W14X550"] * 3
        + ["W14X455"] * 3
        + ["W14X370"] * 3
        + ["W14X311"] * 3
        + ["W14X257"] * 3
        + ["W14X176"] * 3
        + ["W14X109"] * 3
        + ["W14X43"],
        "gravity_col_20": ["W18X40", "W24X55"] + ["W18X40"] * 19 + ["W18X35"],
        "beam": ["W14X22"] * 20 + ["W14X22", "W12X14"],
        "doubler": (0.0, [0.0] * 15 + [0.625] * 5 + [0.5, 0.0]),
        # B-2 lists no cover plates for the LA 20-story girders W30X99 / W27X84 /
        # W21X50 / W14X22 — only W30X108 and W24X62 are covered (see
        # docs/fema355c_schedules.md section 6 for the reconciliation).
        "cover_plates": {
            "W30X108": {"top": (9.0, 0.75), "bottom": (12.0, 0.625)},
            "W24X62": {"top": (5.0, 0.75), "bottom": (9.0, 0.5)},
        },
    },
    # ── Boston ─────────────────────────────────────────────────────────
    ("BO", 3, "pre"): {
        "col_ext": ["W14X74"] * 3,
        "col_int": ["W14X99"] * 3,
        "girder": ["W18X35", "W21X57", "W21X62"],
        "gravity_col_40": ["W12X65"] * 3,  # 4 columns below penthouse
        "gravity_col_20": ["W12X72"] * 3,  # 2 columns below penthouse
        "gravity_col_other": ["W12X58"] * 3,
        "beam": ["W16X26", "W16X26", "W14X22"],
    },
    ("BO", 3, "post"): {
        "col_ext": ["W14X82"] * 3,
        "col_int": ["W14X145"] * 3,
        "girder": ["W21X62", "W21X62", "W14X48"],
        "gravity_col_40": ["W12X65"] * 3,
        "gravity_col_20": ["W12X72"] * 3,
        "gravity_col_other": ["W12X58"] * 3,
        "beam": ["W16X26", "W16X26", "W14X22"],
    },
    ("BO", 9, "pre"): {
        "col_ext": ["W14X211"] * 2
        + ["W14X159"] * 2
        + ["W14X132"] * 2
        + ["W14X99"] * 2
        + ["W14X61"] * 2,
        "col_int": ["W14X283"] * 2
        + ["W14X233"] * 2
        + ["W14X211"] * 2
        + ["W14X176"] * 2
        + ["W14X120"] * 2,
        "girder": [
            "W24X68",
            "W36X135",
            "W33X118",
            "W30X116",
            "W30X116",
            "W30X108",
            "W30X99",
            "W27X94",
            "W24X76",
            "W18X40",
        ],
        "gravity_col_40": ["W14X145"] * 2
        + ["W14X120"] * 2
        + ["W14X99"] * 2
        + ["W12X79"] * 2
        + ["W12X53"] * 2,
        "gravity_col_20": ["W14X159"] * 2
        + ["W14X132"] * 2
        + ["W12X106"] * 2
        + ["W12X79"] * 2
        + ["W12X58"] * 2,
        "gravity_col_other": ["W14X145"] * 2
        + ["W12X120"] * 2
        + ["W14X90"] * 2
        + ["W12X65"] * 2
        + ["W8X48"] * 2,
        "beam": ["W16X26"] * 9 + ["W14X22"],
        "beam_gravity": [
            "W24X68",
            "W27X94",
            "W27X84",
            "W27X84",
            "W24X76",
            "W24X76",
            "W24X68",
            "W24X62",
            "W24X55",
            "W18X40",
        ],
    },
    ("BO", 9, "post"): {
        "col_ext": ["W14X283"] * 2
        + ["W14X257"] * 2
        + ["W14X211"] * 2
        + ["W14X159"] * 2
        + ["W14X109"] * 2,
        "col_int": ["W14X500"] * 2
        + ["W14X455"] * 2
        + ["W14X398"] * 2
        + ["W14X311"] * 2
        + ["W14X193"] * 2,
        "girder": [
            "W12X53",
            "W33X141",
            "W33X141",
            "W21X101",
            "W21X101",
            "W21X101",
            "W21X101",
            "W18X97",
            "W16X67",
            "W12X53",
        ],
        "gravity_col_40": ["W14X145"] * 2
        + ["W14X120"] * 2
        + ["W14X99"] * 2
        + ["W12X79"] * 2
        + ["W12X53"] * 2,
        "gravity_col_20": ["W14X159"] * 2
        + ["W14X132"] * 2
        + ["W12X106"] * 2
        + ["W12X79"] * 2
        + ["W12X58"] * 2,
        "gravity_col_other": ["W14X145"] * 2
        + ["W12X120"] * 2
        + ["W14X90"] * 2
        + ["W12X65"] * 2
        + ["W8X48"] * 2,
        "beam": ["W16X26"] * 9 + ["W14X22"],
        "beam_gravity": [
            "W12X53",
            "W16X67",
            "W16X67",
            "W16X67",
            "W16X67",
            "W14X61",
            "W12X58",
            "W12X58",
            "W12X53",
            "W12X53",
        ],
    },
    ("BO", 20, "pre"): {
        "col_ext": ["W14X370"] * 4
        + ["W14X311"] * 2
        + ["W14X283"] * 2
        + ["W14X233"] * 2
        + ["W14X193"] * 2
        + ["W14X159"] * 2
        + ["W14X132"] * 2
        + ["W14X109"] * 2
        + ["W14X82"] * 2
        + ["W14X61"] * 2,
        "col_next": ["W36X194"] * 4
        + ["W36X182"] * 2
        + ["W36X170"] * 2
        + ["W36X160"] * 2
        + ["W36X150"] * 2
        + ["W36X135"] * 2
        + ["W30X116"] * 2
        + ["W30X99"] * 2
        + ["W27X84"] * 2
        + ["W24X68"] * 2,
        "col_int": ["W36X260"] * 4
        + ["W33X221"] * 2
        + ["W33X201"] * 4
        + ["W30X173"] * 4
        + ["W27X161"] * 2
        + ["W27X146"] * 2
        + ["W24X131"] * 2
        + ["W24X68"] * 2,
        "girder": [
            "W12X14",
            "W27X94",
            "W36X182",
            "W36X160",
            "W36X160",
            "W36X150",
            "W36X150",
            "W36X150",
            "W36X135",
            "W36X135",
            "W33X130",
            "W33X130",
            "W33X130",
            "W33X118",
            "W33X118",
            "W30X116",
            "W30X108",
            "W30X99",
            "W27X94",
            "W27X84",
            "W24X68",
            "W18X35",
        ],
        "gravity_col_40": ["W14X311"] * 4
        + ["W14X257"] * 2
        + ["W14X233"] * 2
        + ["W14X211"] * 2
        + ["W14X176"] * 2
        + ["W14X145"] * 2
        + ["W14X120"] * 2
        + ["W12X96"] * 2
        + ["W12X72"] * 2
        + ["W10X49"] * 2,
        "gravity_col_20": ["W14X211"] * 4
        + ["W14X176"] * 2
        + ["W14X159"] * 2
        + ["W14X145"] * 2
        + ["W14X120"] * 2
        + ["W14X109"] * 2
        + ["W14X90"] * 2
        + ["W12X72"] * 2
        + ["W10X60"] * 2
        + ["W8X48"] * 2,
        "beam_40": ["W18X40"] * 22,
        "beam_20": ["W12X16"] + ["W12X19"] * 20 + ["W14X22"],
    },
    ("BO", 20, "post"): {
        "col_ext": ["W14X455"] * 4
        + ["W14X370"] * 2
        + ["W14X342"] * 4
        + ["W14X311"] * 2
        + ["W14X283"] * 4
        + ["W14X193"] * 2
        + ["W14X159"] * 2
        + ["W14X109"] * 2,
        "col_next": ["W36X393"] * 4
        + ["W36X328"] * 2
        + ["W36X300"] * 4
        + ["W36X260"] * 6
        + ["W36X182"] * 2
        + ["W36X150"] * 2
        + ["W24X117"] * 2,
        "col_int": ["W36X485"] * 4
        + ["W36X393"] * 2
        + ["W36X359"] * 4
        + ["W36X300"] * 4
        + ["W36X280"] * 2
        + ["W36X210"] * 2
        + ["W36X150"] * 2
        + ["W24X131"] * 2,
        "girder": ["W12X14", "W16X67"]
        + ["W33X141"] * 5
        + ["W24X131"] * 6
        + ["W24X117"] * 2
        + ["W24X104"] * 3
        + ["W21X101", "W18X86", "W18X76", "W12X53"],
        "gravity_col_40": ["W14X311"] * 4
        + ["W14X257"] * 2
        + ["W14X233"] * 2
        + ["W14X211"] * 2
        + ["W14X176"] * 2
        + ["W14X145"] * 2
        + ["W14X120"] * 2
        + ["W12X96"] * 2
        + ["W12X72"] * 2
        + ["W10X49"] * 2,
        "gravity_col_20": ["W14X211"] * 4
        + ["W14X176"] * 2
        + ["W14X159"] * 2
        + ["W14X145"] * 2
        + ["W14X120"] * 2
        + ["W14X109"] * 2
        + ["W14X90"] * 2
        + ["W12X72"] * 2
        + ["W10X60"] * 2
        + ["W8X48"] * 2,
        "beam_40": ["W18X40"] * 22,
        "beam_20": ["W12X16"] + ["W12X19"] * 20 + ["W14X22"],
    },
    # ── Seattle ────────────────────────────────────────────────────────
    ("SE", 3, "pre"): {
        "col_ext": ["W14X159"] * 3,
        "col_int": ["W14X176"] * 3,
        "girder": ["W24X76", "W24X84", "W18X40"],
        "gravity_col": ["W10X77", "W10X60", "W10X60"],
        "beam": ["W16X26", "W16X26", "W14X22"],
    },
    ("SE", 3, "post"): {
        "col_ext": ["W14X342", "W14X159", "W14X159"],
        "col_int": ["W14X398", "W14X159", "W14X159"],
        "girder": ["W33X141", "W21X62", "W21X62"],
        "gravity_col": ["W10X77", "W10X60", "W10X60"],
        "beam": ["W16X26", "W16X26", "W14X22"],
    },
    ("SE", 9, "pre"): {
        "col_ext": ["W24X229"] * 4 + ["W24X207"] * 2 + ["W24X162"] * 2 + ["W24X131"] * 2,
        "col_int": ["W24X229"] * 4 + ["W24X207"] * 2 + ["W24X162"] * 2 + ["W24X131"] * 2,
        "girder": [
            "W30X108",
            "W30X108",
            "W30X116",
            "W30X108",
            "W27X94",
            "W27X94",
            "W24X76",
            "W24X76",
            "W24X62",
            "W24X62",
        ],
        "gravity_col": [
            "W14X159",
            "W14X159",
            "W14X132",
            "W14X132",
            "W14X109",
            "W14X109",
            "W14X90",
            "W14X90",
            "W14X61",
            "W14X61",
        ],
        "beam": ["W16X31"] + ["W16X26"] * 8 + ["W14X22"],
    },
    ("SE", 9, "post"): {
        "col_ext": ["W24X229"] * 4 + ["W24X207"] * 2 + ["W24X162"] * 2 + ["W24X131"] * 2,
        "col_int": ["W24X229"] * 4 + ["W24X207"] * 2 + ["W24X162"] * 2 + ["W24X131"] * 2,
        "girder": [
            "W27X114",
            "W27X114",
            "W27X114",
            "W27X94",
            "W27X94",
            "W27X94",
            "W24X76",
            "W24X76",
            "W21X62",
            "W21X62",
        ],
        "gravity_col": [
            "W14X159",
            "W14X159",
            "W14X132",
            "W14X132",
            "W14X109",
            "W14X109",
            "W14X90",
            "W14X90",
            "W14X61",
            "W14X61",
        ],
        "beam": ["W16X31"] + ["W16X26"] * 8 + ["W14X22"],
    },
    ("SE", 9, "rbs"): {
        "col_ext": ["W24X176"] * 6 + ["W24X131"] * 2 + ["W24X94"] * 2,
        "col_int": ["W24X229"] * 4 + ["W24X207"] * 2 + ["W24X162"] * 2 + ["W24X131"] * 2,
        "girder": [
            "W30X108",
            "W30X116",
            "W30X116",
            "W30X108",
            "W27X94",
            "W27X94",
            "W24X84",
            "W24X76",
            "W24X55",
            "W21X44",
        ],
        "gravity_col": [
            "W14X159",
            "W14X159",
            "W14X132",
            "W14X132",
            "W14X109",
            "W14X109",
            "W14X90",
            "W14X90",
            "W14X61",
            "W14X61",
        ],
        "beam": ["W16X31"] + ["W16X26"] * 8 + ["W14X22"],
    },
    ("SE", 20, "pre"): {
        "col_ext": ["W24X229"] * 6 + ["W24X192"] * 8 + ["W24X162"] * 4 + ["W24X131"] * 4,
        "col_int": ["W24X229"] * 6 + ["W24X192"] * 8 + ["W24X162"] * 4 + ["W24X131"] * 4,
        "girder": ["W12X14"]
        + ["W30X132"] * 7
        + ["W30X116"] * 2
        + ["W27X114"] * 2
        + ["W27X94"] * 4
        + ["W24X62"] * 2
        + ["W21X57"] * 4,
        "gravity_col": ["W14X426"] * 2
        + ["W14X398"] * 2
        + ["W14X342"] * 2
        + ["W14X311"] * 2
        + ["W14X257"] * 2
        + ["W14X233"] * 2
        + ["W14X193"] * 2
        + ["W14X159"] * 2
        + ["W14X120"] * 2
        + ["W14X90"] * 2
        + ["W14X61"] * 2,
        "beam_40": ["W21X44", "W21X50"] + ["W21X44"] * 19 + ["W18X35"],
        "beam_20": ["W12X14", "W12X16"] + ["W12X14"] * 20,
    },
    ("SE", 20, "post"): {
        "col_ext": ["W24X306"] * 6
        + ["W24X279"] * 6
        + ["W24X229"] * 2
        + ["W24X192"] * 2
        + ["W24X162"] * 2
        + ["W24X131"] * 4,
        "col_int": ["W24X306"] * 6
        + ["W24X279"] * 6
        + ["W24X229"] * 2
        + ["W24X192"] * 2
        + ["W24X162"] * 2
        + ["W24X131"] * 4,
        "girder": ["W12X14"] + ["W27X129"] * 8 + ["W24X103"] * 4 + ["W24X84"] * 3 + ["W21X62"] * 6,
        "gravity_col": ["W14X426"] * 2
        + ["W14X398"] * 2
        + ["W14X342"] * 2
        + ["W14X311"] * 2
        + ["W14X257"] * 2
        + ["W14X233"] * 2
        + ["W14X193"] * 2
        + ["W14X159"] * 2
        + ["W14X120"] * 2
        + ["W14X90"] * 2
        + ["W14X61"] * 2,
        "beam_40": ["W21X44", "W21X50"] + ["W21X44"] * 19 + ["W18X35"],
        "beam_20": ["W12X14", "W12X16"] + ["W12X14"] * 20,
    },
    # ── LA 9-story redesigns (Table B-9) ───────────────────────────────
    # Gravity columns / beams are unchanged from LA 9 pre (B-9 lists only the
    # redesigned moment-frame members).
    ("LA", 9, "r1"): {
        "col_ext": ["W14X283"] * 2
        + ["W14X211"] * 2
        + ["W14X193"] * 2
        + ["W14X145"] * 2
        + ["W14X90"] * 2,
        "col_int": ["W14X311"] * 2
        + ["W14X233"] * 2
        + ["W14X193"] * 2
        + ["W14X145"] * 2
        + ["W14X90"] * 2,
        "girder": [
            "W36X210",
            "W36X210",
            "W36X210",
            "W36X150",
            "W36X150",
            "W36X135",
            "W36X135",
            "W33X118",
            "W33X118",
            "W24X68",
        ],
        "gravity_col": ["W14X211"] * 3
        + ["W14X159"] * 2
        + ["W14X120"] * 2
        + ["W14X90"] * 2
        + ["W14X61"],
        "gravity_col_other": ["W14X193"] * 3
        + ["W14X145"] * 2
        + ["W14X109"] * 2
        + ["W14X82"] * 2
        + ["W14X48"],
        "beam": ["W21X44"] + ["W18X35"] * 8 + ["W16X26"],
    },
    ("LA", 9, "r2"): {
        "col_ext": ["W14X233"] * 2
        + ["W14X145"] * 2
        + ["W14X132"] * 2
        + ["W14X109"] * 2
        + ["W14X74"] * 2,
        "col_int": ["W14X233"] * 2
        + ["W14X145"] * 2
        + ["W14X132"] * 2
        + ["W14X109"] * 2
        + ["W14X74"] * 2,
        "girder": [
            "W33X118",
            "W33X118",
            "W30X99",
            "W27X94",
            "W27X102",
            "W24X84",
            "W27X84",
            "W24X62",
            "W24X62",
            "W18X35",
        ],
        "gravity_col": ["W14X211"] * 3
        + ["W14X159"] * 2
        + ["W14X120"] * 2
        + ["W14X90"] * 2
        + ["W14X61"],
        "gravity_col_other": ["W14X193"] * 3
        + ["W14X145"] * 2
        + ["W14X109"] * 2
        + ["W14X82"] * 2
        + ["W14X48"],
        "beam": ["W21X44"] + ["W18X35"] * 8 + ["W16X26"],
    },
    ("LA", 9, "r3"): {
        "col_ext": ["W14X342"] * 2
        + ["W14X283"] * 2
        + ["W14X233"] * 2
        + ["W14X193"] * 2
        + ["W14X159"] * 2,
        "col_int": ["W14X426"] * 2
        + ["W14X283"] * 2
        + ["W14X257"] * 2
        + ["W14X211"] * 2
        + ["W14X159"] * 2,
        "girder": [
            "W36X256",
            "W36X256",
            "W36X210",
            "W36X210",
            "W36X182",
            "W36X182",
            "W33X141",
            "W33X130",
            "W30X116",
            "W24X94",
        ],
        "gravity_col": ["W14X211"] * 3
        + ["W14X159"] * 2
        + ["W14X120"] * 2
        + ["W14X90"] * 2
        + ["W14X61"],
        "gravity_col_other": ["W14X193"] * 3
        + ["W14X145"] * 2
        + ["W14X109"] * 2
        + ["W14X82"] * 2
        + ["W14X48"],
        "beam": ["W21X44"] + ["W18X35"] * 8 + ["W16X26"],
    },
}


# ═══════════════════════════════════════════════════════════════════
# Model builders
# ═══════════════════════════════════════════════════════════════════

_UNITS: dict[str, str] = {"F": "N", "L": "m", "T": "C"}


def _steel(name: str = "Steel", fy: Optional[float] = None) -> Material:
    """A steel material in SI (Pa)."""
    return Material(
        name=name,
        type="Steel",
        E_mod=E_STEEL,
        G_mod=G_STEEL,
        nu=0.3,
        unit_weight=7.85e4,
        Fy=fy,
    )


def _section(name: str, material: str = "Steel") -> ISection:
    """Build the section object for a schedule name (W-shape or 15x15 box)."""
    if name.startswith("15X15"):
        A, I33, I22, J, D, BF, TF, TW, Z33, Z22 = _BOX_SECTIONS[name]
        return BoxSection(
            name=name,
            shape="Box/Tube",
            material=material,
            A=A,
            I33=I33,
            I22=I22,
            J=J,
            depth=D,
            bf=BF,
            tf=TF,
            tw=TW,
            Z33=Z33,
            Z22=Z22,
        )
    A, I33, I22, J, D, BF, TF, TW, Z33, Z22 = _SECTIONS[name]
    return ISection(
        name=name,
        shape="I/Wide Flange",
        material=material,
        A=A,
        I33=I33,
        I22=I22,
        J=J,
        depth=D,
        bf=BF,
        tf=TF,
        tw=TW,
        Z33=Z33,
        Z22=Z22,
    )


def _assemble(
    nodes,
    restraints,
    sections,
    frame_elements,
    frame_assignments,
    frame_releases=None,
    added_masses=None,
    joint_loads=None,
    area_elements=None,
    steel_name="Steel",
    fy=None,
) -> SAPModelData:
    """Assemble a minimal ``SAPModelData`` (one shared steel material)."""
    materials: dict[str, Material] = {steel_name: _steel(steel_name, fy)}
    for sec in sections.values():
        if sec.material not in materials:
            materials[sec.material] = _steel(sec.material)
    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements=area_elements or {},
        frame_assignments=frame_assignments,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_releases=frame_releases or {},
        joint_loads=joint_loads or [],
        added_masses=added_masses or {},
        # A masses-only MASS SOURCE is what makes ``compute_seismic_masses``
        # actually apply ``added_masses``: without it the modal / RS / pushover
        # runners fall back to element self-weight + DEAD loads and silently
        # drop the seismic masses.  Mirrors the Tcl importer's behaviour.
        mass_sources=({"MSSSRC1": MassSource(name="MSSSRC1", masses=True)} if added_masses else {}),
        units=_UNITS,
    )


def _add_col(frames, assigns, tag, ni, nj, sec, eid, angle=0.0):
    """Add a column frame element; return the next tag."""
    frames[eid] = FrameElement(
        elem_id=eid, elem_tag=tag, node_i=str(ni), node_j=str(nj), angle=angle
    )
    assigns[eid] = sec
    return tag + 1


def _add_beam(frames, assigns, releases, tag, ni, nj, sec, eid, release_i=False, release_j=False):
    """Add a beam; release M3 (major moment) at the I and/or J end."""
    frames[eid] = FrameElement(elem_id=eid, elem_tag=tag, node_i=str(ni), node_j=str(nj))
    assigns[eid] = sec
    if release_i or release_j:
        releases[eid] = FrameRelease(
            eid,
            end_i=[0, 0, 0, 0, 0, 1] if release_i else [0] * 6,
            end_j=[0, 0, 0, 0, 0, 1] if release_j else [0] * 6,
        )
    return tag + 1


def _sections_for(sched: dict) -> dict[str, ISection]:
    """Build the unique section objects a schedule references."""
    names: set[str] = set()
    for key in (
        "col_ext",
        "col_int",
        "col_next",
        "gravity_col",
        "gravity_col_40",
        "gravity_col_20",
        "gravity_col_other",
    ):
        v = sched.get(key)
        if isinstance(v, str):
            names.add(v)
        elif isinstance(v, list):
            names.update(v)
    for key in ("girder", "beam", "beam_40", "beam_20", "beam_gravity"):
        names.update(sched.get(key, []))
    return {n: _section(n) for n in names}


def _gravity_col_sec(sched: dict, j: int) -> str:
    """Gravity-column section for a grid point outside the penthouse footprint.

    Preference order mirrors the split gravity-column groups: the dedicated
    ``gravity_col_other`` tier first, then the 4-column ``gravity_col_40``
    tier (split schedules without a separate "other" group), then the single
    ``gravity_col`` group.  ``col_int`` is a last-resort fallback only.

    Args:
        sched: The member schedule dict for the current model.
        j: The zero-based story index into the schedule lists.

    Returns:
        The selected section name (e.g. ``"W14X193"``).
    """
    if "gravity_col_other" in sched:
        return sched["gravity_col_other"][j]
    if "gravity_col_40" in sched:
        return sched["gravity_col_40"][j]
    return sched.get("gravity_col", sched["col_int"])[j]


def _parse_penthouse(s: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """Parse a ``"C3-D5"`` footprint into ((NS lo, hi), (EW lo, hi)) 0-based indices."""
    a, b = s.split("-")
    gi, li = ord(a[0]) - ord("A"), int(a[1:]) - 1
    gj, lj = ord(b[0]) - ord("A"), int(b[1:]) - 1
    return (min(gi, gj), max(gi, gj)), (min(li, lj), max(li, lj))


def _levels(heights: list[float]) -> list[float]:
    """Cumulative z of each node level (base .. roof)."""
    z = 0.0
    out = [0.0]
    for h in heights:
        z += h
        out.append(z)
    return out


def _grid_label(i: int, k: Optional[int] = None) -> str:
    """Grid label for an N-S line (``k is None``) or an N-S x E-W point.

    N-S lines letter ``A``…; E-W lines number ``1``… (e.g. ``A1``, ``C3``).
    """
    ns = chr(ord("A") + i)
    return ns if k is None else f"{ns}{k + 1}"


def _basement_count(base: str) -> int:
    """Number of basement levels implied by a geometry's base type."""
    return {"fixed": 0, "pinned_ground": 1, "pinned_ground_2basement": 2}[base]


def _story_labels(n_levels: int, base: str) -> list[str]:
    """Level labels, bottom to top: ``02B``/``01B``, ``01F``…, ``RF``.

    Basements label deepest-first (``02B`` below ``01B``); the ground line is
    ``01F`` and the roof is ``RF``.
    """
    n_base = _basement_count(base)
    labels: list[str] = []
    for j in range(n_levels + 1):
        if j < n_base:
            labels.append(f"{n_base - j:02d}B")
        elif j == n_levels:
            labels.append("RF")
        else:
            labels.append(f"{j - n_base + 1:02d}F")
    return labels


def _out_of_plane() -> list[int]:
    """Restraint for a free in-plane node of a planar X-Z frame (UY/RX/RZ fixed)."""
    return [0, 1, 0, 1, 0, 1]


def _lump_2d(masses, n_lines, ns, grid, n_levels) -> dict[str, float]:
    """Distribute per-level masses across column lines (half the building, 2D).

    ``masses`` is bottom-up per above-grade level; its last entry is always
    the roof.  The roof sits at the top level ``j == n_levels``, so the
    lowest mass-bearing level is ``first = n_levels - len(masses) + 1`` —
    the levels below ``first`` (the base and any basement levels) carry no
    seismic mass.
    """
    added: dict[str, float] = {}
    first = n_levels - len(masses) + 1
    for idx, m in enumerate(masses):
        j = first + idx
        weights = [0.5 if i in (0, ns) else 1.0 for i in range(n_lines)]
        total = sum(weights)
        for i, w in enumerate(weights):
            added[grid[(i, j)]] = m * 0.5 * w / total
    return added


def _lump_3d(masses, ns, ew, n_ns_lines, n_ew_lines, grid, n_levels) -> dict[str, float]:
    """Distribute per-level masses across the plan by tributary slab area (3D).

    ``masses`` is bottom-up per above-grade level; its last entry is always
    the roof.  The roof sits at the top level ``j == n_levels``, so the
    lowest mass-bearing level is ``first = n_levels - len(masses) + 1`` —
    the levels below ``first`` (the base and any basement levels) carry no
    seismic mass.
    """
    added: dict[str, float] = {}
    first = n_levels - len(masses) + 1
    for idx, m in enumerate(masses):
        j = first + idx
        for i in range(n_ns_lines):
            for k in range(n_ew_lines):
                w = 1.0
                if i in (0, n_ns_lines - 1):
                    w *= 0.5
                if k in (0, n_ew_lines - 1):
                    w *= 0.5
                added[grid[(i, k, j)]] = m * w / (ns * ew)
    return added


def _gravity_2d(ns, ew_bays, bay, n_levels, grid) -> list[JointLoad]:
    """Nodal DEAD/LIVE loads for the planar frame (half-building tributary)."""
    n_lines = ns + 1
    area = (ns * bay) * (ew_bays * bay / 2.0)
    weights = [0.5 if i in (0, ns) else 1.0 for i in range(n_lines)]
    total = sum(weights)
    loads: list[JointLoad] = []
    for j in range(1, n_levels + 1):
        dead_p = DEAD_ROOF_PA if j == n_levels else DEAD_FLOOR_PA
        for i, w in enumerate(weights):
            frac = w / total
            nid = grid[(i, j)]
            loads.append(JointLoad(pattern="DEAD", node_id=nid, fz=-dead_p * area * frac))
            loads.append(JointLoad(pattern="LIVE", node_id=nid, fz=-LIVE_PA * area * frac))
    return loads


def _gravity_3d(ns, ew, bay, n_levels, grid) -> list[JointLoad]:
    """Nodal DEAD/LIVE loads for the full 3D building (whole footprint)."""
    area = (ns * bay) * (ew * bay)
    n_ns = ns + 1
    n_ew = ew + 1
    loads: list[JointLoad] = []
    for j in range(1, n_levels + 1):
        dead_p = DEAD_ROOF_PA if j == n_levels else DEAD_FLOOR_PA
        for i in range(n_ns):
            for k in range(n_ew):
                w = 1.0
                if i in (0, n_ns - 1):
                    w *= 0.5
                if k in (0, n_ew - 1):
                    w *= 0.5
                frac = w / (ns * ew)
                nid = grid[(i, k, j)]
                loads.append(JointLoad(pattern="DEAD", node_id=nid, fz=-dead_p * area * frac))
                loads.append(JointLoad(pattern="LIVE", node_id=nid, fz=-LIVE_PA * area * frac))
    return loads


def _floor_panels_3d(ns, ew, n_levels, grid, story) -> dict[str, AreaElement]:
    """One loads-only floor panel per bay per level (3D), for diaphragm viewing."""
    panels: dict[str, AreaElement] = {}
    tag = 1
    for j in range(1, n_levels + 1):
        for i in range(ns):
            for k in range(ew):
                aid = f"P-{_grid_label(i, k)}-{story[j]}"
                panels[aid] = AreaElement(
                    area_id=aid,
                    area_tag=tag,
                    node_ids=[
                        grid[(i, k, j)],
                        grid[(i + 1, k, j)],
                        grid[(i + 1, k + 1, j)],
                        grid[(i, k + 1, j)],
                    ],
                )
                tag += 1
    return panels


def _masses_for(n_levels: int) -> list[float]:
    """Seismic mass list (bottom-up) for a building's *level* count.

    Keyed on ``n_levels = len(heights)`` — the number of levels *including*
    basements — not the story count: 3-story → 3 levels, 9-story → 10 (one
    basement), 20-story → 22 (two basements).  The lists are per above-grade
    level; :func:`_lump_2d` / :func:`_lump_3d` anchor them to the correct
    level index (see the ``first`` derivation there).
    """
    by_levels = {3: MASS_3STORY, 10: MASS_9STORY, 22: MASS_20STORY}
    if n_levels not in by_levels:
        raise KeyError(f"no seismic-mass schedule for {n_levels} levels")
    return by_levels[n_levels]


def schedules() -> list[tuple[str, int, str]]:
    """Return the encoded ``(region, stories, design)`` triples in display order.

    Ordered region (LA, SE, BO), then stories (3, 9, 20), then design
    (pre, post, rbs, r1, r2, r3) — a stable order for chooser UIs and tests.
    This is the single source of truth for which combinations
    :func:`build_fema355c` accepts.

    Returns:
        The 22 schedule keys.
    """
    _region_order = {"LA": 0, "SE": 1, "BO": 2}
    _design_order = {"pre": 0, "post": 1, "rbs": 2, "r1": 3, "r2": 4, "r3": 5}
    return sorted(
        _SCHEDULES.keys(),
        key=lambda key: (_region_order[key[0]], key[1], _design_order.get(key[2], 99)),
    )


def build_fema355c(
    region: str,
    stories: int,
    design: str,
    direction: str = "3d",
    restrain_ground_laterally: bool = True,
) -> SAPModelData:
    """Build one FEMA 355C / SAC Phase 2 model as a ``SAPModelData``.

    Args:
        region: ``"LA"``, ``"SE"`` or ``"BO"``.
        stories: 3, 9 or 20.
        design: ``"pre"``, ``"post"``, Seattle ``"rbs"``, or LA9 ``"r1"/"r2"/"r3"``.
        direction: ``"3d"`` (full building) or ``"2d"`` (planar N-S moment frame).
        restrain_ground_laterally: For basement buildings (9- and 20-story),
            whether the ground level (top of the basement stack) is laterally
            restrained, modelling the concrete foundation walls / surrounding
            soil.  ``True`` (default) matches the benchmark's definition;
            ``False`` restrains the structure only at the bottom basement level
            — the common "bounding" alternative for basement analyses.

    Returns:
        The assembled model in SI (N, m, kg); materials authored in Pa (SI).
    """
    region = region.upper()
    design = design.lower()
    direction = direction.lower()
    geom = _GEOM[stories]
    mr = _MR.get((region, stories, design), _MR[(region, stories)])
    if region == "LA" and stories == 9 and design == "r2":
        # R2-LA9 redesign: 15 ft bays, 9 moment bays per face (18 connections).
        geom = {
            "ns_bays": 10,
            "ew_bays": 10,
            "bay": BAY_15,
            "heights": _GEOM[9]["heights"],
            "base": "pinned_ground",
            "penthouse": "D5-G7",
        }
        mr = {"ns_bays_moment": 9, "ew_bays_moment": 9, "pinned": None, "box_corners": False}
    sched = _SCHEDULES[(region, stories, design)]
    _, col_fy = _STEEL_FY[region]
    if direction == "2d":
        return _build_2d(geom, mr, sched, col_fy, restrain_ground_laterally)
    return _build_3d(geom, mr, sched, col_fy, restrain_ground_laterally)


def _build_3d(geom, mr, sched, fy, restrain_ground_laterally=True) -> SAPModelData:
    """Build the full 3D building: 4 perimeter moment frames + gravity columns."""
    ns = geom["ns_bays"]
    ew = geom["ew_bays"]
    bay = geom["bay"]
    heights = geom["heights"]
    n_levels = len(heights)  # number of *levels* (incl. basements), not stories
    n_ns = ns + 1
    n_ew = ew + 1
    levels = _levels(heights)
    story = _story_labels(n_levels, geom["base"])
    (pi0, pi1), (pk0, pk1) = _parse_penthouse(geom["penthouse"])
    two_group = "gravity_col_40" in sched  # 20-story: 4 + 2 gravity columns
    pinned = mr.get("pinned")
    ns_moment = mr["ns_bays_moment"]
    ew_moment = mr["ew_bays_moment"]
    ew_start = mr.get("ew_moment_start", 0)
    ew_end = ew_start + ew_moment  # one past the last E-W moment bay

    # ── Nodes ──
    nodes: dict[str, Node] = {}
    grid: dict[tuple[int, int, int], str] = {}
    tag = 1
    for i in range(n_ns):
        for k in range(n_ew):
            for j in range(n_levels + 1):
                nid = f"{_grid_label(i, k)}-{story[j]}"
                nodes[nid] = Node(node_id=nid, node_tag=tag, x=i * bay, y=k * bay, z=levels[j])
                grid[(i, k, j)] = nid
                tag += 1

    sections = _sections_for(sched)
    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    releases: dict[str, FrameRelease] = {}
    etag = 1

    def is_corner(i, k):
        return i in (0, ns) and k in (0, ew)

    def is_perim(i, k):
        return i in (0, ns) or k in (0, ew)

    def is_next(i, k):
        """Perimeter column adjacent to a corner (Boston/Seattle 20-story)."""
        if i in (0, ns):
            return k in (1, ew - 1)
        if k in (0, ew):
            return i in (1, ns - 1)
        return False

    def in_pent(i, k):
        return pi0 <= i <= pi1 and pk0 <= k <= pk1

    def gravity_sec(i, k, j):
        """Gravity column section for an interior grid point, or None (no column)."""
        if in_pent(i, k):
            if two_group:
                if k in (pk0, pk1):
                    return sched["gravity_col_40"][j]  # 4 corner (E-W edge) columns
                return sched["gravity_col_20"][j]  # 2 middle columns
            return sched["gravity_col"][j]
        # Outside the penthouse: the 20-story interior is open; the 3-/9-story has
        # gravity columns throughout.
        if n_levels == 22:
            return None
        return _gravity_col_sec(sched, j)

    # Column orientation: a column is strong-axis E-W (angle 90°) when it sits in
    # an E-W moment frame (grids A/F) — or is the 3-story leaning/gravity column —
    # and strong-axis N-S (angle 0°) when it belongs to an N-S frame.  ``col_rotation``
    # (set in ``_MR``) selects the layout's E-W column pattern.
    col_rotation = mr.get("col_rotation")

    def is_ew_moment_col(i, k):
        """3-story perimeter column on grid A/E inside the centred E-W frame."""
        return i in (0, ns) and ew_start <= k <= ew_end

    def is_ew_gravity_col(i, k):
        """3-story perimeter gravity column on grid A/E just east of the moment frame."""
        return col_rotation == "centered" and i in (0, ns) and k == ew_end + 1 and k < ew

    def is_ns_leaning_col(i, k):
        """Leaning column at the gravity end of the 3-story N-S frames (E1/E7)."""
        return col_rotation == "centered" and i == ns and k in (0, ew)

    def is_ew_frame_col(i, k):
        """9-story perimeter column on grid A/F that belongs to the E-W frame.

        Grid A (south) moments A1-A5 and pins A6; grid F (north) pins F1 and
        moments F2-F6 (the last beam going clockwise is pinned at the corner).
        """
        if i == 0:
            return k < ew
        if i == ns:
            return k > 0
        return False

    def is_20story_ew_col(i, k):
        """20-story perimeter column on grid A/F between the corners (E-W frame).

        The 20-story has all perimeter connections moment-resisting; the
        intermediate E-W face columns are strong-axis E-W while the corner
        columns stay strong-axis N-S.
        """
        return i in (0, ns) and 0 < k < ew

    def col_angle(i, k):
        """Column rotation (degrees): 90° = strong-axis E-W, dispatching on ``col_rotation``."""
        if col_rotation == "centered" and (
            is_ew_moment_col(i, k) or is_ew_gravity_col(i, k) or is_ns_leaning_col(i, k)
        ):
            return 90.0
        if col_rotation == "pinned_corner" and is_ew_frame_col(i, k):
            return 90.0
        if col_rotation == "perimeter" and is_20story_ew_col(i, k):
            return 90.0
        return 0.0

    # ── Columns ──
    has_next = "col_next" in sched  # Boston/Seattle 20-story "next to exterior"
    for i in range(n_ns):
        for k in range(n_ew):
            for j in range(n_levels):
                angle = col_angle(i, k)
                if is_corner(i, k):
                    sec = sched["col_ext"][j]  # corner column (box for LA 20)
                elif has_next and is_next(i, k):
                    sec = sched["col_next"][j]
                elif is_perim(i, k):
                    if col_rotation == "centered" and is_ew_moment_col(i, k):
                        sec = sched["col_int"][j]
                    elif is_ew_gravity_col(i, k):
                        sec = _gravity_col_sec(sched, j)
                    else:
                        sec = sched["col_int"][j]
                else:
                    sec = gravity_sec(i, k, j)
                    if sec is None:
                        continue
                eid = f"C-{_grid_label(i, k)}-{story[j]}"
                etag = _add_col(
                    frames, assigns, etag, grid[(i, k, j)], grid[(i, k, j + 1)], sec, eid, angle
                )

    # ── Beams: N-S (along X) then E-W (along Y) ──
    # Perimeter moment frames span ``ns_moment``/``ew_moment`` bays starting at
    # grid line 1 (or ``ew_start`` for the E-W faces); the bays beyond are
    # gravity bays (pinned) for ``pinned=None`` (3-/20-story) or a single
    # pinned moment bay for ``pinned="corner"`` (9-story).
    beam_ns = sched.get("beam_20", sched.get("beam"))  # N-S interior beams
    beam_ew = sched.get("beam_40", sched.get("beam"))  # E-W interior beams
    pinned_girder = sched.get("beam_gravity", sched.get("girder"))  # pinned-bay girder
    for j in range(1, n_levels + 1):
        lvl = j - 1
        for k in range(n_ew):
            for i in range(ns):
                sec = beam_ns[lvl]
                release_i = release_j = False
                if k in (0, ew):
                    if pinned is None:
                        # 3-/20-story (and R3): moment bays + gravity bay(s)
                        # released at the J (gravity) end.
                        if i < ns_moment:
                            sec = sched["girder"][lvl]
                        else:
                            release_j = True
                    else:
                        # 9-story: 4 moment bays + the last beam (going clockwise)
                        # pinned at the corner column (grid 1 → A1, grid 6 → F6).
                        pinned_bay = (k == 0 and i == 0) or (k == ew and i == ns - 1)
                        if pinned_bay:
                            sec = pinned_girder[lvl]
                            if pinned == "both":
                                release_i = release_j = True
                            elif k == 0:
                                release_i = True
                            else:
                                release_j = True
                        else:
                            sec = sched["girder"][lvl]
                eid = f"B-{_grid_label(i, k)}-{_grid_label(i + 1, k)}-{story[j]}"
                etag = _add_beam(
                    frames,
                    assigns,
                    releases,
                    etag,
                    grid[(i, k, j)],
                    grid[(i + 1, k, j)],
                    sec,
                    eid,
                    release_i=release_i,
                    release_j=release_j,
                )
        for i in range(n_ns):
            for k in range(ew):
                sec = beam_ew[lvl]
                release_i = release_j = False
                if i in (0, ns):
                    if pinned is None:
                        # 3-/20-story (and R3): moment bays + gravity bay(s)
                        # released at the J (gravity) end.
                        if ew_start <= k < ew_end:
                            sec = sched["girder"][lvl]
                        else:
                            release_j = True
                    else:
                        # 9-story: 4 moment bays + the last beam (going clockwise)
                        # pinned at the corner column (grid A → A6, grid F → F1).
                        pinned_bay = (i == 0 and k == ew - 1) or (i == ns and k == 0)
                        if pinned_bay:
                            sec = pinned_girder[lvl]
                            if pinned == "both":
                                release_i = release_j = True
                            elif i == 0:
                                release_j = True
                            else:
                                release_i = True
                        else:
                            sec = sched["girder"][lvl]
                eid = f"B-{_grid_label(i, k)}-{_grid_label(i, k + 1)}-{story[j]}"
                etag = _add_beam(
                    frames,
                    assigns,
                    releases,
                    etag,
                    grid[(i, k, j)],
                    grid[(i, k + 1, j)],
                    sec,
                    eid,
                    release_i=release_i,
                    release_j=release_j,
                )

    # ── Restraints ──
    # Base is pinned (or fixed for the basement-free 3-story); the ground level
    # (top of the basement stack, level ``n_base``) is laterally restrained to
    # model the concrete foundation walls / surrounding soil.  The benchmark
    # restrains the *ground* level — not the intermediate basement level — so
    # the lateral tie is placed at ``n_base`` (level 1 for the 9-story, level 2
    # for the 20-story), not always at level 1.
    n_base = _basement_count(geom["base"])
    restraints: dict[str, Restraint] = {}
    for i in range(n_ns):
        for k in range(n_ew):
            if geom["base"] == "fixed":
                restraints[grid[(i, k, 0)]] = Restraint([1, 1, 1, 1, 1, 1])
            else:  # pinned base at the bottom basement level
                restraints[grid[(i, k, 0)]] = Restraint([1, 1, 1, 1, 0, 1])
                if restrain_ground_laterally:
                    restraints[grid[(i, k, n_base)]] = Restraint([1, 1, 0, 1, 0, 1])

    masses = _masses_for(n_levels)
    added_masses = _lump_3d(masses, ns, ew, n_ns, n_ew, grid, n_levels)
    joint_loads = _gravity_3d(ns, ew, bay, n_levels, grid)
    area_elements = _floor_panels_3d(ns, ew, n_levels, grid, story)
    return _assemble(
        nodes,
        restraints,
        sections,
        frames,
        assigns,
        releases,
        added_masses,
        joint_loads,
        area_elements,
        fy=fy,
    )


def _build_2d(geom, mr, sched, fy, restrain_ground_laterally=True) -> SAPModelData:
    """Build the planar N-S moment frame (X-Z plane), matching the benchmark."""
    ns = geom["ns_bays"]
    bay = geom["bay"]
    heights = geom["heights"]
    n_levels = len(heights)  # number of *levels* (incl. basements), not stories
    n_ns = ns + 1
    levels = _levels(heights)
    story = _story_labels(n_levels, geom["base"])
    ns_moment = mr["ns_bays_moment"]
    pinned = mr.get("pinned")

    # Column-line roles: the moment frame spans ``ns_moment`` bays; the remaining
    # bay(s) are a gravity bay (pinned=None) or a pinned moment bay (pinned set).
    roles: list[str] = []
    if pinned is None:
        for i in range(n_ns):
            if i <= ns_moment:
                roles.append("ext" if i in (0, ns_moment) else "int")
            else:
                roles.append("gravity")
    else:
        for i in range(n_ns):
            roles.append("ext" if i in (0, ns) else "int")

    nodes: dict[str, Node] = {}
    grid: dict[tuple[int, int], str] = {}
    tag = 1
    for i in range(n_ns):
        for j in range(n_levels + 1):
            nid = f"{_grid_label(i)}-{story[j]}"
            nodes[nid] = Node(node_id=nid, node_tag=tag, x=i * bay, y=0.0, z=levels[j])
            grid[(i, j)] = nid
            tag += 1

    sections = _sections_for(sched)
    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    releases: dict[str, FrameRelease] = {}
    etag = 1

    def col_sec(i, j):
        role = roles[i]
        if role == "gravity":
            return _gravity_col_sec(sched, j)
        return sched["col_" + role][j]

    for i in range(n_ns):
        for j in range(n_levels):
            eid = f"C-{_grid_label(i)}-{story[j]}"
            etag = _add_col(
                frames, assigns, etag, grid[(i, j)], grid[(i, j + 1)], col_sec(i, j), eid
            )

    for j in range(1, n_levels + 1):
        lvl = j - 1
        for i in range(ns):
            if pinned is None and i >= ns_moment:
                # gravity bay: gravity beam, released at the J (gravity) end
                sec = sched.get("beam", sched["girder"])[lvl]
                release = True
                both = False
            elif pinned is not None and i == ns - 1:
                # pinned corner bay: special (single-MR) girder, released at J
                sec = sched.get("beam_gravity", sched["girder"])[lvl]
                release = True
                both = pinned == "both"
            else:
                sec = sched["girder"][lvl]
                release = False
                both = False
            eid = f"B-{_grid_label(i)}-{_grid_label(i + 1)}-{story[j]}"
            etag = _add_beam(
                frames,
                assigns,
                releases,
                etag,
                grid[(i, j)],
                grid[(i + 1, j)],
                sec,
                eid,
                release_i=(release and both),
                release_j=release,
            )

    # Same ground-level lateral-tie logic as the 3D builder: it sits at the top
    # of the basement stack (level ``n_base``), not always at level 1.
    n_base = _basement_count(geom["base"])
    restraints: dict[str, Restraint] = {}
    for i in range(n_ns):
        for j in range(n_levels + 1):
            nid = grid[(i, j)]
            if j == 0:
                restraints[nid] = Restraint(
                    [1, 1, 1, 1, 1, 1] if geom["base"] == "fixed" else [1, 1, 1, 1, 0, 1]
                )
            elif n_base > 0 and j == n_base and restrain_ground_laterally:
                restraints[nid] = Restraint([1, 1, 0, 1, 0, 1])
            else:
                restraints[nid] = Restraint(_out_of_plane())

    masses = _masses_for(n_levels)
    added_masses = _lump_2d(masses, n_ns, ns, grid, n_levels)
    joint_loads = _gravity_2d(ns, geom["ew_bays"], bay, n_levels, grid)
    return _assemble(
        nodes,
        restraints,
        sections,
        frames,
        assigns,
        releases,
        added_masses,
        joint_loads,
        fy=fy,
    )


def write_snapshots(dest: Optional[Path] = None) -> list[Path]:
    """Write one JSON snapshot per encoded (region, stories, design, direction).

    Args:
        dest: Target directory (default ``docs/assets``).

    Returns:
        The list of written file paths.
    """
    dest = dest or (Path(__file__).resolve().parents[3] / "docs" / "assets")
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for region, stories, design in _SCHEDULES:
        for direction in ("2d", "3d"):
            name = f"fema355c_{region.lower()}{stories}_{design}_{direction}"
            path = dest / f"{name}.json"
            path.write_text(
                model_to_json(build_fema355c(region, stories, design, direction)),
                encoding="utf-8",
            )
            written.append(path)
    return written


__all__ = [
    "BAY_15",
    "BAY_20",
    "BAY_30",
    "DEAD_FLOOR_PA",
    "DEAD_ROOF_PA",
    "H12",
    "H13",
    "H18",
    "LIVE_PA",
    "MASS_3STORY",
    "MASS_9STORY",
    "MASS_20STORY",
    "PSF_TO_PA",
    "build_fema355c",
    "schedules",
    "write_snapshots",
]
