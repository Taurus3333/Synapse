"""synapse-eval — run the golden regression suite (LLM-free multihop).

Requires a seeded Postgres (same as integration tests):
  synapse-seed --profile ci --seed 42
  synapse-eval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from synapse.auth.principal import Principal
from synapse.data.generate import generate
from synapse.eval.cases import load_suite
from synapse.eval.runner import run_suite
from synapse.eval.score import score_answer
from synapse.memory.ltm import LongTermMemory
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, load_dataset, session_factory
from synapse.tools.runtime import ToolSession


async def _build_tools(*, profile: str, seed: int) -> tuple[Database, ToolSession]:
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    await create_schema(db.engine)
    sessions = session_factory(db.engine)
    dataset = generate(seed=seed, profile=profile)
    async with sessions() as session:
        await load_dataset(session, dataset, password=settings.demo_password)
    admin = next(u for u in dataset.users if u.email == "uma.berg.0@northwind.example")
    ltm = LongTermMemory(sessions)
    await ltm.write(
        tenant_id=admin.tenant_id,
        user_id=admin.id,
        project_key="ATLAS",
        kind="fact",
        content="Prior note: Harbor vendor SDK slip is the top Atlas delivery risk.",
        confidence=0.8,
    )
    tools = ToolSession(
        principal=Principal(
            user_id=admin.id, tenant_id=admin.tenant_id, role=admin.role.value
        ),
        sessions=sessions,
        embedder=None,
        ltm=ltm,
        max_calls=28,
    )
    return db, tools


async def _async_main(args: argparse.Namespace) -> int:
    clear_settings_cache()
    db, tools = await _build_tools(profile=args.profile, seed=args.seed)
    try:
        report = await run_suite(tools, case_ids=args.case or None)
        payload = report.to_dict()
        payload["profile"] = args.profile
        payload["seed"] = args.seed
        payload["mode"] = "multihop"

        if args.fixture_answer:
            # Optional offline answer checks against a JSON fixture
            fixture = json.loads(Path(args.fixture_answer).read_text(encoding="utf-8"))
            case = load_suite([fixture["case_id"]])[0]
            ans = score_answer(
                case,
                answer=str(fixture.get("answer") or ""),
                citations=list(fixture.get("citations") or []),
                rejected_citations=list(fixture.get("rejected_citations") or []),
            )
            payload["answer_fixture"] = ans.to_dict()
            if not ans.passed:
                payload["passed"] = False

        text = json.dumps(payload, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        print(text)
        print(
            f"pass_rate={payload['pass_rate']} "
            f"n_passed={payload['n_passed']}/{payload['n_cases']} "
            f"passed={payload['passed']}"
        )
        return 0 if payload["passed"] else 1
    finally:
        await db.close()
        clear_settings_cache()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Synapse golden eval (deterministic multihop scoring)."
    )
    parser.add_argument("--profile", default="ci", choices=["ci", "full"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--case",
        action="append",
        help="Case id to run (repeatable). Default: full suite.",
    )
    parser.add_argument("--out", type=Path, help="Write JSON report to this path.")
    parser.add_argument(
        "--fixture-answer",
        type=Path,
        help="Optional JSON with answer/citations for offline answer scoring.",
    )
    args = parser.parse_args()
    code = asyncio.run(_async_main(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
