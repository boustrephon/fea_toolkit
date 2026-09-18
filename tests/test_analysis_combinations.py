"""Tests for load-combination result expansion (``analysis.combinations``).

Verifies that parsed combinations are turned into composite result payloads
in the same shape ``AnalysisBuilder.export_results`` consumes, and that the
``expand_combinations`` opt-in merges them into ``static_results``.
"""

import pytest

from fea_toolkit.analysis.combinations import build_combination_results
from fea_toolkit.model.load_combinations import classify_combination_refs
from fea_toolkit.model.sap_data import (
    LoadCase,
    LoadCombination,
    LoadCombinationEntry,
    SAPModelData,
)
from fea_toolkit.opensees._runner_static import StaticRunnerMixin

# ── Fixtures ───────────────────────────────────────────────────────────


def _case(name, case_type="LinStatic"):
    return LoadCase(name, case_type, "Prog Det", "Dead", "Prog Det", "Non-Composite")


def _combo(name, combo_type, refs):
    return LoadCombination(name, combo_type, [LoadCombinationEntry(n, f) for n, f in refs])


def _model():
    """Minimal SAPModelData carrying load cases and combinations."""
    cases = {
        "DEAD": _case("DEAD"),
        "SDL": _case("SDL"),
        "RSX": _case("RSX", "LinRespSpec"),
    }
    combos = {
        "GRAV": _combo("GRAV", "Linear Add", [("DEAD", 1.2), ("SDL", 1.5)]),
        "SEISM": _combo("SEISM", "Linear Add", [("DEAD", 1.0), ("RSX", 1.0)]),
        "ENV": _combo("ENV", "Envelope", [("DEAD", 1.0), ("SDL", 1.0)]),
    }
    classify_combination_refs(combos, cases)
    md = SAPModelData(
        nodes={},
        restraints={},
        materials={},
        sections={},
        frame_elements={},
        area_elements={},
        frame_assignments={},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )
    md.load_cases = cases
    md.load_combinations = combos
    return md


def _results():
    return {
        "DEAD": {
            "nodal_displacements": {1: [0.0, 0.0, -0.01], 2: [0.0, 0.0, -0.02]},
            "element_forces": {"fx_i": [10.0, 20.0], "mz_i": [1.0, 2.0]},
            "converged": True,
        },
        "SDL": {
            "nodal_displacements": {1: [0.0, 0.0, -0.005], 2: [0.0, 0.0, -0.001]},
            "element_forces": {"fx_i": [1.0, 2.0], "mz_i": [0.5, 0.4]},
            "converged": True,
        },
        "RSX": {
            "nodal_displacements": {1: [0.02, 0.0, 0.0], 2: [0.01, 0.0, 0.0]},
            "element_forces": {"fx_i": [5.0, 5.0], "mz_i": [3.0, 3.0]},
            "converged": True,
        },
    }


# ── Expansion ──────────────────────────────────────────────────────────


def test_linear_combination_scales_and_sums():
    out = build_combination_results(_results(), model=_model(), combinations=["GRAV"])
    assert set(out) == {"GRAV"}
    payload = out["GRAV"]
    assert payload["element_forces"]["fx_i"] == pytest.approx(
        [10 * 1.2 + 1 * 1.5, 20 * 1.2 + 2 * 1.5]
    )
    assert payload["nodal_displacements"][1] == pytest.approx([0.0, 0.0, -0.01 * 1.2 - 0.005 * 1.5])
    # Node tags / nested keys are preserved, non-numeric fields dropped.
    assert sorted(payload["nodal_displacements"]) == [1, 2]
    assert "converged" not in payload


def test_spectrum_forks_into_two_composites():
    out = build_combination_results(_results(), model=_model(), combinations=["SEISM"])
    assert set(out) == {"SEISM #1", "SEISM #2"}
    assert out["SEISM #1"]["nodal_displacements"][1] == pytest.approx([0.02, 0.0, -0.01])
    assert out["SEISM #2"]["nodal_displacements"][1] == pytest.approx([-0.02, 0.0, -0.01])


def test_envelope_maxmin_payload():
    out = build_combination_results(_results(), model=_model(), combinations=["ENV"])
    assert set(out) == {"ENV [max]", "ENV [min]"}
    assert out["ENV [max]"]["element_forces"]["fx_i"] == pytest.approx([10.0, 20.0])
    assert out["ENV [min]"]["element_forces"]["fx_i"] == pytest.approx([1.0, 2.0])


def test_envelope_per_path():
    out = build_combination_results(
        _results(), model=_model(), combinations=["ENV"], envelope_mode="per_path"
    )
    assert set(out) == {"ENV [DEAD]", "ENV [SDL]"}


def test_default_expands_every_combination():
    out = build_combination_results(_results(), model=_model())
    assert {"GRAV", "SEISM #1", "SEISM #2", "ENV [max]", "ENV [min]"} <= set(out)


def test_explicit_mappings_without_model():
    md = _model()
    out = build_combination_results(
        _results(),
        load_cases=md.load_cases,
        load_combinations=md.load_combinations,
        combinations=["GRAV"],
    )
    assert set(out) == {"GRAV"}


# ── Error handling ─────────────────────────────────────────────────────


def test_missing_case_raises():
    md = _model()
    with pytest.raises(KeyError, match="no supplied values"):
        build_combination_results({"DEAD": _results()["DEAD"]}, model=md, combinations=["GRAV"])


def test_unknown_combination_raises():
    with pytest.raises(KeyError, match="Unknown load combination"):
        build_combination_results(_results(), model=_model(), combinations=["NOPE"])


def test_requires_a_source():
    with pytest.raises(ValueError, match="needs model="):
        build_combination_results(_results())


# ── export_results wiring ──────────────────────────────────────────────


class _StubBuilder(StaticRunnerMixin):
    """Minimal host for the mixin method — no OpenSees domain needed."""

    def __init__(self):
        self.mesh_model = None
        self.config: dict = {}


def test_export_requires_model_for_expansion():
    with pytest.raises(ValueError, match="requires model="):
        _StubBuilder().export_results("x.npz", expand_combinations=True)


def test_export_results_expands_combinations(monkeypatch, tmp_path):
    captured = {}

    def fake_write_results(**kwargs):
        captured.update(kwargs)
        return str(tmp_path / "out.npz")

    monkeypatch.setattr("fea_toolkit.io.unified_writer.write_results", fake_write_results)

    _StubBuilder().export_results(
        str(tmp_path / "out.npz"),
        static_results=_results(),
        model=_model(),
        expand_combinations=True,
    )

    merged = captured["static_results"]
    # Load cases survive alongside the generated composites.
    assert {"DEAD", "SDL", "RSX"} <= set(merged)
    assert {"GRAV", "SEISM #1", "SEISM #2", "ENV [max]", "ENV [min]"} <= set(merged)
    assert merged["GRAV"]["element_forces"]["fx_i"] == pytest.approx([13.5, 27.0])


def test_export_results_without_expansion_is_unchanged(monkeypatch, tmp_path):
    captured = {}

    def fake_write_results(**kwargs):
        captured.update(kwargs)
        return str(tmp_path / "out.npz")

    monkeypatch.setattr("fea_toolkit.io.unified_writer.write_results", fake_write_results)

    cases = _results()
    _StubBuilder().export_results(str(tmp_path / "out.npz"), static_results=cases)
    assert captured["static_results"] is cases
    assert "GRAV" not in captured["static_results"]
