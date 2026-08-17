---
title: "Units Workflow (kN·m·s → kip·in Limit-State Analysis)"
description: "Unit-system conversion for MeshModel objects (convert_mesh_units), the kip-in-ksi enabler behind the Elwood column limit-state (Phase 3) analysis of SI models. Round-trip exact, with the full kN·m·s workflow, conversion factors and results back-conversion."
status: "complete"
tags: [units, conversion, kip-in, si, elwood, limit-state]
category: [model-features]
related: [shear_failure_modelling.md]
---
# Units workflow — kN·m·s model to kip-in limit-state analysis

The OpenSees ``limitCurve`` commands behind the toolkit's Elwood column
limit-state modelling (Phase 3 — see ``shear_failure_modelling.md``) embed
**imperial units in their empirical constants**: forces in **kip**, lengths
in **in**, and ``f'c`` in **psi**.  The ``LimitState`` material built on them
feeds force back into the element through zero-length springs, so its
envelope forces/slopes must be in the *same* units as the OpenSees domain.
The only clean, validated pairing is a domain running in **kip-in-ksi**.

This document traces the complete unit workflow for the primary use case: a
500 m outrigger tower whose SAP2000 model arrives in **kN·m·s** units
(force = kN, length = m, stress = kPa, mass = tonne = kN·s²/m).

## 1. The three unit layers (why the workflow exists)

| Layer | Units | Notes |
|---|---|---|
| OpenSees domain + ``LimitState`` springs | **kip, in, ksi** | consistent F/L/stress system; drift ratios are dimensionless |
| ``limitCurve Shear`` ``fc`` input | **psi** | OpenSees's empirical constants were fit with f'c in psi while forces are kip and lengths in — a deliberate unit inconsistency in the C++ source (×1000 from the model's ksi value) |
| ``limitCurve ThreePoint`` (axial) | **drift, kip** | shear-friction surface needs no stress input |

Only the **shear** curve's ``fc`` is fed in psi; everything else in the
limit-state path is kip / in / kip·in⁻¹.

## 2. Converting the MeshModel: `convert_mesh_units()`

``fea_toolkit.model.units.convert_mesh_units(mesh, {"L": "in", "F": "kip"})``
returns a **deep copy** rescaled from the model's current ``units`` dict to
the target force/length system (the input is never mutated).  All factors are
target/source ratios of the unit factors in ``fea_toolkit.utils``:

| Quantity | kN·m·s → kip·in factor | Example |
|---|---|---|
| length (node coords, section geometry) | × 39.3701 | 4.0 m → 157.48 in |
| force (joint loads, axial P) | × 2.2482e-4 | 1000 kN → 224.8 kip |
| moment (F·L) | × 8.852e-3 | 1000 kN·m → 8.852 kip·in |
| force/length (distributed loads) | × 5.710e-6 | 100 kN/m → 0.571 kip/in |
| pressure (F/L²) | × 1.4504e-4 | 10 kN/m² → 1.45e-3 kip/in² |
| **stress** (E, f'c, fy, tie_fy) | × 1.4504e-4 | 40 MPa (=4e4 kPa) → 5.80 ksi |
| section A / I / J / Z | × L² / L⁴ / L³ | 0.25 m² → 387.5 in² |
| mass density (kg/m³) | × 9.356e-11 | 2450 kg/m³ → 2.29e-7 kip·s²/in⁴ |

Dimensionless quantities — strain, reinforcement ratios, gravity load
multipliers, parametric positions, restraint flags — are left untouched.

## 3. Step-by-step workflow (kN·m·s tower)

1. **Parse** the ``.s2k`` into ``SAPModelData`` with ``units = {"L": "m",
   "F": "KN"}``.  Materials are authored in SI (Pa) and scaled **once** to
   model units (kPa) by the Preprocessor via ``scale_material_dict()``
   (guardrail §4.6).  Section geometry is in m; loads in kN, kN·m, kN/m,
   kN/m²; masses in tonne.

2. **Preprocess** to ``MeshModel`` (node/element splitting, meshing, load
   redistribution) — still kN·m·s.

3. **Convert**::

       from fea_toolkit.model.units import convert_mesh_units, KIP_IN_UNITS
       kip_mesh = convert_mesh_units(mesh, KIP_IN_UNITS)   # kip-in-ksi copy

   Now node coordinates are in inches, section geometry in inches (A in in²,
   I in in⁴), materials in ksi (``Fc=5.80`` for C40), loads in kip / kip·in /
   kip/in, and ``kip_mesh.units == {"L": "in", "F": "kip"}``.  The copy is
   round-trip exact: converting it back to kN·m·s reproduces the original
   model (verified by ``tests/test_mesh_units.py``).

4. **Build the domain** in kip-in-ksi.  The AnalysisBuilder emits the
   Elwood limit-state springs for the selected columns:

   - ``limitCurve Shear`` — ``rho`` (dimensionless), ``fc`` in **psi**
     (= ksi value × 1000, e.g. 5800 psi for C40), ``b``/``h``/``d`` in in,
     ``F_sw`` in kip, ``Kdeg`` in kip/in, ``Fres`` in kip.
   - ``limitCurve ThreePoint`` (the OpenSeesPy ``Axial`` binding workaround)
     — ``(drift, kip)`` points pinned to the operating gravity axial load,
     ``Kdeg`` kip/in, ``Fres`` kip.
   - ``uniaxialMaterial LimitState`` — envelope ``(force kip, deformation in)``
     pairs on the elastic slopes ``G·A_v/L`` and ``99·E_c·A_g/L`` (kip/in).
   - ``zeroLength`` shear + axial springs with MPC rigid links at the column
     mid-height.

   Everything downstream of step 3 — materials, ``Concrete01``/``Steel02``,
   ``nonlinearBeamColumn`` fiber sections, gravity (``g_from_units`` now
   returns in/s²), loads, mass — is consistent kip-in-ksi.

5. **Run** the pushover / transient (drift-based failure triggers are
   dimensionless, so the published 2.1 % / 5.2 % drift sequence reproduces
   exactly as validated in ``local/elwood_prototype.py``).

6. **Report back in kN·m·s**: multiply displacements (in) by 0.0254,
   forces (kip) by 4448, moments (kip·in) by 112.98 kN·m/kip·in, stresses
   (ksi) by 6.895 MPa/ksi.  Drift ratios and DCR values need no conversion.

## 4. Scope and limitations

- **Converted**: nodes, sections (geometry + A/I/J/Z + tie_fy), materials
  (E, G, f'c, fy, weights, mass), frame/area/wall elements, distributed /
  joint / area loads, diaphragm metadata.
- **Not converted** (a ``UserWarning`` is emitted if present):
  ``nd_materials`` (FSAM nD materials) and ``layered_shell_sections`` —
  their stress fields need a dedicated scaling pass.  Wall FSAM fibre
  materials are named references and remain valid after conversion.
- The round-trip guarantee holds for the supported surface; any future
  extension to the conversion must add a matching round-trip case.

## 5. References

- ``fea_toolkit/model/units.py`` — implementation.
- ``fea_toolkit/utils.py`` — ``length_scale_factor`` / ``force_scale_factor`` /
  ``stress_scale_factor`` / ``mass_density_scale_factor`` (SI → model).
- ``tests/test_mesh_units.py`` — multiplier spot-checks, kip-in spot-checks,
  N·m → kip·in → N·m round trip, input-immutability.
- ``docs/shear_failure_modelling.md`` (Phase 3) — the limit-state physics and
  the OpenSeesPy ``limitCurve`` binding constraints.

