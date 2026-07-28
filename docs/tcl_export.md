---
title: "Tcl Export for Nonlinear Analysis"
description: "Exporting models to standalone OpenSees Tcl scripts for nonlinear analysis and Xara/OpenSeesRT runtime."
status: "complete"
tags: [export, tcl, xara, opensees, scripting]
category: [export-viz]
related: [xara_tcl_runtime_guide.md, xara_pushover_workflow.md, xara_gravity_and_solver.md, pushover_analysis.md]
---
# Tcl Export for Nonlinear Analysis

Nonlinear RC analysis (fiber sections with ``Concrete01/02``,
``Steel02``, or ``forceBeamColumn`` with ``HingeRadau``) **does not
work in OpenSeesPy**.  Any nonlinear analysis must be exported to Tcl
and run via Xara's standalone ``tclsh8.6``.

Three export paths are available:

| Path | Method | Use case |
|---|---|---|
| Recording | ``RecordingOpenSees`` proxy | Elastic builds — records ``ops.*`` calls during Python build |
| Direct | ``export_model_to_tcl()`` | Nonlinear — translates ``SAPModelData`` directly to Tcl |
| MeshModel | ``export_mesh_model_to_tcl()`` | Nonlinear — translates a preprocessed ``MeshModel`` directly to Tcl |

## MeshModel Tcl export (recommended — uses ``export_mesh_model_to_tcl()``)

```python
from fea_toolkit.opensees.recorder import export_mesh_model_to_tcl, pushover_tcl
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.io.s2k_parser import SAP2000Parser

# --- Setup: caller provides these ---
# parser = SAP2000Parser("model.s2k")
# parser.parse()
# md = parser.get_model_data()
#
# mm = preprocess_model(md)
# config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}
#
# top_node_tag = ...       # control node tag from mesh_model.nodes
# lateral_loads = {...}    # dict of node_tag -> load magnitude in push direction
# gravity_loads = {...}    # dict of node_tag/load pattern for gravity
# --- End setup ---

mm = preprocess_model(md)
config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}

tcl_suffix = pushover_tcl(
    control_node=top_node_tag, dof=1, max_disp=0.15,
    lateral_loads=lateral_loads, gravity_loads=gravity_loads,
    adaptive=True,
)

export_mesh_model_to_tcl(
    mm, "rc_pushover.tcl", config=config, tcl_suffix=tcl_suffix,
)

from fea_toolkit.opensees.recorder import XaraTclRunner
runner = XaraTclRunner()
ret, output = runner.run("rc_pushover.tcl")
```

## What the Tcl export generates

With ``create_fiber_sections=True``, the Tcl output includes:

1. **Nonlinear materials** — ``Concrete01`` (unconfined cover + confined core),
   ``Steel02`` (rebar), or ``Steel01`` (steel fibre sections).
2. **Fiber sections** — ``section Fiber`` with ``patch``/``layer`` commands.
3. **Config-driven ``geomTransf``** — ``Linear``, ``PDelta``, or ``Corotational``.

## Pushover analysis Tcl

``pushover_tcl()`` generates a ``DisplacementControl`` analysis with:

- **Gravity step** (optional) — ramped over 10 sub-steps, then ``loadConst``.
- **Lateral pushover** — ``DisplacementControl`` integrator, ``numberer RCM``,
  auto-fallback algorithm chain (Newton → KrylovNewton → ModifiedNewton).

Full parameter details are in the ``pushover_tcl()`` docstring.

## Two-stage build integration

With the Preprocessor active (default), it produces a ``MeshModel`` with
fully prepared topology.  Use ``export_mesh_model_to_tcl()`` to export
that ``MeshModel`` data directly — the topology is already split and
meshed, eliminating the need for the caller to prepare it manually.

## Confinement data

See ``model/confinement.py`` and its docstring for Mander et al. (1988)
confined concrete properties from stirrup data.

## Current limitations

- **Nonlinear shell sections** are not yet supported — slabs/walls remain
  elastic (``ElasticMembranePlateSection``).
- **Section tag collision** — elastic and fibre sections share the same
  tag range when combined in one model.  Verify tags for mixed-type models.
- **Mixed element types** — only sections with fibre patches use
  ``forceBeamColumn``; others remain ``elasticBeamColumn``.
- **Modal pushover pattern**: run modal analysis in Python (elastic),
  extract eigenvectors, compute ``load = mass × eigenvector``, pass as
  ``lateral_loads`` to ``pushover_tcl()``.