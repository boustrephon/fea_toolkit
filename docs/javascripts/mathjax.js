// MathJax v3 configuration for the MkDocs Material site.
//
// `pymdownx.arithmatex` is configured with `generic: true`, which wraps inline
// math in `<span class="arithmatex">\(...\)</span>` and display math in
// `<div class="arithmatex">\[...\]</div>`.  MathJax is told to typeset only
// those containers (and to re-typeset after each Material navigation, since
// instant loading swaps page content without a full reload).
window.MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"]],
    displayMath: [["\\[", "\\]"]],
    processEscapes: true,
    processEnvironments: true,
  },
  options: {
    ignoreHtmlClass: ".*|",
    processHtmlClass: "arithmatex",
  },
};

document$.subscribe(() => {
  MathJax.startup.output.clearCache();
  MathJax.typesetClear();
  MathJax.texReset();
  MathJax.typesetPromise();
});
