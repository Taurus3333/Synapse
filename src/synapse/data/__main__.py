from __future__ import annotations

import argparse
import json
from pathlib import Path

from synapse.data.generate import generate
from synapse.data.profiles import PROFILES
from synapse.domain.dataset import assert_referential_integrity


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Synapse synthetic corpus.")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="ci")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON to this path. Directories are created.",
    )
    args = parser.parse_args()
    dataset = generate(seed=args.seed, profile=args.profile)
    assert_referential_integrity(dataset)
    counts = dataset.counts()
    print(f"profile={dataset.profile} seed={dataset.seed} as_of={dataset.as_of}")
    print(f"total={dataset.total_records()}")
    for name, count in counts.items():
        print(f"  {name}={count}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(dataset.to_jsonable()), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
