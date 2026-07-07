import uuid
from archguard.dashboard.routes import runs

def test_get_deps_name_error(monkeypatch):
    result = runs.get_deps(job_id=str(uuid.uuid4()))
    assert isinstance(result, dict)
