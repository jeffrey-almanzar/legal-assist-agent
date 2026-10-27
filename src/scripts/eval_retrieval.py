# Measures knowledge base retrieval against labeled queries in evals/retrieval_cases.json.
# Each case names the entry that should be sent to Claude, or null for off-topic queries
# that should get no references. Free to run: no Claude calls, only the local embedding
# model and the database. Exits 1 if any case fails, so it can gate CI.
# Usage (from src/): python -m scripts.eval_retrieval

# eval_retrieval.py
import asyncio
import json
import sys
from pathlib import Path

from app import db
from app.embeddings import embed_query

CASES_PATH = Path(__file__).resolve().parents[2] / 'evals' / 'retrieval_cases.json'


async def main() -> int:
    cases = json.loads(CASES_PATH.read_text())
    await db.connect()
    try:
        results = [(case, await db.search_similar(await embed_query(case['query']))) for case in cases]
    finally:
        await db.close()

    on_topic = [(c, refs) for c, refs in results if c['expected']]
    off_topic = [(c, refs) for c, refs in results if not c['expected']]
    found = [(c, refs) for c, refs in on_topic if c['expected'] in [r.question for r in refs]]
    first = [(c, refs) for c, refs in found if refs[0].question == c['expected']]
    clean = [(c, refs) for c, refs in off_topic if not refs]

    failures = [(c, refs) for c, refs in on_topic if (c, refs) not in found]
    failures += [(c, refs) for c, refs in off_topic if refs]
    for case, refs in failures:
        got = ', '.join(f'{r.question} ({r.distance:.3f})' for r in refs) or 'nothing'
        print(f'FAIL {case["query"]!r}\n     expected: {case["expected"] or "nothing"}\n     got:      {got}')

    print(
        f'\nSent to Claude (recall):       {len(found)}/{len(on_topic)}'
        f'\nRanked first:                  {len(first)}/{len(on_topic)}'
        f'\nOff-topic with no references:  {len(clean)}/{len(off_topic)}'
        f'\nCutoff (MAX_DISTANCE):         {db.MAX_DISTANCE}'
    )
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
