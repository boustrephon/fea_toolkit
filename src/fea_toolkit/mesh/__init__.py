"""Optional mesh quality diagnostics and remeshing utilities.

Both modules are **optional** — they are never imported by the core
workflow.  The checks module has **no** external dependencies (NumPy
only); remeshing requires Gmsh.

* ``fea_toolkit.mesh.checks`` — mesh quality metrics (pure NumPy)
* ``fea_toolkit.mesh.remesh`` — constrained quadrilateral remeshing via
  **Gmsh** (GPL v2+)

Install with::

    pip install fea_toolkit[mesh-remesh]    # gmsh only
"""
