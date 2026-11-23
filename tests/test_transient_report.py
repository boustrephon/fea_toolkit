"""Transient (time-history) report + Rhino consumers (P43 item 6.2 / B.3)."""

import numpy as np
import pytest

from examples.sample_model import make_sample_model
from fea_toolkit.io.npz_reader import read_results
from fea_toolkit.io.npz_writer import write_results_npz


def _transient_npz(tmp_path):
    md = make_sample_model()
    tags = sorted(nd.node_tag for nd in md.nodes.values())
    n = 4
    result = {
        "times": np.arange(n, dtype=float) * 0.01,
        "displacements": {
            tag: np.column_stack([np.arange(n, dtype=float), np.zeros(n), np.zeros(n)])
            for tag in tags
        },
        "base_shear": np.linspace(0.0, 30.0, n),
        "direction": "X",
    }
    path = str(tmp_path / "t.npz")
    write_results_npz(path, md, transient_results={"ELC": result})
    return md, read_results(path)


def test_transient_history_df(tmp_path):
    pytest.importorskip("pandas")

    from fea_toolkit.io.report import transient_history_df

    md, data = _transient_npz(tmp_path)
    node_id = next(iter(md.nodes))

    df = transient_history_df(data, "ELC", node_id=node_id)

    assert list(df.columns) == ["time", "dx", "dy", "dz", "base_shear"]
    assert len(df) == 4
    assert df["dx"].tolist() == [0.0, 1.0, 2.0, 3.0]


def test_transient_history_df_base_shear_only(tmp_path):
    pytest.importorskip("pandas")

    from fea_toolkit.io.report import transient_history_df

    _md, data = _transient_npz(tmp_path)
    df = transient_history_df(data, "ELC", node_id=None)

    assert list(df.columns) == ["time", "base_shear"]


def test_transient_history_df_unknown_node_raises(tmp_path):
    pytest.importorskip("pandas")

    from fea_toolkit.io.report import transient_history_df

    _md, data = _transient_npz(tmp_path)
    with pytest.raises(KeyError, match="not recorded"):
        transient_history_df(data, "ELC", node_id="NO_SUCH_NODE")


def test_transient_nodal_magnitudes_peak_and_at_index(tmp_path):
    from fea_toolkit.rhino.colour_from_npz import transient_nodal_magnitudes

    md, data = _transient_npz(tmp_path)
    magnitudes = transient_nodal_magnitudes(data, "ELC")

    assert set(magnitudes) == set(md.nodes)
    # dx at each node ramps 0..3, so the peak norm is 3.0 everywhere.
    assert all(abs(v - 3.0) < 1e-12 for v in magnitudes.values())

    at_0 = transient_nodal_magnitudes(data, "ELC", k=0)
    assert all(abs(v) < 1e-12 for v in at_0.values())


def test_plot_transient_history_returns_a_figure(tmp_path):
    pytest.importorskip("pandas")

    from fea_toolkit.plotting.report import plot_transient_history

    md, data = _transient_npz(tmp_path)
    fig = plot_transient_history(data, "ELC", next(iter(md.nodes)))

    assert fig is not None
