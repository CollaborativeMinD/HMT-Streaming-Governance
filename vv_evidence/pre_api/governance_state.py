"""Resource limits, session controls and bounded diagnostic state."""
import asyncio
from collections import OrderedDict, deque
from types import MappingProxyType

from governance_contracts import GENESIS_HASH, HoldError, QuarantineError


class EngineState:
    def __init__(self, queue_size=64, max_payload_bytes=2_000_000, *,
                 max_pending_bytes=4_000_000, max_records=10_000,
                 max_sessions=1024, receipt_capacity=1024, event_capacity=1024,
                 hash_offload_bytes=65536, hash_timeout=5.0,
                 wait_timeout=10.0, shutdown_timeout=5.0, yield_every=32,
                 transaction_timeout=10.0, verification_capacity=8,
                 max_verification_bytes=4_000_000):
        limits = dict(queue_size=queue_size, max_payload_bytes=max_payload_bytes,
                      max_pending_bytes=max_pending_bytes, max_records=max_records,
                      max_sessions=max_sessions, receipt_capacity=receipt_capacity,
                      event_capacity=event_capacity, yield_every=yield_every,
                      verification_capacity=verification_capacity,
                      max_verification_bytes=max_verification_bytes)
        for name, value in limits.items():
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
            setattr(self, name, value)
        if type(hash_offload_bytes) is not int or hash_offload_bytes < 0:
            raise ValueError("hash_offload_bytes must be a nonnegative integer")
        for name, value in dict(hash_timeout=hash_timeout, wait_timeout=wait_timeout,
                                shutdown_timeout=shutdown_timeout,
                                transaction_timeout=transaction_timeout).items():
            if type(value) not in (int, float) or not 0 < value < float('inf'):
                raise ValueError(f"{name} must be finite and positive")
            setattr(self, name, value)
        self.hash_offload_bytes = hash_offload_bytes
        self.queue = asyncio.Queue(maxsize=queue_size)
        self._ledger, self._heads, self._locks = {}, {}, {}
        self._pending, self._completed = {}, OrderedDict()
        self._payload_hashes, self._quarantined = set(), set()
        self._held_sessions = {}
        self._events = deque(maxlen=event_capacity)
        self._event_count = self._pending_bytes = 0
        self._inline_jobs = self._thread_jobs = 0
        self._verification_jobs = self._verification_bytes = 0
        self._worker, self._held_reason = None, None
        self._shutdown_task = None
        self._closed = False

    @property
    def ledger(self):
        return MappingProxyType(self._ledger)

    @property
    def events(self):
        return tuple(MappingProxyType(event) for event in self._events)

    def head(self, session_id):
        return self._heads.get(session_id, (-1, GENESIS_HASH))

    def _event(self, event, session_id, sequence_number, detail):
        self._events.append(dict(order=self._event_count, event=event,
                                 session_id=session_id, sequence=sequence_number,
                                 detail=detail))
        self._event_count += 1

    def _register_session(self, session_id):
        if session_id not in self._heads:
            if len(self._heads) >= self.max_sessions:
                raise HoldError("SESSION_CAPACITY")
            self._heads[session_id] = (-1, GENESIS_HASH)

    def session_lock(self, session_id):
        self._register_session(session_id)
        return self._locks.setdefault(session_id, asyncio.Lock())

    def quarantine(self, session_id, sequence_number, reason):
        try:
            self._register_session(session_id)
        except HoldError:
            return  # No capacity to admit an unknown session either.
        if session_id not in self._quarantined:
            self._quarantined.add(session_id)
            self._event("SYSTEM_QUARANTINE", session_id, sequence_number, reason)

    def hold_session(self, session_id, reason):
        self._register_session(session_id)
        self._held_sessions[session_id] = reason
        self._event("SESSION_HOLD", session_id, -1, reason)

    def _hold(self, reason):
        if self._held_reason is None:
            self._held_reason = reason
            self._event("ENGINE_HOLD", "engine", -1, reason)

    def _check_session(self, session_id):
        if session_id in self._quarantined:
            raise QuarantineError("Session is quarantined")
        if self._held_reason or session_id in self._held_sessions:
            raise HoldError(self._held_reason or self._held_sessions[session_id])

    def health(self):
        alive = self._worker is not None and not self._worker.done()
        return dict(state="closed" if self._closed else "held" if self._held_reason else "running" if alive else "stopped",
                    hold_reason=self._held_reason, records=len(self._ledger),
                    sessions=len(self._heads), pending=len(self._pending),
                    pending_bytes=self._pending_bytes, queue_depth=self.queue.qsize(),
                    receipt_cache=len(self._completed), event_cache=len(self._events),
                    events_evicted=max(0, self._event_count - len(self._events)),
                    quarantined_sessions=len(self._quarantined), held_sessions=len(self._held_sessions),
                    inline_hash_jobs=self._inline_jobs, thread_hash_jobs=self._thread_jobs,
                    active_verifications=self._verification_jobs,
                    verification_bytes=self._verification_bytes, worker_alive=alive,
                    limits=dict(max_records=self.max_records, max_sessions=self.max_sessions,
                                receipt_capacity=self.receipt_capacity, event_capacity=self.event_capacity,
                                max_pending_bytes=self.max_pending_bytes,
                                verification_capacity=self.verification_capacity,
                                max_verification_bytes=self.max_verification_bytes))
