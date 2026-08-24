"""Mirror the repository root README onto the docs home page.

The docs home page (``docs/index.md``) displays the repository's main
``README.md`` (the canonical overview) verbatim.  Links in that README are
authored relative to the repository root (e.g. ``docs/pushover_analysis.md``)
so they render correctly on GitHub.  On the built site the home page lives
at the site root, so those targets are remapped here:

* ``docs/<page>.md``   -> ``<page>.md``   (mkdocs rewrites to the page URL)
* ``docs/README.md``   -> ``documentation_index.md`` (the docs index)
* ``examples/...``     -> GitHub blob URL
* ``tests/...``        -> GitHub blob URL

``on_page_read_source`` injects the README content into the home page so
that the link rewriting in ``on_page_markdown`` runs *before* mkdocs
resolves relative links (pymdownx.snippets expands too late for that).
Fenced code blocks are left untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_BLOB = "https://github.com/boustrephon/fea_toolkit/blob/main/"

# Non-image markdown link: [text](target) — no spaces in the target.
_LINK_RE = re.compile(r"(?<!\!)\[([^\]]*)\]\(([^)\s]+)\)")


def _rewrite_href(href: str) -> str:
    if href == "docs/README.md":
        return "documentation_index.md"
    if href.startswith("docs/"):
        return href[len("docs/") :]
    if href.startswith("examples/"):
        return _REPO_BLOB + href
    if href.startswith("tests/"):
        return _REPO_BLOB + href
    return href


def _readme_text(config) -> str:
    docs_dir = Path(config.docs_dir)
    return (docs_dir.parent / "README.md").read_text(encoding="utf-8")


def on_page_read_source(*, page, config, **kwargs):
    """Use the repository root README as the source of the home page."""
    if page.file.src_uri == "index.md":
        return _readme_text(config)
    return None


def on_page_markdown(markdown: str, *, page, config, files) -> str:
    """Rewrite repo-root-relative links on the home page (mirrored README)."""
    if page.file.src_uri != "index.md":
        return markdown

    out: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence:
            line = _LINK_RE.sub(
                lambda m: f"[{m.group(1)}]({_rewrite_href(m.group(2))})",
                line,
            )
        out.append(line)
    return "\n".join(out)
