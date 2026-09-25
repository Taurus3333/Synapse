"""pgvector chunk table + ANN search with tenant filters."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from synapse.platform.schema import Base
from synapse.rag.embeddings import EMBED_DIM, EMBED_MODEL


class ChunkRow(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(64))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    access_level: Mapped[str] = mapped_column(String(32))
    doc_type: Mapped[str] = mapped_column(String(32))
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    contradicts_live_status: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding = mapped_column(Vector(EMBED_DIM))


async def ensure_chunk_index(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_hnsw
            ON document_chunks USING hnsw (embedding vector_cosine_ops)
            """
        )
    )
    await session.commit()


async def delete_chunks_for_document(session: AsyncSession, document_id: str) -> None:
    await session.execute(
        text("DELETE FROM document_chunks WHERE document_id = :d"), {"d": document_id}
    )


async def search_chunks(
    session: AsyncSession,
    *,
    tenant_id: str,
    query_embedding: list[float],
    project_id: str | None = None,
    limit: int = 8,
    max_distance: float = 0.55,
) -> list[ChunkRow]:
    distance = ChunkRow.embedding.cosine_distance(query_embedding)
    stmt = (
        select(ChunkRow, distance.label("distance"))
        .where(
            ChunkRow.tenant_id == tenant_id,
            ChunkRow.embedding_model == EMBED_MODEL,
            ChunkRow.embedding_dim == EMBED_DIM,
        )
        .order_by(distance)
        .limit(limit)
    )
    if project_id:
        stmt = stmt.where(ChunkRow.project_id == project_id)
    result = await session.execute(stmt)
    rows: list[ChunkRow] = []
    for row, dist in result.all():
        # Include non-current (stale) docs — required for live-vs-RAG conflict demos.
        if dist is not None and float(dist) > max_distance:
            continue
        rows.append(row)
    return rows
