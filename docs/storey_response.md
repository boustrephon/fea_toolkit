# Storey-level Response Methodology

This document describes the mathematical approach used by
`fea_toolkit.model.storey_response` to compute storey displacements,
drifts, and shears from FE nodal results.

## 1. Node-to-Storey Assignment

Nodes are grouped by Z-proximity to the identified storey elevations.
A node within `z_tolerance / 2` of a storey elevation is assigned to
that storey.  A node at a storey boundary goes to the nearest level.

## 2. Centre of Mass (CM)

The reference point for each storey is its **mass centroid**:

```
x̄ = Σ (mᵢ · xᵢ) / Σ mᵢ
ȳ = Σ (mᵢ · yᵢ) / Σ mᵢ
```

where mᵢ is the seismic mass at node i (from the mass source defined
in the SAP2000 model).  If nodal masses are not available, a geometric
centroid (equal weight per node) is used instead.

The CM is the natural reference because inertial forces act through it,
and when Rz = 0 the fitted (Ux, Uy) are simply the mass-weighted
average displacement.

## 3. Rigid-Body Fit

For each storey, nodal displacements (uxᵢ, uyᵢ) at positions (xᵢ, yᵢ)
are fitted to a rigid-body field about the CM (x̄, ȳ):

```
uxᵢ = Ux − Rz · (yᵢ − ȳ)
uyᵢ = Uy + Rz · (xᵢ − x̄)
```

In matrix form:

```
[1   0   −(yᵢ−ȳ)]   [Ux]   [uxᵢ]
[0   1    (xᵢ−x̄)] · [Uy] = [uyᵢ]
                     [Rz]
```

This 2n × 3 system is solved via ``numpy.linalg.lstsq`` (minimising
the sum of squared residuals).

### 3.1 Outlier Rejection

A two-pass approach removes nodes that do not move with the floor:

1. **Pass 1**: fit all nodes → compute residual rᵢ = sqrt((uxᵢ − ux̂ᵢ)² +
   (uyᵢ − uŷᵢ)²) at each node.
2. **Flag**: reject nodes where rᵢ > 3 × median({rᵢ}).
3. **Pass 2**: re-fit using only inlier nodes.

The number of rejected nodes is reported so the engineer can assess
whether the rigid-body assumption is appropriate.

## 4. Peak Displacement

The fitted (Ux, Uy, Rz) are evaluated at **every** node position on the
storey to compute the resultant:

```
dᵢ = sqrt((Ux − Rz·(yᵢ−ȳ))² + (Uy + Rz·(xᵢ−x̄))²)
Peak = max(dᵢ)
```

This captures torsional amplification.  For a long building like the
pumphouse (80.7 m × 11.7 m), even a small Rz produces a significantly
larger displacement at the gable ends than at the CM.

## 5. Inter-Storey Drifts

Between consecutive storeys j and j−1:

| Quantity | Formula |
|---|---|
| Drift_X | (Uxⱼ − Uxⱼ₋₁) / hⱼ |
| Drift_Y | (Uyⱼ − Uyⱼ₋₁) / hⱼ |
| Drift_Rz | (Rzⱼ − Rzⱼ₋₁) / hⱼ |
| Drift_peak | sqrt(Drift_X² + Drift_Y²) + \|Drift_Rz\| · r_max |

where hⱼ = Zⱼ − Zⱼ₋₁ is the storey height and r_max is the maximum
distance from the CM to any node on storey j (a conservative bound
for the worst-node drift).

## 6. Modal Drift — CQC Combination

For each mode *m*:

1. Extract nodal eigenvector displacements (uxᵢ, uyᵢ) from the mode
   shape.
2. Fit rigid-body per storey → (Uxₘ, Uyₘ, Rzₘ) per storey.
3. Compute per-mode inter-storey **peak drift** via the same
   ``Drift_peak`` formula used for static load cases
   (``Drift_peak = sqrt(Drift_X² + Drift_Y²) + |Drift_Rz| · r_max``).

The per-mode peak drifts are then combined across modes using the
Complete Quadratic Combination (CQC) formula (Der Kiureghian, 1981):

```
Drift_total = sqrt( Σᵢ Σⱼ ρᵢⱼ · Driftᵢ · Driftⱼ )
```

The correlation coefficient ρᵢⱼ depends on the frequency ratio
r = fᵢ / fⱼ and the damping ratio ζ:

```
ρᵢⱼ = 8·ζ²·(1+r)·r^1.5 / ((1−r²)² + 4·ζ²·r·(1+r)²)
```

## 7. Storey Shears

Storey shear forces are computed by summing the element-end forces
at all nodes assigned to each storey level.  Each element contributes
its force vector [Fx, Fy, Fz, Mx, My, Mz] at both the I-end and J-end
to the storey containing that node.  This gives the net force
transmitted through the storey.

## 8. Flexible Diaphragm Detection

The `RMS_residual` column returned by :func:`storey_displacements`
serves as a flexible diaphragm indicator.  To interpret it, compare
against `Peak_disp` from the same row:

| `RMS_residual / Peak_disp` | Interpretation |
|---|---|
| < 0.1 | Rigid diaphragm — fit is excellent |
| 0.1 – 0.3 | Some in-plane flexibility |
| > 0.3 | Likely flexible diaphragm — consider reporting both rigid-body and average displacement |

The function does not compute this normalised ratio directly — the
engineer can derive it as ``RMS_residual / Peak_disp`` from the
returned DataFrame.  If the rigid-body fit is poor, consider using
peak nodal displacement instead of the fitted rigid-body values.

## References

- Der Kiureghian, A. (1981). "A response spectrum method for random
  vibration analysis of MDOF systems." *Earthquake Engineering &
  Structural Dynamics*, 9(5), 419–435.
- Wilson, E. L., Der Kiureghian, A., & Bayo, E. P. (1981). "A
  replacement for the SRSS method in seismic analysis." *Earthquake
  Engineering & Structural Dynamics*, 9(2), 187–194.
