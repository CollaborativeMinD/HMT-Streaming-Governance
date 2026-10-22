"""Registered payload checks and envelope lineage verification."""
import hashlib
import hmac

from governance_contracts import HoldError, QuarantineError, block_material


class VerificationBoundary:
    def _verifier_available(self):
        if self._held_reason:
            raise HoldError(self._held_reason)
        if self._closed or self._worker is None or self._worker.done():
            raise HoldError("VERIFIER_UNAVAILABLE")

    async def governance_verify_payload(self, payload, expected_hash):
        if not self._valid_hash(expected_hash):
            return dict(verified=False, registered=False, reason="Malformed digest")
        try:
            self._verifier_available()
            if self._verification_jobs >= self.verification_capacity:
                raise HoldError("VERIFICATION_CAPACITY")
            encoded = self._payload_bytes(payload)
            if self._verification_bytes + len(encoded) > self.max_verification_bytes:
                raise HoldError("VERIFICATION_BYTE_CAPACITY")
            self._verification_jobs += 1
            self._verification_bytes += len(encoded)
            try:
                actual = await self._compute(lambda data: hashlib.sha256(data).hexdigest(), encoded)
                self._verifier_available()  # Availability may change across await.
            finally:
                self._verification_jobs -= 1
                self._verification_bytes -= len(encoded)
        except HoldError as exc:
            return dict(verified=False, registered=False, disposition="held", reason=str(exc))
        except (ValueError, UnicodeError) as exc:
            return dict(verified=False, registered=False, reason=str(exc))
        except Exception as exc:
            self._hold("VERIFIER_ERROR:" + type(exc).__name__)
            return dict(verified=False, registered=False, disposition="held", reason=self._held_reason)
        registered = expected_hash in self._payload_hashes
        matches = hmac.compare_digest(actual, expected_hash)
        return dict(verified=registered and matches, registered=registered,
                    actual_hash=actual, expected_hash=expected_hash,
                    reason="Verified against committed content" if registered and matches
                    else "Digest mismatch" if not matches else "Digest is not in this ledger")

    async def verify_envelope(self, envelope):
        tx = self._parse(envelope["transaction"])
        self._check_session(tx.session_id)
        record = self._ledger.get((tx.session_id, tx.sequence_number))
        supplied_block = envelope.get("ledger_hash", "")
        if (record is None or not self._valid_hash(supplied_block)
                or not hmac.compare_digest(record.block_hash, supplied_block)
                or block_material(tx, record.payload_hash) != block_material(record, record.payload_hash)):
            self.quarantine(tx.session_id, tx.sequence_number, "Envelope lineage mismatch")
            raise QuarantineError("Incoming envelope failed lineage verification")
        result = await self.governance_verify_payload(tx.payload, envelope.get("payload_hash", ""))
        self._check_session(tx.session_id)
        if result.get('disposition') == 'held':
            raise HoldError(result['reason'])
        if not result['verified'] or record.payload_hash != result.get('actual_hash'):
            self.quarantine(tx.session_id, tx.sequence_number, "Payload mismatch")
            raise QuarantineError("Incoming envelope failed payload verification")
        return record
