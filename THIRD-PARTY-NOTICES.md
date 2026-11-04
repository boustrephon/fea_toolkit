# Third-Party Notices

This file records the third-party software bundled with, or required by, a
packaged `fea_toolkit` executable (for example a Nuitka "standalone" build). It
is distributed alongside `LICENSE` (the `GPL-3.0-or-later` text) and
`OpenSees-COPYRIGHT.txt`, and its short form is printed by the `--license` flag
on the command-line entry points.

## fea_toolkit

`fea_toolkit` is free software, licensed under the GNU General Public License,
version 3 or later (`GPL-3.0-or-later`). The full text is in `LICENSE`.

## OpenSees (core) — The Regents of the University of California

OpenSeesPy embeds the OpenSees finite-element framework. OpenSees's copyright
notice (reproduced verbatim in `OpenSees-COPYRIGHT.txt`) grants:

- use, reproduction, modification and distribution to educational, research and
  non-profit entities for **non-commercial purposes only**; and
- use, reproduction and modification to other entities for **internal purposes
  only**.

Incorporation of OpenSees into a product for **commercial distribution** requires
a licence from the University of California Office of Technology Licensing
(2150 Shattuck Avenue #510, Berkeley, CA 94720-1620, (510) 643-7201).

> Copyright @ 1999-2020 The Regents of the University of California (The
> Regents). All Rights Reserved.

## OpenSeesPy — Oregon State University

> OpenSeesPy is free for research, education, and internal use. Commercial redistribution
> of OpenSeesPy, such as, but not limited to, an application or cloud-based service that
> uses import openseespy, requires a license similar to that required for commercial
> redistribution of OpenSees.exe. Contact Dr. Minjie Zhu (zhum@oregonstate.edu) for
> commercial licensing details.

**Redistributing a packaged executable that embeds OpenSeesPy is "commercial
redistribution" under the terms above and requires a licence from Oregon State
University — and, for the OpenSees core, from UC OTL — before the build is
published or shared.**

## Bundled and required components

| Component | Licence | Notes |
|---|---|---|
| OpenSees core | UC Regents (non-commercial / internal use) | See `OpenSees-COPYRIGHT.txt` |
| OpenSeesPy | OSU (research / education / internal; commercial redistribution needs a licence) | See above |
| `numpy` | BSD-3-Clause | required |
| `matplotlib` | Matplotlib licence (PSF-based) | required |
| `pyvista` | MIT | required |
| VTK | BSD-3-Clause | pulled in by PyVista |
| GCC runtime (`libgfortran`, `libgomp`, `libquadmath`) | GPL-3.0-or-later with the GCC Runtime Library Exception | bundled with the OpenSeesPy wheel; the exception permits linking into non-GPL programs |
| BLAS / LAPACK (`libblas`, `liblapack`) | reference (netlib) BSD-style | bundled with the OpenSeesPy wheel; confirm provenance at build time |
| CPython runtime | Python Software Foundation Licence | bundled by Nuitka standalone mode |
| `gmsh` (`[mesh-remesh]`) | GPL-2.0-or-later | optional |
| `pandas` (`[report]`) | BSD-3-Clause | optional |
| `imageio` | BSD-2-Clause | optional |
| `h5py` | BSD-3-Clause | optional |

## GPL source-offer

Components distributed under the GPL — `fea_toolkit` itself, and, where bundled,
`gmsh` — carry a corresponding-source obligation. For a packaged build, make the
corresponding source of those components available under the same terms (for
example the tagged source at <https://github.com/boustrephon/fea_toolkit>) and
state where it can be obtained. The GCC runtime libraries are under GPL with the
GCC Runtime Library Exception, which does not impose a source-offer obligation
on the combined binary.

## Notes

- Nuitka is the build tool; its compiler is Apache-2.0, but the generated
  binaries are the licensee's own work and Nuitka does not add a separately
  licensed runtime to the standard standalone output.
- The exact set of bundled shared libraries depends on the platform wheel of
  OpenSeesPy and the optional extras selected. Verify the `.dist` / `lib`
  directory contents before publishing, and keep this table in step with them.
