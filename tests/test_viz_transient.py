"""Tests for the standalone transient (NLTHA) animation (P43 item 6.1 / B.2).

The history-resolution helpers are pure (numpy-only), so they are tested without
PyVista or OpenSees; the plotting entry point gets a single ``needs_pyvista``
smoke test.
"""

import numpy as np
import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.io.npz_reader import read_results
from fea_toolkit.io.npz_writer import write_results_npz
from fea_toolkit.plotting.viz_model import (
    _rekey_transient_history_to_tag,
    _resolve_transient_history,
)


def _transient_npz_dict(tmp_path) -> dict:
    """Write a sample model + one transient case and read the archive back."""
    md = make_sample_model()
    tags = sorted(nd.node_tag for nd in md.nodes.values())
    n = 4
    result = {
        "times": np.arange(n, dtype=float) * 0.01,
        "displacements": {
            tag: np.column_stack([np.arange(n, dtype=float), np.zeros(n), np.zeros(n)])
            for tag in tags
        },
        "base_shear": np.linspace(0.0, 10.0, n),
        "direction": "X",
    }
    path = str(tmp_path / "transient.npz")
    write_results_npz(path, md, transient_results={"ELC": result})
    return read_results(path)


def test_resolve_transient_history_from_npz(tmp_path):
    data = _transient_npz_dict(tmp_path)
    mesh, _times, history_by_tag, n_steps = _resolve_transient_history(data, case="ELC")

    assert n_steps == 4
    assert set(mesh["nodes"])  # geometry resolved
    assert set(history_by_tag) == {nd.node_tag for nd in make_sample_model().nodes.values()}
    assert all(hist.shape == (4, 3) for hist in history_by_tag.values())


def test_resolve_transient_history_from_builder():
    md = make_sample_model()
    tags = sorted(nd.node_tag for nd in md.nodes.values())
    n = 3
    transient = {
        "times": np.arange(n, dtype=float) * 0.01,
        "displacements": {
            tag: np.column_stack([np.arange(n, dtype=float), np.zeros(n), np.zeros(n)])
            for tag in tags
        },
    }

    _mesh, _times, history_by_tag, n_steps = _resolve_transient_history(md, transient=transient)

    assert n_steps == 3
    assert set(history_by_tag) == set(tags)


def test_resolve_transient_history_handles_missing_case(tmp_path):
    data = _transient_npz_dict(tmp_path)
    _mesh, _times, history_by_tag, n_steps = _resolve_transient_history(data, case="NO_SUCH_CASE")

    assert n_steps == 0
    assert history_by_tag == {}


def test_rekey_transient_history_to_tag():
    data = {
        "nodes": {1: {"tag": 11, "x": 0, "y": 0, "z": 0}, "A": {"tag": 12, "x": 0, "y": 0, "z": 0}}
    }
    by_id = {"A": np.zeros((2, 3)), 1: np.ones((2, 3))}

    rekeyed = _rekey_transient_history_to_tag(data, by_id)

    assert set(rekeyed) == {11, 12}


@pytest.mark.needs_pyvista
def test_plot_transient_animation_builds_a_plotter(tmp_path, monkeypatch):
    pv = pytest.importorskip("pyvista")
    monkeypatch.setattr(pv.Plotter, "show", lambda self, *args, **kwargs: None)

    from fea_toolkit.plotting import plot_transient_animation

    data = _transient_npz_dict(tmp_path)
    plotter = plot_transient_animation(data, case="ELC", notebook=True, animate=False)

    assert plotter is not None
