# Element splitting

The ``geometry.split_elements()`` function supports two independent
auto-mesh flags:

| ``AtJoints`` | ``AtFrames`` | Splits at |
|:----------:|:----------:|-----------|
| ``True`` | ``False`` | Existing joint nodes on the element |
| ``False`` | ``True`` | Frame-frame intersections (new nodes created) |
| ``True`` | ``True`` | Both |
| ``False`` | ``False`` | No splitting |

A third mode — **storey-level splitting** — is logically independent
and would use a separate config option.

---

## ``AtJoints`` — split at existing nodes

Elements are split wherever an existing node lies on the element line
segment.  A spatial grid finds intermediate nodes.  No new nodes are
created — only existing joint nodes are used.

## ``AtFrames`` — split at frame-frame intersections

Elements with ``AtFrames=True`` are tested pairwise for 3D line-segment
intersection.  A new ``Node`` (ID ``split_n_*``) is created at each
interior intersection point.  Both crossing elements share the same node.

**Pairing rule**: both elements must have ``AtFrames=True`` — if only
one does, the pair is skipped.

**Node reuse**: if an intersection coincides with an existing joint node
(within tolerance), that node is reused rather than duplicated.

**Limitations**:
- Only coplanar line segments can intersect in 3D — skew lines are
  correctly ignored.
- Elements sharing a node are skipped (already connected).
- Split is performed once (not recursively) — all intersection points
  collected before splitting.
- ``AtFrames`` does not trigger ``AtJoints``, and vice versa.
- Near-identical ``t`` values from different sources are merged.

## Builder integration

``Preprocessor.run()`` calls ``geometry.split_elements()``
and registers any new nodes in the OpenSees domain via ``ops.node()``.
Split children are stored in ``self.split_elements`` /
``self.split_assignments`` / ``self.split_dist_loads``.
