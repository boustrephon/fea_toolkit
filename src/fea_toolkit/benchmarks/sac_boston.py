"""FEMA 355C / SAC Phase 2 benchmark buildings — Boston 3- and 9-story (2D + 3D).

FEMA 355C (the SAC Steel Project "State of the Art Report") documents prototype
steel moment-frame buildings for the Los Angeles, Seattle and Boston regions.
This module builds the **Boston** buildings as loadable ``SAPModelData`` models
— a planar N-S perimeter moment frame (2D) and the full building (3D) for each
of the two heights — for viewing in the FEA-GUI and for analysis through the
standard two-stage pipeline.  The Los Angeles buildings are the Ohtori
benchmark in :mod:`fea_toolkit.benchmarks.sac`.

The member schedules are transcribed from the FEMA 355C tables (see
``docs/sac_boston_benchmark.md`` for the tables and their interpretation):

* **Boston 3-story** — 4 N-S x 6 E-W bays @ 30 ft, 3 stories @ 13 ft, fixed bases.
  The N-S moment frame is **3 bays** (grids A-D) plus a **gravity bay**
  (grid D-E): a vertical gravity column whose inertia is transferred to the
  moment frame through a beam released for major moment.  Columns W14x74
  (exterior) / W14x99 (interior); girders W18x35 / W21x57 / W21x62.
* **Boston 9-story** — 5 x 5 bays @ 30 ft, basement 12 ft + ground 18 ft + 8 stories
  @ 13 ft, bases pinned at B-1 with lateral restraint at ground.  The N-S moment
  frame is **4.5 bays** (4 full moment bays + one bay with a single moment
  connection) plus a gravity column (grid F).  Columns W14x211/W14x283
  at the base reducing to W14x61/W14x120 at the roof (splices every two levels);
  girders W36x135 down to W18x40.

Section properties are AISC wide-flange values converted to SI, sourced from the
project's section database (``data/section_dict.pkl``, AISC13 catalogue) and
hardcoded here so the benchmark is self-contained.  ``E = 200 GPa``, steel.

Units are SI (N, m, kg); materials are authored in SI (Pa) and used as-is
because the model itself is SI (see the material-property convention in
``.clinerules`` §4.6).

Simplifications (documented, not fabrications):

* The penthouse is *not* modelled structurally — it only contributes mass,
  which these viewing models do not carry.
* The gravity column (grid E / F) is a plain vertical column; the floor beams
  tying it to the moment frame are released for major moment (M3) at that
  column's end, so its inertia is passed to the moment frame as axial load.
* Doubler plates are all 0.0 in the source and are ignored.
* Seismic masses are **optional** — supply per-level masses via the ``masses``
  parameter; they are lumped at the nodes by tributary area (no rotational
  inertia), matching the benchmark's lumped-mass convention.
* Dead (96 psf floor / 83 psf roof) and reduced live (20 psf) loads are applied
  as nodal ``joint_loads`` (``DEAD`` / ``LIVE`` patterns) by tributary area;
  the steel frame self-weight (13 psf) is carried by material density.  The 3D
  builders also emit one loads-only floor panel per bay per level for
  diaphragm visualisation (no shell stiffness).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..io.model_codec import model_to_json
from ..model.sap_data import (
    AreaElement,
    FrameElement,
    FrameRelease,
    ISection,
    JointLoad,
    Material,
    Node,
    Restraint,
    SAPModelData,
)

# ── Material / unit constants ──────────────────────────────────────────
E_STEEL = 2.0e11  # Pa
G_STEEL = 7.7e10  # Pa
FT = 0.3048  # m per foot
BAY = 30.0 * FT  # 9.144 m
H13 = 13.0 * FT  # 3.9624 m  (typical story)
H12 = 12.0 * FT  # 3.6576 m  (basement)
H18 = 18.0 * FT  # 5.4864 m  (ground floor, 9-story)

# ── Gravity load pressures (FEMA 355C / Ohtori 2004 floor-load distribution) ──
# Dead/live are *superimposed* pressures (force/area) applied as nodal loads;
# the steel frame self-weight (13 psf) is captured separately by material density.
PSF_TO_PA = 47.88025898  # N/m² per lb/ft²
DEAD_FLOOR_PA = 96.0 * PSF_TO_PA  # 4596.5 N/m²  (floor dead, for weight)
DEAD_ROOF_PA = 83.0 * PSF_TO_PA  # 3974.1 N/m²  (roof dead, excl. penthouse)
LIVE_PA = 20.0 * PSF_TO_PA  # 957.6 N/m²  (reduced live, floor + roof)

# ── AISC wide-flange sections (SI) ─────────────────────────────────────
# name -> (A, I33, I22, J, D, BF, TF, TW, Z33, Z22)  [m², m⁴, m⁴, m⁴, m, m, m, m, m³, m³]
_SECTIONS: dict[str, tuple[float, ...]] = {
    "W14X74": (
        0.014064488,
        0.000330903963,
        5.5775008e-05,
        1.61081552e-06,
        0.36068,
        0.25654,
        0.019939,
        0.01143,
        0.00206477,
        0.000663676,
    ),
    "W14X99": (
        0.018774156,
        0.000462016854,
        0.000167325023,
        2.23516262e-06,
        0.36068,
        0.37084,
        0.019812,
        0.012319,
        0.002834962,
        0.001369959,
    ),
    "W18X35": (
        0.006645148,
        0.000212278014,
        6.36834e-06,
        2.1061309e-07,
        0.44958,
        0.1524,
        0.010795,
        0.00762,
        0.00108974,
        0.00013208,
    ),
    "W21X57": (
        0.010774172,
        0.000486990738,
        1.2736681e-05,
        7.3672958e-07,
        0.53594,
        0.166624,
        0.01651,
        0.010287,
        0.002113931,
        0.000242529,
    ),
    "W21X62": (
        0.011806428,
        0.000553587762,
        2.3933306e-05,
        7.6170346e-07,
        0.5334,
        0.209296,
        0.015621,
        0.01016,
        0.002359737,
        0.000355599,
    ),
    "W16X26": (
        0.004954829,
        0.000125285651,
        3.991659e-06,
        1.0905263e-07,
        0.39878,
        0.1397,
        0.008763,
        0.00635,
        0.000724308,
        8.9801e-05,
    ),
    "W14X22": (
        0.004187088,
        8.2830049e-05,
        2.91362e-06,
        8.657613e-08,
        0.34798,
        0.127,
        0.008509,
        0.005842,
        0.000544051,
        7.1939e-05,
    ),
    "W12X65": (
        0.012322556,
        0.000221851336,
        7.2424264e-05,
        9.0738445e-07,
        0.30734,
        0.3048,
        0.015367,
        0.009906,
        0.001586268,
        0.00072267,
    ),
    "W12X72": (
        0.013612876,
        0.000248490146,
        8.1165123e-05,
        1.219558e-06,
        0.31242,
        0.3048,
        0.017018,
        0.010922,
        0.001769803,
        0.000806244,
    ),
    "W12X58": (
        0.01096772,
        0.000197709915,
        4.453676e-05,
        8.7408594e-07,
        0.30988,
        0.254,
        0.016256,
        0.009144,
        0.001415842,
        0.00053258,
    ),
    "W14X68": (
        0.0129032,
        0.000300519071,
        5.0363999e-05,
        1.25285651e-06,
        0.3556,
        0.254,
        0.018288,
        0.010541,
        0.001884512,
        0.000604683,
    ),
    "W14X211": (
        0.03999992,
        0.001107175524,
        0.000428718342,
        1.856392044e-05,
        0.39878,
        0.40132,
        0.039624,
        0.024892,
        0.006390955,
        0.003244639,
    ),
    "W14X283": (
        0.053741828,
        0.001598328576,
        0.000599373216,
        4.32880656e-05,
        0.42418,
        0.40894,
        0.052578,
        0.032766,
        0.008881789,
        0.004490056,
    ),
    "W14X159": (
        0.030128972,
        0.00079083966,
        0.000311341087,
        8.19975858e-06,
        0.381,
        0.39624,
        0.030226,
        0.018923,
        0.004703087,
        0.002392511,
    ),
    "W14X233": (
        0.04419346,
        0.001252856514,
        0.00047866611,
        2.47657683e-05,
        0.4064,
        0.40386,
        0.043688,
        0.027178,
        0.00714476,
        0.003621541,
    ),
    "W14X132": (
        0.025032208,
        0.000636834042,
        0.000228094807,
        5.11964622e-06,
        0.37338,
        0.37338,
        0.026162,
        0.016383,
        0.003834573,
        0.001851738,
    ),
    "W14X120": (
        0.022774148,
        0.000574399332,
        0.000206034543,
        3.90008822e-06,
        0.3683,
        0.37338,
        0.023876,
        0.014986,
        0.003474058,
        0.001671481,
    ),
    "W14X90": (
        0.01709674,
        0.000415815169,
        0.000150675767,
        1.68989948e-06,
        0.3556,
        0.3683,
        0.018034,
        0.011176,
        0.002572769,
        0.001238862,
    ),
    "W14X61": (
        0.011548364,
        0.000266388096,
        4.453676e-05,
        9.1154677e-07,
        0.35306,
        0.254,
        0.016383,
        0.009525,
        0.001671481,
        0.000537496,
    ),
    "W14X176": (
        0.033419288,
        0.000890735196,
        0.000348801913,
        1.10301321e-05,
        0.38608,
        0.39878,
        0.033274,
        0.021082,
        0.00524386,
        0.002671091,
    ),
    "W14X145": (
        0.027548332,
        0.000711755694,
        0.000281788658,
        6.32671728e-06,
        0.37592,
        0.3937,
        0.027686,
        0.017272,
        0.004260637,
        0.00217948,
    ),
    "W12X106": (
        0.020128992,
        0.000388343896,
        0.000125285651,
        3.80019268e-06,
        0.32766,
        0.30988,
        0.025146,
        0.015494,
        0.002687478,
        0.001230669,
    ),
    "W12X79": (
        0.014967712,
        0.000275545187,
        8.9905982e-05,
        1.59832858e-06,
        0.31496,
        0.30734,
        0.018669,
        0.011938,
        0.001950061,
        0.000889818,
    ),
    "W12X53": (
        0.010064496,
        0.000176898345,
        3.9874968e-05,
        6.5764561e-07,
        0.30734,
        0.254,
        0.014605,
        0.008763,
        0.001276552,
        0.000476864,
    ),
    "W12X120": (
        0.022774148,
        0.000445367598,
        0.000143599833,
        5.36938506e-06,
        0.33274,
        0.31242,
        0.028194,
        0.018034,
        0.003047994,
        0.001399455,
    ),
    "W8X48": (
        0.009096756,
        7.6586578e-05,
        2.5348492e-05,
        8.1581354e-07,
        0.2159,
        0.205994,
        0.017399,
        0.01016,
        0.000802966,
        0.000375264,
    ),
    "W24X68": (
        0.012967716,
        0.000761703462,
        2.9302691e-05,
        7.7835272e-07,
        0.60198,
        0.227838,
        0.014859,
        0.010541,
        0.00290051,
        0.000401483,
    ),
    "W36X135": (
        0.025612852,
        0.00324660492,
        9.3652065e-05,
        2.9136198e-06,
        0.90424,
        0.3048,
        0.020066,
        0.01524,
        0.008341016,
        0.000978308,
    ),
    "W33X118": (
        0.022387052,
        0.00245576526,
        7.7835272e-05,
        2.20602642e-06,
        0.83566,
        0.2921,
        0.018796,
        0.01397,
        0.006800632,
        0.000840656,
    ),
    "W30X116": (
        0.022064472,
        0.002052020802,
        6.826195e-05,
        2.6763679e-06,
        0.762,
        0.2667,
        0.02159,
        0.014351,
        0.00619431,
        0.000806244,
    ),
    "W30X108": (
        0.020451572,
        0.001860554358,
        6.0769784e-05,
        2.07699469e-06,
        0.75692,
        0.2667,
        0.019304,
        0.013843,
        0.005669924,
        0.000719392,
    ),
    "W30X99": (
        0.018774156,
        0.001660763286,
        5.3277619e-05,
        1.56919238e-06,
        0.75438,
        0.2667,
        0.017018,
        0.013208,
        0.005112764,
        0.000632541,
    ),
    "W27X94": (
        0.017870932,
        0.001361076678,
        5.1612694e-05,
        1.67741254e-06,
        0.68326,
        0.254,
        0.018923,
        0.012446,
        0.004555604,
        0.000635818,
    ),
    "W24X76": (
        0.014451584,
        0.00087408594,
        3.4339091e-05,
        1.11550015e-06,
        0.60706,
        0.228346,
        0.017272,
        0.011176,
        0.003277413,
        0.00046867,
    ),
    "W18X40": (
        0.007612888,
        0.000254733617,
        7.95002e-06,
        3.3714743e-07,
        0.45466,
        0.152908,
        0.013335,
        0.008001,
        0.001284746,
        0.000163871,
    ),
    "W27X84": (
        0.015999968,
        0.00118625949,
        4.4120528e-05,
        1.16961023e-06,
        0.67818,
        0.254,
        0.016256,
        0.011684,
        0.003998444,
        0.000544051,
    ),
    "W24X62": (
        0.011741912,
        0.00064515867,
        1.4359983e-05,
        7.1175569e-07,
        0.60198,
        0.178816,
        0.014986,
        0.010922,
        0.002507221,
        0.000257277,
    ),
    "W24X55": (
        0.010451592,
        0.00056191239,
        1.2112334e-05,
        4.9115305e-07,
        0.59944,
        0.178054,
        0.012827,
        0.010033,
        0.002195867,
        0.000217948,
    ),
}

# ── FEMA 355C member schedules (section *names*; properties resolved at build) ──
_S3 = {
    "n_ns": 4,
    "n_ew": 6,
    "n_story": 3,
    "height": [H13, H13, H13],
    "col_lines": ["ext", "int", "int", "ext", "gravity"],  # grids A..E
    "col_ext": "W14X74",
    "col_int": "W14X99",
    "col_gravity": "W12X58",
    "girder": ["W18X35", "W21X57", "W21X62"],  # moment-frame girders, by level (1..3)
    "beam": ["W16X26", "W16X26", "W14X22"],  # E-W gravity beams, by level
    "gravity_col": "W12X58",  # interior gravity columns (3D)
}

_S9 = {
    "n_ns": 5,
    "n_ew": 5,
    "height": [H12, H18] + [H13] * 8,  # 10 stories (B-1..roof)
    "col_lines": ["ext", "int", "int", "int", "int", "gravity"],  # grids A..F
    "col_ext": [
        "W14X211",
        "W14X211",
        "W14X211",
        "W14X159",
        "W14X159",
        "W14X132",
        "W14X132",
        "W14X99",
        "W14X99",
        "W14X61",
    ],
    "col_int": [
        "W14X283",
        "W14X283",
        "W14X283",
        "W14X233",
        "W14X233",
        "W14X211",
        "W14X211",
        "W14X176",
        "W14X176",
        "W14X120",
    ],
    "col_gravity": [
        "W14X211",
        "W14X211",
        "W14X211",
        "W14X159",
        "W14X159",
        "W14X132",
        "W14X132",
        "W14X99",
        "W14X99",
        "W14X68",
    ],  # grid F (note 1)
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
    ],  # by level (ground..roof)
    "beam": ["W16X26"] * 9 + ["W14X22"],  # E-W gravity beams
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
    ],  # single-MR bay
    "gravity_col": "W14X145",  # interior gravity columns (3D)
}

_UNITS: dict[str, str] = {"F": "N", "L": "m", "T": "C"}

# ── Seismic masses (FEMA 355C Appendix B) ──────────────────────────────
# Identical for the LA, Seattle and Boston buildings ("the basic definitions
# for the buildings were kept constant between the different regions").  The
# values are for the *entire* structure, per level, floor 1 .. roof, converted
# from kips-sec²/ft to kg (1 kip-sec²/ft = 14593.9 kg).  The 2D frame builders
# use half of these (one perimeter frame carries half the building's mass).
MASS_3STORY: list[float] = [956339.0, 956339.0, 1034708.0]  # 65.53, 65.53, 70.90 k-s²/ft
MASS_9STORY: list[float] = [
    965000.0,  # ground level (66.0 k-s²/ft, from Ohtori et al. 2004)
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


def _steel(name: str = "Steel", fy: Optional[float] = None) -> Material:
    """A steel material in SI (Pa)."""
    return Material(
        name=name, type="Steel", E_mod=E_STEEL, G_mod=G_STEEL, nu=0.3, unit_weight=7.85e4, Fy=fy
    )


def _isection(name: str, material: str = "Steel") -> ISection:
    """An :class:`ISection` from the AISC data (strong-axis oriented)."""
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
    frame_releases: Optional[dict[str, FrameRelease]] = None,
    added_masses: Optional[dict[str, float]] = None,
    joint_loads: Optional[list[JointLoad]] = None,
    area_elements: Optional[dict[str, AreaElement]] = None,
) -> SAPModelData:
    """Assemble a minimal ``SAPModelData`` (one steel material shared by all)."""
    materials: dict[str, Material] = {"Steel": _steel()}
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
        units=_UNITS,
    )


# ── Frame builders ─────────────────────────────────────────────────────
def _add_col(frames, assigns, tag, ni, nj, sec):
    """Add a frame element and assignment; return the next tag."""
    frames[str(tag)] = FrameElement(elem_id=str(tag), elem_tag=tag, node_i=str(ni), node_j=str(nj))
    assigns[str(tag)] = sec
    return tag + 1


def _add_beam(frames, assigns, releases, tag, ni, nj, sec, release_j: bool = False):
    """Add a beam; ``release_j`` releases M3 (major moment) at the J end."""
    eid = str(tag)
    frames[eid] = FrameElement(elem_id=eid, elem_tag=tag, node_i=str(ni), node_j=str(nj))
    assigns[eid] = sec
    if release_j:
        releases[eid] = FrameRelease(eid, end_j=[0, 0, 0, 0, 0, 1])
    return tag + 1


def _n_stories(sched: dict) -> int:
    return len(sched["height"])


def _levels(sched: dict) -> list[float]:
    """Cumulative z of each node level (base .. roof)."""
    z = 0.0
    out = [0.0]
    for h in sched["height"]:
        z += h
        out.append(z)
    return out


def _sections_for(sched: dict) -> dict[str, ISection]:
    """Build the unique section objects a schedule references."""
    names: set[str] = set()
    for key in ("col_ext", "col_int", "col_gravity", "gravity_col"):
        v = sched.get(key)
        if isinstance(v, str):
            names.add(v)
        elif isinstance(v, list):
            names.update(v)
    for key in ("girder", "beam", "beam_gravity"):
        names.update(sched.get(key, []))
    return {n: _isection(n) for n in names}


def _col_sec(sched: dict, line: int, story: int) -> str:
    """Section name for a moment/gravity column line at a story."""
    role = sched["col_lines"][line]
    sec = sched["col_" + role]
    return sec[story] if isinstance(sec, list) else sec


def _gravity_beam_sec(sched: dict, level: int) -> str:
    """Girder section for the gravity bay at a level (note 2 for the 9-story)."""
    return sched.get("beam_gravity", sched["beam"])[level]


def _out_of_plane() -> list[int]:
    """Restraint for a free in-plane node of a planar X-Z frame (UY/RX/RZ fixed)."""
    return [0, 1, 0, 1, 0, 1]


def _lump_2d(masses, n_lines, n_ns, grid):
    """Distribute per-level masses across column lines by tributary bay width.

    ``masses[j]`` is the seismic mass of level ``j+1`` in the 2D frame's scope
    (half the building, for one perimeter frame).  Edge nodes get half a bay,
    interior nodes a full bay; no rotational mass.
    """
    added: dict[str, float] = {}
    for j, m in enumerate(masses, start=1):
        weights = [0.5 if i in (0, n_ns) else 1.0 for i in range(n_lines)]
        total = sum(weights)
        for i, w in enumerate(weights):
            added[grid[(i, j)]] = m * w / total
    return added


def _lump_3d(masses, n_ns_lines, n_ew_lines, grid):
    """Distribute per-level masses across the grid by tributary slab area.

    ``masses[j]`` is the full-building seismic mass of level ``j+1``.  Corner
    nodes a quarter panel, edge nodes a half panel, interior nodes a full panel.
    """
    added: dict[str, float] = {}
    n_ns = n_ns_lines - 1
    n_ew = n_ew_lines - 1
    for j, m in enumerate(masses, start=1):
        for i in range(n_ns_lines):
            for k in range(n_ew_lines):
                w = 1.0
                if i in (0, n_ns_lines - 1):
                    w *= 0.5
                if k in (0, n_ew_lines - 1):
                    w *= 0.5
                added[grid[(i, k, j)]] = m * w / (n_ns * n_ew)
    return added


def _gravity_2d(sched: dict, grid: dict) -> list[JointLoad]:
    """Nodal ``DEAD``/``LIVE`` loads for a planar frame (half-building tributary).

    ``fz`` is negative (downward, global -Z).  Pressures are the FEMA 355C
    floor-load distribution; the tributary split matches :func:`_lump_2d`.
    """
    n_ns, n_story = sched["n_ns"], _n_stories(sched)
    n_lines = n_ns + 1
    area = (n_ns * BAY) * (sched["n_ew"] * BAY / 2.0)  # frame tributary area (m²)
    weights = [0.5 if i in (0, n_ns) else 1.0 for i in range(n_lines)]
    total = sum(weights)
    loads: list[JointLoad] = []
    for j in range(1, n_story + 1):
        dead_p = DEAD_ROOF_PA if j == n_story else DEAD_FLOOR_PA
        for i, w in enumerate(weights):
            frac = w / total
            nid = grid[(i, j)]
            loads.append(JointLoad(pattern="DEAD", node_id=nid, fz=-dead_p * area * frac))
            loads.append(JointLoad(pattern="LIVE", node_id=nid, fz=-LIVE_PA * area * frac))
    return loads


def _gravity_3d(sched: dict, grid: dict) -> list[JointLoad]:
    """Nodal ``DEAD``/``LIVE`` loads for the full 3D building (whole footprint)."""
    n_ns, n_ew, n_story = sched["n_ns"], sched["n_ew"], _n_stories(sched)
    area = (n_ns * BAY) * (n_ew * BAY)  # full plan area (m²)
    n_ns_lines = n_ns + 1
    n_ew_lines = n_ew + 1
    loads: list[JointLoad] = []
    for j in range(1, n_story + 1):
        dead_p = DEAD_ROOF_PA if j == n_story else DEAD_FLOOR_PA
        for i in range(n_ns_lines):
            for k in range(n_ew_lines):
                w = 1.0
                if i in (0, n_ns_lines - 1):
                    w *= 0.5
                if k in (0, n_ew_lines - 1):
                    w *= 0.5
                frac = w / (n_ns * n_ew)
                nid = grid[(i, k, j)]
                loads.append(JointLoad(pattern="DEAD", node_id=nid, fz=-dead_p * area * frac))
                loads.append(JointLoad(pattern="LIVE", node_id=nid, fz=-LIVE_PA * area * frac))
    return loads


def _floor_panels_3d(sched: dict, grid: dict) -> dict[str, AreaElement]:
    """One loads-only floor panel per bay per level (3D), for diaphragm viewing.

    Panels are visualisation-only: they carry no section, so the AnalysisBuilder
    emits no shell element for them, but the viewer renders them as translucent
    floor surfaces.  Node ordering is counter-clockwise around each bay.
    """
    n_ns, n_ew, n_story = sched["n_ns"], sched["n_ew"], _n_stories(sched)
    panels: dict[str, AreaElement] = {}
    tag = 1
    for j in range(1, n_story + 1):
        for i in range(n_ns):
            for k in range(n_ew):
                aid = f"F{tag}"
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


def build_2d(
    sched: dict, *, fixed_base: bool, masses: Optional[list[float]] = None
) -> SAPModelData:
    """Build the planar N-S moment frame (X-Z plane, one frame)."""
    n_ns, n_story = sched["n_ns"], _n_stories(sched)
    levels = _levels(sched)
    n_lines = n_ns + 1

    nodes: dict[str, Node] = {}
    grid: dict[tuple[int, int], str] = {}
    tag = 1
    for i in range(n_lines):
        for j in range(n_story + 1):
            nid = str(tag)
            nodes[nid] = Node(node_id=nid, node_tag=tag, x=i * BAY, y=0.0, z=levels[j])
            grid[(i, j)] = nid
            tag += 1

    sections = _sections_for(sched)
    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    releases: dict[str, FrameRelease] = {}
    etag = 1

    # Columns.
    for i in range(n_lines):
        for j in range(n_story):
            etag = _add_col(
                frames, assigns, etag, grid[(i, j)], grid[(i, j + 1)], _col_sec(sched, i, j)
            )

    # Beams — the last bay is the gravity bay, released for major moment at its
    # J end (the gravity-column end) so the gravity column's inertia is passed to
    # the moment frame as axial load rather than moment.
    for j in range(1, n_story + 1):
        lvl = j - 1
        for i in range(n_ns):
            gravity_bay = i == n_ns - 1
            sec = _gravity_beam_sec(sched, lvl) if gravity_bay else sched["girder"][lvl]
            etag = _add_beam(
                frames,
                assigns,
                releases,
                etag,
                grid[(i, j)],
                grid[(i + 1, j)],
                sec,
                release_j=gravity_bay,
            )

    restraints: dict[str, Restraint] = {}
    for i in range(n_lines):
        for j in range(n_story + 1):
            nid = grid[(i, j)]
            if j == 0:
                restraints[nid] = Restraint(
                    [1, 1, 1, 1, 1, 1] if fixed_base else [1, 1, 1, 1, 0, 1]
                )
            elif not fixed_base and j == 1:
                restraints[nid] = Restraint([1, 1, 0, 1, 0, 1])  # ground lateral restraint
            else:
                restraints[nid] = Restraint(_out_of_plane())

    added_masses = _lump_2d(masses, n_lines, n_ns, grid) if masses else {}
    joint_loads = _gravity_2d(sched, grid)
    return _assemble(
        nodes, restraints, sections, frames, assigns, releases, added_masses, joint_loads
    )


def build_3d(
    sched: dict, *, fixed_base: bool, masses: Optional[list[float]] = None
) -> SAPModelData:
    """Build the full 3D building (perimeter N-S MRFs + interior gravity framing)."""
    n_ns, n_ew, n_story = sched["n_ns"], sched["n_ew"], _n_stories(sched)
    levels = _levels(sched)
    n_ns_lines, n_ew_lines = n_ns + 1, n_ew + 1

    nodes: dict[str, Node] = {}
    grid: dict[tuple[int, int, int], str] = {}
    tag = 1
    for i in range(n_ns_lines):
        for k in range(n_ew_lines):
            for j in range(n_story + 1):
                nid = str(tag)
                nodes[nid] = Node(node_id=nid, node_tag=tag, x=i * BAY, y=k * BAY, z=levels[j])
                grid[(i, k, j)] = nid
                tag += 1

    sections = _sections_for(sched)
    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    releases: dict[str, FrameRelease] = {}
    etag = 1

    def on_mrf_ew(k: int) -> bool:
        """True for the E-W perimeter lines carrying the N-S moment frames."""
        return k == 0 or k == n_ew_lines - 1

    # Columns — moment/gravity on the E-W perimeter, gravity elsewhere.
    for i in range(n_ns_lines):
        for k in range(n_ew_lines):
            for j in range(n_story):
                sec = _col_sec(sched, i, j) if on_mrf_ew(k) else sched["gravity_col"]
                etag = _add_col(frames, assigns, etag, grid[(i, k, j)], grid[(i, k, j + 1)], sec)

    # N-S beams (along X).
    for j in range(1, n_story + 1):
        lvl = j - 1
        for k in range(n_ew_lines):
            for i in range(n_ns):
                if on_mrf_ew(k):
                    gravity_bay = i == n_ns - 1
                    sec = _gravity_beam_sec(sched, lvl) if gravity_bay else sched["girder"][lvl]
                    etag = _add_beam(
                        frames,
                        assigns,
                        releases,
                        etag,
                        grid[(i, k, j)],
                        grid[(i + 1, k, j)],
                        sec,
                        release_j=gravity_bay,
                    )
                else:
                    etag = _add_beam(
                        frames,
                        assigns,
                        releases,
                        etag,
                        grid[(i, k, j)],
                        grid[(i + 1, k, j)],
                        sched["beam"][lvl],
                    )

    # E-W beams (along Y) — gravity framing throughout.
    for j in range(1, n_story + 1):
        lvl = j - 1
        for i in range(n_ns_lines):
            for k in range(n_ew):
                etag = _add_beam(
                    frames,
                    assigns,
                    releases,
                    etag,
                    grid[(i, k, j)],
                    grid[(i, k + 1, j)],
                    sched["beam"][lvl],
                )

    restraints: dict[str, Restraint] = {}
    base = [1, 1, 1, 1, 1, 1] if fixed_base else [1, 1, 1, 0, 0, 0]
    for i in range(n_ns_lines):
        for k in range(n_ew_lines):
            restraints[grid[(i, k, 0)]] = Restraint(base)
            if not fixed_base:
                restraints[grid[(i, k, 1)]] = Restraint([1, 1, 0, 0, 0, 0])

    added_masses = _lump_3d(masses, n_ns_lines, n_ew_lines, grid) if masses else {}
    joint_loads = _gravity_3d(sched, grid)
    area_elements = _floor_panels_3d(sched, grid)
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
    )


# ── Public API ─────────────────────────────────────────────────────────
def boston3_2d(masses: Optional[list[float]] = None) -> SAPModelData:
    """Boston 3-story planar N-S moment frame (``masses`` = per-level, half-building)."""
    if masses is None:
        masses = [m / 2.0 for m in MASS_3STORY]
    return build_2d(_S3, fixed_base=True, masses=masses)


def boston3_3d(masses: Optional[list[float]] = None) -> SAPModelData:
    """Boston 3-story full 3D building (``masses`` = per-level, full-building)."""
    if masses is None:
        masses = list(MASS_3STORY)
    return build_3d(_S3, fixed_base=True, masses=masses)


def boston9_2d(masses: Optional[list[float]] = None) -> SAPModelData:
    """Boston 9-story planar N-S moment frame (``masses`` = per-level, half-building)."""
    if masses is None:
        masses = [m / 2.0 for m in MASS_9STORY]
    return build_2d(_S9, fixed_base=False, masses=masses)


def boston9_3d(masses: Optional[list[float]] = None) -> SAPModelData:
    """Boston 9-story full 3D building (``masses`` = per-level, full-building)."""
    if masses is None:
        masses = list(MASS_9STORY)
    return build_3d(_S9, fixed_base=False, masses=masses)


def write_snapshots(dest: Optional[Path] = None) -> list[Path]:
    """Write the four model-codec JSON snapshots for the FEA-GUI viewer.

    Args:
        dest: Target directory (default ``docs/assets``).

    Returns:
        The list of written file paths.
    """
    dest = dest or (Path(__file__).resolve().parents[3] / "docs" / "assets")
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, builder in (
        ("sac_boston3_2d", boston3_2d),
        ("sac_boston3_3d", boston3_3d),
        ("sac_boston9_2d", boston9_2d),
        ("sac_boston9_3d", boston9_3d),
    ):
        path = dest / f"{name}.json"
        path.write_text(model_to_json(builder()), encoding="utf-8")
        written.append(path)
    return written


__all__ = [
    "BAY",
    "DEAD_FLOOR_PA",
    "DEAD_ROOF_PA",
    "H12",
    "H13",
    "H18",
    "LIVE_PA",
    "MASS_3STORY",
    "MASS_9STORY",
    "PSF_TO_PA",
    "boston3_2d",
    "boston3_3d",
    "boston9_2d",
    "boston9_3d",
    "build_2d",
    "build_3d",
    "write_snapshots",
]
