from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from archguard.analysis.scoring import ArchDebtResult, LayerScores
from archguard.utils.severity import Severity


@dataclass
class ViolationDetail:
    """A single violation found during analysis."""

    layer: int
    module: str
    message: str
    commit_sha: str
    file_path: str
    line: int = 0
    explanation: str = ""
    severity: Severity = Severity.LOW


@dataclass
class AnalysisResult:
    """Complete result of the analysis pipeline."""

    archdebt: ArchDebtResult
    violations: list[ViolationDetail] = field(default_factory=list)
    layer_scores: LayerScores = field(
        default_factory=lambda: LayerScores(0.0, 0.0, 0.0, 0.0),
    )
    modules_analyzed: int = 0
    changed_files: list[str] = field(default_factory=list)
    commit_sha: str = "unknown"
    skipped: bool = False
    skip_reason: str = ""
    fail_fast_triggered: bool = False
    skipped_layers_names: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    parse_failures: list[Any] = field(default_factory=list)
    partial_analysis: bool = False
