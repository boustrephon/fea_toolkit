---
title: "Building the app with Nuitka"
description: "How to build the standalone CLI binary and the macOS desktop app with Nuitka, set the app name and icon, and point the separate build at an OpenSeesPy installation."
status: "complete"
tags: [build, nuitka, packaging, distribution, icon, app-name, opensees, macos]
category: [tool-specific]
related: [gui.md, licence.md, versioning.md, openseespy_local_build.md, _pending_work.md]
---
# Building the app with Nuitka

`scripts/build_nuitka.sh` compiles `fea_toolkit` into a self-contained
executable with [Nuitka](https://nuitka.net/).  Two independent switches
decide *what* is built:

| Switch | Off (default) | On |
|---|---|---|
| `--gui` | a CLI binary (`--standalone`) | the Qt desktop app; on macOS a `.app` bundle (`--mode=app-dist`) |
| `--with-opensees` | the **separate** build — no OpenSees bundled; analysis shells out to a user-installed OpenSeesPy | the **licensed** build — bundles `openseespy` (gated, see *Licence* below) |

All four combinations are valid.  This guide walks through building, naming,
icon-ing, and wiring up the OpenSees runtime.

## Prerequisites

* **Python with Nuitka installed.**  The script runs `"$PYTHON" -m nuitka`,
  so the interpreter named by `PYTHON` (default `python`) must have `nuitka`
  (and, for a GUI build, the optional deps it imports) available.  A dedicated
  virtualenv is the usual home:

  ```bash
  python3 -m venv ~/fea-build
  ~/fea-build/bin/pip install nuitka
  ```

* **`fea_toolkit` installed in that interpreter** — the build reads the package
  from `src/fea_toolkit` via `--include-package`, but an editable install is
  what lets Nuitka resolve the optional extras:

  ```bash
  ~/fea-build/bin/pip install -e ".[gui,report]"
  ```

* **macOS for the `.app` bundle** — the `--gui` build is an app-dist bundle and
  uses the `pyobjc` AppKit/Foundation bridge on macOS.

## Building

```bash
scripts/build_nuitka.sh                     # CLI, separate (no OpenSees)
scripts/build_nuitka.sh --gui               # desktop app, separate
scripts/build_nuitka.sh --with-opensees     # CLI + OpenSees (licensed)
scripts/build_nuitka.sh --gui --with-opensees
```

Output lands under `build/nuitka/` (gitignored):

| Build | Output |
|---|---|
| CLI | `build/nuitka/fea_toolkit.dist/` |
| GUI (macOS) | `build/nuitka/fea_toolkit.app` (binary at `Contents/MacOS/fea_toolkit`) |

The compile is slow — the first run generates and `clang`-compiles on the order
of ~1,700 C translation units (VTK + Qt + pyvista), so expect 30–60 minutes.
Subsequent rebuilds reuse Nuitka's cache and are faster.

### App name

The binary name and the `.app` bundle name come from the `OUTPUT_NAME`
environment variable (default `fea_toolkit`), which feeds Nuitka's
`--output-filename` and `--macos-app-name`:

```bash
OUTPUT_NAME="FeaToolkit" scripts/build_nuitka.sh --gui
# → build/nuitka/FeaToolkit.app
```

### Icon

macOS app icons are `.icns` files.  Pass one with `--icon` (or the `ICON`
environment variable); when omitted the bundle is built with **no icon**
(`--macos-app-icon=none`), which is fine for internal use:

```bash
scripts/build_nuitka.sh --gui --icon build/fea_toolkit.icns
# or: ICON=build/fea_toolkit.icns scripts/build_nuitka.sh --gui
```

Any size image can be converted to `.icns` with the built-in `iconutil` from a
`128`–`1024` px PNG set (`iconutil -c icns Icon.iconset`).

## Pointing the separate build at OpenSeesPy

The **separate** build (the default) deliberately excludes `openseespy`
(`--nofollow-import-to=openseespy`), so it is a clean GPL-3.0-or-later bundle
that redistributes no OpenSees code.  When you run an analysis in that build,
the solver **shells out to a separate Python interpreter that has OpenSeesPy
installed** and reads the results back — so nothing OpenSees-related needs to
be bundled.

The target interpreter must be an environment with **both**:

1. `openseespy` installed, and
2. `fea_toolkit` importable — the subprocess runs
   `python -m fea_toolkit.opensees.subprocess_driver`, so the driver module
   must be reachable (e.g. `pip install -e "path/to/fea_toolkit"` in the same
   environment).

### Creating the runtime environment

A dedicated virtualenv is the cleanest ("point at an environment"):

```bash
python3 -m venv ~/opensees-env
~/opensees-env/bin/pip install openseespy
~/opensees-env/bin/pip install -e "path/to/fea_toolkit"
```

A direct install into the system/user Python works just as well ("installed
direct") — the only requirement is that `import openseespy` and
`import fea_toolkit` both succeed in that interpreter.

### Telling the app where it is

Resolution order, highest precedence first:

1. **The `opensees.python` setting** in `~/.config/fea_toolkit/gui.json` (or
   `$FEA_TOOLKIT_GUI_CONFIG` if it overrides the path):

   ```json
   { "opensees": { "python": "~/opensees-env" } }
   ```

   The value may be an interpreter path (`~/opensees-env/bin/python`) **or a
   virtualenv directory** (`~/opensees-env`) — a directory is auto-resolved to
   its `bin/python` (or `Scripts/python.exe` on Windows).

2. **The `FEA_TOOLKIT_OPENSEES_PYTHON` environment variable** — same accepted
   values as the setting.

3. **Auto-detection** — `sys.executable` if the *current* interpreter can
   import `openseespy` (the source install and the licensed build), otherwise
   the first `python3` on `PATH`.

The chosen interpreter is logged when a solve is delegated ("delegating static
analysis to …"), and a failed run reports the subprocess's own traceback in the
GUI's Message Log.

> **Which verbs delegate.**  Today `run_static` delegates out-of-process when
> `openseespy` is unavailable.  `run_modal`, `run_rs` and `run_pushover` still
> import `openseespy` in-process and require the licensed (`--with-opensees`)
> build or the source install until their delegation is wired.  See
> `docs/gui.md` → *OpenSees runtime*.

## Licence

The **separate** build contains no OpenSees code and may be shared freely under
GPL-3.0-or-later.  The **`--with-opensees`** build redistributes OpenSees and
OpenSeesPy, which requires licences from the University of California and
Oregon State University for commercial use; the script refuses to build it
unless `OPENSEES_COMMERCIAL_LICENCE=1` is set.  See `docs/licence.md`.
