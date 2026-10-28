# Identifies who is calling: anyone on the public chat page (by IP address), or a holder of
# a client or attorney API key sent in the X-API-Key header.

# auth.py
import hashlib
import os
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from app import db

KEY_PREFIX = 'jlg_'

# Behind a load balancer, request.client.host is the balancer, not the visitor. Only trust
# X-Forwarded-For when a proxy we control sets it, or anyone could spoof their identity.
TRUST_PROXY_HEADERS = os.environ.get('TRUST_PROXY_HEADERS') == '1'


@dataclass
class Caller:
    # 'public', 'client', or 'attorney'.
    role: str
    # Stable identity for rate limiting: 'ip:<address>' or 'key:<id>'.
    identity: str
    api_key_id: int | None = None
    hourly_limit: int | None = None


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    # Keys are long random strings, so a fast hash is enough; there's nothing to brute-force.
    return hashlib.sha256(key.encode()).hexdigest()


def client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            # AWS load balancers append the real client address as the last entry.
            return forwarded.split(',')[-1].strip()
    return request.client.host if request.client else 'unknown'


async def get_caller(request: Request, x_api_key: str | None = Header(default=None)) -> Caller:
    if x_api_key is None:
        return Caller(role='public', identity=f'ip:{client_ip(request)}')
    key = await db.find_api_key(hash_key(x_api_key))
    if key is None:
        # A wrong key is an error, not a silent downgrade to public access.
        raise HTTPException(status_code=401, detail='Invalid or revoked API key.')
    return Caller(role=key.role, identity=f'key:{key.id}', api_key_id=key.id, hourly_limit=key.hourly_limit)

