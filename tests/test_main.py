"""Tests for the ``python -m fea_toolkit`` package entry point (``__main__``)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import fea_toolkit.__main__ as cli
from fea_toolkit.__main__ import describe_public_api, main

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def _rows_by_name() -> dict[str, tuple[str, str]]:
    """Return the lazy listing keyed by public name."""
    return {name: (kind, module) for name, kind, module in describe_public_api()}


class TestDescribePublicApi:
    def test_covers_every_public_name(self):
        import fea_toolkit

        names = [name for name, _, _ in describe_public_api()]
        assert names == sorted(fea_toolkit.__all__)

    def test_classifies_eager_names(self):
        rows = _rows_by_name()
        assert rows["Node"][0] == "class"
        assert rows["SAPModelData"][0] == "class"
        assert rows["Selection"][0] == "class"
        assert rows["ops_version"][0] == "function"
        assert rows["__version__"][0] == "value"

    def test_classifies_lazy_names(self):
        rows = _rows_by_name()
        assert rows["AnalysisBuilder"][0] == "class"
        assert rows["SAP2000Parser"][0] == "class"
        assert rows["preprocess_model"][0] == "function"

    def test_resolves_reexports_to_defining_module(self):
        """Facade re-exports resolve to the module that defines them."""
        rows = _rows_by_name()
        assert rows["plot_mesh"][1] == "fea_toolkit.plotting.viz_model"
        assert rows["plot_capacity_spectrum"][1] == "fea_toolkit.plotting.viz_pushover"

    def test_resolves_aliased_reexport(self, tmp_path, monkeypatch):
        """An aliased ``from .impl import original as renamed`` re-export
        resolves to the defining module via the original imported name."""
        root = tmp_path / "fea_toolkit"
        pkg = root / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "impl.py").write_text("def original():\n    pass\n", encoding="utf-8")
        (pkg / "facade.py").write_text("from .impl import original as renamed\n", encoding="utf-8")
        monkeypatch.setattr(cli, "_package_root", lambda: root)
        monkeypatch.setattr(cli, "_MODULE_CACHE", {})

        resolved = cli._resolve("fea_toolkit.pkg.facade", "renamed")

        assert resolved.kind == "function"
        assert resolved.module == "fea_toolkit.pkg.impl"
        assert resolved.node is not None and resolved.node.name == "original"

    def test_modules_are_within_package(self):
        for _, _, module in describe_public_api():
            assert module == "fea_toolkit" or module.startswith("fea_toolkit.")


class TestMain:
    def test_lazy_listing_prints_table(self, capsys):
        assert main([]) == 0
        out = capsys.readouterr().out
        assert "public names" in out
        assert "NAME" in out
        assert "plot_mesh" in out
        assert "Tip: pass --details" in out

    def test_details_listing_prints_signatures(self, capsys):
        # ``Node`` is an eager, backend-independent name, so ``--details``
        # exercises signatures and docstrings without importing the optional
        # openseespy / pyvista-backed names.
        assert main(["Node", "--details"]) == 0
        out = capsys.readouterr().out
        assert "signature:" in out
        assert "doc:" in out

    def test_version_flag(self, capsys):
        import fea_toolkit

        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert out.strip() == f"fea_toolkit {fea_toolkit.__version__}"


class TestEntryPointLaziness:
    def test_lazy_listing_needs_no_backends(self):
        code = (
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "from fea_toolkit.__main__ import describe_public_api, main; "
            "assert describe_public_api(); "
            "assert 'openseespy' not in sys.modules, 'openseespy loaded'; "
            "assert 'pyvista' not in sys.modules, 'pyvista loaded'; "
            "assert main([]) == 0; "
            "print('clean')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout

    def test_module_invocation(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
        out = subprocess.run(
            [sys.executable, "-m", "fea_toolkit"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
        assert out.returncode == 0, out.stderr
        assert "plot_mesh" in out.stdout


class TestNameFilter:
    def test_exact_name(self, capsys):
        assert main(["plot_mesh"]) == 0
        out = capsys.readouterr().out
        assert "plot_mesh" in out
        assert "AnalysisBuilder" not in out
        assert "1 public name" in out

    def test_substring_matches_several(self, capsys):
        assert main(["plot_"]) == 0
        out = capsys.readouterr().out
        assert "plot_mesh" in out
        assert "plot_capacity_spectrum" in out
        assert "run_modal" not in out

    def test_glob_pattern(self, capsys):
        assert main(["plot_*_view*"]) == 0
        out = capsys.readouterr().out
        assert "plot_building_views" in out

    def test_no_match_returns_2(self, capsys):
        assert main(["definitely_not_a_name"]) == 2
        assert "no public name matches" in capsys.readouterr().err


class TestSourceMode:
    def test_source_for_lazy_name(self, capsys):
        assert main(["plot_mesh", "--source"]) == 0
        out = capsys.readouterr().out
        assert "=== plot_mesh [function] (fea_toolkit.plotting.viz_model) ===" in out
        assert "def plot_mesh(" in out

    def test_source_includes_decorator(self, capsys):
        assert main(["Node", "--source"]) == 0
        out = capsys.readouterr().out
        assert "@dataclass" in out
        assert "class Node:" in out

    def test_source_requires_name(self):
        with pytest.raises(SystemExit):
            main(["--source"])

    def test_source_stays_lazy(self):
        code = (
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "from fea_toolkit.__main__ import main; "
            "assert main(['plot_mesh', '--source']) == 0; "
            "assert 'openseespy' not in sys.modules, 'openseespy loaded'; "
            "assert 'pyvista' not in sys.modules, 'pyvista loaded'; "
            "print('clean')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout
