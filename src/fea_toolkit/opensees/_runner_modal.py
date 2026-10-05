"""Analysis-builder mixin: modal (eigenvalue) analysis and mode-shape extraction."""

import math
from typing import Any

import openseespy.opensees as ops

# NOTE: modal analysis works directly on OpenSees eigenvalues and
# eigenvectors — no numpy import is required at module level.

#: Largest eigenvalue (ω², rad²/s²) accepted as a genuine mode.
#:
#: ``ops.eigen(N)`` on a model with fewer than ``N`` free DOFs returns the
#: available eigenvalues **padded with an uninitialised sentinel**
#: (``DBL_MAX ≈ 1.7976931348623157e+308``) — OpenSees itself warns
#: "mode <k> is out of range".  Those sentinels are finite, so they survive
#: a naive ``ev > tol`` filter, and their derived ω (~1e154) then overflows
#: the CQC correlation denominator.  Genuine eigenvalues span ~1e-6 (very
#: soft, large-unit models) to ~1e18 (stiff, small-unit models), so 1e30
#: separates real modes from sentinels with a wide margin.
_MAX_EIGENVALUE = 1.0e30


class ModalRunnerMixin:
    """Modal (eigenvalue) analysis and mode-shape extraction."""

    def _free_rotation_shell_base_nodes(self) -> list[str]:
        """Shell-only base nodes whose restraint leaves a rotation free.

        A shell-only base node is a bottom node referenced by an area element but
        no frame element (see
        :func:`~fea_toolkit.model.supports.shell_only_base_node_ids`).  When such
        a node's restraint pins translations but not rotations (``[1,1,1,0,0,0]``,
        or no restraint at all), the shell it anchors can rotate freely — a
        drilling mechanism that makes the stiffness matrix singular.  The set
        returned is what :func:`~fea_toolkit.model.supports.fix_shell_only_base_restraints`
        (and the build-time ``fix_shell_only_base`` option) would upgrade.

        Returns:
            The offending node ids, in sorted order.
        """
        from ..model.supports import restraint_dofs, shell_only_base_node_ids

        nodes = []
        for nid in sorted(shell_only_base_node_ids(self.mesh_model)):
            dofs = restraint_dofs(self.mesh_model, nid)
            if not dofs or not all(dofs[3:6]):
                nodes.append(nid)
        return nodes

    def _static_gravity_ok(self) -> bool:
        """Cheap static self-weight step — ``True`` if the solve converged.

        The eigensolver (ARPACK) loops forever on a singular stiffness matrix,
        so the modal runner probes solvability *first*: a non-convergent static
        step means the model is a mechanism, and it is reported as a clean error
        rather than a hung worker.  A healthy model converges in one step, so
        the cost is negligible.
        """
        self.create_loads(pattern_scales={"Self weight": 1.0})
        try:
            if self._edge_constraint_method == "penalty":
                ops.constraints("Penalty", 1.0e12, 1.0e12)
            else:
                ops.constraints("Transformation")
            ops.numberer("RCM")
            ops.system(self.config.get("solver_system", "BandGen"))
            ops.test("NormDispIncr", 1e-3, 5, 0)
            for alg in ("Newton", "NewtonLineSearch", "ModifiedNewton", "KrylovNewton"):
                try:
                    ops.algorithm(alg)
                except Exception:
                    continue
                ops.integrator("LoadControl", 1.0)
                ops.analysis("Static")
                if ops.analyze(1) == 0:
                    return True
        except Exception:
            pass
        return False

    def run_modal_analysis(
        self, num_modes: int = 30, print_results: bool = True, eigen_solver: str = "default"
    ) -> dict[str, Any]:
        """Run eigenvalue / modal analysis and return results.

        Requires that seismic masses have been assigned (call
        :meth:`compute_seismic_masses` first) and the domain has been
        built via :meth:`build_domain`.

        Args:
            num_modes: Number of eigenvalues to solve for.
            print_results: If True, print a modal properties table.
            eigen_solver: Solver strategy.

                ``"default"``
                    ARPACK (fast), fallback to fullGenLapack.
                ``"fullGenLapack"``
                    Robust but slow for large models.
                ``"genBandArpack"``
                    Generalized banded ARPACK — requires a Ritz pre-step.
                ``"symmBandLapack"``
                    Symmetric banded Lapack solver.
                ``"ritz"``
                    Gravity pre-step then ARPACK.

        Returns:
            Dictionary with keys:

            * ``'eigenvalues'`` — list of eigenvalues (omega^2).
            * ``'periods'`` — list of natural periods (s).
            * ``'frequencies'`` — list of natural frequencies (Hz).
            * ``'modal_props'`` — the full ``ops.modalProperties()`` dict.
            * ``'num_modes'`` — number of converged modes.
            * ``'nodal_masses'`` — dict of nodal masses ``{tag: (mx, my, mz)}``
                in model units (tonnes for kN-m models).
        """
        self._log.info("modal", f"Running modal analysis for {num_modes} modes...")

        # ── Ensure seismic masses are present ────────────────────
        _has_mass = False
        for t in ops.getNodeTags():
            try:
                m = ops.nodeMass(t)
                if sum(abs(x) for x in m) > 1e-12:
                    _has_mass = True
                    break
            except Exception:
                pass
        if not _has_mass:
            self.compute_seismic_masses()

        # ── Fail fast on a singular stiffness matrix ─────────────
        # The eigensolver (ARPACK) loops forever on a singular stiffness matrix,
        # so probe solvability *first* with a cheap static self-weight step.  A
        # non-convergent step means the model is a mechanism; report it as a clean
        # error instead of hanging the worker (and the GUI's busy cursor).  When
        # the likely cause is a shell-only base node whose rotation is free, name
        # it — but only trust that hint when the build-time fix is off, because
        # with it on the base is already fixed in the domain and the mechanism is
        # elsewhere.
        if not self._static_gravity_ok():
            if not self.config.get("fix_shell_only_base"):
                singular_nodes = self._free_rotation_shell_base_nodes()
                if singular_nodes:
                    raise RuntimeError(
                        "Modal analysis skipped: the model has "
                        f"{len(singular_nodes)} shell-only base node(s) whose rotation "
                        "is free (a shell has no drilling stiffness), so the stiffness "
                        "matrix is singular and the eigenvalue solver cannot converge.  "
                        "Fix them with a fix_shell_only_base step before meshing, or "
                        f"set fix_shell_only_base: True in this step's config, then "
                        f"retry.  Nodes: {', '.join(singular_nodes[:10])}"
                        + ("…" if len(singular_nodes) > 10 else "")
                    )
            raise RuntimeError(
                "Modal analysis skipped: a static self-weight solve did not "
                "converge, so the stiffness matrix is singular — the model has a "
                "mechanism (an unconstrained DOF or a free-floating part).  Run "
                "check_model_connectivity() to locate it, fix the support / "
                "connectivity, then retry."
            )

        # ── Ritz / pre-load nudge ────────────────────────────────
        _needs_nudge = eigen_solver in ("genBandArpack", "ritz")
        if _needs_nudge:
            self._log.info("modal", "Ritz pre-step (static gravity)...")
            if not self._static_gravity_ok():
                self._log.warning(
                    "modal",
                    "Ritz pre-step did not converge — continuing with zero initial state",
                )

        # ── Set constraint handler for eigen analysis ────────────
        try:
            if self._edge_constraint_method == "penalty":
                ops.constraints("Penalty", 1.0e12, 1.0e12)
            else:
                ops.constraints(self.config.get("solver_constraints", "Transformation"))
            ops.numberer("RCM")
            ops.system(self.config.get("solver_system", "BandGen"))
        except Exception:
            pass

        # ── Eigenvalue solver ────────────────────────────────────
        eigenvals_all = []
        _solver_map = {
            "genBandArpack": "-genBandArpack",
            "symmBandLapack": "-symmBandLapack",
            "fullGenLapack": "-fullGenLapack",
            "default": None,
        }
        solver_flag = _solver_map.get(eigen_solver)
        if solver_flag is not None:
            try:
                eigenvals_all = ops.eigen(solver_flag, num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                self._log.warning("modal", f"{eigen_solver} solver failed — falling back to ARPACK")
                try:
                    eigenvals_all = ops.eigen(num_modes)
                except Exception:
                    eigenvals_all = []
                if not eigenvals_all:
                    self._log.warning("modal", "ARPACK also failed — falling back to fullGenLapack")
                    try:
                        eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                    except Exception:
                        eigenvals_all = []
        else:
            try:
                eigenvals_all = ops.eigen(num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                self._log.warning("modal", "ARPACK solver failed — falling back to fullGenLapack")
                try:
                    eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                except Exception:
                    eigenvals_all = []

        # Keep only genuine modes: reject non-positive eigenvalues and the
        # DBL_MAX sentinels that ``ops.eigen`` pads with when the model has
        # fewer free DOFs than requested (see _MAX_EIGENVALUE).
        eigenvals = [
            ev for ev in eigenvals_all if math.isfinite(ev) and 1e-12 < ev < _MAX_EIGENVALUE
        ]
        n_modes = len(eigenvals)
        if n_modes == 0:
            raise RuntimeError(
                "Modal analysis found no positive eigenvalues — the stiffness "
                "matrix is singular, which means the model has a mechanism (an "
                "unconstrained DOF).  A common cause is a shell-only base node "
                "pinned [1,1,1,0,0,0]: a shell (e.g. ShellMITC4) has no drilling "
                "stiffness, so a shell-only pinned base can rotate freely.  Use "
                "check_model_connectivity() to locate them, and "
                "fix_shell_only_base_restraints() to fix them, before retrying."
            )
        if n_modes < num_modes:
            self._log.warning(
                "modal",
                f"only {n_modes} positive eigenvalues out of {num_modes}. "
                f"Proceeding with {n_modes} modes.",
            )

        periods = [2.0 * math.pi / math.sqrt(ev) for ev in eigenvals]
        frequencies = [math.sqrt(ev) / (2.0 * math.pi) for ev in eigenvals]

        try:
            modal_props = ops.modalProperties("-return", "-unorm")
        except Exception:
            modal_props = {}

        results = {
            "eigenvalues": eigenvals,
            "periods": periods,
            "frequencies": frequencies,
            "modal_props": modal_props,
            "num_modes": n_modes,
            "nodal_masses": self._query_nodal_masses(),
        }

        if print_results:
            print("\n===== MODAL ANALYSIS =====")
            if modal_props:
                try:
                    total_mass = modal_props.get("totalFreeMass", [0])[0]
                    print(f"Total translational mass (free DOFs): {total_mass:.2f} tonnes\n")
                    header = (
                        f"{'Mode':>5} {'Freq(Hz)':>10} {'Period(s)':>10} "
                        f"{'Mx(t)':>12} {'My(t)':>12} {'Mz(t)':>12} "
                        f"{'%X':>7} {'%Y':>7} {'%Z':>7}"
                    )
                    print(header)
                    print("-" * len(header))
                    for i in range(n_modes):
                        mx = modal_props.get("partiMassMX", [0] * n_modes)[i]
                        my = modal_props.get("partiMassMY", [0] * n_modes)[i]
                        mz = modal_props.get("partiMassMZ", [0] * n_modes)[i]
                        rx = modal_props.get("partiMassRatiosMX", [0] * n_modes)[i]
                        ry = modal_props.get("partiMassRatiosMY", [0] * n_modes)[i]
                        rz = modal_props.get("partiMassRatiosMZ", [0] * n_modes)[i]
                        print(
                            f"{i + 1:5d} {frequencies[i]:10.4f} "
                            f"{periods[i]:10.4f} {mx:12.2f} {my:12.2f} "
                            f"{mz:12.2f} {rx:6.2f}% {ry:6.2f}% {rz:6.2f}%"
                        )
                except Exception:
                    pass
            else:
                print(f"{'Mode':>5} {'Period(s)':>10} {'Freq(Hz)':>10}")
                print("-" * 30)
                for i in range(n_modes):
                    print(f"{i + 1:5d} {periods[i]:10.4f} {frequencies[i]:10.4f}")

        return results

    def extract_mode_shapes(
        self, num_modes: int
    ) -> dict[int, dict[int, tuple[float, float, float]]]:
        """Extract mode shape displacements for each node and each mode.

        Must be called **after** :meth:`run_modal_analysis`.

        Args:
            num_modes: Number of modes to extract.

        Returns:
            ``{mode_index: {node_tag: (dx, dy, dz)}}`` where *mode_index*
            is 0‑based and displacements are raw eigenvector components.
        """
        node_tags = list(ops.getNodeTags())
        dof_map = {0: 1, 1: 2, 2: 3}
        shapes: dict[int, dict[int, tuple]] = {}
        for m in range(num_modes):
            mode_num = m + 1
            per_node: dict[int, tuple] = {}
            for tag in node_tags:
                dx = ops.nodeEigenvector(tag, mode_num, dof_map[0])
                dy = ops.nodeEigenvector(tag, mode_num, dof_map[1])
                dz = ops.nodeEigenvector(tag, mode_num, dof_map[2])
                per_node[tag] = (dx, dy, dz)
            shapes[m] = per_node
        return shapes
