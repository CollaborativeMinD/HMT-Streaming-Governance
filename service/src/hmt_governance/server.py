"""Private Unix-domain RPC: only the host-owned Tailcat bridge may attest peer identity."""

import asyncio
import json
import os
import signal
from pathlib import Path

from .contracts import Rejected, Settings, canonical, strict_json
from .engine import Engine


async def serve(engine, path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("Refusing to replace an existing socket")
    gate = asyncio.Semaphore(engine.settings.max_jobs)

    async def handle(reader, writer):
        try:
            if gate.locked():
                raise Rejected("REQUEST_CAPACITY", "HELD")
            async with asyncio.timeout(6), gate:
                raw = await reader.readline()
                packet = strict_json(raw)
                if (
                    set(packet) != {"peer_key", "body"}
                    or type(packet["body"]) is not str
                ):
                    raise Rejected("INVALID_PACKET")
                body = strict_json(packet["body"].encode())
                if set(body) != {"operation", "arguments"}:
                    raise Rejected("INVALID_PACKET")
                result = await engine.request(
                    packet["peer_key"], body["operation"], body["arguments"]
                )
                response = dict(ok=True, result=result)
        except Rejected as exc:
            response = dict(ok=False, code=exc.code, disposition=exc.disposition)
        except (ValueError, TimeoutError):
            response = dict(ok=False, code="FRAMING_OR_TIMEOUT", disposition="HELD")
        except Exception:
            response = dict(ok=False, code="INTERNAL_HOLD", disposition="HELD")
        try:
            writer.write(canonical(response).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(path), limit=1_100_001)
    path.chmod(0o600)
    return server


async def main():
    config = json.loads(Path(os.environ["GOVERNANCE_CONFIG"]).read_text())
    from .keys import OpenBao
    from .sink import HTTPSink

    key_file = config.pop("cache_key_file", None)
    bao_config = config.pop("openbao_file", None)
    bao = OpenBao(**json.loads(Path(bao_config).read_text())) if bao_config else None
    adapters = {name: HTTPSink(**values) for name, values in config.pop("sinks", {}).items()}
    socket = config.pop("socket")
    try:
        key = await bao.cache_key() if bao else Path(key_file).read_bytes() if key_file else b""
        engine = await Engine(Settings(**config, cache_key=key), adapters).start()
    except BaseException:
        for adapter in adapters.values():
            await adapter.close()
        raise
    finally:
        if bao:
            await bao.close()
    server = await serve(engine, socket)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        async with server:
            await stopped.wait()
    finally:
        await engine.close()
        for adapter in adapters.values():
            await adapter.close()
        Path(socket).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
