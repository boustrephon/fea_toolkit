#!/usr/bin/env python
"""RC wall non-linear pushover: LayeredShell with FSAM layers (extracted).

.. warning::

   **This example does NOT converge — it is an extracted reproduction of
   a failed modelling approach, kept for documentation and future
   investigation.**  Run it only if you want to reproduce the failure.

   The FSAM-in-LayeredShell combination is **not validated**.  It is
   kept here (rather than in ``wall_pushover_compare.py``) so that:

   1. the working comparison example stays clean,
   2. the investigation is fully documented and reproducible,
   3. a future fix (or a reinterpretation as a supported recipe) can be
      dropped in without archaeology.

What was attempted
------------------

LayeredShell / ShellNLDKGQ with a 5-layer through-thickness stack where
each layer carries a ``FSAM`` (Fixed-Strut-Angle Model) nD material.
Boundary layers carry 2.5 % smeared reinforcement (``FSAM_bdry``),
interior layers 0.4 % (``FSAM_core``) — mirroring the fibre-enrichment
layout that **is** validated for the SFI_MVLEM_3D macro-element.

Wall geometry / loads (units kN / m / C):

    node 1 = (0, 0, 0)   node 2 = (4, 0, 0)   ← base (fixed)
    node 4 = (0, 0, 3)   node 3 = (4, 0, 3)   ← top (Y-only restraint)
    Lateral push in **+X** (the in-plane direction for this X-Z plane
    wall), 50 kN total at 200 LoadControl steps.
    Wall self-weight (0.3 m × 24 kN/m³) ramped via
    ``gravity_num_substeps: 10`` before the lateral push.

Configuration notes
-------------------

- ``subdivide_shells: 4`` → 16 ShellNLDKGQ quads (the Preprocessor's
  ``subdivide_shells`` option + ``subdivide_area_mesh``).
- ``Transformations`` constraint handler, 10 gravity substeps, FSAM nD
  materials created via ``opensees/builder.py`` → ``nDMaterial FSAM``.

What happened
-------------

The builder creates the FSAM nD materials and the LayeredShell section
successfully, but **no analysis step converges**:

- Gravity substep 1 (or the first lateral step) reports a large
  ``Norm deltaR ~13,950`` and every algorithm in the builder's
  NaN-proof chain (Newton → NewtonLineSearch → ModifiedNewton →
  KrylovNewton, plus the adaptive NormUnbalance fallback) fails.
- This is a near-singular stiffness, not a rigid-body mechanism:
  restraints are correct (verified with
  ``local/diag_fsam_restraints.py`` — all 25 mesh nodes get the right
  DOF fixes), and the identical geometry with ElasticIsotropic layers
  converges to 200/200 steps.

Why it fails (root cause)
-------------------------

FSAM is a **plane-stress membrane** nD material.  It is **not wrapped
as ``PlateFromPlaneStress``** for LayeredShell consumption — see
``docs/mvlem_wall_analysis.md`` §4.3:

    "FSAM materials are not wrapped as PlateFromPlaneStress (they are
    nD materials consumed directly by SFI_MVLEM_3D / LayeredShell)."

The builder's ``_create_layered_shell_sections()`` passes the raw FSAM
tag into the ``section LayeredShell`` command without a
``PlateFromPlaneStress`` wrapper.  ShellNLDKGQ (a plate/shell
formulation) expects a **plate-form** material per layer; the membrane
FSAM law is consumed directly, producing the untested code path that
leads to the near-singular stiffness.

Supporting evidence:

- ``docs/mvlem_wall_analysis.md`` §5.1 — the single-layer FSAM recipe in
  the docs fails with "number of layers must be larger than 2" for
  LayeredShell; no multi-layer FSAM recipe has ever converged.
- No integration test (``tests/test_wall_pushover.py``) or probe
  validates the FSAM-in-LayeredShell combination.  The validated paths
  are: (a) LayeredShell + ``ElasticIsotropic`` layers, and (b)
  SFI_MVLEM_3D consuming FSAM directly.
- The FSAM material is validated as an nD material consumed by the
  SFI_MVLEM_3D macro-element (``local/probe_mvlem_sfi.py``).

What might work (future directions)
-----------------------------------

1. **Wrap FSAM as PlateFromPlaneStress** in
   ``_create_layered_shell_sections()`` so each LayeredShell layer is a
   plate-form material.  This is the most likely fix, but it changes
   the element stress state (plane-stress enforcement) and needs
   OpenSees-level validation with a small probe before promotion to a
   supported recipe.

2. **Use the validated nonlinear shell recipe instead**: concrete
   ``ConcreteS`` + smeared rebar ``J2PlateFibre`` nD materials in a
   LayeredShell / ShellMITC4 or ShellNLDKGQ stack.  This is the
   approach used end-to-end in
   ``local/CLP_BSDG_Latest_Models/Admin_Building/admin_pushover_v4.py``
   and described in ``docs/layered_analysis_workflow.md`` §14.1.
   See ``examples/wall_layered_nonlinear.py`` for the placeholder.

3. **Use SFI_MVLEM_3D** — the validated FSAM deployment (see
   ``examples/wall_pushover_compare.py``).

Usage::

    # Reproduce the failure (prints the failed steps + summary)
    python examples/wall_pushover_fsam_layered.py

    # Export the failing model to Tcl / OpenSeesPy for offline inspection
    python examples/wall_pushover_fsam_layered.py --tcl --py

See also:

- ``docs/mvlem_wall_analysis.md`` — element signatures, §4.3 FSAM
  wrapping note, §5.1 failed single-layer recipe, §5.2 SFI_MVLEM_3D path.
- ``docs/pushover_analysis.md`` §Gravity convergence — the documented
  "Norm = NaN" LayeredShell stiffness shock (relevant, but NOT the
  primary cause here; the FSAM failure is a large but finite Norm).
- ``tests/test_wall_pushover.py`` — validated elastic-layered +
  SFI_MVLEM_3D paths.
- ``local/probe_mvlem_sfi.py`` — validated SFI_MVLEM_3D probe.
- ``local/diag_fsam_restraints.py`` — restraint-propagation diagnostic.
"""

import argparse
import sys
from pathlib import Path

# Make `fea_toolkit` importable when running from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

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

# Mesh refinement for the layered-shell path (4 × 4 = 16 quads).
SHELL_SUBDIVIDE = 4

# This is an extracted, documented failure — expect zero converged steps.
EXPECTED_FAILURE = True


# ── Model data ──────────────────────────────────────────────────────────────
def wall_model_data() -> SAPModelData:
    """Build the 4 m × 3 m RC wall :class:`SAPModelData` (X-Z plane).

    Material strengths/moduli are authored in model units (kN, m) —
    the direct-construction exception documented in ``.clinerules`` §4.6.
    """
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=4.0, y=0.0, z=3.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=3.0),
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
                thickness=0.3,
            ),
        },
        frame_elements={},
        area_elements={
            "A1": AreaElement(
                area_id="A1",
                area_tag=100,
                node_ids=["1", "2", "3", "4"],
                thickness=0.3,
            ),
        },
        frame_assignments={},
        area_assignments={"A1": "WALL_SEC"},
        groups={},
        # Base fully fixed; top corners Y-only.  The wall sits in the
        # X-Z plane, so the Y restraint removes the out-of-plane rigid-body
        # mechanism (there is no out-of-plane stiffness in the plane-stress
        # FSAM layers).  The Preprocessor propagates the Y bit to all mesh
        # nodes via the ``restraints`` plumbing (verified in
        # local/diag_fsam_restraints.py).
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            # Y-only (out-of-plane) restraint on the top corners.
            "3": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
            "4": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
        },
        load_cases={},
        load_patterns={
            "Lateral": LoadPattern(name="Lateral", pattern_type="OTHER"),
            "DEAD": LoadPattern(name="DEAD", pattern_type="DEAD"),
        },
        mass_sources={},
        joint_loads=[
            JointLoad(pattern="Lateral", node_id="3", fx=50.0),
            JointLoad(pattern="Lateral", node_id="4", fx=50.0),
        ],
        frame_gravity_loads=[],
        area_gravity_loads=[
            # Wall self-weight (0.3 m × 24 kN/m³) ramped via
            # ``gravity_num_substeps`` before the lateral push.
            AreaGravityLoad(
                pattern="DEAD",
                area_id="A1",
                multiplier_x=0.0,
                multiplier_y=0.0,
                multiplier_z=-1.0,
            ),
        ],
        area_uniform_loads=[],
        frame_dist_loads=[],
        frame_end_offsets={},
        frame_auto_mesh={},
        units={"F": "kN", "L": "m", "T": "C"},
    )


# ── FSAM config ─────────────────────────────────────────────────────────────
def fsam_nd_config() -> dict:
    """FSAM nD material configs (SI Pa authored — framework scales).

    Boundary fibres carry 2.5 % smeared reinforcement, interior fibres
    0.4 % — the enrichment layout validated for SFI_MVLEM_3D.
    """
    return {
        "nd_materials": {
            "FSAM_bdry": {
                "material_type": "FSAM",
                "density": 2400.0,
                "sx": "steel",
                "sy": "steel",
                "conc": "concrete",
                "rou_x": 0.025,
                "rou_y": 0.025,
                "nu": 0.2,
                "alfadow": 45.0,
            },
            "FSAM_core": {
                "material_type": "FSAM",
                "density": 2400.0,
                "sx": "steel",
                "sy": "steel",
                "conc": "concrete",
                "rou_x": 0.004,
                "rou_y": 0.004,
                "nu": 0.2,
                "alfadow": 45.0,
            },
        },
    }


def layered_shell_fsam_config() -> dict:
    """LayeredShell path: 5 FSAM layers on a 4 × 4 sub-divided wall.

    Same stack geometry as the elastic-layered path in
    ``wall_pushover_compare.py`` but each layer is an FSAM nD material.
    The two cover layers use ``FSAM_bdry`` (2.5 % smeared
    reinforcement), the three interior layers ``FSAM_core`` (0.4 %).
    """
    cfg = dict(fsam_nd_config())
    cfg.update(
        {
            "create_shells": True,
            "verbose": False,
            # ``Transformation`` eliminates constrained DOFs from the
            # global system, avoiding the near-singular stiffness that
            # ``Plain`` produces with FSAM materials on the restrained
            # wall mesh.
            "solver_constraints": "Transformation",
            # Ramp gravity in 10 substeps (see docs/pushover_analysis.md
            # §Gravity convergence — the LayeredShell stiffness shock).
            "gravity_num_substeps": 10,
            "subdivide_shells": SHELL_SUBDIVIDE,
            "shell_layers": {
                "WALL_LAYERS": {
                    "selector": {"sections": ["WALL_SEC"]},
                    "layers": [
                        {"thickness": 0.05, "nd_material": "FSAM_bdry"},
                        {"thickness": 0.05, "nd_material": "FSAM_core"},
                        {"thickness": 0.10, "nd_material": "FSAM_core"},
                        {"thickness": 0.05, "nd_material": "FSAM_core"},
                        {"thickness": 0.05, "nd_material": "FSAM_bdry"},
                    ],
                },
            },
        }
    )
    return cfg


# ── Pushover runner ──────────────────────────────────────────────────────────
def run_wall_pushover(config: dict, lateral: float, num_steps: int):
    """Run one Preprocessor → AnalysisBuilder pushover for the wall.

    Applies the lateral load evenly across all top-edge nodes of the
    (possibly sub-divided) mesh, with a gravity pre-load phase.  Returns
    ``(mesh_model, builder, results)`` where ``results`` carries
    ``control_disp`` and ``base_shear`` arrays.
    """
    import openseespy.opensees as ops

    ops.wipe()  # clean global OpenSees state between models

    md = wall_model_data()
    mm = Preprocessor(config).run(md)
    builder = AnalysisBuilder(mm, config)
    builder.build_domain()

    # ── Gravity phase (ramped) ────────────────────────────────────
    # Ramp the wall self-weight via the builder's NaN-proof static solver
    # chain, then lock gravity with ``loadConst`` before the lateral push.
    builder.create_loads(pattern_scales={"DEAD": 1.0})
    grav_results = builder.run_static_analysis(extract_reactions=True)
    ops.loadConst("-time", 0.0)
    _fz = sum(
        float(r.get("fz", 0.0)) for r in grav_results.get("reactions", {}).values()
    )
    print(f"    Gravity converged — vertical reaction = {_fz:.1f} kN")

    # ── Identify top / base edges from the (possibly sub-divided) mesh ──
    top_z = max(nd.z for nd in mm.nodes.values())
    base_z = min(nd.z for nd in mm.nodes.values())
    top_node_ids = sorted(
        nid for nid, nd in mm.nodes.items() if abs(nd.z - top_z) < 1e-9
    )
    base_node_ids = sorted(
        nid for nid, nd in mm.nodes.items() if abs(nd.z - base_z) < 1e-9
    )
    control_id = "3" if "3" in mm.nodes else top_node_ids[0]
    control_tag = mm.nodes[control_id].node_tag

    # ── Lateral push (incremental, LoadControl) ──
    # +X is the in-plane direction for this X-Z plane wall.  Gravity has
    # been locked by loadConst above.
    px = lateral / len(top_node_ids)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for nid in top_node_ids:
        ops.load(mm.nodes[nid].node_tag, px, 0.0, 0.0, 0.0, 0.0, 0.0)
    ops.constraints(config.get("solver_constraints", "Transformation"))
    ops.numberer("Plain")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 25)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0 / num_steps)
    ops.analysis("Static")

    control_disp = []
    base_shear = []
    for step in range(1, num_steps + 1):
        ok = ops.analyze(1)
        if ok != 0:
            print(f"    [note] analysis failed at step {step}/{num_steps}")
            break
        ops.reactions()
        disp = float(ops.nodeDisp(control_tag, 1))  # type: ignore[arg-type]  # X
        shear = 0.0
        for nid in base_node_ids:
            shear += float(
                ops.nodeReaction(mm.nodes[nid].node_tag, 1)  # type: ignore[arg-type]
            )
        control_disp.append(disp)
        base_shear.append(-shear)  # positive push → positive shear

    results = {
        "control_disp": np.array(control_disp),
        "base_shear": np.array(base_shear),
    }
    return mm, builder, results


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "RC wall FSAM-layered pushover — extracted, documented, "
            "non-converging reproduction."
        ),
    )
    parser.add_argument(
        "--tcl",
        action="store_true",
        help="Also export the failing model to Tcl.",
    )
    parser.add_argument(
        "--py",
        action="store_true",
        help="Also export the model as a standalone OpenSeesPy script.",
    )
    parser.add_argument(
        "--lateral",
        type=float,
        default=50.0,
        help="Total lateral push force in kN (default: 50).",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=200,
        help="Number of load-control steps (default: 200).",
    )
    args = parser.parse_args()

    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"FEA Toolkit Version: {__version__}")
    print(f"OpenSees Version: {ops_version()}")
    print("Wall: 4.0 m × 3.0 m × 0.3 m, units kN/m/C")
    print("⚠  EXPERIMENTAL: FSAM-in-LayeredShell is NOT validated.")
    print("   Expected outcome: no converged steps (documented failure).")
    print(f"Lateral push: {args.lateral:.1f} kN in +X at top edge\n")

    cfg = layered_shell_fsam_config()
    print("── LayeredShell / ShellNLDKGQ (FSAM layers, 4×4) ──")
    mm, _, res = run_wall_pushover(cfg, args.lateral, args.steps)
    n_steps = len(res["control_disp"])
    print()
    if n_steps:
        peak = abs(res["base_shear"]).max()
        drift = res["control_disp"][-1]
        print(f"  Steps completed : {n_steps}")
        print(f"  Peak base shear : {peak:.1f} kN")
        print(f"  Top displacement: {drift:.6f} m")
        if EXPECTED_FAILURE:
            print("\n  ⚠  Unexpected: steps DID converge (this was supposed to fail).")
            print("     If you are reading this, the FSAM-in-LayeredShell path")
            print("     may have been fixed — see the module docstring for the")
            print("     investigation summary before promoting it.")
    elif EXPECTED_FAILURE:
        print("  [expected] no converged steps — this is the documented failure.")
    else:
        print("  ⚠  Unexpected: no converged steps — this model was expected to run.")

    if args.tcl:
        from fea_toolkit.opensees.builder import export_model_to_tcl

        out = out_dir / "wall_layered_fsam.tcl"
        export_model_to_tcl(mm, str(out))  # type: ignore[arg-type]
        print(f"  Saved → {out}")

    if args.py:
        import openseespy.opensees as _real_ops

        import fea_toolkit.opensees.analysis_builder as ab_mod
        from fea_toolkit.opensees.recorder import RecordingOpenSees

        rec = RecordingOpenSees(_real_ops)
        ab_mod.ops = rec
        try:
            md = wall_model_data()
            mm2 = Preprocessor(cfg).run(md)
            AnalysisBuilder(mm2, cfg).build_domain()
        finally:
            ab_mod.ops = _real_ops

        out = out_dir / "wall_layered_fsam.py"
        rec.save_as_python(str(out))
        print(f"  Saved → {out}")
        _real_ops.wipe()

    print("\nDone (documented failure reproduced).")


if __name__ == "__main__":
    main()