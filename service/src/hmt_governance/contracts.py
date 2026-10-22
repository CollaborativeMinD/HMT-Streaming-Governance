"""Version-1 wire identities, strict framing, and host-owned settings."""

import hashlib
import json
import re
from dataclasses import dataclass, field

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
KEY = re.compile(r"nodekey:[0-9a-f]{64}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
GENESIS = "0" * 64


class Rejected(RuntimeError):
    def __init__(self, code, disposition="REJECTED"):
        self.code, self.disposition = code, disposition
        super().__init__(code)


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identity(value):
    if type(value) is not str or not ID.fullmatch(value):
        raise Rejected("INVALID_IDENTIFIER")
    return value


def strict_json(raw, limit=1_100_000):
    if len(raw) > limit:
        raise Rejected("BODY_CAPACITY")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Rejected("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    # Bound nesting before recursive JSON decoding; braces inside strings do not count.
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 32:
                raise Rejected("JSON_DEPTH_CAPACITY")
        elif byte in (93, 125):
            depth -= 1
    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict:
            raise ValueError()
        canonical(value).encode("utf-8")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected("INVALID_JSON") from None


def frame(value, max_payload=1_000_000):
    required = {
        "session_id",
        "sequence_number",
        "source_agent",
        "target_entity",
        "payload",
        "previous_hash",
    }
    if (
        type(value) is not dict
        or not required <= value.keys()
        or value.keys() - required - {"expected_hash"}
    ):
        raise Rejected("INVALID_FRAME")
    value = dict(value)
    for key in ("session_id", "source_agent", "target_entity"):
        identity(value[key])
    if (
        type(value["sequence_number"]) is not int
        or not 0 <= value["sequence_number"] < 2**53
    ):
        raise Rejected("INVALID_SEQUENCE")
    if type(value["previous_hash"]) is not str or not HASH.fullmatch(
        value["previous_hash"]
    ):
        raise Rejected("INVALID_PARENT")
    if type(value["payload"]) is not str or len(value["payload"]) > max_payload:
        raise Rejected("PAYLOAD_CAPACITY")
    try:
        if len(value["payload"].encode()) > max_payload:
            raise Rejected("PAYLOAD_CAPACITY")
    except UnicodeError:
        raise Rejected("INVALID_UNICODE") from None
    expected = value.get("expected_hash")
    if expected is not None and (
        type(expected) is not str or not HASH.fullmatch(expected)
    ):
        raise Rejected("INVALID_DIGEST")
    return value


def block(value, payload_hash):
    material = {
        key: value[key]
        for key in (
            "session_id",
            "sequence_number",
            "source_agent",
            "target_entity",
            "previous_hash",
        )
    }
    return digest(canonical(material | dict(version=1, payload_hash=payload_hash)))


@dataclass(frozen=True, slots=True)
class Settings:
    dsn: str = field(repr=False)
    redis_url: str = field(default="", repr=False)
    cache_key: bytes = field(default=b"", repr=False)
    max_pending: int = 64
    max_records: int = 100000
    max_sessions: int = 1024
    workers: int = 4
    transaction_seconds: float = 10.0
    poll_seconds: float = 0.05
    max_jobs: int = 16

    def __post_init__(self):
        for name in (
            "max_pending",
            "max_records",
            "max_sessions",
            "workers",
            "max_jobs",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError("Invalid bound: " + name)
        for name in ("transaction_seconds", "poll_seconds"):
            if type(getattr(self, name)) not in (int, float) or not 0 < getattr(
                self, name
            ) < float("inf"):
                raise ValueError("Invalid time bound")
        from psycopg.conninfo import conninfo_to_dict
        from urllib.parse import parse_qs, urlsplit

        if conninfo_to_dict(self.dsn).get("sslmode") != "verify-full":
            raise ValueError("PostgreSQL requires sslmode=verify-full")
        if self.redis_url:
            url = urlsplit(self.redis_url)
            query = parse_qs(url.query)
            if (url.scheme != "rediss" or not url.hostname
                or query.get("ssl_check_hostname") != ["true"]
                or query.get("ssl_cert_reqs", ["required"]) != ["required"]):
                raise ValueError("Redis requires verified TLS and hostname checking")
        if type(self.cache_key) is not bytes or (self.cache_key and len(self.cache_key) != 32):
            raise ValueError("Cache key must be 32 bytes")
