---
title: "Structural Modeling Guidance"
description: "Canonical guidance for choosing element formulations, hinge/plasticity models and cyclic laws when building nonlinear FEA models — distilled from the benchmark verification (SAC steel frames, RC walls)."
status: "guidance"
tags: [llm, guidance, modeling, nonlinear, element-choice, hinge, steel, damping, units]
category: [core-pipeline]
related: [llm_guide.md, sac_trilinear_hinge_approaches.md, sac_nonlinear_benchmark.md, nonlinear_dynamic_analysis.md, pushover_analysis.md, units_conversion.md, nonlinear_link_plan.md, modal_analysis.md]
---

# Structural Modeling Guidance

> **What this is.** A distilled set of *engineering* guidance for choosing how
> to model a structure. The *code* rules live in `.clinerules`; this is the
> *domain* guidance — read it before choosing an element formulation, a hinge /
> plasticity model, or a cyclic law for a nonlinear model. It exists because the
> benchmark verification kept hitting the same two failure modes, and they are
> the kind of thing that wastes a session if you have to rediscover them.
>
> **Guidance, not rules.** We are still learning how to model these structures
> well, and this document will be expanded and revised as our benchmark
> experience grows. The benchmarks it draws on are themselves only *evidence of
> what has been considered to work* — not proof of optimality, and a benchmark's
> published "target" is not necessarily the best model either. Treat every
> recommendation here as a current best-guess with a recorded reason, not a
> fixed specification. When a new benchmark or model contradicts something here,
> update this document (and its `.clinerules` §3.13 summary) rather than working
> around it in a one-off script.

## 1. The rule that matters: force-based vs displacement-based elements

Force-based (flexibility) elements — `beamWithHinges`, `forceBeamColumn`,
`nonlinearBeamColumn` — assemble the element from its **flexibility** matrix and
**invert it** to recover stiffness at every state. That inversion is numerically
ill-conditioned at **both** ends of the member-stiffness range:

| Extreme | Symptom | When |
|---|---|---|
| **Too soft** (small `I`) | `ForceBeamColumn3d::getInitialStiff()` — "could not invert flexibility" | at **build** |
| **Too stiff** (large `I`, typically base/foundation members) | `ForceBeamColumn3d::update()` — "could not invert flexibility" | at a **load step** |

A third, hinge-fraction-specific cause of the same message — the negative
`HingeRadau` integration weights of OpenSees' legacy `beamWithHinges` command
(not `fea_toolkit`'s own `beam_with_hinges`, which uses `HingeRadauTwo`) — is
covered in §1.1 below.

Displacement-based **lumped hinges** (`elasticBeamColumn` interior +
`zeroLength` end springs) are stiffness-formulated and **robust across the whole
stiffness range**, at a small accuracy cost (~4–5 % elastic softening vs the
exact force-based element).

> **Working guidance.** For any model that spans a wide stiffness range (tall
> frames, mixed section sizes — box columns beside W columns, transfer members
> beside braces), default to the displacement-based **lumped hinge**. Use a
> force-based element only when you have confirmed that **every** member both
> builds *and* survives the first load steps. A force-based element that
> *builds* is not guaranteed to *run*.

### 1.1 The `beamWithHinges` integration trap (negative `HingeRadau` weights)

OpenSees' legacy **`beamWithHinges` command** integrates its concentrated
plastic hinges under the **`HingeRadau`** rule (six points: two hinge points
plus four interior points).  This is *not* what `fea_toolkit` ships
— the toolkit's `hinge_model='beam_with_hinges'` emits a `forceBeamColumn`
under **`HingeRadauTwo`** (see the fix below).  `HingeRadau`'s two interior
weights are

    w = 0.5 − 2·(lpI + lpJ)/L

which is **negative once the total plastic-hinge length exceeds L/4**.  A
plastic-hinge fraction of `Lp = L/6` per end (total `L/3`) — the SAC benchmark's
θ = φ·L/6 convention — therefore gives every member `w = 0.5 − 2/3 = −1/6`, i.e.
two of its six integration weights are **negative**.

Negative weights are *exact* for the Gauss–Radau rule applied to the
linear-elastic solution, so the net elastic flexibility — and with it the
elastic stiffness and the periods — is still correct.  That exactness does not
extend to the nonlinear response: post-yield accuracy depends on the section
force–deformation behaviour and on the integration rule.  But they are
numerically hazardous: the element's flexibility
matrix is assembled by *adding* the positive-weight contributions and then
*subtracting* the negative-weight ones, and subtracting pieces of a
positive-definite matrix amplifies round-off.  The result can be a 6×6
flexibility matrix that is only marginally invertible, so the LU factorisation
fails with `ForceBeamColumn3d::update() -- could not invert flexibility`.  The
failure is platform-dependent — macOS's Accelerate LAPACK happens to digest the
cancellation, the Linux LAPACK build does not — which is why the SAC 3-story
`beam_with_hinges` model reproduced the benchmark on macOS but failed on the
Linux CI.

The fix is the **`HingeRadauTwo`** integration, whose interior weights are
`0.5 − 0.5·(lpI + lpJ)/L` and stay **positive for any `Lp < L/2`**.
`fea_toolkit` therefore builds `hinge_model='beam_with_hinges'` as a
`forceBeamColumn` under `HingeRadauTwo` — same elastic stiffness, same hinge
recorder layout (sections 1 and 6) — which keeps the SAC 3-story model (and now
the 9/20-story *builds*) working across platforms.

The switch changes only the numerical conditioning of the element, not its
**elastic** response — the elastic stiffness (exact `6EI1/L`) and the recorder
sections (1 and 6) are unchanged.  The **post-yield** (softening) response,
however, is governed by the plastic hinge length `Lp`, which converts the hinge
section's moment-*curvature* backbone to the member-end rotation `θ = φ·Lp`.
The toolkit does **not** rescale `Lp` for the switch, so to preserve the
intended post-yield response users must keep `hinge_length_fraction` at the
value the backbone was calibrated against — the default `1/6` reproduces the SAC
benchmark's `θ = φ·L/6` member-end rotation.  (`HingeRadauTwo` places two points
per hinge — weights `Lp/4` at the member end and `3Lp/4` at `2Lp/3` — which sum
to `Lp`, so a constant-moment hinge still integrates `θ = φ·Lp` exactly; the
two-point layout changes the numerical conditioning, not the integrated plastic
rotation.)  (The `IMKBilin`/IMK calibration is a separate **lumped-hinge**
(`zeroLength` spring) backbone and is not used on this
`beam_with_hinges`/`HingeRadauTwo` path, so it neither compensates for nor is
affected by the integration-rule softening length.)

## 2. Element & hinge cheat-sheet

| Situation | Choice | Notes |
|---|---|---|
| Steel moment frame, any size | `elasticBeamColumn` + `zeroLength` lumped hinge | robust default |
| Small frame, uniform stiffness, need exact stiffness | `beamWithHinges` (concentrated plastic hinge) | verify the full stiffness range |
| Brace — static | `Truss` + `Hysteretic` | |
| Brace — dynamic | `Truss` + `Hysteretic` + `Fatigue` | `brace_fatigue=True` |
| RC wall (fibre/smeared) | `LayeredShell` / `ShellNLDKGQ` | see `pushover_analysis.md` gravity section |

## 3. Cyclic law (steel)

| Law | Robustness | When |
|---|---|---|
| `Hysteretic` (trilinear, kinematic) | ✅ default | accurate and robust; use this first |
| `Steel02` (Giuffré–Menegotto–Pinto) | ✅ good | smooth, slightly softer than `Hysteretic` |
| `Clough` degradation (β = 0.5) | ⚠️ unstable | loses convergence on strong/long records; check a smaller β against the intended cyclic response, or avoid |

## 4. Damping

- Anchor Rayleigh damping at the **elastic** model's periods, not the nonlinear
  model's — damping is an elastic property, and the nonlinear model's (stiffer)
  periods would mis-scale it.
- For large models use the ARPACK eigen solver (`eigen_solver="default"`); the
  dense solver is impractically slow above a few thousand DOF.
- **Exclude `zeroLength` lumped-hinge springs from stiffness-proportional
  Rayleigh damping.**  The near-rigid `K0 = stiff_factor·6EI/L` hinge stiffness
  inflates stiffness-proportional damping forces and destabilises the transient
  (Zareian & Medina 2010; the same convention is adopted in NIST GCR
  17-917-46v2).  The lumped hinge is the toolkit default, so this applies to
  every `hinge_model='lumped'` transient run.

## 5. Units & materials

- Author material strengths and moduli in **SI (Pa)** and scale with
  `scale_material_dict()` — never hand-convert to the model's unit system
  (`.clinerules` §4.6). Hand-converting to kN-m units silently produces 1000×
  under-strength.
- Derive gravity from units with `g_from_units(units)`; never hardcode `9.81`.

## 6. Debugging non-convergence

| Symptom | Likely cause | Fix |
|---|---|---|
| "could not invert flexibility" | a member sits at a stiffness extreme | switch that member to a lumped hinge, or check its `I`/`EI` |
| divergence on strong records with `Clough` | degrading unloading stiffness | switch to `Hysteretic` / `Steel02` |
| gravity `Norm = NaN` | single-step load shock | add `gravity_num_substeps` |
| `BandGen` stall / NaN on large models | banded factorisation | `solver_system: "UmfPack"` |
| `analyze … returned: -2` + "matrix singular" | **singular stiffness (a model problem)** — rigid-body mode / unconnected DOF | check boundary conditions & connectivity; run modal to find the free mode.  A non-singular `-2` is **not** solver-only: inspect both the model (connectivity) and the integrator inputs (the load/control pairing, e.g. a zero displacement at an uncoupled control DOF) before choosing a remedy |
| `analyze … returned: -3` | a failed convergence test — **or** a singular linear solve inside `solveCurrentStep()`, so read the messages | a singular matrix at build/gravity → fix the model (do not tune convergence); a singular matrix **only during a nonlinear step** → check for yielding, capacity exhaustion and collapse (physical instability) before touching the model; otherwise adjust algorithm / shrink the time step / relax tolerance (see §8) |
| `ForceBeamColumn3d::update() -- failed to get compatible …` | section constitutive response: bad material, near-singular section stiffness, fibres oriented wrong for 2D bending, or not discretised in both directions in 3D | inspect the tagged element's sections; swap materials to their elastic equivalents one at a time |

(Full solver tables: `llm_guide.md` §5.)

## 7. Worked example — the SAC frames (evidence)

The SAC 3/9/20-story benchmark frames demonstrate §1 concretely:

- **3-story** (members I33 ≈ 2–3e-3 m⁴): `beamWithHinges` builds **and** runs —
  the sweet spot.
- **9-story**: builds, then dies at **step 7** of the transient — the *stiff*
  basement columns (I33 ≈ 3.4e-3 m⁴) sit at the too-stiff extreme.
- **20-story**: fails at **build** — the *soft* box columns, upper W columns and
  beams (I33 ≲ 1.5e-3 m⁴) sit at the too-soft extreme.

Only the 3-story is inside the force-based element's viable window; the taller
frames run on the lumped hinge.

The four-way cyclic-law study on the 3-story (10 records each) ranks them:

| Formulation | conv. | base shear | drift |
|---|---|---|---|
| B — `beamWithHinges` + `Hysteretic` | 10/10 | −11…+7 % | −14…+48 % |
| C — `beamWithHinges` + `Steel02` | 10/10 | −13…+1 % | −15…+37 % |
| A — lumped + `Hysteretic` | 10/10 | −18…+1 % | −4…+66 % |
| D — `beamWithHinges` + `Clough` | 6/10 | −8…+7 % | −14…+81 % |

**`Hysteretic` is the robust default; `Clough` degradation is unstable on the
strong records.** Full numbers: `sac_trilinear_hinge_approaches.md` and the
nonlinear verification paper.

## 8. External community survey (2026) — cross-validation

> This section records what the wider OpenSees community (English, Chinese,
> Turkish, Portuguese, Spanish and Thai sources) reports as working or failing,
> in so far as it confirms or extends the guidance above.  Collected 2026-11;
> see the source register at the end.  Like the rest of this document it is
> guidance, not rules.

### 8.1 Material models — build up nonlinearity progressively

- Concrete: `Concrete01` (no tension, Karsan–Jirsa unloading) and `Concrete02`
  (linear tension softening) are the robust workhorses; `Concrete04` (Popovics)
  is used for the confined core beside a `Concrete01` cover (Caglar & Tatar,
  Turkish RC-column study).  The advanced models (`Concrete23`/`06`/`07`) add
  convergence risk and are a frequent cause of the `forceBeamColumn` "failed to
  get compatible" failure.
- Steel: `Steel02` is the fibre workhorse; `Hysteretic` (trilinear) and the IMK
  family serve the hinge path (see `imk_deterioration_model.md`).
- **Build up in stages** (Scott, "A Marathon, Not a Sprint"): elastic sections →
  `ElasticNoTension` + `Steel01` → `Concrete01` → `Concrete02`/`Steel02` → only
  then `Concrete23`/`Steel08`.  Re-run gravity + lateral at every stage and make
  sure each change in response is explicable.  When a stage stops converging,
  the last material switched is the culprit — swap it back to its elastic
  equivalent and diagnose before proceeding.

### 8.2 Element combinations

- Force-based (`forceBeamColumn`, `nonlinearBeamColumn`) when axial–moment
  interaction matters (RC moment frames).  For **distributed-plasticity
  Gauss–Lobatto integration** the community consensus is **3–5 integration
  points** — "not more than 5" applies to that scheme only, not to every
  element.  Fixed-layout **hinge integration** is the exception: it places its
  points at the plastic-hinge layout rather than a Gauss–Lobatto spacing, so it
  is not subject to the 3–5 guidance — the toolkit's ``beam_with_hinges`` path
  emits a six-point ``forceBeamColumn`` under ``HingeRadauTwo`` (see §1.1) and
  is preserved as documented.  Displacement-based (`dispBeamColumn`) for
  several short elements (e.g. piles with p-y springs).
- `beamWithHinges` when yielding is confined to member ends and strain softening
  is expected.  Berkeley Example 7 is the canonical case: a distributed-fibre
  `nonlinearBeamColumn` RC 3D frame failed to converge at large drift where
  `beamWithHinges` columns converged.  (Consistent with §1, which prefers the
  lumped hinge across the full stiffness range.)
- Strain-softening concrete localises in one element / integration point, giving
  mesh-dependent response; regularise with Coleman–Spacone ultimate-strain
  scaling, Scott–Fenves end weights, or `GradientInelasticBeamColumn`.
- **Walls:** the community reports the coupled `SFI-MVLEM` has an inconsistent
  tangent and spawns one internal node per fibre, so even an elastic wall takes
  ~18 Newton iterations and the system size balloons; the mitigation is
  `KrylovNewton` or `E_SFI_MVLEM_3D` (no internal nodes).  The toolkit reached
  the same conclusion independently — see `mvlem_wall_analysis.md`.
- Panel zones (rigid offsets vs a rotational-spring box) change little while the
  panel-zone shear demand stays below the expected yield strength at the assessed
  loading level — so refinement is a **demand- and deformation-sensitivity**
  decision, not a collapse-assessment-only one.  When the panel-zone shear demand
  exceeds the expected yield strength, the model must capture that yielding and
  its inelastic deformation rather than leaving the joint rigid.  (UWO STR-833,
  for the steel moment frames it studied — generalise with care.)

### 8.3 Analysis settings

- Handlers: `Transformation` for general use; `Penalty` leaks the constraint
  forces into the convergence norms, so tolerances must account for them.
- Algorithms: `Newton` (quadratic only with a consistent tangent);
  `ModifiedNewton` needs a raised iteration cap; `KrylovNewton` / `BFGS` /
  `Secant` / `Broyden` for sloppy tangents.  **`Linear` is deprecated as a
  convergence workaround.**
- Convergence test: the default `NormUnbalance 1e-8` is far tighter than
  necessary; use `NormDispIncr` for very stiff members or the `Penalty` handler,
  and scale the tolerance by **√N** for large models.  Relative norms misbehave
  when the applied load changes abruptly.
- Error codes are a hint, not a diagnosis: `-2` usually means a singular
  stiffness matrix (a model problem) but can appear without one, and `-3`
  usually means a failed convergence test but can also come from a singular
  linear solve inside `solveCurrentStep()` — diagnose by the failure stage and
  the accompanying solver message, not the code alone — see §6.  Distinguish an
  *initially* singular model (a singular matrix at build or the gravity step) from
  singularity reached *during* the nonlinear response: the former is a modelling
  defect (boundary conditions / connectivity), while the latter usually signals
  physical instability — yielding, strength/ductility capacity exhaustion or
  incipient collapse — which is a result to be interpreted, not a model error to
  be corrected.

### 8.4 Source register

| Language | Sources |
|---|---|
| English | `openseesdigital.com` (M. Scott's blog); `opensees.berkeley.edu` wiki/manuals/examples; NIST GCR 17-917-46v2; UWO STR-833 |
| Chinese | `hanlindong.com` (SmartAnalyze); `jdcui.com` (崔济东's Q&A notes) |

## 9. Storey levels and multi-tower ETABS models

Storey detection (`fea_toolkit.model.stories.identify_stories`) resolves levels
in priority order — the ETABS story table (parsed into `SAPModelData.stories`),
Z-axis diaphragm constraints, nearly-horizontal area elements, then node
Z-clustering.  SAP2000 and SAFE models do **not** guarantee storey definitions
(ETABS does), so the IDA drift
measure degrades along the same chain: with **≥ 2 levels** it is the peak
**interstorey-drift ratio** (`d_max`); with fewer it is the **roof-drift proxy**
(max horizontal nodal displacement ÷ structure height); and when storey levels
exist but the recording is restricted to a single level, the drift is reported
as **unavailable** (`drift_measure = "unavailable"`, drift `NaN`) rather than a
false zero.  The result records which was used (`drift_measure`) so a proxy or
unavailable value is never misread as interstorey drift.

**Multi-tower ETABS models are the known gap.**  ETABS lets one file hold
several "towers", each with its own grid system and its own set of story
levels (CSI: *Edit > Towers, Stories and Grid Systems*).  Story drift and shear
are reported per tower only when the multiple-tower option is enabled, and CSI
notes that a tower cannot be analysed separately — a separate model per tower
is needed for independent results.  `identify_stories` reads the story levels
declared on `SAPModelData.stories` (whose `StoryDefinition.tower` records which
tower each level belongs to) but still returns one flat level list — per-tower
drift is not computed, so levels from different towers are merged by elevation;
the detector warns when duplicate story names or elevations are present.  **Do
not trust interstorey drift on a
multi-tower model — split the towers into separate models first.**

Sources (cited, not reproduced): CSI ETABS help — *Edit > Towers, Stories and
Grid Systems*; the CSI ETABS Modelling FAQ (multiple towers); the ETABS API
`GetStories` (per-tower story information).
| Turkish | METU theses; DergiPark "Fiber Based Modeling Strategies of RC Columns" (Caglar & Tatar) |
| Portuguese | NOVA Lisboa / FEUP / Porto theses; UNICAMP |
| Spanish | CEINCI-LAB (Ecuador); `jpi-ingenieria.com`; `github.com/odarroyo/openseespy_starting_tutorials`; UniAndes wall guide |
| Thai | Chulalongkorn Univ. theses (fibre-based `dispBeamColumn` RC columns — no active Thai forum found) |

Primary URLs: `openseesdigital.com/2020/08/21/a-marathon-not-a-sprint/`,
`…/2021/02/28/norms-and-tolerance/`, `…/2020/02/23/a-tale-of-two-element-formulations/`,
`…/2021/10/10/failed-to-get-compatible/`, `…/2020/10/25/the-basics-of-frame-element-localization/`,
`…/2024/09/02/murum-cura-te-ipsum/`, `…/2019/12/08/an-update-of-the-imk-models/`,
`hanlindong.com/2019/opensees-converge/`, `jdcui.com/?page_id=1238`,
`dergipark.org.tr/en/download/article-file/2136610`.

## 10. Nonlinear links (isolators, dampers, gap/hook)

> SAP2000/ETABS *link* objects (Gap, Hook, Plastic (Wen), Damper,
> Rubber/Friction Isolator) have two builder representations, chosen by the
> ``link_model`` config:
>
> * ``"elastic"`` (default) — one ``Elastic`` spring per active DOF at the
>   effective stiffness.  Fine for elastic static / modal / response-spectrum
>   runs of *any* link type; the builder **warns once per property** when the
>   link is nonlinear, so a silently-linearised isolator is never mistaken for
>   a nonlinear one.
> * ``"nonlinear"`` — the type-specific nonlinear material/element.  Gap, Hook,
>   Plastic (Wen) and Damper emit one uniaxial material per active DOF; the two
>   bearing types (Rubber/Friction Isolator) emit a dedicated bearing element.
>   The type→emitter map is `_LINK_EMISSION_FAMILY` in `opensees/_links.py`;
>   the decisions behind it are in `nonlinear_link_plan.md`.

### 10.1 Apply gravity (and other pre-loads) before the dynamic analysis

Bearing/isolator response is **load-state-dependent**, so a transient, pushover
or other nonlinear analysis must start from the gravity-loaded state — the same
reason modal analysis applies a gravity pre-step (the `genBandArpack` / `ritz`
modes in `modal_analysis.md`).  The transient runner's ``gravity_patterns``
stage runs gravity first and retains the P-Δ and yield state; ``None`` skips it
(**do not** skip it for an isolated model).  Hand-building: apply gravity, then
``loadConst``, then start the dynamic integrator.

- **Friction isolator** — friction is ``μ·N`` and post-yield stiffness is
  ``N/Reff``, so a bearing with no axial load is *degenerate* (an early
  zero-gravity push returned NaN).  Gravity supplies the ``N``.
- **Rubber isolator** — the shear backbone is load-independent, but the
  Schellenberg bearing's P-Δ geometric-stiffness term comes from the axial
  load; without it the tangent is wrong.
- **Gap / Hook** — the open/closed state at the start of the dynamic analysis
  is whatever gravity left it in; applying gravity first establishes the
  correct engagement before the excitation.

### 10.2 Bearing and material choices

| Link type | Emission | Notes |
|---|---|---|
| Rubber Isolator | `elastomericBearingPlasticity` (default) | `rubber_model="boucwen"` opts into `elastomericBearingBoucWen`.  The Schellenberg ``qd`` is the *post-yield intercept* (``F = alpha1·kInit·u + qd``), so the emitter maps SAP2000's yield force ``Fy`` to ``qd = Fy·(1 − ratio)``. |
| Friction Isolator | `singleFPBearing` + friction model | `Coulomb` by default; `VelDependent` when ``mu_fast`` and ``rate`` are both given. |
| Plastic (Wen) | `Steel02` (default, smooth) / `Hysteretic` (`wen_model`) | `BoucWen` is rejected — no explicit ``Fy``, six unexported knobs. |
| Gap | `Hysteretic` (compression-only) | opens at the ``open`` value; zero force in the released direction. |
| Hook | `Hysteretic` (tension-only) | uniform with Gap — one backbone model. |
| Damper | `Viscous` / `Parallel` | `Parallel` puts an elastic spring beside `Viscous` for viscoelastic dampers; friction-damper subtypes are not yet emitted. |

A bearing's axial (`-P`), torsional (`-T`) and bending (`-My`/`-Mz`) DOFs take
``Elastic`` auxiliary materials; the coupled U2/U3 shear is modelled internally
by the bearing element.  The Gap/Hook/bilinear backbones pin pinching and
damage neutral — there are no degradation knobs to mis-tune — and the one-time
`check_hysteretic_backbone` verb re-runs the open-close / symmetric-bilinear
cyclic check on a generated model.

### 10.3 Base isolation as a workflow verb

The ``isolate_base`` verb (wrapping `model.supports.isolate_base`) replaces each
fully-restrained base node with a grounded isolator link assigned the step's
``property``, scoped by ``Selection``.  Run it once, up front, so every later
analysis step inherits the isolated base rather than re-isolating per analysis
(one operation, one spelling).
