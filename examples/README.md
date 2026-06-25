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
| `basic_usage.py` | **Core workflow** — parse .s2k, enrich sections, build OpenSees model, run static analysis with equilibrium checks. Start here. | `.s2k` / `--sample` |
| `static_analysis.py` | **Static analysis + force diagrams** — parse, build, run a load combination, extract element forces, plot 2D/3D moment, shear, and axial diagrams. | `.s2k` / `--sample` |
| `pushover_analysis.py` | **Non-linear pushover** — two-stage gravity + lateral push with fiber sections. Demonstrates all four lateral load patterns (`uniform`, `triangular`, `mode1`, `pattern`). Exports PNG and SVG plots. | `.s2k` / `--sample` |
| `modal_rs_analysis.py` | **Modal + response spectrum** — seismic masses, eigenvalue analysis, CQC response spectrum (GB 50011), element-level RS forces, missing mass correction. | `.s2k` / `--sample` |

## Sample model

`examples/sample_model.py` provides a simple 10 m steel cantilever column
used by the `--sample` flag.  It has a single I‑section, DEAD and WIND
load patterns, and a MASS SOURCE — suitable for all example types.

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
