"""Tests for the out-of-process OpenSees runtime resolver and exchange codec."""

from __future__ import annotations

import json

import numpy as np
import pytest

from fea_toolkit.opensees import runtime
from fea_toolkit.opensees._exchange import nested_from_jsonable, nested_to_jsonable


class TestOpenseesAvailable:
    def test_false_when_find_spec_raises_import_error(self, monkeypatch):
        import importlib.util

        def _raising(_name):
            raise ImportError("excluded")

        monkeypatch.setattr(importlib.util, "find_spec", _raising)
        assert runtime.opensees_available() is False

    def test_true_when_find_spec_resolves(self, monkeypatch):
        import importlib.util

        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
        assert runtime.opensees_available() is True


class TestResolveOpenseesPython:
    def test_explicit_value_wins(self, monkeypatch):
        monkeypatch.setenv(runtime.ENV_VAR, "/env/python")
        assert runtime.resolve_opensees_python("/explicit/python") == "/explicit/python"

    def test_env_var_wins_over_autodetect(self, monkeypatch):
        monkeypatch.setenv(runtime.ENV_VAR, "/env/python")
        monkeypatch.setattr(runtime, "opensees_available", lambda: True)
        assert runtime.resolve_opensees_python(None) == "/env/python"

    def test_autodetect_uses_this_interpreter_when_opensees_present(self, monkeypatch):
        monkeypatch.delenv(runtime.ENV_VAR, raising=False)
        monkeypatch.setattr(runtime, "opensees_available", lambda: True)
        monkeypatch.setattr(runtime.sys, "executable", "/this/python")
        assert runtime.resolve_opensees_python(None) == "/this/python"

    def test_autodetect_falls_back_to_python3_when_absent(self, monkeypatch):
        monkeypatch.delenv(runtime.ENV_VAR, raising=False)
        monkeypatch.setattr(runtime, "opensees_available", lambda: False)
        monkeypatch.setattr(
            runtime.shutil,
            "which",
            lambda name: "/usr/bin/python3" if name == "python3" else None,
        )
        assert runtime.resolve_opensees_python(None) == "/usr/bin/python3"

    def test_a_virtualenv_directory_is_resolved_to_its_interpreter(self, tmp_path):
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        interpreter = venv_bin / "python"
        interpreter.write_text("")
        interpreter.chmod(0o755)

        resolved = runtime.resolve_opensees_python(str(tmp_path / "venv"))
        assert resolved == str(interpreter)

    def test_a_non_directory_value_passes_through_unchanged(self):
        assert runtime.resolve_opensees_python("python3") == "python3"


class TestShouldDelegate:
    def test_delegates_only_when_opensees_absent(self, monkeypatch):
        monkeypatch.setattr(runtime, "opensees_available", lambda: False)
        assert runtime.should_delegate() is True
        monkeypatch.setattr(runtime, "opensees_available", lambda: True)
        assert runtime.should_delegate() is False


class TestRunPython:
    def test_streams_stdout_and_stderr_lines_to_the_log(self):
        seen: list = []
        code, out = runtime.run_python(
            runtime.sys.executable,
            ["-c", "print('one'); import sys; print('two', file=sys.stderr)"],
            log=seen.append,
        )
        assert code == 0
        assert "one" in seen
        assert "two" in seen  # stderr is merged into stdout
        assert "one" in out and "two" in out

    def test_nonzero_exit_is_reported(self):
        code, _out = runtime.run_python(runtime.sys.executable, ["-c", "import sys; sys.exit(3)"])
        assert code == 3


class TestExchangeCodec:
    def test_round_trips_nested_numpy_payloads(self):
        payload = {
            "DEAD": {
                "nodal_displacements": {"1": np.array([1.5, 2.5, 3.5])},
                "element_forces": {"E1": {"fx_i": np.array([1.0, -1.0])}},
                "count": np.int64(3),
            },
            "applied": [1.0, 2.0],
            "name": "DEAD",
            "flag": True,
            "none": None,
        }
        encoded = json.loads(json.dumps(nested_to_jsonable(payload)))
        decoded = nested_from_jsonable(encoded)

        np.testing.assert_allclose(decoded["DEAD"]["nodal_displacements"]["1"], [1.5, 2.5, 3.5])
        np.testing.assert_allclose(decoded["DEAD"]["element_forces"]["E1"]["fx_i"], [1.0, -1.0])
        assert decoded["DEAD"]["count"] == 3
        assert decoded["applied"] == [1.0, 2.0]
        assert decoded["name"] == "DEAD"
        assert decoded["flag"] is True
        assert decoded["none"] is None

    def test_rejects_unrecognised_types(self):
        class _Weird:
            pass

        with pytest.raises(TypeError):
            nested_to_jsonable({"x": _Weird()})
