"""Analysis-builder mixin: loads and rigid diaphragms."""

import logging
import math
from typing import Any, Optional

import numpy as np
import openseespy.opensees as ops

from ..model.geometry import get_local_axes, polygon_area_3d
from ..model.sap_data import FrameElement
from ..model.tree_utils import collect_descendants

logger = logging.getLogger(__name__)


class LoadMixin:
    """Load-pattern creation, gravity axial-load derivation, and rigid-diaphragm application."""

    def create_loads(
        self,
        pattern_scales: Optional[dict[str, float]] = None,
    ) -> None:
        """Create load patterns on the OpenSees domain.

        Args:
            pattern_scales: Dict mapping pattern name → scale factor.
                If provided, only these patterns are created.  If None,
                all patterns from the mesh model are applied.
        """
        self._create_loads(pattern_scales=pattern_scales)
        self._apply_rigid_diaphragms()

    @staticmethod
    def _is_gravity_pattern(pattern: Any) -> bool:
        """True for DEAD / SUPERDEAD / GRAVITY-type load patterns."""
        name = str(getattr(pattern, "name", "") or "").upper()
        ptype = str(getattr(pattern, "pattern_type", "") or "").upper()
        swf = float(getattr(pattern, "self_weight_factor", 0.0) or 0.0)
        return (
            ptype.startswith(("DEAD", "SUPER", "GRAV"))
            or name.startswith(("DEAD", "GRAV"))
            or swf > 0.0
        )

    def _top_node_id(self, elem: FrameElement) -> str:
        """ID of the higher-Z end of a frame element (gravity convention)."""
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return elem.node_j
        return elem.node_j if nj.z >= ni.z else elem.node_i

    def _member_self_weight(self, eid: str) -> float:
        """Total self-weight of a frame element (model force, downward +)."""
        elem = self.mesh_model.frame_elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            return 0.0
        sec_name = self.mesh_model.frame_assignments.get(eid)
        sec = self.mesh_model.sections.get(sec_name) if sec_name else None
        mat = self.mesh_model.materials.get(sec.material) if sec is not None else None
        if sec is None or mat is None:
            return 0.0
        a = float(getattr(sec, "A", 0.0) or 0.0)
        if a <= 0.0 or mat.unit_weight == 0.0:
            return 0.0
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return 0.0
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        return a * mat.unit_weight * L

    def _member_vertical_gravity_load(self, eid: str, grav_patterns: set) -> float:
        """Total vertical gravity load on a member (model force, downward +).

        Self-weight (with the pattern's ``self_weight_factor`` and explicit
        ``frame_gravity_loads`` multipliers) plus the vertical component of
        distributed loads in gravity patterns.  Positive = downward
        (compression at a supporting joint).
        """
        elem = self.mesh_model.frame_elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            return 0.0
        sec_name = self.mesh_model.frame_assignments.get(eid)
        sec = self.mesh_model.sections.get(sec_name) if sec_name else None
        mat = self.mesh_model.materials.get(sec.material) if sec is not None else None
        if sec is None or mat is None:
            return 0.0
        a = float(getattr(sec, "A", 0.0) or 0.0)
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return 0.0
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)

        total = 0.0
        # ── Self-weight (per gravity pattern, with its SWF) ──
        for pname in grav_patterns:
            pat = self.mesh_model.load_patterns.get(pname)
            if pat is None:
                continue
            swf = float(getattr(pat, "self_weight_factor", 0.0) or 0.0)
            if mat.unit_weight != 0.0 and a > 0.0 and swf != 0.0:
                total += a * mat.unit_weight * L * swf
            # Explicit frame gravity-load multipliers (vertical component)
            for gl in getattr(self.mesh_model, "frame_gravity_loads", []):
                if gl.pattern == pname and gl.frame_id == eid:
                    mz = float(getattr(gl, "multiplier_z", 0.0) or 0.0)
                    if mat.unit_weight != 0.0 and a > 0.0:
                        total += a * mat.unit_weight * L * mz

        # ── Distributed loads projected onto the global vertical axis ──
        for dl in self.mesh_model.frame_dist_loads:
            if dl.pattern not in grav_patterns or dl.frame_id != eid:
                continue
            gdir = self._load_global_direction(dl, elem)
            span = (
                float(getattr(dl, "rdist_b", 1.0) or 1.0)
                - float(getattr(dl, "rdist_a", 0.0) or 0.0)
            ) * L
            wavg = 0.5 * (
                float(getattr(dl, "val_a", 0.0) or 0.0) + float(getattr(dl, "val_b", 0.0) or 0.0)
            )
            # Positive vertical component (z_down = -1) contributes compression.
            total += -wavg * gdir[2] * span
        return total

    def _load_global_direction(self, dl: Any, elem: FrameElement) -> tuple:
        """Global direction vector of a distributed-load direction string."""
        direction = str(getattr(dl, "direction", "Gravity"))
        if direction == "Gravity":
            return (0.0, 0.0, -1.0)
        if direction == "X":
            return (1.0, 0.0, 0.0)
        if direction == "Y":
            return (0.0, 1.0, 0.0)
        if direction == "Z":
            return (0.0, 0.0, 1.0)
        # Local directions: compute from mesh geometry (ops domain not yet built)
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is not None and nj is not None:
            vec_x = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
            if np.linalg.norm(vec_x) > 1e-12:
                try:
                    vx, vy, vz = get_local_axes(vec_x, float(getattr(elem, "angle", 0.0) or 0.0))
                    if direction == "LocalX":
                        return tuple(vx)
                    if direction == "LocalY":
                        return tuple(vy)
                    if direction == "LocalZ":
                        return tuple(vz)
                except Exception:
                    pass
        return (0.0, 0.0, -1.0)

    @staticmethod
    def _element_is_vertical(elem: FrameElement, nodes: dict) -> bool:
        """True when the element is aligned with the global Z axis (a column).

        Only Z-aligned members qualify as "the column directly above" for
        the gravity-axial recursion in :meth:`_stack_gravity_axial`.  An
        *axis*-aligned check would also flag horizontal roof beams, whose
        self-weights and floor loads are already covered by
        :meth:`_member_vertical_gravity_load` — recursing into them
        double-counts the whole roof grid and inflates ``P_g``.
        """
        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            return False
        dx = nj.x - ni.x
        dy = nj.y - ni.y
        dz = nj.z - ni.z
        L = math.hypot(dx, dy, dz)
        if L <= 0.0:
            return False
        return abs(dz) / L > 0.99

    def _stack_gravity_axial(self, eid: str, _visiting: Optional[set] = None) -> float:
        """Tributary gravity axial force at the top of a column (compression +).

        Recurses up the vertical column stack so multi-storey columns pick
        up the storeys above: at each joint the joint loads plus one half
        of every non-column member's vertical gravity load are summed, and
        the column above contributes its own self-weight and its top joint
        tributary.  A ``column_gravity_loads`` override always wins over
        this estimate (see :meth:`_derive_gravity_axial_loads`).
        """
        _visiting = set() if _visiting is None else _visiting
        if eid in _visiting:
            return 0.0
        _visiting = _visiting | {eid}
        elem = self.mesh_model.frame_elements.get(eid)
        if elem is None:
            return 0.0
        grav = {n for n, p in self.mesh_model.load_patterns.items() if self._is_gravity_pattern(p)}
        top_id = self._top_node_id(elem)
        nodes = self.mesh_model.nodes
        P = 0.0
        # Joint loads at the column top (vertical component, compression +)
        for jl in self.mesh_model.joint_loads:
            if jl.pattern in grav and jl.node_id == top_id:
                P += max(-float(getattr(jl, "fz", 0.0) or 0.0), 0.0)
        # Members framing into the top joint
        for eid2, elem2 in self.mesh_model.frame_elements.items():
            if eid2 == eid or getattr(elem2, "inactive", False):
                continue
            if top_id not in (elem2.node_i, elem2.node_j):
                continue
            if self._element_is_vertical(elem2, nodes):
                # Column directly above: its self-weight is carried at its
                # base (= this joint) plus the axial at its own top.
                P += self._member_self_weight(eid2) + self._stack_gravity_axial(eid2, _visiting)
            else:
                P += 0.5 * self._member_vertical_gravity_load(eid2, grav)
        return max(P, 0.0)

    def _derive_gravity_axial_loads(self, col_ids: list) -> dict[str, float]:
        """Per-column operating gravity axial load ``P_g`` (compression +).

        Explicit ``column_gravity_loads`` overrides win; otherwise the
        tributary estimate of :meth:`_stack_gravity_axial` is used.
        """
        overrides = self.config.get("column_gravity_loads") or {}
        result: dict[str, float] = {}
        for eid in col_ids:
            if eid in overrides:
                result[eid] = float(overrides[eid] or 0.0)
            else:
                result[eid] = self._stack_gravity_axial(eid)
        return result

    def _create_loads(
        self,
        pattern_scales: Optional[dict[str, float]] = None,
    ) -> None:
        """Create OpenSees load patterns from MeshModel data."""

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads
        edge_loads = self.edge_loads_from_areas

        patterns_created: set = set()
        self.load_totals = {}
        self._sw_load_totals = {}
        self._gravity_load_totals = {}
        self._joint_load_totals = {}

        # ── Pre-compute frame + area self-weight per-node ────────
        # Stored as a list of (node_tag, fz) tuples; applied per-pattern
        # during the pattern loop below if the pattern's swf > 0.
        _sw_node_loads: list[tuple[int, float]] = []
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(eid, "")
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            _A = getattr(sec, "A", 0.0)
            if _A <= 0:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
            total_w = _A * mat.unit_weight * L
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is not None:
                _sw_node_loads.append((nd_i.node_tag, -total_w * 0.5))
            if nd_j is not None:
                _sw_node_loads.append((nd_j.node_tag, -total_w * 0.5))

        # ── Area element self-weight ─────────────────────────────
        from ..model.sap_data import ShellSection as _ShellSec

        for aid, area in self.mesh_model.area_elements.items():
            if getattr(area, "inactive", False):
                continue
            sec_name = self.mesh_model.area_assignments.get(aid, "")
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None or not isinstance(sec, _ShellSec):
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            t = getattr(sec, "thickness", 0.0)
            if t <= 0:
                continue
            poly = [self.mesh_model.nodes.get(nid) for nid in area.node_ids]
            poly = [nd for nd in poly if nd is not None]
            if len(poly) < 3:
                continue
            area_3d = 0.0
            v0 = np.array([poly[0].x, poly[0].y, poly[0].z])
            for k in range(1, len(poly) - 1):
                v1 = np.array([poly[k].x, poly[k].y, poly[k].z])
                v2 = np.array([poly[k + 1].x, poly[k + 1].y, poly[k + 1].z])
                area_3d += 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
            total_w = area_3d * t * mat.unit_weight
            n_corners = len(poly)
            for nd in poly:
                _sw_node_loads.append((nd.node_tag, -total_w / n_corners))

        # Pattern loop — deterministic tag generation
        all_patterns = set()
        for ld in dist_loads:
            all_patterns.add(ld.pattern)
        for ld in edge_loads:
            all_patterns.add(ld.pattern)
        for jl in getattr(self.mesh_model, "joint_loads", []):
            all_patterns.add(jl.pattern)
        for gl in getattr(self.mesh_model, "frame_gravity_loads", []):
            all_patterns.add(gl.pattern)
        for agl in getattr(self.mesh_model, "area_gravity_loads", []):
            all_patterns.add(agl.pattern)
        # Include patterns with self_weight_factor > 0 so their self-weight
        # can be activated even when they have no explicit load entries.
        for pn, lp in self.mesh_model.load_patterns.items():
            if abs(getattr(lp, "self_weight_factor", 0.0)) > 1e-12:
                all_patterns.add(pn)
        # Assign deterministic tags based on sorted pattern names
        _pat_tags = {pname: (1000 + i, 100 + i) for i, pname in enumerate(sorted(all_patterns))}

        for pname in sorted(all_patterns):
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0

            ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
            ops.timeSeries("Linear", ts_tag)
            ops.pattern("Plain", ptag, ts_tag)
            patterns_created.add(pname)

            load_total = 0.0

            # Frame distributed loads
            for ld in dist_loads:
                if ld.pattern != pname:
                    continue
                tag = self.frame_tag_map.get(ld.frame_id)
                if tag is None:
                    continue
                elem = elements.get(ld.frame_id)
                if elem is None or getattr(elem, "inactive", False):
                    continue
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue

                wa = ld.val_a * scale
                wb = ld.val_b * scale
                aL = ld.rdist_a
                bL = ld.rdist_b

                vx, vy, vz = self.get_local_axes(elem)
                T = np.column_stack([vx, vy, vz])
                dir_map = {"Gravity": (0, 0, -1), "X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa
                wz_a = g_local[2] * wa
                wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb
                wz_b = g_local[2] * wb
                wx_b = g_local[0] * wb

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad("-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad("-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a, aL, bL)
                else:
                    L_seg = bL - aL
                    for i in range(4):
                        seg_a = aL + i * L_seg / 4
                        seg_b = aL + (i + 1) * L_seg / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad(
                            "-ele",
                            tag,
                            "-type",
                            "-beamUniform",
                            wy_a + (wy_b - wy_a) * xi,
                            wz_a + (wz_b - wz_a) * xi,
                            wx_a + (wx_b - wx_a) * xi,
                            seg_a,
                            seg_b,
                        )

                load_total += abs(wa + wb) * 0.5 * abs(bL - aL)

            # Edge loads (from area-to-frame conversion)
            for ld in edge_loads:
                if ld.pattern != pname:
                    continue
                tag = self.frame_tag_map.get(ld.frame_id)
                if tag is None:
                    continue
                # Look up the unsplit (original) frame element to get
                # its local axes for projecting the global direction.
                elem = self.mesh_model.frame_elements.get(ld.frame_id)
                if elem is None or getattr(elem, "inactive", False):
                    continue
                try:
                    vx, vy, vz = self.get_local_axes(elem)
                except Exception:
                    continue
                # Determine the global direction vector
                if ld.direction == "Gravity":
                    gdir = np.array([0.0, 0.0, -1.0])
                elif ld.direction == "X":
                    gdir = np.array([1.0, 0.0, 0.0])
                elif ld.direction == "Y":
                    gdir = np.array([0.0, 1.0, 0.0])
                elif ld.direction == "Z":
                    gdir = np.array([0.0, 0.0, 1.0])
                elif ld.direction == "LocalX":
                    gdir = vx
                elif ld.direction == "LocalY":
                    gdir = vy
                elif ld.direction == "LocalZ":
                    gdir = vz
                else:
                    gdir = np.array([0.0, 0.0, -1.0])
                wa = ld.val_a * scale
                wb = ld.val_b * scale
                a_overL = max(0.0, min(1.0, ld.rdist_a))
                b_overL = max(0.0, min(1.0, ld.rdist_b))
                # Project global direction onto local axes
                wx_a = wa * np.dot(gdir, vx)
                wy_a = wa * np.dot(gdir, vy)
                wz_a = wa * np.dot(gdir, vz)
                wx_b = wb * np.dot(gdir, vx)
                wy_b = wb * np.dot(gdir, vy)
                wz_b = wb * np.dot(gdir, vz)
                # Apply using the same approach as the legacy Builder
                is_uniform = abs(wa - wb) < 1e-6
                is_full_span = abs(a_overL) < 1e-12 and abs(b_overL - 1.0) < 1e-12
                if is_uniform and is_full_span:
                    ops.eleLoad("-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad(
                        "-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a, a_overL, b_overL
                    )
                else:
                    # Non-uniform → decompose into partial-span segments
                    N = 4
                    span_frac = b_overL - a_overL
                    for i in range(N):
                        seg_a = a_overL + i * span_frac / N
                        seg_b = a_overL + (i + 1) * span_frac / N
                        xi = (i + 0.5) / N
                        wy_mid = wy_a + (wy_b - wy_a) * xi
                        wz_mid = wz_a + (wz_b - wz_a) * xi
                        wx_mid = wx_a + (wx_b - wx_a) * xi
                        ops.eleLoad(
                            "-ele",
                            tag,
                            "-type",
                            "-beamUniform",
                            wy_mid,
                            wz_mid,
                            wx_mid,
                            seg_a,
                            seg_b,
                        )
                load_total += abs(wa) * abs(b_overL - a_overL)

            # ── Self-weight for this pattern ────────────────────────
            # Apply if the pattern has self_weight_factor > 0 (e.g. DEAD swf=1).
            # Look up the pattern's swf from MeshModel load_patterns (passed
            # through from SAP2000 by the Preprocessor).
            _lp = self.mesh_model.load_patterns.get(pname)
            swf = getattr(_lp, "self_weight_factor", 0.0) if _lp else 0.0
            if abs(swf) > 1e-12:
                sw_scale = swf * scale
                sw_total = 0.0
                _sw_fz_total = 0.0
                for node_tag, fz in _sw_node_loads:
                    ops.load(node_tag, 0.0, 0.0, fz * sw_scale, 0.0, 0.0, 0.0)
                    sw_total += abs(fz * sw_scale)
                    _sw_fz_total += fz * sw_scale
                # Store per-pattern for check_self_weight_consistency
                if pname not in self._sw_load_totals:
                    self._sw_load_totals[pname] = dict.fromkeys(
                        ("fx", "fy", "fz", "mx", "my", "mz"), 0.0
                    )
                self._sw_load_totals[pname]["fz"] += _sw_fz_total
                load_total += sw_total

            # ── Joint loads (SAP2000 "JOINT LOADS - FORCE") ──────────
            # Point forces/moments at joints are applied as nodal loads.
            # Previously these were parsed and carried through the
            # Preprocessor but never emitted to the OpenSees domain
            # (Gap 4 discovery — the Vecchio & Emara benchmark's 700 kN
            # column loads were silently dropped).
            for jl in getattr(self.mesh_model, "joint_loads", []):
                if jl.pattern != pname:
                    continue
                node = self.mesh_model.nodes.get(jl.node_id)
                if node is None:
                    continue
                ops.load(
                    node.node_tag,
                    jl.fx * scale,
                    jl.fy * scale,
                    jl.fz * scale,
                    jl.mx * scale,
                    jl.my * scale,
                    jl.mz * scale,
                )
                load_total += scale * (
                    abs(jl.fx) + abs(jl.fy) + abs(jl.fz) + abs(jl.mx) + abs(jl.my) + abs(jl.mz)
                )
                if pname not in self._joint_load_totals:
                    self._joint_load_totals[pname] = dict.fromkeys(
                        ("fx", "fy", "fz", "mx", "my", "mz"), 0.0
                    )
                for _k in ("fx", "fy", "fz", "mx", "my", "mz"):
                    self._joint_load_totals[pname][_k] += getattr(jl, _k) * scale

            self.load_totals[pname] = load_total

        # ── Frame gravity loads (explicit multipliers on self-weight) ──
        for gl in getattr(self.mesh_model, "frame_gravity_loads", []):
            pname = gl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
            if abs(scale) < 1e-12:
                continue
            # Create pattern if needed
            if pname not in patterns_created:
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries("Linear", ts_tag)
                ops.pattern("Plain", ptag, ts_tag)
                patterns_created.add(pname)
            elem = elements.get(gl.frame_id)
            if elem is None or getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(gl.frame_id, "")
            if not sec_name:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
            if L < 1e-12:
                continue
            sw_per_len = getattr(sec, "A", 0.0) * mat.unit_weight
            fx = sw_per_len * L * gl.multiplier_x * scale * 0.5
            fy = sw_per_len * L * gl.multiplier_y * scale * 0.5
            fz = sw_per_len * L * gl.multiplier_z * scale * 0.5
            ops.load(ni.node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            ops.load(nj.node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            if pname not in self._gravity_load_totals:
                self._gravity_load_totals[pname] = {
                    "fx": 0.0,
                    "fy": 0.0,
                    "fz": 0.0,
                    "mx": 0.0,
                    "my": 0.0,
                    "mz": 0.0,
                }
            self._gravity_load_totals[pname]["fx"] += fx * 2
            self._gravity_load_totals[pname]["fy"] += fy * 2
            self._gravity_load_totals[pname]["fz"] += fz * 2
        # ── Area gravity loads (explicit multipliers) ────────────
        for agl in getattr(self.mesh_model, "area_gravity_loads", []):
            pname = agl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
            if abs(scale) < 1e-12:
                continue
            if pname not in patterns_created:
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries("Linear", ts_tag)
                ops.pattern("Plain", ptag, ts_tag)
                patterns_created.add(pname)
            area_elem = self.mesh_model.area_elements.get(agl.area_id)
            if area_elem is None:
                continue
            if getattr(area_elem, "inactive", False):
                # Parent was split/meshed — apply to all leaf descendants
                sub_ids = collect_descendants(agl.area_id, self.mesh_model.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = self.mesh_model.area_elements[sub_id]
                    sec_name = self.mesh_model.area_assignments.get(sub_id, "")
                    if not sec_name:
                        continue
                    sec = self.mesh_model.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = self.mesh_model.materials.get(sec.material)
                    if mat is None or abs(mat.unit_weight) < 1e-12:
                        continue
                    thickness = getattr(sub_elem, "thickness", 0.0) or 0.0
                    if thickness < 1e-12:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = self.mesh_model.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    sw_per_area = thickness * mat.unit_weight
                    tfx = sw_per_area * area_mag * agl.multiplier_x * scale
                    tfy = sw_per_area * area_mag * agl.multiplier_y * scale
                    tfz = sw_per_area * area_mag * agl.multiplier_z * scale
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        nd = self.mesh_model.nodes.get(nid)
                        if nd is not None:
                            ops.load(nd.node_tag, tfx / n_c, tfy / n_c, tfz / n_c, 0.0, 0.0, 0.0)
                continue
            # Active (unmeshed) area element
            sec_name = self.mesh_model.area_assignments.get(agl.area_id, "")
            if not sec_name:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(area_elem, "thickness", 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in area_elem.node_ids:
                nd = self.mesh_model.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            sw_per_area = thickness * mat.unit_weight
            tfx = sw_per_area * area_mag * agl.multiplier_x * scale
            tfy = sw_per_area * area_mag * agl.multiplier_y * scale
            tfz = sw_per_area * area_mag * agl.multiplier_z * scale
            n_c = len(area_elem.node_ids)
            for nid in area_elem.node_ids:
                nd = self.mesh_model.nodes.get(nid)
                if nd is not None:
                    ops.load(nd.node_tag, tfx / n_c, tfy / n_c, tfz / n_c, 0.0, 0.0, 0.0)

    @staticmethod
    def _select_diaphragm_master(tags):
        """Select the node tag nearest the centroid of the given tags.

        Reads each node's coordinate from the OpenSees domain once and
        caches the ``(x, y)`` values, then returns the tag whose cached
        position is closest to the centroid of all cached points.  Used to
        pick a diaphragm master for both the per-group and per-elevation
        paths.

        Args:
            tags: Sequence of OpenSees node tags in the diaphragm group.

        Returns:
            The node tag whose cached ``(x, y)`` is nearest the centroid.
        """
        coords = {t: tuple(ops.nodeCoord(t)[:2]) for t in tags}
        xs = [c[0] for c in coords.values()]
        ys = [c[1] for c in coords.values()]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        return min(
            tags,
            key=lambda t: (coords[t][0] - cx) ** 2 + (coords[t][1] - cy) ** 2,
        )

    def _apply_rigid_diaphragms(self) -> int:
        """Apply rigid diaphragm constraints at detected storey levels.

        Diaphragm definitions come from ``MeshModel``, which the Preprocessor
        populates from two sources:

        1. **S2K joint constraints** — Z-axis ``DIAPHRAGM`` constraints parsed
           from ``CONSTRAINT DEFINITIONS - DIAPHRAGM`` +
           ``JOINT CONSTRAINT ASSIGNMENTS`` (the canonical source for frame-only
           models with explicit diaphragm definitions).
        2. **Horizontal area elements** — fallback for models without explicit
           constraints.

        When explicit S2K constraints are present, the Preprocessor records
        them as ``mesh_model.diaphragm_components`` — one ``(mean_z, [node_id,
        ...])`` tuple per constraint.  This preserves the S2K constraint
        grouping so **independent diaphragms at the same elevation are not
        merged** (e.g. two building wings separated by a seismic gap).  The
        builder emits one ``rigidDiaphragm`` per group, picking the centroid
        node inside each group as its master.

        When no explicit constraints exist (area-only fallback), the builder
        falls back to per-elevation merging: all nodes near a detected ``z``
        are grouped into a single diaphragm.

        The ``rigid_diaphragms`` config is an optional tri-state override:

        * **absent** — apply constraints detected from the S2K file / area
          elements.  No config entry is required when the model declares its
          diaphragms.
        * ``False`` — explicitly **disable** all rigid diaphragms, even when
          levels are otherwise detected.
        * ``[z1, z2, ...]`` — override the detected levels with explicit
          ones.  When this list is given, per-group components are ignored
          and the per-elevation merge behaviour is used.
        """
        levels = self.mesh_model.diaphragm_levels
        config_val = self.config.get("rigid_diaphragms", None)
        if config_val is False:
            return 0  # explicit opt-out — skip even if levels were detected

        # Distinguish the two list forms:
        #   [z1, z2, ...]              → legacy explicit Z list (merge by elevation)
        #   [{name, nodes|selection}]  → explicit named groups (one
        #                                rigidDiaphragm per component, resolved by
        #                                the Preprocessor)
        if (
            isinstance(config_val, list)
            and config_val
            and any(isinstance(item, dict) for item in config_val)
            and not all(isinstance(item, dict) for item in config_val)
        ):
            raise ValueError(
                "rigid_diaphragms must be either an all-numeric legacy Z list "
                "([z1, z2, ...]) or an all-dict list of explicit named groups "
                "([{name, nodes/selection}, ...]) - mixed lists containing both "
                "dicts and non-dicts are not supported."
            )
        is_legacy_z_list = isinstance(config_val, list) and not (
            bool(config_val) and all(isinstance(item, dict) for item in config_val)
        )
        if is_legacy_z_list:
            levels = sorted(float(z) for z in config_val)
            existing_components = getattr(self.mesh_model, "diaphragm_components", [])
            if existing_components:
                logger.warning(
                    "rigid_diaphragms as a legacy [z1, z2, ...] list will merge %d "
                    "independent constraint group(s) into per-elevation diaphragms. "
                    "Use explicit group dicts ({name, nodes|selection}) to preserve "
                    "independent diaphragm identity at the same elevation.",
                    len(existing_components),
                )

        components = getattr(self.mesh_model, "diaphragm_components", [])
        # Per-group path is used whenever the Preprocessor recorded explicit
        # components (S2K constraint groups, explicit named groups, or forced
        # storey detection).  Only a legacy Z-list override forces the
        # per-elevation merge behaviour.
        use_groups = not is_legacy_z_list and bool(components)

        if not use_groups and not levels:
            return 0

        applied = 0

        # ── Per-group path: preserve S2K constraint identity ──────
        if use_groups:
            for _z, node_ids in components:
                tags = []
                for nid in node_ids:
                    nd = self.mesh_model.nodes.get(nid)
                    if nd is None:
                        continue
                    try:
                        ops.nodeCoord(nd.node_tag)
                        tags.append(nd.node_tag)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Node tag {nd.node_tag} (id={nid}) from diaphragm "
                            f"component at z={_z:.3f} does not exist in the "
                            f"OpenSees domain. The node may have been removed "
                            f"during preprocessing."
                        ) from exc
                if len(tags) < 2:
                    continue

                master = self._select_diaphragm_master(tags)
                slaves = [t for t in tags if t != master]
                try:
                    ops.rigidDiaphragm(3, master, *slaves)
                    applied += 1
                except Exception as exc:
                    logger.warning(
                        "rigidDiaphragm failed for group at z=%.3f (master=%d, %d slaves): %s",
                        _z,
                        master,
                        len(slaves),
                        exc,
                    )
                    continue
            return applied

        # ── Per-elevation fallback: merge all nodes near each level ──
        z_tol = float(getattr(self.mesh_model, "diaphragm_z_tolerance", 0.01))
        for z in levels:
            tags_at_z = []
            for nid, nd in self.mesh_model.nodes.items():
                if abs(nd.z - float(z)) > z_tol:
                    continue
                try:
                    ops.nodeCoord(nd.node_tag)
                    tags_at_z.append(nd.node_tag)
                except Exception:
                    continue
            if len(tags_at_z) < 2:
                continue

            master = self._select_diaphragm_master(tags_at_z)
            slaves = [t for t in tags_at_z if t != master]
            try:
                ops.rigidDiaphragm(3, master, *slaves)
                applied += 1
            except Exception as exc:
                logger.warning(
                    "rigidDiaphragm failed for elevation z=%.3f (master=%d, %d slaves): %s",
                    float(z),
                    master,
                    len(slaves),
                    exc,
                )
                continue
        return applied
