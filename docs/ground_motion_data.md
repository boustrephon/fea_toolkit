---
title: "Ground-Motion Data & Benchmark Sources"
description: "Ground-motion strategy for fea_toolkit validation: canonical units, the source catalogue (PEER NGA, FEMA P-695, K-NET/KiK-net, ESM, AFAD/TADAS, BHRC, CSMNC), PEER NGA access limitations, the GroundMotionManifest dataclass, the fetch workflow, and the CI strategy."
status: "partial"
tags: [ground-motion, validation, benchmark, nonlinear-dynamic, response-spectrum, licensing, ci]
category: [planning]
related: [benchmark_register.md, nonlinear_dynamic_analysis.md]
---

# Ground-Motion Data & Benchmark Sources

## 1. Canonical units

All record functions in `fea_toolkit.io.ground_motion` use the canonical SI
system — acceleration in **m/s²**.  PEER `.AT2` files store acceleration in g
and are converted at read time (`read_peer_record`).  Analysis-domain scaling
(if a model uses mm/s² or other units) is applied by the caller.

## 2. Source catalogue

| Region | Source | Access | Notes |
|---|---|---|---|
| USA (public domain) | FEMA P-695 far-field (USGS / ATC-63) | Public domain | Committed CI baseline |
| USA | PEER NGA-West2 (ngawest2.berkeley.edu) | Registration; see §3 | Shallow-crustal, active regimes |
| Japan | K-NET / KiK-net (NIED, kyoshin.bosai.go.jp) | Free for research; registration | ~1,700 stations; KiK-net downhole + surface |
| EU / Med / Middle-East | ESM v3.0 (ORFEUS–INGV), DOI 10.13127/esm.3 | Open access | FDSN + Obspy; includes Turkey + Greece |
| Turkey | AFAD / TADAS | Open | Also surfaced through ESM |
| Iran | BHRC | Free with acknowledgment | National strong-motion network |
| China | CSMNC (CEA) | Free with acknowledgment | National network |

## 3. PEER NGA limitations

Verified against the current PEER ground-motion database pages:

1. **Restricted registration** — only university or corporate email addresses;
   personal accounts are not accepted.
2. **One-year membership, renewed annually** — as of 2 July 2026 all accounts
   were disabled; users register for a one-year membership that expires.
3. **Gated registration windows** — sign-up is only open Mon–Fri, 8 AM–8 PM PST.
4. **Download caps "due to copyright issues"** — ~200 records per two weeks,
   400 per month; excessive downloads attract further restrictions.
5. **No redistribution** — records are for the downloader's own use under the
   user agreement and may not be re-hosted.  This is the operative constraint
   for a public repository.
6. **As-recorded / unrotated / unscaled** — rotation, baseline correction and
   scaling are the user's responsibility (the site's scale factor is metadata).
7. **"As is", no warranty** — PEER/UC Berkeley and contributors disclaim
   liability for data accuracy.

**Consequence**: NGA waveforms are never committed; they are fetched into a
local mirror by the user under their own account.

## 4. GroundMotionManifest

`fea_toolkit.io.GroundMotionManifest` is the committed, repository-safe
representation of a record: identity (record id, provider, event, station,
component), where to fetch it (`source_url`), its licence, and processing
(scale factor, rotation).  It round-trips through `to_dict()` / `from_dict()`
for a JSON manifest registry, and carries no waveform data.

## 5. Fetch workflow

- **Manifests** (small, JSON) are committed — one per record, with provenance.
- **`scripts/`** holds the fetch/ingest helpers (e.g.
  `scripts/fetch_ground_motions.py`) that read a manifest list and pull the
  waveforms into the local **`data/`** mirror, applying the user's own
  credentials where the provider requires registration.
- **Waveforms** live in `data/` and are never stored in the repository.

## 6. Committed vs fetched

- **Committed**: a small **5-record public-domain subset** (FEMA P-695
  far-field) under `tests/fixtures/` for fast, network-free CI.
- **Fetched**: the full NGA / K-NET / ESM / AFAD / BHRC / CSMNC sets, pulled
  locally when deeper multi-record validation is wanted.

## 7. CI strategy

The existing CI runs `pytest -q -m "not slow"` on Python 3.10/3.12.  Ground
motion and benchmarks slot in as two classes of test:

- **Always-on**: pure-computation anchors (Euler, Przemieniecki/Paz, V&E) and
  NLTHA/RS runs on the committed 5-record subset — no network, no external
  files, fast.
- **Opt-in**: multi-record validation gated on the presence of the fetched
  `data/` mirror, via a `needs_ground_motion` marker (same pattern as
  `needs_pyvista` in `tests/conftest.py`).  These skip cleanly when records are
  absent, so CI stays green and a developer who has fetched records locally
  gets the deep validation.  A separate `workflow_dispatch` job can run the
  heavy suite on demand.

## 8. Processing pipeline

`read_peer_record` / `read_time_history_csv` → `baseline_correct` →
`scale_to_pga` or `scale_to_target_sa` → rotation (fault-normal/parallel) →
analysis.  Summary metrics via `record_summary` (PGA, PGV, Arias intensity,
CAV).
