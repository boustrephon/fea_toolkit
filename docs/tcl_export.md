---
title: "Tcl Export for Nonlinear Analysis"
description: "Exporting models to standalone OpenSees Tcl scripts for nonlinear analysis and Xara/OpenSeesRT runtime."
status: "fallback"
tags: [export, tcl, xara, opensees, scripting]
category: [export-viz]
related: [xara_tcl_runtime_guide.md, xara_pushover_workflow.md, xara_gravity_and_solver.md, pushover_analysis.md]
---
# Tcl Export for Nonlinear Analysis

> **This pathway is now a ✱legacy fallback✱.**  RC pushover runs natively
> in OpenSeesPy via
> :func:`~fea_toolkit.opensees.pushover.pushover_rc_openseespy` (the
> ``PushoverAnalysis`` default) — no Tcl required.  The Tcl/Xara export
> remains available behind ``config={"use_tcl_fallback": True}`` for
> users who need the standalone ``tclsh8.6`` runtime.

fea_toolkit historically exported nonlinear RC analysis to standalone
Tcl scripts and executed them via Xara's standalone ``tclsh8.6``
interpreter (generating fiber sections with ``Concrete01/02``,
``Steel02``, or ``forceBeamColumn`` with ``HingeRadau``).

The Tcl pathway exists because OpenSeesPy links against Tcl 9 via
``tkinter``, which prevents ``opensees.tcl.Interpreter`` from loading
``libOpenSeesRT`` (a Tcl 8.6 extension).  Exporting to a standalone
Tcl script and running it with ``tclsh8.6`` sidesteps that version
conflict.  Execution uses ``subprocess.Popen`` — the same mechanism
as the project's original ``automate_pushover.py`` launcher — so no
external orchestration is required.

Three export paths are available:

| Path | Method | Use case |
|---|---|---|
| Recording | ``RecordingOpenSees`` proxy | Elastic builds — records ``ops.*`` calls during Python build |
| Direct | ``export_model_to_tcl()`` | Nonlinear — translates ``SAPModelData`` directly to Tcl |
| MeshModel | ``export_mesh_model_to_tcl()`` | Nonlinear — translates a preprocessed ``MeshModel`` directly to Tcl |

## MeshModel Tcl export (recommended — uses ``export_mesh_model_to_tcl()``)

```python
from fea_toolkit.opensees.builder import pushover_tcl
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.opensees.recorder import (
    XaraTclRunner,
    export_mesh_model_to_tcl,
    parse_pushover_results,
)
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.utils import g_from_units

# Parse the SAP2000 model and run the Preprocessor (topology mutations only).
parser = SAP2000Parser("model.s2k")
parser.parse()
md = parser.get_model_data()
mm = preprocess_model(md)

# Build the lateral load pattern in the push direction (X → dof 1).
# Use a unit reference load at each node — pushover_tcl applies the
# DisplacementControl integrator, so the absolute magnitude only sets
# the pattern shape, not the target displacement.
direction = "X"                      # "X", "Y", or "Z"
dir_index = {"X": 0, "Y": 1, "Z": 2}[direction]
control_dof = dir_index + 1          # 1=X, 2=Y, 3=Z

lateral_loads: dict[int, tuple] = {}
total_w = 0.0
for nd in mm.nodes.values():
    f = [0.0, 0.0, 0.0]
    f[dir_index] = 1.0               # uniform pattern; swap for mode1/triangular
    lateral_loads[nd.node_tag] = tuple(f)
    total_w += 1.0
if total_w > 0:                      # normalise so the pattern sums to 1.0
    for tag, ld in lateral_loads.items():
        lateral_loads[tag] = tuple(v / total_w for v in ld)

# Gravity loads from nodal masses — g is scaled from model units (never hardcoded).
g = g_from_units(mm.units)
gravity_loads: dict[int, tuple] = {}
for nd in mm.nodes.values():
    if getattr(nd, "mass", None) is not None and nd.mass > 0.0:
        gravity_loads[nd.node_tag] = (0.0, 0.0, -nd.mass * g)

# Control node = topmost node along Z.
control_node = max(mm.nodes.values(), key=lambda nd: nd.z).node_tag

# Base support nodes from restraints (not an implicit node 1).
base_node_tags = sorted(
    nd.node_tag
    for nid, r in mm.restraints.items()
    for nd in [mm.nodes.get(nid)]
    if nd is not None and any(int(x) != 0 for x in r.dofs)
) or [1]

config = {"create_fiber_sections": True, "geom_transf_type": "PDelta"}

tcl_suffix = pushover_tcl(
    control_node=control_node,
    dof=control_dof,
    max_disp=0.15,
    lateral_loads=lateral_loads,
    gravity_loads=gravity_loads,
    adaptive=True,
    base_node_tags=base_node_tags,
    output_prefix="pushover_rc",     # recorder file prefix (default: "wall")
)

export_mesh_model_to_tcl(
    mm, "rc_pushover.tcl", config=config, tcl_suffix=tcl_suffix,
)

runner = XaraTclRunner()
ret, output = runner.run("rc_pushover.tcl")

data = parse_pushover_results(
    "rc_pushover_disp.out",
    "rc_pushover_bs.out",
    "rc_pushover_reaction.out",   # single base node only
)
```

## Runtime architecture

The Tcl pathway is a self-contained subprocess chain:

```
Python                        tclsh8.6 (Xara/OpenSeesRT)
──────                        ───────────────────────────
export_mesh_model_to_tcl()  →  writes {prefix}_disp.out,
  └─ builds Tcl script           {prefix}_bs.out,
      with `load libOpenSeesRT`  {prefix}_reaction*.out
              │
XaraTclRunner.run("model.tcl")
  └─ subprocess.Popen(["tclsh8.6", "model.tcl"])
              │
              ├─ streams stdout/stderr line-by-line
              └─ returns (exit_code, stdout_text)
              │
parse_pushover_results()  ←  reads the recorder output files
  └─ returns numpy arrays for displacement / base shear / reactions
```

- ``export_mesh_model_to_tcl()`` auto-detects ``libOpenSeesRT`` from an
  installed ``opensees`` Python package (``os.path.dirname(opensees.__file__)``),
  falling back to ``libOpenSeesRT.dylib`` on the dynamic loader path.
- ``XaraTclRunner`` (``recorder.py``) mirrors ``automate_pushover.py``:
  it launches ``tclsh8.6 <script>`` via ``subprocess.Popen`` with
  real-time stdout/stderr streaming, a configurable timeout, and an
  optional ``check=True`` to raise on a non-zero exit.
- ``parse_pushover_results()`` reads the ``{prefix}_disp.out`` and
  ``{prefix}_bs.out`` files back into NumPy arrays for plotting and
  post-processing.

This means the full nonlinear workflow — model → Tcl → OpenSeesRT →
results — is driven entirely from Python; no manual ``tclsh`` invocation
or external launcher script is needed.

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

### Base reaction recorders

``pushover_tcl()`` accepts a ``base_node_tags: list[int]`` parameter listing
the nodes whose reactions should be recorded.  This replaces the former
``base_node_tag: int`` / ``push_elem_tag: int`` pair — the element-force
recorder (``wall_forces.out``) has been removed.

Recorder filename convention — controlled by ``output_prefix`` (default
``"wall"`` for backward compatibility).  The generated files are:

| ``output_prefix`` | Displacement | Base shear | Reaction (single node) | Reaction (per node) |
|---|---|---|---|---|
| ``"wall"`` (default) | ``wall_disp.out`` | ``wall_bs.out`` | ``wall_reaction.out`` | ``wall_reaction_<tag>.out`` |
| ``"pushover_rc"`` | ``pushover_rc_disp.out`` | ``pushover_rc_bs.out`` | ``pushover_rc_reaction.out`` | ``pushover_rc_reaction_<tag>.out`` |

* ``{prefix}_disp.out`` — one line per converged step: ``time`` and the
  control-node displacement in the push DOF.
* ``{prefix}_bs.out`` — a single line with the summed base reactions
  ``rx ry rz`` after the final step.
* ``{prefix}_reaction.out`` — one line per step for the single base node
  (``base_node_tags=[tag]``); multiple base nodes emit per-node files
  ``{prefix}_reaction_{tag}.out`` and no bare ``{prefix}_reaction.out``.
* ``None`` (deprecated) falls back to ``[1]`` → ``{prefix}_reaction.out``.

**Resolve base nodes from the model** — derive the list from the model's
restraints so the recorders monitor the actual supports rather than an
implicit node 1:

```python
base_node_tags = sorted(
    nd.node_tag
    for nid, r in mm.restraints.items()
    for nd in [mm.nodes.get(nid)]
    if nd is not None and any(int(x) != 0 for x in r.dofs)
)

tcl = pushover_tcl(
    control_node=roof_tag, dof=1, max_disp=0.15,
    lateral_loads=lateral_loads, gravity_loads=gravity_loads,
    adaptive=True,
    base_node_tags=base_node_tags,
)
```

The same derivation works for ``SAPModelData`` by replacing ``mm.restraints``
with ``md.restraints`` and ``mm.nodes`` with ``md.nodes``.
``PushoverAnalysis._run_rc_tcl_path()`` applies this automatically.

## Two-stage build integration

With the Preprocessor active (default), it produces a ``MeshModel`` with
fully prepared topology.  Use ``export_mesh_model_to_tcl()`` to export
that ``MeshModel`` data directly — the topology is already split and
meshed, eliminating the need for the caller to prepare it manually.
The resulting ``.tcl`` file is executed by ``XaraTclRunner``, which
shells out to ``tclsh8.6`` via ``subprocess.Popen`` (see the Runtime
architecture section above).

## Confinement data

See ``model/confinement.py`` and its docstring for Mander et al. (1988)
confined concrete properties from stirrup data.

## Preferred path — OpenSeesPy RC pushover

Since the two-stage pipeline (Preprocessor → MeshModel → AnalysisBuilder)
matured, RC pushover no longer needs the Tcl round-trip:

```python
from fea_toolkit.analysis.pushover import PushoverAnalysis

# material_type="rc" now defaults to the OpenSeesPy path.
push = PushoverAnalysis(
    mesh_model=mm,
    modal_result=modal_result,
    material_type="rc",
    config={
        "directions": "4dir",        # or "+X", "-X", "+Y", "-Y"
        "beam_integration": "HingeRadau",
        # Optional nonlinear shear walls:
        # "nd_materials": {...},
        # "shell_layers": {...},
    },
)
result = push.run().data   # {direction: {results, adrs, pp, ...}}
```

The same return shape as the Tcl path, but executed in-process with
the standard `AnalysisBuilder` — no subprocess, no ``tclsh8.6``.

To opt back into the legacy Tcl export, pass
``config={"use_tcl_fallback": True}`` — a ``DeprecationWarning`` is
emitted.

## Current limitations

- **Nonlinear shell sections** are not yet supported in the Tcl export —
  slabs/walls remain elastic (``ElasticMembranePlateSection``).
  (The OpenSeesPy path supports nonlinear shells via ``nd_materials``
  and ``shell_layers``.)
- **Section tag collision** — elastic and fibre sections share the same
  tag range when combined in one model.  Verify tags for mixed-type models.
- **Mixed element types** — only sections with fibre patches use
  ``forceBeamColumn``; others remain ``elasticBeamColumn``.
- **Modal pushover pattern**: run modal analysis in Python (elastic),
  extract eigenvectors, compute ``load = mass × eigenvector``, pass as
  ``lateral_loads`` to ``pushover_tcl()``.
