"""Interactive file-selection helpers (tkinter / macOS native dialogs).

Used by standalone scripts to pick an SAP2000 file path without typing.
``mac_file_chooser`` uses AppleScript; ``tkinter_file_chooser`` uses a
plain Tk dialog and works on any platform with Tk.

Both choosers default to **JSON and text input** (the JSON cache format
written by :meth:`fea_toolkit.io.s2k_parser.SAP2000Parser.to_json`, plus
plain ``.txt`` files).  Pass ``file_types`` to offer a different set of
extensions — for example the SAP2000 text exports that ``SAP2000Parser`` reads::

    path = mac_file_chooser(
        file_types=("s2k", "S2K", "$2k", "json", "JSON"),
        prompt="Select a SAP2000 model file",
    )
"""

import shlex
from collections.abc import Sequence
from typing import Optional

#: Extensions offered when the caller passes no ``file_types`` — the JSON
#: cache format produced by ``SAP2000Parser.to_json()``, plus plain text.
DEFAULT_FILE_TYPES = ("json", "JSON", "txt")

#: Dialog prompt used when the caller passes no ``prompt``.
DEFAULT_PROMPT = "Select SAP2000 JSON file to parse"

#: ``filetypes`` rows used by the tkinter dialog in its default JSON/text mode.
_JSON_TK_FILETYPES = [
    ("SAP2000 JSON files", "*.json"),
    ("SAP2000 files", "*.JSON"),
    ("Text files", "*.txt"),
    ("All files", "*.*"),
]


def tk_filetypes(file_types: Optional[Sequence[str]] = None) -> list:
    """Build a tkinter ``filetypes`` list for ``file_types``.

    Args:
        file_types: Extensions without the leading dot, e.g.
            ``("s2k", "$2k", "json")``.  ``None`` (the default) returns
            the default JSON-and-text rows used by :func:`tkinter_file_chooser`.

    Returns:
        A list of ``(label, pattern)`` pairs, always ending with an
        ``All files`` entry.
    """
    if file_types is None:
        return list(_JSON_TK_FILETYPES)
    patterns = " ".join(f"*.{ext}" for ext in file_types)
    return [(f"Model files ({patterns})", patterns), ("All files", "*.*")]


def _applescript_string(value: str) -> str:
    """Quote ``value`` as an AppleScript string literal.

    Backslashes and double quotes are escaped so the value cannot alter
    the surrounding AppleScript expression.

    Args:
        value: Raw text (a dialog prompt or file extension).

    Returns:
        The value wrapped in double quotes, with ``\\`` and ``"`` escaped.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def applescript_choose_file_cmd(
    prompt: Optional[str] = None,
    file_types: Optional[Sequence[str]] = None,
) -> str:
    """Build the ``osascript`` command used by :func:`mac_file_chooser`.

    Kept separate from the chooser so the command can be inspected — and
    tested — without opening a dialog (the dialog itself blocks).

    Args:
        prompt: Dialog prompt.  Defaults to :data:`DEFAULT_PROMPT`.
        file_types: Extensions without the leading dot, e.g.
            ``("s2k", "$2k", "json")``.  Defaults to
            :data:`DEFAULT_FILE_TYPES`.

    Returns:
        A shell-quoted ``osascript`` command string suitable for
        ``subprocess.check_output``.
    """
    extensions = DEFAULT_FILE_TYPES if file_types is None else tuple(file_types)
    title = DEFAULT_PROMPT if prompt is None else prompt
    type_spec = ", ".join(_applescript_string(ext) for ext in extensions)
    script = (
        "POSIX path of (choose file with prompt "
        f"{_applescript_string(title)} of type {{{type_spec}}})"
    )
    return f"osascript -e {shlex.quote(script)}"


def tkinter_file_chooser(
    verbose: bool = False,
    file_types: Optional[Sequence[str]] = None,
    prompt: Optional[str] = None,
) -> Optional[str]:
    """Open a Tk file dialog and return the selected path.

    Args:
        verbose: Print a diagnostic message when the dialog fails.
        file_types: Extensions to offer (without the leading dot).
            Defaults to :data:`DEFAULT_FILE_TYPES` — JSON and text input.
        prompt: Dialog title.  Defaults to :data:`DEFAULT_PROMPT`.

    Returns:
        The selected path, or ``None`` if the user cancelled or the
        dialog could not be opened.
    """
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()

    try:
        # Ask user to select a file
        file_path = filedialog.askopenfilename(
            title=DEFAULT_PROMPT if prompt is None else prompt,
            filetypes=tk_filetypes(file_types),
        )

        root.update()  # Forces macOS to process pending UI events and release the native dialog hook
        root.quit()  # Stops the Tcl/Tk main loop
        root.destroy()  # Safely destroys the root widget

    except tk.TclError:
        if verbose:
            print("❌ TclError - file picking error.")
        file_path = None

    # Tk returns "" (not None) when the user cancels; normalise so callers
    # can rely on a single falsy sentinel.
    return file_path or None


def mac_file_chooser(
    verbose: bool = False,
    file_types: Optional[Sequence[str]] = None,
    prompt: Optional[str] = None,
) -> Optional[str]:
    """Open the native macOS file dialog and return the selected path.

    Args:
        verbose: Print a diagnostic message when the user cancels.
        file_types: Extensions to offer (without the leading dot).
            Defaults to :data:`DEFAULT_FILE_TYPES` — JSON and text input.
        prompt: Dialog prompt.  Defaults to :data:`DEFAULT_PROMPT`.

    Returns:
        The selected path, or ``None`` if the user cancelled or the
        platform is not macOS.
    """
    import sys

    file_path = None
    # --- MACOS DOCK SUPPRESSION ---
    if sys.platform == "darwin":
        import subprocess

        # Call the native AppleScript file chooser
        cmd = applescript_choose_file_cmd(prompt=prompt, file_types=file_types)
        try:
            # Captures the selected file path string
            file_path = subprocess.check_output(cmd, shell=True).decode("utf-8").strip()
        except subprocess.CalledProcessError:
            if verbose:
                print("❌ Selection cancelled by user.")
            file_path = None  # User pressed "Cancel"
    return file_path
