"""Ingest document bodies from Postgres into pgvector."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.platform.schema import DocumentRow
from synapse.rag.chunking import chunk_text
from synapse.rag.embeddings import EMBED_DIM, EMBED_MODEL, Embedder
from synapse.rag.store import ChunkRow, delete_chunks_for_document, ensure_chunk_index


async def ingest_all_documents(
    session: AsyncSession, embedder: Embedder, *, tenant_id: str | None = None
) -> dict[str, int]:
    stmt = select(DocumentRow)
    if tenant_id:
        stmt = stmt.where(DocumentRow.tenant_id == tenant_id)
    docs = list((await session.execute(stmt)).scalars())
    chunks_written = 0
    for doc in docs:
        await delete_chunks_for_document(session, doc.id)
        pieces = chunk_text(doc.body)
        if not pieces:
            continue
        vectors = await embedder.embed_many([p.text for p in pieces])
        for piece, vector in zip(pieces, vectors, strict=True):
            session.add(
                ChunkRow(
                    id=f"chk_{uuid4().hex[:16]}",
                    tenant_id=doc.tenant_id,
                    project_id=doc.project_id,
                    document_id=doc.id,
                    chunk_index=piece.index,
                    text=piece.text,
                    embedding_model=EMBED_MODEL,
                    embedding_dim=EMBED_DIM,
                    access_level=doc.access_level,
                    doc_type=doc.doc_type,
                    authored_at=doc.authored_at,
                    is_current=doc.is_current,
                    contradicts_live_status=doc.contradicts_live_status,
                    content_hash=embedder.content_hash(piece.text),
                    embedding=vector,
                )
            )
            chunks_written += 1
        await session.flush()
    await session.commit()
    await ensure_chunk_index(session)
    return {"documents": len(docs), "chunks": chunks_written}
