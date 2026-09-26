"""CLI: ingest documents into pgvector. Needs SYNAPSE_OPENAI_API_KEY."""

from __future__ import annotations

import argparse
import asyncio

from synapse.platform.config import get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, session_factory
from synapse.rag import store as _store  # noqa: F401 — register ChunkRow on Base
from synapse.rag.embeddings import Embedder
from synapse.rag.ingest import ingest_all_documents


async def _run(tenant_id: str | None) -> None:
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    embedder = Embedder(key)
    db = Database(settings.database_dsn())
    await db.connect()
    try:
        await create_schema(db.engine)
        async with session_factory(db.engine)() as session:
            stats = await ingest_all_documents(session, embedder, tenant_id=tenant_id)
        print(f"ingested documents={stats['documents']} chunks={stats['chunks']}")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default=None)
    args = parser.parse_args()
    asyncio.run(_run(args.tenant_id))


if __name__ == "__main__":
    main()
