---
title: "Shell openings — exact cut-out and shell element-family selection"
description: "Plan for cutting exact holes in structured shell meshes without Gmsh, selecting thin/thick/nonlinear shell element families from the parsed CSI section type with a per-selection override, and boundary-node reuse."
status: "complete"
tags: [openings, shell, meshing, area-elements, geometry, boolean, element-type, csi]
category: [model-features]
related: [shell_support.md, constraint_detection.md, element_splitting.md, element_classification.md, element_properties_config.md]
---
# Shell openings — exact cut-out and shell element-family selection

> **Status: ✅ Complete.**  Implemented and tested: the `cut_cell` boolean, the
> thin/thick/nonlinear shell element-family selection, the CSI `Type` parsing,
> the mesher trimming with boundary-node reuse, the GUI void filtering, and
> the smoke guards.  The "Decisions and consequences" section records the
> agreed design; §8 is the implementation plan that landed.

## Decisions and consequences

| # | Decision | Consequence |
|---|---|---|
| 1 | Exact cut-out in the **built-in** meshers (no Gmsh) | Add `cut_cell`; the staircase hole becomes a conforming boundary (§3) |
| 2 | Triangles are **real triangle elements**, not collapsed quads | Collapsed `ShellMITC4` quads are ill-conditioned and ≠ a triangle (verified ~25 % off); emit genuine triangles |
| 3 | **Pair like with like** — never mix thick and thin | Two consistent families; no thick/thin mismatch at the hole interface (§4.3) |
| 4 | Default family = **thin (DKT/DKQ)** | `ShellDKGQ`/`ShellDKGT` + `ShellNLDKGQ`/`ShellNLDKGT`; matches Tcl export + nonlinear path; elastic quads switch `ShellMITC4` → `ShellDKGQ` |
| 5 | Thick family = **`ASDShellQ4`/`ASDShellT3`** (MITC), not `ShellMITC4` | `ShellMITC4` is quad-only (no `ShellMITC3`); `ASDShellQ4` shares its MITC4 bending + AGQ6 membrane; foundation mats use the ASDShell pair |
| 6 | Default formulation from the **parsed CSI `Type` field** | Honors SAP/ETABS thin/thick/membrane/plate intent (§CSI) |
| 7 | `Membrane`/`Plate-*` map to a **full shell** for now | In-plane-only / bending-only not faithfully represented; type preserved on `ShellSection` for a later faithful pass |
| 8 | Override is **per-selection `element_type`**, not a new section | Section is shared across families (verified); only the element name changes (§Element selection) |
| 9 | **Boundary-node reuse**: opening corners (`o1..o4`) become mesh nodes | Beams/loads framing the opening connect; orphan removal becomes a no-op (§5) |

**Verified facts** (OpenSeesPy 3.8.0, mesh-converged / registration probes):

- `ShellMITC4` is quad-only (no `ShellMITC3`).  `ASDShellQ4` (AGQ6-I + MITC4
  bending) and `ASDShellT3` (ANDeS + MITC3 bending) are the matched thick pair.
- All families accept `ElasticMembranePlateSection` / `PlateFiber` /
  `LayeredShell`.
- `ElasticMembranePlateSection` **supplies transverse shear**: a converged
  thick cantilever (L/t = 2) gives `ShellDKGQ` → Euler–Bernoulli (3.185e-7)
  and `ShellMITC4`/`ASDShellQ4` → Timoshenko (3.782e-7, +18.8 % shear), so the
  thick choice is meaningful over the existing section and `PlateFiber` is not
  needed for linear-elastic.
- `ShellDKGT`, `ASDShellT3`, `ASDShellQ4` are all registered in the wheel.

## 1. Background and current state

An *opening* is an `AreaElement` with `opening=True` (the `"NONE"` section or
`OPENING "YES"` in the source model).  Today:

- **Self-weight** — `net_area()` subtracts the exact opening overlap
  (`model/openings.py`), so weights are correct.
- **Structured meshing** — `mesh_area_elements` and `subdivide_area_mesh`
  (`model/geometry_mesh.py`) drop any grid cell *fully* inside an opening
  (`_cell_kept` → `conform_cell_to_openings`).  Cells that *straddle* the
  boundary are kept whole, so the hole edge is a staircase of grid lines.
- **OpenSees** — `_sections.py` skips opening areas (`opening=True`), so no
  shell element spans the void; the opening corner nodes are orphaned
  (`preprocessor.py`) because nothing references them.
- **GUI** — no opening awareness: the unprocessed view draws openings as
  solid shells; the processed view drops them only by accident (their corner
  nodes are orphaned).

The consequence of the staircase + orphaning is a **connectivity gap**: a
beam framing into an opening edge references `o1..o4`, but those nodes are not
shared by any shell in the staircase mesh, so the beam endpoint floats
disconnected from the slab.

## 2. Goal and scope

- **SAPModel (raw view)** — a *visual* cut-out only: filter `opening=True`
  out of the filled-shell collection and draw the opening as an outline.
  No analytical significance.
- **MeshModel (processed → OpenSees)** — a *real* topological cut-out: the
  structured grid is **trimmed** around the opening so the shell mesh traces
  the opening boundary, and the opening corner nodes become real, shared mesh
  nodes (so beams/loads/constraints connect correctly).

The exact cut-out is implemented in the built-in meshers (no Gmsh).  The
Gmsh `remesh_areas` path may later be taught to treat openings as holes, but
that is a separate, optional enhancement.

## 3. The boolean: convex half-plane chop

The problem is `cut_cell(cell_2d, openings_2d) -> list[convex polygon]` — the
triangulation-ready pieces of `cell − union(openings)`.  The cell is a convex
quad; each opening is triangulated (concave openings become convex triangles
via the existing `_triangulate`).

We deliberately avoid a general polygon boolean (Weiler–Atherton,
Greiner–Hormann, Clipper) because two facts make the problem easy:

1. the cell is **convex**, and
2. `_triangulate` already reduces any opening to **convex triangles**.

The difference of two convex polygons decomposes into a set of non-overlapping
convex pieces obtained by "chopping" the cell against each directed edge of
the triangle.

### 3.1 `_clip_by_halfplane` — the primitive

Clip a convex polygon against a single directed line, keeping either the
inside (left) or outside (right) half-plane:

```python
def _clip_by_halfplane(poly, a, b, keep_inside=True, tol=1e-12):
    """Clip a convex polygon against one directed half-plane.

    ``a -> b`` is the directed edge; the "inside" side is where
    ``(b-a) × (p-a) >= -tol``.  ``keep_inside=False`` keeps the outside
    (right) side instead — the inversion the difference needs and that
    Sutherland–Hodgman does not provide.
    """
    ex, ey = b[0] - a[0], b[1] - a[1]

    def _side(p):
        return ex * (p[1] - a[1]) - ey * (p[0] - a[0])

    def _keep(p):
        s = _side(p)
        return s >= -tol if keep_inside else s <= tol

    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        p_in, q_in = _keep(p), _keep(q)
        if p_in:
            out.append(p)
        if p_in != q_in:
            sp, sq = _side(p), _side(q)
            t = sp / (sp - sq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out
```

This is the existing `_clip_sutherland_hodgman` generalised to keep *either*
side of a single edge; the intersection interpolation `t = sp/(sp − sq)` is
identical to the current `t = -f_p/denom`.

### 3.2 `_convex_difference` — convex cell minus convex triangle

```python
def _convex_difference(cell, tri):
    """Return ``cell − tri`` as non-overlapping convex pieces."""
    result = []
    interior = list(cell)
    for i in range(len(tri)):
        a, b = tri[i], tri[(i + 1) % len(tri)]
        out_part = _clip_by_halfplane(interior, a, b, keep_inside=False)
        in_part = _clip_by_halfplane(interior, a, b, keep_inside=True)
        if len(out_part) >= 3:
            result.append(out_part)
        interior = in_part
        if not interior:
            break
    return result
```

**Correctness invariant:** `out_part` at edge *i* lies outside edge *i*, while
everything produced later lies inside edge *i* (it descends from `interior`,
which is clipped to *inside* at each step).  Hence the pieces are pairwise
disjoint and their union is `cell − tri`.  After all edges, `interior ==
cell ∩ tri`, which is discarded.

### 3.3 `cut_cell` — the public primitive

```python
def cut_cell(cell, openings, area_tol=1e-12):
    """Triangulation-ready pieces of ``cell − union(openings)`` (2-D)."""
    pieces = [list(cell)]
    for opening in openings:
        for tri in _triangulate(opening):           # concave → convex tris
            pieces = [p2 for p in pieces for p2 in _convex_difference(p, tri)]
    cell_area = abs(_signed_area_2d(cell))
    tol = area_tol * max(1.0, cell_area)
    return [p for p in pieces if len(p) >= 3 and abs(_signed_area_2d(p)) > tol]
```

Subtracting openings **sequentially, triangle by triangle** handles
overlapping openings correctly (no double-subtraction), matching the union
semantics `opening_overlap_area` already has.  Each returned piece is convex,
so it fan-triangulates trivially on emit.

## 4. Emit stage — the triangle / quad element question

`cut_cell` returns convex polygons with 3–7 vertices.  To emit them:

- 4 vertices → a quad, emitted directly.
- 3 vertices → a triangle.
- ≥5 vertices → fan-triangulated into triangles.

The bulk of the mesh (cells not touching an opening) stays **quads**; only the
ring of straddling cells produces triangles/pentagons.

### 4.1 How the toolkit currently represents triangles

| Path | Quad | Triangle |
|---|---|---|
| In-process builder (`_sections.py`) | `ShellMITC4` / `ShellNLDKGQ` | **collapsed quad** — 4th corner repeats node 3 |
| Tcl export (`builder.py`, `recorder.py`) | `ShellDKGQ` | `ShellDKGT` |
| NPZ serialisation (`_serial.py`) | `shell_node_1..4` | blank corner in fixed schema; **`ragged` mode** preserves N-gons |
| Renderer (`renderers/`) | quad face | triangle face, fan for pentagons |

So the data model already carries 3-node `AreaElement`s; the *in-process*
builder collapses them to quads at OpenSees creation time.

### 4.2 Decision — use a triangle element, not a collapsed quad

**The current collapsed-quad convention is not a reliable triangle.**  The
in-process builder writes a 3-node area as
`element ShellMITC4 … n1 n2 n3 n3` (4th node repeated) — a **degenerate
4-node shell**, not a triangle.  OpenSees does not document degenerate-quad
support for shells, and the ShellMITC4 formulation assumes a proper
quadrilateral.  Michael H. Scott's "three-node quad" note establishes the
degeneration *for the 2D `quad` element only* and leaves the shell case
explicitly open ("Does `ShellMITC4` reduce to `ShellMITC3`?" — there is no
`ShellMITC3`).  A probe on OpenSeesPy 3.8.0 shows the collapsed quad
converges but returns a thin-plate tip deflection ~25 % stiffer than the
genuine triangle element, consistent with an ill-conditioned degenerate
geometry rather than a clean triangle.

- **Chosen:** keep each mesh formulation-consistent — **pair like with
  like**.  The recommended pairing is the thin (DKT/DKQ) family:
  `ShellDKGQ` quads + `ShellDKGT` triangles (elastic) and `ShellNLDKGQ` +
  `ShellNLDKGT` (layered), matching the Tcl export and the nonlinear path
  (both already DKT-based).  The alternative is the thick (MITC) family:
  `ShellMITC4` quads (unchanged) + `ASDShellT3` triangles.
- **Nonlinear / layered counterpart:** `ShellNLDKGT` (and `ASDShellT3`) exist
  for the layered-shell path; verify their section compatibility before
  switching that path.
- **Availability:** `ShellDKGT` must be present in the pinned OpenSeesPy
  3.8.0.0 wheel.  This is guarded by Check 11 in
  `examples/verify_openseespy.py` (asserted via
  `tests/test_openseespy_smoke.py`), which fails the smoke test if the wheel
  lacks the element.

A fan triangulation of a convex polygon can produce slivers near the hole;
the existing mesh-quality checks are the measuring stick.  A quad-dominant
split (pentagon → 1 quad + 1 triangle) is a possible later refinement.

### 4.3 Pair like with like — do not mix thick and thin

A cut-out mesh must not mix `ShellMITC4` (thick, Reissner–Mindlin) with
`ShellDKGT` (thin, Kirchhoff/DKT): both connect stably (6 DOF/node, real
drilling DOF) but they are different shell theories, so the interface is a
formulation mismatch — negligible for thin slabs, a real accuracy concern for
thick members.

The consistent pairings are:

| Quads | Triangles | Family |
|---|---|---|
| `ASDShellQ4` (AGQ6-I + MITC4) | `ASDShellT3` (ANDeS + MITC3) | thick (Reissner–Mindlin) |
| `ShellDKGQ` | `ShellDKGT` | thin (Kirchhoff / DKT) |
| `ShellNLDKGQ` | `ShellNLDKGT` | nonlinear (DKT-based) |

`ShellMITC4` is quad-only (there is no `ShellMITC3`); its MITC4 bending is
shared with `ASDShellQ4`, which adds the AGQ6-I membrane and Hughes–Brezzi
drilling and is the quad that matches `ASDShellT3`.  Both thick elements
accept `ElasticMembranePlateSection` / `PlateFiber` / `LayeredShell` (verified
on OpenSeesPy 3.8.0).

The thin family is recommended because it is the only family with **both**
linear and nonlinear quad+triangle elements, and it is already what the Tcl
export (`ShellDKGQ`/`ShellDKGT`) and the nonlinear layered path
(`ShellNLDKGQ`) use — so unifying on it removes, rather than adds, internal
inconsistency.  Its cost is switching the elastic quad from `ShellMITC4` to
`ShellDKGQ` (numerically equivalent for thin slabs, the dominant case).

Both triangles accept `ElasticMembranePlateSection` (verified on OpenSeesPy
3.8.0), so either pairing works with the existing section.

## CSI section type — the source of truth for the family

SAP2000 and ETABS specify the shell formulation **per section** (the
"Modeling Type" / `Type` field), not per model:

- `.s2k` `AREA SECTION PROPERTIES` → `Type = Shell-Thin | Shell-Thick |
  Membrane | Plate-Thin | Plate-Thick`.
- `.e2k` `$ WALL/SLAB/DECK PROPERTIES` (`SHELLPROP`) → a shell/membrane type
  keyword.

Mapping to OpenSees:

| CSI `Type` | OpenSees family |
|---|---|
| `Shell-Thin` | thin — `ShellDKGQ` / `ShellDKGT` |
| `Shell-Thick` | thick — `ASDShellQ4` / `ASDShellT3` |
| `Membrane` | full shell (approximation; in-plane-only not represented) |
| `Plate-Thin` / `Plate-Thick` | full shell (approximation; bending-only not represented) |

**Today neither parser reads the `Type` column** — `s2k_parser.py` and
`e2k_parser.py` both hardcode `shape="Shell"` and read only material +
thickness, so the CSI thin/thick/membrane intent is silently dropped.  The
work is: add a `shell_type` field to `ShellSection`, parse the `Type` column,
and keep the raw value so `Membrane`/`Plate-*` can be specialised later.

## Element selection and per-selection override

Resolution order (reusing the existing three-level `AreaElementProperties`
mechanism in `element_properties_config.md`):

1. **Default (Level 3)** — the parsed CSI `Type` field chooses the family:
   thin → `ShellDKGQ`/`ShellDKGT`, thick → `ASDShellQ4`/`ASDShellT3`.
2. **Per-selection (Level 2)** — `element_type` overrides the family for
   matching areas.
3. **Per-ID (Level 1)** — highest priority.

Consequences:

- `AreaElementProperties.element_type` must gain the full vocabulary
  (`ShellDKGQ`, `ShellDKGT`, `ShellNLDKGQ`, `ShellNLDKGT`, `ASDShellQ4`,
  `ASDShellT3`, `None`), and must be **wired into element selection** — today
  `_sections.py` hardcodes `ShellNLDKGQ`/`ShellMITC4` and only uses the role
  string for stiffness factors.
- A thick↔thin override changes **only the element name**; the section
  (`ElasticMembranePlateSection` / `LayeredShell`) is shared and is *not*
  recreated (the existing `_shell_sec_variants` path triggers only on a
  stiffness factor, which is orthogonal).

## 5. Connectivity — boundary-node reuse

The whole point of meshing *around* the opening is that the hole boundary is
made of real mesh nodes:

- When a cut vertex is coincident with an opening corner (`o1..o4`), **reuse
  that node id** rather than creating a duplicate.
- All other cut vertices (cell-edge ∩ opening-edge intersections) are
  deduplicated by coordinate via the existing `_coord_to_id` registry, so two
  adjacent trimmed cells share the same boundary node.

Because the trimmed cells now reference `o1..o4`, the preprocessor's
orphan-removal step (`preprocessor.py`) naturally *stops* orphaning them —
they become legitimate shell-boundary nodes, and a beam over the opening
connects to the slab.  This behaviour must be asserted, not worked around.

## 6. Integration points

1. **`model/openings.py`** — add `_clip_by_halfplane`, `_convex_difference`,
   `cut_cell` (OpenSees-free).
2. **`model/geometry_mesh.py`** — in `mesh_area_elements` and
   `subdivide_area_mesh`, replace the keep-whole/drop branch with
   classify → trim via `cut_cell` → emit sub-elements + new nodes
   (reusing `_coord_to_id` and seeding the opening corners into it).
3. **`opensees/preprocessor.py`** — opening corner orphaning becomes a no-op
   for conformed models (verify; keep for non-conformed openings).
4. **`model/sap_data.py` + `io/s2k_parser.py` + `io/e2k_parser.py`** — add
   `ShellSection.shell_type` and parse the CSI `Type` column.
5. **`opensees/_sections.py`** — wire `element_type`/family into element
   selection (replacing the hardcoded `ShellNLDKGQ`/`ShellMITC4` line); emit
   triangles as `ShellDKGT` (thin) / `ASDShellT3` (thick), quads as
   `ShellDKGQ` (thin) / `ASDShellQ4` (thick).
6. **`plotting/viz_model.py` / `viewer.py`** — the *SAPModel* visual trick
   (filter `opening=True` from filled shells; outline via `_all_nodes`).
7. **Guards** — extend `examples/verify_openseespy.py` (and the smoke-test
   assertion) from `ShellDKGT`-only to the full family (`ShellDKGQ`,
   `ShellDKGT`, `ShellNLDKGQ`, `ShellNLDKGT`, `ASDShellQ4`, `ASDShellT3`).
8. **Loads (follow-on)** — area-load tributary distribution must treat the
   opening edges as free (non-supported) edges.

## 7. Test plan

In `tests/test_openings.py`, for `cut_cell` / `_clip_by_halfplane`:

- convex quad minus a centred axis-aligned opening → the expected L-shaped
  convex pieces;
- opening overlapping a corner / an edge → correct pieces;
- concave opening → handled via `_triangulate`;
- two overlapping openings → no double-subtraction;
- opening fully covering the cell → empty;
- opening outside the cell → unchanged;
- **area conservation**: `Σ area(pieces) == area(cell) − union_overlap_area`
  for every case;
- round-trip on `mesh_area_elements` / `subdivide_area_mesh`: a meshed slab
  with a centred opening yields boundary nodes that coincide with `o1..o4`
  and a connected (no orphaned-corner) mesh.

## 8. Implementation plan (ordered)

1. **`cut_cell` + tests** — `model/openings.py` (`_clip_by_halfplane`,
   `_convex_difference`, `cut_cell`); `tests/test_openings.py` (§7).
2. **CSI type parsing** — add `ShellSection.shell_type`; read the `Type`
   column in `s2k_parser.py` and `e2k_parser.py`; parser tests.
3. **Family selection** — a `SHELL_FAMILIES` map + `default_shell_element()`
   (thin/thick from `shell_type`); wire `_sections.py` and preprocessor
   Level 3; extend `AreaElementProperties.element_type` vocabulary.
4. **Mesher wiring** — replace the `_cell_kept` keep/drop branch with
   `cut_cell` trim → emit in `mesh_area_elements` / `subdivide_area_mesh`;
   seed `_coord_to_id` with the opening corners for boundary-node reuse.
5. **Orphan no-op** — assert the opening corners now stay in `nodes`.
6. **GUI visual** — filter `opening=True` from filled shells; outline via
   `_all_nodes`.
7. **Guards** — extend `verify_openseespy.py` + the smoke test to the full
   family.

Element-resolution precedence (in `_create_shell_elements`):

1. explicit `area_element_properties[aid].element_type` (quad name) — wins;
2. else LayeredShell section → nonlinear family (`ShellNLDKGQ`/`ShellNLDKGT`);
3. else `ShellSection.shell_type` → thin (`ShellDKGQ`/`ShellDKGT`) or thick
   (`ASDShellQ4`/`ASDShellT3`);
4. else thin default (`ShellDKGQ`/`ShellDKGT`).

Triangles pick the sibling via the family map; the collapsed-quad hack
(repeating the 3rd node as the 4th corner) is removed.  `ShellMITC4` stays
accepted (legacy configs) and maps to the thick family (tri → `ASDShellT3`).
