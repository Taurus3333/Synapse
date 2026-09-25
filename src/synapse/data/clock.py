from datetime import UTC, datetime, timedelta
from random import Random

AS_OF = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
HORIZON_START = datetime(2024, 10, 1, tzinfo=UTC)
Q2_2026_START = datetime(2026, 4, 1, tzinfo=UTC)
Q2_2026_END = datetime(2026, 7, 1, tzinfo=UTC)


def between(rng: Random, start: datetime, end: datetime) -> datetime:
    span = max(int((end - start).total_seconds()), 1)
    return start + timedelta(seconds=rng.randint(0, span))
