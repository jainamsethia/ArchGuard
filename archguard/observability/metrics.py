from dataclasses import dataclass, field
from time import perf_counter
from contextlib import contextmanager

@dataclass
class AnalysisMetrics:
    layer_durations: dict[str, float] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0
    violations_found: int = 0
    files_analyzed: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0

    @contextmanager
    def time_layer(self, layer_name: str):
        start = perf_counter()
        yield
        self.layer_durations[layer_name] = perf_counter() - start

    def to_dict(self) -> dict:
        return {
            "layer_durations": self.layer_durations,
            "cache_hit_rate": self.cache_hits / max(1, self.cache_hits + self.cache_misses),
            "violations_found": self.violations_found,
            "files_analyzed": self.files_analyzed,
            "llm_calls": self.llm_calls,
        }
