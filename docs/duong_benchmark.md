---
title: "Duong et al. (2007) Shear-Critical Frame Benchmark (T3)"
description: "End-to-end validation of the simplified-MCFT shear-failure modelling against the published Duong et al. (2007) shear-critical RC frame experiment, as re-analysed by Guner (2008) and Kotsovos & Zygouris (2019)."
status: "complete"
tags: [validation, benchmark, shear, mcft, duong, rc-frame, pushover, shear-critical]
category: [planning]
related: [shear_failure_modelling.md, vecchio_emara_benchmark.md, benchmark_register.md]
---
# Duong et al. (2007) Shear-Critical Frame Benchmark (T3)

## Purpose

The **T3** (experimental-database) anchor for the shear-failure modelling
path: the simplified-MCFT member shear-capacity model and the nonlinear
shear backbone (`SectionAggregator`) are validated against the shear-critical
frame experiment of Duong, Sheikh & Vecchio (2007).

## Model

The two-storey shear-critical RC frame of Duong et al. (2007); the fixture
`make_duong_frame()` lives in `tests/test_duong_benchmark.py` (kN-m units,
planar X–Z per the 3D-only policy).  The first-storey beam is the critical
member (simplified-MCFT capacity ≈ 295 kN).

## Reference

Duong, K.V., Sheikh, S.A. & Vecchio, F.J. (2007), "Seismic Behavior of
Shear-Critical Reinforced Concrete Frame: Experimental Investigation", *ACI
Structural Journal* 104(3):304–313.  Re-analysed by Guner (2008) §2.3.6/§4.8
and Kotsovos & Zygouris (2019), *Magazine of Concrete Research* 71(3):109–125.

## Acceptance

T3 — experimental database.  The mode-of-failure reporter flags the
first-storey beam as shear-critical, and the simplified-MCFT capacity is
reproduced against the published re-analysis.  The full modelling rationale,
the two-phase validation and the capacity equations are in
[shear_failure_modelling.md](shear_failure_modelling.md).

## Test

`tests/test_duong_benchmark.py`.
