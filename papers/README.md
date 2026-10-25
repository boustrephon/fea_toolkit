# papers/

Manuscript drafts produced from the toolkit's benchmark work.

## `ohtori_modal_verification.qmd`

A Quarto notebook documenting the independent OpenSees reproduction of the
Ohtori, Christenson, Spencer & Dyke (2004) SAC benchmark natural frequencies.

**Why Quarto (`.qmd`)?** A single plain-text source that combines prose with
executable Python, renders to PDF / HTML / Word, and can export the same
document as a Jupyter notebook (`.ipynb`) for a submission archive. See the
Quarto "Authoring Manuscripts" guide: https://quarto.org/docs/manuscripts/.

Render — Quarto executes Python through Jupyter, so point it at the OpenSees
environment first:

```bash
# either activate the OpenSeesPy venv, or set QUARTO_PYTHON explicitly:
export QUARTO_PYTHON=/path/to/venv_opensees/bin/python

quarto render papers/ohtori_modal_verification.qmd --to html   # quick check
quarto render papers/ohtori_modal_verification.qmd --to pdf    # manuscript (needs LaTeX)
```

The `jupyter: venv_opensees` key has been removed from the `.qmd` frontmatter;
Quarto executes the code through the Python environment selected above (either
the activated OpenSeesPy venv or the `QUARTO_PYTHON` override), so no Jupyter
kernelspec registration is required. The code chunks import `fea_toolkit` and
`openseespy`, so they need the same OpenSeesPy venv as the test suite.
Citations are resolved from `references.bib`; the generated `.html` /
`*_files/` build outputs are gitignored.
