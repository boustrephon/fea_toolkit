"""Shared low-level helpers for the plotting subpackage.

Isometric-view setup, colour mapping/legends, animation timers, and the
NPZ-data type guard shared by the higher-level viewers and diagrams.
Re-exported by :mod:`fea_toolkit.plotting.viz` for backward compatibility."""

from typing import Any

import numpy as np

_NPZ_TYPES = (dict, np.lib.npyio.NpzFile)


def _set_isometric_view(plotter) -> None:
    """Set an isometric view that works for any model (including 1D columns).

    Also enables terrain-style interaction so Z stays vertical when the
    user rotates the view with the mouse.
    """
    bounds = plotter.bounds
    z_range = max(bounds[5] - bounds[4], 1.0)
    x_range = max(bounds[1] - bounds[0], 0.1)
    y_range = max(bounds[3] - bounds[2], 0.1)
    horiz = max(x_range, y_range)
    cx = (bounds[0] + bounds[1]) * 0.5
    cy = (bounds[2] + bounds[3]) * 0.5
    cz = (bounds[4] + bounds[5]) * 0.5
    dist = max(horiz, z_range) * 1.5
    plotter.camera_position = [
        (cx + dist, cy + dist, cz + dist * 0.4),
        (cx, cy, cz),
        (0.0, 0.0, 1.0),
    ]
    plotter.enable_terrain_style(mouse_wheel_zooms=True, shift_pans=True)


_DEFAULT_HINGE_CMAP = "plasma"


def _sample_cmap(points: list[float], cmap_name: str) -> list[tuple[float, float, float]]:
    """Sample a matplotlib colormap at normalised positions.

    Returns a list of ``(r, g, b)`` tuples in 0..1 for each *points* value
    (each clamped to ``[0, 1]``).  Falls back to a fixed (blue, yellow, red)
    palette if matplotlib is unavailable or the colormap name is unknown.

    Uses ``matplotlib.colormaps.get_cmap`` (available since Matplotlib 3.5).
    The deprecated ``matplotlib.cm.get_cmap`` legacy API (removed in
    Matplotlib 3.11) is intentionally not used.
    """
    try:
        from matplotlib import colormaps as _mcmaps

        cmap = _mcmaps.get_cmap(cmap_name)
        return [tuple(float(c) for c in cmap(min(max(p, 0.0), 1.0))[:3]) for p in points]
    except (ImportError, AttributeError, KeyError, ValueError):
        # Fallback (red-green colour-blind safe defaults preserved) —
        # interpolate the fixed blue → yellow → red palette at the
        # normalised positions so the fallback is position-dependent,
        # matching the matplotlib sampling contract.
        _fallback = [(0.3, 0.45, 0.69), (0.9, 0.8, 0.2), (0.9, 0.25, 0.2)]
        out: list[tuple[float, float, float]] = []
        for p in points:
            t = min(max(p, 0.0), 1.0)
            if t < 0.5:
                s = t * 2.0
                c0, c1 = _fallback[0], _fallback[1]
            else:
                s = (t - 0.5) * 2.0
                c0, c1 = _fallback[1], _fallback[2]
            out.append(
                (
                    c0[0] + (c1[0] - c0[0]) * s,
                    c0[1] + (c1[1] - c0[1]) * s,
                    c0[2] + (c1[2] - c0[2]) * s,
                )
            )
        return out


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    """Convert an (r, g, b) tuple in [0, 1] to a ``#RRGGBB`` hex string."""
    r = max(0.0, min(1.0, rgb[0]))
    g = max(0.0, min(1.0, rgb[1]))
    b = max(0.0, min(1.0, rgb[2]))
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def _ratio_to_color(
    ratio: float, max_r: float = 1.0, cmap_name: str = _DEFAULT_HINGE_CMAP
) -> tuple[float, float, float]:
    """Map hinge ratio [0, max_r] to an RGB colour.

    Colours are sampled from the named matplotlib colormap at positions
    0.0 (elastic), 0.5 (yielding) and 1.0 (fully yielded) with a continuous
    interpolation between them.  ``cmap_name`` defaults to
    :data:`_DEFAULT_HINGE_CMAP` (``"plasma"`` — perceptually uniform and
    colour-blind safe).  The 0.5 / 1.0 thresholds are fixed:

    * **elastic** (ratio < 0.5·max_r).
    * **yielding** (0.5 ≤ ratio < max_r).
    * **fully yielded** (ratio ≥ max_r).

    Args:
        ratio: Hinge ratio in ``[0, max_r]`` (values above *max_r* clamp).
        max_r: Normalisation denominator (expected yield value).
        cmap_name: Matplotlib colormap name (e.g. ``"plasma"``, ``"viridis"``,
            ``"cividis"``); falls back to blue/yellow/red if unavailable.

    Returns:
        ``(r, g, b)`` tuple with components in ``[0, 1]``.
    """
    if max_r < 1e-12:
        try:
            return _sample_cmap([0.0], cmap_name)[0]
        except (ImportError, AttributeError, KeyError, ValueError, TypeError):
            return (0.3, 0.45, 0.69)  # default blue
    norm = min(ratio / max_r, 1.0) if max_r > 0 else 0.0
    samples = _sample_cmap([0.0, 0.5, 1.0], cmap_name)
    c0, c1, c2 = samples[0], samples[1], samples[2]
    # Interpolate: c0 (0) -> c1 (0.5) -> c2 (1.0)
    if norm < 0.5:
        t = norm / 0.5
        return (
            c0[0] + (c1[0] - c0[0]) * t,
            c0[1] + (c1[1] - c0[1]) * t,
            c0[2] + (c1[2] - c0[2]) * t,
        )
    else:
        t = (norm - 0.5) / 0.5
        return (
            c1[0] + (c2[0] - c1[0]) * t,
            c1[1] + (c2[1] - c1[1]) * t,
            c1[2] + (c2[2] - c1[2]) * t,
        )


def _add_hinge_color_legend(
    plotter,
    title: str = "Relative Moment Demand (peak-normalized)",
    position_x: float = 0.82,
    position_y: float = 0.1,
    width: float = 0.06,
    height: float = 0.6,
    n_colors: int = 256,
    cmap_name: str = _DEFAULT_HINGE_CMAP,
) -> None:
    """Add a scalar bar / colour legend for the hinge ratio scale.

    Builds a :class:`pyvista.LookupTable` using the same interpolation
    as :func:`_ratio_to_color` and attaches it to the plotter.

    The colour scale maps the demand/capacity hinge ratio (threshold 0.5) to:

    * **elastic** (ratio < 0.5) — sampled at cmap position 0.0.
    * **yielding** (0.5 ≤ ratio < 1.0) — sampled at cmap position 0.5.
    * **fully yielded** (ratio ≥ 1.0) — sampled at cmap position 1.0.

    Colours come from the named matplotlib colormap (default
    :data:`_DEFAULT_HINGE_CMAP` — ``"plasma"``, which is perceptually
    uniform and colour-blind safe).

    Internally uses ``pyvista.LookupTable`` with ``n_values`` and ``values``
    attributes (not ``number_of_colors`` or ``table``, which were removed
    in PyVista v0.44+).  The ``lookup_table`` kwarg to
    ``plotter.add_scalar_bar()`` is also version-dependent — a
    ``TypeError`` fallback omits it for older PyVista installations.

    Args:
        plotter: PyVista Plotter to add the legend to.
        title: Title text above the scalar bar.
        position_x, position_y: Normalised position of the scalar bar.
        width, height: Normalised size of the scalar bar.
        n_colors: Number of discrete colour steps in the lookup table
            (sets ``LookupTable.n_values``).
        cmap_name: Matplotlib colormap name for the colour scale.
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        return

    # Build lookup table using same sampled-colormap logic as _ratio_to_color
    lut = pv.LookupTable()
    lut.n_values = n_colors
    lut.scalar_range = (0.0, 1.0)
    c0, c1, c2 = _sample_cmap([0.0, 0.5, 1.0], cmap_name)
    colors = np.zeros((n_colors, 4), dtype=np.uint8)
    for i in range(n_colors):
        t = i / (n_colors - 1)
        if t < 0.5:
            s = t * 2.0
            r = c0[0] + (c1[0] - c0[0]) * s
            g = c0[1] + (c1[1] - c0[1]) * s
            b = c0[2] + (c1[2] - c0[2]) * s
        else:
            s = (t - 0.5) * 2.0
            r = c1[0] + (c2[0] - c1[0]) * s
            g = c1[1] + (c2[1] - c1[1]) * s
            b = c1[2] + (c2[2] - c1[2]) * s
        colors[i] = (int(r * 255), int(g * 255), int(b * 255), 255)
    lut.values = colors

    try:
        plotter.add_scalar_bar(
            title=title,
            position_x=position_x,
            position_y=position_y,
            width=width,
            height=height,
            lookup_table=lut,
            title_font_size=10,
            label_font_size=8,
            bold=False,
            italic=False,
            shadow=False,
        )
    except TypeError:
        # Older PyVista versions don't accept lookup_table as a kwarg
        plotter.add_scalar_bar(
            title=title,
            position_x=position_x,
            position_y=position_y,
            width=width,
            height=height,
            title_font_size=10,
            label_font_size=8,
            bold=False,
            italic=False,
            shadow=False,
        )


def _add_shell_color_legend(
    plotter,
    title: str = "Damage Index",
    position_x: float = 0.82,
    position_y: float = 0.1,
    width: float = 0.06,
    height: float = 0.6,
    n_colors: int = 256,
) -> None:
    """Add a scalar bar / colour legend for the shell damage green→yellow→red scale.

    Builds a :class:`pyvista.LookupTable` using the same interpolation
    as :func:`_ratio_to_shell_color` and attaches it to the plotter.

    The colour scale maps the normalised damage ratio to:

    * **Green** (ratio < 0.7) — elastic.
    * **Yellow** (0.7 ≤ ratio < 1.0) — yielding.
    * **Red** (ratio ≥ 1.0) — damaged / crushed.
    * **Gray** — no data (NaN).

    Internally uses ``pyvista.LookupTable`` with ``n_values`` and ``values``
    attributes (not ``number_of_colors`` or ``table``, which were removed
    in PyVista v0.44+).  The ``lookup_table`` kwarg to
    ``plotter.add_scalar_bar()`` is also version-dependent — a
    ``TypeError`` fallback omits it for older PyVista installations.

    Args:
        plotter: PyVista Plotter to add the legend to.
        title: Title text above the scalar bar.
        position_x, position_y: Normalised position of the scalar bar.
        width, height: Normalised size of the scalar bar.
        n_colors: Number of discrete colour steps in the lookup table
            (sets ``LookupTable.n_values``).
    """
    import numpy as np

    try:
        import pyvista as pv
    except ImportError:
        return

    # Build lookup table using same green→yellow→red logic as _ratio_to_shell_color
    lut = pv.LookupTable()
    lut.n_values = n_colors
    lut.scalar_range = (0.0, 1.0)
    colors = np.zeros((n_colors, 4), dtype=np.uint8)
    for i in range(n_colors):
        t = i / (n_colors - 1)
        if t < 0.7:
            s = t / 0.7
            r, g, b = s, 1.0, 0.0
        else:
            s = (t - 0.7) / 0.3
            r, g, b = 1.0, 1.0 - s, 0.0
        colors[i] = (int(r * 255), int(g * 255), int(b * 255), 255)
    lut.values = colors

    try:
        plotter.add_scalar_bar(
            title=title,
            position_x=position_x,
            position_y=position_y,
            width=width,
            height=height,
            lookup_table=lut,
            title_font_size=10,
            label_font_size=8,
            bold=False,
            italic=False,
            shadow=False,
        )
    except TypeError:
        # Older PyVista versions don't accept lookup_table as a kwarg
        plotter.add_scalar_bar(
            title=title,
            position_x=position_x,
            position_y=position_y,
            width=width,
            height=height,
            title_font_size=10,
            label_font_size=8,
            bold=False,
            italic=False,
            shadow=False,
        )


def _add_animation_timer(
    plotter,
    callback,
    max_steps: int = 1000,
    interval_ms: int = 200,
) -> None:
    """Attach a repeating timer to a PyVista plotter for animation.

    Handles PyVista version differences in **both** the registration API
    and the callback signature:

    * PyVista < 0.44: ``add_timer_event`` invokes the callback with no
      arguments (or a single ``step`` argument depending on version).
    * PyVista >= 0.44: ``add_timer_event`` invokes the callback with two
      positional arguments ``(step, plotter)``.

    The caller's callback may accept 0, 1, or 2 positional arguments
    (``f()``, ``f(step)``, or ``f(step, plotter)``).  This helper adapts
    the callback so the correct number of arguments is forwarded no
    matter what the installed PyVista version passes — preventing the
    classic ``TypeError: callback() takes N positional arguments but M
    were given`` that otherwise breaks mode-shape and pushover
    animations on newer PyVista.

    Registration strategies (in order):
      1. Modern PyVista: ``plotter.add_timer_event(max_steps=...,
         interval=..., callback=...)``.
      2. Older PyVista without ``interval`` kwarg support.
      3. Low-level VTK ``iren.AddObserver("TimerEvent", ...)``.

    If none succeed, a brief message is printed and animation proceeds
    via the slider widget alone.

    Args:
        plotter: A PyVista ``Plotter`` instance.
        callback: Callback invoked on each timer tick.  May accept 0, 1,
            or 2 positional arguments (``()``, ``(step,)``, or
            ``(step, plotter)``).
        max_steps: Maximum timer events before auto-stopping.
        interval_ms: Timer interval in milliseconds.
    """
    import inspect

    # ── Adapt the callback to however many positional args the timer passes ──
    try:
        _sig = inspect.signature(callback)
        # Required positional params (no default) — padding below only fills
        # these, so callback defaults are preserved when fewer args arrive.
        _n_pos = sum(
            1
            for p in _sig.parameters.values()
            if p.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            and p.default is inspect.Parameter.empty
        )
        # Total positional params (with defaults) — used only as the upper
        # bound when truncating excess positional args from the timer.
        _n_pos_total = sum(
            1
            for p in _sig.parameters.values()
            if p.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        _has_varargs = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL for p in _sig.parameters.values()
        )
    except (TypeError, ValueError):  # not introspectable (e.g. C-bound)
        # Conservative bound: treat as a bare ``callback(step)``.  Varargs is
        # *not* assumed, so surplus PyVista args (e.g. ``(step, plotter)``)
        # are truncated to ``(step,)`` instead of being forwarded and raising
        # ``TypeError: callback() takes 1 positional argument but 2 were
        # given``.  The step slot stays pad-able from the internal counter.
        _n_pos = 1
        _n_pos_total = 1
        _has_varargs = False

    _vtk_step = [0]  # mutable closure — timer events carry no step count

    def _adapted(*args: Any) -> Any:
        """Forward the right number of positional args to the user callback.

        PyVista's ``add_timer_event`` invokes the callback with
        ``(step, plotter)`` on v0.44+ and ``(step,)`` (or nothing) on
        older versions.

        Three adaptation rules apply:

        1. **Truncate surplus args** when the callback has no ``*args`` to
           absorb them (e.g. a one-argument ``callback(step)`` receiving
           ``(step, plotter)``).  ``_has_varargs`` only suppresses this
           truncation — it never affects the other two rules.
        2. **Supply a missing step** from the internal counter when the
           timer passes nothing at all and the callback needs at least one
           positional argument (e.g. ``callback(step)`` or
           ``callback(step, *extra)`` on a legacy no-argument timer).
        3. **Pad trailing gaps** with ``None`` when the timer passes fewer
           args than the callback's required positional parameters (e.g.
           ``callback(step, plotter)`` receiving only ``(step,)``).
        """
        # Rule 1 — drop surplus positional args unless the callback's
        # *args can absorb them.
        if not _has_varargs and len(args) > _n_pos_total:
            args = args[:_n_pos_total]
        # Rule 2 — legacy no-argument timer: back the step with the same
        # internal counter the VTK path uses.
        if not args and _n_pos >= 1:
            _vtk_step[0] += 1
            args = (_vtk_step[0],)
        # Rule 3 — pad trailing gaps with None.
        if len(args) < _n_pos:
            return callback(*args, *[None] * (_n_pos - len(args)))
        return callback(*args)

    def _vtk_adapted(*_args: Any) -> Any:
        """VTK ``TimerEvent`` observer — forwards ``(caller, event)``.

        The ``caller``/``event`` positional arguments are discarded: they
        carry no step-count meaning, and toolkit callbacks never require
        the VTK caller object.  For a callback that expects a step count
        (e.g. ``plot_mode_animation``'s ``callback(step)``, which computes
        a sine phase), an internal incrementing counter is supplied so the
        oscillation actually progresses; zero-argument callbacks (e.g. the
        pushover ``_timer_callback``) are invoked with no arguments, and
        two-argument callbacks ``callback(step, plotter)`` receive ``None``
        for the plotter since no plotter object exists on this path.
        """
        if _n_pos >= 1:
            # Supply the incrementing counter (plus ``None`` placeholders
            # for any additional expected args, e.g. the plotter).
            _vtk_step[0] += 1
            return callback(_vtk_step[0], *([None] * (_n_pos - 1)))
        return callback()

    # Strategy 1: modern PyVista with named kwargs
    try:
        plotter.add_timer_event(max_steps=max_steps, interval=interval_ms, callback=_adapted)
        return
    except TypeError:
        pass  # fall through
    except AttributeError:
        pass  # fall through

    # Strategy 2: older PyVista — try positional args, or without interval
    try:
        plotter.add_timer_event(max_steps=max_steps, callback=_adapted)
        return
    except TypeError:
        pass
    except AttributeError:
        pass

    # Strategy 3: VTK-level observer (most compatible)
    try:
        iren = plotter.render_window.GetInteractor()
        iren.AddObserver("TimerEvent", _vtk_adapted)
        iren.CreateRepeatingTimer(interval_ms)
        return
    except Exception:
        pass

    # No timer available — animation will use slider only
    print("  [Timer not available — animation via slider widget only]")
