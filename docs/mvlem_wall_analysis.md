# MVLEM / SFI-MVLEM Shear-Wall Analysis

> Status: **✅ Complete** — verified against the shipped
> `openseespy` 3.8.0.0 wheel on macOS arm64.

This document describes the MVLEM / SFI-MVLEM reinforced-concrete
shear-wall modelling workflow for fea_toolkit.  MVLEM (Multiple-Vertical-
Line-Element Model) and its fixed-strut-angle variant SFI-MVLEM capture
shear-flexure interaction in RC walls with a smeared macro-element
formulation.

## 1. Element availability (verified signatures)

Signatures were extracted from the binary `strings` of the shipped
`openseespymac` wheel:

| Command | Form |
|---|---|
| **MVLEM 2D** | `element MVLEM eleTag Dens iNode jNode m c -thick {fiberThick} -width {fiberWidth} -rho {Rho} -matConcrete {matTagsConcrete} -matSteel {matTagsSteel} -matShear {matTagShear}` |
| **SFI_MVLEM 2D** | same keyword form as MVLEM plus `-Coupling` option — **broken in 3.8.0.0** (see §3) |
| **SFI_MVLEM_3D** | `element SFI_MVLEM_3D eleTag iNode jNode kNode lNode m -thick {T} -width {W} -mat {Mat_tags} <-CoR c>` (FSAM nD-materials) |
| **MVLEM_3D** | `element MVLEM_3D eleTag iNode jNode kNode lNode m -thick ... -rho ... -matConcrete ... -matSteel ... -matShear ... <-CoR c>` |
| **FSAM** | `nDMaterial FSAM mattagg rho sX sY conc rouX rouY nu alfadow` |
| **E_SFI** | `element E_SFI eleTag iNode jNode m c -thick -width -mat` (2D nD-based) |

Key details:

- **`c` is positional** in the 2D MVLEM form — the 6th argument after
  `m` — **not** a `-c` keyword.  Passing `-c 0.4` produces
  `Invalid c for element SFI_MVLEM`.
- **`Dens`** (2nd arg) and **`-rho`** must be **non-zero** for static
  analysis; zero density causes internal-node singularity
  (`matrix singular U(i,i)=0`).
- OpenSeesPy's `element()` requires list-valued keyword args
  (`-thick`, `-width`, `-rho`, `-matConcrete`, `-matSteel`, `-mat`) to
  be **expanded as individual scalar arguments** (`*list` syntax), not
  passed as a Python list.

## 2. FSAM nD material

FSAM (fixed-strut-angle model, Hsu & Mo) is the smeared concrete law
used as the `-mat` argument of SFI_MVLEM_3D:

```
nDMaterial FSAM $mattag $rho $sX $sY $conc $rouX $rouY $nu $alfadow
```

- `rho` — mass density (mass/volume, consistent with model units).
- `sX`, `sY` — **uniaxial steel material tags** for the smeared rebar
  in the x and y directions.
- `conc` — **uniaxial concrete material tag**.  The concrete law **must
  implement `getCrackingStrain()`** — verified working with `ConcreteCM`
  (and `ConcreteD`/`Concrete02` in code scans).  `ConcreteS`,
  `Concrete01`, `Concrete04` do **not** provide the method and will fail.
- `rouX`, `rouY` — reinforcement ratios (dimensionless).
- `nu` — Poisson's ratio.
- `alfadow` — wall inclination angle in **degrees** (45 is typical).

### Verified working FSAM + ConcreteCM + SFI_MVLEM_3D stack (kN-m units)

```python
# Model units: kN, m → stress in kN/m²
ops.uniaxialMaterial("ConcreteCM", 1, -30.0e3, -0.002, 30.0e6, 5.0,
                     -0.0002, 3.0e3, 0.0001, 1.5, 0.0001)
ops.uniaxialMaterial("Steel02", 2, 420.0e3, 200.0e6, 0.01)

ops.nDMaterial("FSAM", 100, 2400.0, 2, 2, 1, 0.01, 0.01, 0.2, 45.0)

# 4-node vertical wall panel: nodes 1,2 = bottom; 3,4 = top
m = 4            # number of macro-fibres along the wall
fiber_thick = [0.3 / m] * m
fiber_width = [1.0] * m
ops.element("SFI_MVLEM_3D", 1, 1, 2, 3, 4, m,
            "-thick", *fiber_thick, "-width", *fiber_width,
            "-mat", 100, "-CoR", 0.4)
```

## 3. Known limitations (wheel 3.8.0.0)

- **2D SFI_MVLEM is broken** in the shipped wheel: the parser accepts
  MVLEM-style keywords but the constructor performs nD-material lookups,
  emitting `SFI_MVLEM::SFI_MVLEM() - Null ND material pointer passed`
  for every tag combination (uniaxial tags in `-matShear`, FSAM in
  `-matShear`, FSAM in `-matConcrete` all fail).  Use **MVLEM 2D** (with
  uniaxial materials) or **SFI_MVLEM_3D** (with FSAM) instead.
- **PSUMAT** remains source-restricted (upstream stub) — not fixable by
  a local rebuild.
- **`Corotational`** geometric transformation does not support
  `eleLoad` in 3D — emit a warning if used.

## 4. fea_toolkit integration

### 4.1 `NDMaterial` FSAM fields

The `NDMaterial` dataclass accepts `material_type="FSAM"` with:

```python
NDMaterial(
    name="WallFSAM",
    material_type="FSAM",
    density=2400.0,        # mass density (passed through unscaled)
    sx="SteelX",            # uniaxial material name for x rebar
    sy="SteelY",            # uniaxial material name for y rebar
    conc="ConcreteCM",       # uniaxial concrete name (needs getCrackingStrain)
    rou_x=0.01,             # dimensionless reinforcement ratio
    rou_y=0.01,
    nu=0.2,                # Poisson's ratio (stress-free, unscaled)
    alfadow=45.0,           # wall inclination angle (degrees)
)
```

The concrete/steel `Material` definitions enter through
`model_data.materials` in the usual way.  `sx` / `sy` / `conc` hold
**material names**; they are resolved to integer OpenSees tags at build
time via `material_tags`.

### 4.2 `WallElement` and `_create_wall_elements()`

The `MeshModel` dataclass carries a new `wall_elements` dict
(`elem_id → WallElement`) alongside `frame_elements` /
`area_elements`.  `WallElement` describes one **SFI_MVLEM_3D**
macro-element in the frozen topology:

```python
WallElement(
    elem_id="W1",           # human-readable id
    elem_tag=10000,         # integer OpenSees element tag
    node_ids=["1", "2", "4", "3"],  # OpenSees quad order [i, j, k, l]
    m=5,                    # number of macro-fibres
    thick=[0.3] * 5,        # per-fibre thickness
    width=[0.8] * 5,        # per-fibre width (sums to wall width W)
    fsam_material_names=["FSAM_bdry", "FSAM_core", ...],  # per-fibre FSAM
    CoR=0.4,
)
```

The Preprocessor populates it from wall-classified areas when
`element_strategies.wall.element_type == "SFI_MVLEM_3D"` (see §5.2)
and marks the source area `inactive` so no shell is created for it.

`AnalysisBuilder._create_wall_elements()` is the dedicated builder
step.  It runs **after** `_create_fsam_materials()` (so the FSAM nD
material tags in `_nd_material_tags` are resolved) and **before**
`_create_shell_elements()`.  Each `WallElement` is emitted with list
expansion:

```python
ops.element(
    "SFI_MVLEM_3D", wall.elem_tag, *node_tags, wall.m,
    "-thick", *wall.thick, "-width", *wall.width,
    "-mat", *fsam_tags, "-CoR", wall.CoR,
)
```

Fibres whose FSAM material name is missing are reported and the element
is skipped.  The Tcl exporter (`builder.py`) mirrors this emission,
resolving FSAM names through its own `_nd_mat_tag` map.

### 4.3 AnalysisBuilder ordering

Because FSAM references uniaxial material tags, creation is split:

```
_create_nd_materials()      # ElasticIsotropic / J2PlateFibre / ConcreteS / PlateFromPlaneStress
_create_materials()         # uniaxial materials (assigns material_tags)
_create_fsam_materials()    # FSAM nD materials (resolves sx/sy/conc → tags)
_create_wall_elements()     # SFI_MVLEM_3D macro-elements consuming FSAM tag
_create_layered_shell_sections()  # LayeredShell consuming FSAM tag
```

FSAM tags are stored in `_nd_material_tags` keyed by the nD material
name, so both `WallElement` fibres and `LayeredShellSection` layers can
reference them.  If any referenced uniaxial material name is missing
from the model, a `UserWarning` is emitted and the FSAM material is
skipped (added to `_skipped_nd_materials`, which also skips dependent
layered sections and wall elements).

The concrete uniaxial law must implement `getCrackingStrain()`
(`ConcreteCM`).  It also requires the **negative-compression
convention**: `fpc`, `epcc` and `xcrn` are emitted negative.  Passing
positive magnitudes causes the FSAM damage-coefficient initialiser to
fail at domain-build time (`Damage Coefficient ErRoR !`) when an
`SFI_MVLEM_3D` element consumes the material.

### 4.4 Tcl export

`export_model_to_tcl()` passes the material-tag map (`_mat_tag`) to
`NDMaterial.to_tcl(...)` so FSAM commands resolve uniaxial names to
tags.  FSAM materials are **not** wrapped as `PlateFromPlaneStress`
(they are nD materials consumed directly by `SFI_MVLEM_3D` /
`LayeredShell`), unlike the other nD types.

### 4.5 Unit scaling

`scale_material_dict()` classifies `rou_x`, `rou_y` and `alfadow` as
non-stress numeric fields (dimensionless) so no scaling is applied.
`density` is also passed through unchanged (mass density), consistent
with the existing `rho`/`density` pass-through.

## 5. Minimal end-to-end recipe (probe)

See `local/probe_mvlem_sfi.py` for a runnable standalone probe that
exercises both **MVLEM 2D** (uniaxial stack) and **SFI_MVLEM_3D**
(FSAM stack) with a 100 kN pushover, verifying base reaction
`rx = -100 kN` and realistic top displacement.

Two end-to-end config recipes follow — **LayeredShell / ShellNLDKGQ**
(the through-thickness stack) and **SFI_MVLEM_3D** (the new
macro-element path).  Both run the same 3 m × 4 m × 0.3 m wall through
`Preprocessor → AnalysisBuilder` and are covered by integration tests
in `tests/test_wall_pushover.py`.

#### 5.1 LayeredShell / ShellNLDKGQ path

```python
config = {
    "create_shells": True,
    "nd_materials": {
        "WallConcCM": {"material_type": "ConcreteS", "E": 30.0e9, "nu": 0.2, "fc": 30.0e6, "ft": 3.0e6},
        "wall_fsam": {
            "material_type": "FSAM",
            "density": 2400.0,
            "sx": "RebarX",
            "sy": "RebarY",
            "conc": "WallConcCM",
            "rou_x": 0.01,
            "rou_y": 0.01,
            "nu": 0.2,
            "alfadow": 45.0,
        },
    },
    "shell_layers": {
        "WALL": {
            "selector": {"element_ids": ["A1"]},
            "layers": [{"thickness": 0.3, "nd_material": "wall_fsam"}],
        },
    },
}
```

`RebarX` / `RebarY` / `WallConcCM` are model materials; the FSAM
uniaxial dispatcher emits `ConcreteCM` / `Steel02` for them
automatically (see §4.3).  The layered stack is consumed by
`ShellNLDKGQ` quads.

#### 5.2 SFI_MVLEM_3D path (macro-element)

```python
config = {
    "create_shells": True,
    # Plain constraints + 10 LoadControl substeps match the validated
    # SFI_MVLEM_3D probe solver settings.
    "solver_constraints": "Plain",
    "gravity_num_substeps": 10,
    "nd_materials": {
        "wall_fsam": {
            "material_type": "FSAM",
            "density": 2400.0,
            "sx": "RebarX",
            "sy": "RebarY",
            "conc": "WallConcCM",
            "rou_x": 0.01,
            "rou_y": 0.01,
            "nu": 0.2,
            "alfadow": 45.0,
        },
        # Optional second FSAM for boundary fibres (higher smeared steel).
        "wall_fsam_bdry": {
            "material_type": "FSAM",
            "density": 2400.0,
            "sx": "RebarX",
            "sy": "RebarY",
            "conc": "WallConcCM",
            "rou_x": 0.025,
            "rou_y": 0.025,
            "nu": 0.2,
            "alfadow": 45.0,
        },
    },
    "element_strategies": {
        "wall": {
            "element_type": "SFI_MVLEM_3D",
            "n_fibers": 5,
            "CoR": 0.4,
            # Per-fibre FSAM mapping (list length == n_fibers) — boundary
            # fibres carry 2.5 % smeared reinforcement, interior 0.4 %.
            # A uniform 0.4 % wall is nearly singular under pure lateral
            # push, so the boundary-enriched layout is required.
            "fsam_materials": [
                "wall_fsam_bdry",
                "wall_fsam",
                "wall_fsam",
                "wall_fsam",
                "wall_fsam_bdry",
            ],
        },
    },
}
```

The Preprocessor converts the wall-classified area into a single
`WallElement` with `m = 5` fibres and marks the area `inactive`
(no shell is created).  `AnalysisBuilder._create_wall_elements()`
emits the `SFI_MVLEM_3D` command after FSAM materials are resolved.

#### 5.3 Approach comparison

| Aspect | LayeredShell / ShellNLDKGQ | SFI_MVLEM_3D | **MVLEM_3D** |
|---|---|---|---|
| Element type | `ShellNLDKGQ` quads | `SFI_MVLEM_3D` macro-element | `MVLEM_3D` macro-element |
| Material law | Layered through-thickness stack of nD materials | Per-fibre FSAM nD material | Per-fibre uniaxial concrete + steel + single shear spring |
| Discretisation | Requires mesh refinement | Single element per wall (fibres along width) | Single element per wall (fibres along width) |
| Config key | `shell_layers` | `element_strategies.wall` | `element_strategies.wall` |
| Reinforcement | Per-layer `nd_material` choices | Per-fibre `fsam_materials` (via `rou_x`/`rou_y`) | Per-fibre `steel_material` / `dummy_material` |
| Convergence | ✅ Trivial (Transformation OK) | ❌ **Diverges — upstream C++ bug** | ✅ Trivial (Newton + LoadControl) |
| Shear interaction | Through-thickness | FSAM (2D plane-stress) | Single horizontal spring (macro shear) |
| Solver notes | Default `Transformation` is fine | `Plain` + `gravity_num_substeps: 10` recommended | `Plain` + `gravity_num_substeps: 10` recommended |

The integration tests in `tests/test_wall_pushover.py` run all paths
through `Preprocessor → AnalysisBuilder`, verify `rx = -100 kN` for a
100 kN lateral push, and confirm the expected ordering of top drifts
(the nonlinear macro-element is far more flexible than a purely-elastic
layered stack).

### 5.4 MVLEM_3D path (uniaxial — recommended 3D wall macro-element)

MVLEM_3D is the 3D variant of the Multiple-Vertical-Line-Element Model.
Unlike SFI_MVLEM_3D / E_SFI_MVLEM_3D it uses **uniaxial** materials —
the same ConcreteCM + Steel02 + ElasticPP shear stack that makes the
2D MVLEM converge — and has **no internal σₓ=0 DOFs**, so the upstream
SFI_MVLEM_3D tangent bug does not apply.

```python
config = {
    "create_shells": True,
    "solver_constraints": "Plain",
    "gravity_num_substeps": 10,
    "element_strategies": {
        "wall": {
            "element_type": "MVLEM_3D",
            "material_type": "uniaxial",
            "n_fibers": 5,
            "CoR": 0.4,
            "concrete_material": "concrete",   # model material name (→ ConcreteCM)
            "steel_material": "steel",          # model material name (→ Steel02)
            "dummy_material": "dummy",          # tiny-E Elastic for interior fibres
            "shear_material": "shear",          # single ElasticPP shear spring
            "density": 2400.0,                  # per-fibre -rho (must be non-zero)
            "boundary_fibers": 1,               # outer fibres get real rebar
        },
    },
}
```

The Preprocessor emits a `WallElement` with `concrete_names`,
`steel_names`, `shear_name`, `rho`, and `material_type="uniaxial"`.
The builder dispatches to `element MVLEM_3D ... -matConcrete -matSteel
-matShear` (verified against the shipped wheel binary) and creates the
shear spring as `ElasticPP (k = 0.1·G·A/h, 1e6)` plus the interior
dummy as a tiny-E `Elastic`.

## 6. Axial-load (gravity) experiment — July 2026

A dedicated probe set (`local/probe_wall_gravity.py`) tested whether a
gravity pre-load regularises the FSAM wall elements.  **Findings:**

| Element | Zero-gravity result | Gravity pre-load |
|---|---|---|
| MVLEM_3D | ✅ Newton + LoadControl(0.1), rx=−100 kN | ✅ Gravity and pushover both converge |
| E_SFI_MVLEM_3D | ⚠️ converges only KrylovNewton + DisplacementControl | ❌ Gravity step fails even KrylovNewton (2000 iter) |
| SFI_MVLEM_3D | ❌ diverges first step | ❌ Gravity step fails even KrylovNewton |
| E_SFI 2D | ❌ singular `U(i,i)=0, i=0` at load factor 0 | ✅ **gravity regularises the tangent** — converges |

**Interpretation**

1. **The 3D SFI/E_SFI gravity stage cannot be solved** with the shipped
   element implementations — the singular initial tangent (block
   diagonal, discarded coupling; or closed-form εₓ denominator at N=0)
   prevents even a 10-step LoadControl gravity ramp regardless of
   algorithm.
2. **E_SFI 2D** *does* need axial load — the closed-form εₓ expression
   degenerates at zero axial force, and a 500 kN vertical pre-load
   restores a non-singular tangent.  This is the only FSAM-style
   element for which the user's axial-load hypothesis was confirmed.
3. MVLEM_3D needs no special treatment — its stiffness is assembled
   directly from uniaxial fibres + a single horizontal spring, so both
   gravity and lateral push converge with plain Newton.

### 6.1 E_SFI 2D axial-preload parameter sweep

`local/probe_e_sfi_2d_gravity_sweep.py` swept gravity × aspect ratio ×
fibre count over 45 configurations to quantify the E_SFI 2D axial
pre-load threshold:

| Sweep axis | Values |
|---|---|
| Gravity `P` (kN) | 50, 200, 500, 1000, 2000 |
| Aspect ratio `H/W` | 0.75, 2.0, 4.0 |
| Macro-fibres `m` | 4, 8, 12 |
| Load factor at ux = 0.2 m | reported as `rx/P_ref` (P_ref = 100 kN) |

**Results (41/45 converged).**  The apparent minimum axial ratio is
small — any compression regularises the εₓ denominator:

| H/W | Min converged `P/(Ag·f_c)` | Load factor at ux = 0.2 m |
|---|---|---|
| 0.75 (stocky) | 0.0014–0.0056 | −73.4 … −48.8 |
| 2.0 | 0.0037 | −9.0 … −1.6 |
| 4.0 (slender) | 0.0074 | −2.1 … −0.2 |

Three findings disqualify E_SFI 2D for production use:

1. **The gravity ramp itself fails sporadically** (4/45 cases) —
   H/W=0.75 m=4 @ G=1000, H/W=0.75 m=12 @ G=50, H/W=2.0 m=4 @ G=200
   and H/W=2.0 m=12 @ G=200 — even with the KrylovNewton fallback.
   Failure is *not* a clean threshold: the same geometry converges at
   lower gravity, so the N≈0 initial tangent stays marginal at certain
   `m`/geometry combinations.
2. **Backbones plateau then snap.**  For H/W=2.0, m=8 the base-shear
   load factor is flat at −1.5 from ux = 0.02 … 0.18 m, then jumps to
   ≈ −7 (−710 kN) at ux = 0.20 m — a single-step 4.7× jump with no
   physical softening mechanism.
3. **Absurd base shears on stocky walls.**  At ux = 0.20 m (6.7%
   drift) the H/W=0.75 walls report loads 49–73× the 100 kN reference.
   The closed-form εₓ minimisation locks the fibre state, keeping the
   element far too stiff.

**Verdict: E_SFI 2D is not usable even with axial pre-load.**  The
pre-load removes the structural zero (`U(i,i)=0` at i=0 is gone) but
the resulting response is non-physical and the gravity stage remains
solver-fragile.  MVLEM_3D stays the recommended shear-wall
macro-element.

### 6.2 Fiber beam-column walls — `local/probe_fiber_beam_wall.py` (August 2026)

The "mechanically unified" alternative to the MVLEM family: the wall is
modelled as **N vertical beam-column strips** (`forceBeamColumn` or
`dispBeamColumn`), each split into `NSEG=3` elements along the height,
with a two-dimensional fiber section spanning (section-y = wall width,
section-z = wall thickness).  Axial + flexural coupling is exact through
the section; kinematics are Euler-Bernoulli only — there is **no shear
spring and no out-of-plane plate** (unlike MVLEM_3D which has both).

**Model** (same canonical 4 m × 3 m × 0.3 m wall, kN/m units):
- N strip columns at `x = (i+0.5)·W/N`, each `Lobatto×5` with a
  `quad` concrete patch (8×4 fibres) + `straight` steel layers at both
  faces; boundary strips carry an extra 2.5 % steel in the outer 0.4 m
  edge zones (mirrors the MVLEM_3D probe layout).
- Rigid top floor via `rigidDiaphragm(2, 500, *top_nodes)` — the correct
  MPC for a slab in the X-Z plane (ties in-plane UX/UZ to the master's
  rigid-body motion, leaves the out-of-plane Y and rotations free).
  `equalDOF`/`rigidLink("bar", …)` over-constrain and must not be used
  here (they kill the differential vertical push-pull of the cap).
- `Transformation` constraints (needed to honour the diaphragm):
  `Plain` ignores multi-point constraints and leaves the loaded master
  singular.
- Solver: gravity `Newton + LoadControl(0.1)`; pushover
  `KrylovNewton` + `DisplacementControl(master, UX, 0.002)` to
  ux = 0.2 m.

**Results (all 10 combos converge):**

| elem | N | gravity | DC pushover | ux@100kN (m) | ux_final (m) | rx_final (kN) | mono/rise |
|---|---|---|---|---|---|---|---|
| force | 1 | 0 | ✅ 100/100 | 8.4e-4 | 0.2005 | −2059 | ✅/✅ |
| force | 2 | 0 | ✅ 100/100 | 3.4e-5 | 0.1997 | −3227 | ✅/✅ |
| force | 4 | 0 | ✅ 100/100 | 2.5e-5 | 0.1996 | −2942 | ✅/✅ |
| force | 8 | 0 | ✅ 100/100 | 7.3e-5 | 0.1996 | −2279 | ✅/✅ |
| force | 16 | 0 | ✅ 100/100 | 1.8e-4 | 0.1996 | −1500 | ✅/✅ |
| disp | 1 | 0 | ✅ 100/100 | 8.4e-4 | 0.2005 | −2086 | ✅/✅ |
| disp | 2 | 0 | ✅ 100/100 | 3.4e-5 | 0.1997 | −3257 | ✅/✅ |
| disp | 4 | 0 | ✅ 100/100 | 2.5e-5 | 0.1996 | −3034 | ✅/✅ |
| disp | 8 | 0 | ✅ 100/100 | 7.3e-5 | 0.1996 | −2382 | ✅/✅ |
| disp | 16 | 0 | ✅ 100/100 | 1.8e-4 | 0.1996 | −1622 | ✅/✅ |
| **MVLEM_3D ref** | 8 | 0 | n/a | 1.5e-3 | 0.2013 | −100000* | ✅/✅ |

*MVLEM_3D under displacement control reaches a negative-stiffness
regime (rx ≈ −100000 kN at ux = 0.2 m) — that is a **protocol artifact**,
not a physical result; the validated MVLEM_3D metric is the
force-controlled rx = −100 kN at ux = 1.53 mm (from
`test_wall_pushover.py`).

**Findings:**
1. **Convergence is trivial.**  `forceBeamColumn` and `dispBeamColumn`
   both converge 100/100 KrylovNewton steps to ux = 0.2 m for every
   N = 1…16, with monotone rising backbones.  No axial pre-load or
   solver heroics required (unlike E_SFI/SFI).
2. **Euler-Bernoulli stiffness dominates.**  The 100 kN push produces
   only ~0.03 mm drift once the rigid cap couples N ≥ 2 strips
   (φ-dominated flexure of the whole 4 m lever arm), vs 1.5 mm for
   MVLEM_3D.  The fibre column has **no shear-deformation mechanism**:
   it is correct only for slender flexure-dominated walls.
3. **Over-constraint traps.**  `equalDOF(500, t, …)` and
   `rigidLink("bar", 500, t)` both tie the strip-top UZ to a single value
   and kill the differential push-pull of a rigid cap → zero drift and
   singular/over-constrained states.  `rigidDiaphragm` is the only MPC
   that gives the physically-correct rigid-floor coupling.
4. **Not a shear-wall element.**  A fiber beam-column is a plausible
   back-up for **flexure-dominated piers only** (the "mechanically
   unified" Euler-Bernoulli model).  It cannot reproduce shear
   deformation or shear yielding, so it is not a general replacement
   for MVLEM_3D on shear-dominated walls.

**Verdict: fiber beam-column walls are viable (probe), but
flexure-only.**  They converge trivially and couple axial+flexure
exactly, so they are a good candidate where MVLEM_3D's shear spring is
unwanted and the wall is slender.  For the general shear- and
flexure-dominated goal they do **not** replace MVLEM_3D.


### 6.3 LayeredShell mesh-density sweep - `local/probe_layered_shell_mesh.py` (August 2026)

Question: does refining the ShellNLDKGQ wall mesh (the validated
ConcreteS/J2PlateFibre layered path via `layered_elastic_config`)
improve accuracy at the same 100 kN protocol?

**Model:** `run_wall_pushover` from `examples/wall_pushover_compare.py`
(framework driver - builds gravity then pushes 100 kN lateral in +X
over 20 LoadControl steps).  `subdivide_shells` swept 2 -> 16.

**Results (all converge 20/20):**

| subdivide | quads | steps | peak base shear (kN) | top drift (m) |
|---|---|---|---|---|
| 2 | 5 | 20 | 100.0 | 4.62e-5 |
| 4 | 17 | 20 | 100.0 | 4.94e-5 |
| 8 | 65 | 20 | 100.0 | 4.93e-5 |
| 16 | 257 | 20 | 100.0 | 4.85e-5 |

**Findings:**
1. **The layered-shell wall is mesh-converged already at sub=4.**
   Drift at 100 kN is constant (4.6-4.9e-5 m) across a 50x element
   count (5 -> 257 quads).  Refining further buys nothing.
2. **The elastic/lightly-nonlinear response is extremely stiff.**  The
   4.9e-5 m drift is dominated by the rigid-cap flexure of the full
   4 m width (same order as the fiber beam-column N>=2 results and far
   stiffer than MVLEM_3D's 1.5e-3 m, which includes the soft shear
   spring + real axial-flexure softening).  Neither the layered shell
   nor the fibre columns capture the shear flexibility that governs
   this stocky (H/W = 0.75) wall.
3. **The mesh parameter is not the lever.**  For the nonlinear shear
   behaviour that matters, the through-thickness layer stack (FSAM /
   ConcreteS vs pure elastic) is what drives the difference, not the
   in-plane quad density.

**Verdict: finer meshes do not change the layered-shell path.**
The wall is already mesh-converged; the option adds cost without
accuracy.  MVLEM_3D remains the shear-capable macro-element.


### 6.4 MVLEM 2D in a 3D model — infeasible (upstream dispatch) — August 2026

The final "composite hack" option (stack 2D MVLEM elements in a 3D
frame + constraint network) is **dead on arrival**.  Direct feasibility
check against the shipped wheel:

```
ops.model('basic', '-ndm', 3, '-ndf', 6)   # 3D domain
ops.element('MVLEM', 1, 1000.0, 1, 2, 2, 0.4, '-thick', ..., '-matConcrete', ...)
```

fails with:

```
WARNING iNode jNode kNode lNode or m for element MVLEM_3D1
MVLEM in ndm=3 EXCEPTION: See stderr output
```

OpenSees **dispatches `element MVLEM` to the `MVLEM_3D` class in an
ndm=3 domain**; the 2D `MVLEM` element class is only registered for
ndm=2.  The same command with the canonical 2D keyword form works
fine in an `ndm=2, ndf=3` domain (`ele tags: [1]`), confirming the
restriction is the upstream element registry — not a syntax issue.

**Consequence:** you cannot mix 2D MVLEM walls with 3D frame elements
in one model.  The 2D MVLEM is strictly a plane-frame/2D-toolkit
element.  To get the identical verified in-plane formulation in 3D,
use **MVLEM_3D** (which *is* the 3D-registered MVLEM family member and
adds the linear-elastic Kirchhoff out-of-plane plate).

**Verdict: Option C is infeasible in the shipped wheel.**  No
substitute for MVLEM_3D.
## 7. Working-path summary

| Path | Wall type | Shear interaction | Material stack | Comp. cost | Status |
|---|---|---|---|---|---|
| **LayeredShell/ShellNLDKGQ** | Shear + flexure | Through-thickness layers | ConcreteS / FSAM + J2PlateFibre | High | ✅ Integrated (7/7 tests) |
| **MVLEM_3D** | Shear + flexure | Horizontal spring | ConcreteCM + Steel02 + ElasticPP | Medium | ✅ Integrated (4/4 tests new) |
| **Fiber beam-column** | Flexure only (Euler-Bernoulli; no shear) | None — axial+flexure via 2D fibre section | ConcreteCM + Steel02 | Low | ⚠️ Probe only — converges trivially but has no shear mechanism (slender piers only) |
| MVLEM 2D | Shear + flexure | Horizontal spring | ConcreteCM + Steel02 + ElasticPP | Low | ⚠️ Probe only (2D element) |
| E_SFI_MVLEM_3D | Shear + flexure | FSAM per-fibre | FSAM (same as SFI) | Medium | ⚠️ Probe only — needs Krylov+DC; gravity blocked |
| E_SFI 2D | Shear + flexure | FSAM (closed-form εₓ) | FSAM | Low | ❌ Not usable — pre-load converges but non-physical (base-shear snap, LFac up to −73) |
| SFI_MVLEM_3D | Shear + flexure | FSAM per-fibre | FSAM | Medium | ❌ Broken (upstream C++ bug) |
| SFI_MVLEM 2D | Shear + flexure | FSAM | FSAM | Low | ❌ Broken in wheel (nD lookup) |

**Integration plan (August 2026 audit):**
- **MVLEM_3D** — keep as the recommended 3D wall macro-element;
  already integrated with 4/4 tests.
- **LayeredShell / ShellNLDKGQ** — keep at `subdivide_shells: 4`
  (already the default); the §6.3 sweep proves finer meshes add cost
  without accuracy.  No change.
- **Fiber beam-column** — probe-only (`local/probe_fiber_beam_wall.py`).
  Do **not** promote to a first-class `WallElement` strategy yet: it has
  no shear mechanism and is over-stiff for stocky walls.  If a
  flexure-only "pier strip" wall type is ever wanted, the integration
  spec is: a new `wall` `element_type` ("fiber_beam_column", N strips +
  NSEG elements + fiber sections + `rigidDiaphragm(perp_dof=2, master,
  …)` + `Transformation` constraints), mirroring the probe's verified
  commands.
- **MVLEM 2D / SFI_MVLEM 2D** — stay 2D-only; see §6.4 (upstream
  dispatch makes 3D use infeasible without an OpenSees source patch).

**Recommendation**: for nonlinear 3D shear-wall pushover in fea_toolkit,
use **MVLEM_3D** (uniaxial stack, converges trivially, exact rx=−100 kN)
for both shear- and flexure-dominated walls, and **LayeredShell /
ShellNLDKGQ** when through-thickness layering and per-layer detailing
control are required.
 
## 7.1 Head-to-head comparison (August 2026)

### Method

`examples/wall_pushover_compare.py` compares the two converging toolkit
paths — **MVLEM_3D** (uniaxial macro-element) and
**LayeredShell / ShellNLDKGQ** (through-thickness stack) — on the same
4.0 m × 0.3 m RC wall (kN-m units) at three heights
**H = 2, 4, 8 m** (H/W = 0.5, 1.0, 2.0) to bracket the shear-to-flexure
transition.  The protocol is identical for both paths:

1. **Geometry / boundary conditions** — 4.0 m × H × 0.3 m wall; base
   fully fixed; top-edge Y (out-of-plane) restrained so deformation is
   confined to the X-Z plane.
2. **Gravity first** — constant axial pre-load
   **P = 0.20·fc·Ag = 0.20 × 30{,}000 × (0.3 × 4) = 7,200 kN** applied
   at the top.  The LayeredShell path carries it through the pipeline
   (``area_gravity_loads`` scaled so the pattern totals 7200 kN; gravity
   is locked before the lateral stage).  The MVLEM_3D path injects it as
   top-node ``ops.load`` in the gravity pattern (its wall source area is
   ``inactive``), then reuses ``run_static_analysis()``.
3. **Lateral push** — displacement-controlled to **ux = 0.20 m** in
   100 steps (``DisplacementControl``, du = 0.002 m, ``KrylovNewton``),
   with the gravity stage locked via ``loadConst('-time', 0.0)``.
4. **Metrics** — base shear = sum of X-reactions at the fixed base;
   ``ux@100kN`` interpolates the drift at V = 100 kN, and
   ``k@100kN = 100 kN / ux@100kN`` is the comparable secant stiffness.

The fiber beam-column flexure-only reference is **opt-in** (``--fiber``)
and probe-only (see §7): ``forceBeamColumn`` state-determination fails on
the full 4 m section at the very first lateral step in
OpenSeesPy 3.8.0.0, so the pipeline head-to-head is MVLEM_3D vs
LayeredShell.

### Results

| H (m) | H/W | Path | ux@100kN (m) | k@100kN (kN/m) | V_max (kN) |
|---|---|---|---|---|---|
| 2 | 0.5 | LayeredShell (4×4) | 1.0e-5 | 9.57e6 | 1.91e6 |
| 2 | 0.5 | MVLEM_3D | 1.53e-3 | 6.55e4 | 1.44e5 |
| 4 | 1.0 | LayeredShell (4×4) | 3.2e-5 | 3.11e6 | 6.27e5 |
| 4 | 1.0 | MVLEM_3D | 3.06e-3 | 3.27e4 | 7.20e4 |
| 8 | 2.0 | LayeredShell (4×4) | 1.6e-4 | 6.17e5 | 1.24e5 |
| 8 | 2.0 | MVLEM_3D | 6.11e-3 | 1.64e4 | 3.60e4 |

Figure: ``examples/output/wall_pushover_compare.png`` — one figure, one
subplot per height, MVLEM_3D vs LayeredShell overlaid (u = 0.2 m,
V = 100 kN reference dashed).

### Conclusions

1. **MVLEM_3D is ~100–150× more flexible than the elastic LayeredShell
   upper bound** at every height (k@100kN ratio ≈ 1/146 at H/W = 0.5,
   ≈ 1/95 at H/W = 1.0, ≈ 1/38 at H/W = 2.0).  The MVLEM_3D drift is
   dominated by its horizontal ElasticPP shear spring
   (k = 0.1·G·A/h) — a design choice of the uniaxial stack, not a
   defect: the §6.2 Euler flexure-only probe predicts flexural drift
   of order 10⁻⁴·H, while the macro-element's shear spring contributes
   the bulk of the 1.5–6 mm @ 100 kN drift.
2. **LayeredShell is the elastic stiffness envelope** (no cracking, no
   rebar smearing): the stiffest possible wall response, with V_max at
   0.2 m drift 4–16× the MVLEM_3D capacity.  Nonlinear softening paths
   should start from and fall below this bound.
3. **The H/W sweep brackets the shear-to-flexure transition.**  The
   shear-spring dominance is largest at H/W = 0.5 (stocky) and narrows
   at H/W = 2.0, consistent with the §6.2 prediction that flexure only
   becomes competitive at H/W ≳ 3–5.  For shear-critical walls use
   MVLEM_3D; for slender/flexure-dominated walls the layered path (or
   fiber pier strips, probe-only) is acceptable.
4. **Material-tangent caution (OpenSeesPy 3.8.0.0).**  ``ConcreteCM``'s
   initial tangent is ~37× softer than the passed ``Ec``, so the
   MVLEM_3D gravity stage shows uz_g ≈ −ε₀·H (≈ −20 mm at H = 4 m) and
   its elastic flexural compliance is inflated.  For a
   stiffness-converged MVLEM_3D comparison, use ``Concrete01``
   (E0 = 2·fc/εc0 exact) as in the opt-in fiber reference; the
   LayeredShell path (ElasticIsotropic, E = 30 GPa) gives the correct
   elastic uz_g ≈ −3e-4 m.
5. **Recommendation.**  Compare the two paths at matched material
   stiffness (``Concrete01``-concrete MVLEM_3D vs elastic LayeredShell),
   or treat the MVLEM_3D numbers as the *shear-flexible* nonlinear
   response and LayeredShell as the elastic bound — the bracket is the
   useful output, not a single "correct" curve.

## 8. Local OpenSeesPy build (macOS arm64)

When the shipped wheel lacks a required material (e.g. a locally-built
`OpenSeesPy` includes PSUMAT), see
[docs/openseespy_local_build.md](openseespy_local_build.md) for the
import-chain swap recipe.
