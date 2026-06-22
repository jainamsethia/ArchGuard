import re
import subprocess
import sys
from pathlib import Path


def test_ml_and_dashboard_extras_have_requires_dist(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    import zipfile

    wheel = next(tmp_path.glob("archguard-*.whl"))

    metadata = ""
    with zipfile.ZipFile(wheel, "r") as z:
        for name in z.namelist():
            if name.endswith("METADATA"):
                metadata = z.read(name).decode("utf-8")
                break

    assert re.search(r'Requires-Dist:\s*numpy.*extra == "ml"', metadata)
    assert re.search(r'Requires-Dist:\s*fastapi.*extra == "dashboard"', metadata)


def test_no_bak_files_in_package(tmp_path):
    """Guards against stale backup files re-entering the package source tree."""
    import pathlib

    pkg_root = pathlib.Path(__file__).resolve().parents[2] / "archguard"
    bak_files = list(pkg_root.rglob("*.bak"))
    assert bak_files == [], f"Found stale .bak files: {bak_files}"


def test_primary_model_defaults_are_consistent():
    """All call sites defaulting ARCHGUARD_PRIMARY_MODEL must agree, so the
    contract-inference path and the explanation path never silently diverge."""
    import ast
    import pathlib

    targets = [
        pathlib.Path("archguard/llm/cloud.py"),
        pathlib.Path("archguard/contract/llm_inference.py"),
    ]
    defaults = set()
    for path in targets:
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("getenv", "get")
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "ARCHGUARD_PRIMARY_MODEL"
                and isinstance(node.args[1], ast.Constant)
            ):
                defaults.add(node.args[1].value)
    assert len(defaults) == 1, (
        f"Inconsistent ARCHGUARD_PRIMARY_MODEL defaults: {defaults}"
    )
