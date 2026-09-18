# Structural FEA Model Review Checklist (ETABS & SAP2000)

The model-review checklist is published as **per-program, per-jurisdiction
editions**, generated from a single tagged source so that shared content cannot
drift.

## Editions

| Document | Program | Jurisdiction | Codes referenced |
|---|---|---|---|
| [`ETABS_checklist_is.md`](ETABS_checklist_is.md) | ETABS | India | IS 456:2000, IS 1893 (Part 1): 2016, IS 875 (Parts 1-5), IS 800:2007, IS 1786:2008, IS 13920:2016, IS 16700:2017 |
| [`ETABS_checklist_gb.md`](ETABS_checklist_gb.md) | ETABS | Mainland China | GB 50009-2012, GB 50010-2010 (2015), GB 50011-2010 (2016), GB 50017-2017, GB 50068-2018, GB 50007-2011, GB 50223-2008, GB 18306-2015, JGJ 3-2010, GB/T 1499.2-2018 |
| [`ETABS_checklist_hk.md`](ETABS_checklist_hk.md) | ETABS | Hong Kong | CoP for Structural Use of Concrete 2013, CoP for Structural Use of Steel 2011, CoP for Dead and Imposed Loads 2011, CoP on Wind Effects in Hong Kong 2019 |
| [`SAP2000_checklist_gb.md`](SAP2000_checklist_gb.md) | SAP2000 | Mainland China | as above (GB / JGJ) |
| [`SAP2000_checklist_hk.md`](SAP2000_checklist_hk.md) | SAP2000 | Hong Kong | as above (Hong Kong Codes of Practice) |

Every edition carries the same *Disclaimer and legal notices* section (see
below) and the same eight-phase workflow; only the code references and the
program-specific menu paths and tables differ.

## Disclaimer and legal notices

> **Not a design authority.** This checklist is a quality-assurance aid. It is
> not a substitute for the applicable design code or for the judgement of a
> suitably qualified engineer. **The engineer using it retains full
> professional responsibility for the analysis and for the decisions taken on
> the basis of its results.**

> **Verify every reference.** Code clauses, tables and numerical limits quoted
> in the guidance notes are indicative only and may be revised or superseded.
> Confirm each reference against the current official edition of the relevant
> standard before relying on it.

> **Trademarks.** **ETABS®, SAP2000® and CSi® are registered trademarks of
> Computers and Structures, Inc. (CSI).** These documents are independent and
> are not affiliated with, authorised by, sponsored by or endorsed by CSI;
> CSI software and its documentation are copyrighted, so they cite or link to
> CSI material rather than reproducing manual text, tables or figures.

## Source of truth

`ETABS_checklist_src.md` is the single source. Every checklist row carries two
trailing tags:

- **`Jurisdiction`** - `general`, `IS`, `GB` or `HK`.
- **`Program`** - `general`, `etabs` or `sap2000`.

A row is emitted into an edition when **both** its tags are `general` or match
that edition's target. Where a provision differs, the source holds one row per
variant sharing the same `Ref`, so numbering stays aligned across editions.
Tables without a tag column (the phase summary and the key-resources summary)
pass through unchanged.

## Build

```bash
python docs/checklist/build.py          # regenerate the editions
python docs/checklist/build.py --check  # CI: fail if any edition is stale
```

Editing the generated `ETABS_checklist_*` / `SAP2000_checklist_*` files by hand
is a mistake - edit `ETABS_checklist_src.md` and rebuild. The QA tests in
`tests/test_etabs_checklist.py` enforce the regenerate-and-diff contract and the
per-jurisdiction and per-program isolation.
