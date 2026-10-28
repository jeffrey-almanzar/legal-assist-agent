import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import auth, db, rate_limit, services
from app.main import app
from app.routers import legal_queries
from app.services import QueryResult

client = TestClient(app)

KEYS = {
    'attorney-key': db.ApiKey(id=1, name='Maria', role='attorney', hourly_limit=None),
    'client-key': db.ApiKey(id=2, name='Partner site', role='client', hourly_limit=None),
    'loadtest-key': db.ApiKey(id=3, name='Load test', role='client', hourly_limit=1_000_000),
}


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    by_hash = {auth.hash_key(key): record for key, record in KEYS.items()}

    async def fake_find_api_key(key_hash):
        return by_hash.get(key_hash)

    monkeypatch.setattr(db, 'find_api_key', fake_find_api_key)


@pytest.fixture
def saved_feedback(monkeypatch):
    saved = []

    async def fake_add_feedback(query_id, rating, comment, source, api_key_id):
        saved.append({'source': source, 'api_key_id': api_key_id})
        return len(saved)

    monkeypatch.setattr(db, 'add_feedback', fake_add_feedback)
    return saved


def window_with(count, monkeypatch, minutes_into_hour=45):
    """Make every request look like the `count`-th in a window that started an hour ago or less."""
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes_into_hour)

    async def fake_count_request(identity, bucket):
        return count, start

    monkeypatch.setattr(db, 'count_request', fake_count_request)


def test_generated_keys_are_unique_and_prefixed():
    a, b = auth.generate_key(), auth.generate_key()
    assert a != b and a.startswith('jlg_') and len(a) > 40
    assert auth.hash_key(a) != a


def test_invalid_key_is_rejected(saved_feedback):
    response = client.post('/feedback', headers={'X-API-Key': 'jlg_wrong'},
                           json={'query_id': str(uuid4()), 'rating': 'up'})
    assert response.status_code == 401
    assert saved_feedback == []


def test_feedback_records_who_rated(saved_feedback):
    body = {'query_id': str(uuid4()), 'rating': 'up'}
    client.post('/feedback', json=body)
    client.post('/feedback', headers={'X-API-Key': 'client-key'}, json=body)
    client.post('/feedback', headers={'X-API-Key': 'attorney-key'}, json=body)
    assert saved_feedback == [
        {'source': 'public', 'api_key_id': None},
        {'source': 'client', 'api_key_id': 2},
        {'source': 'attorney', 'api_key_id': 1},
    ]


def test_query_over_limit_returns_429(monkeypatch):
    window_with(rate_limit.LIMITS['query']['public'] + 1, monkeypatch)
    response = client.post('/query', json={'query': 'Is the first consultation free?'})
    assert response.status_code == 429
    assert 0 < int(response.headers['Retry-After']) <= 15 * 60 + 1
    assert 'Try again in 15 minutes' in response.json()['detail']


def test_query_at_limit_is_allowed(monkeypatch):
    async def fake_process_query(query):
        return QueryResult(query_id=uuid4(), response='ok')

    monkeypatch.setattr(legal_queries, 'process_query', fake_process_query)
    window_with(rate_limit.LIMITS['query']['public'], monkeypatch)
    assert client.post('/query', json={'query': 'hi'}).status_code == 200


def test_limits_depend_on_role_and_key_override():
    public = auth.Caller(role='public', identity='ip:1.2.3.4')
    attorney = auth.Caller(role='attorney', identity='key:1')
    loadtest = auth.Caller(role='client', identity='key:3', hourly_limit=1_000_000)
    assert rate_limit.limit_for(public, 'query') < rate_limit.limit_for(attorney, 'query')
    assert rate_limit.limit_for(loadtest, 'query') == 1_000_000
    # The override only applies to /query.
    assert rate_limit.limit_for(loadtest, 'feedback') == rate_limit.LIMITS['feedback']['client']


def test_client_ip_ignores_forwarded_header_unless_trusted(monkeypatch):
    request = SimpleNamespace(headers={'x-forwarded-for': '6.6.6.6, 203.0.113.9'},
                              client=SimpleNamespace(host='10.0.0.5'))
    monkeypatch.setattr(auth, 'TRUST_PROXY_HEADERS', False)
    assert auth.client_ip(request) == '10.0.0.5'
    monkeypatch.setattr(auth, 'TRUST_PROXY_HEADERS', True)
    # The load balancer appends the real client; earlier entries can be spoofed.
    assert auth.client_ip(request) == '203.0.113.9'


def test_mock_llm_skips_claude(monkeypatch):
    monkeypatch.setattr(services, 'MOCK_LLM', True)
    monkeypatch.setattr(services, 'client', None)  # any real call would fail
    ref = db.KnowledgeEntry(category='firm', question='Is it free?', answer='Yes.')
    response = asyncio.run(services.generate_answer('free?', [ref]))
    assert response.model == 'mock'
    assert 'Is it free?' in services.response_text(response)


def test_failed_cleanup_does_not_fail_the_request(monkeypatch):
    async def failing_cleanup():
        raise RuntimeError('database unavailable')

    async def fake_process_query(query):
        return QueryResult(query_id=uuid4(), response='ok')

    monkeypatch.setattr(db, 'delete_old_rate_limits', failing_cleanup)
    monkeypatch.setattr(rate_limit.random, 'random', lambda: 0.0)  # always run the cleanup
    monkeypatch.setattr(legal_queries, 'process_query', fake_process_query)
    assert client.post('/query', json={'query': 'hi'}).status_code == 200
