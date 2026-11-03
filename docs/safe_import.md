---
title: "CSi SAFE (.f2k / .$sf) import"
description: "SAFE tabular (.f2k) and native-database (.$sf) parsers, gmsh floor meshing, and soil/foundation spring supports for slab models."
status: "complete"
tags: [safe, f2k, sf, io, parser, gmsh, springs]
category: [import]
related: [e2k_import_plan.md, shell_openings.md, llm_guide.md]
---

# CSi SAFE (`.f2k` / `.$sf`) import

> **Status: ✅ Complete.** SAFE slab models import through the same
> `load_model_data()` path as SAP2000/ETABS, mesh with gmsh, and carry their
> soil/foundation springs into OpenSees as `zeroLength` supports.

`fea_toolkit` historically parsed SAP2000 (`.s2k`) and ETABS (`.e2k` / `.$et`).
This document records the CSi SAFE import support: the two parsers, the gmsh
floor-meshing stage, and the soil-spring support model.

## Formats and routing

| Suffix | Format | Parser |
|---|---|---|
| `.f2k` | SAFE tabular text export | `io.f2k_parser.SAFEF2KParser` |
| `.$sf` | SAFE native-database text export | `io.safe_db_parser.SAFEDBParser` |

Both share the `TABLE: "…"` + `Key=Value` grammar, so they reuse the shared
tokeniser (`io/_table_text.py`) that the SAP2000 parser was refactored onto.
`load_model_data()` routes each suffix to its parser; `io/__init__.py`
re-exports `SAFEF2KParser` and `SAFEDBParser`.  Table-coverage triage for both
formats lives in `io/safe_table_registry.py` (`safe_classify` for `.f2k`,
`safe_db_classify` for `.$sf`).

## What is imported

Both parsers produce the same `SAPModelData` shape as a `.s2k` parse:

* **Geometry** — points → `Node`; `.f2k` floor/wall objects and `.$sf` areas →
  `AreaElement` (the `.$sf` `OBJECT GEOMETRY - AREAS` table spans several
  physical lines per polygon, merged by the repeated `Area=` key); null lines /
  lines → `FrameElement` (beams via `BEAM PROPERTY ASSIGNMENTS`).
* **Sections** — slab / deck / wall / beam / column properties; beam/column
  rectangles compute `A`/`I33`/`I22` from their dimensions.
* **Springs** — point / line / area spring definitions and assignments.
* **SAFE-specific objects** — tendons (`TendonProperty`), design strips
  (`DesignStrip`, with `.$sf` A/B end records), punching-shear design
  overwrites (`SAPModelData.punching_shear`).
* **Loads / cases / combos / groups / mass source** — same additive fields as
  the other parsers; case types are normalised to the canonical `"LinStatic"`.

## Floor meshing (gmsh)

SAFE does not pre-mesh floors — it exports *objects* (arbitrary `n`-gon slab
areas, openings, drop panels, stiff column zones) plus a global
`AUTOMATIC SLAB MESH OPTIONS` intent, captured on
`SAPModelData.floor_mesh_options`.  `mesh/floors.py` (`mesh_floors`) turns the
per-level planar union into buildable quad/tri `AreaElement`s:

* the **union outer boundary** is reconstructed from undirected edge counts
  (an edge on the outline is one whose midpoint lies inside no other region);
* **openings** are cut as `PlaneSurface` holes;
* **overlapping drop panels / stiff zones** are resolved by per-element
  *overlay precedence* (`Stiff` > `Drop` > `Slab`, read from the section
  `shape`) — OCC booleans cannot subtract coplanar faces, so the mesher works
  in the built-in `geo` kernel instead;
* **beams** (frames with a section assignment) and **column/wall support
  points** are embedded as 1-D / 0-D constraints, and adjacent areas share
  conforming edges;
* **non-horizontal** areas (ramps / staircases) are left unmeshed and reported
  in the returned `skipped` list.

The Preprocessor runs `mesh_floors` when `floor_mesh_options` is present, then
marks the embedded beams `AtJoints=True` so the existing frame-splitting pass
connects every beam to the slab mesh along its length.

## Soil / foundation springs

Springs are resolved **in the Preprocessor** (declarative, §3.2) into
`MeshModel.point_springs: list[NodalSpring]`, and **emitted** by the
AnalysisBuilder as coincident-ground-node `zeroLength` supports:

* **point springs** map 1:1 onto their assigned node (UX…RZ → DOF 1…6);
* **line springs** are distributed to the endpoints of each active frame leaf
  per tributary half-length (vertical → UZ, rotational → torsion about the line
  axis);
* **area springs** are distributed evenly over the (meshed) area's corners per
  tributary area (U1/U2/U3 → DOF 1/2/3).

Stiffnesses accumulate per node.  Each `NodalSpring.stiffness` is a six-element
`[UX, UY, UZ, RX, RY, RZ]` vector; `compression_only` holds the DOFs flagged
SAFE "Compression Only" (always the vertical bearing direction for point/line
springs).  The builder emits one `zeroLength` per node with a uniaxial
`Elastic` material per enabled DOF — or `ENT` (elastic-no-tension) when
`soil_spring_compression_only` is enabled.  Config keys:

* `build_soil_springs` (default `True`) — opt out of emitting supports;
* `soil_spring_compression_only` (default `False`) — model uplift-gauged soil
  with `ENT`, otherwise the domain stays linear.

## Limitations

* **`.$sf` point springs have no assignment table** — definitions are imported
  but no point is wired to them (line springs *do* carry assignments).
* **Line springs** capture only vertical + rotational stiffness (the horizontal
  / transverse terms are not parsed yet).
* **Sloped slabs** (ramps / staircases) are skipped by the floor mesher and
  stay as `n`-gon areas (unbuildable as shells).
* **Tendons, design strips and punching-shear overwrites** are imported for
  introspection/design but not yet analysis-modelled (they are slab-design
  objects, not FEA members).

## Performance

Floor meshing itself is fast (~5 s on the real B3 `.$sf`, 324 areas / 75
openings), but a **full** `Preprocessor.run()` on B3 (> 5000 nodes) takes
**> 2 min** — the cost is elsewhere in the pipeline (frame splitting,
wall-slab intersection detection, coincident-node merging, edge detection).
A profiling pass is tracked as **P44** in `docs/_pending_work.md`.

## Usage

```python
from fea_toolkit.io import load_model_data
from fea_toolkit.opensees.preprocessor import Preprocessor
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

md = load_model_data("slab.$sf")
mm = Preprocessor({"create_shells": True}).run(md)
ab = AnalysisBuilder(mm, {"soil_spring_compression_only": True}).build_domain()
```

