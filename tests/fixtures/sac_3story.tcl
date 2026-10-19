# SAC 3-story benchmark — N-S perimeter moment frame, one frame (half mass).
# Transcribed from Ohtori et al. (2004), "Benchmark Control Problems for
# Seismically Excited Nonlinear Buildings", ASCE J. Eng. Mech. 130(4):366,
# DOI 10.1061/(ASCE)0733-9399(2004)130:4(366), Figure 1 / benchmark data.
#
#   - 4 bays x 9.144 m (30 ft), 3 stories x 3.9624 m (13 ft), fixed bases.
#   - Columns W24x68 (A = 1.290e-2 m2, Ix = 7.617e-4 m4), 345 MPa steel.
#   - Beams   W30x116 (A = 2.206e-2 m2, Ix = 2.052e-3 m4), 248 MPa steel,
#     modelled bare (no composite slab).  Ohtori's published frequencies are
#     for the *composite* beam model, whose effective inertia is ~50-60 %
#     higher, so this centreline model overestimates the periods by ~8 %.
#   - E = 2.0e11 Pa (29000 ksi).
#   - Seismic mass per level, one frame (half the whole building):
#       L1 = L2 = 4.785e5 kg,  L3 (roof) = 5.20e5 kg.
#   SI units: N, m, kg.
#
# Mass is lumped at the floor nodes by tributary bay width (edge nodes carry a
# half bay, interior nodes a full bay).

wipe
model BasicBuilder -ndm 2 -ndf 3

set E 2.0e11
set Acol 1.290e-2
set Icol 7.617e-4
set Abeam 2.206e-2
set Ibeam 2.052e-3

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

# Columns (W24x68) — ground -> L1.
element elasticBeamColumn 1 1 6 $Acol $E $Icol 1
element elasticBeamColumn 2 2 7 $Acol $E $Icol 1
element elasticBeamColumn 3 3 8 $Acol $E $Icol 1
element elasticBeamColumn 4 4 9 $Acol $E $Icol 1
element elasticBeamColumn 5 5 10 $Acol $E $Icol 1
# Columns — L1 -> L2.
element elasticBeamColumn 6 6 11 $Acol $E $Icol 1
element elasticBeamColumn 7 7 12 $Acol $E $Icol 1
element elasticBeamColumn 8 8 13 $Acol $E $Icol 1
element elasticBeamColumn 9 9 14 $Acol $E $Icol 1
element elasticBeamColumn 10 10 15 $Acol $E $Icol 1
# Columns — L2 -> L3.
element elasticBeamColumn 11 11 16 $Acol $E $Icol 1
element elasticBeamColumn 12 12 17 $Acol $E $Icol 1
element elasticBeamColumn 13 13 18 $Acol $E $Icol 1
element elasticBeamColumn 14 14 19 $Acol $E $Icol 1
element elasticBeamColumn 15 15 20 $Acol $E $Icol 1

# Beams (W21x44) — L1.
element elasticBeamColumn 16 6 7 $Abeam $E $Ibeam 1
element elasticBeamColumn 17 7 8 $Abeam $E $Ibeam 1
element elasticBeamColumn 18 8 9 $Abeam $E $Ibeam 1
element elasticBeamColumn 19 9 10 $Abeam $E $Ibeam 1
# Beams — L2.
element elasticBeamColumn 20 11 12 $Abeam $E $Ibeam 1
element elasticBeamColumn 21 12 13 $Abeam $E $Ibeam 1
element elasticBeamColumn 22 13 14 $Abeam $E $Ibeam 1
element elasticBeamColumn 23 14 15 $Abeam $E $Ibeam 1
# Beams — L3.
element elasticBeamColumn 24 16 17 $Abeam $E $Ibeam 1
element elasticBeamColumn 25 17 18 $Abeam $E $Ibeam 1
element elasticBeamColumn 26 18 19 $Abeam $E $Ibeam 1
element elasticBeamColumn 27 19 20 $Abeam $E $Ibeam 1

# Seismic masses (kg), tributary-weighted across the 5 nodes per level.
# L1 and L2: total 4.785e5 kg -> edge 5.981e4 kg, interior 1.196e5 kg.
mass 6 5.981e4 5.981e4 0.0
mass 7 1.196e5 1.196e5 0.0
mass 8 1.196e5 1.196e5 0.0
mass 9 1.196e5 1.196e5 0.0
mass 10 5.981e4 5.981e4 0.0
mass 11 5.981e4 5.981e4 0.0
mass 12 1.196e5 1.196e5 0.0
mass 13 1.196e5 1.196e5 0.0
mass 14 1.196e5 1.196e5 0.0
mass 15 5.981e4 5.981e4 0.0
# L3 (roof): total 5.20e5 kg -> edge 6.50e4 kg, interior 1.30e5 kg.
mass 16 6.50e4 6.50e4 0.0
mass 17 1.30e5 1.30e5 0.0
mass 18 1.30e5 1.30e5 0.0
mass 19 1.30e5 1.30e5 0.0
mass 20 6.50e4 6.50e4 0.0
