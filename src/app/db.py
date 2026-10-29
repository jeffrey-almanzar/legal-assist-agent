# PostgreSQL + pgvector access: connection pool and knowledge base similarity search.

# db.py
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

DEFAULT_DATABASE_URL = 'postgresql://legal:legal@localhost:5432/legal'


def connection_options() -> dict:
    """Connection settings from DATABASE_URL, or from separate DB_* variables.

    On AWS, ECS injects the RDS host, user, and password as separate values from Secrets
    Manager; passing them as keywords avoids URL-encoding a generated password.
    """
    if os.environ.get('DB_HOST'):
        return {
            'host': os.environ['DB_HOST'],
            'port': int(os.environ.get('DB_PORT', '5432')),
            'user': os.environ['DB_USER'],
            'password': os.environ['DB_PASSWORD'],
            'database': os.environ.get('DB_NAME', 'legal'),
        }
    return {'dsn': os.environ.get('DATABASE_URL', DEFAULT_DATABASE_URL)}


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
    pool = await asyncpg.create_pool(**connection_options(), init=register_vector)


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


async def add_feedback(
    query_id: UUID, rating: str, comment: str | None, source: str = 'public', api_key_id: int | None = None
) -> int:
    try:
        return await pool.fetchval(
            '''
            INSERT INTO feedback (query_id, rating, comment, source, api_key_id)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            ''',
            query_id, rating, comment, source, api_key_id,
        )
    except asyncpg.ForeignKeyViolationError:
        raise QueryNotFoundError(query_id) from None


async def rated_queries(rating: str, sources: list[str]) -> list[asyncpg.Record]:
    """Answered queries rated `rating` by any of `sources`, newest first, with comments and who rated."""
    return await pool.fetch(
        '''
        SELECT q.id, q.query, q.response, q.references_used, q.created_at,
               array_remove(array_agg(f.comment ORDER BY f.created_at), NULL) AS comments,
               array_agg(DISTINCT f.source) AS sources
        FROM query_log q
        JOIN feedback f ON f.query_id = q.id
        WHERE f.rating = $1 AND f.source = ANY($2::text[]) AND q.status = 'answered'
        GROUP BY q.id
        ORDER BY q.created_at DESC
        ''',
        rating, sources,
    )


async def known_phrasings() -> set[str]:
    rows = await pool.fetch('SELECT lower(text) AS text FROM knowledge_base_phrasings')
    return {row['text'] for row in rows}


@dataclass
class ApiKey:
    id: int
    name: str
    role: str
    hourly_limit: int | None


async def find_api_key(key_hash: str) -> ApiKey | None:
    row = await pool.fetchrow(
        'SELECT id, name, role, hourly_limit FROM api_keys WHERE key_hash = $1 AND revoked_at IS NULL',
        key_hash,
    )
    return ApiKey(**row) if row else None


async def create_api_key(name: str, role: str, key_hash: str, hourly_limit: int | None) -> int:
    return await pool.fetchval(
        'INSERT INTO api_keys (name, role, key_hash, hourly_limit) VALUES ($1, $2, $3, $4) RETURNING id',
        name, role, key_hash, hourly_limit,
    )


async def revoke_api_key(key_id: int) -> bool:
    status = await pool.execute(
        'UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL', key_id
    )
    return status == 'UPDATE 1'


async def list_api_keys() -> list[asyncpg.Record]:
    return await pool.fetch('SELECT id, name, role, hourly_limit, created_at, revoked_at FROM api_keys ORDER BY id')


async def count_request(identity: str, bucket: str) -> tuple[int, datetime]:
    """Counts a request in the caller's current one-hour window; returns (count, window_start)."""
    row = await pool.fetchrow(
        '''
        INSERT INTO rate_limits (identity, bucket, window_start, count)
        VALUES ($1, $2, date_trunc('hour', now()), 1)
        ON CONFLICT (identity, bucket, window_start) DO UPDATE SET count = rate_limits.count + 1
        RETURNING count, window_start
        ''',
        identity, bucket,
    )
    return row['count'], row['window_start']


async def delete_old_rate_limits() -> None:
    await pool.execute("DELETE FROM rate_limits WHERE window_start < now() - interval '1 day'")
