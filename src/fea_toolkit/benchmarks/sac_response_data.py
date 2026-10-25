"""Transcribed benchmark *response* targets for the Ohtori et al. (2004) SAC models.

The benchmark archive ships three ``unctrl#.mat`` files
(``local/257/Files_bldg_full3.zip`` → ``MATLAB_Utility/unctrl3.mat`` /
``unctrl9.mat`` / ``unctrl20.mat``) holding the **uncontrolled** (open-loop, no
control device) peak response of each building to the ten required earthquake
records.  These are the authoritative NLTHA acceptance targets (A4): the same
ten cases, run without a controller, whose peak response the toolkit must
reproduce.

This module is the committed, checkable transcription of those ``.mat`` files
(the sources live under the gitignored ``local/`` tree).  Each value is a
**peak over the whole record**, one per benchmark case.

Case ordering (from the archive's ``Run_NLBM.m``): the ten simulations are

    1–3   El Centro  1940, intensities 0.5 / 1.0 / 1.5
    4–6   Hachinohe 1968, intensities 0.5 / 1.0 / 1.5
    7–8   Northridge 1994, intensities 0.5 / 1.0
    9–10  Kobe 1995, intensities 0.5 / 1.0

Quantities (the ``EvalNLBM.m`` denominators J1/J2/J3/J7/J8/J9):

* ``d_max``   — peak interstory drift **ratio** (drift ÷ story height).
* ``xdd_max`` — peak **absolute** floor acceleration (m/s²).
* ``F_max``   — peak base shear = ``Σ m_i·xdd_i`` (total inertial force, N).
* ``phi_max`` — peak ductility ``max|φ_j|`` (member end curvature ÷ yield
  curvature ``d1``).
* ``E_max``   — peak dissipated energy ``dE_j`` (÷ yield moment × yield
  curvature).
* ``N_d``     — number of damaged (yielded) connections.

``F_max`` uses the **whole-building** mass (the evaluator's ``mi``), which is
twice the per-frame seismic mass in ``struct#.m`` — so a single planar frame's
base shear must be doubled to compare.  ``d_max`` / ``xdd_max`` are the maxima
over the floor levels.  A ``NaN`` marks a case the archive recorded without a
damage value (the 3-story Hachinohe 0.5 run).
"""

from typing import NamedTuple

from numpy import nan

#: ``(record_id, intensity)`` for each of the ten benchmark cases, in
#: ``Run_NLBM.m`` order (index 0 = El Centro 0.5 … index 9 = Kobe 1.0).
CASE_ORDER: list[tuple[str, float]] = [
    ("ELCENTRO", 0.5),
    ("ELCENTRO", 1.0),
    ("ELCENTRO", 1.5),
    ("HACHINOHE", 0.5),
    ("HACHINOHE", 1.0),
    ("HACHINOHE", 1.5),
    ("NORTHRIDGE", 0.5),
    ("NORTHRIDGE", 1.0),
    ("KOBE", 0.5),
    ("KOBE", 1.0),
]

#: Peak response per building → quantity → ten case values (in ``CASE_ORDER``
#: order), transcribed from ``unctrl#.mat``.
UNCTRL_TARGETS: dict[int, dict[str, list[float]]] = {
    3: {
        "d_max": [
            0.010529,
            0.015034,
            0.017136,
            0.008731,
            0.014491,
            0.020449,
            0.018433,
            0.025292,
            0.019929,
            0.036526,
        ],
        "xdd_max": [
            4.282,
            6.5389,
            7.4044,
            3.9299,
            6.48,
            6.93,
            7.2971,
            9.5898,
            7.3332,
            12.035,
        ],
        "F_max": [
            8.9267e6,
            1.0120e7,
            1.0978e7,
            6.8201e6,
            9.6532e6,
            1.1374e7,
            1.2542e7,
            1.4225e7,
            1.1148e7,
            1.3369e7,
        ],
        "phi_max": [
            1.1186,
            1.8119,
            2.2866,
            0.86224,
            1.761,
            2.2747,
            3.0339,
            4.1131,
            2.3134,
            3.9848,
        ],
        "E_max": [
            1.8401,
            91.945,
            182.6,
            nan,
            107.29,
            274.85,
            37.655,
            188.77,
            104.3,
            250.11,
        ],
        "N_d": [2, 22, 22, nan, 22, 24, 24, 33, 24, 32],
    },
    9: {
        "d_max": [
            0.008568,
            0.015088,
            0.021311,
            0.006997,
            0.013336,
            0.019413,
            0.015453,
            0.020871,
            0.021616,
            0.029315,
        ],
        "xdd_max": [
            3.28838,
            5.4153,
            6.145658,
            2.591307,
            5.159775,
            6.030332,
            5.632658,
            7.339528,
            6.92491,
            10.097151,
        ],
        "F_max": [
            1.1580815e7,
            1.7336945e7,
            1.9399511e7,
            9.9776553e6,
            1.6709901e7,
            1.8260956e7,
            1.7985166e7,
            2.1233004e7,
            1.4706796e7,
            2.0813284e7,
        ],
        "phi_max": [
            0.872857,
            2.11446,
            3.11852,
            0.753913,
            1.87836,
            2.0809,
            2.16598,
            2.83986,
            2.46085,
            3.18367,
        ],
        "E_max": [
            1.7e-5,
            15.704,
            40.527,
            1e-6,
            16.6934,
            43.9831,
            32.3484,
            89.5881,
            74.1193,
            244.275,
        ],
        "N_d": [0, 49, 69, 0, 57, 71, 63, 75, 26, 76],
    },
    20: {
        "d_max": [
            0.003542,
            0.007084,
            0.010640,
            0.003206,
            0.006411,
            0.009158,
            0.010899,
            0.018820,
            0.010807,
            0.020481,
        ],
        "xdd_max": [
            2.669615,
            5.339231,
            7.78557,
            1.829085,
            3.65817,
            4.715187,
            6.007568,
            8.510666,
            7.494492,
            9.974086,
        ],
        "F_max": [
            3.6882261e6,
            7.3764521e6,
            9.5281765e6,
            3.1784112e6,
            6.3568225e6,
            9.1233878e6,
            1.0363015e7,
            1.4256577e7,
            1.0078977e7,
            1.1423370e7,
        ],
        "phi_max": [
            0.466296,
            0.932592,
            1.50325,
            0.427801,
            0.855602,
            1.39703,
            1.67531,
            3.36007,
            1.92294,
            3.19872,
        ],
        "E_max": [
            1.078e-3,
            4.313e-3,
            19.4655,
            4.24e-4,
            1.695e-3,
            6.09228,
            20.6055,
            86.2258,
            18.7005,
            282.69,
        ],
        "N_d": [0, 0, 86, 0, 0, 86, 96, 192, 78, 168],
    },
}


class TargetRow(NamedTuple):
    """One benchmark case's peak-response targets (drawn from ``UNCTRL_TARGETS``)."""

    record: str
    intensity: float
    d_max: float
    xdd_max: float
    F_max: float
    phi_max: float
    E_max: float
    N_d: float


def case_targets(building: int, case_index: int) -> TargetRow:
    """Return the peak targets for one building and case (0-based index)."""
    rec, intensity = CASE_ORDER[case_index]
    row = UNCTRL_TARGETS[building]
    return TargetRow(
        record=rec,
        intensity=intensity,
        d_max=row["d_max"][case_index],
        xdd_max=row["xdd_max"][case_index],
        F_max=row["F_max"][case_index],
        phi_max=row["phi_max"][case_index],
        E_max=row["E_max"][case_index],
        N_d=row["N_d"][case_index],
    )


__all__ = [
    "CASE_ORDER",
    "UNCTRL_TARGETS",
    "TargetRow",
    "case_targets",
]
