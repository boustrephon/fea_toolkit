"""
Type stubs for openseespy.opensees (C extension).

Provides function signatures with named parameters for hover documentation
and basic type checking. Based on the official OpenSees command manual:
https://opensees.ist.berkeley.edu/wiki/index.php/Command_Manual
"""

from typing import Any, List, Optional, Tuple, Union, overload

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

@overload
def nodeReaction(tag: int, dof: int) -> float:
    """Return a single nodal reaction component."""
    ...

@overload
def nodeReaction(tag: int, *dofs: int) -> Tuple[float, ...]:
    """Return nodal reaction forces for multiple or all DOFs."""
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
# Element commands
# ============================================================================

def eleNodes(tag: int) -> Tuple[int, int]:
    """Return the node tags of an element.

    Args:
        tag: Element tag.
    Returns:
        Tuple of (iNode, jNode).
    """
    ...


def eleResponse(tag: int, *args: Any) -> Any:
    """Query an element response quantity.

        Beam-column element force queries (3D frame elements):

        * ``eleResponse(tag, "forces")`` — **global** element-end forces,
          12 values ``[Fx_i, Fy_i, Fz_i, Mx_i, My_i, Mz_i, Fx_j, ..., Mz_j]``.
          **2D frame elements return 6 values** (one force + moment per end).
        * ``eleResponse(tag, "localForces")`` — **local** element-end forces,
          12 values ``[P_i, Vy_i, Vz_i, T_i, My_i, Mz_i, P_j, ..., Mz_j]`` for
          3D frames (6 for 2D).  Local forces are the preferred choice for
          section checks and framing envelopes: local Fx = axial, Fy/Fz =
          shear, My/Mz = bending.  ``Truss`` elements return **nodal force
          vectors** (2 values in 1D, 4 in 2D, 6 in 3D) for ``forces``, but
          **each of ``localForces`` and ``basicForce`` returns a scalar
          axial response** (tension-positive).

        Beam-column section queries (fiber `forceBeamColumn`/`dispBeamColumn`):

        * ``eleResponse(tag, "section", n, "force")`` — **section resultants
          (singular keyword ``"force"``)**, e.g. ``[P, Mz, My, ...]`` at
          integration point ``n`` (1-based).  Component ordering and count
          are beam-section-specific (not a universal six-value schema).

        Shell section / material queries (ASDShellQ4 and other multi-layer
        shells):

        * ``eleResponse(tag, "section", n, "force")`` — per-integration-point
          resultants.  For **ASDShellQ4** and similar layered shells the
          section resultants are ``[Nx, Ny, Nxy, Mx, My, Mxy]`` —
          per-unit-width membrane + bending.  Force and deformation
          lengths and ordering are **section-specific**; check the element
          type before relying on a fixed layout.
        * ``eleResponse(tag, "section", n, "deformation")`` — section
          deformations (fiber beam-columns: ``[eps0, ky, kz, ...]``).

        Geometry / material queries:

        * ``eleResponse(tag, "yaxis")`` / ``"zaxis"`` — local y/z unit vectors
          (3 values each).  **Frame beam-column elements only**.
        * ``eleResponse(tag, "material", point, "stress")`` /
          ``"material", point, "strain"`` — per-integration-point stress/strain,
          where *point* is the integration-point index (also called
          ``matTag`` or ``material_index``).  For shells this is the
          integration-point index of the material-quantity query.
        * ``eleResponse(tag, "plasticDeformation")`` — plastic deformation
          (beam-column / truss elements with an inelastic material).
        * ``eleResponse(tag, "plasticRotation")`` — plastic rotation
          (beam-column elements; **not** available for truss or shell
          elements).

    Args:
        tag: Element tag.
        *args: Response identifiers (see above).
    Returns:
        Requested response value(s); a list/tuple for vector quantities.
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

def loadConst(*args: Union[str, float]) -> None:
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

def analyze(num_incr: int, *args: Any) -> int:
    """Perform an analysis.

    Args:
        num_incr: Number of analysis increments.
        *args: Additional arguments (e.g. ``numSubIncr, dt`` for transient).
    Returns:
        0 if successful, non-zero if failed.
    """
    ...

def reactions(*args: str) -> None:
    """Compute nodal reactions for the current load case.
    Must be called after ``ops.analyze()`` before querying ``nodeReaction``.

    Usage::

        ops.reactions()                          # static reactions
        ops.reactions('-dynamic', '-rayleigh')   # include dynamic/rayleigh

    Args:
        *args: Optional flags such as ``'-dynamic'``, ``'-rayleigh'``.
    """
    ...

def modalProperties(*args: str) -> Optional[dict]:
    """Return modal properties (periods, frequencies, participation factors).

    The report is printed to stdout **only** when ``'-print'`` is supplied.
    ``'-return'`` controls whether a dictionary is returned::

        # Returns dict (no console output)
        props = ops.modalProperties('-return', '-unorm')

        # Prints to stdout, returns None
        ops.modalProperties('-print', '-unorm')

        # Neither -return nor -print: returns None, no console output
        ops.modalProperties('-unorm')

    Args:
        *args: Options such as ``'-return'`` (return dict), ``'-print'``
               (print to console), ``'-unorm'`` (displacement‑normalised
               eigenvectors — default ARPACK result is mass‑normalised),
               ``'-file', path`` (write to file).
    Returns:
        Dictionary with keys like ``eigenFrequency``, ``eigenPeriod``,
        ``partiFactorMX``, ``partiMassMX``, ``partiMassRatiosMX``,
        ``totalFreeMass``, etc., or ``None`` if ``-return`` is not passed.
    """
    ...

# ============================================================================
# Recorder commands
# ============================================================================

def recorder(*args: Any) -> int:
    """Create a recorder to monitor analysis results.

    Args:
        *args: Recorder arguments per the OpenSees recorder command.
    Returns:
        Recorder handle (integer), or -1 on failure.
    """
    ...

# ============================================================================
# Material commands
# ============================================================================

def uniaxialMaterial(mat_type: str, tag: int, *args: Any) -> None:
    """Create a uniaxial material.

    Common concrete arguments::

        # Concrete01 (no tension): fc, epsc0, fcU, epsU
        uniaxialMaterial('Concrete01', tag, -30e6, -0.002, -6e6, -0.006)

        # Concrete02 (Kent-Scott-Park + tension softening):
        # fc, epsc0, fcU, epsU, lambda, ft, Ets
        uniaxialMaterial('Concrete02', tag, -30e6, -0.002, -3e6, -0.006, 0.1, 3e6, 3e9)

        # Bond_SP01 (Zhao-Sritharan strain penetration, used as a
        # moment-rotation law at member ends): Fy, Sy, Fu, Su, b, R
        uniaxialMaterial('Bond_SP01', tag, 150e3, 8e-4, 210e3, 0.028, 0.5, 0.7)

        # Steel01 (bilinear): Fy, E, b (strain-hardening ratio)
        uniaxialMaterial('Steel01', tag, 250e6, 200e9, 0.01)

        # Steel02 (Menegotto-Pinto): Fy, E, b, R0, cR1, cR2
        uniaxialMaterial('Steel02', tag, 400e6, 200e9, 0.01, 18.5, 0.925, 0.15)

    Args:
        mat_type: ``'Steel01'``, ``'Concrete01'``, ``'Elastic'``, etc.
        tag: Material tag.
        *args: Material-specific arguments (see OpenSees command manual).
    """
    ...


def limitCurve(curve_type: str, tag: int, *args: Any) -> None:
    """Create a limit-state limit curve (Elwood column shear/axial failure).

    Used together with ``uniaxialMaterial('LimitState', ...)`` to trigger
    drift-based shear / axial failure of RC columns.  The empirical
    equations embed imperial units (forces kip, lengths in, ``fc`` psi).

    Common forms::

        # Shear: rho, fc(psi), b, h, d, Fsw, Kdeg, Fres, defType, forType,
        #        ndI, ndJ, dof, perpDirn
        limitCurve('Shear', tag, eleTag, rho, fc, b, h, d, fsw, kdeg,
                   fres, defType, forType, ndI, ndJ, dof, perpDirn)

        # ThreePoint: (x1,y1) (x2,y2) (x3,y3) + Kdeg, Fres, defType, forType
        limitCurve('ThreePoint', tag, eleTag, x1, y1, x2, y2, x3, y3,
                   kdeg, fres, defType, forType, ndI, ndJ, dof, perpDirn)

    Args:
        curve_type: ``'Shear'``, ``'Axial'`` (broken in OpenSeesPy 3.8.0 —
            use a ``'ThreePoint'`` fit), ``'ThreePoint'``, ``'Bilinear'``,
            ``'Pivot'`` or ``'Degradation'``.
        tag: Limit-curve tag.
        *args: Curve-specific arguments (see the OpenSees command manual and
            ``docs/shear_failure_modelling.md`` Phase 3).
    """
    ...

def section(sec_type: str, tag: int, *args: Any) -> None:
    """Create a section.

    Common forms::

        # Elastic section (for linear analysis)
        section('Elastic', tag, E, A, Iz, Iy, G, J)

        # Fiber section (for nonlinear RC)
        section('Fiber', tag, '-GJ', J)
        #   ... followed by patch / layer commands

        # ElasticMembranePlateSection (for shells)
        section('ElasticMembranePlateSection', tag, E, nu, thickness)

    Args:
        sec_type: ``'Elastic'``, ``'Fiber'``, ``'ElasticMembranePlateSection'``.
        tag: Section tag.
        *args: Section-specific arguments.
    """
    ...

def patch(patch_type: str, mat_tag: int, n_y: int, n_z: int, *coords: float) -> None:
    """Define a fiber patch within a fiber section.

    Called between ``section('Fiber', ...)`` and ``section('Fiber', '-end')``::

        # Rectangular patch
        patch('rect', mat_tag, n_y, n_z, yI, zI, yJ, zJ)

        # Circular patch (annular ring)
        patch('circ', mat_tag, n_y, n_z, y_c, z_c, inner_r, outer_r)

        # Quadrilateral patch (4 corner points in order)
        patch('quad', mat_tag, n_y, n_z, y1, z1, y2, z2, y3, z3, y4, z4)

    Args:
        patch_type: ``'rect'``, ``'circ'``, or ``'quad'``.
        mat_tag: Material tag for this patch.
        n_y: Number of subdivisions in the y-direction.
        n_z: Number of subdivisions in the z-direction.
        *coords: Patch geometry coordinates.
    """
    ...

def layer(layer_type: str, mat_tag: int, n_bars: int, area: float, *coords: float) -> None:
    """Define a reinforcement layer within a fiber section.

    Called between ``section('Fiber', ...)`` and ``section('Fiber', '-end')``::

        # Straight layer (rebar along a line)
        layer('straight', mat_tag, n_bars, bar_area, y1, z1, y2, z2)

        # Circular layer (rebar on a circle)
        layer('circ', mat_tag, n_bars, bar_area, y_c, z_c, radius, startAng, endAng)

    Args:
        layer_type: ``'straight'`` or ``'circ'``.
        mat_tag: Material tag (usually Steel02 for rebar).
        n_bars: Number of bars.
        area: Cross-sectional area of each bar.
        *coords: Layer geometry.
    """
    ...

# ============================================================================
# Material commands (nD)
# ============================================================================

def nDMaterial(mat_type: str, tag: int, *args: Any) -> None:
    """Create an n‑dimensional material (for continuum/shell elements).

    Common types::

        nDMaterial('ElasticIsotropic', tag, E, nu)
        nDMaterial('J2PlateFibre', tag, E, nu, Fy, H)
        nDMaterial('ConcreteS', tag, fc, ft, Es)
        nDMaterial('PlateFromPlaneStress', tag, inner_tag, thickness)

    Args:
        mat_type: Material type name.
        tag: Material tag.
        *args: Material-specific parameters.
    """
    ...

# ============================================================================
# Element commands
# ============================================================================

def element(elem_type: str, tag: int, *args: Any) -> None:
    """Create an element.

    Common forms::

        # Elastic beam-column
        element('elasticBeamColumn', tag, iNode, jNode, secTag, transfTag)

        # Force-based beam-column (distributed plasticity)
        element('forceBeamColumn', tag, iNode, jNode, transfTag, integrationTag)

        # Displacement-based beam-column
        element('dispBeamColumn', tag, iNode, jNode, numIntPts, secTag, transfTag)

        # Nonlinear beam-column (simplified, npts integration points)
        element('nonlinearBeamColumn', tag, iNode, jNode, numIntPts, secTag, transfTag)

        # Truss (axial only)
        element('Truss', tag, iNode, jNode, area, matTag)

        # Shell (4-node quadrilateral)
        element('ShellMITC4', tag, n1, n2, n3, n4, secTag)

        # Shell (3-node triangular)
        element('ShellDKGT', tag, n1, n2, n3, secTag)

        # Zero-length (spring between coincident nodes)
        element('zeroLength', tag, iNode, jNode, '-mat', m1, ..., '-dir', d1, ...)

        # Zero-length with section (for lumped plasticity)
        element('zeroLengthSection', tag, iNode, jNode, secTag)

    Args:
        elem_type: Element type string.
        tag: Element tag.
        *args: Element-specific arguments.
    """
    ...

# ============================================================================
# Geometric transformation commands
# ============================================================================

def geomTransf(transf_type: str, tag: int, *args: Any) -> None:
    """Define a geometric transformation for frame elements.

    Common forms::

        # Linear (small displacements, no P-Delta)
        geomTransf('Linear', tag, vecXx, vecXy, vecXz)

        # P-Delta (second-order effects via geometric stiffness)
        geomTransf('PDelta', tag, vecXx, vecXy, vecXz)

        # Corotational (large displacements, for buckling/braces)
        geomTransf('Corotational', tag, vecXx, vecXy, vecXz)

        # With joint offsets (rigid end zones)
        geomTransf('Linear', tag, vecXx, vecXy, vecXz,
                   '-jntOffset', dXi, dYi, dZi, dXj, dYj, dZj)

    Args:
        transf_type: ``'Linear'``, ``'PDelta'``, or ``'Corotational'``.
        tag: Transformation tag.
        *args: Vector components or options.
    """
    ...

# ============================================================================
# Beam integration commands
# ============================================================================

def beamIntegration(integration_type: str, tag: int, *args: Any) -> None:
    """Define beam integration (for forceBeamColumn elements).

    Common forms::

        # Lobatto (Gauss-Lobatto, points concentrated at element ends)
        beamIntegration('Lobatto', tag, secTag, N)

        # HingeRadau (plastic hinge at ends + elastic interior)
        beamIntegration('HingeRadau', tag, sec_tag_i, lp_i, sec_tag_j, lp_j,
                        sec_tag_e)
        # With hinge-length type flag:
        beamIntegration('HingeRadau', tag, sec_tag_i, lp_i, sec_tag_j, lp_j,
                        sec_tag_e, '-lLengthTag', type)

        # Newton-Cotes (evenly spaced)
        beamIntegration('NewtonCotes', tag, secTag, N)

    Args:
        integration_type: ``'Lobatto'``, ``'HingeRadau'``, etc.
        tag: Integration tag.
        *args: Section tag and number of integration points (Lobatto/NewtonCotes)
               or hinge parameters (HingeRadau).
    """
    ...

# ============================================================================
# Constraint commands
# ============================================================================

def equalDOF(node_r: int, node_c: int, *dofs: int) -> None:
    """Tie selected DOFs between two nodes (master-slave constraint).

    Args:
        node_r: Retained (master) node tag.
        node_c: Constrained (slave) node tag.
        *dofs: DOF numbers to tie (1‑based).
    """
    ...

def rigidLink(link_type: str, node_r: int, node_c: int) -> None:
    """Create a rigid link between two nodes.

    Args:
        link_type: ``'bar'`` (translations only) or ``'beam'`` (all 6 DOFs).
        node_r: Retained (master) node tag.
        node_c: Constrained (slave) node tag.
    """
    ...

def rigidDiaphragm(perp_dof: int, master_tag: int, *slave_tags: int) -> None:
    """Impose a rigid diaphragm constraint.

    All slave nodes in the diaphragm are constrained to move as a rigid
    body in the plane perpendicular to *perp_dof*.

    Args:
        perp_dof: DOF perpendicular to the diaphragm (3=Z).
        master_tag: Master node tag.
        *slave_tags: Slave node tags.
    """
    ...

def equationConstraint(*args: Any) -> None:
    """Define a general multi-point constraint equation.

    See the OpenSees manual for equation syntax.
    """
    ...

# ============================================================================
# Analysis component commands
# ============================================================================

def constraints(constraint_type: str, *args: Any) -> None:
    """Set the constraint handler.

    Usage::

        constraints('Transformation')
        constraints('Penalty', 1e6, 1e6)
        constraints('Lagrange', 1e3)

    Args:
        constraint_type: ``'Transformation'``, ``'Penalty'``, ``'Lagrange'``.
        *args: Optional penalty factors (alphaS, alphaM) for Penalty/Lagrange.
    """
    ...

def numberer(numberer_type: str, *args: str) -> None:
    """Set the equation numberer.

    ``'RCM'`` (Reverse Cuthill-McKee) reduces matrix bandwidth for
    large models.  ``'Plain'`` numbers by node tag order.

    Args:
        numberer_type: ``'Plain'`` or ``'RCM'``.
        *args: Optional options (currently unused in OpenSees).
    """
    ...

def system(system_type: str, *args: str) -> None:
    """Set the system of equations solver.

    Usage::

        system('BandGeneral')
        system('UmfPack')
        system('UmfPack', '-useLongIndices')

    Args:
        system_type: ``'BandGeneral'``, ``'ProfileSPD'``, ``'UmfPack'``, etc.
        *args: Optional solver-specific options.
    """
    ...

def test(test_type: str, *args: Any) -> None:
    """Set the convergence test.

    Args:
        test_type: ``'NormDispIncr'``, ``'NormUnbalance'``, ``'EnergyIncr'``,
                   ``'FixedNumIter'``, etc.
        *args: Test-specific parameters (tol, max_iter for NormDispIncr;
               iteration count for FixedNumIter).
    """
    ...

def algorithm(algo_type: str, *args: Any) -> None:
    """Set the solution algorithm.

    Common values::

        algorithm('Newton')
        algorithm('NewtonLineSearch')
        algorithm('ModifiedNewton')
        algorithm('KrylovNewton')

    Args:
        algo_type: Algorithm name.
        *args: Algorithm-specific parameters.
    """
    ...

def integrator(integ_type: str, *args: Any) -> None:
    """Set the integrator for the analysis.

    Common forms::

        # Static load control
        integrator('LoadControl', delta_lambda)

        # Static displacement control (pushover)
        integrator('DisplacementControl', node_tag, dof, delta_disp)

        # Transient (Newmark)
        integrator('Newmark', gamma, beta)

        # Transient (Hilber-Hughes-Taylor)
        integrator('HHT', alpha)

    Args:
        integ_type: ``'LoadControl'``, ``'DisplacementControl'``, etc.
        *args: Integrator parameters.
    """
    ...

def eigen(*args: Any) -> List[float]:
    """Compute eigenvalues (natural frequencies squared).

    Supports both forms::

        eigenvalues = ops.eigen('-fullGenLapack', numModes)
        eigenvalues = ops.eigen(numModes)

    Args:
        *args: Optional solver string (e.g. ``'-fullGenLapack'``) followed
               by the number of eigenvalues to compute.
    Returns:
        List of eigenvalues ω², sorted ascending.
    """
    ...

# ============================================================================
# Sensitivity commands
# ============================================================================

def responseSpectrumAnalysis(ts_tag: int, dof: int, *args: Any) -> None:
    """Run a response‑spectrum analysis for one mode.

    Usage::

        ops.responseSpectrumAnalysis(tsTag, dof, '-mode', modeNum)
        ops.responseSpectrumAnalysis(tsTag, dof, '-mode', modeNum,
                                     '-Tn', Tn_override, '-Sa', Sa_override)

    Must be called after :func:`eigen` and :func:`modalProperties`.

    Args:
        ts_tag: Tag of a ``Path`` time series defining the spectrum.
        dof: Excitation direction (1=UX, 2=UY, 3=UZ, 4=RX, 5=RY, 6=RZ).
        *args: ``'-mode', modeNum`` and optional overrides.
    """
    ...

# ── Additional query / removal commands ──

@overload
def getEleTags() -> List[int]:
    """Return tags of all elements in the model (no selector).

    Returns:
        List of element tags.
    """
    ...

@overload
def getEleTags(selector: str, meshTag: int) -> List[int]:
    """Return element tags for a specific mesh.

    Usage::

        ops.getEleTags('-mesh', meshTag)

    Args:
        selector: ``'-mesh'``.
        meshTag: Mesh tag to filter by.
    Returns:
        List of element tags.
    """
    ...

def getLoadPatternTags() -> Tuple[int, ...]:
    """Return tags of all load patterns in the model.

    Returns:
        Tuple of load pattern tags.
    """
    ...

@overload
def remove(obj_type: str) -> None:
    """Remove all recorders.

    Usage::

        ops.remove('recorders')

    Args:
        obj_type: ``'recorders'`` (single-argument form).
    """
    ...

@overload
def remove(obj_type: str, tag: int) -> None:
    """Remove a tagged object from the model domain.

    Usage::

        ops.remove('timeSeries', ts_tag)
        ops.remove('element', elem_tag)
        ops.remove('loadPattern', pat_tag)

    Args:
        obj_type: Object type name (``'timeSeries'``, ``'element'``,
                  ``'loadPattern'``, etc.).
        tag: Tag of the object to remove.
    """
    ...

@overload
def remove(obj_type: str, nodeTag: int, dofTag: int) -> None:
    """Remove a single-point constraint (``sp``) from all load patterns.

    Usage::

        ops.remove('sp', nodeTag, dofTag)

    Args:
        obj_type: ``'sp'``.
        nodeTag: Node tag.
        dofTag: DOF number (1‑based).
    """
    ...

@overload
def remove(obj_type: str, nodeTag: int, dofTag: int, patternTag: int) -> None:
    """Remove a single-point constraint (``sp``) from the domain.

    Usage::

        ops.remove('sp', nodeTag, dofTag, patternTag)

    Args:
        obj_type: ``'sp'``.
        nodeTag: Node tag.
        dofTag: DOF number (1‑based).
        patternTag: Load pattern tag.
    """
    ...

# ============================================================================
# Fallback for any undocumented functions
# ============================================================================

def __getattr__(name: str) -> Any: ...
