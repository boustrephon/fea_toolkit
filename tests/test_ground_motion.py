"""Tests for ``io/ground_motion.py`` — canonical SI (m/s²) record handling.

Covers the H1 review fix: PEER ``.AT2`` records are converted from g to
m/s² at read time, and ``record_summary`` uses the SI gravitational
constant for Arias intensity (no hardcoded 9.81).
"""

import numpy as np
import pytest

from fea_toolkit.io.ground_motion import (
    GroundMotionManifest,
    load_manifest_registry,
    read_peer_record,
    record_summary,
    save_manifest_registry,
)
from fea_toolkit.utils import DEFAULT_GRAVITY_MS2


class TestReadPeerRecordUnits:
    """PEER .AT2 records are stored in g and converted to m/s² at read."""

    def test_g_to_ms2_conversion(self, tmp_path):
        f = tmp_path / "record.at2"
        f.write_text("NPTS=3\nDT=0.005\n0.1\n0.1\n0.1\n", encoding="utf-8")
        times, accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.005, 0.01])
        assert accel.tolist() == pytest.approx([0.1 * DEFAULT_GRAVITY_MS2] * 3)

    def test_dt_fallback_is_005(self, tmp_path):
        """A header without an explicit DT falls back to the PEER 0.005 s convention."""
        f = tmp_path / "no_dt.at2"
        f.write_text("0.2\n0.2\n0.2\n", encoding="utf-8")
        times, accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.005, 0.01])
        assert accel.tolist() == pytest.approx([0.2 * DEFAULT_GRAVITY_MS2] * 3)


class TestRecordSummaryUnits:
    """record_summary assumes m/s² input; Arias uses the SI constant."""

    def test_arias_constant_accel(self):
        # Constant a (m/s²) over duration T: ∫a² dt = a²·T.
        dt = 0.005
        a = 3.0  # m/s²
        n = 200
        times = np.arange(n, dtype=float) * dt
        accel = np.full(n, a)
        out = record_summary(times, accel)
        T = (n - 1) * dt
        expected_ai = (np.pi / (2.0 * DEFAULT_GRAVITY_MS2)) * a**2 * T
        assert out["ai"] == pytest.approx(expected_ai)

    def test_pgv_constant_accel(self):
        # vel[k] = dt·Σa = a·dt·(k+1); max at the last sample.
        dt = 0.005
        a = 3.0
        n = 200
        times = np.arange(n, dtype=float) * dt
        out = record_summary(times, np.full(n, a))
        assert out["pgv"] == pytest.approx(a * n * dt)

    def test_pga_is_max_abs(self):
        times = np.array([0.0, 0.01, 0.02])
        accel = np.array([0.0, -1.5, 0.0])
        assert record_summary(times, accel)["pga"] == pytest.approx(1.5)


class TestGroundMotionManifest:
    """``GroundMotionManifest`` is a plain provenance record with a JSON round-trip."""

    def test_to_dict_and_from_dict_round_trip(self):
        m = GroundMotionManifest(
            record_id="RSN6",
            provider="peer_nga_west2",
            event="Imperial Valley-02",
            station="El Centro Array #9",
            component="H1",
            source_url="https://ngawest2.berkeley.edu/",
            licence="PEER NGA-West2 user agreement",
            scale_factor=2.0,
            rotation="fault_normal",
        )
        assert GroundMotionManifest.from_dict(m.to_dict()) == m

    def test_defaults(self):
        m = GroundMotionManifest(
            record_id="X",
            provider="p",
            event="e",
            station="s",
            component="c",
            source_url="u",
        )
        assert m.licence == ""
        assert m.scale_factor == 1.0
        assert m.rotation == "as_recorded"
        assert m.note == ""


class TestManifestRegistry:
    """``load_manifest_registry`` / ``save_manifest_registry`` JSON round-trip."""

    def test_save_and_load_round_trip(self, tmp_path):
        manifests = [
            GroundMotionManifest(
                record_id=f"RSN{i}",
                provider="peer_nga_west2",
                event="e",
                station="s",
                component="H1",
                source_url="https://example.org/record.at2",
            )
            for i in range(3)
        ]
        p = tmp_path / "manifests.json"
        save_manifest_registry(str(p), manifests)
        assert load_manifest_registry(str(p)) == manifests

    def test_non_list_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        with pytest.raises(ValueError):
            load_manifest_registry(str(p))
