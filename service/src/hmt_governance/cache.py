"""Bounded encrypted Redis acceleration; a cache hit never grants authority."""

import hashlib
import os

import redis.asyncio as redis
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import canonical, strict_json


class Cache:
    def __init__(self, settings):
        self.client = (
            redis.from_url(
                settings.redis_url,
                socket_timeout=0.2,
                socket_connect_timeout=0.2,
                max_connections=settings.workers + 2,
            )
            if settings.redis_url and settings.cache_key
            else None
        )
        self.cipher = AESGCM(settings.cache_key) if settings.cache_key else None
        self.namespace = hashlib.sha256(settings.cache_key).hexdigest()[:16]
        self.hits = self.misses = self.errors = 0

    def key(self, block_hash):
        return "hmt:cache:" + self.namespace + ":" + block_hash

    async def get(self, block_hash):
        if not self.client:
            self.misses += 1
            return None
        try:
            value = await self.client.get(self.key(block_hash))
            if value is None:
                self.misses += 1
                return None
            if len(value) > 70000:
                raise ValueError("Oversized cache entry")
            decoded = self.cipher.decrypt(value[:12], value[12:], block_hash.encode())
            result = strict_json(decoded, limit=66000)
            self.hits += 1
            return result
        except Exception:
            self.errors += 1
            return None  # Re-read and verify PostgreSQL; never fabricate success.

    async def put(self, record):
        if not self.client:
            return
        raw = canonical(record).encode()
        if len(raw) > 65536:
            return
        try:
            nonce = os.urandom(12)
            value = nonce + self.cipher.encrypt(
                nonce, raw, record["block_hash"].encode()
            )
            await self.client.set(self.key(record["block_hash"]), value, ex=60)
        except Exception:
            self.errors += 1

    async def close(self):
        if self.client:
            await self.client.aclose()
