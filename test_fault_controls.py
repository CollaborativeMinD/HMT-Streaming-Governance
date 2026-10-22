import asyncio
import unittest

from governance_core import (
    GENESIS_HASH,
    AgentGateway,
    GovernanceLedgerEngine,
    HoldError,
    QuarantineError,
)
from test_uplift import frame


class FaultControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_outbound_cancellation_holds_the_session(self):
        engine = await GovernanceLedgerEngine().start()
        started, release = asyncio.Event(), asyncio.Event()
        original = engine._process

        async def stalled(tx, encoded, deadline):
            started.set()
            await release.wait()
            return await original(tx, encoded, deadline)

        engine._process = stalled
        task = asyncio.create_task(AgentGateway(engine, 's').wrap_output('safe'))
        try:
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(engine.health()['held_sessions'], 1)
            release.set()
            await engine.queue.join()
            self.assertEqual(len(engine.ledger), 0)
        finally:
            release.set()
            await engine.close()

    async def test_deadline_is_rechecked_after_verification(self):
        engine = await GovernanceLedgerEngine(transaction_timeout=0.005).start()
        original = engine._compute

        async def delayed(*args):
            await asyncio.sleep(0.02)
            return await original(*args)

        engine._compute = delayed
        try:
            receipt = await engine.governance_commit_state(**frame())
            result = await engine.wait(receipt['tracking_token'])
            self.assertEqual(result['reason'], 'TRANSACTION_DEADLINE')
            self.assertEqual(len(engine.ledger), 0)
        finally:
            await engine.close()

    async def test_inline_worker_yields_during_a_full_queue(self):
        engine = await GovernanceLedgerEngine(yield_every=2).start()
        try:
            receipts = [await engine.governance_commit_state(**frame(str(i))) for i in range(8)]
            await asyncio.sleep(0)
            self.assertLess(len(engine.ledger), 8)
            for receipt in receipts:
                self.assertEqual((await engine.wait(receipt['tracking_token']))['status'], 'verified')
        finally:
            await engine.close()

    async def test_configuration_rejects_bool_negative_and_nonfinite_limits(self):
        for kwargs in ({'queue_size': True}, {'max_records': 0}, {'hash_offload_bytes': -1},
                       {'hash_timeout': float('nan')}, {'shutdown_timeout': float('inf')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                GovernanceLedgerEngine(**kwargs)
        with self.assertRaises(ValueError):
            AgentGateway(GovernanceLedgerEngine(), 's', max_context_entries=0)

    async def test_session_capacity_and_unknown_receipts_do_not_grant_trust(self):
        engine = await GovernanceLedgerEngine(max_sessions=1, receipt_capacity=1).start()
        try:
            first = await engine.governance_commit_state(**frame())
            record = await engine.wait(first['tracking_token'])
            denied = await engine.governance_commit_state(**frame('other'))
            self.assertEqual(denied, dict(status='held', reason='SESSION_CAPACITY'))
            engine.quarantine('unadmitted-session', 0, 'Invalid external envelope')
            self.assertEqual(len(engine._heads), 1)
            second = await engine.governance_commit_state(**frame(seq=1, parent=record['block_hash']))
            await engine.wait(second['tracking_token'])
            self.assertEqual(engine.governance_get_status(first['tracking_token'])['status'], 'unknown')
            self.assertEqual((await engine.wait('unknown'))['status'], 'unknown')
        finally:
            await engine.close()

    async def test_byte_budget_checks_unicode_and_malformed_expected_hash(self):
        engine = await GovernanceLedgerEngine(max_payload_bytes=4).start()
        try:
            for payload in ('12345', 'ééé', '\ud800'):
                self.assertEqual((await engine.governance_commit_state(**frame(payload=payload)))['status'], 'rejected')
            result = await engine.governance_commit_state(**frame(), expected_hash='bad')
            self.assertEqual(result['status'], 'rejected')
            self.assertFalse((await engine.governance_verify_payload('safe', 'bad'))['verified'])
            self.assertFalse((await engine.governance_verify_payload('12345', GENESIS_HASH))['verified'])
        finally:
            await engine.close()

    async def test_worker_error_holds_all_queued_work_without_quarantine(self):
        engine = await GovernanceLedgerEngine().start()

        async def broken(*args):
            raise RuntimeError('Injected worker fault')

        engine._process = broken
        try:
            one = await engine.governance_commit_state(**frame('one'))
            two = await engine.governance_commit_state(**frame('two'))
            self.assertEqual((await engine.wait(one['tracking_token']))['status'], 'held')
            self.assertEqual((await engine.wait(two['tracking_token']))['status'], 'held')
            self.assertEqual(engine.health()['quarantined_sessions'], 0)
            self.assertEqual(engine.health()['pending_bytes'], 0)
            with self.assertRaises(HoldError):
                await engine.start()
            self.assertEqual((await engine.governance_verify_payload('safe', GENESIS_HASH))['disposition'], 'held')
        finally:
            await engine.close()

    async def test_shutdown_timeout_cancels_active_worker_and_settles_receipt(self):
        engine = await GovernanceLedgerEngine(shutdown_timeout=0.001).start()
        started = asyncio.Event()

        async def stalled(*args):
            started.set()
            await asyncio.Event().wait()

        engine._process = stalled
        receipt = await engine.governance_commit_state(**frame())
        await started.wait()
        await engine.close()
        self.assertEqual(engine.health()['hold_reason'], 'SHUTDOWN_TIMEOUT')
        self.assertEqual((await engine.wait(receipt['tracking_token']))['status'], 'held')
        self.assertEqual(engine.health()['pending_bytes'], 0)
        await engine.close()
        with self.assertRaises(HoldError):
            await engine.governance_commit_state(**frame())

    async def test_gateway_receipt_timeout_holds_session_and_releases_lock(self):
        engine = await GovernanceLedgerEngine(wait_timeout=0.001).start()
        release = asyncio.Event()
        original = engine._process

        async def stalled(tx, encoded, deadline):
            await release.wait()
            return await original(tx, encoded, deadline)

        engine._process = stalled
        sender = AgentGateway(engine, 's')
        try:
            with self.assertRaises(HoldError):
                await sender.wrap_output('safe')
            self.assertEqual(engine.health()['held_sessions'], 1)
            release.set()
            await engine.queue.join()
            self.assertEqual(len(engine.ledger), 0)
            with self.assertRaises(HoldError):
                await sender.wrap_output('again')
        finally:
            release.set()
            await engine.close()

    async def test_gateway_rejects_quarantined_outbound_and_verifier_byte_exhaustion(self):
        engine = await GovernanceLedgerEngine(max_verification_bytes=2).start()
        try:
            envelope = await AgentGateway(engine, 's', 'sender').wrap_output('safe', 'receiver')
            with self.assertRaises(HoldError):
                await AgentGateway(engine, 's', 'receiver').execute_verified(envelope, lambda _: self.fail('Tool ran'))
            self.assertEqual(engine.health()['quarantined_sessions'], 0)
            engine.quarantine('s', 0, 'Known integrity incident')
            with self.assertRaises(QuarantineError):
                await AgentGateway(engine, 's').wrap_output('blocked')
        finally:
            await engine.close()
