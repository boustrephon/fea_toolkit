# Ground-motion mirror — public records

This directory holds ground-motion waveforms that are **redistributable**
(public domain, or otherwise licensed for redistribution).  They are committed
to the repository and always available for CI.

The non-redistributable mirror (PEER NGA-West2, FEMA P-695, …) lives in the
sibling `local/` directory, which is kept out of the repository.
`scripts/fetch_ground_motions.py` routes each record by its manifest
`redistributable` flag:

- `redistributable: true` → `data/ground_motion/public/`
- `redistributable: false` → `data/ground_motion/local/`

See `docs/ground_motion_data.md`.
