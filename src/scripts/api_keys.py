# Creates, lists, and revokes API keys. A new key is printed once and only its hash is
# stored, so save it when it's created.
# Usage (from src/):
#   python -m scripts.api_keys create --name "Maria Lopez" --role attorney
#   python -m scripts.api_keys create --name "Load test" --role client --hourly-limit 1000000
#   python -m scripts.api_keys list
#   python -m scripts.api_keys revoke 3

# api_keys.py
import argparse
import asyncio

from app import db
from app.auth import generate_key, hash_key


async def main(args) -> None:
    await db.connect()
    try:
        if args.command == 'create':
            key = generate_key()
            key_id = await db.create_api_key(args.name, args.role, hash_key(key), args.hourly_limit)
            print(f'Created {args.role} key {key_id} for {args.name!r}. Save it now; it is not shown again:\n\n  {key}\n')
        elif args.command == 'list':
            for row in await db.list_api_keys():
                status = f'revoked {row["revoked_at"]:%Y-%m-%d}' if row['revoked_at'] else 'active'
                limit = f', {row["hourly_limit"]}/hour' if row['hourly_limit'] is not None else ''
                print(f'{row["id"]:>4}  {row["role"]:<9} {row["name"]}{limit}  ({status})')
        elif args.command == 'revoke':
            revoked = await db.revoke_api_key(args.key_id)
            print(f'Revoked key {args.key_id}.' if revoked else f'No active key with id {args.key_id}.')
    finally:
        await db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Manage API keys.')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Create a key and print it once')
    create.add_argument('--name', required=True, help='Who the key is for')
    create.add_argument('--role', required=True, choices=['client', 'attorney'])
    create.add_argument('--hourly-limit', type=int, help="Override the role's /query limit per hour")
    commands.add_parser('list', help='List keys (without the keys themselves)')
    revoke = commands.add_parser('revoke', help='Revoke a key by id')
    revoke.add_argument('key_id', type=int)
    asyncio.run(main(parser.parse_args()))
