# Syncs the knowledge base with a JSON file: adds and updates its entries and removes
# any entry that is no longer in the file, so the file stays the source of truth.
# Usage (from src/): python -m scripts.seed [path/to/knowledge_base.json]

# seed.py
import asyncio
import json
import sys
from pathlib import Path

from app import db
from app.embeddings import embed_documents

DEFAULT_PATH = Path(__file__).resolve().parents[2] / 'db' / 'knowledge_base.json'


async def main(path: Path) -> None:
    entries = [
        db.KnowledgeEntry(
            category=item['category'],
            question=item['question'],
            answer=item['answer'],
            sources=item.get('sources', []),
            aliases=item.get('aliases', []),
        )
        for item in json.loads(path.read_text())
    ]
    # Embed every phrasing (question + aliases), but not the answers: matching a
    # query against similar questions retrieves better than matching the answer text.
    phrasings = [[entry.question, *entry.aliases] for entry in entries]
    flat = iter(embed_documents([text for group in phrasings for text in group]))
    embeddings = [[next(flat) for _ in group] for group in phrasings]

    await db.connect()
    try:
        await db.sync_entries(list(zip(entries, embeddings)))
    finally:
        await db.close()
    print(f'Synced {len(entries)} entries ({sum(map(len, phrasings))} phrasings) from {path}')


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH))
