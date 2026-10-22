"""Fixed HTTPS/mTLS sink adapter; payloads cannot choose an endpoint or redirect."""

import ssl

import httpx

from .contracts import Rejected, strict_json


class HTTPSink:
    def __init__(self, url, ca, cert, key):
        if not url.startswith("https://") or len(url) > 512 or "?" in url or "#" in url:
            raise ValueError("A fixed HTTPS sink is required")
        context = ssl.create_default_context(cafile=ca)
        context.load_cert_chain(cert, key)
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            verify=context,
            timeout=2,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=8),
        )

    async def dispatch(self, operation):
        async with self.client.stream(
            "POST", "/operations/" + operation["effect_id"], json=operation
        ) as response:
            return await self._read(response)

    async def lookup(self, operation):
        async with self.client.stream(
            "GET", "/operations/" + operation["effect_id"]
        ) as response:
            if response.status_code == 404:
                return {k: operation[k] for k in ("effect_id", "block_hash")} | dict(
                    state="UNKNOWN"
                )
            return await self._read(response)

    async def _read(self, response):
        response.raise_for_status()
        body = bytearray()
        async for segment in response.aiter_bytes():
            if len(body) + len(segment) > 65536:
                raise Rejected("SINK_RESPONSE_CAPACITY", "HELD")
            body.extend(segment)
        return strict_json(bytes(body), 65536)

    async def close(self):
        await self.client.aclose()
