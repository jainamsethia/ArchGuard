import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from pydriller import Repository

from archguard.evolution.snapshots import CommitHealthSnapshot, EvolutionReport
from archguard.evolution.worktree import git_worktree

logger = logging.getLogger(__name__)

class ArchitectureEvolutionTracker:
    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path).resolve()

    def analyze_history(self, max_commits: int = 10, max_workers: int = 4) -> EvolutionReport:
        commits = list(Repository(str(self.repo_path)).traverse_commits())
        if len(commits) > max_commits:
            commits = commits[-max_commits:]
        snapshots = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_commit = {
                executor.submit(self._analyze_commit, commit.hash, commit.author_date.isoformat(), commit.author.name, commit.msg): commit.hash
                for commit in commits
            }
            for future in as_completed(future_to_commit):
                try:
                    snapshot = future.result()
                    if snapshot:
                        snapshots.append(snapshot)
                except Exception as e:
                    logger.error(f"Failed to analyze commit: {e}")
        snapshots.sort(key=lambda s: s.committed_at)
        return EvolutionReport(snapshots=snapshots)

    def _analyze_commit(self, sha: str, committed_at: str, author: str, message: str) -> CommitHealthSnapshot | None:
        with git_worktree(self.repo_path, sha) as wt_path:
            return CommitHealthSnapshot(
                sha=sha,
                committed_at=committed_at,
                health_score=100.0,
                composite_score=0.0,
                layer_scores={},
                violation_count=0,
                author=author,
                message=message
            )
