# Xara/OpenSeesRT Gravity & Solver Lessons Learned

## Model Profile

**Admin Building** (0.7E): 8-storey RC frame with brick infill walls (no structural shear walls).

| Metric | Value |
|--------|-------|
| Nodes | 1,139 (after splitting) |
| Frame elements | 881 |
| Area elements | 1,332 (all loads-only for Tcl path) |
| Total mass | 5,866 t |
| Eigensystem | 6,834 equations |
| Fundamental period (X) | 0.37 s (Mode 2) |
| Fundamental period (Y) | 0.46 s (Mode 1) |

## Run History

| # | Element | Geom Transf | Solver | IPs | Gravity? | Shells? | Lateral | Result |
|---|---------|-------------|--------|-----|----------|---------|---------|--------|
| 1 | forceBeamColumn | PDelta | BandGeneral | 5 | N/A | Yes | modal (Mode 1) | 10 min timeout |
| 2 | forceBeamColumn | Linear | BandGeneral | 5 | N/A | Yes | modal (Mode 1) | 10 min timeout |
| 3 | forceBeamColumn | Linear | BandGeneral | 5 | No | Yes | modal (Mode 1) | 1800s timeout |
| 4 | dispBeamColumn | Linear | UmfPack | 5 | No | Yes | modal (Mode 1) | killed at 6 min |
| 5 | HingeRadau | Linear | UmfPack | 5 | No | Yes | modal (Mode 1) | killed at 9 min |
| 6 | dispBeamColumn | Linear | UmfPack | 5 | No | Yes | modal (Mode 1) | killed at 9 min |
| 7 | dispBeamColumn | Linear | UmfPack | 3 | No | Yes | modal (Mode 1) | 1800s timeout |
| 8 | dispBeamColumn | Linear | UmfPack | 3 | Yes | No | modal (Mode 1) | killed |
| 9 | dispBeamColumn | Linear | UmfPack | 3 | **Yes** | **No** | **triangular** | **(running)** |

**Note:** Runs 1–8 all used `modal_to_lateral_loads()` which had two bugs:
- **Wrong mode**: always picked `shapes.get(0)` = Mode 1 (Y-direction, 53.43% Y mass), even for X-direction pushover. The X-direction needs Mode 2 (54.88% X mass).
- **No real masses**: `getattr(nd, "mass", 1.0)` always returned 1.0 because `MeshModel` nodes are `Node` dataclass instances with no mass field. No actual mass weighting was applied.

Neither bug caused the timeouts (displacement-controlled pushover scales the reference load), but they produced wrong Fx distributions. Run #9 is the first with corrected triangular loads using actual seismic masses.

## Critical Discovery: No Gravity in the Tcl

`mesh_model_to_gravity_loads()` was returning an **empty dict** for the Admin Building. The function only iterated over `mesh_model.frame_gravity_loads` — but this model's DEAD loads are applied via **area** elements (wall self-weight, slab uniform loads), not frame gravity loads. Result: `pushover_tcl()` checks `if gravity_loads:` and skips Step A entirely.

The generated Tcl went straight from material/section definitions into Step B (lateral pushover) with no gravity phase at all. The `gravity_reaction.out` recorder was never created.

### Fix in `mesh_model_to_gravity_loads()` (v2)

The function now computes gravity from three sources:
1. **Frame self-weight** — `frame_gravity_loads` (self-weight multipliers on frame elements)
2. **Area self-weight** — `area_gravity_loads` (self-weight multipliers on area elements), using polygon area × thickness × material density
3. **Joint loads** — `joint_loads` (concentrated forces)

Supports a `pattern_combination` parameter, e.g. `{"DEAD": 1.0, "LIVE": 0.25}`.

## Critical Discovery: Wrong Modal Loads

### Bug 1: Wrong Mode Selected

`modal_to_lateral_loads()` always did `shapes.get(0, shapes.get(1, {}))` — index 0, which is Mode 1 (T=0.46s, 53.43% Y mass). For an X-direction pushover this should pick **Mode 2** (T=0.37s, 54.88% X mass).

**Fix:** `_find_dominant_mode()` now uses `numpy.argmax()` on the direction-specific mass participation ratios (`partiMassRatiosMX`, etc.).

### Bug 2: Nodal Masses Always 1.0

The old code did `getattr(nd, "mass", 1.0) or 1.0` — always 1.0 because `MeshModel` nodes are `Node` dataclass instances (`node_id`, `node_tag`, `x`, `y`, `z` — no mass field). The actual seismic masses are computed by `AnalysisBuilder.compute_seismic_masses()` from the Mass Source.

**Fix:** `compute_lateral_loads()` now accepts an optional `nodal_masses` dict. The admin script passes `nodal_masses=masses` from `b.compute_seismic_masses()` converted to `{node_tag: mass}`.

### Three Lateral Load Patterns Added

All per FEMA 356:

| Pattern | Formula | Use |
|---------|---------|-----|
| `uniform` | `F_i ∝ m_i` | Mass-proportional |
| `triangular` | `F_i ∝ m_i × z^k` | Inverted triangle (k=1.0 default) |
| `modal` | `F_i ∝ m_i × φ_{i,mode}` | Dominant mode in push direction |

Default changed from `"modal"` to `"triangular"` — the FEMA 356 standard for regular buildings.

## RC Fiber vs Elastic

All attempts with fiber sections (`create_fiber_sections: True`) timed out with 881 fiber beam-columns — even with `export_shells: False`. The primary bottleneck is likely the combination of fiber-section convergence + 881 elements.

## Key Recommendations

### For the Xara Tcl Path

1. **`export_shells: False`** — All area elements (walls, slabs) are loads-only in the Tcl path. They contribute mass and gravity loads but have no stiffness. This removes 1,332 shell elements from the equation system.
2. **Gravity must come from `pattern_combination={"DEAD": 1.0}`** — Not the default auto-detect. The area self-weight must be explicitly included.
3. **Use `verify_tcl_gravity_loads()`** — Always verify the Tcl has gravity loads before running.
4. **Gravity reaction recorder** — New `gravity_reaction.out` file records per-step Z-reactions during the gravity phase. Check this to verify the gravity analysis converged and the total reaction matches the applied loads.
5. **Use `triangular_lateral_loads()`** — Or `uniform_lateral_loads()` — and pass `nodal_masses` from `compute_seismic_masses()`. Avoid the old `modal_to_lateral_loads()`.
6. **Validate the control node** — Ensure the pushover control node is connected to at least one frame element.

### For Convergence

- RC fiber with 881 elements × `dispBeamColumn` × 3 IPs is still too heavy for 30-minute runs
- Consider `forceBeamColumn` + 3 IPs as an alternative (more stable convergence)
- Consider elastic-only pushover (`create_fiber_sections: False`) to validate the pipeline
- The test model (`sample_model.s2k` with ~50 frames) is a good first validation target

## Tcl Gravity Verification Helper

`verify_tcl_gravity_loads(tcl_path, expected_total_z=None)` parses a generated Tcl file and:
- Finds `pattern Plain 1` blocks (gravity load pattern)
- Sums all Z-direction loads
- Reports the number of loaded nodes
- Optionally compares against an expected total (within 1 %)
## 2026-07-26: Elastic Pipeline Validation (Run #11)

**MeshModel validation run** — elastic pushover via Tcl/Xara, reusing cached MeshModel.

| Metric | Value |
|--------|-------|
| Elements | 881 elasticBeamColumn |
| Shells | Omitted (export_shells=False) |
| Nodes | 656 (filtered from 1,139 — frame-only) |
| Gravity pattern | DEAD=1.0, Self weight=1.0, LL=0.25 |
| Lateral pattern | Uniform (mass-proportional) |
| Solver | Newton (fixed-step, adaptive=False) |
| Gravity | ✅ Locked — Rz = 49,986 KN (5,095 t) |
| Pushover | ✅ 9/10 steps to 51.2 mm |
| Base shear | Rx = −1,370 KN |
| Direction | ✅ dof=1 (Ux) matches lateral lateral loads in X |

### Bug #1: Shell-only orphan nodes cause gravity divergence
- `export_mesh_model_to_tcl()` created ALL 1,139 nodes
- 500 nodes had gravity loads but no element connection (shells omitted)
- Solver diverged instantly: Norm=4.96e64
- **Fix:** `recorder.py` now scans for nodes referenced by exported elements (frames + optional shells + restraints) and skips orphan nodes.

### Bug #2: Recorders placed after analysis block
- `pushover_tcl()` emitted `recorder Node ... wall_disp.out ...` commands AFTER the DisplacementControl while-loop
- Recorder files were created empty (0 bytes)
- **Fix:** moved recorder commands to BEFORE the analysis block.

### Bug #3: Duplicate recorder block
- The old code had two copies of the three recorder commands — one before the analysis (now removed) and one after the results section.
- **Fix:** removed the second copy.

### Key lesson: frame-only node filtering for gravity/lateral loads
When `export_shells=False`, the Tcl exporter must filter gravity and lateral load entries to only include nodes that are actually exported (frame-connected nodes). This filtering is now applied centrally in the exporter rather than relying on external validation scripts. The exporter derives the set of exported nodes from the frame element connectivity, then rejects any load entry whose node is not in that set, while preserving load emission for all valid exported nodes.

## External Guidance on RC Pushover Convergence

### From OpenSees examples (Berkeley wiki)
1. **Example 7 – 3D RC Frame**: "The reinforced-concrete model using the nonlinearBeamColumn element has difficulties converging at very large lateral deformations. A second model uses BeamWithHinges element, which is able to achieve convergence at such high lateral-drift levels."
2. **Example 4.1**: Uses `test NormDispIncr 1.0e-8 10 0` with `algorithm Newton` and a fallback to `Newton -initial` with `4000` iterations. The initial stiffness fallback is critical for fiber convergence.
3. **Krylov-Newton Algorithm**: Default `maxDim=3` means the tangent is reformed every 3 iterations. For large models (6,834 DOF → 6,000+ active), increasing `maxDim` to 10–20 improves convergence.

### Key recommendations adopted for the adaptive pushover chain:
- `test NormDispIncr 1.0e-3 200 0` → validated tolerance for Xara Tcl pushover loops (1.0e-5 recommended for gravity solver stage only)
- `KrylovNewton` fallback with increased iterations (500)
- `ModifiedNewton -initial` fallback (initial stiffness is the most robust)
- Step-size halving when all algorithms fail
