"""Retrieve document hits with DATA framing and stale-vs-live flags."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from synapse.evidence.frame import frame_retrieved_data
from synapse.rag.embeddings import Embedder
from synapse.rag.store import search_chunks


@dataclass(frozen=True)
class DocHit:
    chunk_id: str
    document_id: str
    project_id: str
    text: str
    doc_type: str
    authored_at: str
    framed: str
    stale_vs_live: bool
    distance: float | None = None


async def retrieve(
    session: AsyncSession,
    embedder: Embedder,
    *,
    tenant_id: str,
    query: str,
    project_id: str | None = None,
    limit: int = 8,
) -> list[DocHit]:
    vector = await embedder.embed_one(query)
    rows = await search_chunks(
        session,
        tenant_id=tenant_id,
        query_embedding=vector,
        project_id=project_id,
        limit=limit,
    )
    out: list[DocHit] = []
    for row in rows:
        stale = bool(row.contradicts_live_status)
        framed = frame_retrieved_data(
            document_id=row.document_id,
            chunk_id=row.id,
            authored_at=row.authored_at.isoformat(),
            text=row.text,
            stale_vs_live=stale,
        )
        out.append(
            DocHit(
                chunk_id=row.id,
                document_id=row.document_id,
                project_id=row.project_id,
                text=row.text,
                doc_type=row.doc_type,
                authored_at=row.authored_at.isoformat(),
                framed=framed,
                stale_vs_live=stale,
            )
        )
    return out
