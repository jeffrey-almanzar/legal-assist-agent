# Generates text embeddings locally with fastembed for pgvector similarity search.

# embeddings.py
import asyncio
import os

import numpy as np
from fastembed import TextEmbedding

EMBEDDING_MODEL = 'BAAI/bge-small-en-v1.5'
EMBEDDING_DIM = 384  # Must match the VECTOR(...) column in db/init.sql.

_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    # Loaded lazily: the first call downloads the model (~70MB) and caches it.
    global _model
    if _model is None:
        # FASTEMBED_CACHE_PATH lets the Docker image bake the model in at build time.
        _model = TextEmbedding(EMBEDDING_MODEL, cache_dir=os.environ.get('FASTEMBED_CACHE_PATH'))
    return _model


def embed_documents(texts: list[str]) -> list[np.ndarray]:
    return list(_get_model().embed(texts))


async def embed_query(text: str) -> np.ndarray:
    # Embedding is CPU-bound, so run it off the event loop.
    return await asyncio.to_thread(lambda: next(iter(_get_model().query_embed(text))))
