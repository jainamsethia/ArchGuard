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


def test_primary_model_default_is_defined_exactly_once():
    """The primary/fallback model defaults must live in one place.

    They used to be duplicated as literals in cloud.py and llm_inference.py,
    which let the explanation path and the contract-inference path silently
    drift onto different models. Both now resolve through
    archguard.llm.gemini, so this asserts no call site reintroduces its own
    literal default.
    """
    import ast
    import pathlib

    targets = [
        pathlib.Path("archguard/llm/cloud.py"),
        pathlib.Path("archguard/contract/llm_inference.py"),
        pathlib.Path("archguard/llm/advisor.py"),
    ]
    offenders = []
    for path in targets:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("getenv", "get")
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value
                in ("ARCHGUARD_PRIMARY_MODEL", "ARCHGUARD_FALLBACK_MODEL")
            ):
                offenders.append(f"{path}: {node.args[0].value}")
    assert offenders == [], (
        "Model defaults must come from archguard.llm.gemini, not be redefined: "
        f"{offenders}"
    )

    from archguard.llm import gemini

    assert gemini.DEFAULT_PRIMARY_MODEL
    assert gemini.DEFAULT_FALLBACK_MODEL
