import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import services
from app.db import KnowledgeEntry

REFERENCE = KnowledgeEntry(
    category='firm',
    question='How much does an initial consultation cost?',
    answer='The initial consultation is free.',
    sources=['Rule 4-1.5'],
    distance=0.1,
)

QUERY_ID = uuid4()


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def fake_response(stop_reason='end_turn', text='It is free.'):
    return SimpleNamespace(
        stop_reason=stop_reason,
        model='claude-opus-5-5',
        usage=SimpleNamespace(input_tokens=120, output_tokens=30),
        content=[SimpleNamespace(type='text', text=text)],
    )


@pytest.fixture
def fake_claude(monkeypatch):
    async def fake_embed_query(text):
        return [0.0]

    async def fake_search_similar(embedding):
        return [REFERENCE]

    messages = FakeMessages(fake_response())
    messages.logged = []

    async def fake_log_query(log):
        messages.logged.append(log)
        return QUERY_ID

    monkeypatch.setattr(services, 'embed_query', fake_embed_query)
    monkeypatch.setattr(services.db, 'search_similar', fake_search_similar)
    monkeypatch.setattr(services.db, 'log_query', fake_log_query)
    monkeypatch.setattr(services, 'client', SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def test_build_user_message_without_references():
    assert services.build_user_message('Is it free?', []) == 'Is it free?'


def test_build_user_message_with_references():
    message = services.build_user_message('Is it free?', [REFERENCE])
    assert '<answer>The initial consultation is free.</answer>' in message
    assert '<sources>Rule 4-1.5</sources>' in message
    assert message.endswith('<question>Is it free?</question>')


def test_process_query_sends_references_to_claude(fake_claude):
    result = asyncio.run(services.process_query('Is the consultation free?'))
    assert result == services.QueryResult(query_id=QUERY_ID, response='It is free.')
    sent = fake_claude.calls[0]['messages'][0]['content']
    assert REFERENCE.answer in sent


def test_process_query_logs_answer(fake_claude):
    asyncio.run(services.process_query('Is the consultation free?'))
    [log] = fake_claude.logged
    assert log.status == 'answered'
    assert log.response == 'It is free.'
    assert log.references == [REFERENCE]
    assert (log.model, log.input_tokens, log.output_tokens) == ('claude-opus-5-5', 120, 30)
    assert log.latency_ms >= log.llm_ms >= 0


def test_process_query_logs_and_raises_on_refusal(fake_claude):
    fake_claude.response = fake_response(stop_reason='refusal', text='')
    with pytest.raises(services.QueryRefusedError) as exc_info:
        asyncio.run(services.process_query('anything'))
    assert exc_info.value.query_id == QUERY_ID
    assert fake_claude.logged[0].status == 'refused'
    assert fake_claude.logged[0].response is None


def test_process_query_logs_api_errors(fake_claude):
    fake_claude.response = RuntimeError('API unavailable')
    with pytest.raises(RuntimeError):
        asyncio.run(services.process_query('anything'))
    assert fake_claude.logged[0].status == 'error'
    assert 'API unavailable' in fake_claude.logged[0].error
