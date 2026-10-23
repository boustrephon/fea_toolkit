# FEMA 355C SAC Model Buildings — Source Schedules

Source: FEMA 355C "State of the Art Report on Connection Performance"
(SAC Joint Venture, September 2000), Appendix B. Transcribed for encoding into
`fea_toolkit.benchmarks`. Regions: Los Angeles (LA), Seattle (SE), Boston (BO).
Heights: 3-, 9-, 20-story. Designs: pre-Northridge, post-Northridge, plus the
Seattle RBS design and the three redesigned LA 9-story buildings (R1/R2/R3).

## 1. Geometry (Figure B-1 / B.1 text)

"Basic definitions kept constant between regions" — the same geometry and
loading apply to LA, Seattle and Boston.

| | 3-story | 9-story | 20-story |
|---|---|---|---|
| Storeys (above grade) | 3 @ 13 ft | 9 (12 ft basement, 18 ft ground, 8 x 13 ft) | 20 (2 x 12 ft basement, 18 ft ground, 19 x 13 ft) |
| Bays | 4 N-S (A-E) x 6 E-W (1-7) | 5 N-S (A-F) x 5 E-W (1-6) | 5 N-S (A-F) x 6 E-W |
| Bay size | 30 ft | 30 ft | 20 ft |
| Plan | 120 ft x 180 ft | 150 ft x 150 ft | 100 ft x 120 ft |
| Base | fixed | pinned basement + ground restraint | pinned basement + ground + 1st-basement restraint |
| Penthouse | C3-D5 | C3-E4 | C3-D5 |

- Drift limit h/400. Stiff soil (S2, UBC '94 / BOCA '93).
- Basement floors (-1, and -2 for the 20-story) have simple connections.
- Column splices are 6 ft above the floor centreline where two sections are given.
- Doubler plates are comma-separated: "exterior, interior" (3- and 9-story, and
  the LA/Seattle 20-story, which have two column tiers) or "exterior,
  next-to-exterior, interior" (Boston 20-story, which has three column tiers), in
  inches.  "0" = no doubler plate; "2 x 3/8" = two stacked 3/8 in plates (Boston
  post-Northridge, at the interior panel zone of 19/20; the Boston 3-story post
  has "2 x 1/2" at 1/2 and 2/3).  (The source tables use commas; an earlier
  transcription had rendered the first separator as a full stop.)

## 2. Loading (B.1)

- Steel framing: as designed (13 psf assumed for all designs).
- Floors/roof: 3 in metal deck + 2.5 in normal-weight concrete fill + fireproofing.
- Roofing 7 psf; ceilings/flooring 3 psf; M/E 7 psf (+40 psf over penthouse).
- Partitions: 10 psf (seismic) / 20 psf (gravity). Exterior wall 25 psf, 2 ft to envelope edge, 42 in parapet at main roof.
- Live load 50 psf (office). Wind: exposure B.

Floor load distribution (for weight):
- Floor dead (weight) 96 psf; floor dead (mass) 86 psf.
- Roof dead (excl. penthouse) 83 psf; penthouse dead 116 psf.
- Reduced live (floor + roof) 20 psf. Steel 13 psf.

## 3. Seismic mass (entire structure, kips-sec^2/ft; 1 kip-sec^2/ft = 14593.9 kg)

- 3-story: Roof 70.90; Floors 2-3 65.53.
- 9-story: Roof 73.10; Floors 3-9 67.86; Floor 2 69.04.
- 20-story: Roof 40.06; Floors 3-20 37.76; Floor 2 38.63.

## 4. Moment-frame layout (Figures B-2 / B-3)

All buildings use perimeter moment-resisting frames; the N-S and E-W frames are
"identical or very similar", so only the N-S frame is analysed. Gravity
beams/sub-beams run N-S; gravity frame columns are oriented strong-axis N-S.

- **3-story**: N-S moment frame 3 bays + a gravity bay; E-W moment frames at
  grids A2-A5 and E2-E5 (3 bays).
- **9-story LA/Boston**: moment frames on all four perimeter faces, each 5 bays
  = 4 full moment bays + the last beam (going clockwise) moment-connected at the
  inner end and **pinned at the corner** (4.5 bays) — avoids biaxial moment in
  the corner columns. Frames: A1-F1, F6-A6, A1-A6, F6-F1.
- **9-story Seattle**: same faces, but the last beam (clockwise) is **pinned at
  both ends** (4 bays) — the four faces' moment frames are independent.
- **20-story**: all perimeter connections moment-resisting. LA uses box corner
  columns (15x15); Boston/Seattle use W-shapes.
- Corner columns (e.g. F1, F6 in Boston 9): exterior moment columns, oriented
  about the axis of the frame they are **fixedly** connected to.

## 5. Table B-1 — LA beam/column sections + doubler plates

### PRE-NORTHRIDGE

**3-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1/2 | W14X257 | W14X311 | 0,0 | W33X118 | W14X82 | W14X68 | W18X35 |
| 2/3 | W14X257 | W14X311 | 0,0 | W30X116 | W14X82 | W14X68 | W18X35 |
| 3/Roof | W14X257 | W14X311 | 0,0 | W24X68 | W14X82 | W14X68 | W16X26 |

Note: Column line A strong axis; line F weak axis; lines B-E interior.

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W14X370 | W14X500 | 0,0 | W36X160 | W14X211 | W14X193 | W21X44 |
| 1/2 | W14X370 | W14X500 | 0,0 | W36X160 | W14X211 | W14X193 | W18X35 |
| 2/3 | W14X370, W14X370 | W14X500, W14X455 | 0,0 | W36X160 | W14X211, W14X159 | W14X193, W14X145 | W18X35 |
| 3/4 | W14X370 | W14X455 | 0,0 | W36X135 | W14X159 | W14X145 | W18X35 |
| 4/5 | W14X370, W14X283 | W14X455, W14X370 | 0,0 | W36X135 | W14X159, W14X120 | W14X145, W14X109 | W18X35 |
| 5/6 | W14X283 | W14X370 | 0,0 | W36X135 | W14X120 | W14X109 | W18X35 |
| 6/7 | W14X283, W14X257 | W14X370, W14X283 | 0,0 | W36X135 | W14X120, W14X90 | W14X109, W14X82 | W18X35 |
| 7/8 | W14X257 | W14X283 | 0,0 | W30X99 | W14X90 | W14X82 | W18X35 |
| 8/9 | W14X257, W14X233 | W14X283, W14X257 | 0,0 | W27X84 | W14X90, W14X61 | W14X82, W14X48 | W18X35 |
| 9/Roof | W14X233 | W14X257 | 0,0 | W24X68 | W14X61 | W14X48 | W16X26 |

**20-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS | BEAMS (40 ft span) | BEAMS (20 ft span) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -2/-1 | 15X15X2.00 | W24X335 | 0,0 | W14X22 | W14X550 | W21X50 | W14X22 |
| -1/1 | 15X15X2.00 | W24X335 | 0,0 | W30X99 | W14X550 | W24X68 | W16X26 |
| 1/2 | 15X15X2.00 | W24X335 | 0,0 | W30X99 | W14X550 | W21X50 | W14X22 |
| 2/3 | 15X15X2.00, 15X15X1.25 | W24X335, W24X335 | 0,0 | W30X99 | W14X550, W14X455 | W21X50 | W14X22 |
| 3/4 | 15X15X1.25 | W24X335 | 0,0 | W30X99 | W14X455 | W21X50 | W14X22 |
| 4/5 | 15X15X1.25 | W24X335 | 0,0 | W30X99 | W14X455 | W21X50 | W14X22 |
| 5/6 | 15X15X1.25, 15X15X1.00 | W24X335, W24X229 | 0,0 | W30X108 | W14X455, W14X370 | W21X50 | W14X22 |
| 6/7 | 15X15X1.00 | W24X229 | 0,0 | W30X108 | W14X370 | W21X50 | W14X22 |
| 7/8 | 15X15X1.00 | W24X229 | 0,0 | W30X108 | W14X370 | W21X50 | W14X22 |
| 8/9 | 15X15X1.00, 15X15X1.00 | W24X229, W24X229 | 0,0 | W30X108 | W14X370, W14X311 | W21X50 | W14X22 |
| 9/10 | 15X15X1.00 | W24X229 | 0,0 | W30X108 | W14X311 | W21X50 | W14X22 |
| 10/11 | 15X15X1.00 | W24X229 | 0,0 | W30X108 | W14X311 | W21X50 | W14X22 |
| 11/12 | 15X15X1.00, 15X15X1.00 | W24X229, W24X192 | 0,0 | W30X99 | W14X311, W14X257 | W21X50 | W14X22 |
| 12/13 | 15X15X1.00 | W24X192 | 0,0 | W30X99 | W14X257 | W21X50 | W14X22 |
| 13/14 | 15X15X1.00 | W24X192 | 0,0 | W30X99 | W14X257 | W21X50 | W14X22 |
| 14/15 | 15X15X1.00, 15X15X0.75 | W24X192, W24X131 | 0,5/8 | W30X99 | W14X257, W14X176 | W21X50 | W14X22 |
| 15/16 | 15X15X0.75 | W24X131 | 0,5/8 | W30X99 | W14X176 | W21X50 | W14X22 |
| 16/17 | 15X15X0.75 | W24X131 | 0,5/8 | W30X99 | W14X176 | W21X50 | W14X22 |
| 17/18 | 15X15X0.75, 15X15X0.75 | W24X131, W24X117 | 0,5/8 | W27X84 | W14X176, W14X108 | W21X50 | W14X22 |
| 18/19 | 15X15X0.75 | W24X117 | 0,5/8 | W27X84 | W14X108 | W21X50 | W14X22 |
| 19/20 | 15X15X0.75, 15X15X0.50 | W24X117, W24X84 | 0,0 | W24X62 | W14X108 | W21X50 | W14X22 |
| 20/Roof | 15X15X0.50 | W24X84 | 0,0 | W21X50 | W14X108, W14X43 | W21X44 | W12X16 |

Note: basement floor (-1 level) has simple connections.

### POST-NORTHRIDGE

**3-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1/2 | W14X257 | W14X311 | 0,0 | W30X116 | W14X82 | W14X68 | W16X26 |
| 2/3 | W14X257 | W14X311 | 0,0 | W30X116 | W14X82 | W14X68 | W16X26 |
| 3/Roof | W14X257 | W14X311 | 0,0 | W24X62 | W14X82 | W14X68 | W14X22 |

Note: Column line A strong axis; line F weak axis; lines B-E interior.

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W14X370 | W14X500 | 0,0 | W36X150 | W14X211 | W14X193 | W18X35 |
| 1/2 | W14X370 | W14X500 | 0,0 | W36X150 | W14X211 | W14X193 | W16X26 |
| 2/3 | W14X370, W14X370 | W14X500, W14X455 | 0,0 | W36X150 | W14X211, W14X159 | W14X193, W14X145 | W16X26 |
| 3/4 | W14X370 | W14X455 | 0,0 | W33X141 | W14X159 | W14X145 | W16X26 |
| 4/5 | W14X370, W14X283 | W14X455, W14X370 | 0,0 | W33X141 | W14X159, W14X120 | W14X145, W14X109 | W16X26 |
| 5/6 | W14X283 | W14X370 | 0,0 | W33X141 | W14X120 | W14X109 | W16X26 |
| 6/7 | W14X283, W14X257 | W14X370, W14X283 | 0,1/2 | W33X130 | W14X120, W14X90 | W14X109, W14X82 | W16X26 |
| 7/8 | W14X257 | W14X283 | 0,1/2 | W27X102 | W14X90 | W14X82 | W16X26 |
| 8/9 | W14X257, W14X233 | W14X283, W14X257 | 0,1/2 | W27X94 | W14X90, W14X61 | W14X82, W14X48 | W16X26 |
| 9/Roof | W14X233 | W14X257 | 0,0 | W24X62 | W14X61 | W14X48 | W14X22 |

**20-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS | BEAMS (40 ft span) | BEAMS (20 ft span) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -2/-1 | 15X15X2.00 | W24X335 | 0,0 | W14X22 | W14X550 | W18X40 | W14X22 |
| -1/1 | 15X15X2.00 | W24X335 | 0,0 | W30X99 | W14X550 | W24X55 | W14X22 |
| 1/2 | 15X15X2.00 | W24X335 | 0,0 | W30X99 | W14X550 | W18X40 | W14X22 |
| 2/3 | 15X15X2.00, 15X15X1.25 | W24X335, W24X335 | 0,0 | W30X99 | W14X550, W14X455 | W18X40 | W14X22 |
| 3/4 | 15X15X1.25 | W24X335 | 0,0 | W30X99 | W14X455 | W18X40 | W14X22 |
| 4/5 | 15X15X1.25 | W24X335 | 0,0 | W30X99 | W14X455 | W18X40 | W14X22 |
| 5/6 | 15X15X1.25, 15X15X1.00 | W24X335, W24X279 | 0,0 | W30X108 | W14X455, W14X370 | W18X40 | W14X22 |
| 6/7 | 15X15X1.00 | W24X279 | 0,0 | W30X108 | W14X370 | W18X40 | W14X22 |
| 7/8 | 15X15X1.00 | W24X279 | 0,0 | W30X108 | W14X370 | W18X40 | W14X22 |
| 8/9 | 15X15X1.00, 15X15X1.00 | W24X279, W24X279 | 0,0 | W30X108 | W14X370, W14X311 | W18X40 | W14X22 |
| 9/10 | 15X15X1.00 | W24X279 | 0,0 | W30X108 | W14X311 | W18X40 | W14X22 |
| 10/11 | 15X15X1.00 | W24X279 | 0,0 | W30X108 | W14X311 | W18X40 | W14X22 |
| 11/12 | 15X15X1.00, 15X15X1.00 | W24X279, W24X229 | 0,0 | W30X99 | W14X311, W14X257 | W18X40 | W14X22 |
| 12/13 | 15X15X1.00 | W24X229 | 0,0 | W30X99 | W14X257 | W18X40 | W14X22 |
| 13/14 | 15X15X1.00 | W24X229 | 0,0 | W30X99 | W14X257 | W18X40 | W14X22 |
| 14/15 | 15X15X1.00, 15X15X0.75 | W24X229, W24X162 | 0,5/8 | W30X99 | W14X257, W14X176 | W18X40 | W14X22 |
| 15/16 | 15X15X0.75 | W24X162 | 0,5/8 | W30X99 | W14X176 | W18X40 | W14X22 |
| 16/17 | 15X15X0.75 | W24X162 | 0,5/8 | W30X99 | W14X176 | W18X40 | W14X22 |
| 17/18 | 15X15X0.75, 15X15X0.75 | W24X162, W24X117 | 0,5/8 | W27X84 | W14X176, W14X108 | W18X40 | W14X22 |
| 18/19 | 15X15X0.75 | W24X117 | 0,5/8 | W27X84 | W14X108 | W18X40 | W14X22 |
| 19/20 | 15X15X0.75, 15X15X0.50 | W24X117, W24X94 | 0,1/2 | W24X62 | W14X108 | W18X40 | W14X22 |
| 20/Roof | 15X15X0.50 | W24X94 | 0,0 | W21X50 | W14X108, W14X43 | W18X35 | W12X14 |

Note: basement floor (-1 level) has simple connections.

> **Cross-check note (Notre Dame benchmark).** The Notre Dame "Benchmark Control
> Problems" MATLAB files (`struct9.m`, `struct20.m`, Ohtori/Christenson/Spencer
> 1999) encode the LA pre-Northridge models (matched here by area + strong-axis
> inertia against the AISC section table). `struct9.m` matches Table B-1 exactly —
> columns, girders, and the splice materials 12–16 (weighted `6/13`/`7/13` blends
> of the two sections listed at a splice floor). `struct20.m` matches the B-1
> columns exactly (15×15 box corners + W24 interior, splices 17–25), but its
> moment-frame girders differ in two places:
>
> | Level | struct20.m | FEMA 355C B-1 |
> | :--- | :--- | :--- |
> | basement (-2/-1) | W30X99 (pinned, elem type 3) | W14X22 (simple) |
> | 14/15 – 16/17 | W24X131 | W30X99 |
>
> The FEMA 355C B-1 (Phase 2) values are the ones encoded in `fema355c.py`.

## 6. Table B-2 — LA post-Northridge cover plates

Top plate: W1 x T1 (width x thickness), fixed 18 in length, tapers to 3 in wide.
Bottom plate: W2 x T2 (width x thickness), rectangular.

| Girder Section | Top Plate (W1 x T1) | Bottom Plate (W2 x T2) |
| :--- | :--- | :--- |
| W21X44 | 5" x 1/2" | 8" x 1/2" |
| W24X55 | 5" x 3/4" | 9" x 3/8" |
| W24X62 | 5" x 3/4" | 9" x 1/2" |
| W24X84 | 7" x 3/4" | 11" x 5/8" |
| W24X94 | 7" x 3/4" | 12" x 5/8" |
| W27X94 | 8" x 3/4" | 11" x 5/8" |
| W27X102 | 8-1/2" x 3/4" | 12" x 5/8" |
| W30X108 | 9" x 3/4" | 12" x 5/8" |
| W30X116 | 9" x 3/4" | 12" x 3/4" |
| W33X130 | 9" x 1" | 13" x 3/4" |
| W33X141 | 9" x 1" | 13" x 3/4" |
| W36X150 | 10" x 1" | 14" x 3/4" |

## 7. Table B-7 — Boston beam/column sections + doubler plates

### PRE-NORTHRIDGE

**3-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1/2 | W14X74 | W14X99 | 0,0 | W18X35 | 4-W12X65 & 2-W12X72 | W12X58 | W16X26 |
| 2/3 | W14X74 | W14X99 | 0,0 | W21X57 | 4-W12X65 & 2-W12X72 | W12X58 | W16X26 |
| 3/Roof | W14X74 | W14X99 | 0,0 | W21X62 | 4-W12X65 & 2-W12X72 | W12X58 | W14X22 |

Note 1: Column line A strong axis; line F weak axis (W14X61 changes to W14X68); lines B-E interior.

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W14X211 | W14X283 | 0,0 | W24X68 | 4-W14X145 & 2-W14X159 | W14X145 | W16X26 |
| 1/2 | W14X211 | W14X283 | 0,0 | W36X135 | 4-W14X145 & 2-W14X159 | W14X145 | W16X26 |
| 2/3 | W14X211, W14X159 | W14X283, W14X233 | 0,0 | W33X118 | see note 3 | W14X145, W12X120 | W16X26 |
| 3/4 | W14X159 | W14X233 | 0,0 | W30X116 | 4-W14X120 & 2-W14X132 | W12X120 | W16X26 |
| 4/5 | W14X159, W14X132 | W14X233, W14X211 | 0,0 | W30X116 | see note 4 | W12X120, W14X90 | W16X26 |
| 5/6 | W14X132 | W14X211 | 0,0 | W30X108 | 4-W14X99 & 2-W12X106 | W14X90 | W16X26 |
| 6/7 | W14X132, W14X99 | W14X211, W14X176 | 0,0 | W30X99 | see note 5 | W14X90, W12X65 | W16X26 |
| 7/8 | W14X99 | W14X176 | 0,0 | W27X94 | 6-W12X79 | W12X65 | W16X26 |
| 8/9 | W14X99, W14X61 | W14X176, W14X120 | 0,0 | W24X76 | see note 6 | W12X65, W8X48 | W16X26 |
| 9/Roof | W14X61 | W14X120 | 0,0 | W18X40 | 4-W12X53 & 2-W12X58 | W8X48 | W14X22 |

Note 2 (bay with only 1 MR connection, Floor 1 to Roof): W24X68, W27X94, W27X84, W27X84, W24X76, W24X76, W24X68, W24X62, W24X55, W18X40.

Gravity column splices (below penthouse): note 3: 4-W14X145→4-W14X120, 2-W14X159→2-W14X132; note 4: 4-W14X120→4-W14X99, 2-W14X132→2-W12X106; note 5: 4-W14X99→4-W12X79, 2-W12X106→2-W12X79; note 6: 4-W12X79→4-W12X53, 2-W12X79→2-W12X58.

**20-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Next to Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS (Gravity) | BEAMS (40 ft span) | BEAMS (20 ft span) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -2/-1 | W14X370 | W36X194 | W36X260 | 0,0 | W12X14 | 4-W14X311 & 2-W14X211 | W18X40 | W12X16 |
| -1/1 | W14X370 | W36X194 | W36X260 | 0,0 | W27X94 | 4-W14X311 & 2-W14X211 | W18X40 | W12X19 |
| 1/2 | W14X370, W14X370 | W36X194, W36X194 | W36X260, W36X260 | 0,0 | W36X182 | 4-W14X311 & 2-W14X211 (splice in story) | W18X40 | W12X19 |
| 2/3 | W14X370 | W36X194 | W36X260 | 0,0 | W36X160 | 4-W14X311 & 2-W14X211 | W18X40 | W12X19 |
| 3/4 | W14X370, W14X311 | W36X194, W36X182 | W36X260, W33X221 | 0,0 | W36X160 | 4-W14X311 to 4-W14X257 & 2-W14X211 to 2-W14X176 | W18X40 | W12X19 |
| 4/5 | W14X311 | W36X182 | W33X221 | 0,0 | W36X150 | 4-W14X257 & 2-W14X176 | W18X40 | W12X19 |
| 5/6 | W14X311, W14X283 | W36X182, W36X170 | W33X221, W33X201 | 0,0 | W36X150 | 4-W14X257 to 4-W14X233 & 2-W14X176 to 2-W14X159 | W18X40 | W12X19 |
| 6/7 | W14X283 | W36X170 | W33X201 | 0,0 | W36X150 | 4-W14X233 & 2-W14X159 | W18X40 | W12X19 |
| 7/8 | W14X283, W14X233 | W36X170, W36X160 | W33X201, W33X201 | 0,0 | W36X135 | 4-W14X233 to 4-W14X211 & 2-W14X159 to 2-W14X145 | W18X40 | W12X19 |
| 8/9 | W14X233 | W36X160 | W33X201 | 0,0 | W36X135 | 4-W14X211 & 2-W14X145 | W18X40 | W12X19 |
| 9/10 | W14X233, W14X193 | W36X160, W36X150 | W33X201, W30X173 | 0,0 | W33X130 | 4-W14X211 to 4-W14X176 & 2-W14X145 to 2-W14X120 | W18X40 | W12X19 |
| 10/11 | W14X193 | W36X150 | W30X173 | 0,0 | W33X130 | 4-W14X176 & 2-W14X120 | W18X40 | W12X19 |
| 11/12 | W14X193, W14X159 | W36X150, W36X135 | W30X173, W30X173 | 0,0 | W33X130 | 4-W14X176 to 4-W14X145 & 2-W14X120 to 2-W14X109 | W18X40 | W12X19 |
| 12/13 | W14X159 | W36X135 | W30X173 | 0,0 | W33X118 | 4-W14X145 & 2-W14X109 | W18X40 | W12X19 |
| 13/14 | W14X159, W14X132 | W36X135, W30X116 | W30X173, W27X161 | 0,0 | W33X118 | 4-W14X145 to 4-W14X120 & 2-W14X109 to 2-W14X90 | W18X40 | W12X19 |
| 14/15 | W14X132 | W30X116 | W27X161 | 0,0 | W30X116 | 4-W14X120 & 2-W14X90 | W18X40 | W12X19 |
| 15/16 | W14X132, W14X109 | W30X116, W30X99 | W27X161, W27X146 | 0,0 | W30X108 | 4-W14X120 to 4-W12X96 & 2-W14X90 to 2-W12X72 | W18X40 | W12X19 |
| 16/17 | W14X109 | W30X99 | W27X146 | 0,0 | W30X99 | 4-W12X96 & 2-W12X72 | W18X40 | W12X19 |
| 17/18 | W14X109, W14X82 | W30X99, W27X84 | W27X146, W24X131 | 0,0 | W27X94 | 4-W12X96 to 4-W12X72 & 2-W12X72 to 2-W10X60 | W18X40 | W12X19 |
| 18/19 | W14X82 | W27X84 | W24X131 | 0,0 | W27X84 | 4-W12X72 & 2-W10X60 | W18X40 | W12X19 |
| 19/20 | W14X82, W14X61 | W27X84, W24X68 | W24X131, W24X68 | 0,0 | W24X68 | 4-W12X72 to 4-W10X49 & 2-W10X60 to 2-W8X48 | W18X40 | W12X19 |
| 20/Roof | W14X61 | W24X68 | W24X68 | 0,0 | W18X35 | 4-W10X49 & 2-W8X48 | W18X40 | W14X22 |

Note: basement floor (-1 level) has simple connections.

### POST-NORTHRIDGE

**3-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1/2 | W14X82 | W14X145 | 3/8, 2 x 1/2 | W21X62 | 4-W12X65 & 2-W12X72 | W12X58 | W16X26 |
| 2/3 | W14X82 | W14X145 | 3/8, 2 x 1/2 | W21X62 | 4-W12X65 & 2-W12X72 | W12X58 | W16X26 |
| 3/Roof | W14X82 | W14X145 | 3/8,3/8 | W14X48 | 4-W12X65 & 2-W12X72 | W12X58 | W14X22 |

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W14X283 | W14X500 | 0,0 | W12X53 | 4-W14X145 & 2-W14X159 | W14X145 | W16X26 |
| 1/2 | W14X283 | W14X500 | 3/8,0 | W33X141 | 4-W14X145 & 2-W14X159 | W14X145 | W16X26 |
| 2/3 | W14X283, W14X257 | W14X500, W14X455 | 3/8,3/8 | W33X141 | see note 3 | W14X145, W12X120 | W16X26 |
| 3/4 | W14X257 | W14X455 | 0,0 | W21X101 | 4-W14X120 & 2-W14X132 | W12X120 | W16X26 |
| 4/5 | W14X257, W14X211 | W14X455, W14X398 | 3/8,0 | W21X101 | see note 4 | W12X120, W14X90 | W16X26 |
| 5/6 | W14X211 | W14X398 | 3/8,0 | W21X101 | 4-W14X99 & 2-W12X106 | W14X90 | W16X26 |
| 6/7 | W14X211, W14X159 | W14X398, W14X311 | 1/2,7/16 | W21X101 | see note 5 | W14X90, W12X65 | W16X26 |
| 7/8 | W14X159 | W14X311 | 7/16,3/8 | W18X97 | 6-W12X79 | W12X65 | W16X26 |
| 8/9 | W14X159, W14X109 | W14X311, W14X193 | 3/8,9/16 | W16X67 | see note 6 | W12X65, W8X48 | W16X26 |
| 9/Roof | W14X109 | W14X193 | 3/8,3/8 | W12X53 | 4-W12X53 & 2-W12X58 | W8X48 | W14X22 |

Note 2 (bay with only 1 MR connection, Floor 1 to Roof): W12X53, W16X67, W16X67, W16X67, W16X67, W14X61, W12X58, W12X58, W12X53, W12X53.

**20-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Next to Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS (Gravity) | BEAMS (40 ft span) | BEAMS (20 ft span) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -2/-1 | W14X455 | W36X393 | W36X485 | 0,0,0 | W12X14 | 4-W14X311 & 2-W14X211 | W18X40 | W12X16 |
| -1/1 | W14X455 | W36X393 | W36X485 | 0,0,0 | W16X67 | 4-W14X311 & 2-W14X211 | W18X40 | W12X19 |
| 1/2 | W14X455, W14X455 | W36X393, W36X393 | W36X485, W36X485 | 0,3/8,0 | W33X141 | 4-W14X311 & 2-W14X211 (splice in story) | W18X40 | W12X19 |
| 2/3 | W14X455 | W36X393 | W36X485 | 0,3/8,0 | W33X141 | 4-W14X311 & 2-W14X211 | W18X40 | W12X19 |
| 3/4 | W14X455, W14X370 | W36X393, W36X328 | W36X393 | 0,7/16,3/8 | W33X141 | 4-W14X311 to 4-W14X257 & 2-W14X211 to 2-W14X176 | W18X40 | W12X19 |
| 4/5 | W14X370 | W36X328 | W36X393 | 0,7/16,3/8 | W33X141 | 4-W14X257 & 2-W14X176 | W18X40 | W12X19 |
| 5/6 | W14X370, W14X342 | W36X328, W36X300 | W36X393, W36X359 | 0,5/8,3/8 | W33X141 | 4-W14X257 to 4-W14X233 & 2-W14X176 to 2-W14X159 | W18X40 | W12X19 |
| 6/7 | W14X342 | W36X300 | W36X359 | 0,1/2,3/8 | W24X131 | 4-W14X233 & 2-W14X159 | W18X40 | W12X19 |
| 7/8 | W14X342, W14X342 | W36X300, W36X300 | W36X359, W36X359 | 0,1/2,3/8 | W24X131 | 4-W14X233 to 4-W14X211 & 2-W14X159 to 2-W14X145 | W18X40 | W12X19 |
| 8/9 | W14X342 | W36X300 | W36X359 | 0,1/2,3/8 | W24X131 | 4-W14X211 & 2-W14X145 | W18X40 | W12X19 |
| 9/10 | W14X342, W14X311 | W36X300, W36X260 | W36X359, W36X300 | 0,11/16,1/2 | W24X131 | 4-W14X211 to 4-W14X176 & 2-W14X145 to 2-W14X120 | W18X40 | W12X19 |
| 10/11 | W14X311 | W36X260 | W36X300 | 0,11/16,1/2 | W24X131 | 4-W14X176 & 2-W14X120 | W18X40 | W12X19 |
| 11/12 | W14X311, W14X283 | W36X260, W36X260 | W36X300, W36X300 | 0,3/8,1/2 | W24X131 | 4-W14X176 to 4-W14X145 & 2-W14X120 to 2-W14X109 | W18X40 | W12X19 |
| 12/13 | W14X283 | W36X260 | W36X300 | 0,1/2,3/8 | W24X117 | 4-W14X145 & 2-W14X109 | W18X40 | W12X19 |
| 13/14 | W14X283, W14X283 | W36X260, W36X260 | W36X300, W36X280 | 0,1/2,7/16 | W24X117 | 4-W14X145 to 4-W14X120 & 2-W14X109 to 2-W14X90 | W18X40 | W12X19 |
| 14/15 | W14X283 | W36X260 | W36X280 | 0,3/8,3/8 | W24X104 | 4-W14X120 & 2-W14X90 | W18X40 | W12X19 |
| 15/16 | W14X283, W14X193 | W36X260, W36X182 | W36X280, W36X210 | 3/8,9/16,7/16 | W24X104 | 4-W14X120 to 4-W12X96 & 2-W14X90 to 2-W12X72 | W18X40 | W12X19 |
| 16/17 | W14X193 | W36X182 | W36X210 | 3/8,9/16,7/16 | W24X104 | 4-W12X96 & 2-W12X72 | W18X40 | W12X19 |
| 17/18 | W14X193, W14X159 | W36X182, W36X150 | W36X210, W36X150 | 9/16,11/16,11/16 | W21X101 | 4-W12X96 to 4-W12X72 & 2-W12X72 to 2-W10X60 | W18X40 | W12X19 |
| 18/19 | W14X159 | W36X150 | W36X150 | 3/8,1/2,1/2 | W18X86 | 4-W12X72 & 2-W10X60 | W18X40 | W12X19 |
| 19/20 | W14X159, W14X109 | W36X150, W24X117 | W36X150, W24X131 | 9/16,7/16, 2 x 3/8 | W18X76 | 4-W12X72 to 4-W10X49 & 2-W10X60 to 2-W8X48 | W18X40 | W12X19 |
| 20/Roof | W14X109 | W24X117 | W24X131 | 3/8,3/8,3/8 | W12X53 | 4-W10X49 & 2-W8X48 | W18X40 | W14X22 |

Note: basement floor (-1 level) has simple connections.

## 8. Table B-8 — Boston post-Northridge cover plates

Top plate: T x W1 x W2 x L (thickness x width-at-face x width-at-end x length).
Bottom plate: T x W x L (thickness x width x length).

**3-story**

| Girder Section | Top Plate (T x W1 x W2 x L) | Bottom Plate (T x W x L) |
| :--- | :--- | :--- |
| W21X62 | 3/4 x 7-3/4 x 2-1/2 x 15 | 5/8 x 9-1/2 x 15 |
| W14X48 | 5/8 x 6-7/8 x 6-7/8 x 13 | 1/2 x 9 x 13 |

**9-story**

| Girder Section | Top Plate (T x W1 x W2 x L) | Bottom Plate (T x W x L) |
| :--- | :--- | :--- |
| W33X141 | 1 x 11-1/2 x 5-1/2 x 20 | 1 x 13-1/2 x 20 |
| W21X101 | 13/16 x 12 x 6 x 14 | 11/16 x 14 x 14 |
| W18X97 | 7/8 x 11 x 5 x 12 | 3/4 x 13 x 12 |
| W16X67 | 11/16 x 10 x 4 x 11 | 9/16 x 11-1/2 x 11 |
| W12X58 | 11/16 x 9-1/2 x 3-1/2 x 9 | 9/16 x 11-1/2 x 9 |
| W12X53 | 11/16 x 9-1/2 x 3-1/2 x 9 (W2 shown as 3-1/3, likely typo) | 9/16 x 11-1/2 x 9 |

**20-story**

| Girder Section | Top Plate (T x W1 x W2 x L) | Bottom Plate (T x W x L) |
| :--- | :--- | :--- |
| W33X141 | 1-3/16 x 11-1/2 x 5-1/2 x 20 | 1-3/16 x 13-1/2 x 20 |
| W24X131 | 1-1/16 x 12-1/2 x 6-1/2 x 15 | 15/16 x 15 x 15 |
| W24X117 | 1 x 12-1/2 x 6-1/2 x 15 | 13/16 x 15 x 15 |
| W24X104 | 7/8 x 12-1/2 x 6-1/2 x 15 | 3/4 x 14-1/2 x 15 |
| W21X101 | 7/8 x 12 x 6 x 14 | 3/4 x 14 x 14 |
| W18X86 | 7/8 x 11 x 5 x 12 | 3/4 x 13 x 12 |
| W18X76 | 3/4 x 11 x 5 x 12 | 5/8 x 12-1/2 x 12 |
| W16X67 | 3/4 x 10 x 4 x 11 | 5/8 x 12 x 11 |
| W12X53 | 3/4 x 9-1/2 x 3-1/2 x 9 | 9/16 x 11-1/2 x 9 |

## 9. Table B-3 — Seattle beam/column sections + doubler plates

### PRE-NORTHRIDGE

**3-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1/2 | W14X159 | W14X176 | 0,1/4 | W24X76 | W10X77 | | W16X26 |
| 2/3 | W14X159 | W14X176 | 0,9/16 | W24X84 | W10X77, W10X60 | Same as below penthouse | W16X26 |
| 3/Roof | W14X159 | W14X176 | 0,0 | W18X40 | W10X60 | Same as below penthouse | W14X22 |

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W24X229 | Same as Ext. | 0,0 | W30X108 | W14X159 | | W16X31 |
| 1/2 | W24X229 | Same as Ext. | 0,0 | W30X108 | W14X159 | | W16X26 |
| 2/3 | W24X229, W24X229 | Same as Ext. | 0,1/4 | W30X116 | W14X159, W14X132 | | W16X26 |
| 3/4 | W24X229 | Same as Ext. | 0,0 | W30X108 | W14X132 | | W16X26 |
| 4/5 | W24X229, W24X207 | Same as Ext. | 0,0 | W27X94 | W14X132, W14X109 | | W16X26 |
| 5/6 | W24X207 | Same as Ext. | 0,0 | W27X94 | W14X109 | Same as below penthouse | W16X26 |
| 6/7 | W24X207, W24X162 | Same as Ext. | 0,1/4 | W24X76 | W14X109, W14X90 | Same as below penthouse | W16X26 |
| 7/8 | W24X162 | Same as Ext. | 0,1/4 | W24X76 | W14X90 | Same as below penthouse | W16X26 |
| 8/9 | W24X162, W24X131 | Same as Ext. | 0,1/4 | W24X62 | W14X90, W14X61 | Same as below penthouse | W16X26 |
| 9/Roof | W24X131 | Same as Ext. | 0,1/4 | W24X62 | W14X61 | Same as below penthouse | W14X22 |

Note: Column lines A-E strong axis; line F weak axis.

**20-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS (Gravity) | BEAMS (40 ft span) | BEAMS (20 ft span) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -2/-1 | W24X229 | Same as Ext. | 0,0 | W12X14 | W14X426 | W21X44 | W12X14 |
| -1/1 | W24X229 | Same as Ext. | 0,1/4 | W30X132 | W14X426 | W21X50 | W12X16 |
| 1/2 | W24X229, W24X229 | Same as Ext. | 0,1/4 | W30X132 | W14X426, W14X398 | W21X44 | W12X14 |
| 2/3 | W24X229 | Same as Ext. | 0,1/4 | W30X132 | W14X398 | W21X44 | W12X14 |
| 3/4 | W24X229, W24X229 | Same as Ext. | 0,1/4 | W30X132 | W14X398, W14X342 | W21X44 | W12X14 |
| 4/5 | W24X229 | Same as Ext. | 0,1/4 | W30X132 | W14X342 | W21X44 | W12X14 |
| 5/6 | W24X229, W24X192 | Same as Ext. | 0,1/2 | W30X132 | W14X342, W14X311 | W21X44 | W12X14 |
| 6/7 | W24X192 | Same as Ext. | 0,1/2 | W30X132 | W14X311 | W21X44 | W12X14 |
| 7/8 | W24X192, W24X192 | Same as Ext. | 0,1/2 | W30X132 | W14X311, W14X257 | W21X44 | W12X14 |
| 8/9 | W24X192 | Same as Ext. | 0,1/4 | W30X116 | W14X257 | W21X44 | W12X14 |
| 9/10 | W24X192, W24X192 | Same as Ext. | 0,1/4 | W30X116 | W14X257, W14X233 | W21X44 | W12X14 |
| 10/11 | W24X192 | Same as Ext. | 0,1/4 | W27X114 | W14X233 | W21X44 | W12X14 |
| 11/12 | W24X192, W24X192 | Same as Ext. | 0,1/4 | W27X114 | W14X233, W14X193 | W21X44 | W12X14 |
| 12/13 | W24X192 | Same as Ext. | 0,1/4 | W27X94 | W14X193 | W21X44 | W12X14 |
| 13/14 | W24X192, W24X162 | Same as Ext. | 0,1/4 | W27X94 | W14X193, W14X159 | W21X44 | W12X14 |
| 14/15 | W24X162 | Same as Ext. | 0,1/4 | W27X94 | W14X159 | W21X44 | W12X14 |
| 15/16 | W24X162, W24X162 | Same as Ext. | 0,1/4 | W27X94 | W14X159, W14X120 | W21X44 | W12X14 |
| 16/17 | W24X162 | Same as Ext. | 0,0 | W24X62 | W14X120 | W21X44 | W12X14 |
| 17/18 | W24X162, W24X131 | Same as Ext. | 0,0 | W24X62 | W14X120, W14X90 | W21X44 | W12X14 |
| 18/19 | W24X131 | Same as Ext. | 0,0 | W21X57 | W14X90 | W21X44 | W12X14 |
| 19/20 | W24X131, W24X131 | Same as Ext. | 0,0 | W21X57 | W14X90, W14X61 | W21X44 | W12X14 |
| 20/Roof | W24X131 | Same as Ext. | 0,0 | W21X57 | W14X61 | W18X35 | W12X14 |

Note: basement floor (-1 level) has simple connections.

### POST-NORTHRIDGE

**3-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1/2 | W14X342 | W14X398 | 0,3/8 | W33X141 | W10X77 | | W16X26 |
| 2/3 | W14X342, W14X159 | W14X398, W14X159 | 0,5/8 | W21X62 | W10X77, W10X60 | Same as below penthouse | W16X26 |
| 3/Roof | W14X159 | W14X159 | 0.7/8 | W21X62 | W10X60 | Same as below penthouse | W14X22 |

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W24X229 | Same as Ext. | 0,7/16 | W27X114 | W14X159 | | W16X31 |
| 1/2 | W24X229 | Same as Ext. | 0,7/16 | W27X114 | W14X159 | | W16X26 |
| 2/3 | W24X229, W24X229 | Same as Ext. | 0,7/16 | W27X114 | W14X159, W14X132 | | W16X26 |
| 3/4 | W24X229 | Same as Ext. | 0,3/8 | W27X94 | W14X132 | | W16X26 |
| 4/5 | W24X229, W24X207 | Same as Ext. | 0,3/8 | W27X94 | W14X132, W14X109 | | W16X26 |
| 5/6 | W24X207 | Same as Ext. | 0,3/8 | W27X94 | W14X109 | Same as below penthouse | W16X26 |
| 6/7 | W24X207, W24X162 | Same as Ext. | 0,3/8 | W24X76 | W14X109, W14X90 | Same as below penthouse | W16X26 |
| 7/8 | W24X162 | Same as Ext. | 0,3/8 | W24X76 | W14X90 | Same as below penthouse | W16X26 |
| 8/9 | W24X162, W24X131 | Same as Ext. | 0,3/8 | W21X62 | W14X90, W14X61 | Same as below penthouse | W16X26 |
| 9/Roof | W24X131 | Same as Ext. | 0,7/16 | W21X62 | W14X61 | Same as below penthouse | W14X22 |

Note: Column lines A-E strong axis; line F weak axis.

**20-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS (Gravity) | BEAMS (40 ft span) | BEAMS (20 ft span) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -2/-1 | W24X306 | Same as Ext. | 0,0 | W12X14 | W14X426 | W21X44 | W12X14 |
| -1/1 | W24X306 | Same as Ext. | 0,3/8 | W27X129 | W14X426 | W21X50 | W12X16 |
| 1/2 | W24X306, W24X306 | Same as Ext. | 0,3/8 | W27X129 | W14X426, W14X398 | W21X44 | W12X14 |
| 2/3 | W24X306 | Same as Ext. | 0,3/8 | W27X129 | W14X398 | W21X44 | W12X14 |
| 3/4 | W24X306, W24X306 | Same as Ext. | 0,3/8 | W27X129 | W14X398, W14X342 | W21X44 | W12X14 |
| 4/5 | W24X306 | Same as Ext. | 0,3/8 | W27X129 | W14X342 | W21X44 | W12X14 |
| 5/6 | W24X306, W24X279 | Same as Ext. | 0,3/8 | W27X129 | W14X342, W14X311 | W21X44 | W12X14 |
| 6/7 | W24X279 | Same as Ext. | 0,3/8 | W27X129 | W14X311 | W21X44 | W12X14 |
| 7/8 | W24X279, W24X279 | Same as Ext. | 0,3/8 | W27X129 | W14X311, W14X257 | W21X44 | W12X14 |
| 8/9 | W24X279 | Same as Ext. | 0,3/8 | W27X129 | W14X257 | W21X44 | W12X14 |
| 9/10 | W24X279, W24X279 | Same as Ext. | 0,3/8 | W24X103 | W14X257, W14X233 | W21X44 | W12X14 |
| 10/11 | W24X279 | Same as Ext. | 0,3/8 | W24X103 | W14X233 | W21X44 | W12X14 |
| 11/12 | W24X279, W24X229 | Same as Ext. | 0,3/8 | W24X103 | W14X233, W14X193 | W21X44 | W12X14 |
| 12/13 | W24X229 | Same as Ext. | 0,3/8 | W24X103 | W14X193 | W21X44 | W12X14 |
| 13/14 | W24X229, W24X192 | Same as Ext. | 0,3/8 | W24X84 | W14X193, W14X159 | W21X44 | W12X14 |
| 14/15 | W24X192 | Same as Ext. | 0,3/8 | W24X84 | W14X159 | W21X44 | W12X14 |
| 15/16 | W24X192, W24X162 | Same as Ext. | 0,1/2 | W24X84 | W14X159, W14X120 | W21X44 | W12X14 |
| 16/17 | W24X162 | Same as Ext. | 0,3/8 | W21X62 | W14X120 | W21X44 | W12X14 |
| 17/18 | W24X162, W24X131 | Same as Ext. | 0,3/8 | W21X62 | W14X120, W14X90 | W21X44 | W12X14 |
| 18/19 | W24X131 | Same as Ext. | 0,3/8 | W21X62 | W14X90 | W21X44 | W12X14 |
| 19/20 | W24X131, W24X131 | Same as Ext. | 0,3/8 | W21X62 | W14X90, W14X61 | W21X44 | W12X14 |
| 20/Roof | W24X131 | Same as Ext. | 0,7/16 | W21X62 | W14X61 | W18X35 | W12X14 |

Note: basement floor (-1 level) has simple connections.

### REDUCED BEAM SECTION (RBS) — POST-NORTHRIDGE

**9-story**

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | DOUBLER PLATES (in) | GIRDER | COLUMNS Below penthouse | COLUMNS Others | BEAMS |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| -1/1 | W24X176 | W24X229 | 0,1/4 | W30X108 | W14X159 | | W16X31 |
| 1/2 | W24X176 | W24X229 | 0,1/4 | W30X116 | W14X159 | | W16X26 |
| 2/3 | W24X176, W24X176 | W24X229, W24X229 | 0,1/4 | W30X116 | W14X159, W14X132 | | W16X26 |
| 3/4 | W24X176 | W24X229 | 0,0 | W30X108 | W14X132 | | W16X26 |
| 4/5 | W24X176, W24X176 | W24X229, W24X207 | 0,0 | W27X94 | W14X132, W14X109 | | W16X26 |
| 5/6 | W24X176 | W24X207 | 0,0 | W27X94 | W14X109 | Same as below penthouse | W16X26 |
| 6/7 | W24X176, W24X131 | W24X207, W24X162 | 0,1/4 | W24X84 | W14X109, W14X90 | Same as below penthouse | W16X26 |
| 7/8 | W24X131 | W24X162 | 0,0 | W24X76 | W14X90 | Same as below penthouse | W16X26 |
| 8/9 | W24X131, W24X94 | W24X162, W24X131 | 0,0 | W24X55 | W14X90, W14X61 | Same as below penthouse | W16X26 |
| 9/Roof | W24X94 | W24X131 | 0,0 | W21X44 | W14X61 | Same as below penthouse | W14X22 |

Note: Column lines A-E strong axis; line F weak axis; lines B, C, D interior.

## 10. Table B-4 — Seattle post-Northridge cover plates

Top plate: L x W1 x T1 (length x width-at-face x thickness); maintains W1 for
2-1/2 in, then tapers to 3-1/2 in. Bottom plate: L x W2 x T2 (rectangular).

| Girder Section | Top Plate (L x W1 x T1) | Bottom Plate (L x W2 x T2) |
| :--- | :--- | :--- |
| W21X62 | 11 x 8-1/4 x 3/4 | 11 x 10-1/4 x 5/8 |
| W24X76 | 12 x 9 x 3/4 | 12 x 11 x 5/8 |
| W24X84 | 12 x 9 x 7/8 | 12 x 11 x 3/4 |
| W24X103 | 12 x 9 x 1-1/8 | 12 x 11 x 1 |
| W27X94 | 14 x 10 x 7/8 | 14 x 12 x 3/4 |
| W27X129 | 14 x 10 x 1-1/4 | 14 x 12 x 1-1/8 |
| W33X141 | 17 x 11-1/2 x 1-1/8 | 17 x 13-1/2 x 1 |

## 11. Table B-5 — Seattle RBS cover plates

Same format as B-4, but the top plate tapers to 3 in (not 3-1/2 in).

| Girder Section | Top Plate (L x W1 x T1) | Bottom Plate (L x W2 x T2) |
| :--- | :--- | :--- |
| W21X44 | 10 x 6-1/2 x 3/8 | 10 x 8-1/2 x 1/4 |
| W24X55 | 11 x 7 x 3/8 | 11 x 9 x 5/16 |
| W24X76 | 11 x 9 x 5/16 | 11 x 11 x 1/4 |
| W24X84 | 11 x 9 x 3/8 | 11 x 11 x 5/16 |
| W27X94 | 13 x 10 x 3/8 | 13 x 12 x 5/16 |
| W30X108 | 14 x 10-1/2 x 7/16 | 14 x 12-1/2 x 3/8 |
| W30X116 | 14 x 10-1/2 x 7/16 | 14 x 12-1/2 x 3/8 |

## 12. Table B-6 — RBS cutout geometry (Seattle)

RBS starts at L - 0.5 x L(RBS) from the column face, extends L(RBS), minimum
width W(RBS) at midsection (50% max flange reduction).

| Girder Section | L x L(RBS) x W(RBS) |
| :--- | :--- |
| W21X44 | 18-1/2 x 15-1/2 x 3-1/4 |
| W24X55 | 21 x 18 x 3-1/2 |
| W24X76 | 21 x 18 x 4-1/2 |
| W24X84 | 21 x 18 x 5 |
| W27X94 | 24 x 20-1/4 x 5 |
| W30X108 | 26-1/2 x 22-1/2 x 5-1/4 |
| W30X116 | 26-1/2 x 22-1/2 x 5-1/4 |

## 13. Table B-9 — Redesigned LA 9-story (pre-Northridge)

No doubler plates required; fully UBC 1994 compliant. Splices 6 ft above floor
centreline where two sections are given.

**R1-LA9** — original configuration, 9 MR connections/frame/floor

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | GIRDER |
| :--- | :--- | :--- | :--- |
| -1/1 | W14X283 | W14X311 | W36X210 |
| 1/2 | W14X283 | W14X311 | W36X210 |
| 2/3 | W14X283, W14X211 | W14X311, W14X233 | W36X210 |
| 3/4 | W14X211 | W14X233 | W36X150 |
| 4/5 | W14X211, W14X193 | W14X233, W14X193 | W36X150 |
| 5/6 | W14X193 | W14X193 | W36X135 |
| 6/7 | W14X193, W14X145 | W14X193, W14X145 | W36X135 |
| 7/8 | W14X145 | W14X145 | W33X118 |
| 8/9 | W14X145, W14X90 | W14X145, W14X90 | W33X118 |
| 9/Roof | W14X90 | W14X90 | W24X68 |

**R2-LA9** — 15 ft bay width, 18 MR connections/frame/floor

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | GIRDER |
| :--- | :--- | :--- | :--- |
| -1/1 | W14X233 | Same as Exterior | W33X118 |
| 1/2 | W14X233 | Same as Exterior | W33X118 |
| 2/3 | W14X233, W14X145 | Same as Exterior | W30X99 |
| 3/4 | W14X145 | Same as Exterior | W27X94 |
| 4/5 | W14X145, W14X132 | Same as Exterior | W27X102 |
| 5/6 | W14X132 | Same as Exterior | W24X84 |
| 6/7 | W14X132, W14X109 | Same as Exterior | W27X84 |
| 7/8 | W14X109 | Same as Exterior | W24X62 |
| 8/9 | W14X109, W14X74 | Same as Exterior | W24X62 |
| 9/Roof | W14X74 | Same as Exterior | W18X35 |

**R3-LA9** — original configuration, 6 MR connections/frame/floor

| Story/Floor | COLUMNS Exterior | COLUMNS Interior | GIRDER |
| :--- | :--- | :--- | :--- |
| -1/1 | W14X342 | W14X426 | W36X256 |
| 1/2 | W14X342 | W14X426 | W36X256 |
| 2/3 | W14X342, W14X283 | W14X426, W14X283 | W36X210 |
| 3/4 | W14X283 | W14X283 | W36X210 |
| 4/5 | W14X283, W14X233 | W14X283, W14X257 | W36X182 |
| 5/6 | W14X233 | W14X257 | W36X182 |
| 6/7 | W14X233, W14X193 | W14X257, W14X211 | W33X141 |
| 7/8 | W14X193 | W14X211 | W33X130 |
| 8/9 | W14X193, W14X159 | W14X211, W14X159 | W30X116 |
| 9/Roof | W14X159 | W14X159 | W24X94 |

Note: R2 uses 15 ft bays (vs 30 ft for the others); R1/R3 keep the 5 x 5 @ 30 ft
grid. R2 has 18 moment connections (9 full moment bays), R1 has 9 (4.5 bays),
R3 has 6 (3 moment bays + 2 gravity bays).











