"""Tests for the atomic text writer (``gui/controllers/_atomic.py``)."""

import os

import pytest

from fea_toolkit.gui.controllers._atomic import atomic_write_text


def test_atomic_write_replaces_the_target_and_leaves_no_temp(tmp_path):
    target = tmp_path / "gui.json"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gui.json"]


def test_atomic_write_removes_the_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "gui.json"
    target.write_text("old")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    assert target.read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gui.json"]
