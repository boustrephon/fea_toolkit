#!/usr/bin/env python
"""RC wall non-linear pushover: LayeredShell vs SFI_MVLEM_3D comparison.

Demonstrates the two supported non-linear RC wall discretisations in
fea_toolkit through the full Preprocessor → AnalysisBuilder pipeline on
the **same wall geometry**:

* **LayeredShell / ShellNLDKGQ** — through-thickness FSAM layer stack
  on a nonlinear quad (mesh-refinement dependent).
* **SFI_MVLEM_3D** — a single macro-element with ``m`` macro-fibres,
  each fibre carrying its own FSAM nD material (shear-flexure coupling
  via the fixed-strut-angle model).

Both paths share one 4.0 m × 3.0 m × 0.3 m RC wall (units kN / m / C):

    node 1 = (0, 0, 0)   node 2 = (0, 4, 0)   ← base (fixed)
    node 4 = (0, 0, 3)   node 3 = (0, 4, 3)   ← top
    Lateral push in +X at the two top nodes (nodes 3 and 4).

Material strengths/moduli are authored in **model units** (kN, m) — the
direct-construction exception in ``.clinerules`` §4.6:

    Concrete:  30 MPa = 30e3 kN/m²,  E = 30 GPa = 30e6 kN/m²
    Rebar:    420 MPa = 4.2e5 kN/m², E = 200 GPa = 2e8 kN/m²

Configuration mirrors ``tests/test_wall_pushover.py``:

* FSAM boundary fibres carry 2.5 % smeared reinforcement, interior
  fibres 0.4 % — a uniform-0.4 % wall is nearly singular under pure
  lateral push.
* ``Plain`` constraint handler + 10 LoadControl substeps match the
  validated SFI_MVLEM_3D probe settings.

Produces (all in ``examples/output/``, gitignored):

* ``wall_pushover_compare.png`` / ``.svg`` — overlaid capacity curves
  (base shear vs top drift).
* ``wall_layered.tcl`` / ``wall_sfi_mvlem.tcl`` — Tcl exports of both
  models (with ``--tcl``).

Usage::

    # Run both paths, plot the overlaid capacity curves
    python examples/wall_pushover_compare.py

    # Also export Tcl models for both approaches
    python examples/wall_pushover_compare.py --tcl

    # Console only (no matplotlib required)
    python examples/wall_pushover_compare.py --no-plot

.. note::

   A quantitative agreement between the two approaches is NOT expected:
   the LayeredShell path here stacks 100 % elastic layers (no rebar
   smearing) and is far stiffer than the SFI_MVLEM_3D macro-element,
   whose FSAM concrete softens severely under monotonic lateral push.
   Both models must carry the same 100 kN base shear and drift in the
   push direction — the convergent physics — which is the basis for
   comparison.

See also ``docs/mvlem_wall_analysis.md`` (element signatures,
limitations, config recipes) and ``tests/test_wall_pushover.py``
(integration tests for the two paths).
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


# ── Model data ──────────────────────────────────────────────────────────────
def wall_model_data() -> SAPModelData:
    """Build the shared 4 m × 3 m RC wall :class:`SAPModelData`.

    Material strengths/moduli are authored in model units (kN, m) —
    the direct-construction exception documented in ``.clinerules`` §4.6.
    """
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=0.0, y=4.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=0.0, y=4.0, z=3.0),
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
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
        },
        load_cases={},
        load_patterns={
            "Lateral": LoadPattern(name="Lateral", pattern_type="OTHER"),
        },
        mass_sources={},
        joint_loads=[
            JointLoad(pattern="Lateral", node_id="3", fx=50.0),
            JointLoad(pattern="Lateral", node_id="4", fx=50.0),
        ],
        frame_gravity_loads=[],
        area_gravity_loads=[],
        area_uniform_loads=[],
        frame_dist_loads=[],
        frame_end_offsets={},
        frame_auto_mesh={},
        units={"F": "kN", "L": "m", "T": "C"},
    )


# ── Configs ──────────────────────────────────────────────────────────────────
def fsam_nd_config() -> dict:
    """FSAM nD material configs (SI Pa authored — framework scales).

    Two FSAM variants mirror the validated probe layout
    (``local/probe_mvlem_sfi.py``): boundary fibres carry 2.5 % smeared
    reinforcement, interior fibres 0.4 %.
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


def base_config() -> dict:
    """Shared solver config for the standalone 3D macro-element wall.

    ``Plain`` constraint handler + 10 LoadControl substeps match the
    SFI_MVLEM_3D probe; Transformation + single-step fails for a
    free-standing 3D macro-element wall.
    """
    cfg = dict(fsam_nd_config())
    cfg.update(
        {
            "create_shells": True,
            "verbose": False,
            "solver_constraints": "Plain",
            "gravity_num_substeps": 10,
        }
    )
    return cfg


def layered_shell_config() -> dict:
    """LayeredShell path: FSAM + 5-layer elastic core stack on the wall."""
    cfg = base_config()
    cfg.update(
        {
            # Extra nD material for the through-thickness layers (SI Pa).
            "nd_materials": dict(
                cfg["nd_materials"],
                core={"material_type": "ElasticIsotropic", "E": 30.0e9, "nu": 0.2},
            ),
            "shell_layers": {
                "WALL_LAYERS": {
                    "selector": {"element_ids": ["A1"], "sections": ["WALL_SEC"]},
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


def sfi_mvlem_config() -> dict:
    """SFI_MVLEM_3D path: 5 macro-fibres, CoR 0.4.

    Fibre materials: boundary fibres (0 and 4) use ``FSAM_bdry`` (2.5 %
    smeared reinforcement), interior fibres (1-3) use ``FSAM_core``
    (0.4 %) — matching the converged probe layout.
    """
    cfg = base_config()
    cfg["element_strategies"] = {
        "wall": {
            "element_type": "SFI_MVLEM_3D",
            "n_fibers": 5,
            "CoR": 0.4,
            "fsam_materials": [
                "FSAM_bdry",  # fibre 0 (left boundary)
                "FSAM_core",
                "FSAM_core",
                "FSAM_core",
                "FSAM_bdry",  # fibre 4 (right boundary)
            ],
        },
    }
    return cfg


# ── Pushover runner ──────────────────────────────────────────────────────────
def run_wall_pushover(config: dict, lateral: float, num_steps: int):
    """Run one Preprocessor → AnalysisBuilder pushover for the wall.

    Applies the lateral load (split 50/50 onto the two top nodes) via a
    dedicated plain pattern — ``_create_loads`` records ``JointLoad``
    pattern membership but does not emit ``ops.load`` for joint loads.

    Returns ``(mesh_model, builder, results)`` where ``results`` carries
    ``control_disp`` and ``base_shear`` arrays for plotting.
    """
    import openseespy.opensees as ops

    md = wall_model_data()
    mm = Preprocessor(config).run(md)
    builder = AnalysisBuilder(mm, config)
    builder.build_domain()

    # Per-node lateral load (half each at the two top nodes).
    px = lateral / 2.0

    # ── Lateral push (incremental, LoadControl) ──
    # No gravity phase: the wall has no frame/area gravity loads.
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(3, px, 0.0, 0.0, 0.0, 0.0, 0.0)
    ops.load(4, px, 0.0, 0.0, 0.0, 0.0, 0.0)
    ops.constraints("Plain")
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
        # Compute nodal reactions (nodeReaction returns the last computed
        # reaction; ops.reactions() forces the computation).
        ops.reactions()
        # OpenSeesPy returns a scalar for single-DOF queries; the .pyi
        # stub declares `float | Tuple[float, ...]`, so narrow via float().
        disp = float(ops.nodeDisp(3, 1))  # type: ignore[arg-type]  # top node 3, X
        # Base shear = sum of X-reactions at the two fixed base nodes
        # (DOF 1 = X translation).
        rxn1 = float(ops.nodeReaction(1, 1))  # type: ignore[arg-type]
        rxn2 = float(ops.nodeReaction(2, 1))  # type: ignore[arg-type]
        shear = rxn1 + rxn2
        control_disp.append(disp)
        base_shear.append(-shear)  # positive push → positive shear

    results = {
        "control_disp": np.array(control_disp),
        "base_shear": np.array(base_shear),
    }
    return mm, builder, results


# ── Plotting ─────────────────────────────────────────────────────────────────
def plot_overlay(res_layered: dict, res_sfi: dict, out_dir: Path) -> None:
    """Plot overlaid capacity curves and save PNG + SVG."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  Note: matplotlib not available — skipping plots.")
        return

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(
        res_layered["control_disp"],
        res_layered["base_shear"],
        label="LayeredShell / ShellNLDKGQ (elastic layers)",
        lw=2,
    )
    ax.plot(
        res_sfi["control_disp"],
        res_sfi["base_shear"],
        label="SFI_MVLEM_3D (FSAM macro-element)",
        lw=2,
    )
    ax.set_xlabel("Top displacement (m)")
    ax.set_ylabel("Base shear (kN)")
    ax.set_title("RC wall pushover — LayeredShell vs SFI_MVLEM_3D")
    ax.grid(True, alpha=0.3)
    ax.legend()

    out_dir.mkdir(parents=True, exist_ok=True)
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


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="RC wall non-linear pushover: LayeredShell vs SFI_MVLEM_3D.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip plotting (console summary only).",
    )
    parser.add_argument(
        "--tcl",
        action="store_true",
        help="Also export both models to Tcl.",
    )
    parser.add_argument(
        "--lateral",
        type=float,
        default=100.0,
        help="Total lateral push force in kN (default: 100).",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=50,
        help="Number of load-control steps (default: 50).",
    )
    args = parser.parse_args()

    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"FEA Toolkit Version: {__version__}")
    print(f"OpenSees Version: {ops_version()}")
    print("Wall: 4.0 m × 3.0 m × 0.3 m, units kN/m/C")
    print(f"Lateral push: {args.lateral:.1f} kN in +X at top nodes\n")

    # ── LayeredShell / ShellNLDKGQ ────────────────────────────────────────────
    print("── LayeredShell / ShellNLDKGQ (through-thickness layers) ──")
    mm_l, _, res_l = run_wall_pushover(layered_shell_config(), args.lateral, args.steps)
    n_steps_l = len(res_l["control_disp"])
    if n_steps_l:
        peak_l = abs(res_l["base_shear"]).max()
        drift_l = res_l["control_disp"][-1]
        print(f"  Steps completed : {n_steps_l}")
        print(f"  Peak base shear : {peak_l:.1f} kN")
        print(f"  Top displacement: {drift_l:.6f} m")
    else:
        print("  [warning] analysis produced no converged steps")

    # ── SFI_MVLEM_3D ──────────────────────────────────────────────────────────
    print("\n── SFI_MVLEM_3D (FSAM macro-element) ──")
    mm_s, _, res_s = run_wall_pushover(sfi_mvlem_config(), args.lateral, args.steps)
    n_steps_s = len(res_s["control_disp"])
    if n_steps_s:
        peak_s = abs(res_s["base_shear"]).max()
        drift_s = res_s["control_disp"][-1]
        print(f"  Steps completed : {n_steps_s}")
        print(f"  Peak base shear : {peak_s:.1f} kN")
        print(f"  Top displacement: {drift_s:.6f} m")
    else:
        print("  [warning] analysis produced no converged steps")

    # ── Comparison table ───────────────────────────────────────────────────────
    print("\n── Comparison ─────────────────────────────────────────────")
    if n_steps_l and n_steps_s:
        wall_h = 3.0
        print(f"  {'':28s} {'LayeredShell':>14s} {'SFI_MVLEM_3D':>14s}")
        print(f"  {'Peak base shear (kN)':28s} {peak_l:14.1f} {peak_s:14.1f}")
        print(f"  {'Top drift (m)':28s} {drift_l:14.4f} {drift_s:14.4f}")
        print(
            f"  {'Top drift ratio':28s} {drift_l / wall_h * 100:13.2f}% {drift_s / wall_h * 100:13.2f}%"
        )
    print("  " + "-" * 58)

    # ── Plots ──────────────────────────────────────────────────────────────────
    if not args.no_plot and n_steps_l and n_steps_s:
        print("\n── Capacity curves ──")
        plot_overlay(res_l, res_s, out_dir)

    # ── Tcl export (optional) ──────────────────────────────────────────────────
    if args.tcl:
        print("\n── Tcl export ──")
        export_tcl(mm_l, "layered", out_dir)
        export_tcl(mm_s, "sfi_mvlem", out_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
