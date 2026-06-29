"""Tests for RecordingOpenSees proxy."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import openseespy.opensees as _real
import pytest

from fea_toolkit.opensees.recorder import RecordingOpenSees


class TestRecordingOpenSees:
    """Verify the recording proxy captures, exports, and stays optional."""

    def test_records_commands(self):
        """Basic recording captures name, args, and kwargs."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        rec.model("basic", "-ndm", 3, "-ndf", 6)
        rec.node(1, 0.0, 0.0, 0.0)
        rec.fix(1, 1, 1, 1, 1, 1, 1)

        assert len(rec.commands) == 4
        assert rec.commands[0][0] == "wipe"
        assert rec.commands[1] == ("model", ("basic", "-ndm", 3, "-ndf", 6), {})
        assert rec.commands[2] == ("node", (1, 0.0, 0.0, 0.0), {})
        _real.wipe()

    def test_clear_empties_commands(self):
        """Clear discards all recorded commands."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        assert len(rec.commands) == 1
        rec.clear()
        assert len(rec.commands) == 0

    def test_commands_are_copied(self):
        """The .commands property returns a copy, not the internal list."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        cmds = rec.commands
        cmds.clear()
        assert len(rec.commands) == 1  # internal list unchanged

    def test_non_callable_pass_through(self):
        """Non-callable attributes (e.g. constants) pass through unwrapped."""
        rec = RecordingOpenSees(_real)
        # Access a known non-callable (__name__ exists on any module)
        assert rec.__name__ == getattr(_real, "__name__", "openseespy.opensees")

    def test_forward_executes_real_ops(self):
        """Commands forwarded to the real module execute for real."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        rec.model("basic", "-ndm", 3, "-ndf", 6)
        rec.node(1, 0.0, 0.0, 0.0)
        # Node should actually exist in OpenSees memory
        import openseespy.opensees as ops
        coords = ops.nodeCoord(1)
        assert list(coords) == [0.0, 0.0, 0.0]
        _real.wipe()

    def test_save_as_python_creates_executable_script(self, tmp_path):
        """save_as_python writes a valid script that runs."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        rec.model("basic", "-ndm", 3, "-ndf", 6)
        rec.node(1, 0.0, 0.0, 0.0)
        rec.node(2, 6.0, 0.0, 0.0)

        out = tmp_path / "test_model.py"
        rec.save_as_python(str(out))
        content = out.read_text()

        # Validate content
        assert "import openseespy.opensees as ops" in content
        assert "def build_model():" in content
        assert 'ops.wipe()' in content
        assert "ops.node(1, 0, 0, 0)" in content

        # Actually run it
        import subprocess
        ret = subprocess.run(
            [sys.executable, str(out)],
            capture_output=True, text=True,
        )
        assert ret.returncode == 0, f"Script failed: {ret.stderr}"
        assert "Model built successfully" in ret.stdout
        _real.wipe()

    def test_save_as_tcl_creates_tcl_script(self, tmp_path):
        """save_as_tcl writes a valid Tcl script."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        rec.model("basic", "-ndm", 3, "-ndf", 6)
        rec.node(1, 0.0, 0.0, 0.0)

        out = tmp_path / "test_model.tcl"
        rec.save_as_tcl(str(out))
        content = out.read_text()

        assert "wipe" in content
        assert "model basic -ndm 3 -ndf 6" in content
        assert "node 1 0 0 0" in content

    def test_save_as_python_custom_func_name(self, tmp_path):
        """Custom function name is used in generated Python."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        out = tmp_path / "custom.py"
        rec.save_as_python(str(out), func_name="run_model")
        assert "def run_model():" in out.read_text()

    def test_numpy_values_are_clean(self, tmp_path):
        """NumPy types are serialised to plain Python literals."""
        import numpy as np
        rec = RecordingOpenSees(_real)
        # Manually inject commands with numpy types to test serialisation
        # without forwarding to the real C++ engine (which chokes on numpy types).
        object.__setattr__(rec, "_commands", [
            ("wipe", (), {}),
            ("load", (1, np.float64(-10.5), np.int32(3), 0.0), {}),
        ])
        rec.save_as_python(str(tmp_path / "npy.py"))
        content = (tmp_path / "npy.py").read_text()
        assert "np" not in content  # no numpy dependency
        assert "ops.load(1, -10.5, 3, 0)" in content

        rec.save_as_tcl(str(tmp_path / "npy.tcl"))
        tcl = (tmp_path / "npy.tcl").read_text()
        assert "load 1 -10.5 3 0" in tcl

    def test_args_are_snapshotted(self):
        """Mutating an argument after the call does not affect recorded command."""
        import numpy as np
        rec = RecordingOpenSees(_real)
        rec.wipe()
        rec.model("basic", "-ndm", 3, "-ndf", 6)

        # Pass a mutable list, then mutate it
        coords = [1.0, 2.0, 3.0]
        rec.node(1, *coords)
        coords[0] = 99.0  # mutate after the call

        cmd = rec.commands[-1]
        assert cmd == ("node", (1, 1.0, 2.0, 3.0), {})

    def test_kwargs_are_snapshotted(self):
        """Mutating a kwarg dict after the call does not affect recorded command.

        This test uses a call that will fail at the C++ layer (unknown kwarg),
        but the recorder's wrapper records *before* forwarding, so the command
        is still captured with its original kwargs.
        """
        rec = RecordingOpenSees(_real)
        rec.wipe()
        d = {"key": "value"}
        try:
            rec.model("basic", "-ndm", 3, "-ndf", 6, **d)
        except Exception:
            pass
        d["key"] = "mutated"

        assert len(rec.commands) >= 2  # wipe + model
        cmd = rec.commands[-1]
        assert cmd[0] == "model"
        assert cmd[2] == {"key": "value"}  # snapshot, not "mutated"

    def test_save_as_python_validates_func_name(self, tmp_path):
        """Non-identifier or keyword func_name raises ValueError."""
        rec = RecordingOpenSees(_real)
        rec.wipe()

        out = tmp_path / "m.py"
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            rec.save_as_python(str(out), func_name="123bad")
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            rec.save_as_python(str(out), func_name="my func")
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            rec.save_as_python(str(out), func_name="")

    def test_save_as_python_rejects_keywords(self, tmp_path):
        """Python keywords are rejected as func_name."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        out = tmp_path / "m.py"
        with pytest.raises(ValueError, match="Python keyword"):
            rec.save_as_python(str(out), func_name="for")
        with pytest.raises(ValueError, match="Python keyword"):
            rec.save_as_python(str(out), func_name="class")
        with pytest.raises(ValueError, match="Python keyword"):
            rec.save_as_python(str(out), func_name="return")

    def test_save_as_python_valid_names_work(self, tmp_path):
        """Valid identifiers like 'build_model' and 'run' are accepted."""
        rec = RecordingOpenSees(_real)
        rec.wipe()
        out = tmp_path / "m.py"
        # Should not raise
        rec.save_as_python(str(out), func_name="build_model")
        rec.save_as_python(str(out), func_name="run")
        rec.save_as_python(str(out), func_name="_helper")
        rec.save_as_python(str(out), func_name="main")

    def test_no_recording_without_swap(self):
        """Builder works normally without recorder (no side effects)."""
        from fea_toolkit.opensees.builder import OpenSeesBuilder
        from fea_toolkit.model.sap_data import (
            SAPModelData, Node, Material, Restraint, Section, FrameElement,
        )
        md = SAPModelData(
            nodes={
                "1": Node("1", 1, 0, 0, 0),
                "2": Node("2", 2, 6, 0, 0),
            },
            materials={
                "Concrete": Material("Concrete", "Concrete", E_mod=3e10),
            },
            sections={
                "Col600": Section(
                    name="Col600", shape="Rectangular",
                    material="Concrete", A=0.36, I33=0.0108, I22=0.0108, J=0.018,
                ),
            },
            frame_elements={
                "1": FrameElement("1", 1, "1", "2"),
            },
            frame_assignments={
                "1": "Col600",
            },
            restraints={
                "1": Restraint([1, 1, 1, 1, 1, 1]),
            },
            # Unused empties
            area_elements={}, area_assignments={},
            groups={}, frame_auto_mesh={},
        )
        # Use elastic sections to avoid needing fiber-patch support.
        b = OpenSeesBuilder(md, {
            "verbose": False, "use_elastic_sections": True,
        })
        b.build()

        # Verify the builder created nodes in OpenSees memory.
        import openseespy.opensees as ops
        assert list(ops.nodeCoord(1)) == [0.0, 0.0, 0.0]
        assert list(ops.nodeCoord(2)) == [6.0, 0.0, 0.0]
        ops.wipe()
