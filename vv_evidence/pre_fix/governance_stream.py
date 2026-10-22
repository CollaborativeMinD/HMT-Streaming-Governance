"""A bounded scan/sequence pipeline; presentation need not retain the whole stream."""
import asyncio
from base64 import b64encode
from inspect import isawaitable

from governance_cdc import RabinCDCScanner
from governance_contracts import canonical_json
from governance_gateway import AgentGateway


def chunk_payload(chunk):
    return canonical_json(dict(encoding="base64", chunk_index=chunk.index,
                               start_byte=chunk.start, end_byte=chunk.end,
                               data=b64encode(chunk.data).decode("ascii")))


class StreamPump:
    """Own the staging queue, source lifetime, and one sequencer."""

    def __init__(self, packets, engine, session_id, on_verified, cdc_parameters,
                 staging_size, max_packet_bytes, target_entity):
        self.packets, self.on_verified = packets, on_verified
        self.scanner = RabinCDCScanner(**(cdc_parameters or {}))
        self.staging = asyncio.Queue(maxsize=staging_size)
        self.counters = dict(chunks=0, input_bytes=0, staging_peak=0)
        self.sender = AgentGateway(engine, session_id, "stream-agent")
        self.max_packet_bytes, self.target_entity = max_packet_bytes, target_entity

    async def push(self, chunk):
        await self.staging.put(chunk)
        self.counters['staging_peak'] = max(self.counters['staging_peak'], self.staging.qsize())

    async def produce(self):
        try:
            async for packet in self.packets:
                if memoryview(packet).nbytes > self.max_packet_bytes:
                    raise ValueError("PACKET_BYTE_CAPACITY")
                self.counters['input_bytes'] += memoryview(packet).nbytes
                for chunk in self.scanner.feed(packet):
                    await self.push(chunk)
                await asyncio.sleep(0)
            for chunk in self.scanner.finish():
                await self.push(chunk)
            await self.staging.put(None)
        finally:
            close = getattr(self.packets, 'aclose', None)
            if close is not None:
                await close()

    async def consume(self):
        while True:
            chunk = await self.staging.get()
            try:
                if chunk is None:
                    return
                envelope = await self.sender.wrap_output(chunk_payload(chunk), self.target_entity)
                result = self.on_verified(envelope)
                if isawaitable(result):
                    await result
                self.counters['chunks'] += 1
            finally:
                self.staging.task_done()


async def stream_to_ledger(packets, engine, session_id, on_verified, *,
                           cdc_parameters=None, staging_size=8,
                           max_packet_bytes=65536, deadline_s=30.0,
                           target_entity="tool-boundary"):
    """Scan packets concurrently with sequential commits and bounded staging.

    on_verified receives a committed envelope; recipients still verify before
    invoking tools. No retries are owned here. The source's aclose() is called
    on all exits. A failure cancels siblings; the borrowed engine is caller-owned.
    The pipeline retains no whole stream. CPU scanning yields between packets.
    """
    if type(staging_size) is not int or staging_size < 1:
        raise ValueError("staging_size must be positive")
    if type(max_packet_bytes) is not int or max_packet_bytes < 1:
        raise ValueError("max_packet_bytes must be positive")
    if type(deadline_s) not in (float, int) or not 0 < deadline_s < float('inf'):
        raise ValueError("deadline_s must be finite and positive")
    pump = StreamPump(packets, engine, session_id, on_verified, cdc_parameters,
                      staging_size, max_packet_bytes, target_entity)
    async with asyncio.timeout(deadline_s):
        async with asyncio.TaskGroup() as group:
            group.create_task(pump.produce(), name="cdc-producer")
            group.create_task(pump.consume(), name="ledger-sequencer")
    return pump.counters
