from unittest.mock import patch

from archguard.github.checks import ChecksAPIClient, CheckAnnotation
from archguard.github.annotation_builder import violations_to_annotations


def test_violations_to_annotations():
    violations = [
        {
            "file": "src/main.py",
            "line": 10,
            "severity": "critical",
            "type": "Coupling",
            "message": "Cross module import",
        }
    ]
    annotations = violations_to_annotations(violations)
    assert len(annotations) == 1
    assert annotations[0].path == "src/main.py"
    assert annotations[0].start_line == 10
    assert annotations[0].annotation_level == "failure"


@patch("httpx.post")
def test_check_annotations_created_for_violations(mock_post):
    mock_post.return_value.json.return_value = {"id": 123}
    client = ChecksAPIClient(token="test", repo_full_name="owner/repo")

    client.create_check_run(
        name="ArchGuard",
        head_sha="abc123",
        status="completed",
        conclusion="failure",
        title="Test",
        summary="Test",
        annotations=[
            CheckAnnotation(
                path="src/main.py",
                start_line=10,
                end_line=10,
                annotation_level="failure",
                title="Violation",
                message="Cross module import",
            )
        ],
    )

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    assert payload["output"]["annotations"][0]["path"] == "src/main.py"
