---
title: "Three-Storey Shear-Building Benchmark (T2)"
description: "Przemieniecki/Paz three-DOF shear-building benchmark: modal periods and CQC response-spectrum base shear against the closed-form generalised eigenproblem."
status: "complete"
tags: [validation, benchmark, shear-building, modal, response-spectrum, cqc, closed-form]
category: [planning]
related: [benchmark_register.md, modal_analysis.md]
---

# Three-Storey Shear-Building Benchmark (T2)

## Purpose

The canonical three-DOF system for modal and response-spectrum validation
(Przemieniecki 1968 stiffness; Paz 1985 modal; Wilson et al. 1981 CQC).  A
three-storey shear building with equal story stiffness `k` and equal floor mass
`m` reduces to the tridiagonal generalised eigenproblem

```
K = k · [[ 2, -1,  0], [-1,  2, -1], [ 0, -1,  1]],   M = m·I
```

whose eigenvalues are `λᵢ = 2 − 2 cos((2i−1)π/7)`.

## Model

- Four nodes at `z = 0, 3, 6, 9 m`; base fixed; each floor restrained to a
  single lateral (`UX`) DOF — a planar 3D model per the project's 3D-only
  policy.
- Three `elasticBeamColumn` columns, isotropic `I = k·h³/12E = 2.25e-4 m⁴`
  with `E = 2e11 Pa`, so each story stiffness is exactly `k = 2e7 N/m`.
- Floor mass `m = 2e4 kg` each, assigned through a load-based MASS SOURCE
  (`DEAD` joint loads), so the mass matrix is exactly `m·I`.

## Reference

- **Periods**: the three generalised eigenvalues `T = 2π/ω` of `(K, M)`,
  solved in the test with `numpy.linalg.eigh` (no hardcoded digits).
- **CQC base shear**: under a flat spectrum `Sa = 1 m/s²` and `ζ = 0.05`, the
  modal base shear is `Vᵢ = Sa·Γᵢ²` and the CQC combination
  `√(Σᵢⱼ ρᵢⱼ Vᵢ Vⱼ)` uses Der Kiureghian's `ρᵢⱼ`, evaluated in the test.

## Acceptance

- Periods: **±0.1 %** (`rel=1e-3`).
- CQC base shear: **±2 %** (`rel=2e-2`).

## Implementation

`tests/test_benchmark_shear_building.py`.
