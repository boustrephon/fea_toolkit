"""Tests for the interactive file-selection helpers (``fea_toolkit.io.helper``).

The dialogs themselves block, so the pure command-building helpers are
asserted directly and the dialog backends are stubbed.  ``tkinter`` is
imported lazily so the module still collects on interpreters built without
Tk.
"""

import subprocess
import sys

import pytest

from fea_toolkit.io import helper

# ═══════════════════════════════════════════════════════════════════
# Pinned pre-``file_types`` behaviour
# ═══════════════════════════════════════════════════════════════════

#: The exact ``osascript`` command the JSON default produced before the
#: ``file_types`` / ``prompt`` arguments were introduced.
LEGACY_JSON_OSASCRIPT = 'osascript -e \'POSIX path of (choose file with prompt "Select SAP2000 JSON file to parse" of type {"json", "JSON", "txt"})\''

#: The ``filetypes`` rows the tkinter dialog has always shown by default.
LEGACY_JSON_TK_FILETYPES = [
    ("SAP2000 JSON files", "*.json"),
    ("SAP2000 files", "*.JSON"),
    ("Text files", "*.txt"),
    ("All files", "*.*"),
]

#: The extension set ``examples/basic_usage.py`` asks for when picking a model.
MODEL_FILE_TYPES = ("s2k", "S2K", "$2k", "json", "JSON")


# ═══════════════════════════════════════════════════════════════════
# Defaults
# ═══════════════════════════════════════════════════════════════════


class TestDefaults:
    def test_defaults_are_json_only(self):
        """Both choosers default to the JSON cache format."""
        assert helper.DEFAULT_FILE_TYPES == ("json", "JSON", "txt")
        assert helper.DEFAULT_PROMPT == "Select SAP2000 JSON file to parse"


# ═══════════════════════════════════════════════════════════════════
# tk_filetypes — tkinter dialog rows
# ═══════════════════════════════════════════════════════════════════


class TestTkFiletypes:
    def test_default_matches_legacy_rows(self):
        assert helper.tk_filetypes() == LEGACY_JSON_TK_FILETYPES

    def test_explicit_none_matches_legacy_rows(self):
        assert helper.tk_filetypes(None) == LEGACY_JSON_TK_FILETYPES

    def test_default_returns_independent_copy(self):
        rows = helper.tk_filetypes()
        rows.append(("junk", "*"))
        assert helper.tk_filetypes() == LEGACY_JSON_TK_FILETYPES

    def test_model_extensions_build_one_pattern_row(self):
        rows = helper.tk_filetypes(MODEL_FILE_TYPES)
        patterns = "*.s2k *.S2K *.$2k *.json *.JSON"
        assert rows == [
            (f"Model files ({patterns})", patterns),
            ("All files", "*.*"),
        ]

    def test_always_ends_with_all_files(self):
        for file_types in (None, MODEL_FILE_TYPES, ("$2k",)):
            assert helper.tk_filetypes(file_types)[-1] == ("All files", "*.*")


# ═══════════════════════════════════════════════════════════════════
# applescript_choose_file_cmd — macOS dialog command
# ═══════════════════════════════════════════════════════════════════


class TestApplescriptCommand:
    def test_default_command_is_unchanged(self):
        """The JSON default must stay byte-identical to the old literal."""
        assert helper.applescript_choose_file_cmd() == LEGACY_JSON_OSASCRIPT

    def test_custom_prompt_and_types(self):
        cmd = helper.applescript_choose_file_cmd(
            prompt="Pick a file", file_types=("s2k", "$2k", "json")
        )
        assert cmd.startswith("osascript -e '")
        assert cmd.endswith("'")
        assert 'prompt "Pick a file"' in cmd
        assert 'of type {"s2k", "$2k", "json"}' in cmd

    def test_dollar_extensions_survive_for_the_shell(self):
        """``$2k`` must reach AppleScript unexpanded by the shell."""
        cmd = helper.applescript_choose_file_cmd(file_types=("$2k",))
        assert '"$2k"' in cmd
        assert cmd.startswith("osascript -e '")

    def test_title_is_escaped_for_applescript_and_shell(self):
        """Quotes in the prompt cannot alter the AppleScript/shell syntax."""
        cmd = helper.applescript_choose_file_cmd(prompt='Say "hi"')
        assert cmd.startswith("osascript -e '")
        assert 'prompt "Say \\"hi\\""' in cmd


# ═══════════════════════════════════════════════════════════════════
# mac_file_chooser — stubbed subprocess
# ═══════════════════════════════════════════════════════════════════


class TestMacFileChooser:
    @pytest.fixture(autouse=True)
    def _force_darwin(self, monkeypatch):
        """Force ``sys.platform`` to ``darwin`` so the subprocess path runs.

        The off-macOS test overrides this with an explicit ``linux`` patch.
        """
        monkeypatch.setattr(sys, "platform", "darwin")

    def test_returns_stripped_path(self, monkeypatch):
        monkeypatch.setattr(subprocess, "check_output", lambda cmd, **kw: b"/tmp/model.s2k\n")
        assert helper.mac_file_chooser() == "/tmp/model.s2k"

    def test_custom_extensions_reach_the_command(self, monkeypatch):
        captured = {}

        def fake_check_output(cmd, **kwargs):
            captured["cmd"] = cmd
            return b"/tmp/m.s2k\n"

        monkeypatch.setattr(subprocess, "check_output", fake_check_output)
        result = helper.mac_file_chooser(file_types=("s2k", "$2k"), prompt="Pick a model")

        assert result == "/tmp/m.s2k"
        assert 'prompt "Pick a model"' in captured["cmd"]
        assert 'of type {"s2k", "$2k"}' in captured["cmd"]

    def test_cancel_returns_none(self, monkeypatch):
        def cancel(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "check_output", cancel)
        assert helper.mac_file_chooser() is None

    def test_cancel_verbose_reports(self, monkeypatch, capsys):
        def cancel(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "check_output", cancel)
        assert helper.mac_file_chooser(verbose=True) is None
        assert "cancel" in capsys.readouterr().out.lower()

    def test_returns_none_off_macos_without_calling_out(self, monkeypatch):
        def unexpected(*args, **kwargs):
            raise AssertionError("check_output must not run off macOS")

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(subprocess, "check_output", unexpected)
        assert helper.mac_file_chooser() is None


# ═══════════════════════════════════════════════════════════════════
# tkinter_file_chooser — stubbed Tk dialog
# ═══════════════════════════════════════════════════════════════════


class _FakeRoot:
    """Minimal stand-in for ``tkinter.Tk``; needs no display."""

    def withdraw(self):
        pass

    def update(self):
        pass

    def quit(self):
        pass

    def destroy(self):
        pass


@pytest.fixture
def fake_dialog(monkeypatch):
    """Stub the Tk root and file dialog, returning the capture dict.

    ``state["result"]`` scripts what the dialog returns; ``state["raise"]``
    scripts an exception instead (``None`` for success).
    """
    tkinter = pytest.importorskip("tkinter")
    filedialog = pytest.importorskip("tkinter.filedialog")

    state = {"result": "", "kwargs": {}, "raise": None}

    def fake_askopenfilename(**kwargs):
        state["kwargs"] = kwargs
        if state["raise"] is not None:
            raise state["raise"]
        return state["result"]

    monkeypatch.setattr(tkinter, "Tk", _FakeRoot)
    monkeypatch.setattr(filedialog, "askopenfilename", fake_askopenfilename)
    return state


@pytest.fixture
def tcl_error():
    """The ``TclError`` class; skips the test if Tk is unavailable."""
    tkinter = pytest.importorskip("tkinter")
    return tkinter.TclError("stubbed failure")


class TestTkinterFileChooser:
    def test_returns_selected_path(self, fake_dialog):
        fake_dialog["result"] = "/tmp/model.s2k"
        assert helper.tkinter_file_chooser() == "/tmp/model.s2k"

    def test_cancel_returns_none(self, fake_dialog):
        """Tk signals cancel with an empty string, not ``None``."""
        fake_dialog["result"] = ""
        assert helper.tkinter_file_chooser() is None

    def test_default_title_and_rows(self, fake_dialog):
        fake_dialog["result"] = "/tmp/m.json"
        helper.tkinter_file_chooser()
        assert fake_dialog["kwargs"]["title"] == helper.DEFAULT_PROMPT
        assert fake_dialog["kwargs"]["filetypes"] == LEGACY_JSON_TK_FILETYPES

    def test_custom_title_and_rows(self, fake_dialog):
        fake_dialog["result"] = "/tmp/m.s2k"
        helper.tkinter_file_chooser(file_types=MODEL_FILE_TYPES, prompt="Pick a model")
        assert fake_dialog["kwargs"]["title"] == "Pick a model"
        rows = fake_dialog["kwargs"]["filetypes"]
        assert rows[0][0].startswith("Model files")
        assert "*.$2k" in rows[0][1]

    def test_tcl_error_returns_none(self, fake_dialog, tcl_error):
        fake_dialog["raise"] = tcl_error
        assert helper.tkinter_file_chooser() is None

    def test_tcl_error_verbose_reports(self, fake_dialog, tcl_error, capsys):
        fake_dialog["raise"] = tcl_error
        assert helper.tkinter_file_chooser(verbose=True) is None
        assert "TclError" in capsys.readouterr().out
