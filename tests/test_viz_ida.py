"""Smoke tests for the IDA curve / fragility plotters.

The plots read a flat NPZ dict carrying the ``ida`` block (schema v5); a
synthetic two-record dict exercises both figures without any OpenSees run.
``MPLBACKEND=Agg`` is forced suite-wide in ``tests/conftest.py``, so no window
is opened.
"""

import pytest

from fea_toolkit.io.npz_writer import collect_ida_arrays
from fea_toolkit.plotting.viz_ida import (
    plot_collapse_fragility,
    plot_ida_curves,
    plot_ida_summary,
)


@pytest.fixture()
def ida_arrays():
    from tests.test_ida_schema import _synthetic_ida_result

    return collect_ida_arrays(_synthetic_ida_result())


def test_plot_ida_curves_returns_figure(ida_arrays):
    fig = plot_ida_curves(ida_arrays)
    assert fig is not None
    import matplotlib.pyplot as plt

    plt.close(fig)


def test_plot_collapse_fragility_returns_figure(ida_arrays):
    fig = plot_collapse_fragility(ida_arrays)
    assert fig is not None
    import matplotlib.pyplot as plt

    plt.close(fig)


def test_plot_ida_summary_returns_figure(ida_arrays):
    fig = plot_ida_summary(ida_arrays)
    assert fig is not None
    import matplotlib.pyplot as plt

    plt.close(fig)


def test_plot_ida_curves_empty_is_none():
    assert plot_ida_curves({}) is None
