---
title: "OpenSees Modeling Sources (2026 Community Survey)"
description: "Register of the web sources surveyed in November 2026 for OpenSees modeling choices — material and element combinations, analysis settings, and convergence — across English, Chinese, Turkish, Portuguese, Spanish and Thai sources."
status: "guidance"
tags: [opensees, sources, survey, modeling, nonlinear, convergence, references]
category: [opensees]
related: [modeling_guidance.md, imk_deterioration_model.md, llm_guide.md]
---

# OpenSees Modeling Sources (2026 Community Survey)

> **What this is.**  The register of web sources behind
> `modeling_guidance.md` §8 and `imk_deterioration_model.md` §8 — the
> November 2026 survey of what the OpenSees community reports as working or
> failing when choosing material and element models, their combinations, and
> analysis settings.  Every site below provided useful information; **none
> refused access** during the survey.  Each entry lists the site and the
> specific pages that were actually used.

## English

- **`openseesdigital.com`** — Michael H. Scott's blog (Portwood Digital).
  The richest single source (material / element / analysis-settings convergence
  guidance).  Posts used:
  - `openseesdigital.com/2019/10/23/non-convergence-does-not-mean-opensees-crashed/` — non-convergence ≠ crash; check material models first.
  - `openseesdigital.com/2021/02/28/norms-and-tolerance/` — convergence tests, √N tolerance scaling.
  - `openseesdigital.com/2020/02/23/a-tale-of-two-element-formulations/` — force- vs displacement-based elements.
  - `openseesdigital.com/2020/02/08/better-than-ideal-conditions/` — consistent tangent; Newton vs Krylov/BFGS.
  - `openseesdigital.com/2020/08/21/a-marathon-not-a-sprint/` — build-up-nonlinearity-progressively checklist.
  - `openseesdigital.com/2021/10/10/failed-to-get-compatible/` — "failed to get compatible" diagnosis.
  - `openseesdigital.com/2020/10/25/the-basics-of-frame-element-localization/` — localization and regularisation.
  - `openseesdigital.com/2023/11/12/flipping-rotation-axes/` — zero-length spring pitfalls.
  - `openseesdigital.com/2024/09/02/murum-cura-te-ipsum/` — SFI-MVLEM inconsistent tangent; KrylovNewton.
  - `openseesdigital.com/2019/12/08/an-update-of-the-imk-models/` — IMK lineage.
- **`opensees.berkeley.edu`** — OpenSees wiki, manuals and examples:
  `opensees.berkeley.edu/OpenSees/workshops/OpenSeesDays2012/B1_StructuralModeling.pdf`
  (force-based / displacement-based / beam-with-hinges), Example 7 (3D frame —
  `beamWithHinges` for RC at large drift), Example 3.1 and the *RC Frame
  Pushover Analysis* wiki page (the Newton → `ModifiedNewton -initial` fallback
  loop), the *Examples Manual* index, *Baseline Steel Materials* (comparison
  manual), and the *Limit State Material Manual*.
- **`opensees.github.io/OpenSeesDocumentation`** — current OpenSees
  documentation (forceBeamColumn, convergence tests, `analysis` command).
- **`nvlpubs.nist.gov/nistpubs/gcr/2017/NIST.GCR.17-917-46v2.pdf`** — NIST GCR
  17-917-46v2, *Guidelines for Nonlinear Structural Analysis and Design of
  Buildings, Part IIa — Steel Moment Frames*.
- **`ir.lib.uwo.ca/csce2016/London/Structural/21`** — UWO STR-833 (Steneker &
  Wiebe), panel-zone modelling.
- **`peer.berkeley.edu`** — Kolozvari (2019), RC shear-wall models.

## Chinese

- **`hanlindong.com/2019/opensees-converge/`** — 董翰林's blog, *如何使OpenSees模型收敛？
  SmartAnalyze帮助您*: the `-2` vs `-3` error-code diagnosis and the SmartAnalyze
  algorithm / time-step / tolerance fallback ladder.
- **`jdcui.com/?page_id=1238`** — 崔济东's *OpenSees 软件笔记* Q&A thread: IMK
  hinge calibration, series/parallel materials, UmfPack `LVALUE too small`,
  non-convergence diagnosis, damping inconsistencies.

## Turkish

- **`dergipark.org.tr/en/download/article-file/2136610`** — Caglar & Tatar,
  *Fiber Based Modeling Strategies of RC Columns*: Concrete04-confined core +
  Concrete01 cover, physical regularisation, plastic-hinge length = section
  depth.
- **`open.metu.edu.tr`** — METU theses: steel shear-link `J2BeamFiber`;
  semi-rigid-connection beam integration.
- **`web.itu.edu.tr`** — ITU OpenSees-vs-SeismoStruct comparison presentation.

## Portuguese

- **`hdl.handle.net/10362/11484`** — *Modelação tridimensional de estruturas
  sujeitas a sismos utilizando o OpenSees* (NOVA Lisboa, steel).
- **`repositorio-aberto.up.pt`** — *ANÁLISES PUSHOVER* (FEUP/Porto, concentrated
  vs distributed plasticity).
- **`hdl.handle.net/10362/152021`** — *Análise Não Linear de Pórticos de BA
  Reforçados com Pré-Esforço* (NOVA Lisboa, OpenSeesPy `nonlinearBeamColumn`).
- **UNICAMP** (`doi.org/10.47749/t/unicamp.2006.377861`) — Fontana, physical
  nonlinearity in RC buildings.

## Spanish

- **`jpi-ingenieria.com/osp_notes.html`** — OpenSeesPy course notebooks (RC
  frames + walls, static and dynamic).
- **`github.com/odarroyo/openseespy_starting_tutorials`** — Spanish OpenSeesPy
  tutorials for RC fibre frames.
- **CEINCI-LAB** (`doi.org/10.24133/riie.v25i3.1691`) — Ecuador, pushover of
  steel frames.
- **UniAndes** (`redcol.minciencias.gov.co`) — *Guía para la modelación y
  análisis no lineal de muros de concreto reforzado con OpenSees*.

## Thai

- **`digital.car.chula.ac.th`** — Chulalongkorn University theses (Khin Khin Moe
  2023, `chulaetd/11964`; Thin Zar Win, `chulaetd/11984`): fibre-based
  `dispBeamColumn` RC columns, confined / unconfined concrete fibres.
  (Thai-language OpenSees material is thin — academic theses only; no active
  Thai forum was found.)
