---
title: "Custom OpenSeesPy local build — swap recipe"
description: "How to build a local OpenSeesPy wheel and swap it into the import chain on macOS arm64."
status: "draft"
tags: [openseespy, build, macos, fea-toolkit]
category: [opensees]
---
# Custom OpenSeesPy build — swap recipe (macOS arm64)

## Why this document exists

The shipped `openseespymac` wheel (OpenSeesPy 3.8.0.0, Darwin/arm64) has a
couple of gaps that a local rebuild can fix:

- **2D `SFI_MVLEM` is broken** — the parser accepts the MVLEM-style keywords
  (`-matConcrete/-matSteel/-matShear`) but the constructor still performs
  nD-material lookups, aborting with `Null ND material pointer passed`
  for every tag combination. The `MVLEM_3D`/`SFI_MVLEM_3D`/2D `MVLEM`
  elements work fine, so this specific mismatch is likely a stale-source
  artifact that a fresh build from `github.com/OpenSees/OpenSees` fixes.
- **PSUMAT is NOT fixable by rebuilding** — the stub is in the upstream
  OpenSees source itself (`PSUMAT - NOT DEFINED IN THIS VERSION, SOURCE CODE
  RESTRICTED`). Rebuilding the wheel does not unlock it.
- Anything else that needs a newer/different OpenSees snapshot than the
  wheel was built from.

The goal of this guide is to document how to produce a real, self-contained
OpenSeesPy build on macOS arm64 and **swap it into the import chain** so
`import openseespy.opensees as ops` picks it up.

## Current import chain (Darwin/arm64)

```
openseespy.opensees/__init__.py
    -> sys.platform 'darwin'  and  platform.machine() 'arm64'
    -> from openseespymac.opensees import *
    -> loads openseespymac/opensees.so            (installed wheel, 29,508,704 B)
```

`openseespy/opensees/__init__.py` is the gate keeper. The local build outputs
(`openseespy/opensees/opensees.so` + `OpenSeesPy.dylib`) sit in the same
site-packages but are **never imported** — they are unmanaged artifacts of a
manual build that nobody hooked up.

## Two swap strategies

> **Notation used below:** `$SITE_PACKAGES` is the active virtualenv's
> site-packages directory (typically
> `$VIRTUAL_ENV/lib/python3.12/site-packages`); `$CONAN_HOME` is the conan
> cache root used by the build (the shipped wheel's rpath references a
> Conan-cache path — an observation only, not proof the wheel was built
> with conan).  Adjust both to your own machine.

### Strategy A — redirect the dispatch (cleanest, no wheel surgery)

Patch `openseespy/opensees/__init__.py` to prefer the local build over the
platform wheel, so both the wheel and the local build coexist. macOS arm64:

```python
# In site-packages/openseespy/opensees/__init__.py, darwin branch:
elif sys.platform.startswith('darwin'):
    if _arch != 'arm64':
        raise RuntimeError(...)
    # Local custom build first: opensees/opensees.so defines all the
    # ops.* names. Importing the local .so directly (rather than going
    # through this __init__ again) avoids recursion.
    from os.path import dirname, join, isfile
    _local_so = join(dirname(__file__), "opensees.so")
    if isfile(_local_so):
        from openseespy.opensees.opensees import *
    else:
        # Wheel fallback ONLY when no local build is present.  If a local
        # .so exists but fails to import, the error propagates (the wheel
        # is not silently substituted) so a broken local build is obvious.
        from openseespymac.opensees import *
```

> Note: `openseespy/opensees/opensees.so` is the *compiled module*; the
> wheel's platform module is `openseespymac.opensees`. The line
> `from openseespy.opensees.opensees import *` imports the local compiled
> module and skips this `__init__.py` (the import machinery treats
> `openseespy.opensees.opensees` as a submodule, not a re-entry).
> If the local `.so` is absent, fall back to the wheel.

This keeps the wheel intact for rollback (just revert the patch). The local
build must be installed *next to* the wheel, i.e. its `.so` + bundled
`.dylibs` must be findable from `openseespy/opensees/`.

### Strategy B — replace the wheel binary (surgical)

1. Back up the wheel:
   ```bash
   cd $SITE_PACKAGES
   mv openseespymac/opensees.so openseespymac/opensees.so.wheel-backup
   ```
2. Copy the local build into the platform package:
   ```bash
   cp openseespy/opensees/opensees.so      openseespymac/opensees.so
   cp openseespy/opensees/OpenSeesPy.dylib openseespymac/OpenSeesPy.dylib
   # plus any dylibs it needs (see `otool -L`), typically the local .dylibs:
   cp -R openseespy/.dylibs openseespymac/.dylibs
   ```
3. Fix rpath so `@rpath/OpenSeesPy.dylib` resolves, or make the local build
   self-contained (`@loader_path/...` paths are relative to the `.so` location,
   which is why the local `openseespy/.dylibs` layout works).
4. Smoke-test with `examples/verify_openseespy.py`.

> Generally prefer **Strategy A** — it is non-destructive, keeps the wheel,
> and makes rollback trivial.

## Building a real local OpenSeesPy wheel

The cleanest path is to build from the OpenSeesPy source that fetches and
compiles the upstream OpenSees:

```bash
git clone --recursive https://github.com/OpenSees/OpenSees
#  └─ includes the mvlem/FSAM element + material sources (open-source)
#  (Do NOT set GIT_SSL_NO_VERIFY=true — if your clone fails TLS
#   verification, fix your local CA trust configuration instead of
#   disabling certificate checks.)

cd OpenSees
# follow the OpenSeesPy build instructions for your platform.
# On macOS arm64 you typically need a conan toolchain.  The shipped wheel's
# rpath references a Conan-cache path, but that is an observation only — not
# proof the wheel was built with conan.  Verify the actual embedded path
# yourself with the documented inspection command (`otool -L` on the .so /
# OpenSeesPy.dylib).
```

The Kolozvari **MVLEM/SFI-MVLEM/FSAM** sources live in the official OpenSees
repo under `SRC/element/` / `SRC/material/nD/` and are open-source, so a
rebuild picks up the correct 2D parser. **PSUMAT** is the only excluded
material — its `PlaneStressUserMaterial` source is deliberately empty.

## Verifying the swap worked

```python
import openseespy.opensees as ops
print(ops.__file__)          # must point at the build you expect
# confirm SFI_MVLEM_3D constructs (fast):
ops.wipe(); ops.model('basic','-ndm',3,'-ndf',6)
# ... create FSAM + Steel02 + ConcreteCM, then:
ops.element('SFI_MVLEM_3D', 1, 1,2,3,4, 2, '-thick', 0.3, 0.3,
            '-width', 2.0, 2.0, '-mat', 5, 6, '-CoR', 0.4)   # -> ok
```

Run `import openseespy.opensees as ops` in the same venv the toolkit uses and
check the module file; if it points at `openseespymac` you are still on the
wheel — remove the dispatch override or check `PYTHONPATH`.

## Reverting

- **Strategy A**: revert the `openseespy/opensees/__init__.py` patch.
- **Strategy B**: `mv openseespymac/opensees.so.wheel-backup openseespymac/opensees.so`.

Both are safe because the wheel's own `opensees.so` is never re-downloaded
unless you reinstall `openseespy`, `openseespymac`, or the `openseespy-mac`
specifically.