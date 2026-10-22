"""Stable wire identities and immutable byte/ledger records."""
import hashlib
import json
from dataclasses import dataclass

GENESIS_HASH = "0" * 64

def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(payload):
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GovernanceError(RuntimeError):
    pass


class QuarantineError(GovernanceError):
    pass


class HoldError(GovernanceError):
    pass


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    start: int
    end: int
    data: bytes
    fingerprint: int
    reason: str


@dataclass(frozen=True, slots=True)
class Transaction:
    session_id: str
    sequence_number: int
    source_agent: str
    target_entity: str
    payload: str
    previous_hash: str
    expected_hash: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    session_id: str
    sequence_number: int
    source_agent: str
    target_entity: str
    payload_hash: str
    previous_hash: str
    block_hash: str
    timestamp_ns: int
    verified: bool = True


def block_material(tx, payload_hash):
    # Exclude the nondeterministic receipt timestamp and optional assertion.
    return dict(version=1, session_id=tx.session_id,
                sequence_number=tx.sequence_number, source_agent=tx.source_agent,
                target_entity=tx.target_entity, payload_hash=payload_hash,
                previous_hash=tx.previous_hash)
