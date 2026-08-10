#!/usr/bin/env python
"""RC wall non-linear pushover: ConcreteS + J2PlateFibre layered shell.

.. note::

   **PLACEHOLDER — not yet runnable.**  This file documents the
   *validated recipe* for a non-linear layered RC shell wall (concrete
   ``ConcreteS`` + smeared rebar ``J2PlateFibre`` nD materials in a
   LayeredShell / ShellMITC4 or ShellNLDKGQ stack) and provides the
   config skeleton for it.  The config/runner below is intentionally
   incomplete and is **not called** — promoting it to a working example
   requires an end-to-end verification run, which is tracked in
   ``docs/_pending_work.md``.

The recipe
----------

This is the **validated** non-linear shell path — the alternative to the
non-converging FSAM-in-LayeredShell combination (see
``examples/wall_pushover_fsam_layered.py``).

It is used end-to-end in:

- ``local/CLP_BSDG_Latest_Models/Admin_Building/admin_pushover_v4.py``
  (working example in the stock ``pip`` OpenSeesPy), and
- described in ``docs/layered_analysis_workflow.md`` §14.1:

    "RC walls / nonlinear shells — concrete + smeared rebar via
    ``ConcreteS`` and ``J2PlateFibre`` nD materials with ``ShellMITC4`` /
    ``ShellNLDKGQ`` layered shells (see
    ``local/CLP_BSDG_Latest_Models/Admin_Building/admin_pushover_v4.py``
    for a working end-to-end example in the stock ``pip`` OpenSeesPy)."

Compared with FSAM:

- ``ConcreteS`` is a smeared-crack concrete nD material (with
  ``PlateFromPlaneStress``-compatible behaviour when used as a layered
  shell layer).
- ``J2PlateFibre`` is a J2-plasticity steel rebar fibre nD material,
  smeared over the layer plane.
- Together they give concrete tension-softening/cracking + smeared
  reinforcement hardening in a layered shell without the FSAM
  strut-angle coupling (and without the FSAM-in-LayeredShell
  near-singularity documented in ``examples/wall_pushover_fsam_layered.py``).

Layer stack (through thickness, t = 0.3 m) — the standard RC wall
layout:

+------------+-------------+--------------------------------------+
| Layer      | thickness   | nD material                          |
+============+=============+======================================+
| 1 (cover)  | 0.05 m      | ConcreteS (concrete, boundary)       |
+------------+-------------+--------------------------------------+
| 2 (rebar)  | 0.02 m      | J2PlateFibre (smeared rebar, rho_x)  |
+------------+-------------+--------------------------------------+
| 3 (core)   | 0.16 m      | ConcreteS (concrete, core; lower fc)  |
+------------+-------------+--------------------------------------+
| 4 (rebar)  | 0.02 m      | J2PlateFibre (smeared rebar, rho_x)  |
+------------+-------------+--------------------------------------+
| 5 (cover)  | 0.05 m      | ConcreteS (concrete, boundary)       |
+------------+-------------+--------------------------------------+

Wall geometry / loads (units kN / m / C) — match
``examples/wall_pushover_compare.py``:

    node 1 = (0, 0, 0)   node 2 = (0, 4, 0)   ← base (fully fixed)
    node 4 = (0, 0, 3)   node 3 = (0, 4, 3)   ← top (free)
    Lateral push in **+X**, 50 kN total at 200 LoadControl steps.

Material parameters (SI Pa authored — framework scales) — indicative
values from ``admin_pushover_v4.py``; author in Pa and let
``scale_material_dict()`` / the Preprocessor's `nd_materials` flow
convert to model units:

    ConcreteS:  fc  = 30e6   (30 MPa),  ft  = 2.5e6   (tension)
                E   = 30e9   (30 GPa),  nu  = 0.2
    J2PlateFibre: Es = 200e9,  fy = 420e6,  hiso = 1.0e-2

To promote to a working example
-------------------------------

1. Verify the exact ``ops.nDMaterial`` argument signatures for
   ``ConcreteS`` and ``J2PlateFibre`` in the installed OpenSeesPy wheel
   (probe with a minimal single-quad model first, as in
   ``local/probe_mvlem_sfi.py``).
2. Fill in the ``nd_materials`` / ``shell_layers`` config below (or
   replace with the working block from ``admin_pushover_v4.py``) and a
   pushover runner (copy ``run_wall_pushover`` from
   ``examples/wall_pushover_compare.py`` — it already handles top/base
   edge detection, +X push and base-shear summation).
3. Run with the validation settings: base fully fixed, top free,
   ``Plain`` constraints, no gravity, 50 kN / 200 steps.
4. Add an integration test mirroring ``tests/test_wall_pushover.py``.
5. Update ``docs/mvlem_wall_analysis.md`` and this docstring to mark the
   recipe ✅ complete, and remove the placeholder cross-reference here.

See also:

- ``docs/layered_analysis_workflow.md`` §14.1 — the canonical recipe
  reference.
- ``docs/mvlem_wall_analysis.md`` — FSAM limitations and the
  SFI_MVLEM_3D alternative.
- ``local/CLP_BSDG_Latest_Models/Admin_Building/admin_pushover_v4.py`` —
  working end-to-end ConcreteS + J2PlateFibre example.
- ``examples/wall_pushover_compare.py`` — the runnable validated
  comparison (elastic layered shell vs SFI_MVLEM_3D).
- ``examples/wall_pushover_fsam_layered.py`` — the documented
  FSAM-in-LayeredShell failure investigation.
"""

import argparse
import sys
from pathlib import Path

# Make `fea_toolkit` importable when running from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

OUT_DIR = Path(__file__).parent / "output"


def nonlinear_shell_config() -> dict:
    """Non-linear layered shell config — **PLACEHOLDER (not validated)**.

    Returns an indicative config skeleton for the ConcreteS +
    J2PlateFibre layered shell recipe.  The ``nd_materials`` argument
    signatures and layer stack below are **not verified** against the
    installed OpenSeesPy wheel and must be checked before this is made
    runnable (see the module docstring "To promote to a working example").
    """
    return {
        "create_shells": True,
        "verbose": False,
        "solver_constraints": "Plain",
        "subdivide_shells": 4,
        "nd_materials": {
            # Concrete (smeared-crack): fc, ft, E, nu — SI Pa.
            "conc_bdry": {
                "material_type": "ConcreteS",
                "E": 30.0e9,
                "nu": 0.2,
                "fc": 30.0e6,
                "ft": 2.5e6,
            },
            "conc_core": {
                "material_type": "ConcreteS",
                "E": 30.0e9,
                "nu": 0.2,
                "fc": 30.0e6,
                "ft": 2.5e6,
            },
            # Smeared rebar (J2-plasticity plate fibre): Es, fy, hiso — SI Pa.
            "rebar_plate": {
                "material_type": "J2PlateFibre",
                "Es": 200.0e9,
                "fy": 420.0e6,
                "hiso": 1.0e-2,
            },
        },
        "shell_layers": {
            "WALL_LAYERS": {
                "selector": {"sections": ["WALL_SEC"]},
                "layers": [
                    {"thickness": 0.05, "nd_material": "conc_bdry"},
                    {"thickness": 0.02, "nd_material": "rebar_plate"},
                    {"thickness": 0.16, "nd_material": "conc_core"},
                    {"thickness": 0.02, "nd_material": "rebar_plate"},
                    {"thickness": 0.05, "nd_material": "conc_bdry"},
                ],
            },
        },
    }


def main() -> None:
    """Placeholder driver — prints the recipe status, does not run FEA."""
    parser = argparse.ArgumentParser(
        description=(
            "RC wall non-linear layered shell (ConcreteS + J2PlateFibre) "
            "— PLACEHOLDER, not runnable yet."
        ),
    )
    parser.parse_args()

    print("Layered non-linear shell (ConcreteS + J2PlateFibre) — PLACEHOLDER")
    print("=" * 70)
    print(
        "This example is a documented placeholder for the validated\n"
        "ConcreteS + J2PlateFibre layered shell recipe.\n"
    )
    print("References:")
    print("  - docs/layered_analysis_workflow.md §14.1")
    print("  - local/CLP_BSDG_Latest_Models/Admin_Building/admin_pushover_v4.py")
    print("  - examples/wall_pushover_compare.py (runnable comparison)")
    print("  - examples/wall_pushover_fsam_layered.py (FSAM-layered failure)\n")
    print(
        f"Indicative config (module-level, named "
        f"{nonlinear_shell_config.__name__}()):"
    )
    import pprint

    pprint.pprint(nonlinear_shell_config(), width=100, sort_dicts=False)
    print("\nNo FEA was run. Promote per the module docstring steps.")


if __name__ == "__main__":
    main()