import importlib.metadata

try:
    # This reads the dynamic version from the environment metadata
    __version__ = importlib.metadata.version("fea_toolkit")
except importlib.metadata.PackageNotFoundError:
    # Fallback if package is not installed via pip
    __version__ = "0.0.0.dev0+unknown"

def ops_version() -> str:
    ops_ver = importlib.metadata.version("openseespy")
    return ops_ver


# ── Convenience re-exports ──────────────────────────────────────────

from fea_toolkit.report import generate_report  # noqa: E402, F401
