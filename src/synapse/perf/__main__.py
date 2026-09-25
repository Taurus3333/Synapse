"""synapse-perf — measure ask/multihop latency + token/cost against a live API.

Requires a running API (seeded + ingested):
  synapse-api
  synapse-perf --out docs/perf_results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from synapse.perf.harness import run_harness


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure Synapse /v1/ask and /v1/multihop latency + token/cost."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="uma.berg.0@northwind.example")
    parser.add_argument("--password", default="synapse-demo")
    parser.add_argument("--project-key", default="ATLAS")
    parser.add_argument("--ask-repeats", type=int, default=3)
    parser.add_argument("--multihop-repeats", type=int, default=5)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/perf_results.json"),
        help="Write JSON report (default: docs/perf_results.json).",
    )
    args = parser.parse_args()

    try:
        report = run_harness(
            base_url=args.base_url,
            email=args.email,
            password=args.password,
            project_key=args.project_key,
            ask_repeats=args.ask_repeats,
            multihop_repeats=args.multihop_repeats,
        )
    except Exception as exc:
        print(f"perf harness failed: {exc}", file=sys.stderr)
        sys.exit(2)

    report["measured_at"] = datetime.now(UTC).isoformat()
    text = json.dumps(report, indent=2)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")

    for sc in report["scenarios"]:
        lat = sc["latency"]
        cost = sc["cost_usd_est"]["per_successful_ask"]
        usage = sc["usage_totals"]
        print(
            f"{sc['id']}: n={sc['n']} ok={usage.get('n_ok')} "
            f"p50={lat['p50_ms']}ms p95={lat['p95_ms']}ms "
            f"avg_tokens={usage.get('avg_total_tokens')} "
            f"avg_chat_calls={usage.get('avg_chat_calls')} "
            f"est_usd/ask={cost['total_usd']}"
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
