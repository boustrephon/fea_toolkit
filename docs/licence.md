---
title: "Licence and Disclaimer"
description: "Licensing terms for fea_toolkit (GPL-3.0-or-later), the absence of any warranty, how the toolkit is distributed (source, by git clone, not PyPI), and the third-party dependency licences — including OpenSeesPy's commercial-redistribution clause."
status: "complete"
tags: [licence, gpl, warranty, disclaimer, distribution, openseespy, legal]
category: [about]
related: [openseespy_local_build.md, rhino_export.md, workflow.md]
---
# Licence and Disclaimer

> **No warranty.** `fea_toolkit` is distributed in the hope that it will be
> useful, but **without any warranty, express or implied**, including without
> limitation the implied warranties of merchantability, fitness for a
> particular purpose and non-infringement. The entire risk as to the quality
> and performance of the software is with you.
>
> **Engineering judgement is required.** The toolkit performs finite-element
> analysis and design checks. Every result must be independently verified by a
> suitably qualified engineer before it is relied upon for design,
> construction or assessment. Nothing produced by this software is a
> substitute for engineering judgement or for the applicable design code.

## Licence

`fea_toolkit` is free software, licensed under the **GNU General Public
License, version 3 or later** (SPDX: `GPL-3.0-or-later`).

The full licence text ships with the source as
[`LICENSE`](https://github.com/boustrephon/fea_toolkit/blob/main/LICENSE),
and is also available from the
[GNU project](https://www.gnu.org/licenses/gpl-3.0.html).

**You may** use, study, modify and redistribute the toolkit — including
commercially — provided you honour the licence.

**Distributing the toolkit (or a modified version) obliges you to:**

1. keep the copyright notice and the licence text intact;
2. mark modified versions as changed;
3. make the **corresponding source** of the whole work available under the
   GPL-3.0-or-later — a closed-source derivative may not be distributed;
4. state the absence of warranty (GPL-3.0 §15).

Running the toolkit internally carries **no** distribution obligation:
research, teaching and internal use inside an organisation are unrestricted.
GPL-3.0 does not extend to network use — delivering an analysis *service* over
the network does not by itself trigger the source-availability duty (that
would require AGPL, which this project does not use).

## Distribution

`fea_toolkit` is currently distributed **as source, by git clone only**:

```bash
git clone https://github.com/boustrephon/fea_toolkit.git
cd fea_toolkit
pip install -e ".[report]"       # add [mesh-remesh] for Gmsh remeshing
```

It is **not published on PyPI**, so `pip install fea_toolkit` will not find it
and there is no released wheel to install. Because distribution is
source-only, GPL-3.0 obligations arise only when *you* redistribute the source
or a modified copy — and the git history is the record of provenance.

## Third-party dependencies

`fea_toolkit` depends on the following packages. None of them are vendored or
re-bundled by this project; they are installed from their own distributions
under their own terms.

| Package | Required? | Licence |
|---|---|---|
| `openseespy` (with the platform binary `openseespylinux` / `openseespywin` / `openseespymac`) | yes | OpenSeesPy licence — see below |
| `numpy` | yes | BSD-3-Clause |
| `matplotlib` | yes | Matplotlib licence (PSF-based, BSD-compatible) |
| `pyvista` | yes | MIT |
| VTK (pulled in by PyVista) | yes | BSD-3-Clause |
| `gmsh` — `[mesh-remesh]` extra | optional | **GPL-2.0-or-later** |
| `pandas` — `[report]` extra | optional | BSD-3-Clause |
| `imageio` — animated GIF export | optional | BSD-2-Clause |
| `h5py` — HDF5 stage/result files | optional | BSD-3-Clause |
| `opstool` | not required | **GPL-3.0** |
| `opsvis` | not required | **GPL-3.0-or-later** |

`opstool` and `opsvis` are **not** dependencies of this project: the NPZ
results schema is merely *aligned* with opstool's ODB dimension naming, and the
`docs/references/` example scripts are annotated OpenSees examples included for
reference. Install them yourself only if you use those third-party tools
directly.

Rhino 8 and its SDKs (`Rhino`, `RhinoCommon`, `rhinoscriptsyntax`,
`scriptcontext`) are proprietary software from McNeel, supplied by the host
application and **not** distributed here. The `fea_toolkit.rhino` subpackage
runs inside that host; GPL-3.0 applies to the subpackage's source, while the
host remains governed by McNeel's own terms.

## OpenSeesPy: commercial redistribution requires a licence

`fea_toolkit` builds and drives OpenSees models, but it does **not** bundle or
redistribute OpenSeesPy. OpenSeesPy carries its own licence, which is more
restrictive than GPL-3.0 in one important respect. Verbatim from the
`openseespy` distribution:

> OpenSeesPy is free for research, education, and internal use. Commercial
> redistribution of OpenSeesPy, such as, but not limited to, an application or
> cloud-based service that uses `import openseespy`, requires a license similar
> to that required for commercial redistribution of OpenSees.exe. Contact
> Dr. Minjie Zhu (zhum@oregonstate.edu) for commercial licensing details.

In practical terms:

- **Internal use is free.** Using `fea_toolkit` (and therefore OpenSeesPy)
  inside your organisation for analysis requires no OpenSees licence.
- **Redistribution is not.** Shipping an application, or operating a
  cloud-based service, that imports `openseespy` requires a commercial licence
  from Oregon State University. This obligation is **independent of** and
  **additional to** `fea_toolkit`'s own GPL-3.0 terms — choosing a permissive
  licence for this project would not remove it.

If you intend to distribute a product or run a service built on this toolkit,
confirm your OpenSeesPy licensing position with Oregon State University before
release.

## Support and verification

The toolkit is provided as-is, with no support, maintenance or
fitness-for-purpose commitment of any kind and, as noted above, no warranty.
Validation evidence for the implemented procedures is recorded in the
per-feature documentation and in `tests/`; it is not a certification, and it
does not transfer design responsibility away from the engineer using the
software.
