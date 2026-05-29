import traceback

try:
    import numpy as np
    import numpy.typing as npt
    from sentence_transformers import SentenceTransformer
except Exception as e:
    traceback.print_exc()
