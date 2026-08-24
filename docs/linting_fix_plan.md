---
title: "Linting Fix Plan"
description: "Pyright error baseline and phased fix strategy for src/fea_toolkit (reference for cleanup work)."
status: "draft"
tags: [planning, linting, pyright, cleanup]
category: [planning]
---
# Linting Fix Plan — Pyright Errors (Jan 2026)

> Generated from a full pyright run on `src/fea_toolkit/` on 2026-01-08.
> **537 errors total**, across 35+ files.
>
> If starting a fresh task, read this document first — all line-level diagnostics
> are reproduced below so you don't need to re-run pyright.
>
> **Status update (2026-08-21):** re-running `python -m pyright src/fea_toolkit/`
> now reports **219 errors / 109 warnings** (down from 537).  The Phase 1 real
> bugs listed below are **all fixed** — verified against the current code
> (`io/log.py`, `stories.py`, `colour_from_npz.py`, `renderers/pyvista.py`,
> `analysis/nonlinear_dynamic.py`, `analysis_builder.py`, `storey_response.py`).
> `analysis/manager.py` no longer exists (removed with the analysis-manager
> simplification — see `docs/analysis_builder_migration_plan.md`).  The
> remaining 219 errors are overwhelmingly Phase 3 typing noise (pandas/pyvista
> overloads, `Optional`-access) — the benign categories listed in `.clinerules`
> §11 — not runtime bugs.  Phase 2 (`pyrightconfig.json`) was not committed;
> the recommended next step is a fresh per-file triage of the Phase 3 count.
>
> **Status update (2026-08-24):** the historical file paths in this document
> are pre-split.  Post-P2 + runner-split: `model/geometry.py` is a 79-line
> facade (errors moved to `geometry_core/frames/mesh`), `plotting/viz.py` is a
> 136-line facade (errors in `viz_*`), and the runner errors moved from
> `opensees/analysis_builder.py` into the `_runner_*` modules.  Re-run
> `python -m pyright src/` for a fresh per-file triage before using the
> counts below.

## Quick Start (for a fresh task)

1. **Read this document** to understand the overall strategy.
2. **Check the current state**: `python -m pyright src/ --outputjson > /tmp/pyright_src.json`
3. **Compare** against the baseline counts below to see what's already been fixed.
4. **Phase 1 (real bugs) is already fixed** (was (9 findings → 13 diagnostics), then **Phase 2 (config)**, then **Phase 3 (optional type improvements)**.

---

## Current Baseline (as of 2026-01-08)

| Phase | Category | Count | Strategy |
|---|---|---|---|
| 1 | Real bugs (missing imports, undefined vars) | 13 diagnostics (9 findings; finding 1.9 spans 6 files) | Fix individually |
| 2 | Config suppression (rhino, optional, dynamic attr) | ~342 | `pyrightconfig.json` |
| 3 | Type variance / annotation improvements | ~182 | Fix or suppress per-file |

---

## Phase 1: Real Bugs (Must Fix — Actual Runtime Errors)

> **Historical (fixed as of 2026-08-21)** - all findings below are already
> fixed in the current code and retained for reference only; see the status
> update at the top of this document.

These are errors that pyright is **correct** about and will cause runtime failures.

### 1.1 Missing `import re` in `src/fea_toolkit/io/log.py`

- **Line 130**: `"re" is not defined`
- **Line 160**: `"re" is not defined`
- **Fix**: Add `import re` at the top of the file.
- **File**: `src/fea_toolkit/io/log.py`

### 1.2 Undefined `args` and `label` in `src/fea_toolkit/io/log.py`

- **Line 306**: `"args" is not defined`
- **Line 307**: `"label" is not defined`
- **Fix**: Investigate — these may be refactored-out references. Check surrounding code.
- **File**: `src/fea_toolkit/io/log.py`

### 1.3 Missing `import pandas as pd` in `src/fea_toolkit/model/stories.py`

- **Line 491**: `"pd" is not defined`
- **Fix**: Add `import pandas as pd` at the top of the file.
- **File**: `src/fea_toolkit/model/stories.py`

### 1.4 Missing `Dict` in imports in `src/fea_toolkit/rhino/colour_from_npz.py`

- **Line 390**: `"Dict" is not defined`
- **Fix**: Add `Dict` to the `from typing import ...` line.
- **File**: `src/fea_toolkit/rhino/colour_from_npz.py`

### 1.5 Broken relative import in `src/fea_toolkit/plotting/renderers/pyvista.py`

- **Line 346**: `Import "..utils" could not be resolved`
- **Fix**: Check if `..utils` exists or should be `.utils` or an absolute import.
- **File**: `src/fea_toolkit/plotting/renderers/pyvista.py`

### 1.6 Unknown import in `src/fea_toolkit/analysis/nonlinear_dynamic.py`

- **Line 100**: `"dynamic_time_history_tcl" is unknown import symbol`
- **Fix**: Check what's actually exported from the imported module. May need to update the import path or fix the exported name.
- **File**: `src/fea_toolkit/analysis/nonlinear_dynamic.py`

### 1.7 Unbound `get_SAP_vecxz` in `src/fea_toolkit/opensees/analysis_builder.py`

- **Line 1748**: `"get_SAP_vecxz" is unbound`
- **Fix**: This is likely a missing import from `..model.geometry`. Add the import.
- **File (baseline)**: `src/fea_toolkit/opensees/analysis_builder.py` — the
  symbol now lives in `model/geometry_core.py`; the bug was fixed.

### 1.8 Possibly-unbound `np` in `src/fea_toolkit/model/storey_response.py`

- **Line 646**: `"np" is possibly unbound`
- **Line 653**: `"np" is possibly unbound`
- **Line 658**: `"np" is possibly unbound` (×3 occurrences on same line)
- **Fix**: The `import numpy as np` appears to be conditional. Make it unconditional or add `else: import numpy as np`.
- **File**: `src/fea_toolkit/model/storey_response.py`

### 1.9 Undefined `MeshModel` forward references in analysis package

Pyright doesn't resolve string annotations (`"MeshModel"`). These are in:

- `src/fea_toolkit/analysis/base.py` line 219
- `src/fea_toolkit/analysis/modal.py` line 30
- `src/fea_toolkit/analysis/rs.py` line 46
- `src/fea_toolkit/analysis/static.py` line 38
- `src/fea_toolkit/opensees/recorder.py` line 482

**Fix options** (choose one):
1. Use `from __future__ import annotations` at the top of each file (already Python 3.9+ compatible per `pyproject.toml`).
2. Add a real `MeshModel` import (e.g., `from ..model.mesh_model import MeshModel`) under `TYPE_CHECKING`.
3. Suppress with `# pyright: ignore[reportUndefinedVariable]` on each line.

---

## Phase 2: pyrightconfig.json (One-Time Config)

Create `pyrightconfig.json` at the repo root with:

```json
{
  "exclude": [
    "src/fea_toolkit/rhino",
    "local",
    "examples",
    "tests",
    "docs"
  ],
  "typeCheckingMode": "basic",
  "reportOptionalMemberAccess": "warning",
  "reportOptionalSubscript": "warning",
  "reportOptionalOperand": "warning",
  "reportOptionalIterable": "warning",
  "reportAttributeAccessIssue": "warning",
  "reportMissingImports": "error",
  "reportUndefinedVariable": "error",
  "reportUnboundVariable": "error"
}
```

**What this suppresses** (~342 errors → warnings):

| Rule | Errors | Justification |
|---|---|---|
| Rhino host-only (exclude) | ~163 | `System.Drawing`, `rhinoscriptsyntax`, etc. only exist inside Rhino 8 |
| `reportOptionalMemberAccess` | ~72 | `Optional[T]` without `is not None` guard — runtime-safe duck-typing |
| `reportOptionalSubscript` | ~31 | Same pattern, dict access on Optional |
| `reportOptionalOperand` | ~23 | Math on Optional values — guarded at runtime |
| `reportOptionalIterable` | ~1 | Iterating Optional |
| `reportAttributeAccessIssue` | ~91 | Subclass attrs on base type, RecordingOpenSees proxy |

**Commit**: Commit `pyrightconfig.json` to the repo (it's project-level config, not user-specific).

---

## Phase 3: Optional Type Improvements (Lower Priority)

These remain after Phase 1 & 2. They are not runtime bugs but would improve type safety.

### 3.1 `reportArgumentType` (144 errors)

Mostly `tuple[float, float]` vs `list[float]` mismatches in geometry code and numpy interop. The code coerces at runtime. Fixing would require either:
- Changing function signatures to accept `Sequence[float]` or `Union[list, tuple]`
- Adding `# type: ignore[arg-type]` at each call site

**Files affected**: `model/geometry.py` (97), `plotting/viz.py` (73), `io/report.py`, `plotting/report.py`, `opensees/analysis_builder.py`

### 3.2 `reportReturnType` (12 errors)

Mostly in `plotting/viz.py` — inferred returns don't match declared types because numpy/PyVista types aren't fully resolved.

**Fix**: Add explicit type casts or `# type: ignore[return-type]` at return statements.

### 3.3 `reportCallIssue` (57 errors)

Wrong arg counts, missing kwargs — mostly in `model/geometry.py` and `mesh/remesh.py`. Many are false positives from numpy/pyvista overloads.

### 3.4 `reportIndexIssue` (21 errors)

Using int/float as dict keys — runtime duck-typing, but the type checker can't verify.

---

## Files Sorted by Error Count (for prioritization)

| File | Errors |
|---|---|
| `model/geometry.py` | 97 |
| `plotting/viz.py` | 73 |
| `rhino/geometry.py` | 62 (→ excluded) |
| `rhino/colors.py` | 52 (→ excluded) |
| `rhino/colour_from_npz.py` | 49 (→ excluded) |
| `opensees/analysis_builder.py` | 36 |
| `plotting/report.py` | 28 |
| `plotting/interactive_viewer.py` | 24 |
| `plotting/renderers/pyvista.py` | 14 |
| `io/report.py` | 12 |
| `model/storey_response.py` | 9 |
| `io/npz_reader.py` | 8 |
| `opensees/preprocessor.py` | 6 |
| `opensees/recorder.py` | 5 |
| `plotting/diagnostics.py` | 5 |
| Others (20+ files) | remaining |

---

## Execution Order

```
1. Phase 1 bugs are fixed (9 findings → 13 diagnostics)  ←  run pyright, verify ~13 drop
2. Create pyrightconfig.json  ←  run pyright, verify ~342 drop (Phase 2 baseline)
3. Run full test suite  ←  confirm no regressions
4. (Future) Phase 3 improvements  ←  per-file triage
```

---

## Renewing the Diagnostic Baseline

To re-run pyright and save the output:
```bash
cd /Users/andrew/Projects/fea_toolkit
source /Users/andrew/Projects/OpenSeesPy/venv_opensees/bin/activate
python -m pyright src/ --outputjson > /tmp/pyright_src.json 2>/dev/null
```

To check counts:
```bash
python3 -c "
import json
with open('/tmp/pyright_src.json') as f:
    diags = json.load(f)['generalDiagnostics']
from collections import Counter
print(f'Total: {len(diags)}')
print('By rule:')
for r, c in Counter(d['rule'] for d in diags).most_common(15):
    print(f'  {r}: {c}')
"