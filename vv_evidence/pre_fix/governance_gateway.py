"""Per-session outbound serialization and immutable input snapshots."""
import asyncio

from governance_contracts import GovernanceError, HoldError, QuarantineError


class AgentGateway:
    """Snapshot mutable inputs, serialize outbound chains, gate every invocation."""

    def __init__(self, engine, session_id, agent_id="primary-agent", max_context_entries=64):
        if type(max_context_entries) is not int or max_context_entries < 1:
            raise ValueError("max_context_entries must be positive")
        self.engine, self.session_id, self.agent_id = engine, session_id, agent_id
        self.max_context_entries = max_context_entries
        self.context = []

    async def wrap_output(self, payload, target_entity="tool-boundary"):
        async with self.engine.session_lock(self.session_id):
            seq, parent = self.engine.head(self.session_id)
            frame = dict(session_id=self.session_id, sequence_number=seq + 1,
                         source_agent=self.agent_id, target_entity=target_entity,
                         payload=payload, previous_hash=parent)
            receipt = await self.engine.governance_commit_state(**frame)
            if receipt["status"] != "pending":
                self.context.clear()
                error = QuarantineError if receipt['status'] == 'quarantined' else HoldError
                raise error(receipt.get("reason", receipt["status"]))
            try:
                committed = await self.engine.wait(receipt["tracking_token"])
            except asyncio.CancelledError:
                self.context.clear()
                self.engine.hold_session(self.session_id, "OUTBOUND_CANCELLED")
                raise
            if committed["status"] != "verified":
                self.context.clear()
                if committed['status'] == 'pending':
                    self.engine.hold_session(self.session_id, "RECEIPT_TIMEOUT")
                error = QuarantineError if committed['status'] == 'quarantined' else HoldError
                raise error(committed.get("reason", "Commit unavailable"))
            return dict(transaction=frame, payload_hash=committed["payload_hash"],
                        ledger_hash=committed["block_hash"])

    async def execute_verified(self, envelope, tool):
        try:
            # Only immutable strings and validated scalar metadata survive this
            # snapshot. Later caller mutation cannot swap the verified payload.
            snapshot = dict(transaction=dict(envelope['transaction']),
                            payload_hash=envelope['payload_hash'], ledger_hash=envelope['ledger_hash'])
            transaction = self.engine._parse(snapshot['transaction'])
            if transaction.session_id != self.session_id or transaction.target_entity != self.agent_id:
                raise QuarantineError("Unexpected session or recipient")
            await self.engine.verify_envelope(snapshot)
        except HoldError:
            self.context.clear()
            raise
        except (GovernanceError, KeyError, TypeError, ValueError) as exc:
            self.context.clear()
            self.engine.quarantine(self.session_id, -1, f"Hash/chain hook halted ingestion: {exc}")
            raise QuarantineError("Context cleared; tool execution halted") from exc
        self.context.append(snapshot['ledger_hash'])
        del self.context[:-self.max_context_entries]
        return tool(transaction.payload)
