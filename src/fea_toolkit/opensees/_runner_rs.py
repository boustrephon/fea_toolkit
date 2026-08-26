"""Analysis-builder mixin: response-spectrum analysis and RS result extraction."""

import contextlib
import math
from typing import Any, Optional, Union

import openseespy.opensees as ops

from ..utils import cqc_combine


class RsRunnerMixin:
    """Response-spectrum analysis, RS force extraction, and modal displacements."""

    def run_response_spectrum_analysis(
        self,
        num_modes: int,
        modal_periods: list[float],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        T_rigid: Optional[float] = None,
        print_results: bool = True,
    ) -> dict[str, Any]:
        """Run a response‑spectrum analysis using CQC modal combination.

        Performs mode‑by‑mode RS analysis using OpenSees'
        ``responseSpectrumAnalysis``, then combines with CQC.

        **How base reactions are computed**

        ``ops.responseSpectrumAnalysis(... '-mode', mode)`` sets the
        modal displacement field for a single mode on the domain (via
        ``node->setTrialDisp()``).  After ``commitDomain()``, element
        internal forces are consistent with those displacements.  We then
        query ``ops.eleResponse(eid, 'forces')`` which returns **global
        element‑end forces** ``[Fx, Fy, Fz, Mx, My, Mz]`` at the I-end
        then J-end.

        For each base-connected element we extract the base-end forces
        and accumulate into a per-mode 6-DoF reaction vector.  The
        element-end moments include column bending directly, but the
        **axial force lever‑arm** (Fz from one column × distance to the
        centroid of another) is a structural‑level effect not present in
        individual element stiffness outputs.  We add it here:

        ``mx += Mx_direct + Fz·dy − Fy·dz``
        ``my += My_direct + Fx·dz − Fz·dx``

        using the **fixed geometric centroid** (bounding-box midpoint
        ``(min+max)/2`` of all nodes).  This fixed reference is the same
        as :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        uses for static lateral loads — ensuring consistent moment
        origins across all analysis types.

        .. note::
           A fixed reference point is **required** for CQC combination:
           if each mode used its own Fz-weighted centroid, the per-mode
           moments would reference different points and CQC would be
           physically invalid.

        Args:
            num_modes: Number of modes to include.
            modal_periods: Natural periods of each mode (s).
            spectrum_periods: Period axis of the response spectrum (s).
            spectrum_accels: Spectral acceleration values (m/s^2).
            direction: Excitation direction — ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            T_rigid: Rigid cut-off period (s). ``None`` = no cut-off.
            print_results: If True, print a summary table.

        Returns:
            Dictionary with:
            - ``modal_base_shear`` / ``modal_base_moment`` (scalar, backward compat)
            - ``base_shear_cqc`` / ``base_shear_srss`` / ``base_moment_cqc`` / ``base_moment_srss``
            - ``modal_periods``
            - ``modal_base_reactions`` (list of 6-DoF dicts per mode)
            - ``base_reactions_cqc`` / ``base_reactions_srss`` (6-DoF combined)
                where Mx/My include overturning from Fz × lever-arm about
                the fixed geometric centroid (bounding-box midpoint).
                This fixed reference ensures CQC validity across modes.
        """
        if self.config.get("verbose"):
            print(f"Running response spectrum analysis (dir={direction})...")

        num_modes = min(num_modes, len(modal_periods))
        if num_modes == 0:
            raise ValueError("No modal periods available for RS analysis")

        # num_modes is clamped above; omega must match damp_ratios and the
        # value list consumed by the CQC combination, so slice to num_modes.
        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods[:num_modes]]
        damp_ratios = [damping_ratio] * num_modes

        SPECTRUM_TS_TAG = 9999
        with contextlib.suppress(Exception):
            ops.remove("timeSeries", SPECTRUM_TS_TAG)
        ops.timeSeries(
            "Path", SPECTRUM_TS_TAG, "-time", *spectrum_periods, "-values", *spectrum_accels
        )

        modal_base_shear = []
        modal_base_moment = []
        dof = {"X": 1, "Y": 2, "Z": 3}[direction]

        dof_idx = {"X": 0, "Y": 1, "Z": 2}[direction]
        base_nodes = {
            nid
            for nid, r in self.mesh_model.restraints.items()
            if len(r.dofs) > dof_idx and r.dofs[dof_idx] == 1
        }

        elements = self.mesh_model.frame_elements
        # Pre-compute base-element node coordinates for lever-arm inside
        # the base-element loop, so every frame-map identifier resolves to
        # its *actual* element.  Split-frame children are addressed by
        # their frame_tag_map ops-tag (not elem.elem_tag) — an elem_tag-
        # keyed index would silently drop them from _base_elem_coords.
        _base_elem_coords = []
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is None or nd_j is None:
                continue
            # Use the actual OpenSees tag from the frame_tag_map so that
            # split-frame children are addressed by their correct tag.
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            if elem.node_i in base_nodes and elem.node_j not in base_nodes:
                _base_elem_coords.append((ops_tag, "i", nd_i.x, nd_i.y, nd_i.z))
            elif elem.node_j in base_nodes and elem.node_i not in base_nodes:
                _base_elem_coords.append((ops_tag, "j", nd_j.x, nd_j.y, nd_j.z))

        # ── Pre-compute fixed reference point for overturning moment ──
        # Compute from base (support) nodes only — the centre of the base
        # footprint. This ensures a consistent reference across all modes
        # for valid CQC combination.  Same approach as
        # sum_reactions_with_overturning in utils.py.
        _base_nds = [
            self.mesh_model.nodes[nid] for nid in base_nodes if nid in self.mesh_model.nodes
        ]
        if _base_nds:
            _xs = [n.x for n in _base_nds]
            _ys = [n.y for n in _base_nds]
            _cx = (min(_xs) + max(_xs)) * 0.5
            _cy = (min(_ys) + max(_ys)) * 0.5
            _z_base = sum(n.z for n in _base_nds) / len(_base_nds)
        else:
            _cx = _cy = _z_base = 0.0

        modal_base_reactions = []
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, "-mode", mode)

            rxn = {"fx": 0.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}
            for eid, end, nx, ny, nz in _base_elem_coords:
                try:
                    forces = ops.eleResponse(eid, "forces")
                except Exception:
                    continue
                if end == "i":
                    fx, fy, fz, mx, my, mz = (
                        forces[0],
                        forces[1],
                        forces[2],
                        forces[3],
                        forces[4],
                        forces[5],
                    )
                else:
                    fx, fy, fz, mx, my, mz = (
                        forces[6],
                        forces[7],
                        forces[8],
                        forces[9],
                        forces[10],
                        forces[11],
                    )

                rxn["fx"] += fx
                rxn["fy"] += fy
                rxn["fz"] += fz
                # Overturning: direct moment + force × lever-arm about fixed reference
                dx = nx - _cx
                dy = ny - _cy
                dz = nz - _z_base
                rxn["mx"] += mx + fz * dy - fy * dz
                rxn["my"] += my + fx * dz - fz * dx
                rxn["mz"] += mz + fy * dx - fx * dy

            modal_base_reactions.append(rxn)

        # ── CQC / SRSS per component ───────────────────────────
        dof_map = {"X": (0, 4), "Y": (1, 3), "Z": (2, 4)}
        #   X: shear=fx(idx 0), overturning=my(idx 4)
        #   Y: shear=fy(idx 1), overturning=mx(idx 3)  ← was mz before fix
        #   Z: shear=fz(idx 2), overturning=my(idx 4)
        f_idx, m_idx = dof_map[direction]
        comp_order = ["fx", "fy", "fz", "mx", "my", "mz"]

        # Keep scalar arrays for backward compat
        modal_base_shear = [r[comp_order[f_idx]] for r in modal_base_reactions]
        modal_base_moment = [r[comp_order[m_idx]] for r in modal_base_reactions]

        base_reactions_cqc = {}
        base_reactions_srss = {}
        for comp in comp_order:
            vals = [r[comp] for r in modal_base_reactions]
            base_reactions_cqc[comp] = cqc_combine(vals, omega, damp_ratios)
            base_reactions_srss[comp] = math.sqrt(sum(v * v for v in vals))

        base_shear_cqc = base_reactions_cqc[comp_order[f_idx]]
        base_shear_srss = base_reactions_srss[comp_order[f_idx]]
        base_moment_cqc = base_reactions_cqc[comp_order[m_idx]]
        base_moment_srss = base_reactions_srss[comp_order[m_idx]]

        result = {
            "modal_base_shear": modal_base_shear,
            "modal_base_moment": modal_base_moment,
            "base_shear_cqc": base_shear_cqc,
            "base_shear_srss": base_shear_srss,
            "base_moment_cqc": base_moment_cqc,
            "base_moment_srss": base_moment_srss,
            "modal_periods": modal_periods,
            # New: full 6-DoF base reactions per-mode and combined
            "modal_base_reactions": modal_base_reactions,
            "base_reactions_cqc": base_reactions_cqc,
            "base_reactions_srss": base_reactions_srss,
        }

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM ({direction}) =====")
            print(f"{'Mode':>5} {'Period(s)':>10} {'Shear (kN)':>14} {'Moment (kN-m)':>16}")
            print("-" * 48)
            for i, (T, v, m) in enumerate(
                zip(modal_periods[:num_modes], modal_base_shear, modal_base_moment)
            ):
                print(f"{i + 1:5d} {T:10.4f} {v:14.2f} {m:16.2f}")
            print("-" * 48)
            print(f"{'CQC':>5} {'':>10} {base_shear_cqc:14.2f} {base_moment_cqc:16.2f}")
            print(f"{'SRSS':>5} {'':>10} {base_shear_srss:14.2f} {base_moment_srss:16.2f}")
            print()

        return result

    def extract_element_rs_forces(
        self,
        num_modes: int,
        modal_periods: list[float],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> dict[str, Any]:
        """Run RS analysis and return CQC‑combined element forces sorted by height.

        For each element this returns the CQC‑combined moments (My_i, My_j,
        Mz_i, Mz_j) and the corresponding shears derived from the moment
        gradient (Vy = dMz/dx, Vz = dMy/dx).

        The parameters mirror :meth:`run_response_spectrum_analysis`.

        Returns:
            Dictionary with keys:

            * ``'element_results'`` — list of dicts sorted by elevation, each
                containing ``elem_id``, ``z_bot``, ``z_mid``, ``Vy_i``, ``Vy_j``,
                ``Vz_i``, ``Vz_j``, ``My_i``, ``My_j``, ``Mz_i``, ``Mz_j``.
            * ``'modal_periods'``, ``'omega'`` — for diagnostics.
        """
        if self.config.get("verbose"):
            print("Extracting element RS forces...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        dof = {"X": 1, "Y": 2, "Z": 3}[direction]

        SPECTRUM_TS_TAG = 9999

        # Create the Path time series before the mode loop so standalone
        # calls work — matching run_response_spectrum_analysis (which
        # registers it under the same tag from the same inputs).
        with contextlib.suppress(Exception):
            ops.remove("timeSeries", SPECTRUM_TS_TAG)
        ops.timeSeries(
            "Path", SPECTRUM_TS_TAG, "-time", *spectrum_periods, "-values", *spectrum_accels
        )

        elements = self.mesh_model.frame_elements

        # Pre-compute element info + storage
        elem_data = {}
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            z_i, z_j = ni.z, nj.z
            if z_i > z_j:
                z_i, z_j = z_j, z_i
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            elem_data[eid] = {
                "tag": ops_tag,
                "elem_id": eid,
                "z_bot": z_i,
                "z_mid": (z_i + z_j) * 0.5,
                "My_i": [],
                "My_j": [],
                "Mz_i": [],
                "Mz_j": [],
            }

        # Mode-by-mode extraction
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, "-mode", mode)
            for eid, ed in elem_data.items():
                try:
                    forces = ops.eleResponse(ed["tag"], "forces")
                except Exception:
                    forces = [0.0] * 12
                ed["My_i"].append(forces[4])
                ed["My_j"].append(forces[10])
                ed["Mz_i"].append(forces[5])
                ed["Mz_j"].append(forces[11])

        # CQC combine per element and compute shears
        element_results = []
        for eid, ed in elem_data.items():
            ne = len(ed["My_i"])
            n_use = min(ne, num_modes)
            o_use = omega[:n_use]
            d_use = damp_ratios[:n_use]

            My_i = cqc_combine(ed["My_i"][:n_use], o_use, d_use)
            My_j = cqc_combine(ed["My_j"][:n_use], o_use, d_use)
            Mz_i = cqc_combine(ed["Mz_i"][:n_use], o_use, d_use)
            Mz_j = cqc_combine(ed["Mz_j"][:n_use], o_use, d_use)

            # Element length
            elem = elements.get(eid)
            if elem:
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z) if ni and nj else 1.0
            else:
                L = 1.0

            # Shear from moment gradient
            Vy_i = (Mz_i - Mz_j) / L if L > 1e-12 else 0.0
            Vy_j = Vy_i
            Vz_i = (My_i - My_j) / L if L > 1e-12 else 0.0
            Vz_j = Vz_i

            element_results.append(
                {
                    "elem_id": ed["elem_id"],
                    "z_bot": ed["z_bot"],
                    "z_mid": ed["z_mid"],
                    "Vy_i": Vy_i,
                    "Vy_j": Vy_j,
                    "Vz_i": Vz_i,
                    "Vz_j": Vz_j,
                    "My_i": My_i,
                    "My_j": My_j,
                    "Mz_i": Mz_i,
                    "Mz_j": Mz_j,
                }
            )

        # Sort by height
        element_results.sort(key=lambda r: r["z_mid"])

        if print_results:
            print(
                f"\n===== RESPONSE SPECTRUM RESULTS ({direction} only, CQC) FOR ALL ELEMENTS ====="
            )
            header = (
                f"{'Elem':>30} {'Z_bot(m)':>10} {'Z_mid(m)':>10} {'End':>5} "
                f"{'Vy (kN)':>12} {'Vz (kN)':>12} {'My (kN-m)':>12} {'Mz (kN-m)':>12}"
            )
            print(header)
            print("-" * len(header))
            for r in element_results:
                eid_str = f"{r['elem_id']:30s}"
                print(
                    f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'I':>5} "
                    f"{r['Vy_i']:12.2f} {r['Vz_i']:12.2f} {r['My_i']:12.2f} {r['Mz_i']:12.2f}"
                )
                print(
                    f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'J':>5} "
                    f"{r['Vy_j']:12.2f} {r['Vz_j']:12.2f} {r['My_j']:12.2f} {r['Mz_j']:12.2f}"
                )

        return {
            "element_results": element_results,
            "modal_periods": modal_periods,
            "omega": omega,
        }

    def compute_rs_nodal_displacements(
        self,
        num_modes: int,
        modal_periods: list[float],
        eigenvalues: list[float],
        spectrum_func,
        direction: str = "X",
        damping_ratio: float = 0.05,
        return_srss: bool = False,
    ) -> Union[
        dict[int, tuple[float, float, float]],
        tuple[dict[int, tuple[float, float, float]], dict[int, tuple[float, float, float]]],
    ]:
        """Compute CQC‑ (and optionally SRSS‑) combined peak nodal
        displacements from RS analysis.

        Uses mode‑shape superposition rather than re‑running the RS analysis:

            u_m = Γ_m · φ_m · Sa_m / ω²_m

        then combined with CQC (and optionally SRSS).

        Args:
            num_modes: Number of modes.
            modal_periods: Natural periods of each mode (s).
            eigenvalues: Eigenvalues (ω²) from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²**.
            direction: Excitation direction ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            return_srss: If True, return ``(cqc_result, srss_result)``
                as a tuple of two dicts.  If False (default), return
                only ``cqc_result`` for backward compatibility.

        Returns:
            Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model length
            units.  When ``return_srss=True``, returns a tuple of two
            such dicts: ``(cqc, srss)``.
        """
        dof = {"X": 1, "Y": 2, "Z": 3}[direction]
        dof_idx = dof - 1

        # Get participation factors from modalProperties
        try:
            mp = ops.modalProperties("-return", "-unorm")
        except Exception:
            mp = {}
        mass_key = (
            "partiMassMX"
            if direction == "X"
            else "partiMassMY"
            if direction == "Y"
            else "partiMassMZ"
        )
        eff_masses = mp.get(mass_key, [0.0] * num_modes)

        # omega must stay aligned with the num_modes entries appended to
        # per_mode[tag][d] below — slice modal_periods to num_modes first.
        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods[:num_modes]]
        damp = [damping_ratio] * num_modes

        node_tags = list(ops.getNodeTags())

        per_mode = {tag: {d: [] for d in range(3)} for tag in node_tags}

        for m in range(num_modes):
            if eigenvalues[m] <= 1e-12 or omega[m] <= 1e-12:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            T = modal_periods[m]
            Sa = spectrum_func(T)
            Gamma = math.sqrt(abs(eff_masses[m])) if eff_masses[m] != 0 else 0.0
            factor = Gamma * Sa / (omega[m] ** 2)

            if abs(factor) < 1e-15:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            for tag in node_tags:
                phi = ops.nodeEigenvector(tag, m + 1, dof)
                per_mode[tag][dof_idx].append(phi * factor)
                for d in range(3):
                    if d != dof_idx:
                        per_mode[tag][d].append(0.0)

        cqc_result = {}
        srss_result = {}
        for tag in node_tags:
            cqc_vals = tuple(cqc_combine(per_mode[tag][d], omega, damp) for d in range(3))
            cqc_result[tag] = cqc_vals
            srss_vals = tuple(math.sqrt(sum(v * v for v in per_mode[tag][d])) for d in range(3))
            srss_result[tag] = srss_vals

        if return_srss:
            return cqc_result, srss_result
        return cqc_result
