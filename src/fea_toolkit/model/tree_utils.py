"""Tree-traversal utilities for the frame-element split hierarchy.

Frame elements may be split multiple times::

    1. ``split_elements()`` — splits at joints → children ``"1-0"``, ``"1-1"``
    2. ``_split_frames_at_shell_subdiv()`` — splits at shell mesh nodes
       → children ``"1-1-0"``, ``"1-1-1"``

Each element stores only its **immediate** children (``child_ids``) and
parent (``parent_id``).  The functions below traverse this chain to
answer queries like "what are all the leaf elements of this root?" or
"what is the full path from root to this leaf?"
"""

from typing import Dict, List, Optional, Set

from .sap_data import FrameElement


def collect_descendants(
    elem_id: str,
    frame_elements: Dict[str, FrameElement],
    cache: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Return all **active leaf** descendants of a root frame element.

    Used when gathering results for an original beam/column that has
    been split into multiple sub-elements — e.g. to collect bending
    moments along the full length of a continuous beam and sum or
    envelope them for design.

    The function recurses through ``child_ids`` until it finds elements
    marked ``inactive=False`` (leaf elements that exist in the OpenSees
    domain).  A *cache* dict can be provided to avoid re-traversing the
    same subtree on repeated calls.

    Args:
        elem_id: Root element ID (e.g. ``"3"``, ``"1-0"``).
        frame_elements: ``{elem_id: FrameElement}`` from the MeshModel.
        cache: Optional dict for memoisation.  Each entry maps an
            element ID to its list of descendant leaf IDs.

    Returns:
        List of leaf element IDs under *elem_id*.

    Example::

        leaves = collect_descendants("3", mesh.frame_elements)
        # → ["3-0", "3-1-0", "3-1-1", "3-2"]
    """
    if cache is not None and elem_id in cache:
        return cache[elem_id]

    elem = frame_elements.get(elem_id)
    if elem is None:
        return []
    if not elem.inactive:
        result = [elem_id]
    else:
        result = []
        for cid in elem.child_ids:
            result.extend(collect_descendants(cid, frame_elements, cache))
    if cache is not None:
        cache[elem_id] = result
    return result


def get_root_parent(
    elem_id: str,
    frame_elements: Dict[str, FrameElement],
) -> Optional[str]:
    """Trace ``parent_id`` up the chain to find the ultimate root.

    The root is the original SAP2000 frame element that was split —
    it has ``parent_id=None`` or references an element not in the dict.

    Args:
        elem_id: Any element ID (leaf or intermediate).
        frame_elements: ``{elem_id: FrameElement}``.

    Returns:
        Root element ID, or *elem_id* itself if it has no parent.

    Example::

        root = get_root_parent("3-1-1", mesh.frame_elements)
        # → "3"
    """
    seen: Set[str] = set()
    current = elem_id
    while current in frame_elements:
        if current in seen:
            break  # circular reference guard
        seen.add(current)
        parent = frame_elements[current].parent_id
        if parent is None or parent not in frame_elements:
            return current
        current = parent
    return current


def get_element_chain(
    elem_id: str,
    frame_elements: Dict[str, FrameElement],
) -> List[str]:
    """Return the full chain from root to *elem_id* (inclusive).

    Useful for understanding the splitting history of an element
    and for labelling output (e.g. ``"3 → 3-1 → 3-1-1"``).

    Args:
        elem_id: Leaf or intermediate element ID.
        frame_elements: ``{elem_id: FrameElement}``.

    Returns:
        List ``[root_id, ..., parent_id, elem_id]``.

    Example::

        chain = get_element_chain("3-1-1", mesh.frame_elements)
        # → ["3", "3-1", "3-1-1"]
    """
    chain: List[str] = []
    seen: Set[str] = set()
    current = elem_id
    while current in frame_elements:
        if current in seen:
            break
        seen.add(current)
        chain.append(current)
        parent = frame_elements[current].parent_id
        if parent is None or parent not in frame_elements:
            break
        current = parent
    chain.reverse()
    return chain


def frame_split_summary(
    frame_elements: Dict[str, FrameElement],
) -> List[dict]:
    """Summarise the split hierarchy of all root frame elements.

    Returns a list of dicts, one per root element, with its ID,
    leaf count, and child IDs (immediate children only).

    Useful for diagnostics and for verifying that splitting
    produced the expected number of sub-elements.

    Args:
        frame_elements: ``{elem_id: FrameElement}``.

    Returns:
        List of ``{"root_id": str, "leaf_count": int,
                   "children": List[str]}``.

    Example::

        summary = frame_split_summary(mesh.frame_elements)
        for s in summary:
            print(f"{s['root_id']}: {s['leaf_count']} leaves")
    """
    # Find all roots (elements whose parent is not in the dict)
    all_ids = set(frame_elements.keys())
    root_ids: List[str] = []
    for eid, elem in frame_elements.items():
        if elem.parent_id is None or elem.parent_id not in all_ids:
            root_ids.append(eid)

    # Sort by a natural numeric key if possible for readable output
    def _sort_key(x: str) -> tuple:
        parts = x.split("-")
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (0, x)

    root_ids.sort(key=_sort_key)

    cache: Dict[str, List[str]] = {}
    result: List[dict] = []
    for rid in root_ids:
        leaves = collect_descendants(rid, frame_elements, cache)
        elem = frame_elements[rid]
        result.append({
            "root_id": rid,
            "leaf_count": len(leaves),
            "children": list(elem.child_ids),
        })
    return result
