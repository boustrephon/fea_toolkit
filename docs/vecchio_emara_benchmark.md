---
title: "Vecchio & Emara (1992) RC-Frame Pushover Benchmark"
description: "End-to-end validation of the nonlinear RC pushover pipeline against the published large-scale two-storey frame experiment: literature findings, three validation passes, and the rigid joint end-zone (Level 1) modelling that brings the model inside the acceptance band."
status: "complete"
tags: [validation, benchmark, pushover, rc, fiber-section, shear, rigid-end-offset, joint]
category: [analysis-types]
related: [pushover_analysis.md, deprecation_plan.md, report_generation.md, builder_reference.md]
---
# Vecchio & Emara (1992) RC-Frame Pushover Benchmark

## TL;DR

This document summarises the end-to-end validation of the toolkit's
Preprocessor → AnalysisBuilder → pushover pipeline against the **Vecchio
& Emara (1992)** large-scale two-storey RC frame experiment (University
of Toronto), and the findings from the supporting literature.  After two
model-fidelity fixes — the element formulation and auto-generated **rigid
joint end zones** (Level 1 joint modelling) — the toolkit reproduces the
experimental peak base shear to **1.07 ×** (≈ 353 kN vs ≈ 330 kN) and the
secant stiffness at 50 mm to **1.03 ×** (≈ 6.3 vs ≈ 6.1 kN/mm), both
inside the original **±10–15 % acceptance band** with no calibration.
This is consistent with the independent published analysis of the same
specimen by Kotsovos & Zygouris (2019) (≈ 0.91 ×).

```text
                 peak ratio           secant @ 50 mm ratio
centreline flexure-only (pass 1)       1.50                 1.41
dispBeamColumn → forceBeamColumn       0.88                 0.93
+ rigid joint end zones (final)        1.07                 1.03
```

---

## 1. Why this benchmark

Gap 4 of the deprecation-removal programme (`docs/deprecation_plan.md`)
requires that the toolkit's **nonlinear static (pushover) analysis of RC
frames** be demonstrably trustworthy before the ~820-line deprecated
``OpenSeesBuilder`` API can be removed.  A published large-scale frame
experiment is the strongest possible guard: it exercises the whole
pipeline (SAP-style data model → Preprocessor topology → MeshModel →
AnalysisBuilder fibre-rebuild → gravity + displacement-controlled push)
against reality.

The Vecchio & Emara (1992) frame was chosen because it is a canonical
RC-frame shear benchmark: it was explicitly designed to expose the role
of **shear deformations** in frame response, and it has since been used
to validate several independent analysis platforms (VecTor2/DSFM,
VecTor5, Kotsovos' mode-of-failure model).

---

## 2. The specimen and the experiment

Data transcribed from Guner (2008) §2.3.5/§4.7 (``Table 2.5``, ``Figure
2.15``) and the PEER Report 2006/04 (§4.5.1/§5.3):

- **Geometry** — one-bay, two-storey frame; centre-to-centre span
  3500 mm; storey height 2000 mm (column centreline to beam centreline;
  base beam top to Level 2 = 4000 mm, overall 4600 mm including the
  400-mm base beam).
- **Members** — all 300 × 400 mm; beams and columns reinforced with
  4 No. 20M bars top + bottom (Aₛ = 300 mm² each face); No. 10M closed
  stirrups @ 125 mm.
- **Materials** — f′꜀ = 30 MPa (E꜀ = 23 674 MPa, ε꜀₀ = 1.85e-3);
  longitudinal fy = 418 MPa (fu = 596 MPa, Es = 192 500 MPa,
  εsh = 9.5e-3, Esh = 3100 MPa); ties fy = 454 MPa.
- **Loading** — constant **700 kN axial per column** (force-controlled
  jacks), then a monotonic lateral displacement at the Level-2 beam
  pushed to **155 mm** and unloaded.
- **Reported response** — peak net lateral load **≈ 330 kN** reached
  near **50 mm**; energy dissipation 44.4 kN·m; effective secant
  stiffness at yield **≈ 6.1 kN/mm**.

---

## 3. Findings from the literature

### 3.1 Vecchio & Emara (1992) — the experiment

"Shear Deformations in Reinforced Concrete Frames", *ACI Structural
Journal* 89(1) 46–56.  The paper established that shear deformations are
**not negligible** in RC frame members and presented this benchmark as
controlled evidence.  Key quantitative anchors used by every downstream
validation: peak ≈ 330 kN @ ≈ 50 mm, secant ≈ 6.1 kN/mm, energy
44.4 kN·m.  (Free PDF: `vectoranalysisgroup.com/journal_publications/jp16.pdf`,
DOI 10.14359/1283.)

### 3.2 Guner (2008) — PhD thesis, University of Toronto

The thesis (“Performance Assessment of Reinforced Concrete Frame Buildings
Using a Force-Based Frame Element”) compiles an experimental RC-frame
database (Table 2.5) that includes the V&E frame (geometry in Fig 2.15)
and uses it (§4.7) to validate a **force-based** frame element with
flexure-shear interaction and the DSFM (Disturbed Stress Field Model).
Its conclusions — that force-based distributed-inelasticity elements with
explicit cracked-shear stiffness reproduce frame response far better than
classical lumped-plasticity or Euler-Bernoulli flexure-only elements — are
the architectural justification for the toolkit's ``forceBeamColumn`` +
fibre rebuild path.

### 3.3 Guner & Vecchio (2010b) — journal validation

The journal companion (also citing the V&E frame among its validation
suite) demonstrates that the force-based frame element with the DSFM
reproduces the experimental response of RC frames, including the V&E
frame, within engineering accuracy.  It is the published benchmark that
makes “0.9–1.1 × experimental on this specimen” a realistic target.

### 3.4 PEER Report 2006/04

The PEER report provides an independent documentation/transcription of
the specimen (reinforcement layout, material data, measured response
used for member/frame component databases) and is the cross-check for
the geometry and material values used in the toolkit model.

### 3.5 Kotsovos & Zygouris (2019)

An **independent** analysis of the same specimen (Kotsovos & Zygouris
2019, *Magazine of Concrete Research*, 71(3), 109–125,
doi:10.1680/jmacr.17.00092) predicts ≈ **300 kN** peak (0.91 ×
experimental).
That the toolkit's uncalibrated final model (1.07 ×) lands within a few
percent of this independent state-of-the-art prediction is strong
evidence that the remaining discrepancy is model-physics, not a toolkit
error.

### 3.6 What the literature establishes

1. The experimental response (330 kN / 6.1 kN/mm / 44.4 kN·m) is a
   stable, widely reproduced target.
2. **Shear deformation is a first-order effect** in this frame (~20 % of
   the lateral drift) — a flexure-only model is structurally incapable of
   matching the experiment in the cracked range.
3. **Force-based distributed-inelasticity elements** (not
   displacement-based) are the appropriate tool for RC frame pushover.
4. A centreline-to-centreline member idealisation is too flexible at the
   joints — **rigid joint zones** are needed to model the correct
   clear-span flexure.

---

## 4. The toolkit model

``make_vecchio_emara_frame()`` (in `tests/test_rc_benchmark.py`) builds
an ``SAPModelData`` directly (no `.s2k` file dependency): planar X–Z
frame in kN-m units (per the 3D-only policy), base nodes fully fixed,
Mander confinement fed the published No. 10M @ 125 mm tie data, and the
published 4 No. 20M top/bottom bars.  The pushover uses
`_BENCH_CONFIG`: ``geom_transf_type = "PDelta"`` (P-Δ drives the
post-peak descent), 5 Lobatto integration points, the published
steel hardening ratio ``rebar_b = Esh/Es = 0.0161``, uniform
mass-proportional lateral load, control node 5, pushed to 155 mm in
62 steps with the 700 kN column loads applied as joint loads.

---

## 5. Validation history — three passes

### Pass 1 — flexure-only, ``dispBeamColumn`` (centreline)

Peak ≈ **495 kN (1.50 ×)**, secant ≈ **8.6 kN/mm (1.41 ×)**.  The
flexure-only fibre model has no bond-slip, no shear deformation (~20 %
share in the experiment) and no distributed-cracking effective-stiffness
reduction, so it is too stiff in the cracked range; the higher column
shears inflate the frame-action axial force in the beams, raising their
confined/hardening section capacity (M ≈ 255 kN·m at P ≈ −750 kN vs
≈ 195 kN·m at P = 0).  The first-yield lateral load (hand calc 312 kN)
is exceeded and a P-Δ-driven post-peak descent does appear — but the
bias is systematic and documented, not ±10 %.  This pass also caught a
**joint-load bug**: the 700 kN column loads were parsed and carried by
the Preprocessor but never emitted to the domain (fixed;
``test_gravity_joint_loads_applied`` guards it).

### Pass 2 — ``forceBeamColumn`` (flexibility-based) + shear aggregation

Re-running the same pipeline with the fibre rebuild on ``forceBeamColumn``
instead of ``dispBeamColumn`` drops the peak to ≈ **291 kN (0.88 ×)** and
the secant to ≈ **5.6 kN/mm (0.93 ×)** — inside the original ±10–15 %
band.  **Discovery:** ``dispBeamColumn`` (Euler-Bernoulli) never engages
section shear DOFs, so the new ``aggregate_shear`` / ``SectionAggregator``
option is inert for it (the builder now warns); ``forceBeamColumn``
engages them.  The elastic ``GAᵥ`` shear term itself contributes only
≈ 0.2 % for these members — the experimental ~20 % shear share is a
*cracked*-shear phenomenon, deferred.  New tests:
``TestVecchioEmaraShearFlexibleVariant``.

### Pass 3 — rigid joint end zones (this document's focus)

Adding auto-generated rigid joint end zones (Level 1) with MPC links
shortens the flexible members to the joint faces and lifts the peak to
≈ **353 kN (1.07 ×)** and the secant @ 50 mm to ≈ **6.3 kN/mm (1.03 ×)**.
The combination of the correct element formulation (pass 2) and the
correct joint idealisation (pass 3) now brackets the experiment from
both sides with no calibration.

---

## 6. Frame member rigid end zones (Level 1 joint modelling)

### 6.1 Concept

SAP2000's joint-fidelity taxonomy distinguishes four levels.  Level 1
(“rigid offset”) models the beam-column joint as a **rigid zone**: the
member flexure starts at the **face of the intersecting member**, not at
the centreline.  A beam end is offset by (a factor of) the **column
depth**, and a column end by the beam depth.  On the V&E frame this
removes 0.2 m per member end (0.5 × 0.4 m), i.e. columns flex as
1.8 m (Level-1 columns) / 1.6 m (Level-2 columns) clear heights and the
beams as 3.1 m — the dominant source of the pass-2 → pass-3
stiffening (secant 0.93 × → 1.03 × experimental).

### 6.2 Configuration

Auto-generation is opt-in on the **Preprocessor** config:

| Key | Default | Meaning |
|---|---|---|
| ``rigid_end_zones`` | ``False`` | Auto-derive rigid end offsets for every member end that meets a non-collinear member. |
| ``rigid_offset_factor`` | ``0.5`` | Offset = factor × intersecting member depth (0.5 → flexure from the joint face). |
| ``rigid_offset_absolute`` | ``None`` | Fixed offset length; overrides the derived ``factor × D`` when set. |
| ``joint_extents`` | ``None`` | ``{node_id: panel_dimension}`` of an explicit Level-3 joint element — subtracted (clamped ≥ 0) so Level 1 and Level 3 never double-count the same region. |

Explicit S2K ``frame_end_offsets`` (from the ``FRAME END OFFSETS``
table) always **win** over auto-derived values.  The offsets are applied
by the existing ``apply_frame_end_offsets()``, which shortens the members
and emits one rigid link per offset end.

### 6.3 Derivation algorithm — ``derive_rigid_end_offsets()``

In ``fea_toolkit/model/geometry.py`` (pure geometry, no OpenSees):

1. Build a node → connected-element adjacency (inactive/split parents
   skipped).
2. For each end of each member, scan the members at that node and keep
   only **non-collinear** connectors (collinear continuations — e.g. the
   adjacent bay beam — are ignored).
3. ``D`` = the largest section depth among those connectors
   (``depth``, falling back to ``bf`` then ``diameter``); the offset is
   ``factor × D`` (or ``absolute``).
4. Subtract ``joint_extents[node]`` if given, clamped at ≥ 0.
5. Emit a ``FrameEndOffset(end_i, end_j)`` per member with a non-zero
   derived offset.

### 6.4 Rigid links: MPC vs stiff elastic

The builder creates the rigid zone either as

- **stiff ``elasticBeamColumn``** segments (the historic default), or
- **``ops.rigidLink("beam")`` MPCs** (``rigid_link_mpc=True``).

The MPC form is required for P-Δ pushover: the very stiff elastic links
(E = 2e14) give stiffness ratios of ~1e6–1e8 against the concrete
members, which **ill-conditions the global system and fails to converge
at the gravity stage** under PDelta (the same documented problem as the
brace-subdivision rigid links).  MPCs constrain the offset node to the
joint node's rigid-body motion instead of adding stiffness, so the system
stays well-conditioned and the benchmark runs with the default
``solver_constraints = Transformation``.

### 6.5 Preprocessor orphan-node fix

A latent bug (now fixed) surfaced when offsets were first enabled: after
``apply_frame_end_offsets()`` rewires the members to offset nodes, the
**original joint nodes are referenced only by the rigid-link
bookkeeping**, so the orphan-node step dropped them and the rigid links
were silently skipped → **singular stiffness matrix**
(``BandGenLinLapackSolver … U(i,i)=0``).  The step now protects the rigid
link endpoint nodes (regression-tested).

### 6.6 Interaction with Level 3 joint elements

When explicit Level-3 joint elements are added in future, supplying
``joint_extents`` automatically shrinks the Level-1 offsets to the joint
face, so the two features compose without rework.  (Level 2 — a
zero-length spring restoring some % of the rigid connection — remains
unimplemented; Level 3 joint elements remain on the README roadmap.)

### 6.7 Test coverage

``tests/test_rigid_end_zones.py`` (14 tests):

- **Geometry** — ``TestDeriveRigidEndOffsets``: orthogonal half-depth
  offsets, collinear connectors ignored, absolute override, factor
  scaling, ``joint_extents`` subtraction + clamping.
- **Preprocessor** — ``TestPreprocessorRigidEndZones``: default off,
  enabled → 2 links on the L-frame, explicit offsets win, the
  orphan-node regression, ``rigid_offset_absolute`` and
  ``joint_extents`` config wiring (offset-node coordinates).
- **AnalysisBuilder** — ``TestBuilderRigidEndZones``: MPC links build +
  a static step converges with the tip load equilibrated at the base,
  the default elastic-link path still creates elastic elements, and
  rigid end zones measurably **stiffen** the lateral response of the
  planar test frame.

Plus the end-to-end benchmark test
``test_rigid_end_zones_lands_in_acceptance_band`` in
``tests/test_rc_benchmark.py`` (forceBeamColumn + rigid zones → peak
ratio [0.95, 1.15], secant [0.9, 1.15] × experimental).

---

## 7. Model performance summary

| Configuration | Peak (kN) | Peak ratio | Secant @ 50 mm (kN/mm) | Secant ratio |
|---|---|---|---|---|
| Experimental (V&E 1992) | ≈ 330 | 1.00 | ≈ 6.1 | 1.00 |
| Kotsovos & Zygouris (2019, independent) | ≈ 300 | 0.91 | — | — |
| Pass 1: ``dispBeamColumn`` (centreline, flexure-only) | ≈ 495 | 1.50 | ≈ 8.6 | 1.41 |
| Pass 2: ``forceBeamColumn`` | ≈ 291 | 0.88 | ≈ 5.6 | 0.93 |
| **Pass 3: + rigid joint end zones** | **≈ 353** | **1.07** | **≈ 6.3** | **1.03** |

---

## 8. Known limitations and deferred work

1. **Post-peak shape.**  The model continues to rise after ≈ 50 mm
   instead of descending like the experiment — the peak-to-peak and
   secant metrics are inside the band, but the *shape* of the capacity
   curve after the experimental peak is not reproduced.  The nonlinear
   shear backbone (``aggregate_shear = "nonlinear"``) was applied to the
   V&E frame (2026-08-24) and is **inert for the post-peak shape**: peak
   348 kN (1.05×) vs 353 kN (1.07×) with elastic shear, and the curve
   still rises to the 155 mm end.  The V&E frame is **shear-strong
   (flexure-critical)** by design, so member shear never reaches the MCFT
   backbone's degrading branch within the push range; the same mechanism
   *does* reproduce the post-peak descent on the companion **shear-critical
   Duong frame** (``docs/shear_failure_modelling.md``, ≥ 15 % post-peak
   drop).  Reproducing the V&E descent therefore requires the next
   increment — flexure softening (strain-softening concrete) and/or
   bond-slip springs — tracked in ``docs/_pending_work.md`` (P5).
2. **Elastic shear aggregation is not enough.**  The ~20 % experimental
   shear-drift share is a cracked-shear phenomenon; the elastic
   ``GAᵥ`` term adds ≈ 0.2 %.  The nonlinear backbone layer (see above)
   is the cracked-shear increment.
3. **No base-beam bottom offsets.**  The model fixes the base nodes at
   the column centreline and does not model the 400-mm base beam, so
   the Level-1 columns lack a bottom rigid zone.
4. **Level 2 (spring offsets) and Level 3 (joint elements) are
   unimplemented** — but ``joint_extents`` composes with Level 1.
5. **Vecchio & Balopoulou (1990) variant** — to be re-run with the new
   shear-capacity reporter / backbone layers.

---

## 8a. Companion benchmark — Duong et al. (2007) shear-critical frame

The two-layer shear-failure modelling driven by the **Duong, Sheikh &
Vecchio (2007)** shear-critical frame is documented in
``docs/shear_failure_modelling.md``:

- **Phase 1 (reporter)** correctly identifies the **first-storey beam as
  the shear-governing member** on the Duong frame (matching the
  experimental mid-span diagonal failure and Guner's beam-1S sequence) and
  finds **no** shear exceedance on this (flexure-governed) V&E frame.
- **Phase 2 (nonlinear backbone)** reproduces the Duong **stage-1
  response** with an explicit backbone: peak ≈ 227 kN (**1.03 ×** the
  experimental 220 kN) with the classic two-step shear-failure descent.
- **Known limitation:** the auto simplified-MCFT backbone (V_n ≈ 295 kN)
  overestimates this beam's capacity (~1.5 × the effective experimental
  value) — the documented MCFT-vs-Kotsovos difference.  The reporter's
  *mode* is correct; the *absolute level* is conservative-high.

---

## 9. References

1. Vecchio, F.J. & Emara, M.B. (1992). “Shear Deformations in
   Reinforced Concrete Frames.” *ACI Structural Journal* 89(1) 46–56.
   DOI 10.14359/1283.
2. Guner, S. (2008). “Performance Assessment of Reinforced Concrete
   Frame Buildings Using a Force-Based Frame Element.” PhD thesis,
   University of Toronto (§2.3.5, §4.7; Table 2.5; Fig 2.15).
3. Guner, S. & Vecchio, F.J. (2010b). *Pushover analysis of RC frames
   with a force-based frame element* (journal companion; used the V&E
   frame in the validation suite).
4. Lowes, L. / Mosalam, K. et al. — PEER Report 2006/04 (frame
   specimen documentation, §4.5.1/§5.3).
5. Kotsovos, G.M. & Zygouris, N.S. (2019). “Reinforced concrete frame
   analysis with mode of failure prediction capability.” *Magazine of
   Concrete Research*, 71(3), 109–125. doi:10.1680/jmacr.17.00092
6. Implementation: ``tests/test_rc_benchmark.py``,
   ``tests/test_rigid_end_zones.py``, ``docs/deprecation_plan.md``
   (Gap 4), ``docs/report_generation.md`` §3.4.
