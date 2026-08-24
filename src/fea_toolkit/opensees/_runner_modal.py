"""Analysis-builder mixin: modal (eigenvalue) analysis and mode-shape extraction."""

import math
from typing import Any

import openseespy.opensees as ops

# NOTE: modal analysis works directly on OpenSees eigenvalues and
# eigenvectors — no numpy import is required at module level.


class ModalRunnerMixin:
    """Modal (eigenvalue) analysis and mode-shape extraction."""

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
        if self.config.get("verbose"):
            print(f"Running modal analysis for {num_modes} modes...")

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

        # ── Ritz / pre-load nudge ────────────────────────────────
        _needs_nudge = eigen_solver in ("genBandArpack", "ritz")
        if _needs_nudge:
            if self.config.get("verbose"):
                print("  Ritz pre-step (static gravity)...")
            # Run a self-weight gravity load step
            self.create_loads(pattern_scales={"Self weight": 1.0})
            try:
                if self._edge_constraint_method == "penalty":
                    ops.constraints("Penalty", 1.0e12, 1.0e12)
                else:
                    ops.constraints("Transformation")
                ops.numberer("RCM")
                ops.system(self.config.get("solver_system", "BandGen"))
                ops.test("NormDispIncr", 1e-3, 5, 0)
                _algorithms = ["Newton", "NewtonLineSearch", "ModifiedNewton", "KrylovNewton"]
                _ok = -1
                for _alg in _algorithms:
                    try:
                        ops.algorithm(_alg)
                    except Exception:
                        continue
                    ops.integrator("LoadControl", 1.0)
                    ops.analysis("Static")
                    _ok = ops.analyze(1)
                    if _ok == 0:
                        break
                if _ok != 0 and self.config.get("verbose"):
                    print("  ⚠ Ritz pre-step did not converge — continuing with zero initial state")
            except Exception:
                if self.config.get("verbose"):
                    print("  ⚠ Ritz pre-step failed — continuing")

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
                print(f"  ⚠ {eigen_solver} solver failed — falling back to ARPACK")
                try:
                    eigenvals_all = ops.eigen(num_modes)
                except Exception:
                    eigenvals_all = []
                if not eigenvals_all:
                    print("  ⚠ ARPACK also failed — falling back to fullGenLapack")
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
                print("  ⚠ ARPACK solver failed — falling back to fullGenLapack")
                try:
                    eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                except Exception:
                    eigenvals_all = []

        eigenvals = [ev for ev in eigenvals_all if ev > 1e-12]
        n_modes = len(eigenvals)
        if n_modes < num_modes and self.config.get("verbose"):
            print(
                f"  Warning: only {n_modes} positive eigenvalues out of "
                f"{num_modes}.  Proceeding with {n_modes} modes."
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
