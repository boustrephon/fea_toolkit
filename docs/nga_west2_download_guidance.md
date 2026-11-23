---
title: "NGA-West2 Waveform Download — Guidance"
description: "How to fetch the 7 PEER NGA-West2-only IK-2005 LMSR-N records (and, later, the full 40-record set) into data/ground_motion/local/: the login flow, component selection by PGA, the download quota, credentials, and the fallback paths."
status: "draft"
tags: [ground-motion, benchmark, validation, nga-west2, download, ik-2005]
category: [planning]
related: [ground_motion_data.md, ik2005_generic_frame_benchmark.md, _pending_work.md]
---

# NGA-West2 waveform download — guidance

## 0. Next session — runbook (8 AM–8 PM PST only)

NGA-West2 is only reachable **Mon–Fri, 8 AM–8 PM PST**.  Within that window:

```bash
cd /Users/andrew/Projects/fea_toolkit
source /Users/andrew/Projects/OpenSeesPy/venv_opensees/bin/activate   # selenium is already installed here

export NGA_W2_USER="<your PEER email>"
export NGA_W2_PASS="<your PEER password>"

# 1. See the 7 records and their order (the --rsns list must match this order):
python scripts/fetch_peer_records.py --list

# 2. On https://ngawest2.berkeley.edu/members/sign_in, search each record by
#    EVENT + STATION (not the old hand-guessed RSNs) and note its RSN, then:
python scripts/fetch_peer_records.py --download --rsns <n1>,<n2>,...,<n7> --no-headless

# 3. Confirm all 40 records are now present (expect "40 records"):
python -c "from fea_toolkit.benchmarks.ik2005_records import fetched_ids; print(len(fetched_ids()), 'records')"

# 4. Re-run the 40-record collapse benchmark:
python scripts/run_ik2005_collapse.py --code 0909
```

The download lands in `data/ground_motion/downloads/`; the selected horizontals
are copied to `data/ground_motion/local/<record_id>.AT2` (gitignored).  If the
`--no-headless` login shows a CAPTCHA, complete it manually in that browser.

## 1. Purpose

The 40-record LMSR-N set (IK-2005 Table 4.2, Medina 2002) drives the IK-2005
generic-frame collapse benchmark and its MK-2003 modal anchors.  33 of the 40
are already fetched from open archives (CESMD + USGS NSMP); **7 exist only in
PEER NGA-West2** and need an interactive download under a PEER account.

This document is the corrected, repo-specific guidance for that download.  It
replaces the earlier generic "ObsPy + Selenium" proposal with what this project
actually needs — the records are *left as raw `.AT2`* and read by the existing
`read_peer_record` reader (no ObsPy), and the horizontal component is chosen by
matching the paper's PGA (no guess).

## 2. The 7 records (search by event + station, not RSN)

| record_id | event | station | Table 4.2 PGA (g) |
|---|---|---|---|
| `LP89agw` | Loma Prieta 1989 | Agnews State Hospital | 0.172 |
| `LP89sjw` | Loma Prieta 1989 | Salinas - John & Work | 0.112 |
| `NR94lv6` | Northridge 1994 | Leona Valley #6 | 0.178 |
| `NR94pic` | Northridge 1994 | LA - Pico & Sentous | 0.186 |
| `SH87bra` | Superstition Hills 1987 | Brawley | 0.156 |
| `SH87icc` | Superstition Hills 1987 | El Centro Imp. Co. Cent | 0.358 |
| `SH87pls` | Superstition Hills 1987 | Plaster City | 0.186 |

The historical hand-guessed RSNs are **unreliable** (see
`ik2005_generic_frame_benchmark.md` — e.g. RSN 1045 is Newhall, not a Northridge
CSMIP station).  Look the correct RSN up on the site by **event + station**
(which are unambiguous), one per record.  `scripts/fetch_peer_records.py --list`
prints this table in the order its `--rsns` argument expects.

## 3. What already exists (do not reinvent)

- `fea_toolkit.io.ground_motion.read_peer_record` reads `.AT2` natively,
  returning `(times, accel)` in **m/s²** (g → m/s² at read).  No ObsPy.
- `fea_toolkit.io.ground_motion.select_component_by_pga` picks the component
  whose PGA (in g) is closest to a target — the component-selection rule.
- `scripts/fetch_peer_records.py` holds `PEER_RECORDS` (id, event, station,
  target PGA), the vertical-component filter, RSN grouping, and the
  select-and-rename step; its Selenium downloader needs only the
  `[nga-download]` extra.
- `benchmarks/ik2005_records.PEER_ONLY` is the runner's source of truth; once
  the 7 files land as `<record_id>.AT2`, `fetched_ids()` returns 40 with no code
  change.

## 4. Running it

```bash
# install the optional Selenium extra once
pip install -e ".[nga-download]"

# credentials from the environment, never committed
export NGA_W2_USER="you@university.edu"
export NGA_W2_PASS="…"

python scripts/fetch_peer_records.py --list                          # the 7 records
python scripts/fetch_peer_records.py --download --rsns 763,779,...   # login + download
python scripts/fetch_peer_records.py --download --rsns … --no-headless  # visible browser
python scripts/fetch_peer_records.py --select <dir> --rsns 763,779,...  # select from a dir you downloaded manually
```

`--download` logs in, enters the RSNs, clicks **Download**, accepts the two
confirmation dialogs, waits for the ZIP (`PEERNGARecords_Unscaled*.zip`) to stop
growing, extracts it, and copies the PGA-matching horizontal of each record to
`data/ground_motion/local/<record_id>.AT2`.

## 5. The Selenium flow (verified against the reference tools)

The login/download flow follows `NGA_W2_RSNs2TH` (the reference the original
guidance named) and `PyPeerNGA`:

1. Sign in at `https://ngawest2.berkeley.edu/members/sign_in` — fill
   `#user_email`, `#user_password`, click `#user_submit`.
2. Open `https://ngawest2.berkeley.edu/spectras/new?sourceDb_flag=1` and accept
   the terms (`#buttons > button`) if shown.
3. Enter comma-separated RSNs into `#search_search_nga_number`, then submit
   (`button[onclick*='OnSubmit']`).
4. Scroll down and click the download button
   (`button[onclick*='getSelectedResult']`), then accept the two `alert` dialogs.
5. Wait until the download directory stops growing (size-stable polling, 5 × 6 s),
   then extract the `PEERNGARecords_Unscaled*.zip` and keep only the `.AT2` files
   (`RSN####_EVENT_STATION<comp>.AT2`).

These locators come from the 2020–2021 reference tools and **should be re-checked
against the live site before first use** — the site may have changed, and headless
mode can fail silently on human verification.  On any element-not-found error the
script tells you to retry with `--no-headless` and complete verification manually.

## 6. Component selection + rename

Each RSN downloads **both horizontals** (and sometimes a vertical, which the
script skips).  The script loads each horizontal with `read_peer_record`, computes
its PGA in g, and keeps the one closest to Table 4.2's value.  That file is copied
to `data/ground_motion/local/<record_id>.AT2`.  Matching PGA (not "any horizontal")
is essential: the two horizontals can differ by ~70 % in `Sa(T1)`.

## 7. Quota, credentials, non-redistribution

- PEER caps downloads at ~**200 records / 2 weeks, 400 / month**.  The 7 records
  (≈ 14 files) are well inside; the script still refuses a single run over 50
  RSNs and logs the per-run count.
- Credentials live in `NGA_W2_USER` / `NGA_W2_PASS`; never hardcode or commit them.
- `data/ground_motion/local/` is gitignored — NGA-West2 records are
  non-redistributable and must **never** be committed (`ground_motion_data.md` §3).

## 8. Fallbacks

- **Human verification** — if headless fails, run `--no-headless` and complete any
  CAPTCHA/verification manually; the script still does the rest.
- **Manual download + `--select`** — download the ZIP yourself on the site and run
  `--select <unzipped-dir> --rsns …` to do the component selection and rename
  (fully functional with no Selenium).

## 9. Staged follow-up (S2 — full 40-record provenance)

After the 7 land, a provenance upgrade to NGA-West2 originals for **all 40**
records is optional but removes the CESMD-vs-PEER processing caveat.  It needs:

1. a **validated 40-record RSN/station mapping** (search by event + station),
2. a decision on the **14 USGS multi-component records**: `_load_record` appends
   the channel suffix (`<record_id>_<channel>`), so a single `<record_id>.AT2` is
   ignored for those — either store them as `<record_id>_<channel>.AT2` or drop
   them from `USGS_CHANNELS` and pass `channel=None`.

See P51 in `docs/_pending_work.md`.