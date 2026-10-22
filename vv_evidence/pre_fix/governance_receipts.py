"""Bounded queue admission and cancellation-safe per-token completion."""
import asyncio
import re
import uuid

from governance_contracts import HoldError, LedgerRecord, QuarantineError, Transaction


class ReceiptFlow:
    @staticmethod
    def _valid_hash(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    def _payload_bytes(self, payload):
        if not isinstance(payload, str):
            raise ValueError("payload must be a string")
        if len(payload) > self.max_payload_bytes:
            raise ValueError("Payload exceeds configured size limit")
        encoded = payload.encode("utf-8")
        if len(encoded) > self.max_payload_bytes:
            raise ValueError("Payload exceeds configured size limit")
        return encoded

    def _parse(self, frame):
        tx = Transaction(**frame)
        for field in (tx.session_id, tx.source_agent, tx.target_entity):
            if not isinstance(field, str) or not field.strip() or len(field) > 256:
                raise ValueError("Identifiers must be nonempty strings <= 256 characters")
        if type(tx.sequence_number) is not int or tx.sequence_number < 0:
            raise ValueError("sequence_number must be a nonnegative integer")
        if not isinstance(tx.payload, str):
            raise ValueError("payload must be a string")
        if not self._valid_hash(tx.previous_hash):
            raise ValueError("previous_hash must be 64 lowercase hex characters")
        if tx.expected_hash is not None and not self._valid_hash(tx.expected_hash):
            raise ValueError("expected_hash must be 64 lowercase hex characters")
        return tx

    async def governance_commit_state(self, **frame):
        if self._closed or self._worker is None or self._worker.done():
            raise HoldError("Start a live engine before submitting")
        try:
            tx = self._parse(frame)
            self._check_session(tx.session_id)
            if self.queue.full():
                return dict(status="busy", reason="QUEUE_CAPACITY")
            encoded = self._payload_bytes(tx.payload)
            if self._pending_bytes + len(encoded) > self.max_pending_bytes:
                return dict(status="busy", reason="PENDING_BYTE_CAPACITY")
            if len(self._ledger) + len(self._pending) >= self.max_records:
                return dict(status="held", reason="LEDGER_CAPACITY")
            self._register_session(tx.session_id)
        except QuarantineError as exc:
            return dict(status="quarantined", reason=str(exc))
        except HoldError as exc:
            return dict(status="held", reason=str(exc))
        except (ValueError, TypeError, UnicodeError) as exc:
            return dict(status="rejected", reason=str(exc))
        token = uuid.uuid4().hex
        self._pending[token] = asyncio.get_running_loop().create_future()
        self._pending_bytes += len(encoded)
        deadline = asyncio.get_running_loop().time() + self.transaction_timeout
        self.queue.put_nowait((token, tx, encoded, deadline))
        self._event("QUEUED", tx.session_id, tx.sequence_number, "Awaiting verification")
        return dict(status="pending", tracking_token=token)

    @staticmethod
    def _receipt(token, result):
        status, value = result
        receipt = dict(status=status, tracking_token=token)
        if isinstance(value, LedgerRecord):
            receipt.update({name: getattr(value, name) for name in LedgerRecord.__dataclass_fields__})
        else:
            receipt['reason'] = value
        return receipt

    def _complete(self, token, status, value, size):
        future = self._pending.pop(token, None)
        if future is None:
            return
        self._pending_bytes -= size
        result = (status, value)
        self._completed[token] = result
        while len(self._completed) > self.receipt_capacity:
            self._completed.popitem(last=False)
        if not future.done():
            future.set_result(result)

    def governance_get_status(self, tracking_token):
        if tracking_token in self._pending:
            return dict(status="pending", tracking_token=tracking_token)
        result = self._completed.get(tracking_token)
        return self._receipt(tracking_token, result) if result else dict(
            status="unknown", reason="Unknown or evicted receipt; no trust granted")

    async def wait(self, tracking_token, timeout=None):
        future = self._pending.get(tracking_token)
        if future is None:
            return self.governance_get_status(tracking_token)
        try:
            result = await asyncio.wait_for(asyncio.shield(future),
                                            self.wait_timeout if timeout is None else timeout)
        except TimeoutError:
            return dict(status="pending", tracking_token=tracking_token, reason="RECEIPT_TIMEOUT")
        return self._receipt(tracking_token, result)
