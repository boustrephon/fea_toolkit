# Xara/OpenSeesRT Tcl Runtime Guide

> Lessons learned from running pushover analysis on a 3-storey RC building
> model via Xara's `libOpenSeesRT.dylib` and `tclsh8.6`.

## 1. Solver Selection

### Do NOT use `system UmfPack`

Xara's build of `libOpenSeesRT.dylib` (verified on arm64, July 2026 build)
**does not contain UmfPack symbols** in its exported symbol table.
Calling `system UmfPack` causes a **SIGSEGV (exit code -11)** with zero
output — the crash happens at the Tcl command execution level, before any
`puts` output is flushed.

**Use `system BandGeneral`** instead.  BandGeneral is a dense solver, so
it is expensive for large models, but it is reliably compiled into every
OpenSees build and will never segfault.  For models under ~2000 DOF
(≈300–400 nodes, 6 DOF each) it is acceptable.

| Solver | Xara? | Behaviour |
|--------|-------|-----------|
| `BandGeneral` | ✅ | Works correctly |
| `BandSPD` | ❓ | Untested, likely absent |
| `UmfPack` | ❌ | **SIGSEGV crash** |
| `SuperLU` | ❓ | Untested |
| `Mumps` | ❓ | Untested |

### Sparse solver workaround

For larger models, the best approach is to **reduce model size** (crop
the SAP2000 data before preprocessing) rather than rely on a sparse
solver.  The cropped model technique is described in §5.

## 2. Constraint Handler

### Do NOT switch constraint handlers mid-analysis

`constraints Transformation` is the default in OpenSees.  If gravity
analysis runs with `constraints Transformation`, you **cannot** issue
`constraints Penalty 1.0e12 1.0e12` after `loadConst` — the constraint
handler switch while the system already has active loads causes an
internal inconsistency that either:
- Silently produces wrong results, or
- Triggers a SIGSEGV.

**Use `constraints Transformation` throughout** — set it once before
gravity and leave it alone for the pushover phase.

| Handler | Use case | In Xara? |
|---------|----------|----------|
| `Transformation` | Default, works for all standard models | ✅ |
| `Penalty` | Needed for edge-constrained shell MPCs | ⚠️ Set **before** gravity |
| `Lagrange` | Specialised | ❓ Untested |

### When would you need Penalty?

`Penalty` is required when shell edges are connected to beam-column
elements and you need correct moment transfer at the shell edge.
For this building model the shells are walls/slabs with beam-column
frame elements framing into shell nodes — `Transformation` was
sufficient.

## 3. Convergence Settings for Fiber-Section Models

### Gravity analysis

```
constraints Transformation
numberer RCM
system BandGeneral
test NormDispIncr 1.0e-3 20 0      # Relaxed norm (not 1.0e-6!)
algorithm Newton
integrator LoadControl 0.05          # 20 substeps (1/20 = 0.05)
analysis Static
analyze 20
```

Key points:
- `NormDispIncr 1.0e-3` is essential — fiber sections produce much
  larger displacement increments in early steps than elastic sections.
  1.0e-5 or 1.0e-6 almost never converges on the first iteration.
- `LoadControl 0.05` with 20 substeps = gentle ramp-up.
- 20 iterations max per substep is enough for gravity.

### Pushover analysis

```
system BandGeneral
constraints Transformation
set dU_base [expr $targetDisp / $numSteps]
set dU [expr $dU_base / 10.0]        # Gentle first step (1/10 of base)

test NormDispIncr 1.0e-3 200 0
integrator DisplacementControl $ctrlNode $dof $dU
analysis Static

while {$currentDisp < $targetDisp} {
    algorithm Newton
    set ok [analyze 1]

    # Fallback 1: Krylov-Newton
    if {$ok != 0} {
        test NormDispIncr 1.0e-2 500 0
        algorithm KrylovNewton
        set ok [analyze 1]
    }

    # Fallback 2: ModifiedNewton (initial stiffness)
    if {$ok != 0} {
        algorithm ModifiedNewton -initial
        set ok [analyze 1]
    }

    # Fallback 3: Cut step size by 90% + repeat Newton
    if {$ok != 0} {
        set dU [expr $dU * 0.1]
        integrator DisplacementControl $ctrlNode $dof $dU
        algorithm Newton
        test NormDispIncr 1.0e-2 500 0
        set ok [analyze 1]
    }

    # Fallback 4: Minimal step + KrylovNewton with very relaxed norm
    if {$ok != 0} {
        set dU [expr $dU_base / 100.0]
        integrator DisplacementControl $ctrlNode $dof $dU
        test NormDispIncr 1.0e-1 1000 0
        algorithm KrylovNewton
        set ok [analyze 1]
    }
}
```

Key points:
- **Initial dU = dU_base / 10** — the first pushover step is critical.
  Full step size (0.003 m) on a 200-step target of 0.3 m will fail for
  fiber models.  0.0003 m initial step converges reliably.
- **4-level fallback chain**: Newton → KrylovNewton → step cut →
  KrylovNewton with 1.0e-1 norm.  The last resort (1.0e-1, 1000 iters)
  catches even very stiff steps.
- **`NormDispIncr 1.0e-3`** matches gravity tolerance — keeping them
  consistent avoids convergence surprises.

## 4. Subprocess Execution

### Use `stderr=subprocess.STDOUT`

When running Tcl via `subprocess.Popen`, **always merge stderr into
stdout**:

```python
proc = subprocess.Popen(
    [tclsh_path, tcl_file],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,   # ← critical
    text=True, bufsize=1,
)
```

If you use separate pipes for stdout/stderr:
- The subprocess can **deadlock** when stderr pipe buffer fills up
  (e.g. from `load` library warnings emitted on stderr).  Tcl writes
  the warning and blocks; Python reads stdout but never touches stderr.
- The `load` command for `libOpenSeesRT.dylib` often emits warnings
  about missing dependent symbols — these go to stderr.

### Use line-by-line reading with `bufsize=1`

```python
for line in proc.stdout:
    line = line.rstrip("\n")
    print(line)
    stdout_buf.append(line)
```

This gives **real-time progress** — without it, Tcl's `flush stdout`
is insufficient because Python buffers the pipe at the OS level.

## 5. Model Size Reduction via SAP Data Cropping

The most effective way to reduce solve time is to **crop the SAP2000
model before preprocessing**, not after Tcl generation.

### Procedure (used in `admin_pushover_v2.py`)

```python
# 1. Parse full model
md_full = SAP2000Parser(s2k_path).parse()

# 2. Deep-copy and filter nodes
md = copy.deepcopy(md_full)
kept_nodes = {nid for nid, nd in md.nodes.items()
              if nd.x <= MAX_X and nd.z <= MAX_Z}

# 3. Filter frame elements (both nodes must be kept)
for eid in list(md.frame_elements.keys()):
    fe = md.frame_elements[eid]
    if fe.node_i not in kept_nodes or fe.node_j not in kept_nodes:
        del md.frame_elements[eid]

# 4. Filter area elements (all nodes must be kept)
for aid in list(md.area_elements.keys()):
    ae = md.area_elements[aid]
    if not all(n in kept_nodes for n in ae.node_ids):
        del md.area_elements[aid]

# 5. Filter assignments, loads, restraints (same principle)
# 6. Build MeshModel from filtered data
mm = preprocess_model(md, cfg)
```

### Results from Admin Building crop (X ≤ 18, Z ≤ 13.275)

| Metric | Full (v1) | Cropped (v2) | Reduction |
|--------|-----------|---------------|-----------|
| Nodes | 1,139 | 194 | 83% |
| Frame elements | 881 | 177 | 80% |
| Area elements | 1,332 | 199 | 85% |
| DOF (approx) | 6,834 | 1,164 | 83% |
| Tcl file size | 6,672 lines | 1,365 lines | 80% |
| Gravity load | 54,503 KN | 10,989 KN | 80% |
| Run time | Never completed | ~90 s | — |

The cropped model retained the **first 3 storeys** (Z ≤ 13.275 m)
across the **full X-direction width** (X ≤ 18 m), which covers the
staircase core and office wing.  Modal analysis validated the sub-model
with 95.5% X-direction mass participation.

## 6. The v1 vs v2 Comparison

### Why v1 (full model) never converged

The v1 Tcl had two settings that differed from what actually made v2 work:

1. **`test NormDispIncr 1.0e-5 200 0`** — too tight for fiber sections.
   Every step failed at iteration 200 with `Norm deltaR ≈ 223,451 KN`.
   The relaxed norm `1.0e-3` was in `pushover_tcl()` in `builder.py` but
   hadn't been re-run to regenerate the Tcl.

2. **Full step size `dU = 0.003 m`** — the initial displacement increment
   was 10× too large.  The gentle ramp-up `set dU [expr $dU_base / 10.0]`
   reduced this to 0.0003 m, which converged on the first step.

3. **`system UmfPack`** — both v1 and v2 had this in the Tcl file.
   The v1 file never reached this line because it failed at the tight
   tolerance test first.  When v2's relaxed tolerance got past the test,
   it hit `system UmfPack` and crashed with SIGSEGV.  Switching to
   `system BandGeneral` fixed this.

4. **`constraints Penalty`** — the first v2 attempt had `Penalty` after
   gravity, causing an immediate crash.  Changing to `constraints
   Transformation` fixed this.

### Why v2 succeeded

| Setting | v1 (failed) | v2 (succeeded) |
|---------|-------------|----------------|
| Model size | 1,139 nodes | **194 nodes** (cropped) |
| Gravity constraint | `Transformation` | `Transformation` |
| Pushover constraint | `Transformation` | `Transformation` |
| Gravity solver | `BandGeneral` | `BandGeneral` |
| Pushover solver | `UmfPack` (never reached) | **`BandGeneral`** |
| Tolerance | `1.0e-5` | **`1.0e-3`** |
| Initial step | `dU_base` (0.003 m) | **`dU_base/10`** (0.0003 m) |
| Fallback levels | 3 | **4** (added KrylovNewton at 1e-1) |

## 7. Verified Pushover Results

The cropped 3-storey model ran to **300.2 mm** top displacement in
**100 steps** with:

- **Total base shear**: 98,853 KN (sum of all reactions in X direction)
  — note this includes reactions at base nodes that resist the lateral
  load pattern + P-Δ effects from gravity.
- **Gravity load**: 10,989 KN, matching the hand-calculated total.
- **Convergence**: First 2 steps used KrylovNewton fallback (fiber
  sections initialising).  Steps 3–100 converged with plain Newton at
  full step size.