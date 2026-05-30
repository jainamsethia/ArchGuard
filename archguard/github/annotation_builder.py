from archguard.github.checks import CheckAnnotation
from typing import List, Dict, Any


def violations_to_annotations(
    violations: List[Dict[str, Any]],
) -> List[CheckAnnotation]:
    """Convert archguard violations to GitHub check annotations."""
    annotations = []
    for v in violations:
        # Try to extract file and line from violation
        file_path = v.get("file") or v.get("source_file", "")
        if not file_path:
            continue

        line = v.get("line", 1)
        from typing import Literal

        level: Literal["failure", "warning"] = (
            "failure" if v.get("severity") == "critical" else "warning"
        )

        annotations.append(
            CheckAnnotation(
                path=file_path,
                start_line=max(1, line),
                end_line=max(1, line),
                annotation_level=level,
                title=f"ArchGuard: {v.get('type', 'Violation')}",
                message=v.get("message", "Architectural violation detected"),
                raw_details=v.get("explanation"),
            )
        )
    return annotations
