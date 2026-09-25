"""synapse-e2e — live API end-to-end + failure validation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from synapse.e2e.validation import (
    resolve_api,
    resolve_password,
    run_validation,
    write_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Synapse live E2E + failure validation against a running API."
    )
    parser.add_argument("--api", default=resolve_api())
    parser.add_argument("--password", default=resolve_password())
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/e2e_results.json"),
        help="Write JSON report (default: docs/e2e_results.json).",
    )
    args = parser.parse_args()
    cases = run_validation(api=args.api, password=args.password)
    payload = write_report(cases, api=args.api, out=args.out)
    print(f"wrote {args.out}")
    for c in cases:
        mark = "PASS" if c.passed else "FAIL"
        extra = f" :: {c.error}" if c.error else ""
        print(f"  [{mark}] {c.id} {c.name}{extra}")
    print(f"pass_rate={payload['n_passed']}/{payload['n_cases']} passed={payload['passed']}")
    sys.exit(0 if payload["passed"] else 1)


if __name__ == "__main__":
    main()
