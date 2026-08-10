#!/usr/bin/env python
"""RC wall non-linear pushover: two validated modelling approaches compared.

Demonstrates the validated non-linear RC wall discretisations in
fea_toolkit through the full Preprocessor → AnalysisBuilder pipeline on
the **same wall geometry**:

* **LayeredShell / ShellNLDKGQ — elastic layers** — a through-thickness
  stack of 5 ``ElasticIsotropic`` layers (no rebar smearing, no
  cracking).  Linear elastic at every step → upper-bound stiffness.
  Run on a **4 × 4 structured sub-division** of the wall (16
  ShellNLDKGQ elements with shared nodes, via the Preprocessor's
  ``subdivide_shells`` option and
  :func:`~fea_toolkit.model.geometry.subdivide_area_mesh`).
  ShellNLDKGQ is mesh-refinement dependent — a single quad
  under-predicts bending.
* **SFI_MVLEM_3D** — a single macro-element (1 × 1) with ``m``
  macro-fibres, each fibre carrying its own FSAM nD material
  (Fixed-Strut-Angle Model — shear-flexure coupling through a fixed
  45° strut angle).  The macro-fibres sub-divide the wall width
  internally.

Both paths share one 4.0 m × 3.0 m × 0.3 m RC wall (units kN / m / C):

    node 1 = (0, 0, 0)   node 2 = (4, 0, 0)   ← base (fully fixed)
    node 4 = (0, 0, 3)   node 3 = (4, 0, 3)   ← top (free)
    Lateral push in **+X** (the in-plane direction for this X-Z plane
    wall) at the top edge, distributed evenly across all top-edge
    nodes.  Base shear is the sum of the X-reactions at the fixed
    base nodes.

The configuration mirrors the validated integration tests in
``tests/test_wall_pushover.py`` and the OpenSees probe
``local/probe_mvlem_sfi.py``:

* base fully fixed, top edge **free** (no out-of-plane restraint needed
  for these two element formulations — SFI_MVLEM_3D must sit in the
  X-Z plane with the push along X, its in-plane fibre direction),
* ``Plain`` constraint handler,
* **no gravity pre-load** — the lateral push starts from the
  zero-stress state.

``LayeredShell`` with FSAM layers is intentionally **not** included
here: FSAM is a plane-stress membrane nD material that is *not* wrapped
as ``PlateFromPlaneStress`` for LayeredShell consumption (see
``docs/mvlem_wall_analysis.md`` §4.3), and the combination fails to
converge.  See ``examples/wall_pushover_fsam_layered.py`` for an
extracted reproduction with a full write-up of the investigation.

Material strengths/moduli are authored in **model units** (kN, m) — the
direct-construction exception in ``.clinerules`` §4.6:

    Concrete:  30 MPa = 30e3 kN/m²,  E = 30 GPa = 30e6 kN/m²
    Rebar:    420 MPa = 4.2e5 kN/m², E = 200 GPa = 2e8 kN/m²

Produces (all in ``examples/output/``, gitignored):

* ``wall_pushover_compare.png`` / ``.svg`` — overlaid capacity curves
  (base shear vs top drift) for both models.
* ``wall_layered_elastic.tcl`` / ``wall_sfi_mvlem.tcl`` — Tcl exports
  (with ``--tcl``).
* ``wall_layered_elastic.py`` / ``wall_sfi_mvlem.py`` — standalone
  OpenSeesPy scripts (with ``--py``), captured via
  :class:`~fea_toolkit.opensees.recorder.RecordingOpenSees`.

Usage::

    # Run both models, plot the overlaid capacity curves
    python examples/wall_pushover_compare.py

    # Also export Tcl + Python model scripts
    python examples/wall_pushover_compare.py --tcl --py

    # Console only (no matplotlib required)
    python examples/wall_pushover_compare.py --no-plot

.. note::

   **Material-model stiffness mismatch (expected).**  The elastic-layer
   shell stays at E = 30 GPa for every step, while the SFI_MVLEM_3D
   macro-element departs from the initial tangent modulus from the first
   load increment — the ConcreteCM envelope starts softening
   immediately.  So the two paths are not expected to share an identical
   initial elastic stiffness, and the SFI_MVLEM_3D is the more flexible
   of the two.

   **Mesh-refinement effect.**  Sub-dividing the shell path to 4 × 4
   lets ShellNLDKGQ capture bending; the SFI_MVLEM_3D stays a single
   macro-element whose ``m`` macro-fibres sub-divide the wall width
   internally.

See also ``docs/mvlem_wall_analysis.md`` (element signatures,
limitations, config recipes), ``tests/test_wall_pushover.py``
(integration tests for the paths), and
``examples/wall_pushover_fsam_layered.py`` (documented FSAM-layered
failure investigation).
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

# Mesh refinement for the layered-shell path (4 × 4 = 16 quads).
SHELL_SUBDIVIDE = 4


# ── Model data ──────────────────────────────────────────────────────────────
def wall_model_data() -> SAPModelData:
    """Build the shared 4 m × 3 m RC wall :class:`SAPModelData`.

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
        # Base fully fixed; top edge free, with Y (out-of-plane)
        # restraint on the top nodes to enforce the X-Z plane
        # orientation (matches the canonical probe layout in
        # ``local/probe_mvlem_sfi.py``).
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "3": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
            "4": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
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
    """FSAM nD material configs.

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
    """Shared solver config for the two wall paths.

    ``Plain`` constraint handler matches the validated probe
    (``local/probe_mvlem_sfi.py``) and the integration tests
    (``tests/test_wall_pushover.py``).  No gravity pre-load is applied.
    """
    cfg = dict(fsam_nd_config())
    cfg.update(
        {
            "create_shells": True,
            "verbose": False,
            "solver_constraints": "Plain",
        }
    )
    return cfg


def layered_elastic_config() -> dict:
    """LayeredShell path: 5 elastic layers on a 4 × 4 sub-divided wall.

    The wall is structured-subdivided to ``SHELL_SUBDIVIDE × SHELL_SUBDIVIDE``
    via the Preprocessor's ``subdivide_shells`` option.  The ``shell_layers``
    selector matches by **section** (``WALL_SEC``) so every sub-element
    inherits the layer stack.
    """
    cfg = base_config()
    cfg.update(
        {
            # Through-thickness layer material.  Authored in SI (Pa) and
            # scaled to model units by the framework, exactly as in
            # ``tests/test_wall_pushover.py``.
            "nd_materials": dict(
                cfg["nd_materials"],
                core={"material_type": "ElasticIsotropic", "E": 30.0e9, "nu": 0.2},
            ),
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


def sfi_mvlem_config() -> dict:
    """SFI_MVLEM_3D path: 5 macro-fibres, CoR 0.4, no shell sub-division.

    Fibre materials: boundary fibres (0 and 4) use ``FSAM_bdry`` (2.5 %
    smeared reinforcement), interior fibres (1-3) use ``FSAM_core``
    (0.4 %) — matching the converged probe layout.  The wall remains a
    single 1 × 1 macro-element; the ``m`` macro-fibres sub-divide the wall
    width internally.
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

    Applies the lateral load **evenly across all top-edge nodes** of the
    (possibly sub-divided) mesh — for the single-quad 1 × 1 mesh this is
    exactly the historical 50/50 split on the two corner nodes; for the
    4 × 4 mesh the five top-edge nodes each get ``lateral / 5``.  Base
    shear is summed from the **all base-edge nodes**' X-reactions.

    The load is applied via a dedicated plain pattern —
    ``_create_loads`` records ``JointLoad`` pattern membership but does
    not emit ``ops.load`` for joint loads.

    Returns ``(mesh_model, builder, results)`` where ``results`` carries
    ``control_disp`` and ``base_shear`` arrays for plotting.
    """
    import openseespy.opensees as ops

    ops.wipe()  # clean global OpenSees state between models

    md = wall_model_data()
    mm = Preprocessor(config).run(md)
    builder = AnalysisBuilder(mm, config)
    builder.build_domain()

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
    # The push is +X (the SFI_MVLEM_3D in-plane direction — the wall
    # must sit in the X-Z plane) at the top edge, spread evenly across
    # all top-edge nodes.  Plain constraint handler + Newton match the
    # validated probe / integration-test solver settings.
    push_dir = {'X':1, 'Y':2, 'Z':3}['X']
    force_vector = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    fx_val = lateral / len(top_node_ids)
    force_vector[push_dir - 1] = fx_val
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for nid in top_node_ids:
        ops.load(mm.nodes[nid].node_tag, *force_vector)
    ops.constraints(config.get("solver_constraints", "Plain"))
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
        disp = float(ops.nodeDisp(control_tag, push_dir))  # type: ignore[arg-type]  # X
        # Base shear = sum of X-reactions at all fixed base nodes (DOF 1).
        shear = 0.0
        for nid in base_node_ids:
            shear += float(
                ops.nodeReaction(mm.nodes[nid].node_tag, push_dir)  # type: ignore[arg-type]
            )
        control_disp.append(disp)
        base_shear.append(-shear)  # positive push → positive shear

    results = {
        "control_disp": np.array(control_disp),
        "base_shear": np.array(base_shear),
    }
    return mm, builder, results


# ── Plotting ─────────────────────────────────────────────────────────────────
def plot_overlay(curves: list[tuple[str, dict]], out_dir: Path) -> None:
    """Plot overlaid capacity curves for all models and save PNG + SVG.

    Args:
        curves: ``[(label, results_dict), ...]`` in plot order.
        out_dir: Output directory for ``wall_pushover_compare.png/.svg``.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  Note: matplotlib not available — skipping plots.")
        return

    fig, ax = plt.subplots(figsize=(9, 6))
    for label, res in curves:
        ax.plot(
            res["control_disp"],
            res["base_shear"],
            label=label,
            lw=2,
        )
    ax.set_xlabel("Top displacement X (m)")
    ax.set_ylabel("Base shear (kN)")
    ax.set_title(
        f"RC wall pushover — {len(curves)} modelling approaches "
        f"(shell path on {SHELL_SUBDIVIDE}×{SHELL_SUBDIVIDE} mesh, "
        "SFI_MVLEM_3D on 1×1)"
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)

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


# ── Python script export ─────────────────────────────────────────────────────
def export_py(config: dict, label: str, out_dir: Path) -> None:
    """Export the model to a standalone OpenSeesPy script.

    Rebuilds the domain with the module-level ``ops`` binding swapped for
    a :class:`~fea_toolkit.opensees.recorder.RecordingOpenSees` proxy,
    then saves the captured commands as ``build_model()`` in a runnable
    ``.py`` file.  The generated script imports ``openseespy.opensees``
    and replays every recorded ``ops.*`` call.
    """
    import openseespy.opensees as _real_ops

    import fea_toolkit.opensees.analysis_builder as ab_mod
    from fea_toolkit.opensees.recorder import RecordingOpenSees

    rec = RecordingOpenSees(_real_ops)
    ab_mod.ops = rec
    try:
        md = wall_model_data()
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
        description="RC wall non-linear pushover: two modelling approaches.",
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
    print(f"Shell path: {SHELL_SUBDIVIDE}×{SHELL_SUBDIVIDE} sub-divided mesh")
    print(f"Lateral push: {args.lateral:.1f} kN in +X at top edge\n")

    models = [
        (
            "layered_elastic",
            "LayeredShell / ShellNLDKGQ (elastic layers, 4×4)",
            layered_elastic_config(),
        ),
        (
            "sfi_mvlem",
            "SFI_MVLEM_3D (FSAM macro-element, 1×1)",
            sfi_mvlem_config(),
        ),
    ]

    results = {}
    mesh_models = {}
    for key, label, cfg in models:
        print(f"── {label} ──")
        mm, _, res = run_wall_pushover(cfg, args.lateral, args.steps)
        mesh_models[key] = mm
        results[key] = res
        n_steps = len(res["control_disp"])
        if n_steps:
            peak = abs(res["base_shear"]).max()
            drift = res["control_disp"][-1]
            print(f"  Steps completed : {n_steps}")
            print(f"  Peak base shear : {peak:.1f} kN")
            print(f"  Top displacement: {drift:.6f} m")
        else:
            print("  [warning] analysis produced no converged steps")
        print()

    # ── Comparison table ───────────────────────────────────────────────
    print("── Comparison ─────────────────────────────────────────────")
    wall_h = 3.0
    headers = ["LayeredShell", "SFI_MVLEM_3D"]
    n_rows = [len(results[k]["control_disp"]) for k, *_ in models]
    peaks = [
        abs(results[k]["base_shear"]).max() if n_rows[i] else float("nan")
        for i, (k, *_) in enumerate(models)
    ]
    drifts = [
        results[k]["control_disp"][-1] if n_rows[i] else float("nan")
        for i, (k, *_) in enumerate(models)
    ]
    row_w = 26
    col_w = 18
    print("  " + f"{'':{row_w}s}" + "".join(f"{h:>{col_w}s}" for h in headers))
    print(
        "  " + f"{'Peak base shear (kN)':{row_w}s}"
        + "".join(f"{p:>{col_w}.1f}" for p in peaks)
    )
    print(
        "  " + f"{'Top drift (m)':{row_w}s}"
        + "".join(f"{d:>{col_w}.4f}" for d in drifts)
    )
    print(
        "  " + f"{'Top drift ratio':{row_w}s}"
        + "".join(f"{d / wall_h * 100:>{col_w - 1}.2f}%" for d in drifts)
    )
    print("  " + "-" * (row_w + col_w * len(models)))

    # ── Plots ──────────────────────────────────────────────────────────
    if not args.no_plot and all(len(results[k]["control_disp"]) for k, *_ in models):
        print("\n── Capacity curves ──")
        plot_overlay(
            [(label, results[key]) for key, label, _cfg in models],
            out_dir,
        )

    # ── Tcl export (optional) ──────────────────────────────────────────
    if args.tcl:
        print("\n── Tcl export ──")
        for key, _label, _cfg in models:
            export_tcl(mesh_models[key], key, out_dir)

    # ── Python script export (optional) ─────────────────────────────────
    if args.py:
        print("\n── Python script export ──")
        for key, _label, cfg in models:
            export_py(cfg, key, out_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()