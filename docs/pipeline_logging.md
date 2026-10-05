# Pipeline logging — one event, many sinks

The toolkit routes its narrative diagnostics through a single
`fea_toolkit.io.log.PipelineLog`.  This page is the contract for that
layer: what a log *is*, who emits to it, what each `step` tag means, and
where plain `print()` is still the right tool.

## The contract

A source **emits** a `(level, step, msg)` event *once*; sinks *render* it
(stdout, a JSON file, a Qt signal, the report's "Analysis Log" table).  The
emitter never decides *where* a message goes — it only tags it.

```python
from fea_toolkit.io.log import PipelineLog

log = PipelineLog()                       # collect=True, verbose=True
log.info("static", "build-once: reusing one OpenSees domain")
log.warning("modal", "fewer than 3 modes converged")
print(log.summary())                      # WARN/ERROR only
print(log.markdown())                     # all levels, as a table
log.to_json("analysis.log.json")
```

### Levels

| Level | Printed by default stdout sink? | Meaning |
|---|---|---|
| `DEBUG` | only when `verbose` | per-item diagnostic detail |
| `INFO`  | only when `verbose` | run-lifecycle progress |
| `WARN`  | **always** | recoverable problem / fallback / skip |
| `ERROR` | **always** | non-recoverable failure |

A quiet build (`verbose=False`) therefore still surfaces warnings — the
design rule is *"a warning is never silently swallowed"*.

### The `log` parameter

Public entry points accept `log: PipelineLog | bool | None` and normalise it
through `coerce_log`:

* `None` / `True` → `console_log` (stdout, no retention),
* `False` → `_silent_log` (no output, no retention),
* a `PipelineLog` → returned unchanged (collected, sinkable).

Constructors (`AnalysisBuilder`, `Preprocessor`) default a *missing* `log` to
`PipelineLog(verbose=config["verbose"], collect=False)`, so the legacy
`verbose` config key still works and still gates `INFO`/`DEBUG`.

## Step tags

`step` scopes an event to the phase that emitted it, so the report table can
group and filter.  Current tags:

| Tag | Emitters |
|---|---|
| `preprocessor` | `Preprocessor` split/wall/diaphragm progress, property resolution |
| `materials` | `AnalysisBuilder` material creation |
| `elements` | `AnalysisBuilder` element creation |
| `sections` | `AnalysisBuilder` section creation |
| `static` | linear/static runner, build-once decision |
| `modal` | modal runner |
| `rs` | response-spectrum runner |
| `pushover` | pushover runner / orchestration |
| `workflow` | recipe-step progress routed through `run_recipe(..., pipeline_log=...)` |

New code must use an existing tag or add one here deliberately.

## Report-table carve-out

Functions whose *product is* a table keep writing text (usually via `print`
under a `print_results` flag); their *callers* log a one-line summary.  This
applies to:

* the modal period table and the RS CQC/SRSS tables in the runners,
* `plotting/diagnostics.py` tables,
* `io/report.py`'s modal table,
* `model/checks.py` tables,
* the wall-in-slab report in `geometry_mesh`.

The rule: **a table is a report, not a diagnostic event.**  It must not be
squashed into a single log line, and it must not emit one log event per row.

## `print_progress` carve-out

The pushover runner's `print_progress` flag ("print a line per pushover
step") is a **streaming progress reporter**, not a diagnostic.  Its `print()`
calls stay `print()`:

* Routing ~100 per-step lines through `PipelineLog` would flood the report's
  Analysis Log table with near-identical entries.
* Per-iteration output is explicitly out of scope for the structured log
  (see the performance guardrail below).

If a *collected* progress stream is ever wanted, the right shape is a
dedicated *progress sink* on `PipelineLog` (renders without retaining), not a
rename of `print_progress` to `log`.

## Performance guardrail

Events are O(phases + cases + warnings) — a 1:1 swap of the old boundary-level
`print()` calls.  **Do not** emit log events inside loops over nodes,
elements, or solver iterations; a log call in a hot path turns a cheap
format-string into millions of allocations.  Per-iteration detail belongs at
`DEBUG` and even then only when it is not in the innermost loop.

## stdlib `logging` bridge

A few modules still emit through the stdlib `logging` module
(`logger = logging.getLogger(__name__)` — the builder's Tcl-export wall
warnings, the constraint narrative, limit-state skips, …).  Rather than
converting them one-by-one, `generate_report` attaches a `PipelineHandler` (a
`logging.Handler`) to the `fea_toolkit` logger for the duration of the run, so
those `logger.warning` / `logger.info` calls become `PipelineLog` events with
the same level mapping (`WARNING→WARN`, `INFO→INFO`) and a `step` tag derived
from the logger's leaf module name (`_constraints` → `constraints`,
`_runner_static` → `static`, `builder` → `builder`).

The bridge is installed only while `generate_report` runs; a bare analysis call
does not install it, so a stray `logger.warning` there still reaches stderr.
Fully converting such a call means replacing the `logger.*` line with a
`self._log.*` line (or threading a `log` through) — not leaving it to the
bridge.

## Future work

* **Diagnostic `print()` → `logger` sweep is done** across `plotting/`,
  `rhino/`, `io/` and `model/`: the diagnostic warnings (`"No <x> data found"`,
  `"… not installed …"`, `"Warning: …"`, `"WARNING: …"`, `"Storey response …
  failed"`) now go through module-level `logging.getLogger(__name__)` loggers
  (or the `log` channel where one is already threaded), so the
  ``PipelineHandler`` bridge captures them during a report run.

  What remains as plain `print()` is the deliberate carve-out — the *product*
  of a function, not an event:

  * report tables (`diagnostics.py` modal-disconnect table, `checks.py`
    buckling/self-weight, `io/report.py` participation summary, `geometry_mesh.py`
    wall–slab report),
  * CLI entry points (`model/review.py`, `io/helper.py`, `io/table_registry.py`),
  * docstring ``>>>`` examples,
  * boundary-level **success/progress** lines — `"… saved …"`, `"Loaded N …"`,
    `"Coloured N …"`, `"Created N …"`, the Rhino importer's verbose-gated
    `"Creating …"`, and `geometry_frames.py`'s verbose split summaries.

  The progress lines are the one open judgement call: they could become
  `logger.info` (silent unless the logger is configured), but that would hide
  user-facing feedback in standalone use — so they stay `print()` for now,
  matching the `print_progress` carve-out.

  The GUI's progress log is already merged: `run_recipe(..., pipeline_log=...)`
  routes `context.log` progress (step ``"workflow"``) and the structured
  analysis events through one `PipelineLog`, which `MainWindow` sinks into its
  message log.
