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

| Aspect | LayeredShell / ShellNLDKGQ | SFI_MVLEM_3D |
|---|---|---|
| Element type | `ShellNLDKGQ` quads | `SFI_MVLEM_3D` macro-element |
| Material law | Layered through-thickness stack of nD materials | Per-fibre FSAM nD material |
| Discretisation | Requires mesh refinement | Single element per wall (fibres along width) |
| Config key | `shell_layers` | `element_strategies.wall` |
| Reinforcement | Per-layer `nd_material` choices | Per-fibre `fsam_materials` (via `rou_x`/`rou_y`) |
| Solver notes | Default `Transformation` is fine | `Plain` + `gravity_num_substeps: 10` recommended |

The integration tests in `tests/test_wall_pushover.py` run both paths
through `Preprocessor → AnalysisBuilder`, verify `rx = -100 kN` for a
100 kN lateral push, and confirm the expected ordering of top drifts
(the nonlinear macro-element is far more flexible than a purely-elastic
layered stack).

## 6. Local OpenSeesPy build (macOS arm64)

When the shipped wheel lacks a required material (e.g. a locally-built
`OpenSeesPy` includes PSUMAT), see
[docs/openseespy_local_build.md](openseespy_local_build.md) for the
import-chain swap recipe.