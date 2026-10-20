# SAC 3-story benchmark — N-S perimeter moment frame, one frame (half mass).
# Transcribed from Ohtori et al. (2004), "Benchmark Control Problems for
# Seismically Excited Nonlinear Buildings", ASCE J. Eng. Mech. 130(4):366,
# DOI 10.1061/(ASCE)0733-9399(2004)130:4(366), Figure 1 / benchmark data.
#
#   - 4 bays x 9.144 m (30 ft), 3 stories x 3.9624 m (13 ft), fixed bases.
#   - Columns (345 MPa): exterior lines W14x257, interior lines W14x311, plus a
#     leaning column at the far line (models the interior gravity framing for
#     P-delta: small flexural inertia, real axial stiffness).
#   - Beams (248 MPa), per the benchmark schedule (struct3.m); the floor system
#     is composite for gravity design, but the lateral model uses bare-steel
#     beam inertia and rigid axial (EA = 1e10) for the floor diaphragm:
#       level 1  bays 1-3 W33x118, bay 4 W21x44
#       level 2  bays 1-3 W30x116, bay 4 W21x44
#       roof     bays 1-3 W24x68,  bay 4 W21x44
#   - E = 2.0e11 Pa (29000 ksi).
#   - Seismic mass per level, one frame (half the whole building):
#       L1 = L2 = 4.7835e5 kg,  L3 (roof) = 5.1779e5 kg.
#   SI units: N, m, kg.
#
# Mass is lumped at the floor nodes by tributary bay width (edge nodes carry a
# half bay, interior nodes a full bay).

wipe
model BasicBuilder -ndm 2 -ndf 3

set E 2.0e11

# Column sections (A, I from struct3.m EA / EI).
set Acol_ext 0.048759
set Icol_ext 1.41475e-3
set Acol_int 0.05895
set Icol_int 1.8017e-3
set Acol_lean 0.012899
set Icol_lean 5.035e-5

# Beam sections — rigid axial (EA = 1e10 -> A = 0.05), real flexural inertia.
set Abeam 0.05
set Ibeam33 2.455e-3
set Ibeam30 2.0514e-3
set Ibeam24 7.6145e-4
set Ibeam21 3.50775e-4

# Nodes: 5 column lines x 4 levels.
#   x = 0, 9.144, 18.288, 27.432, 36.576 m
#   z = 0, 3.9624, 7.9248, 11.8872 m
node 1 0.0 0.0
node 2 9.144 0.0
node 3 18.288 0.0
node 4 27.432 0.0
node 5 36.576 0.0
node 6 0.0 3.9624
node 7 9.144 3.9624
node 8 18.288 3.9624
node 9 27.432 3.9624
node 10 36.576 3.9624
node 11 0.0 7.9248
node 12 9.144 7.9248
node 13 18.288 7.9248
node 14 27.432 7.9248
node 15 36.576 7.9248
node 16 0.0 11.8872
node 17 9.144 11.8872
node 18 18.288 11.8872
node 19 27.432 11.8872
node 20 36.576 11.8872

# Column bases fixed.
fix 1 1 1 1
fix 2 1 1 1
fix 3 1 1 1
fix 4 1 1 1
fix 5 1 1 1

geomTransf Linear 1

# Columns — line 1 (W14x257), lines 2-3 (W14x311), line 4 (W14x257), line 5 (lean).
# ground -> L1.
element elasticBeamColumn 1 1 6 $Acol_ext $E $Icol_ext 1
element elasticBeamColumn 2 2 7 $Acol_int $E $Icol_int 1
element elasticBeamColumn 3 3 8 $Acol_int $E $Icol_int 1
element elasticBeamColumn 4 4 9 $Acol_ext $E $Icol_ext 1
element elasticBeamColumn 5 5 10 $Acol_lean $E $Icol_lean 1
# Columns — L1 -> L2.
element elasticBeamColumn 6 6 11 $Acol_ext $E $Icol_ext 1
element elasticBeamColumn 7 7 12 $Acol_int $E $Icol_int 1
element elasticBeamColumn 8 8 13 $Acol_int $E $Icol_int 1
element elasticBeamColumn 9 9 14 $Acol_ext $E $Icol_ext 1
element elasticBeamColumn 10 10 15 $Acol_lean $E $Icol_lean 1
# Columns — L2 -> L3.
element elasticBeamColumn 11 11 16 $Acol_ext $E $Icol_ext 1
element elasticBeamColumn 12 12 17 $Acol_int $E $Icol_int 1
element elasticBeamColumn 13 13 18 $Acol_int $E $Icol_int 1
element elasticBeamColumn 14 14 19 $Acol_ext $E $Icol_ext 1
element elasticBeamColumn 15 15 20 $Acol_lean $E $Icol_lean 1

# Beams (W33x118 bays 1-3, W21x44 bay 4) — L1.
element elasticBeamColumn 16 6 7 $Abeam $E $Ibeam33 1
element elasticBeamColumn 17 7 8 $Abeam $E $Ibeam33 1
element elasticBeamColumn 18 8 9 $Abeam $E $Ibeam33 1
element elasticBeamColumn 19 9 10 $Abeam $E $Ibeam21 1
# Beams (W30x116 bays 1-3, W21x44 bay 4) — L2.
element elasticBeamColumn 20 11 12 $Abeam $E $Ibeam30 1
element elasticBeamColumn 21 12 13 $Abeam $E $Ibeam30 1
element elasticBeamColumn 22 13 14 $Abeam $E $Ibeam30 1
element elasticBeamColumn 23 14 15 $Abeam $E $Ibeam21 1
# Beams (W24x68 bays 1-3, W21x44 bay 4) — L3 (roof).
element elasticBeamColumn 24 16 17 $Abeam $E $Ibeam24 1
element elasticBeamColumn 25 17 18 $Abeam $E $Ibeam24 1
element elasticBeamColumn 26 18 19 $Abeam $E $Ibeam24 1
element elasticBeamColumn 27 19 20 $Abeam $E $Ibeam21 1

# Seismic masses (kg), tributary-weighted across the 5 nodes per level.
# L1 and L2: total 4.7835e5 kg -> edge 5.979375e4 kg, interior 1.195875e5 kg.
mass 6 5.979375e4 5.979375e4 0.0
mass 7 1.195875e5 1.195875e5 0.0
mass 8 1.195875e5 1.195875e5 0.0
mass 9 1.195875e5 1.195875e5 0.0
mass 10 5.979375e4 5.979375e4 0.0
mass 11 5.979375e4 5.979375e4 0.0
mass 12 1.195875e5 1.195875e5 0.0
mass 13 1.195875e5 1.195875e5 0.0
mass 14 1.195875e5 1.195875e5 0.0
mass 15 5.979375e4 5.979375e4 0.0
# L3 (roof): total 5.1779e5 kg -> edge 6.472375e4 kg, interior 1.294475e5 kg.
mass 16 6.472375e4 6.472375e4 0.0
mass 17 1.294475e5 1.294475e5 0.0
mass 18 1.294475e5 1.294475e5 0.0
mass 19 1.294475e5 1.294475e5 0.0
mass 20 6.472375e4 6.472375e4 0.0
