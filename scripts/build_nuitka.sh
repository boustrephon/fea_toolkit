#!/usr/bin/env bash
# Build a Nuitka executable for fea_toolkit.
#
# Two independent switches:
#
#   --gui            build the Qt desktop app.  On macOS this is a .app bundle
#                    (--mode=app-dist).  Default is a CLI binary (--standalone).
#                    Both include the visualization stack (pyvista/VTK,
#                    matplotlib); the GUI adds PySide6/pyvistaqt/qtpy and, on
#                    macOS, the pyobjc AppKit/Foundation bridge.
#
#   --with-opensees  bundle openseespy (the OpenSees core + its BLAS/LAPACK/GCC
#                    runtime libraries).  This is redistribution of OpenSees and
#                    is gated behind OPENSEES_COMMERCIAL_LICENCE=1.  The default
#                    ("separate") build bundles no OpenSees: analysis shells out
#                    to a user-installed OpenSees via the Tcl export.
#
# Usage:
#   scripts/build_nuitka.sh                     # CLI, no OpenSees
#   scripts/build_nuitka.sh --gui               # GUI app, no OpenSees
#   scripts/build_nuitka.sh --with-opensees     # CLI + OpenSees (licensed)
#   scripts/build_nuitka.sh --gui --with-opensees
#   scripts/build_nuitka.sh --gui --icon build/fea_toolkit.icns
#
# Overridable via the environment: PYTHON (default "python"), PACKAGE (default
# "src/fea_toolkit"), OUTPUT_NAME (default "fea_toolkit"), ICON (default none;
# a macOS .icns path, or pass --icon).  See docs/build_nuitka.md.

set -euo pipefail

GUI=0
WITH_OPENSEES=0
ICON="${ICON:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --gui) GUI=1 ;;
        --with-opensees) WITH_OPENSEES=1 ;;
        --icon) ICON="$2"; shift ;;
        --icon=*) ICON="${1#--icon=}" ;;
        *)
            echo "build_nuitka.sh: unknown argument: $1" >&2
            exit 2
            ;;
    esac
    shift
done

if [[ "$WITH_OPENSEES" -eq 1 && "${OPENSEES_COMMERCIAL_LICENCE:-}" != "1" ]]; then
    echo "build_nuitka.sh: --with-opensees bundles OpenSees/OpenSeesPy, which is" >&2
    echo "commercial redistribution under the OpenSees licences.  It requires OSU/UC" >&2
    echo "licences before the binary may be published." >&2
    echo "Set OPENSEES_COMMERCIAL_LICENCE=1 only once those licences are held, then retry." >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python}"
PACKAGE="${PACKAGE:-src/fea_toolkit}"
GUI_ENTRY="${GUI_ENTRY:-scripts/fea_gui_entry.py}"
OUTPUT_NAME="${OUTPUT_NAME:-fea_toolkit}"

ARGS=(
    --assume-yes-for-downloads
    --output-dir="$ROOT/build/nuitka"
    --output-filename="$OUTPUT_NAME"
    --include-package=fea_toolkit
    --include-data-files="$ROOT/LICENSE=LICENSE"
    --include-data-files="$ROOT/THIRD-PARTY-NOTICES.md=THIRD-PARTY-NOTICES.md"
    # Bloat fea_toolkit never imports directly: scipy (pulled in via matplotlib /
    # pyvista) and the Jupyter/IPython autocomplete stack (via pyvista's trame
    # dependency).  Large and unused at runtime.
    --nofollow-import-to=scipy
    --nofollow-import-to=IPython
    --nofollow-import-to=ipywidgets
    --nofollow-import-to=jedi
    --nofollow-import-to=parso
)

if [[ "$GUI" -eq 1 ]]; then
    # A .app bundle on macOS; satisfies the pyobjc AppKit/Foundation requirement.
    # Compile the top-level GUI entry script, not `-m` on the subpackage: the
    # latter broke relative imports (io.report's ``from ..utils import ...``).
    ICON_ARG="--macos-app-icon=none"
    [[ -n "$ICON" ]] && ICON_ARG="--macos-app-icon=$ICON"
    ARGS+=(--mode=app-dist --enable-plugin=pyside6 "$ICON_ARG" --macos-app-name="$OUTPUT_NAME")
    ENTRY="$GUI_ENTRY"
else
    ARGS+=(
        --standalone
        --python-flag=-m
        # CLI build has no Qt and no macOS pyobjc bridge.
        --nofollow-import-to=PySide6
        --nofollow-import-to=pyvistaqt
        --nofollow-import-to=qtpy
        --nofollow-import-to=AppKit
        --nofollow-import-to=Foundation
        --nofollow-import-to=objc
    )
    ENTRY="$PACKAGE"
fi

if [[ "$WITH_OPENSEES" -eq 1 ]]; then
    ARGS+=(
        --include-package=openseespy
        --include-data-files="$ROOT/OpenSees-COPYRIGHT.txt=OpenSees-COPYRIGHT.txt"
    )
    # The OpenSeesPy platform wheel bundles BLAS/LAPACK/GCC runtime libraries
    # beside opensees.so; Nuitka's static analysis does not follow them, so
    # carry the whole lib/ directory across explicitly.
    OS_LIB="$("$PYTHON" -c 'import importlib.util, os; s = importlib.util.find_spec("openseespylinux"); print(os.path.join(os.path.dirname(s.origin), "lib") if s else "")' 2>/dev/null || true)"
    if [[ -n "$OS_LIB" && -d "$OS_LIB" ]]; then
        ARGS+=("--include-data-dir=$OS_LIB=openseespylinux/lib")
    fi
else
    # Separate build: keep OpenSees out of the binary so it stays a clean
    # GPL-3.0 bundle.
    ARGS+=(
        --nofollow-import-to=openseespy
        --nofollow-import-to=openseespylinux
        --nofollow-import-to=openseespywin
        --nofollow-import-to=openseespymac
    )
fi

ARGS+=("$ENTRY")

INTERFACE="cli"
[[ "$GUI" -eq 1 ]] && INTERFACE="gui"
MODE="separate"
[[ "$WITH_OPENSEES" -eq 1 ]] && MODE="with-opensees"
echo "build_nuitka.sh: interface=$INTERFACE mode=$MODE"
"$PYTHON" -m nuitka "${ARGS[@]}"

if [[ "$WITH_OPENSEES" -eq 1 && "$GUI" -eq 0 ]]; then
    # The bundled BLAS/LAPACK/GCC libraries resolve only via LD_LIBRARY_PATH at
    # runtime (same rationale as .github/workflows/ci.yml).  Emit a launcher
    # that sets it before exec'ing the real binary.
    LAUNCHER="$ROOT/build/nuitka/$OUTPUT_NAME"
    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
here="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="\$here/$OUTPUT_NAME.dist/openseespylinux/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "\$here/$OUTPUT_NAME.dist/$OUTPUT_NAME" "\$@"
EOF
    chmod +x "$LAUNCHER"
    echo "build_nuitka.sh: licensed launcher -> $LAUNCHER"
fi

if [[ "$GUI" -eq 1 ]]; then
    echo "build_nuitka.sh: built -> $ROOT/build/nuitka/ (look for $OUTPUT_NAME.app)"
else
    echo "build_nuitka.sh: built -> $ROOT/build/nuitka/$OUTPUT_NAME.dist/"
fi
