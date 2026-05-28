import re
from pathlib import Path

def patch_cloud():
    p = Path("llm/cloud.py")
    text = p.read_text(encoding="utf-8")
    
    # 1. Top level
    text = text.replace(
        "from typing import Any, TYPE_CHECKING\n",
        "from typing import Any, TYPE_CHECKING\n\n"
        "try:\n"
        "    import anthropic\n"
        "    _ML_AVAILABLE = True\n"
        "except ImportError:\n"
        "    _ML_AVAILABLE = False\n"
        "    anthropic = None  # type: ignore[assignment]\n"
    )
    
    # 2. _call_api
    old_call = '    def _call_api(self, prompt: str, model: str) -> tuple[str, str]:\n        """Call the Anthropic API. Lazy-imports the SDK."""\n        import anthropic  # lazy import\n'
    new_call = '    def _call_api(self, prompt: str, model: str) -> tuple[str, str]:\n        """Call the Anthropic API. Lazy-imports the SDK."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )\n'
    text = text.replace(old_call, new_call)
    
    p.write_text(text, encoding="utf-8")

def patch_duplication():
    p = Path("analysis/duplication.py")
    text = p.read_text(encoding="utf-8")
    
    # Top level
    text = text.replace(
        "import numpy as np\nimport numpy.typing as npt\n",
        "try:\n"
        "    import faiss\n"
        "    import numpy as np\n"
        "    import numpy.typing as npt\n"
        "    _ML_AVAILABLE = True\n"
        "except ImportError:\n"
        "    _ML_AVAILABLE = False\n"
        "    faiss = None  # type: ignore[assignment]\n"
        "    np = None  # type: ignore[assignment]\n"
        "    npt = None  # type: ignore[assignment]\n"
    )
    
    # build_index
    text = text.replace(
        '        Returns ``(index, ordered_keys)``.\n        """\n        try:\n            import faiss  # lazy import\n        except (ImportError, AttributeError):\n            return None, []',
        '        Returns ``(index, ordered_keys)``.\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    
    # _l2_to_cosine
    text = text.replace(
        '        Clamped to ``[0.0, 1.0]``.\n        """\n        return np.clip',
        '        Clamped to ``[0.0, 1.0]``.\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )\n        return np.clip'
    )
    
    # analyze_module
    text = text.replace(
        '        """Run duplication analysis for a single module."""\n        # 1. Check cache staleness',
        '        """Run duplication analysis for a single module."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )\n        # 1. Check cache staleness'
    )
    
    p.write_text(text, encoding="utf-8")

def patch_semantic():
    p = Path("analysis/semantic.py")
    text = p.read_text(encoding="utf-8")
    
    # Top level
    text = text.replace(
        "import numpy as np\nimport numpy.typing as npt\n",
        "try:\n"
        "    import numpy as np\n"
        "    import numpy.typing as npt\n"
        "    from sentence_transformers import SentenceTransformer\n"
        "    _ML_AVAILABLE = True\n"
        "except ImportError:\n"
        "    _ML_AVAILABLE = False\n"
        "    np = None  # type: ignore[assignment]\n"
        "    npt = None  # type: ignore[assignment]\n"
        "    SentenceTransformer = None  # type: ignore[assignment]\n"
    )
    
    # cosine_distance
    text = text.replace(
        '    """``1 - cosine_similarity``, clamped to ``[0.0, 1.0]``."""\n    norm_a',
        '    """``1 - cosine_similarity``, clamped to ``[0.0, 1.0]``."""\n    if not _ML_AVAILABLE:\n        raise RuntimeError(\n            "ML dependencies are not installed. Run: pip install archguard[ml]"\n        )\n    norm_a'
    )
    
    # embed_chunks
    text = text.replace(
        '        Returns ``{"{file_path}::{function_name}": embedding}``.\n        """\n        result: dict',
        '        Returns ``{"{file_path}::{function_name}": embedding}``.\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )\n        result: dict'
    )
    
    old_try = '            try:\n                from sentence_transformers import SentenceTransformer  # lazy\n                model = SentenceTransformer("all-MiniLM-L6-v2")\n                texts = [c.source for c in to_embed]\n                embeddings = model.encode(texts, batch_size=batch_size)\n                model_name = "all-MiniLM-L6-v2"\n            except ImportError:\n                embeddings = [np.zeros(384, dtype=np.float32) for _ in to_embed]\n                model_name = "none"\n'
    new_try = '            model = SentenceTransformer("all-MiniLM-L6-v2")\n            texts = [c.source for c in to_embed]\n            embeddings = model.encode(texts, batch_size=batch_size)\n            model_name = "all-MiniLM-L6-v2"\n'
    text = text.replace(old_try, new_try)
    
    # compute_centroid
    text = text.replace(
        '        Raises ``ValueError`` if *embeddings* is empty.\n        """\n        if not embeddings:',
        '        Raises ``ValueError`` if *embeddings* is empty.\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )\n        if not embeddings:'
    )
    
    # compute_drift
    text = text.replace(
        '        """Compute semantic drift for *module_name* given changed files."""\n        # 1. Load',
        '        """Compute semantic drift for *module_name* given changed files."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )\n        # 1. Load'
    )
    
    p.write_text(text, encoding="utf-8")

def patch_cache_embeddings():
    p = Path("cache/embeddings.py")
    text = p.read_text(encoding="utf-8")
    
    # Top level
    text = text.replace(
        "import numpy as np\nimport numpy.typing as npt\n",
        "try:\n"
        "    import numpy as np\n"
        "    import numpy.typing as npt\n"
        "    _ML_AVAILABLE = True\n"
        "except ImportError:\n"
        "    _ML_AVAILABLE = False\n"
        "    np = None  # type: ignore[assignment]\n"
        "    npt = None  # type: ignore[assignment]\n"
    )
    
    text = text.replace(
        '        """Return stored embedding if *content_hash* matches, else ``None``."""',
        '        """Return stored embedding if *content_hash* matches, else ``None``."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    text = text.replace(
        '        """Upsert an embedding into the cache."""',
        '        """Upsert an embedding into the cache."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    text = text.replace(
        '        """Return ``(centroid_array, content_hash)`` or ``None``."""',
        '        """Return ``(centroid_array, content_hash)`` or ``None``."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    text = text.replace(
        '        """Upsert a module centroid."""',
        '        """Upsert a module centroid."""\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    text = text.replace(
        '        keys format: "file_path::function_name::content_hash"\n        """',
        '        keys format: "file_path::function_name::content_hash"\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    text = text.replace(
        '        items format: "file_path::function_name::content_hash::model_name" -> embedding\n        """',
        '        items format: "file_path::function_name::content_hash::model_name" -> embedding\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    text = text.replace(
        '        Values are ``(embedding_array, content_hash)``.\n        """',
        '        Values are ``(embedding_array, content_hash)``.\n        """\n        if not _ML_AVAILABLE:\n            raise RuntimeError(\n                "ML dependencies are not installed. Run: pip install archguard[ml]"\n            )'
    )
    
    p.write_text(text, encoding="utf-8")

def patch_scoring():
    p = Path("analysis/scoring.py")
    text = p.read_text(encoding="utf-8")
    
    # Top level
    text = text.replace(
        "import numpy as np\nimport numpy.typing as npt\n",
        "try:\n"
        "    import numpy as np\n"
        "    import numpy.typing as npt\n"
        "    from scipy.optimize import nnls as _nnls\n"
        "    _ML_AVAILABLE = True\n"
        "except ImportError:\n"
        "    _ML_AVAILABLE = False\n"
        "    np = None  # type: ignore[assignment]\n"
        "    npt = None  # type: ignore[assignment]\n"
        "    _nnls = None  # type: ignore[assignment]\n"
    )
    
    text = text.replace(
        '    ``should_fail_ci = composite_breach OR per_component_breach``.\n    """\n    layer_values',
        '    ``should_fail_ci = composite_breach OR per_component_breach``.\n    """\n    if not _ML_AVAILABLE:\n        raise RuntimeError(\n            "ML dependencies are not installed. Run: pip install archguard[ml]"\n        )\n    layer_values'
    )
    
    text = text.replace(
        '    Falls back to ``DEFAULT_WEIGHTS`` on any failure.\n    """\n    if not historical_scores:',
        '    Falls back to ``DEFAULT_WEIGHTS`` on any failure.\n    """\n    if not _ML_AVAILABLE:\n        raise RuntimeError(\n            "ML dependencies are not installed. Run: pip install archguard[ml]"\n        )\n    if not historical_scores:'
    )
    
    old_try = '    try:\n        from scipy.optimize import nnls as _nnls  # lazy import\n\n        a_matrix'
    new_try = '    try:\n        a_matrix'
    text = text.replace(old_try, new_try)
    
    p.write_text(text, encoding="utf-8")

try:
    patch_cloud()
    print("Cloud OK")
except Exception as e: print("Cloud err:", e)

try:
    patch_duplication()
    print("Duplication OK")
except Exception as e: print("Duplication err:", e)

try:
    patch_semantic()
    print("Semantic OK")
except Exception as e: print("Semantic err:", e)

try:
    patch_cache_embeddings()
    print("Cache Embeddings OK")
except Exception as e: print("Cache Embeddings err:", e)

try:
    patch_scoring()
    print("Scoring OK")
except Exception as e: print("Scoring err:", e)
