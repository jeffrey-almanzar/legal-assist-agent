# PostgreSQL + pgvector access: connection pool and knowledge base similarity search.

# db.py
import json
import os
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://legal:legal@localhost:5432/legal')

# Cosine distance above which a knowledge base entry is treated as unrelated.
# Calibrated on sample queries: real matches scored up to ~0.37, off-topic ones from ~0.39.
MAX_DISTANCE = 0.37

pool: asyncpg.Pool | None = None


@dataclass
class KnowledgeEntry:
    category: str
    question: str
    answer: str
    sources: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    distance: float = 0.0


@dataclass
class QueryLog:
    query: str
    references: list[KnowledgeEntry]
    retrieval_ms: int
    status: str = 'error'
    response: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    llm_ms: int | None = None
    latency_ms: int = 0
    error: str | None = None


class QueryNotFoundError(Exception):
    """Raised when feedback refers to a query_id that isn't in the log."""


async def connect() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, init=register_vector)


async def close() -> None:
    if pool is not None:
        await pool.close()


async def search_similar(embedding: np.ndarray, limit: int = 4) -> list[KnowledgeEntry]:
    # Fetch extra phrasings, since several can belong to the same entry.
    rows = await pool.fetch(
        '''
        SELECT kb.category, kb.question, kb.answer, kb.sources, p.embedding <=> $1 AS distance
        FROM knowledge_base_phrasings p
        JOIN knowledge_base kb ON kb.id = p.entry_id
        ORDER BY p.embedding <=> $1
        LIMIT $2
        ''',
        embedding,
        limit * 4,
    )
    entries: dict[str, KnowledgeEntry] = {}
    for row in rows:
        # Rows are sorted by distance, so the first phrasing seen is each entry's best match.
        if row['distance'] <= MAX_DISTANCE and row['question'] not in entries:
            entries[row['question']] = KnowledgeEntry(**row)
    return list(entries.values())[:limit]


async def sync_entries(entries: list[tuple[KnowledgeEntry, list[np.ndarray]]]) -> None:
    """Make the knowledge base match `entries` exactly: upsert each one, delete the rest.

    Each entry comes with one embedding per phrasing: its question, then its aliases.
    """
    async with pool.acquire() as conn, conn.transaction():
        for entry, embeddings in entries:
            entry_id = await conn.fetchval(
                '''
                INSERT INTO knowledge_base (category, question, answer, sources)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (question) DO UPDATE
                SET category = EXCLUDED.category, answer = EXCLUDED.answer,
                    sources = EXCLUDED.sources, updated_at = now()
                RETURNING id
                ''',
                entry.category, entry.question, entry.answer, entry.sources,
            )
            await conn.execute('DELETE FROM knowledge_base_phrasings WHERE entry_id = $1', entry_id)
            await conn.executemany(
                'INSERT INTO knowledge_base_phrasings (entry_id, text, embedding) VALUES ($1, $2, $3)',
                [(entry_id, text, emb) for text, emb in zip([entry.question, *entry.aliases], embeddings)],
            )
        await conn.execute(
            'DELETE FROM knowledge_base WHERE question <> ALL($1::text[])',
            [entry.question for entry, _ in entries],
        )


async def log_query(log: QueryLog) -> UUID:
    references = [{'question': ref.question, 'distance': round(ref.distance, 4)} for ref in log.references]
    return await pool.fetchval(
        '''
        INSERT INTO query_log (query, response, status, references_used, model, input_tokens,
                               output_tokens, retrieval_ms, llm_ms, latency_ms, error)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11)
        RETURNING id
        ''',
        log.query, log.response, log.status, json.dumps(references), log.model, log.input_tokens,
        log.output_tokens, log.retrieval_ms, log.llm_ms, log.latency_ms, log.error,
    )


async def add_feedback(query_id: UUID, rating: str, comment: str | None) -> int:
    try:
        return await pool.fetchval(
            'INSERT INTO feedback (query_id, rating, comment) VALUES ($1, $2, $3) RETURNING id',
            query_id, rating, comment,
        )
    except asyncpg.ForeignKeyViolationError:
        raise QueryNotFoundError(query_id) from None


async def rated_queries(rating: str) -> list[asyncpg.Record]:
    """Answered queries with at least one rating of `rating`, newest first, with their comments."""
    return await pool.fetch(
        '''
        SELECT q.id, q.query, q.response, q.references_used, q.created_at,
               array_remove(array_agg(f.comment ORDER BY f.created_at), NULL) AS comments
        FROM query_log q
        JOIN feedback f ON f.query_id = q.id
        WHERE f.rating = $1 AND q.status = 'answered'
        GROUP BY q.id
        ORDER BY q.created_at DESC
        ''',
        rating,
    )


async def known_phrasings() -> set[str]:
    rows = await pool.fetch('SELECT lower(text) AS text FROM knowledge_base_phrasings')
    return {row['text'] for row in rows}
