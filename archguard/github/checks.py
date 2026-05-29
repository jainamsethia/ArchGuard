import httpx
from dataclasses import dataclass
from typing import Literal, Optional, List, Dict, Any

@dataclass
class CheckAnnotation:
    path: str                    # Relative file path from repo root
    start_line: int
    end_line: int
    annotation_level: Literal["notice", "warning", "failure"]
    title: str
    message: str
    raw_details: Optional[str] = None

class ChecksAPIClient:
    ANNOTATION_BATCH_SIZE = 50  # GitHub API limit per request

    def __init__(self, token: str, repo_full_name: str):
        self.token = token
        self.repo = repo_full_name
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create_check_run(
        self,
        name: str,
        head_sha: str,
        status: Literal["queued", "in_progress", "completed"],
        conclusion: Optional[Literal["success", "failure", "neutral", "cancelled", "skipped"]],
        title: str,
        summary: str,
        annotations: List[CheckAnnotation],
    ) -> Dict[str, Any]:
        """Create or update a GitHub check run with annotations."""
        
        # GitHub limits 50 annotations per request — create run, then update with annotations
        payload: Dict[str, Any] = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
            "output": {
                "title": title,
                "summary": summary,
                "annotations": [
                    {
                        "path": a.path,
                        "start_line": a.start_line,
                        "end_line": a.end_line,
                        "annotation_level": a.annotation_level,
                        "title": a.title,
                        "message": a.message,
                        **({"raw_details": a.raw_details} if a.raw_details else {}),
                    }
                    for a in annotations[:self.ANNOTATION_BATCH_SIZE]
                ],
            },
        }
        
        if conclusion:
            payload["conclusion"] = conclusion
            
        response = httpx.post(
            f"https://api.github.com/repos/{self.repo}/check-runs",
            json=payload,
            headers=self.headers,
            timeout=30.0,
        )
        response.raise_for_status()
        from typing import cast
        result = cast(Dict[str, Any], response.json())
        
        # If more than 50 annotations, paginate with PATCH
        if len(annotations) > self.ANNOTATION_BATCH_SIZE:
            check_id = result["id"]
            for i in range(self.ANNOTATION_BATCH_SIZE, len(annotations), self.ANNOTATION_BATCH_SIZE):
                batch = annotations[i:i + self.ANNOTATION_BATCH_SIZE]
                self._update_check_run(check_id, batch)
                
        return result

    def _update_check_run(self, check_id: int, annotations: List[CheckAnnotation]) -> None:
        httpx.patch(
            f"https://api.github.com/repos/{self.repo}/check-runs/{check_id}",
            json={
                "output": {
                    "title": "ArchGuard", 
                    "summary": "",
                    "annotations": [
                        {
                            "path": a.path, 
                            "start_line": a.start_line, 
                            "end_line": a.end_line,
                            "annotation_level": a.annotation_level, 
                            "title": a.title,
                            "message": a.message
                        }
                        for a in annotations
                    ]
                }
            },
            headers=self.headers,
            timeout=30.0,
        ).raise_for_status()
