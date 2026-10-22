"""Independent oracles, generated traces and boundary/lifecycle tests."""
import asyncio
import hashlib
import json
import random
import unittest

from governance_core import (
    AgentGateway,
    GovernanceLedgerEngine,
    RabinCDCScanner,
    canonical_json,
)


def reference_cuts(data, width, minimum, maximum, modulus, target):
    result, start = [], 0
    for end in range(1, len(data) + 1):
        value = 0
        for byte in data[max(0, end - width):end]:
            value = (value * 256 + byte) % (2**61 - 1)
        if end - start >= minimum and (value % modulus == target or end - start >= maximum):
            result.append((start, end, value, 'content' if value % modulus == target else 'max size'))
            start = end
    if start < len(data):
        result.append((start, len(data), value, 'tail'))
    return result


class OracleUnitTests(unittest.TestCase):
    def test_generated_polynomial_and_boundary_oracle(self):
        for seed in range(32):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                width, modulus = rng.randint(1, 12), rng.choice((2, 7, 16, 31, 64, 257))
                minimum, target = width + rng.randint(0, 4), rng.randrange(modulus)
                maximum = minimum + rng.randint(0, 30)
                data = rng.randbytes(rng.randint(0, 800))
                expected = reference_cuts(data, width, minimum, maximum, modulus, target)
                scanner = RabinCDCScanner(modulus, width, minimum, maximum, target)
                actual = []
                for offset in range(0, len(data), 13):
                    actual.extend(scanner.feed(data[offset:offset + 13]))
                actual.extend(scanner.finish())
                self.assertEqual([(c.start, c.end, c.fingerprint, c.reason) for c in actual], expected)
                self.assertEqual(b''.join(c.data for c in actual), data)

    def test_canonical_json_known_answer_and_key_order(self):
        expected = '{"a":"é","z":[true,null,2]}'
        self.assertEqual(canonical_json(dict(z=[True, None, 2], a='é')), expected)
        self.assertEqual(canonical_json(dict(a='é', z=[True, None, 2])), expected)

    def test_scanner_invalid_configuration_types_and_edges(self):
        for options in (dict(window_size=True), dict(block_size_target=2.0), dict(target=-1),
                        dict(target=True), dict(min_size=64, max_size=63), dict(min_size=False)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                RabinCDCScanner(**options)
        scanner = RabinCDCScanner(2, 1, 1, 1, 0)
        self.assertEqual([c.data for c in scanner.feed(b'abc')], [b'a', b'b', b'c'])
        self.assertEqual(scanner.finish(), [])
        with self.assertRaises(RuntimeError):
            list(scanner.feed(b''))


class StateModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_traces_match_abstract_chain_model(self):
        engine = await GovernanceLedgerEngine().start()
        self.addAsyncCleanup(engine.close)
        model = {f's{i}': dict(sequence=-1, parent='0'*64, quarantined=False) for i in range(8)}
        rng = random.Random(517)
        for step in range(150):
            session = rng.choice(list(model))
            state = model[session]
            sequence = state['sequence'] + 1
            parent = state['parent']
            if rng.random() < .12:
                sequence += rng.choice((-1, 1))
            if rng.random() < .12:
                parent = 'f' * 64
            frame = dict(session_id=session, sequence_number=max(0, sequence), previous_hash=parent,
                         source_agent='model', target_entity='receiver', payload=f'bytes {step} · é')
            valid = (not state['quarantined'] and frame['sequence_number'] == state['sequence'] + 1
                     and parent == state['parent'])
            result = await engine.governance_commit_state(**frame)
            if result['status'] == 'pending':
                result = await engine.wait(result['tracking_token'])
            self.assertEqual(result['status'], 'verified' if valid else 'quarantined')
            if valid:
                payload_hash = hashlib.sha256(frame['payload'].encode()).hexdigest()
                material = {k: v for k, v in frame.items() if k != 'payload'}
                material.update(version=1, payload_hash=payload_hash)
                block = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                                   ensure_ascii=False).encode()).hexdigest()
                self.assertEqual(result['block_hash'], block)
                state.update(sequence=sequence, parent=block)
            else:
                state['quarantined'] = True
            self.assertEqual(engine.head(session), (state['sequence'], state['parent']))
        self.assertEqual(len(engine.ledger), sum(s['sequence'] + 1 for s in model.values()))

    async def test_utf8_exact_limit_and_limit_plus_one(self):
        engine = await GovernanceLedgerEngine(max_payload_bytes=4).start()
        self.addAsyncCleanup(engine.close)
        good = await AgentGateway(engine, 'good').wrap_output('éé')
        self.assertTrue((await engine.governance_verify_payload('éé', good['payload_hash']))['verified'])
        for payload in ('ééa', '\ud800'):
            result = await engine.governance_commit_state(session_id='bad', sequence_number=0,
                source_agent='sender', target_entity='receiver', previous_hash='0'*64, payload=payload)
            self.assertEqual(result['status'], 'rejected')
        self.assertEqual(len(engine.ledger), 1)

    async def test_cancelled_close_caller_does_not_abandon_shared_cleanup(self):
        engine = await GovernanceLedgerEngine().start()
        self.addAsyncCleanup(engine.close)
        entered, release = asyncio.Event(), asyncio.Event()
        original = engine._process

        async def blocked(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        engine._process = blocked
        receipt = await engine.governance_commit_state(session_id='a', sequence_number=0, source_agent='s',
            target_entity='r', previous_hash='0'*64, payload='safe')
        await entered.wait()
        closer = asyncio.create_task(engine.close())
        await asyncio.sleep(0)
        closer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closer
        release.set()
        self.assertEqual(await asyncio.gather(engine.close(), return_exceptions=True), [None])
        self.assertEqual(engine.health()['pending'], 0)
        self.assertEqual(engine.health()['pending_bytes'], 0)
        self.assertEqual(engine.governance_get_status(receipt['tracking_token'])['status'], 'verified')
        self.assertTrue(engine._shutdown_task.done())
