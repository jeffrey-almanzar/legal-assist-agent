# Applies db/init.sql to the database. Every statement is IF NOT EXISTS, so it is safe
# to re-run and adds new tables to an existing database without touching its data.
# Usage (from src/): python -m scripts.init_db

# init_db.py
import asyncio
from pathlib import Path

import asyncpg

from app.db import connection_options

SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'db' / 'init.sql'


async def main() -> None:
    conn = await asyncpg.connect(**connection_options())
    try:
        await conn.execute(SCHEMA_PATH.read_text())
    finally:
        await conn.close()
    print(f'Applied {SCHEMA_PATH}')


if __name__ == '__main__':
    asyncio.run(main())
