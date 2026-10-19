---
title: "Cantilever Benchmark (T1)"
description: "Closed-form vertical-cantilever benchmark: static tip deflection PL³/(3EI) against the Euler-Bernoulli cantilever."
status: "complete"
tags: [validation, benchmark, cantilever, static, closed-form]
category: [planning]
related: [benchmark_register.md, vecchio_emara_benchmark.md]
---

# Cantilever Benchmark (T1)

## Purpose

The simplest possible anchor for the static lateral path: a fixed-free vertical
cantilever under a lateral tip point load, compared against the Euler-Bernoulli
closed form `δ = P L³ / (3 E I)`.

## Model

- Single vertical column, length `L = 10 m`, isotropic bending
  `I33 = I22 = I = 1e-3 m⁴`, `E = 2e11 Pa`, `A = 0.1 m²`.
- Base node fully fixed; tip free.  One `elasticBeamColumn`.
- One lateral tip point load `P = 1e4 N` in the `LAT` pattern.

## Reference

`δ = P L³ / (3 E I) = 1e4 · 1000 / (3 · 2e11 · 1e-3) = 1.6667e-2 m`.

## Acceptance

T1 — closed form.  The test asserts the tip `UX` displacement matches the
reference to within **±0.01 %** (`rel=1e-4`).

## Implementation

`tests/test_benchmark_cantilever.py`.
