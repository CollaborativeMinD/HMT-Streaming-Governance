import asyncio
import base64
import json
import unittest

from governance_core import AgentGateway, GovernanceLedgerEngine, stream_to_ledger


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_pipeline_preserves_bytes_bounds_staging_and_closes_source(self):
        data = bytes(range(256)) * 80
        closed = []

        async def packets():
            try:
                for offset in range(0, len(data), 73):
                    yield data[offset:offset+73]
            finally:
                closed.append(True)

        engine = await GovernanceLedgerEngine().start()
        receiver = AgentGateway(engine, 'stream', 'tool-boundary')
        restored = bytearray()

        async def receive(envelope):
            await receiver.execute_verified(envelope, lambda text: restored.extend(
                base64.b64decode(json.loads(text)['data'])))

        try:
            counts = await stream_to_ledger(packets(), engine, 'stream', receive, staging_size=2)
            self.assertEqual(bytes(restored), data)
            self.assertEqual(counts['input_bytes'], len(data))
            self.assertLessEqual(counts['staging_peak'], 2)
            self.assertEqual(counts['chunks'], len(engine.ledger))
            self.assertEqual(closed, [True])
        finally:
            await engine.close()

    async def test_sink_failure_cancels_source_without_retry(self):
        entered, closed = [], []

        async def packets():
            try:
                while True:
                    yield bytes(range(256)) * 16
            finally:
                closed.append(True)

        def fail(envelope):
            entered.append(envelope['ledger_hash'])
            raise RuntimeError('Sink failed; do not replay effects')

        engine = await GovernanceLedgerEngine().start()
        try:
            with self.assertRaises(ExceptionGroup):
                await stream_to_ledger(packets(), engine, 'stream', fail, staging_size=1)
            self.assertEqual(len(entered), 1)
            self.assertEqual(closed, [True])
        finally:
            await engine.close()

    async def test_oversized_packet_and_deadline_close_the_source(self):
        for oversized in (True, False):
            closed = []

            async def packets():
                try:
                    if oversized:
                        yield b'x' * 100
                    else:
                        await asyncio.Event().wait()
                        yield b''
                finally:
                    closed.append(True)

            engine = await GovernanceLedgerEngine().start()
            try:
                expected = ExceptionGroup if oversized else TimeoutError
                with self.assertRaises(expected):
                    await stream_to_ledger(packets(), engine, 'stream', lambda _: None,
                                           max_packet_bytes=10, deadline_s=0.01)
                self.assertEqual(closed, [True])
            finally:
                await engine.close()
