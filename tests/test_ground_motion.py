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
    select_component_by_pga,
    source_extension,
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

    def test_save_preserves_existing_mode(self, tmp_path):
        """An existing registry's mode survives an atomic re-save."""
        import os

        p = tmp_path / "manifests.json"
        p.write_text("[]", encoding="utf-8")
        os.chmod(p, 0o640)
        save_manifest_registry(str(p), [])
        assert os.stat(p).st_mode & 0o777 == 0o640

    def test_save_fresh_file_uses_umask_default_mode(self, tmp_path):
        """A new registry gets the umask-derived mode, not ``mkstemp``'s 0600."""
        import os

        p = tmp_path / "manifests.json"
        save_manifest_registry(str(p), [])
        umask = os.umask(0)
        os.umask(umask)
        assert os.stat(p).st_mode & 0o777 == 0o666 & ~umask


class TestSourceExtension:
    """``source_extension`` — the shared fetch-URL extension selector."""

    def test_direct_url_keeps_source_suffix(self):
        assert source_extension("https://example.org/record.at2") == ".at2"
        assert source_extension("https://example.org/record.txt?download=1") == ".txt"
        assert source_extension("https://example.org/record.CSV") == ".csv"

    def test_non_direct_url_falls_back_to_at2(self):
        assert source_extension("https://example.org/portal") == ".at2"
        assert source_extension("https://example.org/record.zip") == ".at2"


class TestReadPeerRecordHeaderVariants:
    """NGA-West2 headers can write DT in scientific notation, leading-dot or
    trailing-dot form."""

    def test_dt_scientific_notation(self, tmp_path):
        f = tmp_path / "sci.at2"
        f.write_text("NPTS=3\nDT=5.0E-03\n0.1\n0.1\n0.1\n", encoding="utf-8")
        times, accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.005, 0.01])
        assert accel.tolist() == pytest.approx([0.1 * DEFAULT_GRAVITY_MS2] * 3)

    def test_dt_leading_dot(self, tmp_path):
        f = tmp_path / "dot.at2"
        f.write_text("NPTS=2\nDT= .00500\n0.2\n0.2\n", encoding="utf-8")
        times, _accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.005])

    def test_dt_trailing_dot_exponent(self, tmp_path):
        """``DT=5.E-03`` (digits then a dot with no fractional digit) parses in full."""
        f = tmp_path / "trailing_dot.at2"
        f.write_text("NPTS=3\nDT=5.E-03\n0.1\n0.1\n0.1\n", encoding="utf-8")
        times, accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.005, 0.01])
        assert accel.tolist() == pytest.approx([0.1 * DEFAULT_GRAVITY_MS2] * 3)

    def test_dt_comma_delimited_on_same_header_line(self, tmp_path):
        """``DT`` after a comma delimiter with no space (``NPTS=  3,DT=  0.005``) is recognised."""
        f = tmp_path / "comma.at2"
        f.write_text("NPTS=  3,DT=  0.005\n0.1\n0.1\n0.1\n", encoding="utf-8")
        times, accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.005, 0.01])
        assert accel.tolist() == pytest.approx([0.1 * DEFAULT_GRAVITY_MS2] * 3)

    def test_npts_mismatch_warns(self, tmp_path):
        f = tmp_path / "trunc.at2"
        f.write_text("NPTS=5\nDT=0.005\n0.1\n0.1\n0.1\n", encoding="utf-8")
        with pytest.warns(UserWarning, match="truncated"):
            times, _accel = read_peer_record(str(f))
        assert len(times) == 3

    def test_positional_header_provides_time_step(self, tmp_path):
        """A positional ``<NPTS> <DT> NPTS, DT`` header sets a non-default step."""
        f = tmp_path / "positional.at2"
        f.write_text("3 0.02 NPTS, DT\n0.1\n0.2\n0.3\n", encoding="utf-8")
        times, accel = read_peer_record(str(f))
        assert times.tolist() == pytest.approx([0.0, 0.02, 0.04])
        assert accel.tolist() == pytest.approx(
            [0.1 * DEFAULT_GRAVITY_MS2, 0.2 * DEFAULT_GRAVITY_MS2, 0.3 * DEFAULT_GRAVITY_MS2]
        )


class TestSelectComponentByPGA:
    """``select_component_by_pga`` picks the component matching a paper's PGA."""

    def test_picks_closest_pga(self):
        times = np.arange(5) * 0.005
        candidates = {
            "a": (times, np.array([0.0, 0.1, -0.05, 0.02, 0.0]) * DEFAULT_GRAVITY_MS2),
            "b": (times, np.array([0.0, 0.3, -0.3, 0.1, 0.0]) * DEFAULT_GRAVITY_MS2),
        }
        assert select_component_by_pga(candidates, 0.12) == "a"
        assert select_component_by_pga(candidates, 0.28) == "b"

    def test_empty_returns_none(self):
        assert select_component_by_pga({}, 0.1) is None
