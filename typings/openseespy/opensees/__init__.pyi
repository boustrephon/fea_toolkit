"""
Type stubs for openseespy.opensees (C extension).

Provides function signatures with named parameters for hover documentation
and basic type checking. Based on the official OpenSees command manual:
https://opensees.ist.berkeley.edu/wiki/index.php/Command_Manual
"""
from typing import Any, List, Optional, Tuple, Union


# ============================================================================
# Domain / model commands
# ============================================================================

def wipe() -> None:
    """Destroy all existing OpenSees objects and reset the model."""
    ...

def model(model_type: str, *args: str) -> None:
    """Create a new model domain.

    Args:
        model_type: 'basic' (only supported value).
        *args: Options like ``'-ndm', ndm, '-ndf', ndf``.
    """
    ...

def node(tag: int, *coords: float) -> None:
    """Create a node.

    Args:
        tag: Node tag (integer).
        *coords: Nodal coordinates (x, y) for 2D or (x, y, z) for 3D.
    """
    ...

def fix(tag: int, *dofs: int) -> None:
    """Impose fixity (boundary conditions) at a node.

    Args:
        tag: Node tag.
        *dofs: Values 0=free, 1=fixed for each DOF.
    """
    ...

def nodeCoord(tag: int) -> Tuple[float, ...]:
    """Return the coordinates of a node.

    Args:
        tag: Node tag.
    Returns:
        Tuple of coordinates (x, y) or (x, y, z).
    """
    ...

def nodeDisp(tag: int, *dofs: int) -> Union[float, Tuple[float, ...]]:
    """Return nodal displacements.

    Args:
        tag: Node tag.
        *dofs: Optional DOF numbers to query (1‑based). If omitted, all DOFs.
    Returns:
        Single displacement if one DOF requested, else tuple.
    """
    ...


# ============================================================================
# Section commands
# ============================================================================

def section(section_type: str, tag: int, *args: Any) -> None:
    """Create a section object.

    Args:
        section_type: ``'Elastic'``, ``'Fiber'``, etc.
        tag: Section tag.
        *args: Section-specific arguments.
    """
    ...


# ============================================================================
# Geometric transformation commands
# ============================================================================

def geomTransf(transform_type: str, tag: int, *vecxz: float) -> None:
    """Create a geometric transformation object.

    Args:
        transform_type: ``'Linear'``, ``'PDelta'``, ``'Corotational'``.
        tag: Transformation tag.
        *vecxz: Vector components defining the local x-z plane (3 values for 3D).
    """
    ...


# ============================================================================
# Element commands
# ============================================================================

def element(element_type: str, tag: int, *args: Any) -> None:
    """Create an element.

    Args:
        element_type: ``'elasticBeamColumn'``, ``'forceBeamColumn'``,
                      ``'dispBeamColumn'``, ``'nonlinearBeamColumn'``, etc.
        tag: Element tag.
        *args: Element-specific arguments.
    """
    ...

def beamIntegration(integration_type: str, tag: int,
                    sec_tag: int, num_pts: int) -> None:
    """Create a beam integration object.

    Args:
        integration_type: ``'Lobatto'``, ``'Legendre'``, ``'NewtonCotes'``, etc.
        tag: Integration tag.
        sec_tag: Section tag.
        num_pts: Number of integration points.
    """
    ...

def eleNodes(tag: int) -> Tuple[int, int]:
    """Return the node tags of an element.

    Args:
        tag: Element tag.
    Returns:
        Tuple of (iNode, jNode).
    """
    ...

def eleResponse(tag: int, *args: str) -> Any:
    """Query an element response quantity.

    Args:
        tag: Element tag.
        *args: Response identifiers (e.g. ``'yaxis'``, ``'zaxis'``, ``'force'``).
    Returns:
        Requested response value(s).
    """
    ...


# ============================================================================
# Load commands
# ============================================================================

def timeSeries(series_type: str, tag: int, *args: Any) -> None:
    """Create a time series object.

    Args:
        series_type: ``'Linear'``, ``'Constant'``, ``'Trig'``, ``'Path'``, etc.
        tag: Time series tag.
        *args: Series-specific arguments.
    """
    ...

def pattern(pattern_type: str, tag: int, *args: Any) -> None:
    """Create a load pattern.

    Args:
        pattern_type: ``'Plain'``, ``'UniformExcitation'``, etc.
        tag: Pattern tag.
        *args: Pattern-specific arguments.
    """
    ...

def load(node_tag: int, *values: float) -> None:
    """Apply nodal loads.

    Args:
        node_tag: Node tag.
        *values: Load values (fx, fy, fz, mx, my, mz).
    """
    ...

def eleLoad(*args: Any) -> None:
    """Apply element loads (distributed, point, etc.).

    Usage::

        ops.eleLoad('-ele', eleTag1, ..., '-type', '-beamUniform', wy, wz[, wx])
        ops.eleLoad('-ele', eleTag1, ..., '-type', '-beamPoint', Py, Pz, xL)

    Args:
        *args: Element load arguments per the OpenSees eleLoad command.
    """
    ...


# ============================================================================
# Analysis commands
# ============================================================================

def constraints(constraint_type: str) -> None:
    """Set the constraint handler.

    Args:
        constraint_type: ``'Plain'``, ``'Lagrange'``, ``'Penalty'``,
                         ``'Transformation'``.
    """
    ...

def numberer(numberer_type: str) -> None:
    """Set the DOF numberer.

    Args:
        numberer_type: ``'RCM'``, ``'Plain'``, ``'AMD'``.
    """
    ...

def system(system_type: str) -> None:
    """Set the system of equations solver.

    Args:
        system_type: ``'BandGeneral'``, ``'BandSPD'``, ``'ProfileSPD'``,
                     ``'UmfPack'``, ``'SparseGeneral'``, ``'Mumps'``, etc.
    """
    ...

def test(test_type: str, *args: Any) -> None:
    """Set the convergence test.

    Args:
        test_type: ``'NormDispIncr'``, ``'NormUnbalance'``,
                   ``'EnergyIncr'``, ``'RelativeNormDispIncr'``, etc.
        *args: Test-specific parameters (tol, maxIter, etc.).
    """
    ...

def algorithm(algorithm_type: str, *args: Any) -> None:
    """Set the solution algorithm.

    Args:
        algorithm_type: ``'Newton'``, ``'ModifiedNewton'``, ``'KrylovNewton'``,
                        ``'BFGS'``, ``'NewtonLineSearch'``, etc.
        *args: Algorithm-specific arguments.
    """
    ...

def integrator(integrator_type: str, *args: Any) -> None:
    """Set the integrator.

    Args:
        integrator_type: ``'LoadControl'``, ``'DisplacementControl'``,
                         ``'ArcLength'``, ``'Newmark'``, ``'HHT'``, etc.
        *args: Integrator-specific arguments.
    """
    ...

def analyze(num_incr: int, *args: Any) -> int:
    """Perform an analysis.

    Args:
        num_incr: Number of analysis increments.
        *args: Additional arguments (e.g. ``numSubIncr, dt`` for transient).
    Returns:
        0 if successful, non-zero if failed.
    """
    ...


# ============================================================================
# Recorder commands
# ============================================================================

def recorder(*args: Any) -> None:
    """Create a recorder to monitor analysis results.

    Args:
        *args: Recorder arguments per the OpenSees recorder command.
    """
    ...


# ============================================================================
# Material commands
# ============================================================================

def uniaxialMaterial(mat_type: str, tag: int, *args: Any) -> None:
    """Create a uniaxial material.

    Args:
        mat_type: ``'Steel01'``, ``'Concrete01'``, ``'Elastic'``, etc.
        tag: Material tag.
        *args: Material-specific arguments.
    """
    ...


# ============================================================================
# Fallback for any undocumented functions
# ============================================================================

def __getattr__(name: str) -> Any: ...
