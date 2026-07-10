# Constraint Edge Detection

Detects "tears" in the final meshed model where adjacent area elements have
incompatible meshes along a shared geometric edge — one chain has intermediate
mesh nodes that another chain doesn't share. These need line/edge constraints
in OpenSees.

## Algorithm: Sorted-Tuple Edge Registry + Sweep-Line Chain Following

### Data Structure

The core data structure is a **sorted-tuple edge registry**:

```
{(nA, nB): [elem_id, ...]}    # nA ≤ nB by position (X → Y → Z)
```

Key properties:
- `(nA, nB)` is always sorted by node position — **not by node ID**.
  This means the *same geometric edge* always produces the *identical key*,
  regardless of which element direction created it.
- Direction is **implicit**: from nA → nB is the positive direction.
  Following a chain from a current node means computing the direction from
  that node to the far end of the key on the fly.
- Compatible edges (2+ elements sharing the exact same key) are identified
  automatically — the key is identical.

### Why sorted by position?

Using `frozenset` for edge keys gives unordered node pairs, which means:
- Each time you iterate, the edge direction is non-deterministic.
- Chain-following needs consistent direction; frozenset requires a separate
  directed-edge list with stored direction vectors.

A position-sorted tuple solves both problems: deterministic keys, implicit
direction, and no separate data structures.

### Preparation

1. **Build sorted-tuple edge registry** — For every active area element,
   register each consecutive node pair as a position-sorted tuple:
   `(n_small, n_large) = sort_by_position(n_i, n_{i+1})`,
   `edge_reg[(n_small, n_large)].append(elem_id)`.
   Non-structural element types (e.g. brick wall load panels) are excluded.

2. **Build node→keys index** — Derived directly from the registry:
   `node_keys[nA].append((nA, nB))` and same for nB. This is an index,
   not a separate data model — the registry keys are the source of truth.

### Horizontal Pass (by Z-band) — Single Pass

All edges in typical building models are at constant Z (within the slab
plane). A single Z-band sweep suffices — there are no true vertical
(Z-changing) tears. The sweep operates on registry keys, not edge dicts.

3. **Group by Z-band** — Partition near-horizontal edges (Z-span < 0.2 m)
   by their average Z. Each Z-band is processed independently.

4. **Sort** — Within each Z-band, sort keys by `min(key[0].x, key[1].x)`
   then `min(key[0].y, key[1].y)`.

5. **Sweep — detect tear starts** — Maintain an active list of keys whose
   X-range covers the sweep position. When a new key `(nA, nB)` enters:
   - Check **both** nA and nB against all active keys for shared-node matches.
   - If an active key shares a node (single node, not both), compute the
     direction from that shared node through each key.
   - If directions match (positive cosine > 0.9999), follow both chains.
   - **Skip identical keys** (same sorted tuple) — these are adjacent elements
     sharing an identical geometric edge with matching mesh, not a tear.

6. **Follow a chain** — From the shared node and a registry key, compute
   direction from the shared node to the key's far end. At each subsequent
   node, scan `node_keys` for keys whose direction matches the *accumulated*
   chain direction (from start node to latest node, recomputed at each step).
   Stop when no continuation is found, or when the chain reaches a node that
   another chain has already reached.

7. **Junction truncation** — After following both chains fully, find the
   *first common node* after the start node. Truncate both chains at that
   node. If no common node exists, this is not a tear (chains diverge without
   rejoining). If a junction exists, collect all unique nodes from both
   truncated chains. Sort by t-parameter along the shared direction.
   If ≥3 nodes, register as a constraint.

8. **Advance sweep** — Remove keys whose far-end X is behind the sweep
   position (retirement by X-range). Continue until all keys in the Z-band
   are processed.

### Post-processing

9. **Merge overlapping tears** — Group tears that share ≥1 node and are
   colinear (absolute cosine of endpoint vectors > 0.9999). Merge all nodes
   in the group, sort by t-parameter, and produce one continuous constraint.
   This reunites tears that were split by truncation at their junction.
   The merge **preserves type information** — the set of all element types
   involved is collected and the two most common are reported as ``type_a``
   and ``type_b`` in the output.

10. **Output** — One constraint per merged tear: ``(node_ids, type_a, type_b)``.

### Solver Compatibility

The ``equalDOF`` constraints produced from these tears are appropriate for:

- **Linear static analysis** ✅
- **Non-linear static pushover** ✅ (constraint enforced at each converged step)
- **Large displacement / P-Delta** ✅ (constraints on global DOFs)
- **Modal / response spectrum** ✅

**Constraint handler**: Use ``constraints Penalty αS αM`` or
``constraints Transformation``. The Transformation method warns against nodes
appearing in multiple constraint relationships — merging colinear overlapping
tears reduces the number of constraint relationships, which is beneficial.

**Shell element DOFs**: ``ShellMITC4`` has 6 DOFs per node (UX, UY, UZ, RX, RY, RZ).
Constraining all 6 DOFs along incompatible edges is correct for both
translational and rotational continuity.

### Key Properties

- **Sorted-tuple keys** make edges deterministic and direction implicit.
- **Single data structure** — no separate edge dicts or frozenset registry.
- **Accumulated chain direction** — robust against slight edge-angle variation.
- **Junction truncation + merge** — clean short tears that recombine naturally.
- **Type tracking** — each merged tear carries the set of element types involved.
- **O(n log n)** sweep complexity.
