---
title: "Tcl Model Import"
description: "OpenSees Tcl → SAPModelData importer for SAC-style elastic plane frames, including the 2-D to planar-3-D lift."
status: "complete"
tags: [io, tcl, sac, import, benchmark]
category: [planning]
related: [benchmark_register.md, ground_motion_data.md]
---

# Tcl Model Import

## Purpose

`parse_tcl_model()` imports an OpenSees Tcl model script (the SAC benchmark
frames and similar elastic plane frames) into `SAPModelData`, so a SAC model
flows through the standard Preprocessor → AnalysisBuilder pipeline.

## Scope

Supported commands: `model`, `node`, `fix`, `mass`, `geomTransf`,
`element elasticBeamColumn`, and `pattern … { load … }`.  Analysis/solver
commands are skipped silently; any *model* command that is not understood
(`eleLoad`, non-elastic elements, non-Elastic materials) emits a
`UserWarning` rather than being dropped silently.

## 2-D → planar 3-D lift

The SAC frames are `-ndm 2 -ndf 3`.  The importer lifts them to the toolkit's
planar-3-D convention (`.clinerules` §3.11):

- node `(x, y)` → `(x, 0, y)` (the vertical axis becomes `Z`);
- fixity `[UX, UY, RZ]` → `[UX, UZ, RY]`, with the out-of-plane
  (`UY`, `RX`, `RZ`) DOFs always restrained;
- `mass` / `load` components map `Y → Z` and `Mz → My`;
- section bending is isotropic (`I33 = I22 = I`), so the in-plane stiffness is
  exact and independent of the element's local-axis orientation.

## Units

The Tcl script does not declare units; pass them explicitly (SAC models are
`{"F": "kip", "L": "in", "T": "C"}`).

## Implementation

`src/fea_toolkit/io/tcl_importer.py`; tests in `tests/test_tcl_importer.py`.
