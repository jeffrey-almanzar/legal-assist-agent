from datetime import datetime, timezone

import pytest

from app import db


@pytest.fixture(autouse=True)
def no_rate_limit_db(monkeypatch):
    """Unit tests have no database, so count every request as the first in its window."""
    async def fake_count_request(identity, bucket):
        return 1, datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    monkeypatch.setattr(db, 'count_request', fake_count_request)
