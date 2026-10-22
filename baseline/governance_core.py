"""Byte-preserving CDC and a fail-closed, in-memory governance reference engine."""
import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from types import MappingProxyType

GENESIS_HASH = "0" * 64

def canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

def sha256_text(payload):
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()

class GovernanceError(RuntimeError):
    pass

class QuarantineError(GovernanceError):
    pass

@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    data: bytes
    fingerprint: int
    reason: str

class RabinCDCScanner:
    """Polynomial rolling CDC (Rabin–Karp style, not a GF(2) Rabin fingerprint).

    Hash the last W bytes in base 256 modulo 2**61-1. A separate boundary
    modulus controls cuts. feed() yields chunks independent of packetization;
    finish() emits the tail. Chunks remain bytes, even across UTF-8 characters.
    The rolling window continues across cuts, helping content resynchronize.
    """

    def __init__(
        self,
        block_size_target=512,
        window_size=64,
        min_size=None,
        max_size=None,
        target=1,
    ):
        if (
            type(block_size_target) is not int
            or block_size_target < 2
        ):
            raise ValueError(
                "block_size_target must be an integer >= 2"
            )
        if (
            type(window_size) is not int
            or window_size < 1
        ):
            raise ValueError(
                "window_size must be a positive integer"
            )
        self.modulus = block_size_target
        self.window_size = window_size
        self.min_size = (
            max(window_size, block_size_target // 4)
            if min_size is None
            else min_size
        )
        self.max_size = (
            block_size_target * 4
            if max_size is None
            else max_size
        )
        if not (
            type(self.min_size) is int
            and type(self.max_size) is int
            and window_size
            <= self.min_size
            <= self.max_size
        ):
            raise ValueError(
                "Require window_size <= min_size <= max_size"
            )
        if (
            type(target) is not int
            or not 0 <= target < self.modulus
        ):
            raise ValueError(
                "target must be inside the boundary modulus"
            )
        self.target = target
        self.base = 256
        self.field_prime = (1 << 61) - 1
        self._remove_factor = pow(
            self.base, window_size - 1, self.field_prime
        )
        self._window = deque()
        self._buffer = bytearray()
        self._fingerprint = 0
        self._offset = 0
        self._chunk_start = 0
        self._index = 0
        self._finished = False

    def _cut(self, reason):
        chunk = Chunk(
            self._index,
            self._chunk_start,
            self._offset,
            bytes(self._buffer),
            self._fingerprint,
            reason,
        )
        self._buffer.clear()
        self._chunk_start = self._offset
        self._index += 1
        return chunk

    def feed(self, data):
        if self._finished:
            raise RuntimeError(
                "Cannot feed after finish()"
            )
        if not isinstance(
            data, (bytes, bytearray, memoryview)
        ):
            raise TypeError(
                "feed() requires bytes; encode text once at the boundary"
            )
        for byte in data:
            if len(self._window) == self.window_size:
                outgoing = self._window.popleft()
                self._fingerprint = (
                    self._fingerprint
                    - outgoing * self._remove_factor
                ) % self.field_prime
            self._fingerprint = (
                self._fingerprint * self.base + byte
            ) % self.field_prime
            self._window.append(byte)
            self._buffer.append(byte)
            self._offset += 1
            if len(self._buffer) >= self.min_size:
                if (
                    self._fingerprint % self.modulus
                    == self.target
                ):
                    yield self._cut("content")
                elif len(self._buffer) >= self.max_size:
                    yield self._cut("max size")

    def finish(self):
        if self._finished:
            return []
        self._finished = True
        return (
            [self._cut("tail")] if self._buffer else []
        )

    def chunk_stream(self, text_stream):
        """Convenience API for a fresh scanner; byte offsets are authoritative."""
        return [
            *self.feed(text_stream.encode("utf-8")),
            *self.finish(),
        ]

@dataclass(frozen=True)
class Transaction:
    session_id: str
    sequence_number: int
    source_agent: str
    target_entity: str
    payload: str
    previous_hash: str
    expected_hash: str | None = None

@dataclass(frozen=True)
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
    return dict(
        version=1,
        session_id=tx.session_id,
        sequence_number=tx.sequence_number,
        source_agent=tx.source_agent,
        target_entity=tx.target_entity,
        payload_hash=payload_hash,
        previous_hash=tx.previous_hash,
    )

class GovernanceLedgerEngine:
    """Single-consumer queue, thread-offloaded SHA-256, append-only public view.

    This is a reference boundary, not durable storage or identity attestation.
    A queued receipt is pending; only a verified receipt permits ingestion.
    """

    def __init__(
        self, queue_size=64, max_payload_bytes=2_000_000
    ):
        if (
            type(queue_size) is not int
            or queue_size < 1
        ):
            raise ValueError(
                "queue_size must be positive"
            )
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.max_payload_bytes = max_payload_bytes
        self._ledger = {}
        self._heads = {}
        self._receipts = {}
        self._payload_hashes = set()
        self._quarantined = set()
        self._events = []
        self._worker = None
        self._closed = False

    @property
    def ledger(self):
        return MappingProxyType(self._ledger)

    @property
    def events(self):
        return tuple(
            MappingProxyType(event)
            for event in self._events
        )

    def head(self, session_id):
        return self._heads.get(
            session_id, (-1, GENESIS_HASH)
        )

    def _event(
        self, event, session_id, sequence_number, detail
    ):
        self._events.append(
            dict(
                order=len(self._events),
                event=event,
                session_id=session_id,
                sequence=sequence_number,
                detail=detail,
            )
        )

    def quarantine(
        self, session_id, sequence_number, reason
    ):
        self._quarantined.add(session_id)
        self._event(
            "SYSTEM_QUARANTINE",
            session_id,
            sequence_number,
            reason,
        )

    async def start(self):
        if self._closed:
            raise GovernanceError("Engine is closed")
        if self._worker is None:
            self._worker = asyncio.create_task(
                self.run_background_governance_loop()
            )
        return self

    async def close(self):
        self._closed = True
        if self._worker is not None:
            await self.queue.join()
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    @staticmethod
    def _valid_hash(value):
        return (
            isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value)
            is not None
        )

    def _parse(self, frame):
        tx = Transaction(**frame)
        for field in (
            tx.session_id,
            tx.source_agent,
            tx.target_entity,
        ):
            if (
                not isinstance(field, str)
                or not field.strip()
                or len(field) > 256
            ):
                raise ValueError(
                    "Identifiers must be nonempty strings <= 256 characters"
                )
        if (
            type(tx.sequence_number) is not int
            or tx.sequence_number < 0
        ):
            raise ValueError(
                "sequence_number must be a nonnegative integer"
            )
        if not isinstance(tx.payload, str):
            raise ValueError("payload must be a string")
        if (
            len(tx.payload.encode("utf-8"))
            > self.max_payload_bytes
        ):
            raise ValueError(
                "Payload exceeds configured size limit"
            )
        if not self._valid_hash(tx.previous_hash):
            raise ValueError(
                "previous_hash must be 64 lowercase hex characters"
            )
        if (
            tx.expected_hash is not None
            and not self._valid_hash(tx.expected_hash)
        ):
            raise ValueError(
                "expected_hash must be 64 lowercase hex characters"
            )
        return tx

    async def governance_commit_state(self, **frame):
        if (
            self._closed
            or self._worker is None
            or self._worker.done()
        ):
            raise GovernanceError(
                "Start a live engine before submitting"
            )
        try:
            tx = self._parse(frame)
        except (
            ValueError,
            TypeError,
            UnicodeError,
        ) as exc:
            return dict(
                status="rejected", reason=str(exc)
            )
        if tx.session_id in self._quarantined:
            return dict(
                status="quarantined",
                reason="Session is quarantined",
            )
        token = uuid.uuid4().hex
        try:
            self.queue.put_nowait((token, tx))
        except asyncio.QueueFull:
            return dict(
                status="busy",
                reason="Queue is full; retry without advancing sequence",
            )
        self._receipts[token] = dict(
            status="pending", tracking_token=token
        )
        self._event(
            "QUEUED",
            tx.session_id,
            tx.sequence_number,
            "Awaiting background verification",
        )
        return dict(self._receipts[token])

    def governance_get_status(self, tracking_token):
        return dict(
            self._receipts.get(
                tracking_token, dict(status="unknown")
            )
        )

    async def wait(self, tracking_token):
        # Demo convenience. MCP clients poll governance_get_status instead.
        await self.queue.join()
        return self.governance_get_status(
            tracking_token
        )

    async def run_background_governance_loop(self):
        while True:
            token, tx = await self.queue.get()
            try:
                if tx.session_id in self._quarantined:
                    raise QuarantineError(
                        "Session is quarantined"
                    )
                seq, head_hash = self.head(
                    tx.session_id
                )
                if tx.sequence_number != seq + 1:
                    raise QuarantineError(
                        f"Expected sequence {seq + 1}; received {tx.sequence_number}"
                    )
                if not hmac.compare_digest(
                    tx.previous_hash, head_hash
                ):
                    raise QuarantineError(
                        "Predecessor does not match the verified session head"
                    )
                payload_hash = await asyncio.to_thread(
                    sha256_text, tx.payload
                )
                if (
                    tx.expected_hash is not None
                    and not hmac.compare_digest(
                        payload_hash, tx.expected_hash
                    )
                ):
                    raise QuarantineError(
                        "Payload SHA-256 assertion mismatch"
                    )
                block_hash = await asyncio.to_thread(
                    sha256_text,
                    canonical_json(
                        block_material(tx, payload_hash)
                    ),
                )
                # A concurrent verification gate may have quarantined the session
                # while the hashing thread was running. Never commit after that.
                if tx.session_id in self._quarantined:
                    raise QuarantineError(
                        "Session was quarantined during verification"
                    )
                record = LedgerRecord(
                    tx.session_id,
                    tx.sequence_number,
                    tx.source_agent,
                    tx.target_entity,
                    payload_hash,
                    tx.previous_hash,
                    block_hash,
                    time.time_ns(),
                )
                self._ledger[
                    (tx.session_id, tx.sequence_number)
                ] = record
                self._heads[tx.session_id] = (
                    tx.sequence_number,
                    block_hash,
                )
                self._payload_hashes.add(payload_hash)
                self._receipts[token] = dict(
                    status="verified",
                    tracking_token=token,
                    **asdict(record),
                )
                self._event(
                    "COMMITTED",
                    tx.session_id,
                    tx.sequence_number,
                    block_hash,
                )
            except Exception as exc:
                self.quarantine(
                    tx.session_id,
                    tx.sequence_number,
                    str(exc),
                )
                self._receipts[token] = dict(
                    status="quarantined",
                    tracking_token=token,
                    reason=str(exc),
                )
            finally:
                self.queue.task_done()

    async def governance_verify_payload(
        self, payload, expected_hash
    ):
        if not isinstance(
            payload, str
        ) or not self._valid_hash(expected_hash):
            return dict(
                verified=False,
                registered=False,
                reason="Malformed payload or digest",
            )
        try:
            if (
                len(payload.encode("utf-8"))
                > self.max_payload_bytes
            ):
                return dict(
                    verified=False,
                    registered=False,
                    reason="Payload too large",
                )
            actual = await asyncio.to_thread(
                sha256_text, payload
            )
        except UnicodeError:
            return dict(
                verified=False,
                registered=False,
                reason="Invalid UTF-8 text",
            )
        registered = (
            expected_hash in self._payload_hashes
        )
        matches = hmac.compare_digest(
            actual, expected_hash
        )
        return dict(
            verified=registered and matches,
            registered=registered,
            actual_hash=actual,
            expected_hash=expected_hash,
            reason="Verified against committed content"
            if registered and matches
            else "Digest mismatch"
            if not matches
            else "Digest is not in this ledger",
        )

    async def verify_envelope(self, envelope):
        """Bind ingestion to a committed envelope, not just self-claimed content."""
        tx = self._parse(envelope["transaction"])
        record = self._ledger.get(
            (tx.session_id, tx.sequence_number)
        )
        supplied_block = envelope.get("ledger_hash", "")
        result = await self.governance_verify_payload(
            tx.payload, envelope.get("payload_hash", "")
        )
        if tx.session_id in self._quarantined:
            raise QuarantineError(
                "Session is quarantined"
            )
        if (
            not result["verified"]
            or record is None
            or not self._valid_hash(supplied_block)
            or not hmac.compare_digest(
                record.block_hash, supplied_block
            )
            or record.payload_hash
            != result.get("actual_hash")
            or block_material(tx, record.payload_hash)
            != block_material(
                record, record.payload_hash
            )
        ):
            self.quarantine(
                tx.session_id,
                tx.sequence_number,
                "Incoming envelope failed payload or lineage verification",
            )
            raise QuarantineError(
                "Incoming envelope failed verification"
            )
        return record

class AgentGateway:
    """Enforce hooks in this Python boundary; a prompt alone cannot enforce them."""

    def __init__(
        self,
        engine,
        session_id,
        agent_id="primary-agent",
    ):
        self.engine = engine
        self.session_id = session_id
        self.agent_id = agent_id
        self.context = []

    async def wrap_output(
        self, payload, target_entity="tool-boundary"
    ):
        seq, parent = self.engine.head(self.session_id)
        frame = dict(
            session_id=self.session_id,
            sequence_number=seq + 1,
            source_agent=self.agent_id,
            target_entity=target_entity,
            payload=payload,
            previous_hash=parent,
        )
        receipt = (
            await self.engine.governance_commit_state(
                **frame
            )
        )
        if receipt["status"] != "pending":
            raise GovernanceError(
                receipt.get("reason", receipt["status"])
            )
        committed = await self.engine.wait(
            receipt["tracking_token"]
        )
        if committed["status"] != "verified":
            self.context.clear()
            raise QuarantineError(
                committed.get("reason", "Commit failed")
            )
        return dict(
            transaction=frame,
            payload_hash=committed["payload_hash"],
            ledger_hash=committed["block_hash"],
        )

    async def execute_verified(self, envelope, tool):
        try:
            transaction = envelope["transaction"]
            if (
                transaction["session_id"]
                != self.session_id
            ):
                raise QuarantineError(
                    "Envelope belongs to another session"
                )
            await self.engine.verify_envelope(envelope)
            if (
                transaction["target_entity"]
                != self.agent_id
            ):
                raise QuarantineError(
                    "Envelope is addressed to another entity"
                )
        except (
            GovernanceError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            self.context.clear()
            self.engine.quarantine(
                self.session_id,
                -1,
                f"Hash/chain hook halted ingestion: {exc}",
            )
            raise QuarantineError(
                "Context cleared; tool execution halted"
            ) from exc
        self.context.append(envelope["ledger_hash"])
        return tool(transaction["payload"])