# Examples

Example scripts demonstrating the `fea_toolkit` workflow.
All examples accept `--sample` to use a built‑in cantilever model
(no external files needed).

## Quick start

```bash
# Built‑in sample (no .s2k file needed)
python examples/basic_usage.py --sample
python examples/static_analysis.py --sample
python examples/pushover_analysis.py --sample
python examples/modal_rs_analysis.py --sample

# Or your own model
python examples/basic_usage.py /path/to/model.s2k
```

## Available examples

| Script | What it demonstrates | Input |
|---|---|---|
| `verify_openseespy.py` | **OpenSeesPy installation smoke-test** — standalone RC cantilever with Concrete01/Steel01 fiber section, dispBeamColumn, gravity analysis, and displacement-controlled pushover. No fea_toolkit imports. | None (self-contained) |
| `basic_usage.py` | **Core workflow** — parse .s2k, enrich sections, build OpenSees model, run static analysis with equilibrium checks. Start here. | `.s2k` / `--sample` |
| `static_analysis.py` | **Static analysis + force diagrams** — parse, build, run a load combination, extract element forces, plot 2D/3D moment, shear, and axial diagrams. | `.s2k` / `--sample` |
| `pushover_analysis.py` | **Non-linear pushover** — two-stage gravity + lateral push with fiber sections. Demonstrates all four lateral load patterns (`uniform`, `triangular`, `mode1`, `pattern`). Exports PNG and SVG plots. | `.s2k` / `--sample` |
| `modal_rs_analysis.py` | **Modal + response spectrum** — seismic masses, eigenvalue analysis, CQC response spectrum (GB 50011), element-level RS forces, missing mass correction. | `.s2k` / `--sample` |
| `wall_pushover_compare.py` | **RC wall pushover comparison** — runs the converging toolkit paths (**MVLEM_3D** and **LayeredShell/ShellNLDKGQ**) on the same 4 m × 0.3 m wall at three heights (H/W = 0.5, 1.0, 2.0), unified gravity (P = 0.20·fc·Ag = 7200 kN) + displacement-controlled protocol, overlaid capacity curves (one figure, three subplots), optional Tcl + OpenSeesPy script export, optional `--fiber` opt-in flexure-only reference.  Results in `docs/mvlem_wall_analysis.md` §7.1. | None (self-contained) |

## Sample model

`examples/sample_model.py` provides three programmatic models (no `.s2k`
file needed):

- `make_sample_model()` — a simple 10 m steel cantilever column with a
  single I‑section, DEAD and WIND load patterns, and a MASS SOURCE;
  used by the `--sample` flag and suitable for all example types.
- `make_rc_frame_model()` — a single-storey, 1-bay reinforced-concrete
  moment frame (kN-m units, C30/Rebar/Q355) that yields in pushover with
  fibre sections; the representative nonlinear model for the CSM
  performance-point workflow.
- `make_rc_frame_3d()` — a genuinely **3D** single-storey, 2-bay × 2-bay
  RC moment frame (nodes with non-zero Y), exercising the full 3D
  OpenSees domain (`ndm=3`, `ndf=6`); used to validate the 3D-only
  pushover path (see `docs/deprecation_plan.md` Gap 3).

## Output directory

Generated plots (PNG, SVG) are saved to `examples/output/`, which is
gitignored.  Each script creates this directory automatically.

## Running from anywhere

All examples add `src/` to `sys.path` automatically, so they work from
any working directory:

```bash
python examples/basic_usage.py --sample
python examples/static_analysis.py --sample
python examples/pushover_analysis.py --sample
```

## See also

- `docs/pushover_analysis.md` — detailed documentation for pushover analysis
- `tests/` — unit tests for the library components
