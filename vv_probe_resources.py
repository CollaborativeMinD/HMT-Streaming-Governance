"""Resource, lifecycle, byte-stream and independent polynomial observations."""
import asyncio
import base64
import json
import random

from governance_core import (
    AgentGateway,
    GovernanceLedgerEngine,
    RabinCDCScanner,
    stream_to_ledger,
)


async def resource_probe(case):
    options = dict(queue_size=1) if case == 'queue' else {}
    if case == 'pending_bytes':
        options.update(max_pending_bytes=4)
    if case == 'ledger':
        options.update(max_records=1)
    if case == 'retention':
        options.update(receipt_capacity=1, event_capacity=2)
    engine = await GovernanceLedgerEngine(**options).start()
    frame = dict(session_id='a', sequence_number=0, source_agent='sender', target_entity='receiver',
                 payload='safe', previous_hash='0' * 64)
    try:
        if case == 'concurrent_close':
            results = await asyncio.gather(*(engine.close() for _ in range(8)), return_exceptions=True)
            return dict(all_closed=all(x is None for x in results), pending=engine.health()['pending'],
                        pending_bytes=engine.health()['pending_bytes'], worker_alive=engine.health()['worker_alive'])
        if case == 'deadline':
            original = engine._process

            async def expired(tx, encoded, deadline):
                return await original(tx, encoded, asyncio.get_running_loop().time() - 1)

            engine._process = expired
        if case == 'schema':
            results = []
            for changes in (dict(sequence_number=True), dict(sequence_number=-1), dict(payload=42),
                            dict(previous_hash='F' * 64), dict(payload='\ud800'), dict(session_id='')):
                result = await engine.governance_commit_state(**(frame | changes))
                results.append(result['status'])
            return dict(outcomes=results, records=len(engine.ledger), pending_bytes=engine.health()['pending_bytes'])
        first = await engine.governance_commit_state(**frame)
        if case in ('queue', 'pending_bytes'):
            result = await engine.governance_commit_state(**(frame | dict(session_id='b')))
            accepted = await engine.wait(first['tracking_token'])
            return dict(outcome=result['status'], reason=result['reason'], accepted=accepted['status'],
                        records=len(engine.ledger), pending_bytes=engine.health()['pending_bytes'])
        accepted = await engine.wait(first['tracking_token'])
        if case == 'deadline':
            return dict(outcome=accepted['status'], reason=accepted['reason'], records=len(engine.ledger),
                        quarantined=engine.health()['quarantined_sessions'], pending_bytes=engine.health()['pending_bytes'])
        result = await engine.governance_commit_state(**(frame | dict(session_id='b')))
        if case == 'ledger':
            return dict(outcome=result['status'], reason=result['reason'], records=len(engine.ledger),
                        head_unchanged=engine.head('a')[1] == accepted['block_hash'])
        await engine.wait(result['tracking_token'])
        return dict(outcome=engine.governance_get_status(first['tracking_token'])['status'],
                    records=len(engine.ledger), receipts=engine.health()['receipt_cache'],
                    events=engine.health()['event_cache'], registered=(await engine.governance_verify_payload(
                        'safe', accepted['payload_hash']))['verified'])
    finally:
        await engine.close()


async def stream_probe(case):
    data = bytes(range(256)) * 12 + '🧬漢é'.encode()
    closed, effects, restored = [], [], bytearray()

    async def packets():
        try:
            for offset in range(0, len(data), 37):
                yield data[offset:offset + 37]
        finally:
            closed.append(True)

    engine = await GovernanceLedgerEngine().start()
    receiver = AgentGateway(engine, 'stream', 'receiver')

    async def sink(envelope):
        if case == 'sink_failure':
            effects.append('attempt')
            raise RuntimeError('effect outcome unknown; never retry')
        await receiver.execute_verified(envelope, lambda text: restored.extend(
            base64.b64decode(json.loads(text)['data'])))
        await asyncio.sleep(0)

    try:
        try:
            counts = await stream_to_ledger(packets(), engine, 'stream', sink,
                                           staging_size=2, target_entity='receiver')
        except ExceptionGroup:
            if case != 'sink_failure':
                raise
            return dict(attempts=len(effects), source_closed=closed == [True])
        return dict(exact_bytes=bytes(restored) == data, input_bytes=counts['input_bytes'],
                    expected_bytes=len(data), staging_bounded=counts['staging_peak'] <= 2,
                    chunks=counts['chunks'], records=len(engine.ledger), source_closed=closed == [True])
    finally:
        await engine.close()


def scanner_probe():
    data = random.Random(481).randbytes(4096) + bytes(200)
    options = dict(block_size_target=31, window_size=7, min_size=9, max_size=59, target=3)
    reference, start = [], 0
    # Deliberately O(n*window): independently recompute every full window.
    for end in range(1, len(data) + 1):
        polynomial = 0
        for byte in data[max(0, end - 7):end]:
            polynomial = (polynomial * 256 + byte) % (2**61 - 1)
        if end - start >= 9 and (polynomial % 31 == 3 or end - start >= 59):
            reference.append((start, end, polynomial, 'content' if polynomial % 31 == 3 else 'max size'))
            start = end
    if start < len(data):
        reference.append((start, len(data), polynomial, 'tail'))
    signatures, exact = [], True
    for packet in (1, 7, 37, len(data)):
        scanner = RabinCDCScanner(**options)
        chunks = []
        for offset in range(0, len(data), packet):
            chunks.extend(scanner.feed(data[offset:offset + packet]))
        chunks.extend(scanner.finish())
        signatures.append([(c.start, c.end, c.fingerprint, c.reason) for c in chunks])
        exact &= b''.join(c.data for c in chunks) == data
    return dict(oracle=all(s == reference for s in signatures), exact_bytes=exact,
                packet_independent=all(s == signatures[0] for s in signatures), bounded=all(
                    end - start <= 59 for start, end, _, _ in reference))
