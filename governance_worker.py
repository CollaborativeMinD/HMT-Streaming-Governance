"""Supervised verification worker with deadlines and cooperative scheduling."""
import asyncio
import hashlib
import hmac
import time

from governance_contracts import (
    HoldError,
    LedgerRecord,
    QuarantineError,
    block_material,
    canonical_json,
    sha256_text,
)


class WorkerLifecycle:
    async def start(self):
        if self._closed or self._held_reason:
            raise HoldError("Engine is closed or held")
        if self._worker is None:
            self._worker = asyncio.create_task(self.run_background_governance_loop())
            self._worker.add_done_callback(self._worker_stopped)
        return self

    async def close(self):
        self._closed = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        # A cancelled caller cannot abandon cleanup or cancel other closers.
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self):
        worker = self._worker
        if worker is None:
            return
        try:
            await asyncio.wait_for(self.queue.join(), self.shutdown_timeout)
        except TimeoutError:
            self._hold("SHUTDOWN_TIMEOUT")
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            self._worker_stopped(worker)
            self._worker = None

    def _worker_stopped(self, task):
        if not self._closed and self._held_reason is None:
            self._hold("WORKER_STOPPED")
        if not task.cancelled():
            task.exception()  # Retrieve failure; terminal receipts carry HOLD.
        while not self.queue.empty():
            token, tx, encoded, deadline = self.queue.get_nowait()
            self._complete(token, "held", self._held_reason or "WORKER_STOPPED", len(encoded))
            self.queue.task_done()
        for token in tuple(self._pending):
            self._complete(token, "held", self._held_reason or "WORKER_STOPPED", 0)

    @staticmethod
    def _hash_frame(tx, encoded):
        payload_hash = hashlib.sha256(encoded).hexdigest()
        block_hash = sha256_text(canonical_json(block_material(tx, payload_hash)))
        return payload_hash, block_hash

    async def _compute(self, function, encoded, *prefix):
        if len(encoded) < self.hash_offload_bytes:
            self._inline_jobs += 1
            return function(*prefix, encoded)
        self._thread_jobs += 1
        try:
            return await asyncio.wait_for(asyncio.to_thread(function, *prefix, encoded), self.hash_timeout)
        except TimeoutError as exc:
            self._hold("HASH_TIMEOUT")
            raise HoldError("HASH_TIMEOUT") from exc

    async def _process(self, tx, encoded, deadline):
        self._check_session(tx.session_id)
        if asyncio.get_running_loop().time() >= deadline:
            raise HoldError("TRANSACTION_DEADLINE")
        seq, parent = self.head(tx.session_id)
        if tx.sequence_number != seq + 1:
            raise QuarantineError(f"Expected sequence {seq + 1}; received {tx.sequence_number}")
        if not hmac.compare_digest(tx.previous_hash, parent):
            raise QuarantineError("Predecessor does not match the verified session head")
        payload_hash, block_hash = await self._compute(self._hash_frame, encoded, tx)
        if tx.expected_hash is not None and not hmac.compare_digest(payload_hash, tx.expected_hash):
            raise QuarantineError("Payload SHA-256 assertion mismatch")
        self._check_session(tx.session_id)  # Quarantine may arrive during thread work.
        if asyncio.get_running_loop().time() >= deadline:
            raise HoldError("TRANSACTION_DEADLINE")
        record = LedgerRecord(tx.session_id, tx.sequence_number, tx.source_agent,
                              tx.target_entity, payload_hash, tx.previous_hash,
                              block_hash, time.time_ns())
        self._ledger[(tx.session_id, tx.sequence_number)] = record
        self._heads[tx.session_id] = (tx.sequence_number, block_hash)
        self._payload_hashes.add(payload_hash)
        self._event("COMMITTED", tx.session_id, tx.sequence_number, block_hash)
        return record

    async def run_background_governance_loop(self):
        processed = 0
        while True:
            token, tx, encoded, deadline = await self.queue.get()
            try:
                record = await self._process(tx, encoded, deadline)
                self._complete(token, "verified", record, len(encoded))
            except QuarantineError as exc:
                self.quarantine(tx.session_id, tx.sequence_number, str(exc))
                self._complete(token, "quarantined", str(exc), len(encoded))
            except HoldError as exc:
                self.hold_session(tx.session_id, str(exc))
                self._complete(token, "held", str(exc), len(encoded))
            except asyncio.CancelledError:
                self._complete(token, "held", "WORKER_CANCELLED", len(encoded))
                raise
            except Exception as exc:
                self._hold("WORKER_ERROR:" + type(exc).__name__)
                self._complete(token, "held", self._held_reason, len(encoded))
            finally:
                self.queue.task_done()
                # Do not retain the last payload while waiting on an empty queue.
                del tx, encoded
            processed += 1
            if processed % self.yield_every == 0:
                await asyncio.sleep(0)
