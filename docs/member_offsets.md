---
title: "Member End Offsets & Cardinal Points (Insertion Point)"
description: "How SAP2000 frame end offsets and cardinal-point (insertion-point) offsets are carried onto the OpenSees frame element, the -jntOffset mechanism decision, and the shared local↔global coordinate transformation."
status: "complete"
tags: [offsets, cardinal-point, insertion-point, rigid-zones, jntOffset, geomTransf, frames, sap2000]
category: [model-features]
related: [parser_coverage.md, report_generation.md, builder_reference.md, diaphragm_constraints.md, member_releases.md]
---
# Member End Offsets & Cardinal Points (Insertion Point)

A SAP2000/ETABS frame object can be drawn on a **cardinal point** (insertion
point) — a point on the section's bounding box numbered 1–11, where 10 is the
centroid and 11 the shear centre — and can carry explicit **end length
offsets**.  The line through the joints (the *reference line*) is then offset
from the member's *elastic* (centroid) line.  CSI models that offset as a
**rigid** connection, so the member flexes from its centroid line while the
joint is held at the reference line.

This document records how `fea_toolkit` carries that offset onto the OpenSees
domain, and why the native `-jntOffset` mechanism is the default.

## 1. Data model

- `FrameEndOffset` (`model/sap_data.py`) holds six offsets plus a rigidity
  factor:
  - `end_i` / `end_j` — longitudinal, along the local 1‑axis;
  - `off_y_i` / `off_z_i` / `off_y_j` / `off_z_j` — lateral, along local 2 / 3;
  - `rigid_factor` — partial-rigidity factor (`0 < RigidFactor < 1`), currently
    always treated as fully rigid.
- `FrameElement.cardinal_point` (1–11, default 10) plus `mirror_2` / `mirror_3`
  / `transform_stiffness` (the *"do not transform stiffness for offsets from
  centroid"* flag).
- The parser folds the cardinal point and the section centroid offset into the
  lateral offsets at parse time via
  `SAP2000Parser._merge_cardinal_into_offsets()` /
  `_cardinal_point_offset()`, writing the result into
  `FrameEndOffset.off_y_* / off_z_*`.

## 2. Current state — carried to every consumer

Both the longitudinal and the lateral end offsets now reach the analysis model,
the GUI viewer and the Rhino export through the one shared helper
:func:`fea_toolkit.model.geometry_core.frame_end_offsets_global` (see §5):

| Offset | Consumer | Mechanism |
|---|---|---|
| `end_i`, `end_j` (longitudinal) | `opensees/_elements.py` (default `"jntOffset"`), `plotting/viewer.py`, `rhino/geometry.py` | native `-jntOffset` in `geomTransf`; stiff-beam / `rigidLink` MPC fallbacks (`"beam"` / `"mpc"`) |
| `off_y_i/z_i`, `off_y_j/z_j` (lateral) | `opensees/_elements.py`, `plotting/viewer.py`, `rhino/geometry.py` | the same `-jntOffset` rotation |

*Historical gap (now closed — P38).*  The lateral offsets were originally
consumed by `rhino/geometry.py` **only**, so a frame placed on a non-centroid
cardinal point (e.g. a beam drawn top-of-slab) was extruded at the offset but
**modelled at the reference line** in OpenSees: the drawn model and the analysis
model disagreed, and the offset stiffness was missing entirely.  That is the gap
the completed P38 work closed.

## 3. Mechanism decision

| Option | Mechanism | Applies to | Notes |
|---|---|---|---|
| `"jntOffset"` (**default**) | `-jntOffset` in `geomTransf` | Linear, PDelta, Corotational | native; exact; no extra nodes/MPC/stiff beams; offsets are **global** (needs a local→global rotation); fixed-vector small-offset assumption |
| `"beam"` | stiff elastic beams (≈1000× member stiffness) | all | universal fallback; ill-conditions under PDelta; adds orphan-node bookkeeping; under-counts rigid-zone mass (see §9) |
| `"mpc"` | `rigidLink("beam")` | all | true MPCs; **silently breaks** with diaphragm / BODY constraints (DOF conflict) |

**Default is `"jntOffset"`.**  It is the native CSI-equivalent ("always fully
rigid"), avoids the MPC/constraint-handler interaction, and avoids the
stiff-beam ill-conditioning.  `"beam"` is kept as an opt-in fallback for parity
and for any situation where the fixed-vector assumption below is unacceptable;
`"mpc"` is retained only for pure-frame models with no conflicting constraints.

## 4. Verified facts about `-jntOffset` (source-level)

1. **It exists for `Linear`, `PDelta` and `Corotational`, in 2D and 3D.**
   Verified in the current OpenSees source: `LinearCrdTransf3d.cpp`
   (`OPS_LinearCrdTransf3d`) and `CorotCrdTransf3d.cpp`
   (`OPS_CorotCrdTransf3d`) both parse `-jntOffset` into two 3‑vectors.  The
   old wiki's omission of 3D `Corotational` is **stale** — the source supports
   it.
2. **The offsets are in GLOBAL coordinates.**  In
   `LinearCrdTransf3d::computeElemtLengthAndOrient` the offset vectors are
   added directly to the global node-to-node `dx` before the local x‑axis is
   computed (`dx += nodeJOffset; dx -= nodeIOffset;`).  There is **no**
   local-frame rotation — the wiki's *"global coordinate system"* wording is
   correct.  This is why the local offsets need a rotation before use (see §5).
3. **The offset is a fixed vector** applied at the initial configuration and
   not updated with the member's finite rotation — a small-offset assumption,
   consistent with CSI's own small-offset insertion-point treatment.  Acceptable
   for typical end offsets (~member depth); an approximation under large
   displacement (`Corotational`).
4. **The element length `L` becomes the flexible (centroid-to-centroid)
   length**, so `eleLoad` distributes over that shortened length.  Interaction
   with the existing load-splitting must be verified (see §7 step 2).
5. **OpenSeesPy passes it straight through** —
   `ops.geomTransf("Linear", tag, *vecxz, "-jntOffset", *offI, *offJ)`.  The
   type stub `typings/openseespy/opensees/__init__.pyi` documents the `Linear`,
   `PDelta` and `Corotational` `-jntOffset` forms plus the global-coordinates
   note (§7 step 5).

## 5. The coordinate transformation (shared, generalised)

The offsets are authored in **local** coordinates but `-jntOffset` consumes
**global** ones, so the toolkit needs a reusable local↔global rotation.  Rather
than a one-off in the builder, this is a small generalised pair in the canonical
geometry module, `model/geometry_core.py`, reusing the existing orthonormal
basis from `get_local_axes()`:

```python
def local_to_global(axes, vec):
    """Rotate a local (1, 2, 3) vector into global coordinates."""
    vx, vy, vz = axes
    return vx * vec[0] + vy * vec[1] + vz * vec[2]

def global_to_local(axes, vec):
    """Rotate a global vector into local (1, 2, 3) coordinates."""
    vx, vy, vz = axes
    return np.array([np.dot(vx, vec), np.dot(vy, vec), np.dot(vz, vec)])
```

- `axes = get_local_axes(axis, angle)` supplies the orthonormal `(vx, vy, vz)`
  following the CSI default frame-local convention.
- The pair is the single spelling for the offset pipeline: the builder, the
  GUI viewer and the Rhino export all derive their vectors through
  :func:`frame_end_offsets_global` (which calls :func:`local_to_global`),
  rather than each site repeating its own local-to-global rotation.

## 6. Sign convention

OpenSees computes the flexible span as
`(nodeJ + nodeJOffset) − (nodeI + nodeIOffset)`.  Matching the centroid-line
convention already used by the Rhino exporter, the six global offsets are:

```
nodeIOffset = local_to_global(axes, [  end_i, off_y_i, off_z_i])
nodeJOffset = local_to_global(axes, [ -end_j, off_y_j, off_z_j])
```

so the flexible member runs centroid‑to‑centroid and the rigid offset bridges
each joint back to the reference line.

## 7. Implementation plan

1. **Coordinate transform + shared helper.**  Add `local_to_global` /
   `global_to_local` to `model/geometry_core.py` and a shared
   `frame_end_offsets_global(axis, angle, offset) -> (off_i, off_j)` (or extend
   an existing centroid-endpoint helper) so the builder, GUI and Rhino all
   derive the same vectors.  Unit-test the rotation on a non-axis-aligned member
   (round-trip `global_to_local(local_to_global(v)) == v` and a hand-checked
   case).
2. **Builder emission.**  In `ElementMixin._add_beam_column`
   (`opensees/_elements.py`), when `rigid_offset_method == "jntOffset"`, append
   `"-jntOffset", *off_i, *off_j` to the existing `geomTransf` call and skip the
   offset-node / rigid-link path.  **Verify `eleLoad`** is distributed over the
   shortened flexible length `L`, not the reference line.
3. **Offset-node exclusion.**  Keep the offset-node exclusion from
   diaphragm/BODY sweeps — it is needed by the `"beam"` fallback and by the
   pre-existing longitudinal rigid-link path regardless of the default.
4. **MPC guard.** ✅  Confine `rigid_link_mpc` to the `"mpc"` option (the
   builder's offset-link emission now resolves the flag through
   `_rigid_offset_method()` rather than reading the legacy boolean directly)
   and warn when it coexists with diaphragms/BODY constraints.
5. **Stub.** ✅  Extend `typings/openseespy/opensees/__init__.pyi` `geomTransf`
   docstring with the `PDelta`/`Corotational` `-jntOffset` forms and a
   global-coordinates clarification.
6. **GUI / Rhino parity.** ✅  Thread the same `frame_end_offsets_global()`
   through the GUI frame mesh and the Rhino export so the drawn member, the
   analysis model and the exported geometry agree: `ModelViewer` now shifts each
   ``FrameGeom`` to the centroid line (``plotting/viewer.py``
   ``_offset_frame_endpoints``), and the Rhino exporter derives the same full
   I/J centroid endpoints from the shared helper (``rhino/geometry.py``
   ``_get_frame_points``) and sweeps the extrusion along that centroid line
   instead of the full reference length.
7. **Stiff-beam fallback stiffness.** ✅  For `"beam"`, scale the elastic link
   stiffness ≈1000× the stiffest member's axial (`E·A`) and flexural (`E·I`)
   stiffness in model units (`ElementMixin._rigid_section_properties()` in
   `opensees/_elements.py`) instead of the hardcoded `2.0e14` (wrong in kN-m
   and mm unit systems).  Falls back to a dimensionless `1.0` placeholder when
   the model has no usable section/material stiffness.
8. **Tests + docs.** ✅  Rotation, `-jntOffset` emission (incl. the Corotational
   form), the eleLoad length and the fallback stiffness are all tested; P38 is
   closed here and in `docs/parser_coverage.md` (the `off_y/off_z` consumer
   moved from *"rhino only"* to the builder / viewer).

## 8. Config naming

A single key, `rigid_offset_method` ∈ `{"jntOffset" | "beam" | "mpc"}`,
default `"jntOffset"`.  The existing `rigid_link_mpc` boolean maps onto it for
back-compatibility (`True` → `"mpc"`, `False` → `"jntOffset"`).

## 9. Open items / risks

- **`eleLoad` over the flexible length** — verified (step 2).  OpenSees applies
  ``eleLoad`` over the shortened flexible length between the ``-jntOffset``
  ends, not the reference line — pinned by
  ``test_jnt_offset_eleload_over_flexible_length`` in
  ``tests/test_analysis_csm.py``.
- **Fixed-vector approximation** for `Corotational` large-displacement models —
  document, and keep `"beam"` as the escape hatch.
- **Stiff-beam mass deficit** (`"beam"` fallback only).  The rigid links are
  emitted massless (`-mass 0.0`, `Elastic` section with no density), and the
  member is shortened by the longitudinal offsets `end_i + end_j`, so the
  rigid-zone self-weight/mass `A × ρ × (end_i + end_j)` is **under-counted**,
  not over-counted.  Only the **axial** offsets reduce the mass — the lateral
  cardinal-point offsets are perpendicular to the member axis (and, in the
  `"beam"` path, never reach the analysis model), so they neither add nor
  remove mass.  The `"jntOffset"` default keeps the full reference-line
  geometry and therefore has no such deficit.
- **Partial rigidity** (`rigid_factor`) remains out of scope (tracked as P16).
