#!/usr/bin/env bash
# Build a Nuitka "standalone" executable for fea_toolkit.
#
# Two modes, selected by --with-opensees:
#
#   (default) "separate" — OpenSeesPy is *not* bundled.  Analysis is left to a
#             separately-installed OpenSees: the toolkit exports a Tcl model
#             (opensees/builder.py) that the user runs in their own licensed
#             OpenSees.  This binary is clean GPL-3.0-or-later and may be
#             published (e.g. as a GitHub Release asset) without an OpenSees
#             licence.
#
#   --with-opensees — bundles openseespy, the OpenSees core and its bundled
#             BLAS/LAPACK/GCC runtime libraries.  That is redistribution of
#             OpenSees, so this mode refuses to run unless the environment
#             carries OPENSEES_COMMERCIAL_LICENCE=1 (set only once the OSU/UC
#             licences are actually held).
#
# Usage:
#   scripts/build_nuitka.sh                  # separate (no OpenSeesPy)
#   scripts/build_nuitka.sh --with-opensees  # licensed build (gated)
#
# Overridable via the environment: PYTHON (default "python"), PACKAGE (default
# "src/fea_toolkit", the package directory compiled in "-m" mode so
# fea_toolkit.__main__ runs with the correct package context), OUTPUT_NAME
# (default "fea_toolkit").

set -euo pipefail

WITH_OPENSEES=0
for arg in "$@"; do
    case "$arg" in
        --with-opensees) WITH_OPENSEES=1 ;;
        *)
            echo "build_nuitka.sh: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
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
OUTPUT_NAME="${OUTPUT_NAME:-fea_toolkit}"

ARGS=(
    --standalone
    --python-flag=-m
    --assume-yes-for-downloads
    --output-dir="$ROOT/build/nuitka"
    --output-filename="$OUTPUT_NAME"
    --include-package=fea_toolkit
    --include-data-files="$ROOT/LICENSE=LICENSE"
    --include-data-files="$ROOT/THIRD-PARTY-NOTICES.md=THIRD-PARTY-NOTICES.md"
)

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
    # Separate build: keep OpenSees, the [gui] Qt stack, and the macOS pyobjc
    # bridge (AppKit/Foundation, imported by gui/app.py to retitle the menu) out
    # of the binary so it stays a clean GPL-3.0 bundle that builds without an
    # app bundle.
    ARGS+=(
        --nofollow-import-to=openseespy
        --nofollow-import-to=openseespylinux
        --nofollow-import-to=openseespywin
        --nofollow-import-to=openseespymac
        --nofollow-import-to=PySide6
        --nofollow-import-to=pyvistaqt
        --nofollow-import-to=qtpy
        --nofollow-import-to=AppKit
        --nofollow-import-to=Foundation
        --nofollow-import-to=objc
    )
fi

ARGS+=("$PACKAGE")

MODE="separate"
[[ "$WITH_OPENSEES" -eq 1 ]] && MODE="with-opensees"
echo "build_nuitka.sh: mode=$MODE"
"$PYTHON" -m nuitka "${ARGS[@]}"

if [[ "$WITH_OPENSEES" -eq 1 ]]; then
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

echo "build_nuitka.sh: built -> $ROOT/build/nuitka/$OUTPUT_NAME.dist/"
