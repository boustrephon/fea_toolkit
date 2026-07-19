# Tcl Export for Nonlinear Analysis

Nonlinear RC analysis (fiber sections with ``Concrete01/02``,
``Steel02``, or ``forceBeamColumn`` with ``HingeRadau``) **does not
work in OpenSeesPy**.  Any nonlinear analysis must be exported to Tcl
and run via Xara's standalone ``tclsh8.6``.

Two export paths are available:

| Path | Method | Use case |
|---|---|---|
| Recording | ``RecordingOpenSees`` proxy | Elastic builds — records ``ops.*`` calls during Python build |
| Direct | ``export_model_to_tcl()`` | Nonlinear — translates ``SAPModelData`` directly to Tcl |

## Direct Tcl export (recommended)

```python
from fea_toolkit.opensees.builder import OpenSeesBuilder

config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}

tcl_suffix = OpenSeesBuilder.pushover_tcl(
    control_node=top_node_tag, dof=1, max_disp=0.15,
    lateral_loads=lateral_loads, gravity_loads=gravity_loads,
    adaptive=True,
)

OpenSeesBuilder.export_model_to_tcl(
    md, "rc_pushover.tcl", config=config, tcl_suffix=tcl_suffix,
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

With ``use_preprocessor: True`` (the default), the Preprocessor produces a ``MeshModel``
with fully prepared topology.  ``export_model_to_tcl()`` can accept the
MeshModel's data directly — the topology is already split and meshed,
eliminating the need for the caller to prepare it manually.

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
