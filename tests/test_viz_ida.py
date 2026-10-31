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


def test_plot_ida_curves_flatline_option(ida_arrays):
    """flatline=True draws a horizontal cap instead of × markers."""
    fig = plot_ida_curves(ida_arrays, flatline=True)
    assert fig is not None
    import matplotlib.pyplot as plt

    ax = fig.axes[0]
    # IV79cal collapses by non-convergence -> one dashed horizontal flatline.
    flat = [ln for ln in ax.lines if ln.get_linestyle() == "--" and len(ln.get_xdata()) == 2]
    assert len(flat) == 1
    assert flat[0].get_ydata()[0] == flat[0].get_ydata()[1]
    # No × non-converged scatter in flatline mode (only the collapse markers).
    assert len(ax.collections) == 1
    plt.close(fig)


def test_plot_ida_curves_marks_nonconverged_rungs(ida_arrays):
    """A non-converged rung is a `×` marker; curves stop at the collapse IM."""
    fig = plot_ida_curves(ida_arrays)
    assert fig is not None
    import matplotlib.pyplot as plt

    ax = fig.axes[0]
    # IV79cal (non-convergence collapse at scale 3.0) -> 3 converged rungs;
    # the failed rung is a separate scatter, not joined.
    assert len(ax.lines[0].get_xdata()) == 3
    # IV79chi (drift collapse at scale 2.0) -> curve stops at 2 rungs (drift < 0.10).
    assert len(ax.lines[1].get_xdata()) == 2
    # A × scatter marks the failed rung(s), plus the collapse markers.
    assert (
        sum(isinstance(c, plt.matplotlib.collections.PathCollection) for c in ax.collections) >= 2
    )
    plt.close(fig)


def test_plot_ida_curves_drift_collapse_vertical_marker(ida_arrays):
    """A drift-criterion collapse gets a vertical line at θ = collapse_drift."""
    fig = plot_ida_curves(ida_arrays)
    assert fig is not None
    import matplotlib.pyplot as plt

    ax = fig.axes[0]
    vertical = [ln for ln in ax.lines if ln.get_linestyle() == ":" and len(ln.get_xdata()) == 2]
    assert len(vertical) == 1
    xs = vertical[0].get_xdata()
    assert xs[0] == xs[1] == pytest.approx(0.10)
    plt.close(fig)
