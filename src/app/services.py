# Service that answers legal queries with Claude, grounded in the firm's knowledge base.

# services.py
import time
from dataclasses import dataclass, field
from uuid import UUID

from anthropic import AsyncAnthropic

from app import db
from app.embeddings import embed_query

MODEL = 'claude-opus-5-5'

SYSTEM_PROMPT = (
    'You are the legal information assistant for Bayvale Legal Group, a law firm '
    'in Tampa, Florida. Answer common legal questions clearly and concisely in '
    'plain language. Answers are about Florida law unless the question is clearly '
    'about another jurisdiction, in which case say that the firm practices only in '
    'Florida. You provide general information, not legal advice: when a question '
    "depends on the specifics of someone's situation, say so and recommend "
    "speaking with one of the firm's attorneys.\n\n"
    'Some questions come with reference answers that the firm has approved. When '
    'a reference answer is relevant, base your answer on it and do not contradict '
    'it, and mention the statute or rule it cites where that helps the reader. '
    'Ignore reference answers that are not relevant to the question.'
)

# Reads ANTHROPIC_API_KEY from the environment.
client = AsyncAnthropic()


@dataclass
class QueryResult:
    query_id: UUID
    response: str
    references: list[db.KnowledgeEntry] = field(default_factory=list)


class QueryRefusedError(Exception):
    """Raised when Claude declines to answer a query."""

    def __init__(self, query_id: UUID):
        super().__init__(f'Query {query_id} was refused')
        self.query_id = query_id


def _ms_since(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def build_user_message(query: str, references: list[db.KnowledgeEntry]) -> str:
    if not references:
        return query
    blocks = '\n'.join(
        f'<reference>\n<question>{ref.question}</question>\n<answer>{ref.answer}</answer>\n'
        + (f'<sources>{"; ".join(ref.sources)}</sources>\n' if ref.sources else '')
        + '</reference>'
        for ref in references
    )
    return f'<references>\n{blocks}\n</references>\n\n<question>{query}</question>'


async def generate_answer(query: str, references: list[db.KnowledgeEntry]):
    """Calls Claude with the query and references; shared by the API and the answer eval."""
    return await client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={'effort': 'medium'},
        # On a safety refusal, the API retries on a fallback model in the same call.
        betas=['server-side-fallback-2026-07-01'],
        fallbacks='default',
        messages=[{'role': 'user', 'content': build_user_message(query, references)}],
    )


def response_text(response) -> str:
    return ''.join(block.text for block in response.content if block.type == 'text')


async def process_query(query: str) -> QueryResult:
    start = time.perf_counter()
    references = await db.search_similar(await embed_query(query))
    log = db.QueryLog(query=query, references=references, retrieval_ms=_ms_since(start))

    llm_start = time.perf_counter()
    try:
        response = await generate_answer(query, references)
    except Exception as exc:
        log.status, log.error = 'error', repr(exc)
        log.llm_ms, log.latency_ms = _ms_since(llm_start), _ms_since(start)
        await db.log_query(log)
        raise

    log.llm_ms, log.latency_ms = _ms_since(llm_start), _ms_since(start)
    # The model that served the request, which differs from MODEL after a fallback.
    log.model = response.model
    log.input_tokens, log.output_tokens = response.usage.input_tokens, response.usage.output_tokens
    if response.stop_reason == 'refusal':
        log.status = 'refused'
        raise QueryRefusedError(await db.log_query(log))

    log.status = 'answered'
    log.response = response_text(response)
    return QueryResult(query_id=await db.log_query(log), response=log.response, references=references)
