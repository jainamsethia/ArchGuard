import tempfile
import time
from pathlib import Path

from archguard.analysis.semantic import FunctionChunk, SemanticAnalyzer
from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache


def benchmark() -> None:
    print("Benchmarking SemanticAnalyzer Model Loading (Caching fix)")
    # We clear the global cache just in case
    from archguard.analysis.semantic import _GLOBAL_MODEL_CACHE

    _GLOBAL_MODEL_CACHE.clear()

    temp_db_path = Path(tempfile.mktemp(suffix=".db"))
    db = EmbeddingDB(temp_db_path)
    cache = EmbeddingCache(db)
    analyzer = SemanticAnalyzer(cache)

    chunks1 = [FunctionChunk("file1.py", "f1", "def f1(): pass", "h1")]
    chunks2 = [FunctionChunk("file2.py", "f2", "def f2(): pass", "h2")]

    print("Loading first module (should take time to load model)...")
    start = time.time()
    analyzer.embed_chunks(chunks1)
    end1 = time.time()

    print(f"First module time: {end1 - start:.2f}s")

    print("Loading second module (should be almost instant)...")
    start = time.time()
    analyzer.embed_chunks(chunks2)
    end2 = time.time()

    print(f"Second module time: {end2 - start:.2f}s")

    ratio = (end1 - start) / max(end2 - start, 1e-6)
    print(f"Second module was ~{ratio:.1f}x faster.")


if __name__ == "__main__":
    benchmark()
