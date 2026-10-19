#!/usr/bin/env python
"""Fetch ground-motion records listed in a manifest registry into ``data/``.

Reads a JSON manifest registry (see
:class:`fea_toolkit.io.ground_motion.GroundMotionManifest`) and downloads each
record's waveform into ``data/ground_motion/<public|local>/<record_id>.<ext>``,
where ``<ext>`` follows the source file's extension (``.at2``, ``.txt`` or
``.csv``).

Providers that require an interactive login (e.g. PEER NGA-West2) cannot be
fetched directly: for those, the script prints the metadata and the portal URL
and expects the user to download the file manually to
``data/ground_motion/local/<record_id>.at2``.

Usage::

    python scripts/fetch_ground_motions.py [manifest.json]
"""

import argparse
import contextlib
import os
import pathlib
import sys
import tempfile
import urllib.request

from fea_toolkit.io.ground_motion import is_direct_url, load_manifest_registry, source_extension

DEFAULT_MANIFEST = pathlib.Path("tests") / "fixtures" / "ground_motion_manifests.json"
ROOT = pathlib.Path("data") / "ground_motion"


def _dest_for(m) -> pathlib.Path:
    """Route a manifest to the public (redistributable) or local mirror."""
    sub = "public" if m.redistributable else "local"
    return ROOT / sub


def _validate_record_id(record_id: str) -> str:
    """Reject a record id that could escape the selected mirror directory."""
    if (
        not record_id
        or record_id in (".", "..")
        or "/" in record_id
        or "\\" in record_id
        or record_id != os.path.basename(record_id)
    ):
        raise ValueError(
            f"Unsafe record id {record_id!r}: it must be a plain file name "
            "with no path separators or traversal components."
        )
    return record_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args(argv)

    manifests = load_manifest_registry(args.manifest)
    for m in manifests:
        _dest_for(m).mkdir(parents=True, exist_ok=True)

    failed = False
    for m in manifests:
        try:
            record_id = _validate_record_id(m.record_id)
        except ValueError as exc:
            print(f"  !! {m.record_id}: {exc}", file=sys.stderr)
            failed = True
            continue

        ext = source_extension(m.source_url)
        target = _dest_for(m) / f"{record_id}{ext}"
        if target.exists():
            print(f"skip   {record_id} (already present)")
            continue
        if is_direct_url(m.source_url):
            print(f"fetch  {record_id} <- {m.source_url}")
            tmp = None
            try:
                # Ensure the mirror directory exists right before the temp
                # file is created, so a missing/read-only directory surfaces
                # as a per-manifest failure rather than aborting the run.
                target.parent.mkdir(parents=True, exist_ok=True)
                # A unique temp file beside the target, so concurrent fetches
                # of the same record (or two records in one mirror) never
                # collide on a deterministic ``.<name>.part`` path.
                fd, tmp_name = tempfile.mkstemp(
                    dir=target.parent, prefix=f".{target.name}.", suffix=".part"
                )
                tmp = pathlib.Path(tmp_name)
                os.close(fd)
                urllib.request.urlretrieve(m.source_url, tmp)
            except Exception as exc:  # network / provider / filesystem failures
                print(f"  !! {record_id}: {exc}", file=sys.stderr)
                failed = True
                if tmp is not None:
                    with contextlib.suppress(OSError):
                        tmp.unlink(missing_ok=True)
            else:
                tmp.replace(target)
        else:
            print(
                f"manual {record_id}: {m.event} / {m.station} ({m.component})\n"
                f"    provider: {m.provider}   licence: {m.licence}\n"
                f"    download from {m.source_url} and save to {target}"
            )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
