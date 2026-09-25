"""synapse-redteam — PyRIT-backed injection / jailbreak regression suite."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from synapse.redteam.converters import pyrit_available
from synapse.redteam.runner import run_full_suite


async def _async_main(args: argparse.Namespace) -> int:
    report = await run_full_suite()
    payload = report.to_dict()
    payload["pyrit_available"] = pyrit_available()
    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    print(text)
    print(
        f"pass_rate={payload['pass_rate']} "
        f"n_passed={payload['n_passed']}/{payload['n_scores']} "
        f"pyrit={payload['pyrit_available']} passed={payload['passed']}"
    )
    if report.failures:
        print("FAILURES:", file=sys.stderr)
        for f in report.failures[:20]:
            print(f"  {f.attack_id}/{f.mutation}: {f.detail}", file=sys.stderr)
    return 0 if report.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Synapse red-team suite (PyRIT converters + policy/framing/grounding)."
    )
    parser.add_argument("--out", type=Path, help="Write JSON report path.")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
