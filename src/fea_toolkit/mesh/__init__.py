"""Optional mesh quality diagnostics and remeshing utilities.

Both modules are **optional** — they are never imported by the core
workflow and require extra dependencies:

* ``fea_toolkit.mesh.checks`` — mesh quality metrics via **COMPAS** (MIT)
* ``fea_toolkit.mesh.remesh`` — constrained quadrilateral remeshing via
  **Gmsh** (GPL v2+)

Install with::

    pip install fea_toolkit[mesh-quality]   # compas only
    pip install fea_toolkit[mesh-remesh]    # gmsh only
    pip install fea_toolkit[mesh-all]       # both
"""
