"""RAG: chunking, embeddings, pgvector store, retrieval."""

from synapse.rag.chunking import chunk_text
from synapse.rag.embeddings import EMBED_DIM, EMBED_MODEL, Embedder
from synapse.rag.retrieve import DocHit, retrieve

__all__ = [
    "EMBED_DIM",
    "EMBED_MODEL",
    "DocHit",
    "Embedder",
    "chunk_text",
    "retrieve",
]
