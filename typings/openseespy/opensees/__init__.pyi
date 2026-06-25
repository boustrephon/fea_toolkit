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

def mass(tag: int, *values: float) -> None:
    """Assign mass to a node.

    Args:
        tag: Node tag.
        *values: Mass values for each DOF (mx, my, mz, mrx, mry, mrz).
    """
    ...

def nodeMass(tag: int, *dofs: int) -> Union[float, Tuple[float, ...]]:
    """Return nodal mass.

    Args:
        tag: Node tag.
        *dofs: Optional DOF numbers to query (1‑based). If omitted, all DOFs.
    Returns:
        Single mass value if one DOF requested, else tuple.
    """
    ...

def nodeReaction(tag: int, *dofs: int) -> Union[float, Tuple[float, ...]]:
    """Return nodal reaction forces.

    Args:
        tag: Node tag.
        *dofs: Optional DOF numbers to query (1‑based). If omitted, all DOFs.
    Returns:
        Single reaction if one DOF requested, else tuple.
    """
    ...

def nodeResponse(tag: int, dof: int, response_id: int) -> float:
    """Return a nodal response quantity.

    Args:
        tag: Node tag.
        dof: DOF number (1‑based).
        response_id: Response type (1=disp, 2=vel, 3=accel, 4=eigenvector,
                     5=unbalanced load, 6=reaction, 7=Rayleigh force).
    Returns:
        Requested response value.
    """
    ...

def nodeEigenvector(tag: int, mode: int, dof: int) -> float:
    """Return a component of a mode shape at a node.

    Args:
        tag: Node tag.
        mode: Mode number (1‑based).
        dof: DOF number (1‑based).
    Returns:
        Eigenvector component value.
    """
    ...

def getNodeTags() -> Tuple[int, ...]:
    """Return tags of all nodes in the model.

    Returns:
        Tuple of node tags.
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

def patch(patch_type: str, mat_tag: int, *args: Any) -> None:
    """Generate fibers over a cross‑sectional area (inside a Fiber section).

    Three patch types are available:

    **Rectangular patch** — fibers in a rectangle from (yI,zI) to (yJ,zJ)::

        ops.patch('rect', matTag, numSubdivY, numSubdivZ, yI, zI, yJ, zJ)

    **Circular patch** — fibers in a circular ring::

        ops.patch('circ', matTag, numSubdivCirc, numSubdivRad,
                  yCenter, zCenter, intRad, extRad, startAng, endAng)

    **Quadrilateral patch** — fibers inside a 4‑vertex polygon (CCW order)::

        ops.patch('quad', matTag, numSubdivIJ, numSubdivJK,
                  yI, zI, yJ, zJ, yK, zK, yL, zL)

    Args:
        patch_type: ``'rect'``, ``'circ'``, or ``'quad'``.
        mat_tag: Tag of previously defined material.
        *args: Patch‑specific arguments as shown above.
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

    **Path time series** (used for response spectrum)::

        ops.timeSeries('Path', tag, '-values', *values, '-dt', dt,
                       '-factor', factor)
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

def loadConst(*args: str) -> None:
    """Lock existing load patterns at their current load factor.

    Usage::

        ops.loadConst('-time', 0.0)   # lock gravity, reset domain time

    After calling this, new patterns vary independently from the locked ones.
    Commonly used in multi‑stage analyses (e.g. gravity → pushover).

    Args:
        *args: Options such as ``'-time', value``.
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

def wipeAnalysis() -> None:
    """Destroy all existing analysis objects (constraints, numberer, system,
    algorithm, integrator, analysis) while preserving the model
    (nodes, elements, patterns, loads).

    Used before re‑defining analysis parameters for a new stage without
    rebuilding the model.
    """
    ...

def analysis(analysis_type: str, *args: Any) -> None:
    """Create the Analysis object.

    Args:
        analysis_type: ``'Static'``, ``'Transient'``, etc.
        *args: Additional analysis options.
    """
    ...

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

def eigen(*args: Any) -> Tuple[float, ...]:
    """Solve the eigenvalue problem.

    Usage::

        eigenvalues = ops.eigen('-fullGenLapack', numModes)
        eigenvalues = ops.eigen('-standard', numModes)

    Args:
        *args: Solver type and number of modes (e.g. ``'-fullGenLapack', 30``).
    Returns:
        Tuple of eigenvalues (ω²).
    """
    ...

def reactions() -> None:
    """Compute nodal reactions for the current load case.
    Must be called after ``ops.analyze()`` before querying ``nodeReaction``.
    """
    ...

def modalProperties(*args: str) -> dict:
    """Return modal properties (periods, frequencies, participation factors).

    Usage::

        props = ops.modalProperties('-return', '-unorm')

    Args:
        *args: Options such as ``'-return'`` (return dict instead of printing),
               ``'-unorm'`` (mass‑normalised eigenvectors).
    Returns:
        Dictionary with keys like ``eigenFrequency``, ``eigenPeriod``,
        ``partiFactorMX``, ``partiMassMX``, ``partiMassRatiosMX``,
        ``totalFreeMass``, etc.
    """
    ...

def responseSpectrumAnalysis(ts_tag: int, dof: int, *args: str) -> None:
    """Run a response‑spectrum analysis for one mode.

    Usage::

        ops.responseSpectrumAnalysis(tsTag, dof, '-mode', modeNum)

    Must be called after :func:`eigen` and :func:`modalProperties`.

    Args:
        ts_tag: Tag of a ``Path`` time series defining the spectrum.
        dof: Excitation direction (1=UX, 2=UY, 3=UZ, 4=RX, 5=RY, 6=RZ).
        *args: ``'-mode', modeNum``.
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
