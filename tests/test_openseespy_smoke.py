"""Smoke test: verify_openseespy.py runs without error."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
SCRIPT = EXAMPLES_DIR / "verify_openseespy.py"

openseespy_available = importlib.util.find_spec("openseespy") is not None

pytestmark = pytest.mark.skipif(
    not openseespy_available,
    reason="openseespy not installed",
)


def test_openseespy_smoke_quick():
    """Quick smoke-test: setup + gravity only."""
    assert SCRIPT.exists(), f"{SCRIPT} not found"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--quick"],
        capture_output=True, text=True,
        timeout=120,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
    assert result.returncode == 0, (
        f"verify_openseespy.py --quick failed with exit code {result.returncode}"
    )
    assert "Quick verification passed" in result.stdout


def test_openseespy_smoke_full():
    """Full smoke-test: setup + gravity + pushover (slower, ~300 steps)."""
    assert SCRIPT.exists(), f"{SCRIPT} not found"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
        timeout=600,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
    assert result.returncode == 0, (
        f"verify_openseespy.py failed with exit code {result.returncode}"
    )
    assert "All 14 checks passed" in result.stdout
