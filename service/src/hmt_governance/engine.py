"""Owned async worker lifetime and bounded API admission over the durable repository."""

import asyncio
from contextlib import suppress

from . import admission, effects, records, worker
from .cache import Cache
from .contracts import Rejected
from .database import Database


class Engine:
    def __init__(self, settings, adapters=None):
        from types import MappingProxyType

        self.adapters = MappingProxyType(dict(adapters or {}))
        self.settings = settings
        self.database = Database(settings)
        self.cache = Cache(settings)
        self.tasks = []
        self._active = set()
        self.closed = False
        self.held = None
        self._close_task = None
        self._start_lock = asyncio.Lock()
        self._jobs = asyncio.Queue(maxsize=settings.max_jobs)
        for _ in range(settings.max_jobs):
            self._jobs.put_nowait(None)

    async def start(self):
        async with self._start_lock:
            if self.closed or self.held:
                raise Rejected("ENGINE_CLOSED", "HELD")
            if not self.tasks:
                await self.database.start()
                self.tasks = [
                    asyncio.create_task(self._loop())
                    for _ in range(self.settings.workers)
                ]
        return self

    async def _loop(self):
        try:
            while not self.closed and not self.held:
                try:
                    worked = await worker.process_one(self.database)
                except Rejected:
                    worked = (
                        False  # Database outage never falls back to Redis or memory.
                    )
                if not worked:
                    await asyncio.sleep(self.settings.poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.held = "WORKER_FAULT"

    async def close(self):
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        active = list(self._active)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        await self.cache.close()
        await self.database.close()

    async def request(self, peer, operation, arguments):
        if (
            self.closed
            or self.held
            or not self.tasks
            or any(task.done() for task in self.tasks)
        ):
            raise Rejected(self.held or "ENGINE_UNAVAILABLE", "HELD")
        if type(arguments) is not dict:
            raise Rejected("INVALID_ARGUMENTS")
        try:
            self._jobs.get_nowait()
        except asyncio.QueueEmpty:
            raise Rejected("REQUEST_CAPACITY", "HELD") from None
        task = asyncio.current_task()
        self._active.add(task)
        try:
            async with asyncio.timeout(5):
                return await self._dispatch(peer, operation, arguments)
        except TimeoutError:
            raise Rejected("OUTCOME_UNKNOWN", "HELD") from None
        finally:
            self._active.discard(task)
            self._jobs.put_nowait(None)

    async def _dispatch(self, peer, operation, args):
        try:
            if operation == "peer_check":
                from .authority import principal

                if args:
                    raise Rejected("INVALID_ARGUMENTS")
                async with self.database.connection() as connection:
                    actor = await principal(connection, peer, "connect")
                    return dict(agent_id=actor["agent_id"], epoch=actor["epoch"])
            if operation == "commit":
                return await admission.enqueue(self.database, peer, **args)
            if operation == "head":
                return await admission.head(self.database, peer, **args)
            if operation == "status":
                return await admission.receipt(self.database, peer, **args)
            if operation == "verify":
                return await records.verify(self.database, self.cache, peer, **args)
            if operation == "prepare":
                return await effects.prepare(self.database, self.cache, peer, **args)
            if operation == "dispatch":
                from . import runner

                return await runner.execute(self, peer, **args)
            if operation == "reconcile":
                from . import runner

                return await runner.reconcile(self, peer, **args)
        except TypeError:
            raise Rejected("INVALID_ARGUMENTS") from None
        raise Rejected("UNKNOWN_OPERATION")
