"""Create schema and seed Postgres from the synthetic generator."""

from __future__ import annotations

import argparse
import asyncio

from synapse.data.generate import generate
from synapse.data.profiles import PROFILES
from synapse.platform.config import get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, load_dataset, session_factory
from synapse.rag import store as _chunk_table  # noqa: F401 — register ChunkRow


async def _run(profile: str, seed: int) -> None:
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    try:
        await create_schema(db.engine)
        dataset = generate(seed=seed, profile=profile)
        async with session_factory(db.engine)() as session:
            counts = await load_dataset(session, dataset, password=settings.demo_password)
        print(f"seeded profile={profile} seed={seed} total={sum(counts.values())}")
        for name, n in counts.items():
            print(f"  {name}={n}")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate + seed Synapse Postgres")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="ci")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    asyncio.run(_run(args.profile, args.seed))


if __name__ == "__main__":
    main()
