"""E-Defense 2011 full-scale 5-storey base-isolated steel frame (LRB-CLB / TPB).

Reproduces the NEES/E-Defense collaborative shake-table test building
(Ryan et al. 2012; Qin Dong et al. 2013; Dao & Ryan 2015): a 2x2-bay,
5-storey steel moment frame, 10 m (X) x 12 m (Y) in plan, 15.8 m tall, total
weight ``W = 5420 kN``.  Two isolation configurations are built: the LRB-CLB
system (four lead-rubber bearings under the edge columns + five cross-linear
bearings under the corners and centre) and the TPB system (nine identical
triple friction pendulum bearings, ``config="tpb"``).

Modelling follows Qin Dong et al. (2013) -- the superstructure is **linear
elastic**; the fixed-base fundamental period is ``0.70 s`` (analysis 0.69 s,
experiment 0.70 s).  The LRBs use the manufacturer bilinear model
(``K0 = 6.5 kN/mm``, ``KF = 0.65 kN/mm``, ``FU = 73 kN``), emitted as
``elastomericBearingPlasticity``; the CLBs are idealised as frictionless,
vertical-only ``Linear`` links (their ``mu = 0.0019`` friction is omitted — it
is two orders of magnitude below the LRB yield strength).

Units are ``kN, m, s`` (consistent mass unit = tonne), matching the paper's
``kN/mm`` isolator specs converted to ``kN/m``.

References:
- Ryan, K.L., Dao, N.D., Sato, E., Sasaki, T., Okazaki, T. (2012). *NEES/E-
  Defense Base-Isolation Tests: Effectiveness of Friction Pendulum and
  Lead-Rubber Bearings Systems.* WCEE 2012, paper 2054.
- Qin Dong, Okazaki, T., Midorikawa, M., Ryan, K.L., Sato, E., Sasaki, T.
  (2013). *Analysis of shake-table tests of a full-scale building isolated by
  lead-rubber bearings.* hdl.handle.net/2115/54236.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .._unit_scaling import g_from_units, scale_material_dict
from ..model.sap_data import (
    FrameElement,
    JointLoad,
    LinkElement,
    LinkProperty,
    MassSource,
    Material,
    Node,
    Restraint,
    RubberIsolatorParams,
    SAPModelData,
    Section,
    TriplePendulumParams,
)

if TYPE_CHECKING:
    # Type-checking only — importing this at runtime would pull in OpenSees
    # (analysis_builder imports ``openseespy.opensees``), which the model-only
    # recipe verbs must not do (see ``build_builder``'s lazy import below).
    from ..opensees.analysis_builder import AnalysisBuilder

__all__ = [
    "E_DEFENSE_UNITS",
    "EDefenseLayout",
    "build_builder",
    "build_model",
    "column_box_section",
    "layout",
    "read_kobe_record",
    "wide_flange_section",
]

# ============================================================================
# Units and geometry
# ============================================================================

E_DEFENSE_UNITS: dict[str, str] = {"F": "kN", "L": "m", "T": "C"}

#: Steel elastic modulus (Pa) -- 200 GPa, authored in SI and scaled to the
#: model's force/length units exactly once via :func:`scale_material_dict`
#: (so the OpenSees domain carries 2.0e8 kN/m2).
E_STEEL: float = 2.0e11
#: Shear modulus (Pa).
G_STEEL: float = E_STEEL / 2.6
#: Poisson's ratio (steel).
NU_STEEL: float = 0.3

#: Gravitational acceleration (m/s2) for the kN-m unit system, derived from
#: the model length unit rather than hardcoded (§4.6).
GRAVITY: float = g_from_units(E_DEFENSE_UNITS)

#: Bay widths (m): two 5 m bays in X, two 6 m bays in Y.
BAY_X: float = 5.0
BAY_Y: float = 6.0
N_LINES_X: int = 3  # 2 bays -> 3 column lines
N_LINES_Y: int = 3

#: Storey heights (m): first storey 3.835 m, then four 3.0 m -> 15.835 m total
#: (the paper's 15.8 m).  The base level (z = 0) carries the rigid base beams.
STOREY_HEIGHTS: tuple[float, ...] = (3.835, 3.0, 3.0, 3.0, 3.0)

#: Total seismic weight (kN).
TOTAL_WEIGHT: float = 5420.0
#: Extra roof mass (kN) -- the 56-tonne steel plate (penthouse + eccentricity).
#: It is lumped symmetrically over the roof column lines (see
#: :meth:`EDefenseLayout.cell_weight`), so this benchmark does not reproduce the
#: plate-induced torsional (eccentric-mass) response.
ROOF_EXTRA_WEIGHT: float = 560.0


# ============================================================================
# Section properties (computed from the published member schedule)
# ============================================================================


def _box_props(d: float, t: float) -> dict[str, float]:
    """Area / inertias / torsion of a square hollow section (RHS)."""
    di = d - 2.0 * t
    a = d * d - di * di
    i33 = (d**4 - di**4) / 12.0
    j = (d - t) ** 3 * t  # thin-wall St Venant torsion constant
    return {"A": a, "I33": i33, "I22": i33, "J": j}


def _wf_props(d: float, bf: float, tf: float, tw: float) -> dict[str, float]:
    """Area / inertias / torsion of a doubly-symmetric I / wide-flange section."""
    a = 2.0 * bf * tf + (d - 2.0 * tf) * tw
    i33 = bf * d**3 / 12.0 - (bf - tw) * (d - 2.0 * tf) ** 3 / 12.0
    i22 = 2.0 * tf * bf**3 / 12.0 + (d - 2.0 * tf) * tw**3 / 12.0
    j = (2.0 * bf * tf**3 + (d - 2.0 * tf) * tw**3) / 3.0
    return {"A": a, "I33": i33, "I22": i22, "J": j}


def column_box_section(name: str, depth: float = 0.35, t: float = 0.016) -> Section:
    """A 350 mm square hollow section (HSS) column, thickness *t* (m)."""
    p = _box_props(depth, t)
    return Section(
        name=name,
        shape="Box/Tube",
        material="Steel",
        A=p["A"],
        I33=p["I33"],
        I22=p["I22"],
        J=p["J"],
    )


def wide_flange_section(name: str, d: float, bf: float, tf: float, tw: float) -> Section:
    """An I / wide-flange section with the given dimensions (m)."""
    p = _wf_props(d, bf, tf, tw)
    return Section(
        name=name,
        shape="I/Wide Flange",
        material="Steel",
        A=p["A"],
        I33=p["I33"],
        I22=p["I22"],
        J=p["J"],
    )


# ============================================================================
# Layout
# ============================================================================


@dataclass(frozen=True)
class EDefenseLayout:
    """The 3x3 column grid and the isolator placement (WCEE 2012 Fig. 1b).

    ``(i, j)`` indexes the column line in X (0..2) and Y (0..2).  The four LRBs
    sit under the edge *mid* columns (N/S/E/W); the five CLBs under the four
    corners and the centre column.
    """

    n_storeys: int = 5
    n_lines_x: int = 3
    n_lines_y: int = 3
    bay_x: float = BAY_X
    bay_y: float = BAY_Y
    storey_heights: tuple[float, ...] = STOREY_HEIGHTS

    #: ``(i, j)`` column lines carrying an LRB (edge middles).
    lrb_cells: tuple[tuple[int, int], ...] = ((1, 0), (1, 2), (0, 1), (2, 1))
    #: ``(i, j)`` column lines carrying a CLB (corners + centre).
    clb_cells: tuple[tuple[int, int], ...] = ((0, 0), (0, 2), (2, 0), (2, 2), (1, 1))

    def levels(self) -> list[float]:
        """Absolute elevations of the 6 levels (base + 5 floors)."""
        zs = [0.0]
        for h in self.storey_heights:
            zs.append(zs[-1] + h)
        return zs

    def node_id(self, level: int, i: int, j: int) -> str:
        """Canonical node id ``f"{level}-{i}-{j}"``."""
        return f"{level}-{i}-{j}"

    def cell_weight(self, level: int) -> float:
        """Tributary seismic weight (kN) lumped at one column line of a floor.

        The roof's extra plate weight is added uniformly across the roof column
        lines, so the lumped centre of mass stays symmetric — the published
        plate offset (and its torsional response) is not reproduced here.
        """
        typical = (TOTAL_WEIGHT - ROOF_EXTRA_WEIGHT) / self.n_storeys
        w = typical
        if level == self.n_storeys:
            w += ROOF_EXTRA_WEIGHT
        return w / (self.n_lines_x * self.n_lines_y)

    def cell_mass(self, level: int) -> float:
        """Tributary seismic mass (tonnes) at one column line of a floor."""
        return self.cell_weight(level) / GRAVITY

    def isolator_kind(self, i: int, j: int) -> str:
        """``"lrb"`` / ``"clb"`` for the isolator under column line ``(i, j)``."""
        if (i, j) in self.lrb_cells:
            return "lrb"
        if (i, j) in self.clb_cells:
            return "clb"
        raise ValueError(f"column line {(i, j)!r} has no isolator")


layout: EDefenseLayout = EDefenseLayout()


# ============================================================================
# Isolator link properties
# ============================================================================

#: LRB bilinear parameters (Qin Dong 2013), kN/m and kN.
LRB_K0: float = 6.5e3  # initial stiffness (6.5 kN/mm)
LRB_KF: float = 0.65e3  # post-yield stiffness (0.65 kN/mm)
LRB_FY: float = 73.0  # yield force (kN)
LRB_KC: float = 1.5e6  # vertical compression stiffness (1500 kN/mm)
#: CLB friction coefficient (Ryan 2012) — documented for completeness; the CLB
#: is modelled as a frictionless, vertical-only Linear link (see
#: :func:`_clb_property`) — CLB friction is not simulated.
CLB_MU: float = 0.0019

#: TPB design parameters (Dao & Ryan 2015, CCEER 15-01 Table 3-8).  The triple
#: friction pendulum (TPB) configuration uses nine identical EPS bearings.
TPB_MU: tuple[float, float, float] = (0.02, 0.08, 0.08)  # friction coefficients
TPB_RADIUS: tuple[float, float, float] = (0.419, 3.848, 3.848)  # effective radii R - h (m)
TPB_UBAR: tuple[float, float, float] = (0.051, 0.514, 0.514)  # slider displacement limits (m)
TPB_KV: float = 5.3e6  # vertical compression stiffness (5.3 MN/mm -> kN/m)
TPB_KVT: float = 0.1  # vertical tension stiffness (100 N/m -> kN/m)
TPB_MIN_FV: float = 10.0  # minimum vertical force for friction activation (kN)
TPB_UY: float = 1.0e-3  # yield (sliding-start) displacement (m)


def _lrb_property() -> LinkProperty:
    """The LRB link property (``Rubber Isolator``), axial U1 = vertical."""
    ratio = LRB_KF / LRB_K0
    ke = [LRB_KC, LRB_KF, LRB_KF, 0.0, 0.0, 0.0]
    return LinkProperty(
        name="LRB",
        link_type="Rubber Isolator",
        ke=ke,
        nonlinear_params=RubberIsolatorParams(
            k=[0.0, LRB_K0, LRB_K0, 0.0, 0.0, 0.0],
            fy=[0.0, LRB_FY, LRB_FY, 0.0, 0.0, 0.0],
            ratio=ratio,
        ),
    )


def _clb_property() -> LinkProperty:
    """The CLB link property — a bidirectional roller (vertical support only).

    The paper models the cross-linear bearings as "bidirectional rollers" with
    negligible horizontal stiffness and very large vertical stiffness; their
    friction (``mu = 0.0019`` → ~0.9 kN under the 485 kN axial load) is two
    orders of magnitude below the LRB yield strength, so it is omitted here.
    A ``Linear`` link fixes the vertical (U1) DOF and releases the horizontal
    and rotational DOFs.
    """
    return LinkProperty(
        name="CLB",
        link_type="Linear",
        ke=[1.0e7, 0.0, 0.0, 0.0, 0.0, 0.0],
        fixed=[False, False, False, False, False, False],
    )


def _tpb_property() -> LinkProperty:
    """The triple pendulum bearing (TPB) link property (``Triple Friction Pendulum``).

    Nine identical EPS triple friction pendulum bearings (Dao & Ryan 2015,
    CCEER 15-01 Table 3-8): ``mu = (0.02, 0.08, 0.08)``, effective radii
    ``L = R - h = (0.419, 3.848, 3.848) m``, slider limits
    ``d = (0.051, 0.514, 0.514) m``.  The total displacement capacity is
    ``1.13 m`` at a base-shear coefficient of ``0.275`` (verified by the
    ``tests/test_e_defense_tpb.py`` backbone push).  The axial (``U1``) DOF
    carries the compression stiffness; the pendulum shear is modelled
    internally by the element.  For the *elastic* (linearised) representation
    the horizontal DOFs use the first-slope pendulum stiffness ``N / Leff1``
    (``Leff1 = 2·L1``), which gives the isolator's 1.84 s first-slope period.
    """
    w_bearing = TOTAL_WEIGHT / (N_LINES_X * N_LINES_Y)
    k1 = w_bearing / (2.0 * TPB_RADIUS[0])  # first-slope pendulum stiffness (kN/m)
    return LinkProperty(
        name="TPB",
        link_type="Triple Friction Pendulum",
        ke=[TPB_KV, k1, k1, 0.0, 0.0, 0.0],
        nonlinear_params=TriplePendulumParams(
            radius=list(TPB_RADIUS),
            ubar=list(TPB_UBAR),
            mu=list(TPB_MU),
            W=w_bearing,
            Uy=TPB_UY,
            Kvt=TPB_KVT,
            minFv=TPB_MIN_FV,
            tol=1.0e-8,
        ),
    )


# ============================================================================
# Model construction
# ============================================================================


def build_model(isolated: bool = True, config: str = "lrb-clb") -> SAPModelData:
    """Build the E-Defense 5-storey frame as a :class:`SAPModelData`.

    Args:
        isolated: ``True`` places isolators under the base; the base nodes are
            freed and supported by grounded isolator links.  ``False``
            restrains the base fully (the fixed-base anchor).
        config: ``"lrb-clb"`` (four LRBs under the edge mid-columns + five CLBs
            under the corners and centre) or ``"tpb"`` (nine identical triple
            friction pendulum bearings under every column).

    Returns:
        The built model (kN, m, s).  Seismic masses are lumped at the floor
        column lines; a ``DEAD`` pattern of matching vertical joint loads
        provides the gravity hand-off for the transient runner.
    """
    lv = layout
    levels = lv.levels()
    n_levels = len(levels)

    # -- Isolation configuration --
    # Only the two isolation configurations (plus ``isolated=False`` for the
    # fixed-base anchor) are accepted; an unknown ``config`` must not silently
    # fall through to the LRB-CLB branch.
    if config not in ("lrb-clb", "tpb"):
        raise ValueError(
            f"config={config!r} is not a recognised isolation configuration; "
            "expected 'lrb-clb' or 'tpb' (or pass isolated=False for the "
            "fixed-base anchor)"
        )

    # -- Nodes --
    nodes: dict[str, Node] = {}
    tag = 1
    for k in range(n_levels):
        for i in range(lv.n_lines_x):
            for j in range(lv.n_lines_y):
                nid = lv.node_id(k, i, j)
                nodes[nid] = Node(
                    node_id=nid,
                    node_tag=tag,
                    x=i * lv.bay_x,
                    y=j * lv.bay_y,
                    z=levels[k],
                )
                tag += 1

    # -- Sections --
    # Columns: HSS 350×350, thickness tapering 22→12 mm down the height
    # (Lignos 2012 Table 2; CCEER 15-01 §2.1.3).
    col_ts = (0.022, 0.019, 0.016, 0.012, 0.012)
    sections: dict[str, Section] = {}
    for s in range(lv.n_storeys):
        sections[f"COL{s}"] = column_box_section(f"COL{s}", depth=0.35, t=col_ts[s])
    # Girders: the primary beams have a rolled H-400×200×9×12 middle segment and
    # built-up BH-400×200×12×16/19/22 end segments (haunched).  The built-up end
    # segments dominate the frame lateral stiffness (they frame the joints), so a
    # single BH-400×200×12×19 section represents them (see the doc's note).
    girder = wide_flange_section("GIRDER", 0.4, 0.2, 0.019, 0.012)
    # Composite beam effect (Qin Dong 2013): flexural stiffness x2 for girders
    # with the concrete slab on both sides.
    girder.I33 *= 2.0
    sections["GIRDER"] = girder
    sections["SECONDARY"] = wide_flange_section("SECONDARY", 0.3, 0.15, 0.009, 0.006)
    sections["BASE_BEAM"] = wide_flange_section("BASE_BEAM", 0.9, 0.5, 0.028, 0.016)

    # -- Frame elements --
    frames: dict[str, FrameElement] = {}
    assigns: dict[str, str] = {}
    etag = 1
    for s in range(lv.n_storeys):  # columns, storey s from level s -> s+1
        for i in range(lv.n_lines_x):
            for j in range(lv.n_lines_y):
                eid = f"C{s}-{i}-{j}"
                frames[eid] = FrameElement(
                    elem_id=eid,
                    elem_tag=etag,
                    node_i=lv.node_id(s, i, j),
                    node_j=lv.node_id(s + 1, i, j),
                )
                assigns[eid] = f"COL{s}"
                etag += 1

    for k in range(1, n_levels):  # girders at level k (floors 1..5)
        for i in range(lv.n_lines_x - 1):  # X-direction beams
            for j in range(lv.n_lines_y):
                eid = f"BX{k}-{i}-{j}"
                frames[eid] = FrameElement(
                    elem_id=eid,
                    elem_tag=etag,
                    node_i=lv.node_id(k, i, j),
                    node_j=lv.node_id(k, i + 1, j),
                )
                assigns[eid] = "GIRDER"
                etag += 1
        for i in range(lv.n_lines_x):  # Y-direction beams
            for j in range(lv.n_lines_y - 1):
                eid = f"BY{k}-{i}-{j}"
                frames[eid] = FrameElement(
                    elem_id=eid,
                    elem_tag=etag,
                    node_i=lv.node_id(k, i, j),
                    node_j=lv.node_id(k, i, j + 1),
                )
                assigns[eid] = "GIRDER"
                etag += 1

    # Rigid base beams at level 0 (ties the 9 base nodes above the isolators).
    for i in range(lv.n_lines_x - 1):
        for j in range(lv.n_lines_y):
            eid = f"BBX-{i}-{j}"
            frames[eid] = FrameElement(
                elem_id=eid,
                elem_tag=etag,
                node_i=lv.node_id(0, i, j),
                node_j=lv.node_id(0, i + 1, j),
            )
            assigns[eid] = "BASE_BEAM"
            etag += 1
    for i in range(lv.n_lines_x):
        for j in range(lv.n_lines_y - 1):
            eid = f"BBY-{i}-{j}"
            frames[eid] = FrameElement(
                elem_id=eid,
                elem_tag=etag,
                node_i=lv.node_id(0, i, j),
                node_j=lv.node_id(0, i, j + 1),
            )
            assigns[eid] = "BASE_BEAM"
            etag += 1

    # -- Restraints (fixed base) or grounded isolator links --
    restraints: dict[str, Restraint] = {}
    link_connectivity: list[LinkElement] = []
    link_assignments: dict[str, str] = {}
    link_properties: dict[str, LinkProperty] = {}

    if isolated:
        if config == "tpb":
            link_properties["TPB"] = _tpb_property()
            for i in range(lv.n_lines_x):
                for j in range(lv.n_lines_y):
                    nid = lv.node_id(0, i, j)
                    lid = f"TPB_{i}-{j}"
                    link_connectivity.append(
                        LinkElement(link_id=lid, node_i=nid, node_j="", coord_dir1="+Z")
                    )
                    link_assignments[lid] = "TPB"
        else:  # "lrb-clb"
            link_properties["LRB"] = _lrb_property()
            link_properties["CLB"] = _clb_property()
            for i in range(lv.n_lines_x):
                for j in range(lv.n_lines_y):
                    kind = lv.isolator_kind(i, j)
                    nid = lv.node_id(0, i, j)
                    lid = f"{kind.upper()}_{i}-{j}"
                    link_connectivity.append(
                        LinkElement(link_id=lid, node_i=nid, node_j="", coord_dir1="+Z")
                    )
                    link_assignments[lid] = kind.upper()
    else:
        for i in range(lv.n_lines_x):
            for j in range(lv.n_lines_y):
                restraints[lv.node_id(0, i, j)] = Restraint([1, 1, 1, 1, 1, 1])

    # -- Seismic masses and DEAD gravity joint loads --
    added_masses: dict[str, float] = {}
    joint_loads: list[JointLoad] = []
    for k in range(1, n_levels):  # floors 1..5 only (mass at floor lines)
        m = lv.cell_mass(k)
        for i in range(lv.n_lines_x):
            for j in range(lv.n_lines_y):
                nid = lv.node_id(k, i, j)
                added_masses[nid] = m
                joint_loads.append(JointLoad(pattern="DEAD", node_id=nid, fz=-m * GRAVITY))

    # Steel elastic constants are authored in SI (Pa) and scaled to the
    # model's kN-m units exactly once here (§4.6 material convention).
    steel = scale_material_dict({"E": E_STEEL, "G": G_STEEL}, E_DEFENSE_UNITS)

    return SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials={
            "Steel": Material("Steel", "Steel", E_mod=steel["E"], G_mod=steel["G"], nu=NU_STEEL)
        },
        sections=sections,
        frame_elements=frames,
        area_elements={},
        frame_assignments=assigns,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        frame_releases={},
        added_masses=added_masses,
        mass_sources={"MSSSRC1": MassSource(name="MSSSRC1", masses=True)},
        joint_loads=joint_loads,
        link_connectivity=link_connectivity,
        link_assignments=link_assignments,
        link_properties=link_properties,
        units=dict(E_DEFENSE_UNITS),
    )


def build_builder(
    isolated: bool = True, link_model: str = "nonlinear", config: str = "lrb-clb"
) -> AnalysisBuilder:
    """Build the E-Defense model into a ready-to-run :class:`AnalysisBuilder`.

    Args:
        isolated: ``True`` for the isolated configuration; ``False`` for the
            fixed-base anchor.
        link_model: ``"nonlinear"`` (bearing elements) or ``"elastic"``
            (effective-stiffness links, for a linearised modal period).
        config: ``"lrb-clb"`` or ``"tpb"`` (see :func:`build_model`).

    Returns:
        An :class:`AnalysisBuilder` with :meth:`build_domain` already called.
    """
    # Lazy import: ``build_model`` (and the model-only recipe verbs) must not
    # require OpenSees; only the analysis entry point pulls it in.
    from ..opensees.analysis_builder import AnalysisBuilder
    from ..opensees.preprocessor import preprocess_model

    md = build_model(isolated=isolated, config=config)
    mesh = preprocess_model(md, {})
    b = AnalysisBuilder(mesh, {"link_model": link_model})
    b.build_domain()
    return b


def read_kobe_record() -> tuple[np.ndarray, float]:
    """Read the JMA Kobe NS record (``data/ground_motion/local/KOBE.csv``).

    Returns:
        ``(accel, dt)`` -- acceleration samples (m/s2) and the integration step
        (s).  This is a stand-in of comparable intensity: the LRB-CLB test
        itself used non-redistributable synthetic Vogtle / Diablo Canyon
        nuclear-site motions (Huang et al. 2009).
    """
    from pathlib import Path

    from ..io.ground_motion import read_time_history_csv

    root = Path(__file__).resolve().parent.parent.parent.parent
    path = root / "data" / "ground_motion" / "local" / "KOBE.csv"
    times, accel = read_time_history_csv(str(path))
    dt = float(times[1] - times[0])
    return np.asarray(accel, dtype=float), dt
