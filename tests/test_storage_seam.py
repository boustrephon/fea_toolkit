"""The storage seam's rules, enforced rather than merely documented.

See ``docs/dev_notes.md`` → *Results repository and the NumPy-typed seam*.
If one of these fails, either route the new code through the seam or add it to
the allow-list below **with a comment saying why** — the point is that reaching
past the seam is a decision, never an accident.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "fea_toolkit"

#: Modules allowed to call ``np.load``: the readers, plus one format sniff.
_LOAD_ALLOWED = {
    "io/npz_reader.py",
    "io/results_schema.py",
    # Not a results read: it sniffs whether a path is a unified archive or a
    # legacy file, then delegates.  Moving the sniff into the reader is a
    # recorded follow-up, not a licence for consumers to load archives.
    "rhino/colour_from_npz.py",
}

#: Modules allowed to write archives.
_SAVE_ALLOWED = {
    "io/_serial.py",
    "io/npz_writer.py",
}


def _files_calling(*names: str) -> set:
    """Modules that actually *call* ``np.<name>(...)``.

    Parsed rather than grepped: the plotting modules mention ``np.load()`` in
    their docstrings, and a consumer that merely documents the format is not a
    consumer that reads it.  Names are matched **exactly**, so ``np.loadtxt``
    (text I/O for ground motions and recorder output) is deliberately not
    counted — that is not archive storage.
    """
    offenders = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in names
                and isinstance(node.value, ast.Name)
                and node.value.id == "np"
            ):
                offenders.add(path.relative_to(SRC).as_posix())
                break
    return offenders


def _top_level_imports(relative_path: str) -> set:
    """Absolute top-level imports of one module — lazy imports stay lazy."""
    tree = ast.parse((SRC / relative_path).read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_results_are_read_through_one_seam():
    """``np.load`` belongs to the readers; consumers take ``dict``s of arrays."""
    assert _files_calling("load") == _LOAD_ALLOWED


def test_archives_are_written_through_one_seam():
    """``np.savez`` belongs to the writers."""
    assert _files_calling("savez", "savez_compressed") == _SAVE_ALLOWED


def test_the_repository_interface_imports_only_numpy():
    """A backend may be optional; the interface callers depend on never is."""
    assert _top_level_imports("io/results_repository.py") <= {"abc", "typing", "numpy"}


def test_the_model_store_imports_nothing_heavy():
    """Same rule for the topology seam: no Qt, no ``ops``, no ``h5py``."""
    assert _top_level_imports("io/model_store.py") <= {"abc", "dataclasses", "typing"}


def test_the_io_package_reexports_both_seams():
    """The seams are part of the ``io`` facade, like the readers and the writers."""
    from fea_toolkit import io

    for name in (
        "InMemoryModelStore",
        "ModelHeader",
        "ModelStore",
        "NpzResultsRepository",
        "ResultsRepository",
        "model_header",
    ):
        assert name in io.__all__
        assert getattr(io, name) is not None
