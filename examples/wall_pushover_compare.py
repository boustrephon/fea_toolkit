#!/usr/bin/env python
"""RC wall pushover: three modelling approaches compared over three heights.

Compares the **converging** nonlinear RC wall discretisations in
fea_toolkit on the same wall cross-section (4.0 m wide x 0.3 m thick),
at three aspect ratios, with a unified gravity-then-post-yield protocol.

Approaches (docs/mvlem_wall_analysis.md §7):

* **MVLEM_3D** — single macro-element (uniaxial concrete/steel fibres +
  horizontal ElasticPP shear spring).  Shear + flexure.
* **LayeredShell / ShellNLDKGQ** — 4x4 structured sub-division of 5
  elastic ``ElasticIsotropic`` layers (no rebar smearing, no cracking).
  Shear + flexure through the thickness; upper-bound stiffness.
* **Fiber beam-column** — N vertical ``forceBeamColumn`` strips (N = 8 and
  16) with 2-D fiber sections, tied to a rigid top diaphragm
  (``rigidDiaphragm`` + ``Transformation``).  *Flexure only*
  (Euler-Bernoulli; no shear mechanism) — probe-validated in August 2026
  (docs/mvlem_wall_analysis.md §6.2).

Heights: **H = 2, 4, 8 m**  ->  H/W = 0.5, 1.0, 2.0
  (shear-dominated / balanced / flexure-leaning).  This sweeps the VT
  balance: the MVLEM_3D shear-spring drift and the Euler flexural drift
  cross over near H/W ~ 2-3, so the three heights bracket the transition.

Unified protocol (same for every path):

1. **Gravity first** — constant axial pre-load of 20 % of axial capacity:

       P = 0.20 f_c A_g = 0.20 x 30{,}000 x (0.3 x 4) = 7,200 kN

   applied at the top of the wall (constant across all three heights).
2. **Lateral push** — displacement-controlled to **0.20 m** in 100 steps
   (``DisplacementControl(control, ux, 0.002)``, ``KrylovNewton``).
   Base shear is the sum of the X-reactions at the fixed base nodes.
   All paths converge trivially in the elastic range; post-yield
   behaviour (softening / snap-through) shows up in the overlaid
   capacity curves.

The 100 kN reference shear is used to interpolate a common-stiffness
metric: ``k@100kN = 100 kN / ux@100kN``.  The MVLEM_3D vs fiber
beam-column displacement gap at that shear quantifies the **shear
contribution fraction** of the MVLEM macro-element:

    shear share = (u_MVLEM - u_fiber) / u_MVLEM  at V = 100 kN

Pipeline notes:

* The **LayeredShell** path runs through the full
  ``AnalysisBuilder.run_pushover_analysis()`` (gravity via
  ``area_gravity_loads`` scaled to 7200 kN, lock gravity, mass-uniform
  lateral shape, adaptive solver chain).
* The **MVLEM_3D** path's wall source area is marked ``inactive`` by the
  Preprocessor (it becomes a ``WallElement``), so the pipeline has *no*
  gravity load carrier — the axial pre-load is injected as top-node
  ``ops.load`` in the gravity pattern (same manual approach as the
  validated integration test), then the gravity stage reuses
  ``run_static_analysis()`` (with its substep / algorithm-fallback
  machinery).  The lateral stage is a manual
  ``KrylovNewton + DisplacementControl`` loop.
* The **fiber beam-column** path is built directly at the
  ``openseespy`` level (no ``SAPModelData``/Preprocessor), mirroring
  ``local/probe_fiber_beam_wall.py``.

Output (all in ``examples/output/``, gitignored):

* ``wall_pushover_compare.png`` / ``.svg`` — one figure, three subplots
  (one per height), each overlaying the four capacity curves.
* ``wall_layered_elastic.tcl`` / ``.py`` and ``wall_mvlem_3d.tcl`` / ``.py``
  — optional Tcl / Python exports for the two pipeline paths
  (``--tcl`` / ``--py``).

Usage::

    python examples/wall_pushover_compare.py          # full comparison
    python examples/wall_pushover_compare.py --no-plot
    python examples/wall_pushover_compare.py --tcl --py

See also ``docs/mvlem_wall_analysis.md`` §8 (results and interpretation),
``tests/test_wall_pushover.py`` (integration tests for the paths), and
``local/probe_fiber_beam_wall.py`` (the fiber probe this example builds on).
"""

import argparse
import sys
from pathlib import Path

# Make `fea_toolkit` importable when running from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import openseespy.opensees as ops

from fea_toolkit import __version__, ops_version
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaGravityLoad,
    JointLoad,
    LoadPattern,
    Material,
    Node,
    Restraint,
    SAPModelData,
    ShellSection,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import Preprocessor

OUT_DIR = Path(__file__).parent / "output"

# ── Geometry / loads ─────────────────────────────────────────────────────────
W, TW = 4.0, 0.3          # wall width (m), thickness (m)
HEIGHTS = [2.0, 4.0, 8.0]  # H/W = 0.5, 1.0, 2.0
FC = 30.0e3               # kN/m² (30 MPa)
AXIAL_FACTOR = 0.20       # P = 20 % fc*Ag
P_AXIAL = AXIAL_FACTOR * FC * W * TW   # 7200 kN (constant across heights)
REF_LATERAL = 100.0       # kN — reference shear for stiffness interpolation
U_MAX = 0.20              # m — displacement-controlled push target
N_PUSH = 100              # push steps (du = 0.002 m)
SHELL_SUBDIVIDE = 4       # 4x4 structured sub-division for the layered path

# Fiber beam-column mesh refinement swept separately (user-selected):
# number of concrete fibres across the width (8 / 16).
N_FIB_X = [8, 16]


# ── Model data ──────────────────────────────────────────────────────────────
def wall_model_data(height: float = 4.0, axial_kN: float = 0.0) -> SAPModelData:
    """Build the shared RC wall :class:`SAPModelData` for one height.

    Material strengths/moduli are authored in model units (kN, m) — the
    direct-construction exception documented in ``.clinerules`` §4.6.

    When *axial_kN* > 0, an ``AreaGravityLoad`` on pattern ``"Gravity"``
    is added with a multiplier that makes the *total* vertical load equal
    *axial_kN* once the pattern is applied at scale 1.0.  (The multiplier
    equals ``-axial_kN / self_weight`` so the area self-weight is scaled
    to the target axial force.)

    Args:
        height: Wall height H (m).
        axial_kN: Target total vertical pre-load (kN); 0 disables.

    Returns:
        The ``SAPModelData`` for the wall at this height.
    """
    sw = TW * 24.0 * W * height  # wall self-weight (kN)
    mz = (-axial_kN / sw) if sw > 0 else 0.0
    agl = (
        [AreaGravityLoad(pattern="Gravity", area_id="A1", multiplier_z=mz)]
        if abs(mz) > 1e-12
        else []
    )
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=4.0, y=0.0, z=height),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=height),
        },
        materials={
            "concrete": Material(
                name="concrete",
                type="Concrete",
                E_mod=30.0e6,  # kN/m² (30 GPa)
                nu=0.2,
                Fc=30.0e3,  # kN/m² (30 MPa)
                unit_weight=24.0,  # kN/m³
            ),
            "steel": Material(
                name="steel",
                type="Rebar",
                E_mod=200.0e6,  # kN/m² (200 GPa)
                nu=0.3,
                Fy=420.0e3,  # kN/m² (420 MPa)
                unit_weight=78.5,  # kN/m³
            ),
        },
        sections={
            "WALL_SEC": ShellSection(
                name="WALL_SEC",
                shape="ShellSection",
                material="concrete",
                thickness=TW,
            ),
        },
        frame_elements={},
        area_elements={
            "A1": AreaElement(
                area_id="A1",
                area_tag=100,
                node_ids=["1", "2", "3", "4"],
                thickness=TW,
            ),
        },
        frame_assignments={},
        area_assignments={"A1": "WALL_SEC"},
        groups={},
        # Base fully fixed; top edge Y (out-of-plane) restrained to enforce
        # the X-Z plane orientation.
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "3": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
            "4": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
        },
        load_cases={},
        load_patterns={
            "Lateral": LoadPattern(name="Lateral", pattern_type="OTHER"),
            "Gravity": LoadPattern(name="Gravity", pattern_type="OTHER"),
        },
        mass_sources={},
        joint_loads=[
            JointLoad(pattern="Lateral", node_id="3", fx=50.0),
            JointLoad(pattern="Lateral", node_id="4", fx=50.0),
        ],
        frame_gravity_loads=[],
        area_gravity_loads=agl,
        area_uniform_loads=[],
        frame_dist_loads=[],
        frame_end_offsets={},
        frame_auto_mesh={},
        units={"F": "kN", "L": "m", "T": "C"},
    )


def mvlem_3d_model_data(height: float = 4.0, axial_kN: float = 0.0) -> SAPModelData:
    """Wall model data plus the MVLEM_3D shear-spring and dummy-steel materials.

    ``dummy`` is the tiny-E elastic interior steel (boundary fibres get the
    real ``steel``); ``shear`` carries an ``E_mod`` so the builder computes
    the ElasticPP shear-spring stiffness ``k = 0.1·G·A/h`` (same recipe as
    ``local/probe_mvlem_sfi.py`` and the integration tests).
    """
    md = wall_model_data(height=height, axial_kN=axial_kN)
    md.materials["dummy"] = Material(
        name="dummy",
        type="Rebar",
        E_mod=200.0e6,
        nu=0.3,
        Fy=420.0e3,
        unit_weight=0.0,
    )
    md.materials["shear"] = Material(
        name="shear",
        type="Concrete",
        E_mod=30.0e6,
        nu=0.2,
        Fc=30.0e3,
        unit_weight=0.0,
    )
    return md


# ── Configs ──────────────────────────────────────────────────────────────────
def base_config() -> dict:
    """Shared solver config for the unified gravity + post-yield protocol."""
    return {
        "create_shells": True,
        "verbose": False,
        # Transformation honours the fiber path's rigidDiaphragm MPCs and is
        # fine (no MPCs) for the pipeline paths.
        "solver_constraints": "Transformation",
        "gravity_num_substeps": 10,
        "solver_test_tol": 1.0e-4,
        "solver_test_max_iter": 100,
        "solver_algorithm": "Newton",
    }


def layered_elastic_config() -> dict:
    """LayeredShell path: 5 elastic layers on a 4x4 sub-divided wall.

    The wall is structured-subdivided to ``SHELL_SUBDIVIDE × SHELL_SUBDIVIDE``
    via the Preprocessor's ``subdivide_shells`` option.  The ``shell_layers``
    selector matches by **section** (``WALL_SEC``) so every sub-element
    inherits the layer stack.
    """
    cfg = base_config()
    cfg.update(
        {
            "nd_materials": {
                "core": {"material_type": "ElasticIsotropic", "E": 30.0e9, "nu": 0.2},
            },
            "subdivide_shells": SHELL_SUBDIVIDE,
            "shell_layers": {
                "WALL_LAYERS": {
                    "selector": {"sections": ["WALL_SEC"]},
                    "layers": [
                        {"thickness": 0.05, "nd_material": "core"},
                        {"thickness": 0.05, "nd_material": "core"},
                        {"thickness": 0.10, "nd_material": "core"},
                        {"thickness": 0.05, "nd_material": "core"},
                        {"thickness": 0.05, "nd_material": "core"},
                    ],
                },
            },
        }
    )
    return cfg


def mvlem_3d_config() -> dict:
    """MVLEM_3D path: 5 macro-fibres, uniaxial concrete/steel + shear spring.

    Boundary fibres (0 and 4) carry the real ``steel`` (Steel02), interior
    fibres the ``dummy`` (tiny-E Elastic); ``shear`` is the single horizontal
    ElasticPP spring (``k = 0.1·G·A/h`` computed by the builder).
    """
    cfg = base_config()
    cfg["element_strategies"] = {
        "wall": {
            "element_type": "MVLEM_3D",
            "material_type": "uniaxial",
            "n_fibers": 5,
            "CoR": 0.4,
            "concrete_material": "concrete",
            "steel_material": "steel",
            "dummy_material": "dummy",
            "shear_material": "shear",
            "density": 2400.0,
            "boundary_fibers": 1,
        },
    }
    return cfg


# ── Mesh helpers ─────────────────────────────────────────────────────────────
def _top_base_tags(mesh_model) -> tuple[list[int], list[int]]:
    """Return (top-most node tags, fully-fixed base node tags)."""
    top_z = max(nd.z for nd in mesh_model.nodes.values())
    base_z = min(nd.z for nd in mesh_model.nodes.values())
    top_tags = [
        nd.node_tag
        for nid, nd in mesh_model.nodes.items()
        if abs(nd.z - top_z) < 1e-9
    ]
    base_tags = [
        nd.node_tag
        for nid, nd in mesh_model.nodes.items()
        if abs(nd.z - base_z) < 1e-9
    ]
    return top_tags, base_tags


def _base_shear(base_tags) -> float:
    """Sum X-reactions (DOF 1) at the base nodes after ops.reactions()."""
    ops.reactions()
    s = 0.0
    for t in base_tags:
        try:
            rx = ops.nodeReaction(int(t), 1)
            if isinstance(rx, (list, tuple)):
                rx = rx[0] if rx else 0.0
            s += float(rx)
        except Exception:
            pass
    return s


# ── Pushover drivers ─────────────────────────────────────────────────────────
def _run_dc_lateral(control_tag, base_tags, disp_inc, num_steps, loads):
    """Apply unit lateral loads and run a KrylovNewton DC push.

    Assumes gravity has been locked (``ops.loadConst('-time', 0.0)``) and
    the analysis objects cleared.

    Args:
        control_tag: Displacement-control node tag.
        base_tags: Base node tags for reaction summing.
        disp_inc: Displacement increment per step (m).
        num_steps: Number of push steps.
        loads: ``[(tag, fx, fy, fz), ...]`` top loads (magnitude irrelevant
            under displacement control).

    Returns:
        ``(control_disp, base_shear, statuses)`` — arrays of length
        ``num_steps + 1`` with step 0 = zero lateral state.
    """
    ops.timeSeries("Linear", 9001)
    ops.pattern("Plain", 9001, 9001)
    for tag, fx, fy, fz in loads:
        ops.load(int(tag), fx, fy, fz, 0.0, 0.0, 0.0)

    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-4, 100)
    ops.algorithm("KrylovNewton")
    ops.integrator("DisplacementControl", int(control_tag), 1, disp_inc)
    ops.analysis("Static")

    ctrl = [0.0]
    shear = [0.0]
    status = [0]
    for _step in range(1, num_steps + 1):
        ok = ops.analyze(1)
        status.append(ok)
        try:
            cd = float(ops.nodeDisp(int(control_tag), 1))  # type: ignore[arg-type]
        except Exception:
            cd = 0.0 if status[-1] == 0 else ctrl[-1]
        ctrl.append(cd)
        # Base shear = -sum of X-reactions (structure's resistance, +X push).
        shear.append(-_base_shear(base_tags) if ok == 0 else shear[-1])
        if ok != 0:
            break
    return (
        np.array(ctrl, dtype=float),
        np.array(shear, dtype=float),
        np.array(status, dtype=int),
    )


def run_mvlem_pushover(config, height, axial_kN=P_AXIAL, max_disp=U_MAX, num_steps=N_PUSH):
    """MVLEM_3D path: Preprocessor → AnalysisBuilder with gravity + DC push.

    The MVLEM_3D wall source area is ``inactive`` (it becomes a
    ``WallElement``), so the pipeline has no gravity load carrier — the
    axial pre-load is injected as top-node ``ops.load`` inside the
    ``"Gravity"`` pattern, then the gravity stage reuses
    ``run_static_analysis()`` (substeps + algorithm fallback chain).
    """
    md = mvlem_3d_model_data(height=height, axial_kN=axial_kN)
    mm = Preprocessor(config).run(md)
    builder = AnalysisBuilder(mm, config)
    builder.build_domain()

    top_tags, base_tags = _top_base_tags(mm)
    control_tag = max(top_tags)

    # ── Gravity: manual axial injection + pipeline gravity solve ──
    ops.timeSeries("Linear", 1000)
    ops.pattern("Plain", 100, 1000)
    n_top = len(top_tags)
    for t in top_tags:
        ops.load(int(t), 0.0, 0.0, -axial_kN / n_top, 0.0, 0.0, 0.0)
    builder.run_static_analysis(extract_reactions=True)
    uz_g = None
    try:
        uz_g = float(ops.nodeDisp(int(control_tag), 3))  # type: ignore[arg-type]
    except Exception:
        pass

    ops.wipeAnalysis()
    ops.loadConst("-time", 0.0)

    # ── Lateral DC push (unit top loads; magnitude irrelevant) ──
    loads = [(t, 1.0 / n_top, 0.0, 0.0) for t in top_tags]
    ctrl, shear, status = _run_dc_lateral(
        control_tag, base_tags, max_disp / num_steps, num_steps, loads
    )
    results = {
        "control_disp": ctrl,
        "base_shear": shear,
        "status": status,
        "control_node": control_tag,
        "dof": 1,
        "uz_gravity": uz_g,
    }
    return mm, results


def run_layered_pipeline_pushover(
    config, height, axial_kN=P_AXIAL, max_disp=U_MAX, num_steps=N_PUSH
):
    """LayeredShell path: full ``run_pushover_analysis()`` pipeline.

    Gravity is carried by the ``AreaGravityLoad`` entry in the model data
    (scaled so the pattern totals *axial_kN*); ``run_pushover_analysis``
    applies gravity, locks it, applies a mass-uniform lateral shape and
    runs the displacement-controlled push with its adaptive solver chain.
    """
    md = wall_model_data(height=height, axial_kN=axial_kN)
    mm = Preprocessor(config).run(md)
    builder = AnalysisBuilder(mm, config)
    builder.build_domain()
    results = builder.run_pushover_analysis(
        gravity_patterns={"Gravity": 1.0},
        lateral_load_type="uniform",
        lateral_direction="X",
        control_node_tag=None,  # auto-select top-most unrestrained node
        max_disp=max_disp,
        num_steps=num_steps,
        print_progress=False,
    )
    return mm, results


# ── Fiber beam-column (ops-level; Euler reference path) ─────────────────────
def _make_materials():
    # Concrete01 (NOT ConcreteCM): in OpenSeesPy 3.8.0.0 ConcreteCM's initial
    # tangent is ~37x softer than the passed Ec (verified in
    # local/probe_fiber_gravity_check.py), which crushes the wall at the
    # 7200 kN pre-load.  Concrete01 fixes E0 = 2*fpc/epsc0 = 30e6 kPa exactly,
    # giving the intended Euler (flexure-only) reference stiffness.
    ops.uniaxialMaterial(
        "Concrete01", 1,
        -30.0e3, -0.002, -3.0e3, -0.02,
    )
    ops.uniaxialMaterial("Steel02", 2, 420.0e3, 200.0e6, 0.01)


def _build_fiber_wall(height, n_fibers_x):
    """Build a SINGLE full-width forceBeamColumn wall.

    One beam-column centred at x = W/2 with the *full* wall section
    (fiber patch over width W × thickness TW ⇒ I = TW·W³/12, the exact
    Euler cantilever).  *n_fibers_x* is the horizontal fiber-mesh
    refinement (8 / 16), NOT a strip count.

    Why not N parallel strips (the original probe layout)?  Independent
    cantilever strips of width sw = W/N each have I_i = TW·sw³/12, so
    ΣI = TW·W³/(12N²) → the assemblage softens as 1/N² and the rigid
    ``rigidDiaphragm`` top tie over-constrains rotations — the N-strip
    layout does NOT reproduce a flexure-only wall (verified numerically:
    k@100kN came out ~10⁴× the 3EI/H³ Euler value).  A single full-width
    section with in-plane top translation tie is the correct
    mechanical-equivalent flexure reference.

    Returns ``(base_nodes, top_nodes, master)`` — node tags ``10*k`` and
    ``20`` (master).
    """
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)  # type: ignore[arg-type]
    _make_materials()

    H_local = height
    NSEG = 3
    dz = H_local / NSEG
    base_nodes, top_nodes = [], []
    rho_web, rho_bound, b_edge, cover = 0.004, 0.025, 0.4, 0.03

    # Vertical elements: axis along +Z; vecxz=(0,1,0) -> local z=Y, y=-X.
    ops.geomTransf("Linear", 1, 0.0, 1.0, 0.0)

    # Node column at the wall centre (x = W/2).
    for k in range(NSEG + 1):
        z = k * dz
        tag = 10 * k
        ops.node(tag, W / 2.0, 0.0, z)
        if k == 0:
            ops.fix(tag, 1, 1, 1, 1, 1, 1)
        else:
            ops.fix(tag, 0, 1, 0, 0, 0, 0)
        if k == 0:
            base_nodes.append(tag)
        else:
            top_nodes.append(tag)  # top-most in this list after the loop

    sec_tag = 1000
    J = (1.0 / 3.0) * W * TW**3 * (1.0 - 0.63 * TW / W)
    ops.section("Fiber", sec_tag, "-GJ", (0.4 * 30.0e6) * J / (2.0 * 1.2))

    y_lo, y_hi = 0.0, W
    z_hi = TW / 2.0
    ops.patch("quad", 1, n_fibers_x, 4, y_lo, -z_hi, y_hi, -z_hi, y_hi, z_hi, y_lo, z_hi)

    zs = TW / 2.0 - cover
    for z in (-zs, zs):
        n_web = max(2, int(round(W / 0.5)))
        A_web = rho_web * W * TW / (2.0 * n_web)
        ops.layer("straight", 2, n_web, A_web, y_lo, z, y_hi, z)

    n_edge = 4
    A_edge = (rho_bound - rho_web) * b_edge * TW / (2.0 * n_edge)
    for z in (-zs, zs):
        ops.layer("straight", 2, n_edge, A_edge, y_lo, z, y_lo + b_edge, z)
        ops.layer("straight", 2, n_edge, A_edge, y_hi - b_edge, z, y_hi, z)

    for k in range(NSEG):
        int_tag = 2000 + k
        e_tag = 3000 + k
        ni = 10 * k
        nj = 10 * (k + 1)
        ops.beamIntegration("Lobatto", int_tag, sec_tag, 5)
        ops.element("forceBeamColumn", e_tag, ni, nj, 1, int_tag)

    # Control master at the top centre; in-plane translation tie only
    # (equalDOF Ux), leaving the beam's own rotations free.  Tag 500 avoids
    # colliding with the structural node column (10*k → 0, 10, 20, ...).
    master = 500
    ops.node(master, W / 2.0, 0.0, H_local)
    ops.fix(master, 0, 1, 0, 1, 0, 1)
    ops.equalDOF(master, top_nodes[-1], 1)
    return base_nodes, top_nodes, master


def run_fiber_pushover(height, n_strips, axial_kN=500.0, ref_force=REF_LATERAL, steps=10):
    """Fiber beam-column path at ``openseespy`` level — force-controlled.

    Gravity: **500 kN** at the diaphragm master — the pre-load validated in
    ``local/probe_fiber_beam_wall.py`` (not the 7200 kN used by the pipeline
    paths).  Lateral: *force-controlled* ramp to the 100 kN reference shear
    (pattern 9001, LoadControl, KrylovNewton).

    Why not 7200 kN here?  At 0.20·fc·Ag the extreme compression fibre is
    pre-loaded to ~0.2·fc and the first lateral force drives it hard past
    the Concrete01 peak in this OpenSeesPy 3.8.0.0 build — the
    ``forceBeamColumn`` state-determination fails on the very first tiny
    lateral step (verified: step 1 @ V=11 kN fails).  The fiber path serves
    only as the **flexure-only stiffness reference** (Euler, no shear
    mechanism); its 100 kN elastic k is gravity-independent to first order.
    The displacement-controlled push to 0.2 m does not converge for the
    squat (H=2 m) wall even at 500 kN (first 0.002 m increment loads the
    short wall ~2000 kN at once), so the force-controlled ramp to the
    100 kN reference remains the validated protocol.

    Returns ``(results, master, base_nodes)`` with the usual
    ``control_disp`` / ``base_shear`` / ``status`` keys.
    """
    base_nodes, top_nodes, master = _build_fiber_wall(height, n_strips)

    # ── Gravity ──
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(master, 0.0, 0.0, -axial_kN, 0.0, 0.0, 0.0)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.1)
    ops.analysis("Static")
    rc_g = ops.analyze(10)
    uz_g = float(ops.nodeDisp(master, 3)) if rc_g == 0 else None  # type: ignore[arg-type]

    ops.wipeAnalysis()
    ops.loadConst("-time", 0.0)

    # Reference ux after gravity lock: the 7200 kN pre-load tilts the top
    # diaphragm (P−Δ rigid-body rotation couple through the MPC), giving a
    # load-independent ux offset of order -40 µm.  We report the
    # *incremental* lateral displacement from this state, isolating the
    # flexural drift that the +X push produces.
    try:
        ux_0 = float(ops.nodeDisp(master, 1))  # type: ignore[arg-type]
    except Exception:
        ux_0 = 0.0

    # ── Lateral force-controlled push to the 100 kN reference ──
    ops.timeSeries("Linear", 9001)
    ops.pattern("Plain", 9001, 9001)
    ops.load(master, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-4, 100)
    ops.algorithm("KrylovNewton")
    ops.integrator("LoadControl", 1.0 / steps)
    ops.analysis("Static")

    ctrl, status = [0.0], [0]
    for _ in range(steps):
        ok = ops.analyze(1)
        status.append(ok)
        try:
            cd = float(ops.nodeDisp(master, 1)) - ux_0  # type: ignore[arg-type]
        except Exception:
            cd = 0.0 if ok == 0 else ctrl[-1]
        ctrl.append(cd)
        if ok != 0:
            break

    # Ideal 0 → ref_force ramp over the achieved load steps (the load is
    # force-controlled, so base_shear is the applied reference force).
    n = len(ctrl)
    results = {
        "control_disp": np.array(ctrl, dtype=float),
        "base_shear": np.array([ref_force * i / (steps - 1) for i in range(n)], dtype=float),
        "status": np.array(status, dtype=int),
        "control_node": master,
        "dof": 1,
        "uz_gravity": uz_g,
        "gravity_rc": rc_g,
    }
    return results, master, base_nodes


# ── Metrics ──────────────────────────────────────────────────────────────────
def stiffness_at_100kn(results) -> dict:
    """Interpolate ux at V = 100 kN, k@100kN, and final-state metrics.

    Handles both NumPy arrays (manual push drivers) and plain Python
    lists (``AnalysisBuilder.run_pushover_analysis``).
    """
    disp = np.asarray(results["control_disp"], dtype=float)
    shear = np.asarray(results["base_shear"], dtype=float)
    n = disp.size
    out = {
        "steps_ok": n - 1,
        "ux_100": None,
        "k_100": None,
        "ux_max": disp[-1] if n else None,
        "shear_max": np.abs(shear).max() if n else None,
    }
    if n == 0:
        return out
    # Interpolate the displacement where the (rising) shear crosses 100 kN.
    idx = None
    for i in range(1, n):
        if shear[i - 1] <= REF_LATERAL <= shear[i] or shear[i - 1] >= REF_LATERAL >= shear[i]:
            idx = i
            break
    if idx is not None:
        s0, s1 = shear[idx - 1], shear[idx]
        d0, d1 = disp[idx - 1], disp[idx]
        if abs(s1 - s0) > 1e-12:
            ux = d0 + (REF_LATERAL - s0) / (s1 - s0) * (d1 - d0)
            out["ux_100"] = ux
            out["k_100"] = REF_LATERAL / ux
    return out


# ── Reporting helpers ───────────────────────────────────────────────────────
def _report_case(label: str, height: float, res: dict) -> None:
    """Print one pushover case line (steps, axial drift, 100 kN metrics)."""
    m = stiffness_at_100kn(res)
    f_uz = res.get("uz_gravity")
    uz_s = f"  uz_g={float(f_uz):.4e} m" if f_uz is not None else ""
    ux_s = f"{m['ux_100']:.6f}" if m["ux_100"] is not None else "   n/a   "
    k_s = f"{m['k_100']:.0f}" if m["k_100"] is not None else "  n/a "
    print(
        f"  {label:>22s}  H={height:g}m  steps={m['steps_ok']:3d}{uz_s}  "
        f"ux@100kN={ux_s} m  k@100kN={k_s} kN/m  "
        f"ux_max={res['control_disp'][-1]:.4f} m  "
        f"V_max={(m['shear_max'] if m['shear_max'] is not None else float('nan')):7.1f} kN"
    )


def _print_height_table(height: float, per_h, meta: dict) -> None:
    """Print a per-height metric table across all approaches."""
    rows = [
        ("Steps (of %d)" % N_PUSH, lambda m: f"{m['steps_ok']:3d}"),
        (
            "ux @ 100 kN (m)",
            lambda m: f"{m['ux_100']:.6f}" if m["ux_100"] is not None else "   n/a   ",
        ),
        (
            "k @ 100 kN (kN/m)",
            lambda m: f"{m['k_100']:.0f}" if m["k_100"] is not None else "  n/a ",
        ),
        (
            "ux_max (m)",
            lambda m: f"{m['ux_max']:.4f}" if m["ux_max"] is not None else "  n/a ",
        ),
        (
            "V_max (kN)",
            lambda m: f"{m['shear_max']:.1f}" if m["shear_max"] is not None else "  n/a",
        ),
    ]
    labels = [lbl for lbl, _ in per_h]
    col_w = 17
    print(f"  ── summary @ H = {height:g} m ──")
    print("  " + f"{'Metric':>{20}s}" + "".join(f"{lbl[:col_w]:>{col_w}s}" for lbl in labels))
    for name, fn in rows:
        vals = [fn(meta[f"{lbl}@{height:g}"]) for lbl, _ in per_h]
        print("  " + f"{name:>{20}s}" + "".join(f"{v:>{col_w}s}" for v in vals))


def shear_share(meta: dict) -> None:
    """Estimate the shear-contribution fraction vs the flexure-only reference.

    At V = 100 kN: ``share = (u_approach - u_fiber16) / u_approach``.
    The fiber beam-column has no shear mechanism, so any *extra* drift in
    the MVLEM_3D path is attributed to its horizontal shear spring.  The
    LayeredShell path is an elastic upper-bound (stiffer, *less* drift), so
    its share is negative — reported for completeness only.
    """
    lay_lbl = f"LayeredShell ({SHELL_SUBDIVIDE}×{SHELL_SUBDIVIDE})"
    f16_key = "Fiber N=16"
    has_fiber = any(f"{f16_key}@{h:g}" in meta for h in HEIGHTS)
    if not has_fiber:
        return
    print("── Shear contribution vs Fiber N=16 (flexure-only) @ V=100 kN ──")
    print(
        f"  {'H (m)':>6s}  {'H/W':>5s}  {'MVLEM_3D':>12s}  {'Layered':>12s}  {'Fiber N=8':>12s}"
    )
    for h in HEIGHTS:
        u_m = meta[f"MVLEM_3D@{h:g}"]["ux_100"]
        u_l = meta[f"{lay_lbl}@{h:g}"]["ux_100"]
        u_f8 = meta[f"Fiber N=8@{h:g}"]["ux_100"]
        u_f16 = meta[f"Fiber N=16@{h:g}"]["ux_100"]
        sh_m = (u_m - u_f16) / u_m * 100.0 if (u_m and u_f16 and abs(u_m) > 1e-12) else float("nan")
        sh_l = (u_l - u_f16) / u_l * 100.0 if (u_l and u_f16 and abs(u_l) > 1e-12) else float("nan")
        sh_g = (u_f8 - u_f16) / u_f8 * 100.0 if (u_f8 and u_f16 and abs(u_f8) > 1e-12) else float("nan")
        print(
            f"  {h:6.1f}  {h / W:5.2f}  {sh_m:11.1f}%  {sh_l:11.1f}%  {sh_g:11.1f}%"
        )
    print("  (positive share = extra drift vs the flexure-only reference;")


# ── Plotting ────────────────────────────────────────────────────────────────
def plot_height_overlay(cases: dict, out_dir: Path) -> None:
    """One figure, one subplot per height, overlaying every approach.

    Args:
        cases: ``{height: [(label, results_dict), ...]}`` in plot order.
        out_dir: Output directory for ``wall_pushover_compare.png/.svg``.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  Note: matplotlib not available — skipping plots.")
        return

    heights = list(cases.keys())
    n = len(heights)
    fig, axes = plt.subplots(
        1, n, figsize=(6.2 * n, 5.2), sharey=True, squeeze=False
    )
    for ax, h in zip(axes[0], heights):
        for label, res in cases[h]:
            ax.plot(res["control_disp"], res["base_shear"], label=label, lw=2)
        ax.axhline(REF_LATERAL, color="0.6", ls="--", lw=1)
        ax.text(
            0.02,
            REF_LATERAL * 1.03,
            f"V = {REF_LATERAL:.0f} kN",
            fontsize=8,
            color="0.4",
            transform=ax.get_yaxis_transform(),
        )
        ax.set_xlabel("Top displacement X (m)")
        ax.set_title(f"H = {h:g} m  (H/W = {h / W:.2g})", fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
    axes[0][0].set_ylabel("Base shear (kN)")
    fig.suptitle(
        f"RC wall pushover — 3 approaches × 3 heights "
        f"(constant axial P = {P_AXIAL:.0f} kN)",
        y=0.98,
        fontsize=12,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_dir / "wall_pushover_compare.png", dpi=300)
    fig.savefig(out_dir / "wall_pushover_compare.svg")
    print(f"  Saved → {out_dir / 'wall_pushover_compare.png'}  (300 dpi)")
    print(f"  Saved → {out_dir / 'wall_pushover_compare.svg'}")
    plt.close(fig)


# ── Tcl export ───────────────────────────────────────────────────────────────
def export_tcl(mesh_model, label: str, out_dir: Path) -> None:
    """Export the built model to Tcl for external inspection."""
    from fea_toolkit.opensees.builder import export_model_to_tcl

    out = out_dir / f"wall_{label}.tcl"
    export_model_to_tcl(mesh_model, str(out))  # type: ignore[arg-type]
    print(f"  Saved → {out}")


# ── Python script export ─────────────────────────────────────────────────────
def export_py(config: dict, label: str, out_dir: Path, height: float) -> None:
    """Export the model to a standalone OpenSeesPy script.

    Rebuilds the domain with the module-level ``ops`` binding swapped for
    a :class:`~fea_toolkit.opensees.recorder.RecordingOpenSees` proxy,
    then saves the captured commands as ``build_model()`` in a runnable
    ``.py`` file.
    """
    import openseespy.opensees as _real_ops

    import fea_toolkit.opensees.analysis_builder as ab_mod
    from fea_toolkit.opensees.recorder import RecordingOpenSees

    rec = RecordingOpenSees(_real_ops)
    ab_mod.ops = rec
    try:
        if label.startswith("mvlem"):
            md = mvlem_3d_model_data(height=height)
        else:
            md = wall_model_data(height=height)
        mm = Preprocessor(config).run(md)
        builder = AnalysisBuilder(mm, config)
        builder.build_domain()
    finally:
        ab_mod.ops = _real_ops

    out = out_dir / f"wall_{label}.py"
    rec.save_as_python(str(out))
    print(f"  Saved → {out}")
    _real_ops.wipe()


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="RC wall non-linear pushover: 3 approaches × 3 heights.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip plotting (console summary only).",
    )
    parser.add_argument(
        "--tcl",
        action="store_true",
        help="Also export the models to Tcl.",
    )
    parser.add_argument(
        "--py",
        action="store_true",
        help="Also export the models as standalone OpenSeesPy scripts.",
    )
    parser.add_argument(
        "--fiber",
        action="store_true",
        help=(
            "Also run the fiber beam-column flexure-only reference.  "
            "OPT-IN: probe-only (docs/mvlem_wall_analysis.md §6.2); "
            "forceBeamColumn state-determination is fragile for the full "
            "4 m section in OpenSeesPy 3.8.0.0, so each case is wrapped in "
            "try/except and failures are reported but do not abort."
        ),
    )
    args = parser.parse_args()

    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"FEA Toolkit Version: {__version__}")
    print(f"OpenSees Version: {ops_version()}")
    print(f"Wall: {W:g} m wide × {TW:g} m thick, units kN/m/C")
    print(f"Gravity: constant axial P = {P_AXIAL:.0f} kN (20 % fc·Ag) at top")
    print(f"Lateral: displacement-controlled to {U_MAX:g} m "
          f"({N_PUSH} steps, KrylovNewton)")
    print(f"LayeredShell: {SHELL_SUBDIVIDE}×{SHELL_SUBDIVIDE} sub-division")
    print(f"Fiber beam-column: fibres across width = {N_FIB_X}\n")

    approach_labels = [
        ("MVLEM_3D", mvlem_3d_config()),
        (f"LayeredShell ({SHELL_SUBDIVIDE}×{SHELL_SUBDIVIDE})", layered_elastic_config()),
    ]

    curves: dict[float, list[tuple[str, dict]]] = {}
    meta: dict[str, dict] = {}
    mesh_models: dict[str, object] = {}
    for h in HEIGHTS:
        print(f"════════ H = {h:g} m  (H/W = {h / W:.2g}) ════════")
        per_h: list[tuple[str, dict]] = []

        # Pipeline paths (MVLEM_3D + LayeredShell).
        for label, cfg in approach_labels:
            if label.startswith("MVLEM"):
                mm, res = run_mvlem_pushover(cfg, h)
                mesh_models[f"mvlem_3d@{h:g}"] = mm
            else:
                mm, res = run_layered_pipeline_pushover(cfg, h)
                mesh_models[f"layered@{h:g}"] = mm
            _report_case(label, h, res)
            per_h.append((label, res))
            meta[f"{label}@{h:g}"] = stiffness_at_100kn(res)

        # Fiber beam-column (ops-level Euler reference path) — OPT-IN.
        if args.fiber:
            for n in N_FIB_X:
                label = f"Fiber N={n}"
                try:
                    res, _master, _bases = run_fiber_pushover(h, n)
                except Exception as exc:  # state-determination fragility
                    print(f"  [fiber] {label}@H={h:g}m FAILED: {exc}")
                    continue
                _report_case(label, h, res)
                per_h.append((label, res))
                meta[f"{label}@{h:g}"] = stiffness_at_100kn(res)

        curves[h] = per_h
        _print_height_table(h, per_h, meta)
        print()

    # Shear-share estimation vs Fiber N=16 at 100 kN.
    shear_share(meta)

    # ── Plots ──────────────────────────────────────────────────────────
    if not args.no_plot:
        print("\n── Capacity curves ──")
        plot_height_overlay(curves, out_dir)

    # ── Tcl export (optional) ──────────────────────────────────────────
    if args.tcl:
        print("\n── Tcl export ──")
        for key, mm in mesh_models.items():
            label = key.split("@")[0]
            export_tcl(mm, label, out_dir)

    # ── Python script export (optional) ─────────────────────────────────
    if args.py:
        print("\n── Python script export ──")
        for label, cfg in approach_labels:
            export_py(cfg, label.split(" ")[0], out_dir, height=HEIGHTS[1])

    print("\nDone.")


if __name__ == "__main__":

    main()
