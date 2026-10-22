"""OpenBao TLS client: least-privilege runtime token, transit signatures and ephemeral cache DEKs."""

import base64

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .contracts import Rejected, canonical


class OpenBao:
    def __init__(self, url, token, ca):
        if not url.startswith("https://"):
            raise ValueError("OpenBao requires verified HTTPS")
        import ssl

        self.client = httpx.AsyncClient(
            base_url=url,
            verify=ssl.create_default_context(cafile=ca),
            headers={"X-Vault-Token": token},
            timeout=2,
            follow_redirects=False,
            trust_env=False,
        )

    async def _call(self, path, body=None):
        try:
            response = await self.client.request(
                "GET" if body is None else "POST", "/v1/" + path, json=body
            )
            response.raise_for_status()
            return response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError):
            raise Rejected("KEY_SERVICE_UNAVAILABLE", "HELD") from None

    async def cache_key(self):
        data = await self._call("transit/datakey/plaintext/ledger-cache", {})
        value = base64.b64decode(data["plaintext"], validate=True)
        if len(value) != 32:
            raise Rejected("INVALID_DATA_KEY", "HELD")
        return value

    async def sign(self, checkpoint):
        raw = canonical(checkpoint).encode()
        data = await self._call(
            "transit/sign/ledger-checkpoints",
            dict(input=base64.b64encode(raw).decode()),
        )
        return dict(checkpoint=checkpoint, signature=data["signature"])

    async def public_keys(self):
        return (await self._call("transit/keys/ledger-checkpoints"))["keys"]

    async def close(self):
        await self.client.aclose()


def verify_checkpoint(witness, trusted_keys, expected_context):
    try:
        marker, version, signature = witness["signature"].split(":")
        if marker != "vault" or not version.startswith("v"):
            raise ValueError()
        value = witness["checkpoint"]
        if value["context"] != expected_context:
            raise ValueError()
        public = trusted_keys[version[1:]]["public_key"]
        Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public, validate=True)
        ).verify(base64.b64decode(signature, validate=True), canonical(value).encode())
        return value
    except Exception:
        raise Rejected("INVALID_CHECKPOINT", "HELD") from None
