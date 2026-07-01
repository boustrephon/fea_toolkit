# Xara Pushover Workflow — Nonlinear Analysis via Tcl

## Overview

This workflow generates and runs a displacement-controlled nonlinear pushover
analysis using **OpenSees compiled as a Tcl library** (`libOpenSeesRT.dylib`),
executed via Xara's standalone `tclsh8.6` interpreter.  The pipeline:

1. **Parse SAP2000 model** (`.s2k`) → `SAPModelData`
2. **Run precursor analyses** (static, modal, response-spectrum)
3. **Build in OpenSeesPy** (recorded via `RecordingOpenSees`)
4. **Post-process recorded commands** into valid Tcl
5. **Run via `XaraTclRunner`** (subprocess → `tclsh8.6`)
6. **Read results** (displacement + base shear)

## Prerequisites

| Component | Path |
|---|---|
| Tcl interpreter | `tclsh8.6` (via `XaraTclRunner.which_tclsh()`) |
| OpenSeesRT library | `libOpenSeesRT.dylib` (auto-detected from Xara/OpenSees install) |
| Builder module | `src/fea_toolkit/opensees/builder.py` |
| Recorder module | `src/fea_toolkit/opensees/recorder.py` |

## Precursor Analyses

Before running the pushover, the static analysis pipeline must be completed.
This establishes the baseline model state, computes seismic masses (used for
mass-proportional lateral loads), and provides modal/spectral context.

### A. Load and prepare the model

```python
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.sap_data import Restraint
from fea_toolkit.model.selection import Selection

parser = SAP2000Parser("Admin_building.s2k")
parser.parse()
md = parser.get_model_data()
```

**Key model-fix steps** (from `admin_linear.py`):

1. **Fix base restraints**: Shell-only base nodes (referenced only by area
   elements, not frame elements) lack drilling stiffness.  SAP2000 exports
   them as pinned `[1,1,1,0,0,0]` which creates a rotational mechanism when
   shells are present.  They must be changed to full fixity
   `[1,1,1,1,1,1]`:

```python
min_z = min(nd.z for nd in md.nodes.values())
base_ids = {nd.node_id for nd in md.nodes.values() if nd.z == min_z}
frame_conn = set()
for e in md.frame_elements.values():
    if e.node_i in base_ids: frame_conn.add(e.node_i)
    if e.node_j in base_ids: frame_conn.add(e.node_j)
for nid in sorted(base_ids - frame_conn):
    if nid in md.restraints:
        md.restraints[nid] = Restraint([1,1,1,1,1,1])
```

2. **Identify loads-only areas**: Only `"brick wall"` areas are non-structural
   (masonry infill).  Shear walls (`"Shear Wall"`) and slabs
   (`"concrete slabs"`) are structural and should create shell elements
   (when using OpenSeesPy with shell support):

```python
LOADS_ONLY = {"brick wall"}          # not {"Area"}

masonry_ids = {aid for aid in md.area_elements
               if md.area_assignments.get(aid, "") in LOADS_ONLY}
```

   Brick wall self-weight must be manually added to seismic mass (they are
   loads-only so no shell elements contribute their mass).

### B. Seismic mass computation

Before modal or pushover analysis, compute the seismic mass distribution
from the SAP2000 mass source (`MassSource`).  This is needed for both
the response-spectrum analysis and the mass-proportional lateral loads:

```python
b = OpenSeesBuilder(md, dict(...))
b.build(selection=sel)
masses = b.compute_seismic_masses()
total_mass = sum(masses.values())
```

### C. Linear static analysis (DEAD load)

Establishes baseline displacements, reactions, and self-weight consistency:

```python
b = OpenSeesBuilder(md, dict(
    element_type="elasticBeamColumn",
    split_elements=True,
    create_shells=True,
))
b.build()
sw = b.check_self_weight_consistency(verbose=False)
res = b.run_static_analysis(
    pattern_scales={"DEAD": 1.0},
    extract_reactions=True,
)
```

| Check | Expected | Notes |
|---|---|---|
| Self-weight | 51,818.5 kN (down) | Verified against SAP2000 |
| Max DEAD displacement | ~0.002 m | Elastic range |
| Base reactions | Equilibrium with applied loads | Sum Fz matches weight |

### D. Modal analysis

Extracts natural periods and mode shapes for spectral analysis and
pushover load distribution:

```python
b.compute_seismic_masses()
modal = b.run_modal_analysis(num_modes=12, print_results=False)
df = modal_table_enhanced(b, n_modes=12)
```

**Admin building modal results** (12 modes):

| Mode | Period (s) | Mass X (%) | Mass Y (%) | Participation |
|---|---|---|---|---|
| 1 | ~0.8 | 65% | 0% | X-translation |
| 2 | ~0.7 | 0% | 60% | Y-translation |
| 3 | ~0.5 | 5% | 20% | Torsion |
| 4+ | <0.3 | <5% | <5% | Higher |

The fundamental mode (~0.8 s) is used for the pushover load pattern.

### E. Response-spectrum analysis

Combines modal results via CQC to estimate seismic demands:

```python
from fea_toolkit.spectrum import _gb50011_spectrum
T_spec = list(np.linspace(0.01, 6.0, 600))
Sa_spec = list(_gb50011_spectrum(T_spec, alpha_max=alpha_max, tg=tg, g=9.81))
rs = b.run_response_spectrum_analysis(
    num_modes=modal["num_modes"],
    modal_periods=list(modal["periods"]),
    spectrum_periods=T_spec,
    spectrum_accels=Sa_spec,
)
```

### F. Self-weight consistency verification

After every build, verify that the total self-weight matches SAP2000:

```python
totals = b.check_self_weight_consistency()
assert abs(totals["total_model_weight"] - 51818.5) < 100, \
    f"Self-weight mismatch: {totals['total_model_weight']}"
```

This catches issues like:
- Split-element self-weight not being assigned to child elements (fixed in builder)
- Area self-weight on orphan nodes being lost (see Limitations)

## Pushover Step-by-Step
from fea_toolkit.model.selection import Selection

parser = SAP2000Parser("Admin_building.s2k")
parser.parse()
md = parser.get_model_data()

# Fix base restraints (shell-only base nodes)
min_z = min(nd.z for nd in md.nodes.values())
base_ids = {nd.node_id for nd in md.nodes.values() if nd.z == min_z}
frame_conn = set()
for e in md.frame_elements.values():
    frame_conn.update([e.node_i, e.node_j])
for nid in sorted(base_ids - frame_conn):
    if nid in md.restraints:
        md.restraints[nid] = Restraint([1,1,1,1,1,1])

# All areas loads-only (no shell elements)
sel = Selection(element_types=["Area"])
```

### 2. Build with recording — compute masses first

```python
import openseespy.opensees as _real_ops
import fea_toolkit.opensees.builder as builder_mod

# First build (real ops) to get seismic masses
b_tmp = OpenSeesBuilder(md, dict(
    element_type="dispBeamColumn",
    split_elements=True,
    create_shells=True,           # areas loads-only via selection
    create_fiber_sections=True,
    use_elastic_sections=False,   # required for fiber sections
))
b_tmp.build(selection=sel)
masses = b_tmp.compute_seismic_masses()
total_mass = sum(masses.values())
_real_ops.wipe()

# Second build with RecordingOpenSees to capture Tcl commands
from fea_toolkit.opensees.recorder import RecordingOpenSees

rec = RecordingOpenSees(_real_ops)
builder_mod.ops = rec
b = OpenSeesBuilder(md, dict(...))
b.build(selection=sel)
builder_mod.ops = _real_ops
```

### 3. Post-process recorded commands

Key transformations applied to the raw recording:

| Transformation | Why |
|---|---|
| **Pattern block grouping** | OpenSees Tcl requires `pattern Plain $tag $ts { load ... }` with braced body; recording captures flat commands |
| **Inline Lobatto syntax** | Xara/OpenSeesRT requires `element dispBeamColumn ... Lobatto $sec $n` — NOT separate `beamIntegration` command |
| **`numPts ≥ 2` clamp** | Lobatto integration rejects < 2 points |
| **Skip query commands** | `nodeCoord`, `getNodeTags`, etc. produce errors if nodes don't exist |
| **Filter orphan nodes** | Area-only nodes (no frame element connection) have zero stiffness; filter them and their loads |
| **`model BasicBuilder`** | Required by Xara's OpenSeesRT (equiv. to `model Basic`) |
| **`system ProfileSPD`** | `BandGeneral` and `UmfPack` segfault on large fiber-section models |

### 4. Generate lateral loads and pushover suffix

```python
# Mass-proportional lateral loads at 100% g
lat_scale = total_mass * 9.81 * 1.0
for nid, m in masses.items():
    fx = m / total_mass * lat_scale

# Pushover: DisplacementControl, adaptive step, base shear tracking
tcl_suffix = """
system ProfileSPD
numberer RCM
constraints Transformation

while {$currentDisp < $targetDisp} {
    test NormDispIncr 1.0e-5 200 0
    algorithm Newton
    integrator DisplacementControl $ctrl $dof $dU
    analysis Static
    set ok [analyze 1]
    # ... adaptive fallback to KrylovNewton + step-size reduction ...
    reactions                                      ← compute reactions
    foreach nid $baseNodes {                       ← sum base shear
        set baseShear [expr {$baseShear + [nodeReaction $nid 1]}]
    }
    puts $outfile "$currentDisp $baseShear"        ← write disp + baseV
}
wipe
exit
"""
```

### 5. Write and execute Tcl

```python
# Write complete Tcl file
with open("/tmp/admin_po.tcl", "w") as f:
    f.write(header + model_commands + pattern_blocks + suffix)

# Run via XaraTclRunner
from fea_toolkit.opensees.recorder import XaraTclRunner
runner = XaraTclRunner()
ret, stdout = runner.run("/tmp/admin_po.tcl", timeout=600.0)
```

### 6. Read results

```python
disps, shears = [], []
for line in Path(OUT_PATH).read_text().splitlines():
    parts = line.strip().split()
    if len(parts) >= 2:
        disps.append(float(parts[0]))
        shears.append(abs(float(parts[1])) / 1000)  # N → kN
```

## Key Differences from OpenSeesPy

| Aspect | OpenSeesPy | Xara Tcl |
|---|---|---|
| Element syntax | `beamIntegration + element` | **Inline**: `element ... Lobatto $sec $n` |
| Model init | `model('basic', ...)` | `model BasicBuilder -ndm 3 -ndf 6` |
| Pattern loads | `ops.load()` after `ops.pattern()` | `pattern Plain $tag $ts { load ... }` |
| Solver | `SystemBandGen` / `UmfPack` | **`ProfileSPD`** (others segfault) |
| Reactions | `ops.nodeReaction(...)` | `reactions` → `nodeReaction` |
| Units | Any (kN+m) | **Newtons** for `nodeReaction` output |

## Supported Features

| Feature | Status |
|---|---|
| Fiber sections (Concrete01 + Steel02) | ✅ |
| dispBeamColumn with Lobatto integration | ✅ |
| Mass-proportional lateral loads | ✅ |
| Displacement-controlled pushover | ✅ |
| Adaptive step-size (Newton → KrylovNewton) | ✅ |
| Base shear tracking | ✅ (N units) |
| Self-weight (frame) | ✅ |
| Area uniform loads → edge loads | ✅ |
| Area self-weight (filtered orphans ~3% loss) | ⚠️ |
| Shear walls (shell elements) | ❌ not supported in Xara |
| `geomTransf PDelta` | ⚠️ currently using `Linear` |

## Sample: Admin Building Pushover

Generated Tcl file: `/tmp/admin_po_xara.tcl` (147 KB, 4850 lines)

Structure:

```
Lines 1–3:     Header (load library, model BasicBuilder)
Lines 4–443:   Node definitions (443 nodes, minus 29 orphans)
Lines 444–530: Fixities
Lines 531–600: Materials (Concrete01 × 22, Steel02 × 11)
Lines 601–650: Fiber sections (11 × Fiber with patches + layers)
Lines 651–1900: Geometric transformations + elements (639 dispBeamColumn)
Lines 1901–4369: Load patterns (5 patterns, gravity + self-weight)
Lines 4370–4500: Gravity lock + lateral load pattern
Lines 4501–4850: Pushover analysis (adaptive while-loop)
```

Performance: **500 steps, ~60 seconds**, all Newton-converged, 50% stiffness loss.

## Pushover Curve

![Admin building pushover curve](/tmp/admin_po_curve.png)

| Metric | Value |
|---|---|
| Initial stiffness | 413,000 kN/m |
| Final stiffness (0.5m) | 204,000 kN/m |
| Stiffness loss | 50.6% |
| Max base shear | 102,000 kN (~1.76g) |
| Steps | 500 |
| Convergence | Newton (no KrylovNewton fallback needed) |

## Limitations

1. **No shell elements** — Xara's OpenSeesRT doesn't support `ElasticMembranePlateSection`
   or any shell element type. Shear walls and slabs must be modeled via equivalent
   frame elements or omitted (frame-only conservative).
2. **Area self-weight loss** — ~3% of area self-weight (~1,570 kN) goes to orphan
   (area-only) nodes and is filtered.  Redistribution to frame-connected nodes
   would fix this.
3. **`nodeReaction` in N** — reaction values are in Newtons regardless of model
   units (kN).  Factor of 1000 correction needed.
4. **`ProfileSPD` required** — `BandGeneral` and `UmfPack` segfault on large
   fiber-section models.
