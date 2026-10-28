# Turns feedback into review material:
# - Prints thumbs-down answers from anyone (public, client, or attorney), with who rated them,
#   their comments, and the references used, to find what went wrong.
# - Writes answers an attorney rated thumbs-up, whose question isn't already in the knowledge
#   base, to a draft file in knowledge_base.json format. Public and client ratings are never
#   promoted. An attorney reviews each draft, fills in category and sources, moves it into
#   db/knowledge_base.json, and runs scripts.seed.
# Usage (from src/): python -m scripts.export_feedback [output.json]

# export_feedback.py
import asyncio
import json
import sys
from pathlib import Path

from app import db

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / 'db' / 'kb_candidates.json'


def print_down_report(rows) -> None:
    print(f'{len(rows)} answer(s) rated down\n')
    for row in rows:
        references = json.loads(row['references_used'])
        print(f'[{row["created_at"]:%Y-%m-%d %H:%M}] {row["id"]}  (rated by: {", ".join(sorted(row["sources"]))})')
        print(f'  Q: {row["query"]}')
        for comment in row['comments']:
            print(f'  Comment: {comment}')
        if references:
            for ref in references:
                print(f'  Reference used: {ref["question"]} (distance {ref["distance"]})')
        else:
            print('  Reference used: none (possible knowledge base gap)')
        print()


def to_candidate(row) -> dict:
    # The log's references are sorted by distance, so the first is the closest existing entry.
    references = json.loads(row['references_used'])
    closest = references[0] if references else None
    return {
        'category': '',
        'question': row['query'],
        'aliases': [],
        'answer': row['response'],
        'sources': [],
        # Review notes; scripts.seed ignores these keys. Remove them when promoting.
        '_query_id': str(row['id']),
        '_feedback': row['comments'],
        # A close match (low distance) means the knowledge base already covers this
        # question; consider adding it as an alias of that entry instead.
        '_closest_entry': closest['question'] if closest else None,
        '_closest_distance': closest['distance'] if closest else None,
    }


async def main(output: Path) -> None:
    await db.connect()
    try:
        down = await db.rated_queries('down', ['public', 'client', 'attorney'])
        up = await db.rated_queries('up', ['attorney'])
        known = await db.known_phrasings()
    finally:
        await db.close()

    print_down_report(down)
    candidates = [to_candidate(row) for row in up if row['query'].strip().lower() not in known]
    # Biggest gaps first: questions with no close existing entry are most worth adding.
    candidates.sort(key=lambda c: -1 if c['_closest_distance'] is None else c['_closest_distance'], reverse=True)
    output.write_text(json.dumps(candidates, indent=2, ensure_ascii=False) + '\n')
    print(f'Wrote {len(candidates)} knowledge base candidate(s) to {output}')


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT))
