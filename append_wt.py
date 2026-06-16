import tempfile
import uuid
import shutil
import subprocess
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path

@contextmanager
def git_worktree(repo_root: Path | str, sha: str) -> Iterator[Path]:
    repo_root = Path(repo_root).resolve()
    wt_id = uuid.uuid4().hex
    tmp_dir = Path(tempfile.mkdtemp(prefix="archguard_wt_"))
    wt_path = tmp_dir / wt_id
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(wt_path), sha], cwd=str(repo_root), check=True, capture_output=True)
        yield wt_path
    finally:
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=str(repo_root), check=False, capture_output=True)
        except Exception:
            pass
        if tmp_dir.exists():
            def onerror(func, path, exc_info):
                import stat
                import os
                if not os.access(path, os.W_OK):
                    os.chmod(path, stat.S_IWUSR)
                    func(path)
                else:
                    raise
            if hasattr(shutil, "onexc"):
                shutil.rmtree(tmp_dir, onexc=onerror)
            else:
                shutil.rmtree(tmp_dir, onerror=onerror)
