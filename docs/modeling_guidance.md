---
title: "Structural Modeling Guidance"
description: "Canonical guidance for choosing element formulations, hinge/plasticity models and cyclic laws when building nonlinear FEA models — distilled from the benchmark verification (SAC steel frames, RC walls)."
status: "guidance"
tags: [llm, guidance, modeling, nonlinear, element-choice, hinge, steel, damping, units]
category: [core-pipeline]
related: [llm_guide.md, sac_trilinear_hinge_approaches.md, sac_nonlinear_benchmark.md, nonlinear_dynamic_analysis.md, pushover_analysis.md, units_conversion.md]
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
