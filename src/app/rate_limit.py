# Per-caller hourly request limits, so no single caller can run up the Claude bill.

# rate_limit.py
import random
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app import db
from app.auth import Caller

# Requests per hour, by endpoint and caller role. An API key's own hourly_limit, when set,
# replaces its role's /query limit (e.g. a high limit for the load-test key).
LIMITS = {
    'query': {'public': 20, 'client': 200, 'attorney': 500},
    'feedback': {'public': 30, 'client': 200, 'attorney': 1000},
}


def limit_for(caller: Caller, bucket: str) -> int:
    if bucket == 'query' and caller.hourly_limit is not None:
        return caller.hourly_limit
    return LIMITS[bucket][caller.role]


async def enforce(caller: Caller, bucket: str) -> None:
    count, window_start = await db.count_request(caller.identity, bucket)
    # Clean up expired windows now and then, rather than on a schedule.
    if random.random() < 0.01:
        await db.delete_old_rate_limits()

    if count > limit_for(caller, bucket):
        window_end = window_start + timedelta(hours=1)
        retry_after = max(1, int((window_end - datetime.now(timezone.utc)).total_seconds()))
        minutes = -(-retry_after // 60)
        raise HTTPException(
            status_code=429,
            detail=f'Too many requests. Try again in {minutes} minute{"s" if minutes != 1 else ""}.',
            headers={'Retry-After': str(retry_after)},
        )
