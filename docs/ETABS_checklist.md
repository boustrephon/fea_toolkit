# ETABS / Structural FEA Model Review Checklist

The model-review checklist is published in three jurisdiction-specific editions,
generated from a single tagged source so the shared content cannot drift.

## Editions

| Document | Jurisdiction | Codes referenced |
|---|---|---|
| [`ETABS_checklist_is.md`](ETABS_checklist_is.md) | India | IS 456:2000, IS 1893 (Part 1): 2016, IS 875 (Parts 1-5), IS 800:2007, IS 1786:2008, IS 13920:2016, IS 16700:2017 |
| [`ETABS_checklist_gb.md`](ETABS_checklist_gb.md) | Mainland China | GB 50009-2012, GB 50010-2010 (2015), GB 50011-2010 (2016), GB 50017-2017, GB 50068-2018, GB 50007-2011, GB 50223-2008, GB 18306-2015, JGJ 3-2010, GB/T 1499.2-2018 |
| [`ETABS_checklist_hk.md`](ETABS_checklist_hk.md) | Hong Kong | CoP for Structural Use of Concrete 2013, CoP for Structural Use of Steel 2011, CoP for Dead and Imposed Loads 2011, CoP on Wind Effects in Hong Kong 2019 |

## Source of truth

`ETABS_checklist_src.md` is the single source. Every checklist row carries a
trailing `Jurisdiction` tag:

- `general` - code-agnostic; emitted into every edition.
- `IS` / `GB` / `HK` - emitted only into that edition. Where a provision differs
  between jurisdictions, the source holds one row per jurisdiction sharing the
  same `Ref`, so numbering stays aligned across editions.

Tables without a `Jurisdiction` column (for example the phase summary and the
key-resources summary) pass through unchanged.

## Build

```bash
python docs/checklist/build.py          # regenerate the editions
python docs/checklist/build.py --check  # CI: fail if the editions are stale
```

Editing the generated `ETABS_checklist_is/gb/hk.md` files by hand is a mistake -
edit `ETABS_checklist_src.md` and rebuild. The QA tests in
`tests/test_etabs_checklist.py` enforce the regenerate-and-diff contract and the
per-edition code isolation.
