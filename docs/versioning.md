---
title: "Versioning & Release Plan"
description: "How fea_toolkit is versioned (setuptools-scm, tag-driven), what major/minor/patch mean for this project, and the planned release path through the GUI work and toward 1.0."
status: "complete"
tags: [versioning, release, semver, planning, packaging]
category: [planning]
related: [gui_roadmap.md, _pending_work.md, dev_notes.md]
---
# Versioning & Release Plan

## Status: ✅ Complete — policy recorded 2026-09-23

---

## 1. How versions are produced

The version is **derived from git — it is never hand-edited**:

- `pyproject.toml` declares `[build-system] requires = ["setuptools>=80",
  "setuptools-scm>=8"]` with `[tool.setuptools_scm] write_to =
  "src/fea_toolkit/_version.py"`.
- `src/fea_toolkit/_version.py` is **auto-generated** — do not edit it
  (guardrails §5.4).
- The reported version is `<latest tag>` plus a distance/dev suffix, e.g.
  `git describe --tags` → `v0.4.0` at the tag, or `v0.4.0-3-g<hash>` three
  commits past `v0.4.0`.

Three consequences that matter operationally:

1. **A release is a tag.**  Nothing else changes the version.
2. **Documentation-only or refactor-only commits need no tag** — they simply
   advance the `-N-g<hash>` suffix.  Tag only when actually shipping.
3. The project is distributed **source-only, by git clone** (it is not on
   PyPI — guardrails §5.8), so the tag is the marker consumers check out.

---

## 2. What major / minor / patch mean here

The project is **pre-1.0** (`Development Status :: 4 - Beta` in
`pyproject.toml`), and the release series is **one tag per minor**
(`v0.1.0 → v0.2.0 → v0.3.0`).

| Bump | Meaning | Triggers in this project |
|---|---|---|
| **Major** (`1.0.0`) | A breaking public-API change, **or** a deliberate stability / maturity milestone | A public-API freeze and the move to "stable" classifiers — a **project-wide decision**, never a feature addition |
| **Minor** (`0.4.0`) | New, backward-compatible capability | A new subpackage, a new public function, a new optional extra, a new analysis type |
| **Patch** (`0.3.1`) | Backward-compatible fix | Bug fixes, docs corrections, internal refactors with no API change |

Because the project is 0.x, the **middle digit carries the feature signal**.
The goal is to reach 1.0 **only once the public API is deliberately frozen** —
not as a side effect of adding a feature.

---

## 3. Release path

| Version | Contents | Status |
|---|---|---|
| `v0.3.0` | previous release | ✅ released |
| `v0.4.0` | the backlog of backward-compatible features accumulated since `v0.3.0` (load combinations, force-diagram unification, per-storey forces, …) — the current (last tagged) release | ✅ tagged 2026-09-23 |
| `v0.5.0` | the **desktop GUI** — the `[gui]` extra (see [Desktop GUI Roadmap](gui_roadmap.md), P22) | 🚧 planned |
| `v1.0.0` | public-API freeze / stability milestone | 🚧 not scheduled |

Notes:

- The GUI is **additive and isolated** — a new optional `[gui]` extra, a new
  `gui/` subpackage, a new entry point.  The core pipeline and public API are
  untouched, so it is a **minor** bump, never a major one.
- The only dependency floor the GUI introduces (`pyvistaqt` → Python 3.10+) is
  **scoped to the `[gui]` extra**; the core stays at 3.9, so it is **not** a
  breaking change for existing users.
- **Web (trame) and iOS remain out of scope** for the current plan — see the
  rationale in [Desktop GUI Roadmap](gui_roadmap.md) §8.

---

## 4. Release checklist

1. Confirm the working tree is clean and CI is green.
2. Update `README.md` / the docs if the release changes the public surface.
3. Tag from `main`:
   `git tag -a v0.X.0 -m "v0.X.0 — <summary>"`.
4. Push **the branch and the tag**:
   `git push origin main && git push origin v0.X.0`.
5. **Never** `git push --mirror` / `git push origin 'refs/*'` (guardrails
   §6.4) — that would publish the local-only `refs/cline/checkpoints/*`
   snapshots, which contain client data.
