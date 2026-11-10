---
title: "OpenSees GUIs — Comparison and fea_toolkit Positioning"
description: "How fea_toolkit compares against the 15 OpenSees GUIs reviewed by Shabani (2023), mapped onto the article's own feature axes, with prioritized feature additions."
status: "complete"
tags: [comparison, opensees, gui, roadmap, analysis-types, interoperability, ssi, ida, pbee]
category: [planning]
related: [gui_roadmap.md, analysis.md, nonlinear_dynamic_analysis.md, benchmark_roadmap.md, modeling_guidance.md]
---

# OpenSees GUIs — Comparison and fea_toolkit Positioning

> **Status:** ✅ Complete — comparison current to Shabani (2023) and tool
> documentation reviewed 2026-09-11.  Conclusions at the end are proposals,
> not commitments.

## 1. What this compares

This note maps **fea_toolkit** against the **15 OpenSees GUIs** reviewed in
*Shabani, A. (2023), "A review of graphical user interfaces of OpenSees
software framework", Front. Built Environ. 9:1233116* — 13 open-access and 2
commercial — using the article's own comparison axes.  It is *not* a benchmark
of numerical accuracy; it is a **capability-and-positioning** comparison.

The one framing fact to hold onto: those 15 are **pre/post-processor GUIs over
OpenSees**.  fea_toolkit is a **Python library + CLI (+ optional Qt GUI)** whose
center of gravity is *importing a model already built elsewhere* and running the
OpenSees analysis, then taking the results to Rhino/Grasshopper and NPZ-based
post-processing.  It competes conceptually with the OpenSeesPy-side tools the
article touches (OpenSeesPyView, OSLite) but with far greater analysis depth.

## 2. The reviewed landscape

| Tool | Open | Focus |
|---|---|---|
| **Build-X** | ✗ | RC buildings; expert-system suggestions; **SSI** (footings/basements, inertial+kinematic interaction) |
| **DYANAS** | ✓ | SDOF oscillators; IDA + multi-stripe + cloud (PBEE) |
| **FeView** | ✓ | PyQt/PyVista post-processor; 46 element types; nodal responses only |
| **FM-2D** | ✓ | Steel MRF/CBF/EBF + RC MRF (2D); IDA + fragility; parallel |
| **GiD + OpenSees** | ✓ | Full CAD modeller (via GiD); 1D/2D/3D; multi-domain **SSI**; LayeredShell; soil springs (PySimple1/QzSimple1/TzSimple1) |
| **Hyperomet** | ✓ | URM buildings (DM-MVLEM / Unified method); IDA (hunt-&-fill); *no* result visualisation |
| **INSPECT-SPSW** | ✓ | Steel plate shear walls (strip method, 2D); cyclic pushover |
| **MSBridge** | ✗ | Multi-span bridges; foundation soil springs; isolation bearings; PBEE loss plots; *exports to SAP2000 .s2k* |
| **NextFEMdesigner** | ✗ | General FEA pre/post (default OOFEM, also drives OpenSees); 20+ import/export formats; fiber sections free; code checks paid |
| **OpenSees Navigator** | ✗ | MATLAB; templates + **SAP2000 .s2k import**; AISC/CISC sections; AISC design checks; hybrid simulation (OpenFresco) |
| **OpenSeesPL** | ✗ | 3D pile–ground interaction; soil materials; liquefaction; pushover/mode/base-accel |
| **OpenSeesPyView** | ✓ | OpenSeesPy GUI; DXF export of model + mode shapes; modal only |
| **OSLite** | ✗ | Model viewer; DXF import (older versions only) |
| **eSEES** (commercial) | ✗ | "Model manager"; define *components* not nodes; defaults; one-click analyses |
| **STKO** (commercial) | ✗ | Full CAD; STEP/IGES; *all* OpenSees materials/elements; HDF5 MPCO; Python API; OpenSees MP parallel |

## 3. fea_toolkit on the article's axes

| Axis | fea_toolkit | Closest peers |
|---|---|---|
| Open-source | ✅ GPL-3.0-or-later | DYANAS, FeView, FM-2D, GiD+OpenSees, Hyperomet, INSPECT-SPSW, OpenSeesPyView |
| 3D modelling | ⚠️ import-driven (no from-scratch CAD) | GiD+OpenSees, NextFEM, STKO lead here |
| 3D visualisation | ✅ PyVista + interactive viewer + HTML + Rhino 8 | STKO, NextFEM, GiD+OpenSees |
| Model import/export | ✅✅ **best in class** — `.s2k`/`.e2k`/`.f2k` + JSON → NPZ/H5, Tcl, Rhino, JSON | OpenSees Navigator (`.s2k` in, partial), NextFEM (20+ formats), MSBridge (`.s2k` out) |
| Modal / Gravity / Pushover / Transient | ✅ all four | most general GUIs |
| Response spectrum | ✅ **extra, beyond the article's columns** — CQC, GB 50011 / IEC | NextFEM (paid), OpenSees Navigator |
| IDA | ✅ (scoped to `benchmarks/` — SAC + IK-2005, collapse criteria + bisection) | FM-2D, Hyperomet, DYANAS (SDOF) |
| SSI | ⚠️ reads SAFE foundation *support* springs only | Build-X, MSBridge, GiD+OpenSees, OpenSeesPL |
| Code/design checks | ⚠️ capacity modules (GB 50010, ASCE 41, DCR) but no unified check *report* | OpenSees Navigator (AISC), NextFEM (paid) |
| GUI | ⚠️ optional Qt (`[gui]` extra, Py 3.10+), secondary to the Python API | all 15 are GUI-first |
| Parallel | ✗ single interpreter | STKO (OpenSees MP), FM-2D |

## 4. Capability notes

- **CSI interoperability is the differentiator.**  Only OpenSees Navigator among
  the 15 imports SAP2000 `.s2k` (and it "currently does not convert element
  loads and nodal constraints"; the article's own tables mark most tools `✗`
  on import/export).  fea_toolkit parses SAP2000/ETABS/SAFE text into one
  `SAPModelData` and emits the OpenSees domain — a bridge none of the others
  offer at this depth.  (MSBridge runs the reverse direction: OpenSees → SAP2000.)
- **Analysis breadth.**  fea_toolkit covers every analysis column the article
  uses *plus* response spectrum and code-specified capacities.  Its IDA,
  however, lives in `benchmarks/` (SAC / IK-2005 generic-frame collapse) rather
  than the general `analysis/` pipeline — a scope, not an absence.
- **3D-only by design** sidesteps the 2D limitation the article flags on FM-2D,
  Hyperomet, INSPECT-SPSW and DYANAS (no torsional blind spot).
- **Nonlinear modelling depth.**  Fiber sections (Mander confinement), lumped
  hinges with four backbones (ASCE 41, bilinear, trilinear, collapse-level
  `IMKBilin`), `beamWithHinges`, `NDMaterial`/`LayeredShell` walls, MVLEM/SFI-MVLEM
  — deeper on member/hinge formulation than most reviewed GUIs.
- **No SSI platform, no parallel, no CAD authoring, no unified code-check
  report.**  These four are the real gaps, each owned by a subset of the 15.

## 5. Where fea_toolkit leads and trails

**Leads:** CSI import (SAP2000/ETABS/SAFE); analysis breadth (five-plus types
+ response spectrum); 3D-only discipline; nonlinear hinge/material surface;
reproducible recipe pipeline and NPZ/H5 exchange; Rhino/Grasshopper export.

**Trails:** non-programmer accessibility (Python-first); from-scratch 3D
modelling; SSI; parallel; unified code-check report; PBEE frameworks beyond IDA
(cloud, multi-stripe, fragility).

**Closest open-source analogue:** on analyses *and* visualisations together it
is **FeView** — the same Python + PyVista/pyvistaqt + matplotlib stack and the
same core four analysis types (modal, gravity/static, pushover, transient), so
fea_toolkit reads as a superset of it (adding response spectrum, IDA, deep
nonlinear modelling, CSI import, and NPZ/H5/Rhino output).  On IDA/fragility the
closest open-source match is **FM-2D**; on 3D modelling + general nonlinear + SSI
it is **GiD+OpenSees**.

## 6. Conclusions — prioritized features for fea_toolkit

Ordered by *impact on the gaps above* vs *effort*.  Each is tagged with the
reviewed tool that demonstrates the capability.

1. **First-class IDA in `analysis/` (promote from `benchmarks/`).**  The
   machinery exists (collapse criteria, bisection, record ladders); the article
   treats IDA automation as rare (3 of 15).  Promote `run_ida` into the main
   pipeline and add a `workflow` verb.  *(Peers: FM-2D, Hyperomet, DYANAS.)*
2. **Sub-structure SSI soil-spring generation.**  Build on `model/springs.py`
   to generate footing/basement spring stiffness from soil properties, not just
   read SAFE support springs.  The article's single most-cited missing feature.
   *(Peers: Build-X, MSBridge, GiD+OpenSees, OpenSeesPL.)*
3. **Broader geometry import (DXF, then STEP/IGES/IFC).**  Removes the hard
   dependency on having a CSI model.  DXF is the table-stakes level (OSLite,
   OpenSeesPyView, NextFEM); STEP/IGES/IFC is the STKO/NextFEM tier.
4. **Unified code-check report.**  Package the existing `capacity/` modules
   (GB 50010, ASCE 41, Elwood, simplified-MCFT, DCR) into a report step rather
   than scattered helpers.  *(Peers: OpenSees Navigator AISC checks, NextFEM.)*
5. **PBEE frameworks: cloud, multi-stripe, fragility.**  Natural extension of
   item 1 once IDA is first-class — intensity-measure ladders, cloud regression,
   fragility-curve output.  *(Peers: DYANAS, FM-2D.)*
6. **Model templates in the GUI.**  Stick-model / moment-frame / braced-frame
   presets so the tool is usable without a source model.  *(Peers: OpenSees
   Navigator, eSEES.)*
7. **OpenSees MP parallel.**  *(Peer: STKO, FM-2D.)*  Highest effort; defer
   unless large-model demand materialises — and note the OpenSeesPy commercial
   redistribution licence before any cloud/service use.
