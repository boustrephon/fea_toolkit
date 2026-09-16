"""Tests for ``fea_toolkit.plotting.viz_common`` — the PyVista animation
timer contract.

Guards the verified ``add_timer_event(max_steps, duration, callback)``
contract (callback arity, who renders) and the raw VTK fallback — see
``.clinerules`` §12.1 and ``docs/dev_notes.md``.
"""

import pytest

#: The whole module exercises the PyVista animation-timer path, so skip it
#: outright when PyVista is unavailable (e.g. Rhino 8's embedded interpreter).
pv = pytest.importorskip("pyvista")


# ============================================================================
# PyVista animation timer
# ============================================================================


class TestAnimationTimerCallbackArity:
    """Regression tests for the ``add_timer_event`` callback contract.

    PyVista's ``Timer.execute`` calls ``self.callback(self.step)`` — **one**
    positional argument — in 0.43 (where ``add_timer_event`` was
    introduced), 0.44 (the project's floor) and current ``main``.  It also
    renders each frame itself, so a PyVista-registered callback must not
    call ``plotter.render()``.

    The toolkit's own callbacks still differ in what they declare: ``()``
    for ``_timer_callback`` in ``animate_pushover_deformation`` and
    ``(step)`` for the mode-shape callback in ``plot_mode_animation``.
    ``_add_animation_timer`` must forward the right number of arguments for
    each — otherwise the ``step`` PyVista always supplies raises
    ``TypeError: callback() takes 0 positional arguments but 1 was given``
    and the pushover animation never advances.
    """

    @staticmethod
    def _make_fake_plotter(register_as):
        """Build a minimal fake plotter that records how the timer is registered.

        Simulates each registration path in ``_add_animation_timer``:

        - ``"pyvista"`` — a normal PyVista: ``add_timer_event`` accepts
          ``max_steps``/``duration``/``callback`` and stores the callback.
        - ``"pyvista_signature_mismatch"`` — ``add_timer_event`` exists but
          rejects the call with ``TypeError``, so the helper must fall back
          to the low-level VTK observer.
        - ``"vtek"`` — no ``add_timer_event`` at all (a pre-0.43 PyVista),
          so the helper falls through to ``AddObserver("TimerEvent", ...)``.
        """

        class _FakeInteractor:
            def __init__(self, plotter_ref):
                self.plotter_ref = plotter_ref
                self._observers = []

            def AddObserver(self, event, callback):
                self._observers.append((event, callback))
                self.plotter_ref.observer_added = True

            def CreateRepeatingTimer(self, interval_ms):
                self.plotter_ref.timer_created = True
                self.plotter_ref.interval_ms = interval_ms

        class _FakePlotter:
            def __init__(self, mode):
                self.mode = mode
                self.method_calls = []
                self.observer_added = False
                self.timer_created = False
                self.registered_callback = None
                self.render_window = self  # iren lookup uses plotter.render_window
                self._interactor = _FakeInteractor(self)

            def add_timer_event(self, *args, **kwargs):
                self.method_calls.append(("add_timer_event", args, kwargs))
                if self.mode == "vtek":
                    # No timer API available — force VTK fallback.
                    raise AttributeError("no add_timer_event")
                if self.mode == "pyvista_signature_mismatch":
                    # Signature changed out from under us — force VTK fallback.
                    raise TypeError("unexpected keyword argument 'duration'")
                self.registered_callback = kwargs.get("callback")

            def GetInteractor(self):
                return self._interactor

        fp = _FakePlotter(register_as)
        return fp

    def _invoke_registered_callback(self, fake_plotter, register_as, pyvista_args):
        """Simulate the timer invoking the registered callback.

        For the PyVista path the callback was stored by ``add_timer_event``
        and receives ``(step,)``; for the VTK path it was stored via
        ``AddObserver("TimerEvent", ...)`` and receives ``(caller, event)``.
        """
        if register_as in ("vtek", "pyvista_signature_mismatch"):
            assert fake_plotter.observer_added, "VTK observer was not added"
            event, cb = fake_plotter._interactor._observers[0]
            assert event == "TimerEvent"
            # VTK passes (caller, event) — no step meaning.
            return cb("vtk_caller", "TimerEvent")

        assert fake_plotter.registered_callback is not None, "callback not registered"
        return fake_plotter.registered_callback(*pyvista_args)

    def test_one_arg_callback_receives_step(self):
        """A ``callback(step)`` receives the step PyVista supplies."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)
            return step

        fp = self._make_fake_plotter("pyvista")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        out = self._invoke_registered_callback(fp, "pyvista", (3,))
        assert out == 3
        assert calls == [3]

    def test_zero_arg_callback_ignores_step(self):
        """A ``callback()`` (pushover timer) tolerates the step argument.

        PyVista always passes ``step``; the adapter truncates it so the
        zero-argument pushover callback does not raise ``TypeError``.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback():
            calls.append(1)

        fp = self._make_fake_plotter("pyvista")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "pyvista", (3,))
        assert calls == [1]

    def test_varargs_callback_receives_step(self):
        """A ``callback(*args)`` absorbs the step into its varargs."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        received = []

        def callback(*args):
            received.append(args)
            return args

        fp = self._make_fake_plotter("pyvista")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        out = self._invoke_registered_callback(fp, "pyvista", (5,))
        assert out == (5,)
        assert received == [(5,)]

    def test_one_arg_callback_signature_mismatch_falls_back_to_vtk(self):
        """``add_timer_event`` raising ``TypeError`` falls back to VTK."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)

        fp = self._make_fake_plotter("pyvista_signature_mismatch")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        assert fp.observer_added, "VTK fallback was not used"
        self._invoke_registered_callback(fp, "pyvista_signature_mismatch", ())
        assert calls == [1], f"Expected internal step counter, got {calls}"

    def test_one_arg_callback_no_args_invocation(self):
        """A zero-argument timer invocation still supplies a step count.

        No supported PyVista does this — the rule is defensive only — but a
        ``callback(step)`` must not be left without its argument.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)

        fp = self._make_fake_plotter("pyvista")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "pyvista", ())
        assert calls == [1], f"Expected internal step counter, got {calls}"

    def test_vtk_fallback_supplies_incrementing_step(self):
        """VTK TimerEvent passes (caller, event) with no step count — a
        ``callback(step)`` must receive an internal incrementing counter so
        the sine-phase oscillation actually progresses."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback(step):
            calls.append(step)

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        # Internal counter increments on each tick (starts at 1).
        assert calls == [1, 2], f"Expected incrementing steps, got {calls}"

    def test_vtk_fallback_supplies_no_args_to_zero_arg_callback(self):
        """A zero-argument callback (pushover ``_timer_callback``) is invoked
        with **no** args on the VTK fallback path."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        calls = []

        def callback():
            calls.append(1)

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        assert calls == [1]

    def test_vtk_fallback_sets_repeating_timer(self):
        """VTK fallback creates the repeating timer with the interval."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        def callback(step):
            return step

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=33)
        assert fp.observer_added
        assert fp.timer_created
        assert fp.interval_ms == 33

    def test_two_arg_callback_vtk_fallback(self):
        """A ``callback(step, plotter)`` on the VTK fallback receives the
        internal step count plus ``None`` for the unused second parameter."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        received = []

        def callback(step, plotter):
            received.append((step, plotter))
            return step

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        assert received == [(1, None), (2, None)], f"Unexpected args: {received}"

    def test_non_introspectable_callback_truncates_surplus_args(self):
        """A callback whose signature cannot be inspected (``inspect.signature``
        raises, as for some C-bound callables) is treated as ``callback(step)``
        — PyVista's documented contract — so surplus args like
        ``(step, plotter)`` are truncated to ``(step,)`` instead of raising
        ``TypeError``."""
        from unittest.mock import patch

        from fea_toolkit.plotting.viz import _add_animation_timer

        received = []

        def callback(step):
            received.append(step)
            return step

        # Force the non-introspectable fallback branch in _add_animation_timer.
        with patch("inspect.signature", side_effect=TypeError("no signature")):
            fp = self._make_fake_plotter("pyvista")
            _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
            out = self._invoke_registered_callback(fp, "pyvista", (3,))
        assert out == 3
        assert received == [3]

    def test_varargs_callback_with_required_step_vtk_fallback(self):
        """A ``callback(step, *extra)`` on the VTK fallback receives the
        internal step count with ``extra`` empty; the ``(caller, event)``
        pair is discarded rather than forwarded into the varargs."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        received = []

        def callback(step, *extra):
            received.append((step, extra))

        fp = self._make_fake_plotter("vtek")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        self._invoke_registered_callback(fp, "vtek", ("caller", "TimerEvent"))
        assert received == [(1, ())]

    def test_returns_true_when_pyvista_timer_registered(self):
        """PyVista's timer renders for us, so the helper reports ``True``."""
        from fea_toolkit.plotting.viz import _add_animation_timer

        def callback(step):
            return step

        fp = self._make_fake_plotter("pyvista")
        assert _add_animation_timer(fp, callback, max_steps=10, interval_ms=17) is True

    def test_returns_false_on_vtk_fallback(self):
        """The raw VTK observer never renders, so the helper reports ``False``.

        The caller must then drive ``plotter.render()`` from its callback,
        otherwise the animation only repaints on user interaction.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        def callback(step):
            return step

        fp = self._make_fake_plotter("vtek")
        assert _add_animation_timer(fp, callback, max_steps=10, interval_ms=17) is False
        assert fp.observer_added
        assert fp.timer_created

    def test_modern_pyvista_uses_duration_kwarg(self):
        """The interval kwarg is spelled ``duration`` on modern PyVista.

        PyVista >= 0.44 declares ``add_timer_event(max_steps, duration,
        callback)``.  Passing ``interval`` instead raised ``TypeError`` and
        silently dropped the helper onto the VTK fallback path, which does
        not render — so the mode animation appeared frozen until the user
        clicked in the window.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        def callback(step):
            return step

        fp = self._make_fake_plotter("pyvista")
        _add_animation_timer(fp, callback, max_steps=10, interval_ms=17)
        assert len(fp.method_calls) == 1, fp.method_calls
        assert fp.method_calls[0][2].get("duration") == 17

    def test_real_pyvista_native_timer_is_used(self):
        """A real ``pv.Plotter`` takes PyVista's own (rendering) timer path.

        Behavioural replacement for the old signature introspection: if the
        helper passed a keyword PyVista does not accept (the historical
        ``interval=``), the real ``add_timer_event`` call would raise
        ``TypeError`` and the helper would fall back to the low-level VTK
        observer, returning ``False`` — the non-rendering path that froze the
        mode animation.  ``True`` therefore means PyVista owns the timer.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        pv = pytest.importorskip("pyvista")

        def callback(step):
            return step

        plotter = pv.Plotter(off_screen=True)
        try:
            assert _add_animation_timer(plotter, callback, max_steps=10, interval_ms=17) is True
        finally:
            plotter.close()

    def test_real_pyvista_timer_passes_step_and_renders_each_frame(self):
        """Driving PyVista's own timer: the callback gets ``step``, frames render.

        Behavioural replacement for the old source-text assertion.  Firing a
        ``TimerEvent`` through the real interactor exercises upstream's own
        timer, which invokes the callback with its step count and renders a
        frame per invocation.  That is exactly the contract
        ``plot_mode_animation`` relies on when it skips ``plotter.render()`` —
        it only renders when the helper reports ``False``.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        pv = pytest.importorskip("pyvista")

        steps = []
        frames = []

        def callback(step):
            steps.append(step)

        plotter = pv.Plotter(off_screen=True)
        try:
            plotter.add_mesh(pv.Sphere())
            assert _add_animation_timer(plotter, callback, max_steps=3, interval_ms=10) is True

            render_window = plotter.render_window
            # vtkRenderWindow fires StartEvent once per Render() call.
            render_window.AddObserver("StartEvent", lambda *_: frames.append(1))

            render_window.GetInteractor().InvokeEvent("TimerEvent")

            # Zero-based and contiguous — one step per timer tick, whether a
            # release executes a single step per event or drains ``max_steps``
            # within one event.
            assert steps, "PyVista's timer never invoked the callback"
            assert steps == list(range(len(steps))), f"unexpected step sequence: {steps}"
            # PyVista rendered every frame itself — one render per step.
            assert len(frames) == len(steps), f"{len(frames)} frames for {len(steps)} steps"
        finally:
            plotter.close()

    def test_pyvista_signature_mismatch_falls_back_to_vtk(self):
        """A ``TypeError`` from the documented call falls back to VTK.

        Exactly one PyVista attempt is made — no guessing at alternative
        keyword names, which is how the original ``interval=`` bug hid: the
        bogus call raised, a later attempt "succeeded" via an API that does
        not exist, and the non-rendering VTK path was used in silence.
        """
        from fea_toolkit.plotting.viz import _add_animation_timer

        def callback(step):
            return step

        fp = self._make_fake_plotter("pyvista_signature_mismatch")
        assert _add_animation_timer(fp, callback, max_steps=10, interval_ms=17) is False
        assert fp.observer_added
        assert fp.timer_created
        assert len(fp.method_calls) == 1, fp.method_calls
        assert fp.method_calls[0][2].get("duration") == 17
