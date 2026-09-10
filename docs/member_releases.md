---
title: "Member End Releases & Partial Fixity"
description: "How SAP2000 frame end releases and partial-fixity springs are converted to OpenSees zero-length release elements (and Tcl), including the DOF mapping, formulation-aware stiffness selection, and validation."
status: "complete"
tags: [releases, partial-fixity, connections, zeroLength, frames, tcl, sap2000]
category: [model-features]
related: [builder_reference.md, element_properties_config.md, tcl_export.md, workflow.md]
---
# Member End Releases & Partial Fixity

SAP2000 frame objects may **release** any of the six local DOFs at either
end (freed), optionally with a **partial-fixity** (semi-rigid) spring.
`fea_toolkit` parses these assignments and reproduces them in the OpenSees
domain and in the Tcl export.

## SAP2000 source tables

| Table | Content |
|---|---|
| `FRAME RELEASE ASSIGNMENTS 1 - GENERAL` | Yes/No release flags per DOF/end |
| `FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY` | Spring stiffness per released DOF |

Both feed :class:`~fea_toolkit.model.sap_data.FrameRelease`
(`end_i`/`end_j` flags + `end_i_k`/`end_j_k` spring stiffnesses), threaded
through the Preprocessor onto ``MeshModel.frame_releases``.

## Local DOF mapping

The six SAP2000 release DOFs map **1:1** onto OpenSees ``zeroLength`` local
DOFs ``1..6``:

| SAP2000 | OpenSees local DOF |
|---|---|
| P (axial) | 1 |
| V2 (shear, local 2) | 2 |
| V3 (shear, local 3) | 3 |
| T (torsion) | 4 |
| M2 (moment 22, minor) | 5 |
| M3 (moment 33, major) | 6 |

## OpenSees representation

A release is an **end** property, so it cannot be set on a beam-column
element directly.  Following M. Scott's *"Force-Based Element Moment
Release"* (OpenSeesDigital, 2022) — the "extra node + ``zeroLength``"
method — each released end is modelled as::

    structural_node_i → zeroLength(release) → {eid}_rel_i … member …

The ``zeroLength`` element is given the **member's local axes** via
``-orient`` so the release acts on the element's *local* DOFs (not global),
and connects the structural node to a coincident release node.  The member
element is re-pointed to the release node, and the retention/release of each
DOF is expressed through ``-mat``/``-dir`` (a material is supplied only for
DOFs that carry stiffness).  ``equalDOF`` is deliberately **not** used — it
is incompatible with ``rigidLink`` MPC end offsets under the
``Transformation`` constraint handler (the same rationale as the bond-slip
springs).

### Formulation-aware stiffness

The stiffness of each DOF on the release element is derived from the
**member's own section and material** (unit-consistent), so it is rigid
relative to *that* member regardless of the element formulation
(``elasticBeamColumn``, ``dispBeamColumn``, ``forceBeamColumn``, …):

| Local DOF | Retained (rigid) | Full release (soft) |
|---|---|---|
| 1 axial | ``η·E·A/L`` | ``μ·E·A/L`` |
| 2, 3 shear | ``η·G·A/L`` | ``μ·G·A/L`` |
| 4 torsion | ``η·G·J/L`` | ``μ·G·J/L`` |
| 5, 6 bending | ``η·E·I22/L``, ``η·E·I33/L`` | ``μ·E·I22/L``, ``μ·E·I33/L`` |

- ``η`` = ``release_rigidity_factor`` (default **100**) — retained DOFs must
  be much stiffer than the member they terminate.
- ``μ`` = ``release_softness_factor`` (default **1e-6**) — a *fully*
  released DOF uses a small non-zero spring rather than exact zero.  A true
  zero would leave the DOF floating (singular) when nothing else stiffens it,
  e.g. a pinned base whose only member is released.  ``1e-6`` keeps the model
  non-singular while making the release effectively exact (validated to
  <0.02 % error).  Set ``0.0`` for an exact zero release.

For fibre members the gross-section ``A/I/J`` + ``E/G`` provide the rigid
reference (the fibre member's own inelasticity is separate — the release only
needs to be much stiffer than the end rotation).

### Partial fixity

When the ``... 2 - PARTIAL FIXITY`` table supplies a spring for a released
DOF, that **physical stiffness** (force/length for translational DOFs,
moment/radian for rotational DOFs, model units) is used **directly** at that
DOF — no ``η``/``μ`` scaling.  This is identical for elastic and fibre
members, since it is a connection property.

## Split members

A release is an end property of the *original* member.  When the Preprocessor
splits a frame at interior joints, the I-end release moves to the first leaf
and the J-end release to the last leaf, so ``MeshModel.frame_releases`` is
always keyed by an active element.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| ``apply_releases`` | ``True`` | Master switch (releases are a real model property) |
| ``release_rigidity_factor`` | ``DEFAULT_RIGIDITY_FACTOR`` (100.0) | η for retained DOFs |
| ``release_softness_factor`` | ``DEFAULT_SOFTNESS_FACTOR`` (1e-6) | μ for fully released DOFs (``0`` = exact) |

Releases are skipped (with a warning) when ``hinge_model == 'lumped'`` (both
re-point member ends) or when section/material data are insufficient, and are
skipped for degenerate/zero-length members.

## Single shared planner (OpenSeesPy & Tcl)

All three consumers obtain their release topology and per-DOF stiffnesses
from **one** function, :func:`~fea_toolkit.opensees.releases.plan_releases`:

1. :meth:`AnalysisBuilder._create_member_releases` — the OpenSeesPy domain,
2. :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl` — Tcl, and
3. :func:`~fea_toolkit.opensees.builder.export_model_to_tcl` — Tcl.

The Tcl rendering is likewise shared via
:func:`~fea_toolkit.opensees.releases.emit_release_tcl`, so the OpenSeesPy
domain is identical to the Tcl domain **by construction** — there is no
second implementation of the DOF/stiffness rules to keep in sync.

The canonical η / μ values are the module-level constants
``DEFAULT_RIGIDITY_FACTOR`` (=100) and ``DEFAULT_SOFTNESS_FACTOR`` (=1e-6) in
:mod:`fea_toolkit.opensees.releases`; ``AnalysisBuilder._set_defaults()``
imports them for the config keys, and ``plan_releases`` uses them as its
fallbacks.  They are defined in exactly one place.

## Validation

The conversion is verified against closed-form results
(``tests/test_member_releases.py``):

- **Simply-supported beam** (moment releases at both supports, midspan point
  load): δ = ``PL³/48EI`` matches to ≈0.01 %.
- **Partial fixity** (known rotational spring ``k`` at both ends, midspan
  point load): matches the closed form
  ``M = (PL/8)/(1 + 2EI/(kL))`` to ≈0.02 %.
- **Idempotency**: repeated ``build_domain()`` does not duplicate release
  nodes/elements.
- **Split re-mapping** and **Tcl emission** are covered directly.

## References

- OpenSeesWiki — *ZeroLength Element*:
  <https://opensees.berkeley.edu/wiki/index.php/ZeroLength_Element>
- OpenSeesWiki — *ZeroLengthSection Element*:
  <https://opensees.berkeley.edu/wiki/index.php/ZeroLengthSection_Element>
- M. H. Scott — *Force-Based Element Moment Release* (OpenSeesDigital, 2022):
  <https://openseesdigital.com/2022/10/16/force-based-element-moment-release/>
- CSi America — *Frame Releases / Partial Fixity*:
  <https://help.csiamerica.com/help/sap2000/26/26.0.0/SAP2000/WebHelp/Menus/Assign/Frame/Frame_Releases_and_Partial_Fixity.htm>

## Limitations

- Releases act on the member's **local** DOFs; the zero-length orient is
  taken from the same local-axis convention as the frame elements.
- Partial fixity is applied at the **support face** (inside any rigid end
  offset), matching SAP2000's convention.
- ``truss`` members ignore releases (axial-only).
