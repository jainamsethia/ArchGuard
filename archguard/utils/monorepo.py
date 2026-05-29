from pathlib import Path

PACKAGE_MARKERS = ["pyproject.toml", "setup.py", "setup.cfg", "package.json"]

def detect_subpackages(root: Path, max_depth: int = 2) -> list[Path]:
    """Find sub-packages in a monorepo by looking for package marker files."""
    packages = []
    
    # We want to skip the root itself if it has a marker, we only want sub-packages
    # But wait, the instruction says: `pkg_dir != root and pkg_dir not in packages`
    for depth in range(1, max_depth + 1):
        pattern = "/".join(["*"] * depth)
        for marker in PACKAGE_MARKERS:
            for match in root.glob(f"{pattern}/{marker}"):
                pkg_dir = match.parent
                if pkg_dir.resolve() != root.resolve() and pkg_dir not in packages:
                    packages.append(pkg_dir)
                    
    return sorted(set(packages))
