"""ArchGuard — Architectural drift detector for Python codebases."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__: str = version("archguard")
except PackageNotFoundError:
    # Package not installed (e.g., running from source without `pip install -e .`)
    __version__ = "0.0.0.dev0"
