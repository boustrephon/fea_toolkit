#!/usr/bin/env python
"""Fetch ground-motion records listed in a manifest registry into ``data/``.

Reads a JSON manifest registry (see
:class:`fea_toolkit.io.ground_motion.GroundMotionManifest`) and downloads each
record's waveform into ``data/ground_motion/<record_id>.at2``.

Providers that require an interactive login (e.g. PEER NGA-West2) cannot be
fetched directly: for those, the script prints the metadata and the portal URL
and expects the user to download the ``.AT2`` manually to the same path.

Usage::

    python scripts/fetch_ground_motions.py [manifest.json]
"""

import argparse
import pathlib
import sys
import urllib.request

from fea_toolkit.io.ground_motion import load_manifest_registry

DEFAULT_MANIFEST = pathlib.Path("tests") / "fixtures" / "ground_motion_manifests.json"
ROOT = pathlib.Path("data") / "ground_motion"


def _dest_for(m) -> pathlib.Path:
    """Route a manifest to the public (redistributable) or local mirror."""
    sub = "public" if m.redistributable else "local"
    return ROOT / sub


def _is_direct_file(url: str) -> bool:
    return url.lower().endswith((".at2", ".txt", ".csv"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args(argv)

    manifests = load_manifest_registry(args.manifest)
    for m in manifests:
        _dest_for(m).mkdir(parents=True, exist_ok=True)

    for m in manifests:
        target = _dest_for(m) / f"{m.record_id}.at2"
        if target.exists():
            print(f"skip   {m.record_id} (already present)")
            continue
        if _is_direct_file(m.source_url):
            print(f"fetch  {m.record_id} <- {m.source_url}")
            try:
                urllib.request.urlretrieve(m.source_url, target)
            except Exception as exc:  # network / provider failures are expected
                print(f"  !! {m.record_id}: {exc}", file=sys.stderr)
        else:
            print(
                f"manual {m.record_id}: {m.event} / {m.station} ({m.component})\n"
                f"    provider: {m.provider}   licence: {m.licence}\n"
                f"    download from {m.source_url} and save to {target}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
