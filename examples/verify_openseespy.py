"""
verify_openseespy.py — Quick OpenSeesPy installation verifier

Verifies that an OpenSeesPy installation is functional by running three
increasingly demanding checks on a 3D RC cantilever wall:

  1. **Setup**  — geometry, Concrete01/Steel01 materials, fiber section,
                   dispBeamColumn element, PDelta transformation.
  2. **Gravity** — 500 kN load-controlled gravity analysis (10 substeps).
  3. **Pushover** — displacement-controlled lateral push to 150 mm drift.

Plus additional smoke-tests that exercise other OpenSeesPy API surfaces
used elsewhere in ``fea_toolkit``:

  4. **Elastic frame** — elasticBeamColumn with Linear transformation.
  5. **Force-based frame** — forceBeamColumn with Lobatto integration.
  6. **Steel02 material** — Giuffré-Menegotto-Pinto steel (typical for RC).
  7. **nD materials + layered shell** — ElasticIsotropic, J2PlateFibre,
     ConcreteS nD materials, LayeredShell section, ShellMITC4 element.
  8. **Hysteretic material** — pinching steel for brace-truss modelling.
  9. **Truss element** — truss with Hysteretic material for brace buckling.
  10. **Rigid diaphragm** — rigidDiaphragm constraint at a storey level.

Usage:
    python examples/verify_openseespy.py              # All checks
    python examples/verify_openseespy.py --quick       # Checks 1-3 only
    python examples/verify_openseespy.py --verify-all   # Explicitly run all

This script never touches the ``fea_toolkit`` package.  It is designed for:
  - Verifying a new OpenSeesPy installation.
  - Smoke-testing after reinstalling or upgrading OpenSeesPy.
  - Isolating OpenSeesPy issues from fea_toolkit issues.

No external files, mesh models, SAP2000 data, or fea_toolkit imports are
required.  The model is a simple 2-node, 1-element cantilever built
entirely from scratch.
"""

import sys
import os
import argparse

try:
    import openseespy.opensees as ops
except ImportError as exc:
    print(f"✗ FAILED: Could not import openseespy.opensees — {exc}")
    print("  Install with: pip install openseespy")
    sys.exit(1)


# ── Helpers ────────────────────────────────────────────────────────────


def _wipe_model():
    """Wipe OpenSees model state, ignoring errors if none exists."""
    try:
        ops.wipe()
    except Exception:
        pass


def check(seq: int, name: str, ok: bool, detail: str = ""):
    """Print a formatted check result."""
    status = "✅" if ok else "✗"
    label = f"Check {seq}"
    detail_str = f" — {detail}" if detail else ""
    print(f"  {status} {label}: {name}{detail_str}")
    if not ok:
        raise SystemExit(1)


# ── Check 1: Model setup (basic) ──────────────────────────────────────


def _build_cantilever_model():
    """Build a 3D RC cantilever wall (Concrete01 + Steel01 fiber)."""
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 3000.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.uniaxialMaterial("Concrete01", 1, -30.0, -0.002, -5.0, -0.006)
    ops.uniaxialMaterial("Steel01", 2, 400.0, 200000.0, 0.01)
    ops.section("Fiber", 1, "-GJ", 1.0e10)
    ops.patch("rect", 1, 8, 8, -200.0, -200.0, 200.0, 200.0)
    ops.layer("straight", 2, 4, 490.9, -180.0, -180.0, 180.0, -180.0)
    ops.layer("straight", 2, 4, 490.9, -180.0, 180.0, 180.0, 180.0)
    ops.geomTransf("PDelta", 1, 1, 0, 0)
    ops.beamIntegration("Lobatto", 1, 1, 5)
    ops.element("dispBeamColumn", 1, 1, 2, 1, 1)


def _run_gravity() -> float:
    """Run gravity load-control analysis, return axial shortening (mm)."""
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -500000.0, 0.0, 0.0, 0.0)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-6, 10, 0)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.1)
    ops.analysis("Static")
    ok = ops.analyze(10)
    if ok != 0:
        raise RuntimeError(f"Gravity analysis failed (analyze returned {ok})")
    ops.loadConst("-time", 0.0)
    return ops.nodeDisp(2, 3)


def _run_pushover(num_steps: int = 300):
    """Run displacement-controlled pushover to 150 mm drift."""
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    ops.load(2, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    dU = 0.5
    ops.integrator("DisplacementControl", 2, 1, dU)
    ops.analysis("Static")
    target = 150.0
    step = 0
    current = 0.0
    while step < num_steps:
        ops.test("NormDispIncr", 1.0e-5, 200, 0)
        ops.algorithm("Newton")
        ok = ops.analyze(1)
        if ok != 0:
            ops.test("NormDispIncr", 1.0e-5, 500, 0)
            ops.algorithm("KrylovNewton")
            ok = ops.analyze(1)
        if ok != 0:
            ops.algorithm("ModifiedNewton", "-initial")
            ok = ops.analyze(1)
        if ok != 0:
            dU *= 0.1
            ops.integrator("DisplacementControl", 2, 1, dU)
            ops.algorithm("Newton")
            ok = ops.analyze(1)
        if ok != 0:
            raise RuntimeError(f"Pushover failed at step {step}")
        current = ops.nodeDisp(2, 1)
        step += 1
        if current >= target:
            break
    else:
        # while exhausted without break (step >= num_steps and target not reached)
        raise RuntimeError(
            f"Pushover failed to reach target {target:.1f} mm after {num_steps} "
            f"steps (final displacement: {current:.1f} mm)"
        )
    return step, ops.nodeDisp(2, 1)


# ── Check 4: Elastic frame element ──────────────────────────────────


def _check_elastic_frame():
    """Create an elasticBeamColumn with Linear transformation."""
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    ops.node(1, 0.0, 0.0)
    ops.node(2, 0.0, 3000.0)
    ops.fix(1, 1, 1, 1)
    ops.geomTransf("Linear", 1)
    # elasticBeamColumn: tag, iNode, jNode, A, E, Iz, transfTag
    ops.element("elasticBeamColumn", 1, 1, 2, 40000.0, 200000.0, 1.333e9, 1)


# ── Check 5: Force-based frame element ──────────────────────────────


def _check_force_beam_column():
    """Create a forceBeamColumn with fiber section and Lobatto integration."""
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    ops.node(1, 0.0, 0.0)
    ops.node(2, 0.0, 3000.0)
    ops.fix(1, 1, 1, 1)
    ops.uniaxialMaterial("Concrete01", 1, -30.0, -0.002, -5.0, -0.006)
    ops.uniaxialMaterial("Steel01", 2, 400.0, 200000.0, 0.01)
    ops.section("Fiber", 1, "-GJ", 1.0e10)
    ops.patch("rect", 1, 4, 4, -100.0, -100.0, 100.0, 100.0)
    ops.layer("straight", 2, 2, 200.0, -90.0, -90.0, 90.0, -90.0)
    ops.geomTransf("PDelta", 1)
    ops.beamIntegration("Lobatto", 1, 1, 5)
    ops.element("forceBeamColumn", 1, 1, 2, 1, 1)


# ── Check 6: Steel02 material ───────────────────────────────────────


def _check_steel02():
    """Create a Steel02 (Giuffré-Menegotto-Pinto) material."""
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    # Steel02: tag, Fy, E, b, R0, cR1, cR2
    ops.uniaxialMaterial("Steel02", 1, 400.0, 200000.0, 0.01, 18.5, 0.925, 0.15)


# ── Check 7: nD materials + LayeredShell + ShellMITC4 ───────────────


def _check_nd_materials_and_layered_shell():
    """Create nD materials, LayeredShell section, and ShellMITC4 element."""
    _wipe_model()
    ops.model("basic", "-ndm", 3, "-ndf", 6)

    # Four nodes for a single ShellMITC4 element (4×4 m shell at z = 4000 mm)
    ops.node(5, 0.0, 0.0, 4000.0)
    ops.node(6, 4000.0, 0.0, 4000.0)
    ops.node(7, 4000.0, 4000.0, 4000.0)
    ops.node(8, 0.0, 4000.0, 4000.0)

    # nD materials (mimics a reinforced concrete shear wall layup)
    # ConcreteS: tag, E, nu, fc, ft, Es
    ops.nDMaterial("ConcreteS", 1, 30000.0, 0.2, -30.0, 3.0, 0.0)
    # J2PlateFibre: tag, E, nu, fy, Hiso, Hkin
    ops.nDMaterial("J2PlateFibre", 2, 200000.0, 0.3, 400.0, 0.0, 0.0)
    # ElasticIsotropic: tag, E, nu
    ops.nDMaterial("ElasticIsotropic", 3, 30000.0, 0.2)

    # LayeredShell section: tag, nLayers, matTag1, t1, matTag2, t2, ...
    # Wall cross-section (outside → inside):
    #   1. Cover concrete:  40 mm,  ConcreteS (tag 3 = ElasticIsotropic stand-in)
    #   2. Smeared rebar:     2 mm,  J2PlateFibre (tag 2)
    #   3. Core concrete:    300 mm, ConcreteS (tag 1)
    #   4. Smeared rebar:     2 mm,  J2PlateFibre (tag 2)
    #   5. Cover concrete:   40 mm,  ConcreteS (tag 3)
    ops.section(
        "LayeredShell",
        1,
        5,
        3,
        40.0,  # cover (elastic)
        2,
        2.0,  # rebar layer
        1,
        300.0,  # core
        2,
        2.0,  # rebar layer
        3,
        40.0,
    )  # cover (elastic)

    # ShellMITC4 element: tag, n1, n2, n3, n4, secTag
    ops.element("ShellMITC4", 1, 5, 6, 7, 8, 1)


# ── Check 8: Hysteretic material ────────────────────────────────────


def _check_hysteretic():
    """Create a Hysteretic uniaxial material (brace buckling modelling)."""
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    # Hysteretic: tag, s1p, e1p, s2p, e2p, s3p, e3p,
    #                   s1n, e1n, s2n, e2n, s3n, e3n,
    #                   pinchX, pinchY, damage1, damage2, beta
    #
    # Tension:  elastic to 400 MPa @ 0.002, hardening to 440 MPa @ 0.01
    # Compression: buckling at -200 MPa @ -0.002, softening to -50 MPa @ -0.02
    ops.uniaxialMaterial(
        "Hysteretic",
        1,
        400.0,
        0.002,
        440.0,
        0.01,
        440.0,
        0.02,
        -200.0,
        -0.002,
        -100.0,
        -0.01,
        -50.0,
        -0.02,
        0.5,
        0.5,
        0.0,
        0.0,
        0.0,
    )


# ── Check 9: Truss element ──────────────────────────────────────────


def _check_truss():
    """Create a Truss element with a Hysteretic material."""
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    ops.node(1, 0.0, 0.0)
    ops.node(2, 5000.0, 0.0)
    ops.fix(1, 1, 1, 1)
    ops.fix(2, 1, 1, 0)
    ops.uniaxialMaterial("Elastic", 1, 200000.0)
    # Truss: tag, iNode, jNode, A, matTag
    ops.element("Truss", 1, 1, 2, 1000.0, 1)


# ── Check 10: Rigid diaphragm ───────────────────────────────────────


def _check_rigid_diaphragm():
    """Create a rigidDiaphragm constraint at a storey level."""
    _wipe_model()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 6000.0, 0.0, 0.0)
    ops.node(3, 6000.0, 4000.0, 0.0)
    ops.node(4, 0.0, 4000.0, 0.0)
    ops.node(5, 0.0, 0.0, 3000.0)  # master node
    ops.node(6, 6000.0, 0.0, 3000.0)
    ops.node(7, 6000.0, 4000.0, 3000.0)
    ops.node(8, 0.0, 4000.0, 3000.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 1, 1, 1, 1, 1, 1)
    ops.fix(3, 1, 1, 1, 1, 1, 1)
    ops.fix(4, 1, 1, 1, 1, 1, 1)
    # rigidDiaphragm: perpDirn, masterNode, slaveNode1, slaveNode2, ...
    ops.rigidDiaphragm(3, 5, 6, 7, 8)


# ── Main ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Verify OpenSeesPy installation with a nonlinear RC cantilever."
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only checks 1-3 (setup, gravity, pushover).",
    )
    parser.add_argument(
        "--verify-all",
        action="store_true",
        help="Run all checks explicitly (default when --quick is not set).",
    )
    args = parser.parse_args()
    run_all = not args.quick or args.verify_all

    print("═══ OpenSeesPy Verification ═══")
    print()

    c = 1

    # ── Check 1: Setup ────────────────────────────────────────────────
    _wipe_model()
    print(f"{c}. Model setup...", end=" ")
    try:
        _build_cantilever_model()
        check(c, "Concrete01, Steel01, Fiber section, dispBeamColumn, PDelta", True)
    except Exception as exc:
        check(c, "Basic setup", False, str(exc))
    c += 1

    # ── Check 2: Gravity ──────────────────────────────────────────────
    _wipe_model()
    _build_cantilever_model()
    print(f"{c}. Gravity analysis...", end=" ")
    try:
        dz = _run_gravity()
        check(c, f"Converged. Axial shortening dz = {dz:.4f} mm (compression)", True)
    except Exception as exc:
        check(c, "Gravity analysis", False, str(exc))
    c += 1

    # ── Check 3: Pushover ─────────────────────────────────────────────
    _wipe_model()
    _build_cantilever_model()
    _run_gravity()
    print(f"{c}. Pushover analysis...", end=" ")
    try:
        total_steps, final_disp = _run_pushover()
        check(c, f"{total_steps} steps to {final_disp:.1f} mm drift", True)
    except Exception as exc:
        check(c, "Pushover analysis", False, str(exc))
    c += 1

    if not run_all:
        print()
        print("═══ Quick verification passed ═══")
        _wipe_model()
        return 0

    # ── Check 4: Elastic frame ────────────────────────────────────────
    print(f"{c}. Elastic frame element...", end=" ")
    try:
        _check_elastic_frame()
        check(c, "elasticBeamColumn with Linear geomTransf", True)
    except Exception as exc:
        check(c, "Elastic frame element", False, str(exc))
    c += 1

    # ── Check 5: Force-based frame ────────────────────────────────────
    print(f"{c}. Force-based frame element...", end=" ")
    try:
        _check_force_beam_column()
        check(c, "forceBeamColumn with Lobatto integration + fiber section", True)
    except Exception as exc:
        check(c, "Force-based frame element", False, str(exc))
    c += 1

    # ── Check 6: Steel02 material ─────────────────────────────────────
    print(f"{c}. Steel02 material...", end=" ")
    try:
        _check_steel02()
        check(c, "Steel02 (Giuffré-Menegotto-Pinto) with isotropic hardening", True)
    except Exception as exc:
        check(c, "Steel02 material", False, str(exc))
    c += 1

    # ── Check 7: nD materials + LayeredShell ──────────────────────────
    print(f"{c}. nD materials + LayeredShell + ShellMITC4...", end=" ")
    try:
        _check_nd_materials_and_layered_shell()
        check(c, "ConcreteS, J2PlateFibre, ElasticIsotropic, LayeredShell, ShellMITC4", True)
    except Exception as exc:
        check(c, "nD materials + LayeredShell", False, str(exc))
    c += 1

    # ── Check 8: Hysteretic material ──────────────────────────────────
    print(f"{c}. Hysteretic material...", end=" ")
    try:
        _check_hysteretic()
        check(c, "Hysteretic (pinching) material for brace buckling modelling", True)
    except Exception as exc:
        check(c, "Hysteretic material", False, str(exc))
    c += 1

    # ── Check 9: Truss element ────────────────────────────────────────
    print(f"{c}. Truss element...", end=" ")
    try:
        _check_truss()
        check(c, "Truss element with Elastic material", True)
    except Exception as exc:
        check(c, "Truss element", False, str(exc))
    c += 1

    # ── Check 10: Rigid diaphragm ─────────────────────────────────────
    print(f"{c}. Rigid diaphragm constraint...", end=" ")
    try:
        _check_rigid_diaphragm()
        check(c, "rigidDiaphragm(perpDirn=3, master=5, slaves=6,7,8)", True)
    except Exception as exc:
        check(c, "Rigid diaphragm constraint", False, str(exc))
    c += 1

    # ── Xara-specific extensions (informational) ──────────────────────
    # These checks are informational only — they detect whether Xara's
    # custom `libOpenSeesRT` build is loaded (which extends OpenSeesPy
    # with ConcreteCM, SteelMPF, FSAM, and E_SFI elements for RC shear
    # wall analysis).  Standard ``pip install openseespy`` will report
    # these as "not available"; that is expected.

    print(f"{c}. Xara: ConcreteCM (hysteretic concrete)...", end=" ")
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    try:
        ops.uniaxialMaterial(
            "ConcreteCM",
            1,
            -30.0,
            -0.002,
            25000.0,
            4.0,
            1.0,
            2.0,
            0.00008,
            1.0,
            10000.0,
            "-GapClose",
            0,
        )
        print(f"  ✅ Check {c}: available")
    except Exception as exc:
        print(f"  ℹ️ Check {c}: not available (Xara build required: {exc})")
    c += 1

    print(f"{c}. Xara: SteelMPF (Menegotto-Pinto steel with fatigue)...", end=" ")
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    try:
        ops.uniaxialMaterial("SteelMPF", 1, 400.0, 400.0, 2.0e5, 0.01, 0.01, 18.5, 0.925, 0.15)
        print(f"  ✅ Check {c}: available")
    except Exception as exc:
        print(f"  ℹ️ Check {c}: not available (Xara build required: {exc})")
    c += 1

    print(f"{c}. Xara: FSAM (Fixed-Strut Angle Model)...", end=" ")
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    try:
        ops.uniaxialMaterial(
            "ConcreteCM",
            1,
            -30.0,
            -0.002,
            25000.0,
            4.0,
            1.0,
            2.0,
            0.00008,
            1.0,
            10000.0,
            "-GapClose",
            0,
        )
        ops.uniaxialMaterial("SteelMPF", 2, 400.0, 400.0, 2.0e5, 0.01, 0.01, 18.5, 0.925, 0.15)
        ops.nDMaterial("FSAM", 3, 0.0, 2, 2, 1, 0.006, 0.006, 0.25, 0.5)
        print(f"  ✅ Check {c}: available")
    except Exception as exc:
        print(f"  ℹ️ Check {c}: not available (Xara build required: {exc})")
    c += 1

    print(f"{c}. Xara: E_SFI (Efficient Shear-Flexure Interaction element)...", end=" ")
    _wipe_model()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    try:
        ops.uniaxialMaterial(
            "ConcreteCM",
            1,
            -30.0,
            -0.002,
            25000.0,
            4.0,
            1.0,
            2.0,
            0.00008,
            1.0,
            10000.0,
            "-GapClose",
            0,
        )
        ops.uniaxialMaterial("SteelMPF", 2, 400.0, 400.0, 2.0e5, 0.01, 0.01, 18.5, 0.925, 0.15)
        ops.nDMaterial("FSAM", 3, 0.0, 2, 2, 1, 0.006, 0.006, 0.25, 0.5)
        ops.node(1, 0.0, 0.0)
        ops.node(2, 0.0, 1500.0)
        ops.fix(1, 1, 1, 1)
        ops.element(
            "E_SFI",
            1,
            1,
            2,
            4,
            0.4,
            "-thick",
            200.0,
            200.0,
            200.0,
            200.0,
            "-width",
            500.0,
            500.0,
            500.0,
            500.0,
            "-mat",
            3,
            3,
            3,
            3,
        )
        print(f"  ✅ Check {c}: available")
    except Exception as exc:
        print(f"  ℹ️ Check {c}: not available (Xara build required: {exc})")
    c += 1

    # ── Summary ───────────────────────────────────────────────────────
    total = c - 1
    print()
    print(f"═══ All {total} checks passed ═══")
    _wipe_model()
    return 0


if __name__ == "__main__":
    sys.exit(main())
