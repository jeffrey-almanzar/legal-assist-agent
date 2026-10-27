from uuid import uuid4

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.routers import legal_queries
from app.services import QueryRefusedError, QueryResult

client = TestClient(app)

QUERY_ID = uuid4()


def test_api_root():
    response = client.get('/')
    assert response.status_code == 200
    assert response.json() == {'message': 'Welcome to the Legal Assistance API'}


def test_query_returns_answer(monkeypatch):
    async def fake_process_query(query: str) -> QueryResult:
        return QueryResult(query_id=QUERY_ID, response=f'Answer to: {query}')

    monkeypatch.setattr(legal_queries, 'process_query', fake_process_query)
    response = client.post('/query', json={'query': 'What is the process for divorce?'})
    assert response.status_code == 200
    assert response.json()['query_id'] == str(QUERY_ID)
    assert response.json()['response'] == 'Answer to: What is the process for divorce?'
    assert response.json()['disclaimer'] == legal_queries.DISCLAIMER


def test_query_refused(monkeypatch):
    async def fake_process_query(query: str) -> QueryResult:
        raise QueryRefusedError(QUERY_ID)

    monkeypatch.setattr(legal_queries, 'process_query', fake_process_query)
    response = client.post('/query', json={'query': 'something'})
    assert response.status_code == 422


def test_query_rejects_empty_body():
    response = client.post('/query', json={'query': ''})
    assert response.status_code == 422


def test_feedback_is_saved(monkeypatch):
    saved = []

    async def fake_add_feedback(query_id, rating, comment):
        saved.append((query_id, rating, comment))
        return 7

    monkeypatch.setattr(db, 'add_feedback', fake_add_feedback)
    response = client.post(
        '/feedback', json={'query_id': str(QUERY_ID), 'rating': 'down', 'comment': 'Wrong deadline'}
    )
    assert response.status_code == 201
    assert response.json() == {'feedback_id': 7}
    assert saved == [(QUERY_ID, 'down', 'Wrong deadline')]


def test_feedback_unknown_query(monkeypatch):
    async def fake_add_feedback(query_id, rating, comment):
        raise db.QueryNotFoundError(query_id)

    monkeypatch.setattr(db, 'add_feedback', fake_add_feedback)
    response = client.post('/feedback', json={'query_id': str(QUERY_ID), 'rating': 'up'})
    assert response.status_code == 404


def test_feedback_rejects_invalid_rating():
    response = client.post('/feedback', json={'query_id': str(QUERY_ID), 'rating': 'meh'})
    assert response.status_code == 422
