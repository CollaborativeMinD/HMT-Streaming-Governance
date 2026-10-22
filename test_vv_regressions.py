"""Regression witnesses: run unchanged before and after remediation."""
import asyncio
import unittest

from governance_core import AgentGateway, GovernanceLedgerEngine, HoldError


class BoundaryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def setup_boundary(self, **kwargs):
        engine = await GovernanceLedgerEngine(**kwargs).start()
        self.addAsyncCleanup(engine.close)
        sender = AgentGateway(engine, 'regression', 'sender')
        receiver = AgentGateway(engine, 'regression', 'receiver')
        envelope = await sender.wrap_output('verified bytes', 'receiver')
        return engine, receiver, envelope

    async def test_closed_engine_denies_previously_verified_handoff(self):
        engine, receiver, envelope = await self.setup_boundary()
        receiver.context.append('old context')
        await engine.close()
        effects = []
        with self.assertRaises(HoldError):
            await receiver.execute_verified(envelope, effects.append)
        self.assertEqual(effects, [])
        self.assertEqual(receiver.context, [])
        self.assertEqual(engine.health()['quarantined_sessions'], 0)

    async def test_hold_during_standalone_hash_cannot_return_verified(self):
        engine, _, envelope = await self.setup_boundary()
        original = engine._compute

        async def interrupted(function, encoded, *prefix):
            result = await original(function, encoded, *prefix)
            engine._hold('INJECTED_UNAVAILABLE')
            return result

        engine._compute = interrupted
        result = await engine.governance_verify_payload('verified bytes', envelope['payload_hash'])
        self.assertFalse(result['verified'])
        self.assertEqual(result.get('disposition'), 'held')

    async def test_concurrent_close_has_one_shutdown_owner(self):
        engine, _, _ = await self.setup_boundary()
        results = await asyncio.gather(*(engine.close() for _ in range(8)), return_exceptions=True)
        self.assertEqual(results, [None] * 8)
        self.assertEqual(engine.health()['pending_bytes'], 0)
        self.assertFalse(engine.health()['worker_alive'])

    async def test_unexpected_verifier_failure_holds_and_clears_context(self):
        engine, receiver, envelope = await self.setup_boundary()
        receiver.context.append('old context')

        async def broken(*args):
            raise RuntimeError('injected verifier fault')

        engine._compute = broken
        effects = []
        with self.assertRaises(HoldError):
            await receiver.execute_verified(envelope, effects.append)
        self.assertEqual(effects, [])
        self.assertEqual(receiver.context, [])
        self.assertEqual(engine.health()['state'], 'held')
        self.assertEqual(engine.health()['active_verifications'], 0)

    async def test_cancelled_verification_clears_context_and_holds_session(self):
        engine, receiver, envelope = await self.setup_boundary()
        receiver.context.append('old context')
        entered = asyncio.Event()

        async def blocked(*args):
            entered.set()
            await asyncio.Event().wait()

        engine._compute = blocked
        effects = []
        task = asyncio.create_task(receiver.execute_verified(envelope, effects.append))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(effects, [])
        self.assertEqual(receiver.context, [])
        self.assertEqual(engine.health()['held_sessions'], 1)
        self.assertEqual(engine.health()['active_verifications'], 0)


if __name__ == '__main__':
    unittest.main()
