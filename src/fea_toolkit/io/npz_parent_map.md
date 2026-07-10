# Parent-Child Element Mapping in NPZ Results

## The problem

After meshing and splitting, the analysis model has *different elements*
than the original SAP2000 model:

```
Original SAP model          Analysis model (after split)
╔══════════════╗            ╔══════╤══════╤══════╗
║   Frame "1"  ║    ──→     ║ "1-0"│"1-1"│"1-2"║
║   (6 m)      ║            ║ 2m   │ 2m  │ 2m  ║
╚══════════════╝            ╚══════╧══════╧══════╝
  inactive=True               active, results here
```

The **Rhino importer** can create geometry for either:
- The **original** model (`create_meshed=False`) — objects have `SAP_FrameID = "1"`
- The **meshed** model (`create_meshed=True`) — objects have `SAP_FrameID = "1-0"`, `"1-1"`, etc.

The **NPZ results** always use the meshed element IDs (since that's what
was analysed).

## Arrays in the NPZ

| Array | Example | Meaning |
|---|---|---|
| `frame_sap_id` | `["1-0", "1-1", "1-2"]` | Element IDs that were analysed |
| `frame_parent_sap_id` | `["1", "1", "1"]` | Original SAP2000 ID of each child |
| `static/DEAD/fx_i` | `[12.3, 15.1, 11.8]` | Results ordered by the above |

Inactive parents (the original `"1"`) are **excluded** — they have no results.

## Helper functions in `npz_reader.py`

```python
from fea_toolkit.io.npz_reader import (
    read_results_npz,
    npz_build_child_map,
    npz_build_parent_map,
)

data = read_results_npz("results.npz")

# For each original frame, find its children
child_map = npz_build_child_map(data)
# {"1": ["1-0", "1-1", "1-2"], "2": ["2-0"], ...}

# For each child, find its parent
parent_map = npz_build_parent_map(data)
# {"1-0": "1", "1-1": "1", "2-0": "2", ...}
```

## Rhino colouring workflow

When colouring original (un-split) geometry from NPZ results:

```python
from fea_toolkit.io.npz_reader import (
    read_results_npz, npz_build_child_map
)

data = read_results_npz("results.npz")
child_map = npz_build_child_map(data)

# For a Rhino object with SAP_FrameID = "1":
parent_id = "1"
children = child_map.get(parent_id, [parent_id])

# Average the children's forces
fx_sum = 0.0
for cid in children:
    idx = list(data["frame_sap_id"]).index(cid)  # find index
    fx_sum += data["static/DEAD/fx_i"][idx]
fx_avg = fx_sum / len(children)
```

Or for simple cases where you just want the first child's value:

```python
idx = list(data["frame_sap_id"]).index(parent_id)  # fails if not in NPZ
```
