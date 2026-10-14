# Section Designer (SD) sections — import and roadmap

> **Status: ⚠️ Partial (elastic only).**  The parser reads `$ SECTION DESIGNER
> SECTIONS` polygon loops and derives gross elastic properties (`A`, `I33`,
> `I22`, `J`); fiber conversion and several fidelity items remain open and are
> listed below so they can be picked up later.

## 1. What is implemented

`SHAPE "SD Section"` frames resolve to `SDSection` (not a zero-property
placeholder).  The polygon corners (`SHAPETYPE "POLYGON"` + `POLYCORNER … X …
Y …`) are read and their **gross elastic** properties computed by
`polygon_props()` in `model/section_properties.py` (shoelace / Green's-theorem
area, centroid and second moments), with `X` mapped to the local 3-axis and
`Y` to the local 2-axis (the ETABS **frame-section** default).  `REF LINE`
shapes are skipped.  Multi-polygon composites combine about the shared centroid
via the parallel-axis theorem.

The same polygon loops also drive the extruded representation:
`section_profile_outline()` / `section_plates()` emit the actual perimeter
(centred on the composite centroid) rather than the area-equivalent square the
`GeneralSection`-style fallback would draw.  A multi-polygon composite is
decomposed into **regions** — one outer boundary per disjoint part, each with
its holes — and each region is extruded separately.

## 2. Known limitations (addressable follow-ups)

1. **`J` is the polar second moment, not St. Venant.**  `polygon_props()`
   returns `J = I33 + I22`, which is exact for a circle but only an *upper
   bound* for an arbitrary solid section (it over-counts the warping restraint
   of a non-circular cross-section).  The true St. Venant torsional constant
   needs a section-tools computation — thin-wall `Σ b·t³/3` / Bredt for
   wall-type sections, or a Prandtl stress-function / boundary-element /
   finite-difference solve for solid arbitrary polygons.  This is the intended
   *"section tools"* work item (see §3).

2. **Fiber conversion is not implemented.**  `SDSection.to_fiber_patches()`
   raises `NotImplementedError`, so nonlinear (fiber) analysis of SD piers
   falls back to an elastic section.  The eventual path is polygon
   triangulation into concrete patches plus rebar along the `REF LINE` /
   reinforcement records, with per-shape materials (`BASEMATERIAL`, the
   `MATERIAL` per polygon, and rebar).

3. **Local-axis convention is not fully resolved.**  `I33`/`I22` follow the
   ETABS **frame-section** default (`X` → local 3, `Y` → local 2), but the
   `ANGLE` rotation token (present on every SDSECTION record) and the
   **wall-pier** (vs frame) section default are not yet applied.  A controlled
   ETABS export of a *known symmetric* SD section (e.g. a rectangle with
   `ANGLE 0`) should be used to pin down (a) whether `ANGLE` rotates the
   geometry or the local axes, and (b) the wall-pier axis mapping.  Until
   then `I33`/`I22` may be swapped for strongly asymmetric piers.

4. **Centroid offsets are not populated.**  `cg_offset_2`/`cg_offset_3` (and
   shear-centre `ecc_v2`/`ecc_v3`) stay at zero — the section reference-point
   convention needs the same controlled-export check as item 3.
5. **PyVista end caps for arbitrary hole shapes are not triangulated (future
   development).**  In the PyVista extrusion, a hole's annular end caps are only
   produced when the hole loop shares the outer loop's vertex count (``Q == P``,
   the box case); otherwise the annulus is left **open at the ends**.  Closing
   it needs polygon triangulation of the outer-minus-holes annulus, which is
   hard because (a) the outer and each hole are independent point lists with
   unrelated vertex counts, so there is no trivial edge-pairing, and (b) the
   holes must be subtracted from the outer while keeping the cap winding outward
   for both profile orientations.  Rhino is unaffected — ``rg.Extrusion`` /
   ``AddInnerProfile`` generate their own (correct) caps.

## 3. Future "section tools" (planned)

A small pure-geometry toolkit under `model/` that `SDSection` (and the
parametric sections) can reuse:

- **St. Venant `J` for arbitrary polygons** — replace the polar-moment
  approximation: thin-wall summation / Bredt for thin-walled shapes, and a
  Prandtl / finite-difference / boundary-element solve for solid sections
  (matching how `rect_props` already uses the closed-form rectangular-bar
  series, but generalised to arbitrary polygons).
- **Section moduli `Z33`/`Z22`** from extreme fibres, once the orientation
  (item 3) is pinned down.
- **Polygon fiber meshing** (`SDSection.to_fiber_patches`) — triangulate the
  polygon into concrete patches and place rebar from the reinforcement records.
- **Centroid / shear-centre offsets** once the reference-point convention is
  confirmed.

## 4. Verification plan

Each follow-up above should be validated against a controlled ETABS export
(a small model with one or two hand-computable SD sections, exported at
`ANGLE 0` and a known rotation) so the derived values can be compared with the
numbers ETABS reports in the Section Designer property dialog.
