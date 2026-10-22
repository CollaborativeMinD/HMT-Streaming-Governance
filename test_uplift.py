import asyncio
import copy
import unittest
from unittest.mock import patch

from governance_core import (
    GENESIS_HASH,
    AgentGateway,
    GovernanceLedgerEngine,
    HoldError,
    RabinCDCScanner,
)


def frame(session="s", payload="safe", seq=0, parent=GENESIS_HASH):
    return dict(session_id=session, sequence_number=seq, source_agent="sender",
                target_entity="receiver", payload=payload, previous_hash=parent)


class UpliftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = await GovernanceLedgerEngine().start()

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_wait_is_per_receipt_and_cancellation_does_not_cancel_commit(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.engine._process

        async def blocked(tx, encoded, deadline):
            if tx.session_id == "second":
                started.set()
                await release.wait()
            return await original(tx, encoded, deadline)

        self.engine._process = blocked
        first = await self.engine.governance_commit_state(**frame("first"))
        second = await self.engine.governance_commit_state(**frame("second"))
        await started.wait()
        try:
            result = await self.engine.wait(first['tracking_token'], timeout=0.1)
            self.assertEqual(result['status'], 'verified')
            waiter = asyncio.create_task(self.engine.wait(second['tracking_token']))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
        finally:
            release.set()
        self.assertEqual((await self.engine.wait(second['tracking_token']))['status'], 'verified')

    async def test_mutating_handoff_during_verification_cannot_swap_tool_payload(self):
        sender = AgentGateway(self.engine, 's', 'sender')
        receiver = AgentGateway(self.engine, 's', 'receiver')
        envelope = await sender.wrap_output('safe', 'receiver')
        started, release = asyncio.Event(), asyncio.Event()
        original = self.engine.governance_verify_payload

        async def blocked(payload, expected_hash):
            started.set()
            await release.wait()
            return await original(payload, expected_hash)

        self.engine.governance_verify_payload = blocked
        calls = []
        task = asyncio.create_task(receiver.execute_verified(envelope, calls.append))
        await started.wait()
        envelope['transaction']['payload'] = 'unverified swapped bytes'
        release.set()
        await task
        self.assertEqual(calls, ['safe'])

    async def test_shared_session_lock_serializes_concurrent_senders(self):
        senders = [AgentGateway(self.engine, 'shared', f'sender-{i}') for i in range(8)]
        envelopes = await asyncio.gather(*(s.wrap_output(str(i)) for i, s in enumerate(senders)))
        self.assertEqual(sorted(e['transaction']['sequence_number'] for e in envelopes), list(range(8)))
        self.assertEqual(len(self.engine.ledger), 8)
        self.assertEqual(self.engine.health()['quarantined_sessions'], 0)

    async def test_bounded_caches_do_not_evict_ledger(self):
        self.engine.receipt_capacity = 2
        sender = AgentGateway(self.engine, 's', 'sender')
        receiver = AgentGateway(self.engine, 's', 'receiver', max_context_entries=2)
        first = None
        for i in range(8):
            envelope = await sender.wrap_output(str(i), 'receiver')
            if first is None:
                first = copy.deepcopy(envelope)
            await receiver.execute_verified(envelope, lambda _: None)
        self.assertEqual(len(self.engine._completed), 2)
        self.assertEqual(len(receiver.context), 2)
        self.assertEqual(len(self.engine.ledger), 8)
        self.assertTrue((await self.engine.governance_verify_payload('0', first['payload_hash']))['verified'])

    async def test_byte_and_record_capacity_refuse_without_advancing_head(self):
        self.engine.max_pending_bytes = 3
        result = await self.engine.governance_commit_state(**frame(payload='four'))
        self.assertEqual(result, dict(status='busy', reason='PENDING_BYTE_CAPACITY'))
        self.assertEqual(self.engine.head('s'), (-1, GENESIS_HASH))
        self.engine.max_pending_bytes = 100
        self.engine.max_records = 1
        envelope = await AgentGateway(self.engine, 's').wrap_output('one')
        held = await self.engine.governance_commit_state(**frame(seq=1, parent=envelope['ledger_hash']))
        self.assertEqual(held, dict(status='held', reason='LEDGER_CAPACITY'))
        self.assertEqual(len(self.engine.ledger), 1)

    async def test_hash_timeout_is_hold_and_never_invokes_tool(self):
        self.engine.hash_offload_bytes = 0
        self.engine.hash_timeout = 0.01

        async def stalled(*args):
            await asyncio.Event().wait()

        with patch('governance_core.asyncio.to_thread', stalled):
            receipt = await self.engine.governance_commit_state(**frame())
            result = await self.engine.wait(receipt['tracking_token'])
        self.assertEqual(result['status'], 'held')
        self.assertEqual(self.engine.health()['hold_reason'], 'HASH_TIMEOUT')
        self.assertEqual(len(self.engine.ledger), 0)
        self.assertEqual(self.engine.health()['quarantined_sessions'], 0)

    async def test_worker_cancellation_settles_pending_and_drains_queue(self):
        receipt = await self.engine.governance_commit_state(**frame())
        self.engine._worker.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        result = await self.engine.wait(receipt['tracking_token'])
        self.assertEqual(result['status'], 'held')
        self.assertEqual(self.engine.health()['pending_bytes'], 0)
        await asyncio.wait_for(self.engine.queue.join(), 0.1)

    async def test_expired_queue_item_never_commits(self):
        self.engine.transaction_timeout = 0.001
        receipt = await self.engine.governance_commit_state(**frame())
        # Hold the event loop until after the absolute admission deadline.
        import time
        time.sleep(0.003)
        result = await self.engine.wait(receipt['tracking_token'])
        self.assertEqual(result['status'], 'held')
        self.assertEqual(result['reason'], 'TRANSACTION_DEADLINE')
        self.assertEqual(len(self.engine.ledger), 0)

    async def test_receipt_timeout_preserves_pending_result(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.engine._process

        async def blocked(tx, encoded, deadline):
            started.set()
            await release.wait()
            return await original(tx, encoded, deadline)

        self.engine._process = blocked
        receipt = await self.engine.governance_commit_state(**frame())
        await started.wait()
        try:
            result = await self.engine.wait(receipt['tracking_token'], timeout=0.001)
            self.assertEqual(result['reason'], 'RECEIPT_TIMEOUT')
        finally:
            release.set()
        self.assertEqual((await self.engine.wait(receipt['tracking_token']))['status'], 'verified')

    async def test_verification_overload_is_hold_without_quarantine(self):
        envelope = await AgentGateway(self.engine, 's', 'sender').wrap_output('safe', 'receiver')
        self.engine._verification_jobs = self.engine.verification_capacity
        with self.assertRaises(HoldError):
            await AgentGateway(self.engine, 's', 'receiver').execute_verified(envelope, lambda _: self.fail('Tool ran'))
        self.assertEqual(self.engine.health()['quarantined_sessions'], 0)

    async def test_small_hashes_avoid_thread_jobs_and_large_hashes_offload(self):
        await AgentGateway(self.engine, 'small').wrap_output('small')
        self.assertEqual(self.engine.health()['thread_hash_jobs'], 0)
        await AgentGateway(self.engine, 'large').wrap_output('x' * 100000)
        self.assertEqual(self.engine.health()['thread_hash_jobs'], 1)


class ScannerUpliftTests(unittest.TestCase):
    def test_non_power_of_two_modulus_and_abandoned_generator(self):
        data = bytes(range(256)) * 12
        scanner = RabinCDCScanner(block_size_target=257)
        generator = scanner.feed(data)
        first = next(generator)
        generator.close()
        remaining = list(scanner.feed(data[first.end:])) + scanner.finish()
        self.assertEqual(b''.join(c.data for c in [first, *remaining]), data)
        for chunk in [first, *remaining]:
            if chunk.reason == 'content':
                self.assertEqual(chunk.fingerprint % 257, 1)
