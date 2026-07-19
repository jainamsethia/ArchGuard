from fastapi.testclient import TestClient
from archguard.dashboard.app import app

def test_api_routes_take_precedence_over_static_mount():
    """Regression guard for WEB-03: StaticFiles must never shadow an explicit API route."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json().get("status") == "ok"  # confirms the real health_check ran, not a 404 static


def test_route_submodule_import_first_does_not_shadow_api():
    """WEB-03 regression: importing a route submodule before app.py used to
    re-order the route table via circular import, leaving the StaticFiles
    mount before the submodule's API routes (static-404 shadowing). Runs in a
    subprocess because import order is settled per-process."""
    import pathlib
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    code = (
        "from archguard.dashboard.routes import runs\n"
        "from archguard.dashboard.app import app\n"
        "mounts = [i for i, r in enumerate(app.routes) if type(r).__name__ == 'Mount']\n"
        "assert mounts and mounts[-1] == len(app.routes) - 1, f'static mount not last: {mounts}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=repo_root
    )
    assert result.returncode == 0, result.stderr
