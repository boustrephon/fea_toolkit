"""Generate the SAC nonlinear benchmark figures and the loadable model artifact.

Writes into ``docs/assets/`` (committed so MkDocs serves the images).  The
script is **idempotent** — each output is only (re)written when its file is
absent, so re-running it is cheap and never clobbers a curated figure.

Run with the OpenSeesPy venv (needs ``pyvista`` and ``matplotlib``)::

    python docs/_generate_sac_figures.py

Outputs:

* ``sac3_model.png``           — the SAC 3-story frame (columns vs beams).
* ``sac3_hinge_backbone.png``  — bilinear steel-hinge M-θ (column + beam).
* ``sac3_panel_zone.png``      — Krawinkler & Gupta panel-zone M-θ.
* ``sac3_nltha_synthetic.png`` — roof X time history under a synthetic pulse (M1).
* ``sac3_periods.png``         — elastic / M1 / M2 fundamental periods.
* ``sac3_model.json``          — the enriched model as a model-codec snapshot
                                 (openable by ``examples/view_model.py``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("MPLBACKEND", "Agg")


def _skip(path: Path) -> bool:
    return path.exists()


def _render_model_image() -> None:
    img = ASSETS / "sac3_model.png"
    if _skip(img):
        return
    from fea_toolkit.benchmarks.sac import build_builder
    from fea_toolkit.plotting.viz_model import plot_mesh

    b = build_builder(nonlinear=False)
    plotter = plot_mesh(
        b,
        notebook=True,
        off_screen=True,
        window_size=[1400, 1000],
        show_nodes=True,
        section_colors={"SEC1": "#1f77b4", "SEC2": "#d62728"},
    )
    if plotter is not None:
        plotter.camera_position = "iso"
        plotter.screenshot(str(img))
        plotter.close()
        print(f"wrote {img.name}")


def _hinge_backbone() -> None:
    img = ASSETS / "sac3_hinge_backbone.png"
    if _skip(img):
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from fea_toolkit.benchmarks.sac import BEAM, COLUMN

    def steel01(my, e0, b, n=200):
        th_y = my / e0
        th = np.linspace(0.0, 4.0 * th_y, n)
        m = np.where(th <= th_y, e0 * th, my + b * e0 * (th - th_y))
        return th, m

    E = 2.0e11
    col_e0 = 6.0 * E * 7.617e-4 / 3.9624
    beam_e0 = 6.0 * E * 2.052e-3 / 9.144
    col_my = COLUMN["Z33"] * COLUMN["fy"]
    beam_my = BEAM["Z33"] * BEAM["fy"]

    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    for my, e0, label, c in (
        (col_my, col_e0, "W24x68 column (345 MPa)", "#1f77b4"),
        (beam_my, beam_e0, "W30x116 beam (248 MPa)", "#d62728"),
    ):
        th, m = steel01(my, e0, 0.02)
        ax.plot(th * 1e3, m / 1e3, label=label, color=c)
    ax.set_xlabel("rotation θ (mrad)")
    ax.set_ylabel("moment M (kN·m)")
    ax.set_title("Bilinear steel hinge — My = Zx·fy, 2 % hardening")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(img, dpi=150)
    plt.close(fig)
    print(f"wrote {img.name}")


def _panel_zone() -> None:
    img = ASSETS / "sac3_panel_zone.png"
    if _skip(img):
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from fea_toolkit.benchmarks.sac import BEAM, COLUMN

    dc, tp, db = COLUMN["depth"], COLUMN["tw"], BEAM["depth"]
    fy, G, b = COLUMN["fy"], 0.4 * 2.0e11, 0.03
    my = 0.55 * fy * dc * tp * db
    kth = 0.95 * G * tp * dc * db
    th_y = my / kth
    th = np.linspace(0.0, 4.0 * th_y, 200)
    m = np.where(th <= th_y, kth * th, my + b * kth * (th - th_y))

    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    ax.plot(th * 1e3, m / 1e3, color="#2ca02c")
    ax.set_xlabel("rotation θ (mrad)")
    ax.set_ylabel("moment M (kN·m)")
    ax.set_title("Krawinkler & Gupta panel zone — My = 0.55·fy·dc·tp·db")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(img, dpi=150)
    plt.close(fig)
    print(f"wrote {img.name}")


def _nltha_synthetic() -> None:
    img = ASSETS / "sac3_nltha_synthetic.png"
    if _skip(img):
        return
    import math

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks.sac import build_builder

    wipe()
    try:
        b = build_builder(panel_zone=False)
        dt, n = 0.005, 400
        t = np.arange(n) * dt
        accel = np.zeros(n)
        accel[:40] = 2.0 * np.sin(2.0 * math.pi * 1.0 * t[:40])
        res = b.run_transient_analysis(accel, dt, direction="X", damping_ratio=0.05)
        z_max = max(nd.z for nd in b.mesh_model.nodes.values())
        roof_tag = max(
            (nd.node_tag for nd in b.mesh_model.nodes.values() if nd.z == z_max),
            default=None,
        )
        times = res["times"]
        disp = res["displacements"].get(roof_tag, np.zeros((0, 3)))
    finally:
        wipe()

    fig, ax = plt.subplots(figsize=(7.0, 3.4))
    if disp.size:
        ax.plot(times, disp[:, 0], color="#1f77b4")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("roof X displacement (m)")
    ax.set_title("SAC M1 — roof displacement, synthetic 1 Hz pulse")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(img, dpi=150)
    plt.close(fig)
    print(f"wrote {img.name}")


def _t1(*, nonlinear: bool, panel_zone: bool) -> float:
    from openseespy.opensees import wipe

    from fea_toolkit.benchmarks.sac import build_builder

    wipe()
    try:
        b = build_builder(panel_zone=panel_zone, nonlinear=nonlinear)
        m = b.run_modal_analysis(num_modes=1, print_results=False)
        return float(m["periods"][0]) if m.get("periods") else float("nan")
    finally:
        wipe()


def _periods() -> None:
    img = ASSETS / "sac3_periods.png"
    if _skip(img):
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["elastic", "M1 (hinges)", "M2 (+ panel zone)"]
    values = [
        _t1(nonlinear=False, panel_zone=False),
        _t1(nonlinear=True, panel_zone=False),
        _t1(nonlinear=True, panel_zone=True),
    ]
    colors = ["#7f7f7f", "#1f77b4", "#2ca02c"]

    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    bars = ax.bar(labels, values, color=colors)
    ax.set_ylabel("fundamental period T1 (s)")
    ax.set_title("SAC 3-story — fundamental period by model")
    for rect, v in zip(bars, values):
        ax.text(rect.get_x() + rect.get_width() / 2, v, f"{v:.3f}", ha="center", va="bottom")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(img, dpi=150)
    plt.close(fig)
    print(f"wrote {img.name}")


def _artifact_json() -> None:
    path = ASSETS / "sac3_model.json"
    if _skip(path):
        return
    from fea_toolkit.benchmarks.sac import load_model
    from fea_toolkit.io.model_codec import model_to_json

    md = load_model(nonlinear=True)
    path.write_text(model_to_json(md), encoding="utf-8")
    print(f"wrote {path.name}")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    _render_model_image()
    _hinge_backbone()
    _panel_zone()
    _nltha_synthetic()
    _periods()
    _artifact_json()
    print("done")


if __name__ == "__main__":
    main()
